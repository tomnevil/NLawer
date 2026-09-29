"""合同审查「413 决策门」的**浏览器端到端**判据（真实 Chromium + CDP）。

## 为什么这个探针必须存在

2026-09-24 一次**手工**浏览器冒烟发现：粘贴 25000 字点「开始审查」，
页面**没有**出现设计的「413 决策门」，而是直接渲染了**失败结果页**
（「本次审查未能完成…请改用文件上传 + 异步任务」+「重试 / 返回修改原文」）。

⇒ 用户拿到的两个出口都是**死路**：「重试」必然再次 413；「返回修改原文」要手动删掉 5000 字。

**此前所有防线全绿**：
- `pnpm typecheck`、`pnpm lint` 全绿；
- 后端 **815 passed / 0 failed**（含 413 语义判据 R6）；
- API 层真链路冒烟 **8/8**（413 → 异步 → 轮询 → 回读）；
- 前端页面 curl 返回 200，JS chunk 里 `NEXT_PUBLIC_API_BASE` 也对。

**没有一条**能发现「决策门在真机上根本不出现」——因为缺陷在
「`run()` 的 catch 把 413 当成审查失败 ⇒ `setStage("done")` ⇒ 输入表单连同
GateBanner 一起被卸载」这一层，**只有真点一次才看得见**（`methodology.md` **#206** · 原 `#184`）。

## 判据清单

| 编号 | 性质 |
|------|------|
| C1 | 能登录（种子账号可用 ⇒ 环境具备） |
| C2 | 合同审查页渲染出输入表单 |
| C3 | 灌入 25000 字后 CountBadge 显示 `25000 / 20000` |
| **C4** | **提交后仍停留在输入态**（不进失败结果页）—— 本次缺陷的判据 |
| **C5** | **决策门出现**：「存为 .txt 并上传」+「选择本地文件」两个出口都在 |
| **C6** | 超限**不清空原文**（25000 字仍在输入框里） |
| **C7** | 点「存为 .txt 并上传」⇒ 转异步 ⇒ 轮询拿到结论页（含「覆盖度」） |

## 跑法（后端 8001 + web dev server 3000 需已起；库需已灌种子）

    python evidence/verify_contract_review_gate_e2e.py
    python evidence/verify_contract_review_gate_e2e.py --self-test   # 只跑 C1–C3
    python evidence/verify_contract_review_gate_e2e.py --shot        # 留截图

## 退出码（三种语义不同，别混）

    0  全部通过
    1  产品缺陷（断言失败，且环境已确认可用）
    2  环境问题：服务没起 / 种子数据不足以登录 / 浏览器起不来

⚠️ **基础设施故障必须与产品缺陷分开报**（`methodology.md`：别把「我没测成」
报成「产品坏了」）⇒ 所有前置检查失败一律 **2**。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

WEB = "http://localhost:3000"
API = "http://127.0.0.1:8001"
USER = "ent_user"
PWD = "Ent@12345"
BIG = "甲方与乙方就货物买卖事宜达成如下协议，双方应当按照诚实信用原则履行各自义务。" * 1000
BIG = BIG[:25000]

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
    """前置检查：任一不满足 ⇒ exit 2（环境问题，不是产品缺陷）。"""
    if not http_ok(f"{API}/api/health"):
        print(f"❌ 环境：后端 {API} 未就绪 → exit 2", file=sys.stderr)
        raise SystemExit(2)
    if not http_ok(f"{WEB}/login", timeout=60):
        print(f"❌ 环境：web dev server {WEB} 未就绪 → exit 2", file=sys.stderr)
        raise SystemExit(2)


def fill_js(text: str) -> str:
    """React 受控组件必须用原生 setter + input 事件，否则 React 收不到。"""
    return (
        "(() => { const ta = document.querySelector('textarea'); "
        "if (!ta) return -1; "
        "const s = Object.getOwnPropertyDescriptor("
        "window.HTMLTextAreaElement.prototype, 'value').set; "
        f"s.call(ta, {json.dumps(text)}); "
        "ta.dispatchEvent(new Event('input', {bubbles: true})); "
        "return ta.value.length; })()"
    )


async def run(full: bool = True, shot: bool = False) -> int:
    from cdp import Browser  # noqa: PLC0415

    async with Browser(headless=True, width=1280, height=1000) as b:
        # ---- C1 登录（同时证明种子数据可用）----
        await b.goto(f"{WEB}/login", wait=3.0)
        await b.settle(extra=1.5)
        await b.type_into("#login-username", USER)
        await b.type_into("#login-password", PWD)
        await b.click_text("登录")
        await b.settle(extra=4.0, timeout=40.0)
        url = await b.cdp.evaluate("location.href")
        if "/login" in url:
            # 🚨 这处是**多行**写法，第一版「一次性正则修复」**漏了它**
            #    （正则要求整条调用在同一行）⇒ 判据 7 上线当天就把它逮出来。
            #    这也说明：**一次性脚本 ≠ 判据** —— 判据会在下次也生效。
            print(
                "❌ 环境：种子账号登不进去（库可能被清过，先跑 "
                "python backend/seed_demo.py）→ exit 2",
                file=sys.stderr,
            )
            raise SystemExit(2)
        step("C1 登录", True, url)

        await b.goto(f"{WEB}/contract-review", wait=3.0)
        await b.settle(extra=2.5, timeout=40.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("C2 输入表单渲染", "合同原文" in body)

        n = await b.cdp.evaluate(fill_js(BIG))
        await b.settle(extra=1.5)
        body = await b.cdp.evaluate("document.body.innerText")
        step("C3 CountBadge 显示超限", n == len(BIG) and "/ 20000" in body,
             f"长度={n}，计数含 '/ 20000'")

        if not full:
            if shot:
                await b.screenshot(HERE / "contract_review_gate_selftest.png")
            return 0

        # ---- C4/C5/C6：413 必须走决策门，而不是失败结果页 ----
        await b.click_text("开始审查")
        await b.settle(extra=4.0, timeout=40.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("C4 提交后仍停留在输入态", "本次审查未能完成" not in body,
             "命中失败结果页 ⇒ 413 被当成审查失败")
        step("C5 决策门两个出口都在",
             "存为 .txt 并上传" in body and "选择本地文件" in body)
        kept = await b.cdp.evaluate(
            "(() => { const ta = document.querySelector('textarea'); "
            "return ta ? ta.value.length : -1; })()"
        )
        step("C6 超限不清空原文", kept == len(BIG), f"提交后长度={kept}")

        # ---- C7：转异步 ⇒ 轮询 ⇒ 出结论 ----
        await b.click_text("存为 .txt 并上传")
        await b.settle(extra=6.0, timeout=60.0)
        final = ""
        for _ in range(20):
            await asyncio.sleep(3)
            final = await b.cdp.evaluate("document.body.innerText")
            if "覆盖度" in final or "整体风险" in final:
                break
        step("C7 轮询拿到结论页", "覆盖度" in final or "整体风险" in final)

        if shot:
            await b.screenshot(HERE / "contract_review_gate_e2e.png")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="只跑 C1–C3（证明仪器可用），不做完整链路")
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
