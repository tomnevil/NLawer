"""运行时健康检查（真实 Chromium + CDP）：**页面画出来了 ≠ 页面没出错**。

**为什么需要这一类检查**（而不是再加几条 DOM 断言）：

到目前为止所有验证都属于三种「静态快照」——
HTTP 200（`envprobe`）、DOM 事实（`verify_render_e2e.py`）、像素（`visual_baseline.py`）。
它们有一个共同的盲区：**一个页面可以渲染得完美无缺，同时在后台抛未捕获异常、
刷 console error、并且把某个接口的 500 悄悄吞掉。**
这类故障对快照完全不可见（错的那一块往往就是「没画出来的那一块」），
但在真实浏览器里是**第一等公民**——事件流里明明白白。

本脚本读的就是这条事件流：
  - `Runtime.exceptionThrown`              → 未捕获 JS 异常
  - `Runtime.consoleAPICalled(type=error)` → 控制台报错
  - `Network.responseReceived(status>=400)`→ 失败请求（含被吞掉的 500）

**覆盖面**：四个端全部纳入。此前本项目只验证过 admin（3002），
web(3000) / lawyer(3001) / im(3003) **从未被检查过**——
「没测过」和「测过且通过」是两件完全不同的事。

用法：
    python evidence/verify_runtime_health.py                # 四个端全跑
    python evidence/verify_runtime_health.py --app admin    # 只跑一个端
    python evidence/verify_runtime_health.py --self-test    # **自检**：注入三类已知故障，断言必须被捕获

退出码：`0` 干净 / `1` 发现运行时错误（产品缺陷） / `2` 环境问题（服务未起、登录失败）或**自检未通过**

> **`--self-test` 为什么必要**（同 `visual_baseline.py`）：
> 只打印「0 个错误」是没有信息量的——一个永远返回「干净」的检查器也会打印 0。
> 必须先证明它**真的会报警**。
>
> 自检同时回答两个**互相独立**的问题，**不能混成一个布尔值**：
> 1. **检出能力**：三类注入故障是否都被抓到（靠特征串判定）；
> 2. **对照组是否干净**：同一页在不注入时有无错误。
>
> 第一版把「对照组不干净」也算作「自检失败」，于是把 IM 端一条**真实 500**
> 报成了「工具坏了」——正是本项目反复踩的坑：
> **证据工具必须区分「产品坏了」与「我没测成」。**
> 现在：Q1 失败 ⇒ 退出码 2（工具问题）；Q1 通过但 Q2 有货 ⇒ 退出码 1（产品缺陷）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

# ── 被测范围 ────────────────────────────────────────────────────────
#
# `user` 是**实测**出来的可用账号（见 `probe_app_login.py`），不是猜的。
APPS: dict[str, dict] = {
    "web": {
        "port": 3000,
        "user": "ent_admin",
        "pages": ("/", "/qa", "/documents", "/contract-review", "/compliance", "/knowledge", "/billing"),
    },
    "lawyer": {
        "port": 3001,
        "user": "lawyer_wang",
        "pages": ("/", "/dispatches", "/cases", "/reviews", "/archives", "/notifications"),
    },
    "admin": {
        "port": 3002,
        "user": "admin",
        "pages": ("/", "/reviews", "/cases", "/dispatches", "/compliance", "/billing", "/complaints", "/audit"),
    },
    "im": {
        "port": 3003,
        "user": "client",
        # 2026-09-19：im 从「1 个业务路由」变成 §8.4 的 4 个 Tab + 案件详情。
        # `@first-case` 是占位符，运行时从 /cases 的 DOM 里读出真实 id（见 resolve_path）。
        # ⚠️ 这行以前是 `("/",)`——「已访问 1 页 → 0 个问题」是**覆盖率不足**，
        #    不是「im 很健康」。路由加了而这里没跟着加，工具会安静地少测。
        "pages": ("/", "/chat", "/cases", "/cases/@first-case", "/me"),
    },
}

# 登录页四端共用 `packages/ui/LoginShell`，选择器一致
LOGIN_PATH = "/login"

# ── 已知噪声白名单 ──────────────────────────────────────────────────
#
# ⚠️ 白名单是**最容易变成遮羞布**的东西：往里丢一条，就等于把一类真实缺陷永久静音。
# 所以每条都必须写清「为什么它不是产品缺陷」，写不出来的就不许进。
# 这一条是唯一一条，且是纯环境性的。
# 白名单：**只放行「已核实为正常业务状态」的具体响应**。
#
# ⚠️ 两条纪律：
#  1. 模式里**必须带状态码**（`http` 明细的格式是 `"{status} {type} {url}"`，状态在最前），
#     否则同一个 URL 的 5xx 会被一起放过 —— 那是**把真缺陷静音**。
#  2. 每条都必须写清「为什么这是正常的」+「在哪份代码里被显式处理」。
#     **写不出依据的，就不该进白名单。**
IGNORE = (
    (
        r"/favicon\.ico",
        "dev server 不提供 favicon 时必然 404，与产品行为无关（生产构建由静态资源接管）",
    ),
    (
        r"^404\s.*/api/v1/archives/cases/\d+",
        "「案件尚未生成卷宗」是**正常业务状态**：卷宗号只能逐案回查，"
        "前端用 `Promise.allSettled` 并把 404 渲染成空——见 "
        "`frontend/apps/lawyer/app/(app)/archives/page.tsx` 的注释「404 = 未归档」。"
        "**只放行 404**：同一 URL 的 5xx 仍会报出。",
    ),
)


def is_ignored(text: str) -> str | None:
    import re

    for pat, why in IGNORE:
        if re.search(pat, text):
            return why
    return None


# ── 事件分类 ────────────────────────────────────────────────────────


def _short(s: str, n: int = 200) -> str:
    return " ".join(str(s).split())[:n]


def _arg_text(a: dict) -> str:
    """`RemoteObject` → 一行文本。异常对象只有 `description`，原始值只有 `value`。"""
    if "value" in a:
        v = a["value"]
        return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return a.get("description") or a.get("unserializableValue") or a.get("type", "?")


def classify(events: list[dict]) -> dict[str, list[str]]:
    """把 CDP 事件流分成三类问题（**不套白名单**，白名单在报告阶段才生效）。"""
    exc: list[str] = []
    cerr: list[str] = []
    http: list[str] = []
    net: list[str] = []

    for e in events:
        m = e.get("method")
        p = e.get("params") or {}
        if m == "Runtime.exceptionThrown":
            d = p.get("exceptionDetails") or {}
            detail = (d.get("exception") or {}).get("description") or d.get("text") or "?"
            exc.append(_short(f"{detail}  @{d.get('url','')}:{d.get('lineNumber')}"))
        elif m == "Runtime.consoleAPICalled" and p.get("type") == "error":
            cerr.append(_short(" ".join(_arg_text(a) for a in (p.get("args") or []))))
        elif m == "Network.responseReceived":
            r = p.get("response") or {}
            if int(r.get("status") or 0) >= 400:
                http.append(_short(f"{r.get('status')} {p.get('type')} {r.get('url')}", 160))
        elif m == "Network.loadingFailed" and not p.get("canceled"):
            # `canceled=true` 是导航/SPA 切页主动放弃的请求，不是故障；必须滤掉，
            # 否则每次切页都会刷出一片假失败。
            net.append(_short(f"{p.get('errorText')} {p.get('type')}"))

    return {"未捕获异常": exc, "控制台报错": cerr, "失败请求": http, "请求中断": net}


# ── 自检（fault injection）───────────────────────────────────────────
#
# 注入**三类各一条**，因为这三类的采集路径完全不同（Runtime 异常 / console API /
# Network 响应），任何一条路径接错了，对应那一类就会永远报 0。
# 只注入一类是测不出另外两类的接线问题的。
SELF_TEST_JS = """(() => {
  console.error('NLW-SELFTEST console-error');
  setTimeout(() => { throw new Error('NLW-SELFTEST uncaught'); }, 60);
  fetch('/__nlaw_selftest_missing__' + Date.now()).catch(() => {});
})()"""

# 判「注入被检出」靠**特征串**，不靠「条数变多了」——
# 条数会受环境噪声影响，特征串不会。
SELFTEST_MARKERS = {
    "未捕获异常": "NLW-SELFTEST uncaught",
    "控制台报错": "NLW-SELFTEST console-error",
    "失败请求": "__nlaw_selftest_missing__",
}


# ── 采集 ────────────────────────────────────────────────────────────


async def login(b: Browser, base: str, username: str) -> bool:
    from app.seed.data import DEMO_USERS

    u = next((x for x in DEMO_USERS if x["username"] == username), None)
    if u is None:
        raise RuntimeError(f"种子里没有账号 {username}")

    await b.goto(f"{base}{LOGIN_PATH}", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return LOGIN_PATH not in (await b.cdp.evaluate("location.href") or "")


async def visit(b: Browser, url: str) -> list[dict]:
    """导航到一个页面并取回**只属于这次导航**的事件。

    先 `drain` 再导航：CDP 的事件是**会话级累积**的，
    不在导航前清空的话，上一个页面的异常会被算到这一个页面头上。
    """
    b.drain_events()
    await b.goto(url, wait=1.4)
    await b.settle(extra=1.8)
    return b.drain_events()


async def resolve_path(b: Browser, base: str, path: str) -> str:
    """把路径里的占位符换成真实值（目前只有 `@first-case`）。

    为什么不直接调 API 拿 id：SDK 把 access token 存在**模块内存变量**里
    （`packages/sdk/src/index.ts:10`，刻意不落 localStorage —— XSS 读不到），
    所以 CDP 里发 `fetch` 带不上鉴权。**从 DOM 读链接是唯一不用重建鉴权的办法。**

    解析失败**抛异常**（上层归类为环境问题，退 2）——绝不静默跳过，
    否则动态路由会永远测不到而报告依旧全绿。
    """
    if "@first-case" not in path:
        return path
    await visit(b, f"{base}/cases")
    href = await b.cdp.evaluate(
        "(() => { const a = document.querySelector('a[href^=\"/cases/\"]');"
        " return a ? a.getAttribute('href') : null; })()"
    )
    if not href:
        raise RuntimeError(f"{base}/cases 上没有指向案件详情的链接（动态路由测不到）")
    return path.replace("@first-case", href.rsplit("/", 1)[-1])


async def probe_app(app: str, spec: dict, self_test: bool = False) -> dict:
    base = f"http://localhost:{spec['port']}"
    out: dict[str, dict] = {}

    async with Browser(headless=True, width=1280, height=900, device_scale_factor=1) as b:
        await b.apply_device(mobile=False)
        if not await login(b, base, spec["user"]):
            raise RuntimeError(f"{app}: 账号 {spec['user']} 登录失败（环境问题，不是产品缺陷）")

        for path in spec["pages"]:
            real = await resolve_path(b, base, path)
            out[real] = classify(await visit(b, f"{base}{real}"))

        if self_test:
            # 对照组：**同一页**先量一遍「不注入」的基线。
            # 没有这一步，「注入后被检出」无法排除「这一页本来就有噪声」。
            ctrl = classify(await visit(b, f"{base}/"))
            await b.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=SELF_TEST_JS)
            injected = classify(await visit(b, f"{base}/"))
            out["__selftest_control__"] = ctrl
            out["__selftest_injected__"] = injected

    return out


# ── 报告 ────────────────────────────────────────────────────────────


def flatten(app: str, results: dict) -> tuple[int, int, list[str]]:
    """统计一个端的三类问题数（套白名单），返回 (问题数, 被白名单放过数, 明细)。"""
    n, skipped, lines = 0, 0, []
    for path, cats in results.items():
        for cat, items in cats.items():
            for it in items:
                why = is_ignored(it)
                if why:
                    skipped += 1
                    continue
                n += 1
                lines.append(f"  [{cat}] {app}{path}\n           {it}")
    return n, skipped, lines


async def run(only: str | None, self_test: bool) -> int:
    print("── 前置检查 ──")
    from envprobe import check_services

    problems = check_services()
    if problems:
        for p in problems:
            print(f"  [ENV ] {p}")
        print("\n服务未就绪 ⇒ 不启动浏览器（跑下去产出的「干净」是假的）。")
        return 2

    apps = {only: APPS[only]} if only else APPS
    print(f"\n── 运行时健康采集（{len(apps)} 个端{'，自检模式' if self_test else ''}）──")

    all_results: dict[str, dict] = {}
    for app, spec in apps.items():
        try:
            r = await probe_app(app, spec, self_test)
        except Exception as e:  # noqa: BLE001
            print(f"  [ENV ] {app}: {type(e).__name__}: {e}")
            return 2
        all_results[app] = r
        # 把**实际访问到的路径**打出来（含动态路由解析后的真实 id）——
        # 只报「已访问 N 页」时，占位符解析失败被静默跳过也看不出来。
        paths = [p for p in r if not p.startswith("__")]
        print(f"  [ ok ] {app:<7}:{spec['port']}  已访问 {len(paths)} 页（账号 {spec['user']}）")
        print(f"         {'  '.join(paths)}")

    if self_test:
        return report_self_test(all_results, apps)

    print("\n" + "=" * 72)
    total, all_lines, all_skipped = 0, [], 0
    for app, results in all_results.items():
        n, skipped, lines = flatten(app, results)
        total += n
        all_skipped += skipped
        all_lines += lines
        print(f"{app:<7} :{APPS[app]['port']}  {len(results)} 页  →  {n} 个问题" + (f"（白名单放过 {skipped}）" if skipped else ""))

    print("=" * 72)
    if all_lines:
        print(f"\n运行时错误（**产品缺陷**）：{total} 条\n")
        for ln in all_lines:
            print(ln)
        print("=" * 72)
        print("=> 退出码 1：存在未捕获异常 / 控制台报错 / 失败请求")
        return 1
    print("=> 退出码 0：全部页面无未捕获异常、无控制台报错、无失败请求")
    return 0


def report_self_test(all_results: dict, apps: dict) -> int:
    """自检回答两个**互相独立**的问题，不能混成一个布尔值。

    Q1 这个检查器**会不会报警**？ —— 靠三类特征串是否都被检出。
    Q2 被测页面本来干净吗？      —— 靠对照组（未注入）的结果。

    ⚠️ 这两件事必须分开报。第一版把「对照组不干净」也算成「自检失败」，
    结果**把一条真实产品缺陷（IM 端 500）报成了「工具坏了」**——
    正是本项目反复踩的那个坑：证据工具必须区分「产品坏了」与「我没测成」。
    """
    print("\n" + "=" * 72)
    print("自检：已注入三类已知故障 ——", " ".join(SELFTEST_MARKERS.values()))

    detect_fail: list[str] = []  # Q1：没检出 ⇒ 工具失效
    ambient: list[str] = []  # Q2：对照组本来就有 ⇒ 真实缺陷

    for app, results in all_results.items():
        ctrl = results.get("__selftest_control__") or {}
        inj = results.get("__selftest_injected__") or {}

        print(f"\n── {app} ──")
        print("  Q1 检出能力（注入后必须每类都出现特征串）:")
        for cat, marker in SELFTEST_MARKERS.items():
            hit = [i for i in (inj.get(cat) or []) if marker in i]
            if hit:
                print(f"    ✅ {cat}: {len(hit)} 条   ← {hit[0][:110]}")
            else:
                detect_fail.append(f"{app}/{cat}")
                print(f"    ❌ {cat}: 未检出特征串 {marker!r}（该类采集路径没接上）")

        print("  Q2 对照组（未注入）应当干净:")
        ctrl_items = [(cat, i) for cat, items in ctrl.items() for i in items]
        fresh = [(cat, i) for cat, i in ctrl_items if not is_ignored(i)]
        if not fresh:
            print("    ✅ 干净（0 条）")
        else:
            print(f"    ⚠️  本来就有 {len(fresh)} 条 —— 这是**产品缺陷**，不是工具问题：")
            for cat, i in fresh:
                print(f"       [{cat}] {i}")
                ambient.append(f"{app}{cat}: {i}")

    print("\n" + "=" * 72)
    if detect_fail:
        print(f"❌ Q1 失败：{len(detect_fail)} 类未检出 —— {', '.join(detect_fail)}")
        print("   ⇒ 检查器**不可信**，此时任何「运行时干净」的结论都不成立。")
        print("   退出码 2（工具问题，不是产品缺陷）")
        return 2
    print("✅ Q1 通过：三类故障全部检出 ⇒ 这个检查器**真的会报警**。")

    if ambient:
        print(f"\n⚠️  Q2：对照组本来就有 {len(ambient)} 条真实问题（见上）——")
        print("   它们是**产品缺陷**，与自检无关，只是恰好被对照组照出来了。")
        print("   退出码 1")
        return 1

    print("✅ Q2 通过：对照组干净 ⇒ 「注入才出现」这个因果链成立，")
    print("   且当前被测页面确实无运行时错误。")
    print("   退出码 0")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="运行时健康检查（未捕获异常 / 控制台报错 / 失败请求）")
    ap.add_argument("--app", choices=sorted(APPS), help="只检查一个端（默认四个端全跑）")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="自检：注入三类已知故障，断言必须全部被捕获（证明检查器真的会 FAIL）",
    )
    args = ap.parse_args()
    return asyncio.run(run(args.app, args.self_test))


if __name__ == "__main__":
    sys.exit(main())
