"""im 端（C 端客户，端口 3003）的**浏览器端到端**冒烟判据（真实 Chromium + CDP）。

## 为什么这个探针必须存在

四端是四个独立打包单元；im 端（C 端客户）此前**完全没有浏览器判据**——
只能靠「dev server 起来、页面 200」这种静态检查，没人验证过
「真用 client 账号登录 ⇒ 工作台真的渲染 ⇒ 点导航真的跳转」。

im 端**不挂** `ContentTooLargeGate`（只有 admin/lawyer 挂了），所以本探针
只做基础可用性冒烟：登录 ⇒ 工作台渲染 ⇒ 一次 SPA 内导航成功。
这是防止「im 端整体炸了却全绿」的最低防线。

## 判据清单

| 编号 | 性质 |
|------|------|
| I1 | 能登录 im 端（client 种子账号可用 ⇒ 环境具备） |
| I2 | 工作台渲染（含「律小智 AI 法律助手」品牌署名） |
| **I3** | **点「向 AI 提问」⇒ SPA 内跳转到 /chat（导航真的通）** |

## 跑法（后端 8001 + im dev server 3003 需已起；库已灌种子）

    python evidence/verify_im_e2e.py
    python evidence/verify_im_e2e.py --self-test   # 只跑 I1–I2
    python evidence/verify_im_e2e.py --shot        # 留截图

## 退出码

    0  全部通过
    1  产品缺陷（断言失败，且环境已确认可用）
    2  环境问题：服务没起 / 种子数据不足以登录 / 浏览器起不来
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

IM = "http://localhost:3003"
API = "http://127.0.0.1:8001"
USER = "client"
PWD = "Client@12345"

PASS = 0
FAIL = 0
FAILED: list[str] = []


def step(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ❌ {name}" + (f"  ({detail})" if detail else ""))


def http_ok(url: str, timeout: float = 8.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def check_env() -> None:
    if not http_ok(f"{API}/api/health"):
        print(f"❌ 环境：后端 {API} 未就绪 → exit 2", file=sys.stderr)
        raise SystemExit(2)
    if not http_ok(f"{IM}/login", timeout=60):
        print(f"❌ 环境：im dev server {IM} 未就绪 → exit 2", file=sys.stderr)
        raise SystemExit(2)


async def login(b, base: str, user: str, pwd: str) -> None:
    await b.goto(f"{base}/login", wait=4.0)
    # 等登录表单水合：admin/lawyer/im 登录页 hydration 较慢（~5–8s），
    # 没水合时 #login-username 还不存在 ⇒ 直接 type_into 会失败。
    for _ in range(12):
        await b.settle(extra=1.0, timeout=20.0)
        url = await b.cdp.evaluate("location.href")
        if "/login" not in url:
            return  # 会话已恢复（带着有效 cookie）
        if await b.cdp.evaluate("!!document.querySelector('#login-username')"):
            break
    url = await b.cdp.evaluate("location.href")
    if "/login" not in url:
        return  # 会话已恢复
    await b.type_into("#login-username", user)
    await b.type_into("#login-password", pwd)
    await b.click_text("登录")
    await b.settle(extra=5.0, timeout=40.0)
    url = await b.cdp.evaluate("location.href")
    if "/login" in url:
        print(f"❌ 环境：{base} 登录失败（账号/密码或登录页异常）→ exit 2", file=sys.stderr)
        raise SystemExit(2)


async def run(full: bool = True, shot: bool = False) -> int:
    from cdp import Browser  # noqa: PLC0415

    async with Browser(headless=True, width=390, height=780) as b:  # im 是移动优先，用窄视口
        # ---- I1 登录 ----
        await login(b, IM, USER, PWD)
        step("I1 im 端登录", True)

        # ---- I2 工作台渲染 ----
        await b.goto(f"{IM}/", wait=3.0)
        await b.settle(extra=2.5, timeout=40.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("I2 工作台渲染", "律小智 AI 法律助手" in body,
             "品牌署名缺失 ⇒ 页面可能未挂载")

        if not full:
            if shot:
                await b.screenshot(HERE / "im_e2e_selftest.png")
            return 0

        # ---- I3 SPA 内导航 ----
        # 注意：click_text 只检索 button/a/label/div[role=menuitem]，且默认 exact=True。
        # 「向 AI 提问」是 <a>(Next Link) 内层 <span> 的文本，外链 innerText 还带了副标题，
        # 所以必须 exact=False 才能命中这条 <a>（命中后 .click() 由 Next 路由接管跳 /chat）。
        clicked = await b.click_text("向 AI 提问", exact=False)
        await b.settle(extra=2.5, timeout=30.0)
        url = await b.cdp.evaluate("location.href")
        if not clicked:
            step("I3 点「向 AI 提问」跳转到 /chat", False,
                 "click_text 未命中任何元素 ⇒ 聊天入口缺失或选择器失效 → exit 2")
            print("❌ 环境：im「向 AI 提问」入口未找到（可能页面结构变更）→ exit 2", file=sys.stderr)
            raise SystemExit(2)
        step("I3 点「向 AI 提问」跳转到 /chat", "/chat" in url, url)

        if shot:
            await b.screenshot(HERE / "im_e2e.png")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="只跑 I1–I2（证明仪器可用），不做导航")
    ap.add_argument("--shot", action="store_true", help="留截图")
    args = ap.parse_args()

    check_env()
    asyncio.run(run(full=not args.self_test, shot=args.shot))

    print(f"\n通过 {PASS} / 失败 {FAIL}")
    if FAIL:
        print("失败项： " + " · ".join(FAILED))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
