"""管理端「413 全局 Toast」的**浏览器端到端**判据（真实 Chromium + CDP）。

## 为什么这个探针必须存在

2026-09-24 补齐「413 决策门」链路时发现：web 合同审查页靠页内 `GateBanner`
接住了 413，但**其它页面**靠的是挂在各端壳层的 `ContentTooLargeGate`
（`packages/ui/src/components/ContentTooLargeGate.tsx`，挂在
`apps/admin/app/(app)/layout.tsx` 与 `apps/lawyer/app/(app)/layout.tsx`）。

它的职责是：任何页面（只要请求走 SDK 的 `request()`）触发 413，都**至少**
弹一次 warning toast（`单次最多 N 字，原文未丢失，请改用文件上传。`），
而不是「点了没反应」或只冒一条语焉不详的报错。

本探针在**管理端投诉处理**场景真触发一次 413，确认这个全局 Toast 真的出现：
- 投诉 `handle_note` 字段上限 2000 字（`app/api/v1/complaints.py:46`）；
- 在弹窗里粘贴 >2000 字的处理结论并「确认受理」⇒ 后端 413（CONTENT_TOO_LARGE）；
- SDK `request()` 在 413 时派发 `onContentTooLarge`（见 `packages/sdk/src/index.ts:384`）；
- `ContentTooLargeGate` 订阅后弹 toast。

## 判据清单

| 编号 | 性质 |
|------|------|
| T1 | 能登录管理端（种子账号可用 ⇒ 环境具备） |
| T2 | 投诉举报页渲染（标题「投诉举报」出现） |
| T3 | 打开某条工单的处理弹窗（#handle-note 文本框出现） |
| **T4** | **提交超限结论后，全局 Toast 出现**（含「原文未丢失」——这是 `ContentTooLargeGate` 专属文案，API 原始报错里没有） |
| **T5** | **弹窗不消失、超限原文保留**（413 没有被当成「处理成功」静默吞掉） |

T4 是本次缺陷的判据；它专门断言 `ContentTooLargeGate` 的 toast，而不是
调用方自己的 `formError`（后者即使摘掉全局 Gate 也照样出现）。

## 跑法（后端 8001 + admin dev server 3002 需已起；库需已灌种子）

    python evidence/verify_admin_413_toast_e2e.py
    python evidence/verify_admin_413_toast_e2e.py --self-test   # 只跑 T1–T3
    python evidence/verify_admin_413_toast_e2e.py --shot        # 留截图

## 退出码（三种语义不同，别混）

    0  全部通过
    1  产品缺陷（断言失败，且环境已确认可用）
    2  环境问题：服务没起 / 种子数据不足以登录 / 浏览器起不来

⚠️ 基础设施故障必须与产品缺陷分开报（methodology：别把「我没测成」报成「产品坏了」）⇒ 所有前置检查失败一律 **2**。
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

ADMIN = "http://localhost:3002"
API = "http://127.0.0.1:8001"
USER = "admin"
PWD = "Admin@12345"
LIMIT = 2000
# ⚠️ 必须稳超 2000 上限才触发 413。注意：要「整段 ×N 再截断」，不能「前缀 + 后缀×N」——
# 后者只拼出 ~1952 字（<2000），[:2500] 不截断，永远触不到上限 ⇒ 探针假绿。
# 这里用整段 ×80 再 [:2500]，确保 BIG 真有 2500 字。
BIG = ("处理结论：经核实，该投诉所述内容缺乏事实依据，且未提供有效证据支撑，"
       "根据平台规则与相关法律法规，本次作不予受理处理。") * 80
BIG = BIG[:2500]

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
    if not http_ok(f"{ADMIN}/login", timeout=60):
        print(f"❌ 环境：admin dev server {ADMIN} 未就绪 → exit 2", file=sys.stderr)
        raise SystemExit(2)


def seed_complaint() -> None:
    """匿名提交一条投诉（无需登录），保证列表里至少有一条 PENDING 工单可处理。

    公开入口 `POST /api/v1/complaints` 不要求鉴权（第十五条「便捷入口」底线）。
    """
    payload = json.dumps(
        {"type": "OTHER", "description": "探针自动化测试投诉：用于触发处理结论 413。"}
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{API}/api/v1/complaints",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except (urllib.error.URLError, OSError):
        # 种子失败不致命：若库里本来就有 PENDING 工单，探针仍能跑。
        pass


def fill_js(sel: str, text: str) -> str:
    """React 受控组件用原生 setter + input 事件填充（**直接设值、不追加**）。

    不用 `type_into`：本机 Chromium 的 `Input.insertText` 对长文本会截断到 ~1954 字
    且是「追加」语义，重试会越填越长（实测 DOM 1954→3908→5862），永远凑不到 2500。
    `fill_js` 每次把值**整体设成 2500**，配合计数器「已输入 2500」校验，
    命中即说明 2500 字真的进了 React state。
    """
    return (
        "(() => { const el = document.querySelector("
        + json.dumps(sel)
        + "); if (!el) return -1; "
        "const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype "
        ": window.HTMLInputElement.prototype; "
        "const setter = Object.getOwnPropertyDescriptor(proto, 'value').set; "
        f"setter.call(el, {json.dumps(text)}); "
        "el.dispatchEvent(new Event('input', {bubbles: true})); "
        "return el.value.length; })()"
    )


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

    async with Browser(headless=True, width=1280, height=1000) as b:
        # ---- T1 登录 ----
        await login(b, ADMIN, USER, PWD)
        step("T1 管理端登录", True)

        # ---- T2 投诉举报页渲染 ----
        # 🚨 **必须先建工单、再导航**（2026-09-24 · Phase 2 首次在**全新库**上真跑时逮到）：
        #    原实现是「先 `goto`、再 `seed_complaint()`」—— 列表在**导航那一刻**就渲染完了，
        #    而页面**不会**自己轮询 ⇒ 新工单根本不在 DOM 里 ⇒ T3 找不到「处理」钮。
        #    ⚠️ 它在**本机旧库**上看起来完全正常：库里早就有别的 PENDING 工单撑着。
        #       ⇒ 这是「**本机绿、CI 红**」的经典成因，也正是 Phase 2 用**隔离库**的价值。
        #    ⚠️ 顺带记一条：这个顺序问题**不是**产品缺陷，但在旧库上永远暴露不出来 ——
        #       「换一个干净的库再跑一遍」本身就是一条判据。
        seed_complaint()
        await b.goto(f"{ADMIN}/complaints", wait=3.0)
        await b.settle(extra=2.5, timeout=40.0)
        body = await b.cdp.evaluate("document.body.innerText")
        step("T2 投诉举报页渲染", "投诉举报" in body)

        # 列表里 PENDING / PROCESSING 都显示「处理」钮；点它后弹窗的提交钮
        # 文字随状态变（确认受理 / 确认办结 / 确认不予受理）。T4 用前缀「确认」点，
        # 不写死某一态，避免状态漂移导致点不到（见 _dbg_admin_413.py 复现）。

        # ---- T3 打开处理弹窗 ----
        # ⚠️ 同样必须 IIFE：`(() => {...})()`；不调用的箭头函数会被 returnByValue
        #    序列化成 `{}`（空对象），既点不到按钮，`clicked`/`has_ta` 也判不准。
        #    debug 已用 IIFE 形式验证「点 处理 ⇒ #handle-note 出现」。
        clicked = await b.cdp.evaluate(
            """(() => {
                const btn = [...document.querySelectorAll('button')]
                    .find(x => x.textContent.trim() === '处理');
                if (btn) { btn.click(); return true; }
                return false;
            })()"""
        )
        await b.settle(extra=1.5, timeout=20.0)
        has_ta = await b.cdp.evaluate(
            "(() => !!document.querySelector('#handle-note'))()"
        )
        step("T3 打开处理弹窗（#handle-note 出现）", bool(clicked) and has_ta,
             f"clicked={clicked}, textarea={has_ta}")

        if not full:
            if shot:
                await b.screenshot(HERE / "admin_413_toast_selftest.png")
            return 0

        # ---- T4/T5：提交超限结论 ⇒ 全局 Toast ----
        # 切到「不予受理」动作（needsNote=true）。这一步很关键：它让弹窗渲染
        # 「已输入 X / 5 字」计数——这是**受控 textarea 已收到 2500 字**的 DOM 可见证据。
        # 否则 fill 没 propagate 到 React state 时，提交会发空 note ⇒ 不 413 ⇒ 静默成功，
        # 探针会把这个 race 误判成「产品缺陷」。
        await b.click_text("不予受理")
        await b.settle(extra=0.8, timeout=15.0)

        # 用**真实输入事件**填充（type_into → CDP Input.insertText），React 原生处理，
        # 比「原生 setter + 手动 input 事件」更稳——后者偶尔 race 不过 React 的
        # value tracker，导致 2500 字没进 state（debug 偶发复现：同份代码有时 413 有时静默成功）。
        # 带重试：填完读计数「已输入 2500」，没出现就再填，最多 3 次。
        propagated = False
        for attempt in range(3):
            await b.cdp.evaluate(fill_js("#handle-note", BIG))
            await b.settle(extra=1.5, timeout=15.0)
            counter = await b.cdp.evaluate("document.body.innerText")
            # fill_js 每次把值整体设成 2500；计数器「已输入 2500」出现即说明进了 React state。
            if "已输入 2500" in counter:
                propagated = True
                break
        # 前置判据：确认 2500 字真的进了 React state。没进 ⇒ 是仪器 race，按「环境」处理（exit 2），
        # 绝不能误报成产品缺陷。这是把「仪器不可靠」和「产品缺陷」分开的关键。
        if not propagated:
            step("T4 前置：2500 字已进入受控 textarea（计数=2500）", False,
                 "fill 没 propagate 到 React state（仪器 race，非产品缺陷）")
            print("❌ 环境：受控 textarea 填不进 2500 字（CDP 输入 race）→ exit 2", file=sys.stderr)
            raise SystemExit(2)

        # 提交钮文字随状态变（确认受理 / 确认办结 / 确认不予受理），用前缀匹配，
        # 不写死某一态。debug 已验证：点「确认*」⇒ 真 413 ⇒ 全局 Gate 弹「原文未丢失」。
        clicked_submit = await b.click_text("确认", exact=False)
        if not clicked_submit:
            step("T4 全局 413 Toast 出现（含「原文未丢失」）", False,
                 "没找到提交钮（确认*）——弹窗状态与预期不符")
            return 1
        # toast 默认 5s 后消失，留 2.5s 窗口读取
        await b.settle(extra=2.5, timeout=20.0)
        body = await b.cdp.evaluate("document.body.innerText")
        # 「原文未丢失」是 ContentTooLargeGate 专属文案；API 原始报错里没有这句，
        # 因此它能区分「全局 Gate 弹了」与「只是调用方自己 catch 出的 formError」。
        has_toast = "原文未丢失" in body
        step("T4 全局 413 Toast 出现（含「原文未丢失」）",
             has_toast,
             "" if has_toast else "未命中全局 Gate 专属文案（可能只走了调用方 formError）")
        kept = await b.cdp.evaluate(
            "(() => { const ta = document.querySelector('#handle-note'); "
            "return ta ? ta.value.length : -1; })()"
        )
        step("T5 弹窗不消失、超限原文保留",
             kept == len(BIG), f"提交后 #handle-note 长度={kept}")

        if shot:
            await b.screenshot(HERE / "admin_413_toast_e2e.png")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="只跑 T1–T3（证明仪器可用），不触发 413")
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
