"""渲染级验证：im（客户端）四个 Tab 的**点击可达性** + 桌面端三栏宽度未受损。

## 为什么要单独测「点击」

`layout.tsx` 里激活态是**推**出来的：

    const activeId = pathname.split("/").filter(Boolean)[0] ?? ROOT_ID;

也就是说 **Tab 的 `id` 必须等于一级路径段**（根路径用 `ROOT_ID`）。
写错了不会报错——没有异常、没有警告，只是**高亮不见了**。
这类「静默失效」只能靠「点一下再看哪个 tab 亮着」来锁。

同时还要确认：点过去之后**目标页真的渲染出来了**（不是 404、
不是空白壳、不是渲染到一半崩掉）。

## 桌面端为什么要一起测

`TabBar` 自带 `lg:hidden`，所以 ≥1024px 它应当**完全消失**。
如果哪天有人给它加了 `lg:block`，手机端导航会跑进桌面——同样不报错。
另外概念图 06 的三栏（288 会话列表 + 正文 + 320 案件上下文）在 1280px 下
必须还留得下正文栏（`im-mobile-nav-spec.md` §2.3：正文栏 ≥ 432px）。

> ⚠️ **必须点名文档**：`design-spec.md` 也有 `§2.3`（语义层·责任边界与状态），
> 不点名会让覆盖率矩阵判为「归因歧义」而把本门禁的引用**整个丢掉**。
> 这里的 §2.3 指的是 `im-mobile-nav-spec.md` 的「桌面端为什么不用 AppShell」。

## 退出码

  `0` 通过 · `1` 产品缺陷 · `2` 环境问题（登录失败 / 元素找不到）

用法：python evidence/verify_im_tabs.py [--shots] [--port 3003]
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

DEFAULT_PORT = 3003
USER = "client"
# 运行期由 main() 按 --port 赋值；各函数读的是这个全局，所以放在模块级
BASE = ""

# (Tab 文案, 期望路径, 页面标志文案)
# 标志文案用来区分「路由到了但页面是空壳」和「真的渲染出来了」
TABS = [
    ("工作台", "/", "待处理事项"),
    ("咨询", "/chat", "会话"),
    ("我的案件", "/cases", "我的案件"),
    ("我的", "/me", "退出登录"),
]

# 点某个 Tab 按钮：必须**限定在 nav 内**，否则页面上同名文案的按钮会先被点到
CLICK_TAB_JS = """(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  if (!nav) return { ok: false, why: '找不到 nav[aria-label="主导航"]' };
  const btn = [...nav.querySelectorAll('button')].find(b => (b.innerText || '').trim() === %s);
  if (!btn) return { ok: false, why: 'nav 内没有文案为 %s 的按钮' };
  btn.click();
  return { ok: true };
})()"""

# 当前激活的 Tab（`aria-current="page"`）+ 页面状态，一次算完
STATE_JS = """(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  const cur = nav ? nav.querySelector('[aria-current="page"]') : null;
  const text = document.body.innerText || '';
  const flat = text.replace(/\\s+/g, ' ');
  // 结构检查：内容区里真的挂上了页面组件（而不是一个空 div）
  const wrap = nav ? nav.parentElement.firstElementChild : null;
  const page = wrap ? wrap.firstElementChild : null;
  return {
    path: location.pathname,
    title: document.title,
    activeTab: cur ? (cur.innerText || '').trim() : null,
    tabCount: nav ? nav.querySelectorAll('button').length : 0,
    bodyLen: text.length,
    // 框架级错误页（Next 的 error boundary）——比「字少」可靠得多
    errorBoundary: /Application error|client-side exception|Unhandled Runtime Error/.test(text),
    notFound: /This page could not be found|无法找到此页面/.test(text) && text.length < 600,
    pageChildren: page ? page.children.length : 0,
    // 截 4000 字够用来找标志文案，又不会把整页灌回来
    body: flat.slice(0, 4000),
    head: flat.slice(0, 160),
  };
})()"""

DESKTOP_JS = """(() => {
  const nav = document.querySelector('nav[aria-label="主导航"]');
  const navVisible = nav ? getComputedStyle(nav).display !== 'none' : false;
  const rail = document.querySelector('[class*="w-rail"]');
  const panel = document.querySelector('[class*="w-panel"]');
  const row = rail ? rail.parentElement : null;
  const cols = row ? [...row.children].map(el => ({
    cls: (el.className || '').toString().split(' ').slice(0, 3).join(' '),
    w: Math.round(el.getBoundingClientRect().width),
    visible: el.getBoundingClientRect().width > 0,
  })) : [];
  return {
    innerWidth: innerWidth,
    navExists: !!nav,
    navVisible: navVisible,
    railW: rail ? Math.round(rail.getBoundingClientRect().width) : null,
    panelW: panel ? Math.round(panel.getBoundingClientRect().width) : null,
    cols: cols,
  };
})()"""


async def login(b: Browser) -> bool:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == USER)
    await b.goto(f"{BASE}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


async def wait_path(b: Browser, want: str, timeout: float = 12.0) -> bool:
    """等客户端路由真正切过去（`router.push` 是异步的）。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        got = await b.cdp.evaluate("location.pathname")
        if got == want:
            return True
        await asyncio.sleep(0.25)
    return False


async def main() -> int:
    global BASE

    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", action="store_true", help="每个 Tab 存一张截图（取证用）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="im 实例端口（默认 3003）")
    args = ap.parse_args()
    BASE = f"http://localhost:{args.port}"
    tag = "" if args.port == DEFAULT_PORT else f"-p{args.port}"

    print("── im 四 Tab 点击可达性 + 桌面三栏宽度实测 ──")
    print(f"   目标 {BASE}\n")
    defects: list[str] = []
    env_notes: list[str] = []

    # ============================================================ 阶段 A：移动端点按
    async with Browser(headless=True, width=390, height=844, device_scale_factor=3) as b:
        await b.apply_device(mobile=True)
        if not await login(b):
            print("  ✗ 登录失败 —— 环境问题")
            return 2
        await b.goto(f"{BASE}/", wait=1.5)
        await b.settle(extra=2.5)

        start = await b.cdp.evaluate(STATE_JS)
        if not start or start["tabCount"] != 4:
            print(f"  ✗ TabBar 不是 4 项（实测 {(start or {}).get('tabCount')}）—— 环境问题或结构变更")
            return 2
        print(f"阶段 A：移动端 390×844，TabBar 共 {start['tabCount']} 项\n")

        for label, want_path, marker in TABS:
            r = await b.cdp.evaluate(CLICK_TAB_JS % (f'"{label}"', label))
            if not r or not r.get("ok"):
                env_notes.append(f"点「{label}」失败：{(r or {}).get('why')}")
                print(f"  ✗ 「{label}」点击失败：{(r or {}).get('why')}  —— 环境问题")
                continue
            ok_path = await wait_path(b, want_path)
            await b.settle(extra=1.2)
            s = await b.cdp.evaluate(STATE_JS)

            print(f"  「{label}」→ 路径 {s['path']!r}（期望 {want_path!r}）{'✓' if ok_path else '✗'}")
            print(f"        高亮 Tab = {s['activeTab']!r}（期望 {label!r}）{'✓' if s['activeTab'] == label else '✗'}")
            has_marker = marker in s["body"]
            print(f"        标志文案 {marker!r} 出现: {has_marker}   "
                  f"正文 {s['bodyLen']} 字   页面子节点 {s['pageChildren']}   "
                  f"错误页={s['errorBoundary']}  404={s['notFound']}")
            print(f"        正文开头: {s['head'][:90]}")

            if not ok_path:
                defects.append(f"「{label}」点击后路径为 {s['path']!r}，期望 {want_path!r}")
            if s["activeTab"] != label:
                defects.append(
                    f"「{label}」点击后高亮的却是 {s['activeTab']!r}"
                    f" —— Tab `id` 与一级路径段不一致（激活态是推出来的，不会报错）"
                )
            if s["notFound"]:
                defects.append(f"「{label}」落地到 404 页（路径 {s['path']!r}）")
            if s["errorBoundary"]:
                defects.append(f"「{label}」落地页触发框架错误边界（客户端异常）")
            # ⚠️ 这里刻意**不**用「正文多少字」当判据：客户端的会话数就是 0，
            #    /chat 与 /me 本来就短（73 / 114 字）。拿字数判「空壳」会把
            #    **合法的空状态** 报成缺陷——实测已犯过一次（4 条假红）。
            #    改用结构判据：内容区里真的挂上了页面组件。
            if s["pageChildren"] == 0:
                defects.append(f"「{label}」内容区里没有页面组件（空壳）")
            if not has_marker:
                defects.append(f"「{label}」落地页找不到标志文案 {marker!r} —— 可能渲染成了别的页面")
            if args.shots:
                slug = want_path.strip("/").replace("/", "-") or "home"
                await b.screenshot(HERE / f"shot_im_tab_{slug}{tag}.png", full=False)
            print()

    # ============================================================ 阶段 B：桌面端
    #
    # ⚠️ 两档都测，**不假定宽度**：`tokens.css:244` 有一条
    #    `@media (max-width: 1400px)` 把 rail/panel 整体收窄
    #    （288→250、320→280）。第一版只测 1280px 却按 288/320 断言，
    #    于是报出 2 条**假缺陷**——把「按设计收窄」当成了「宽度受损」。
    #    测两档之后，这条断点行为本身也变成了一条被锁住的断言。
    for width, want_rail, want_panel in ((1280, 250, 280), (1440, 288, 320)):
        async with Browser(headless=True, width=width, height=900, device_scale_factor=1) as b:
            await b.apply_device(mobile=False)
            if not await login(b):
                print("  ✗ 登录失败 —— 环境问题")
                return 2
            await b.goto(f"{BASE}/chat", wait=1.5)
            await b.settle(extra=2.5)
            d = await b.cdp.evaluate(DESKTOP_JS)

            body_w = max((c["w"] for c in d["cols"] if c["visible"]), default=0)
            print(f"阶段 B：桌面端 {d['innerWidth']}×900，落在 /chat")
            print(f"  TabBar 存在={d['navExists']}  可见={d['navVisible']}（期望 False，`lg:hidden`）")
            print(f"  三栏实测（期望 rail={want_rail} panel={want_panel}，"
                  f"{'≤1400 收窄档' if width <= 1400 else '>1400 常规档'}）：")
            for c in d["cols"]:
                print(f"      {c['w']:>5}px  {c['cls']}")
            print(f"  ⇒ 正文栏 ≈ {body_w}px（判据 ≥ 432px，来自 §2.3：带 240 侧栏时的宽度）")
            print()

            if d["navVisible"]:
                defects.append(
                    f"[{width}px] 桌面端 TabBar 仍然可见 —— `lg:hidden` 失效，移动导航跑进桌面"
                )
            if body_w < 432:
                defects.append(f"[{width}px] 桌面端正文栏只有 {body_w}px < 432px（§2.3 判据）")
            if d["railW"] != want_rail:
                defects.append(
                    f"[{width}px] 会话列表栏宽 {d['railW']}px ≠ {want_rail}px（概念图 06 + tokens.css:244 断点）"
                )
            if d["panelW"] != want_panel:
                defects.append(
                    f"[{width}px] 案件上下文栏宽 {d['panelW']}px ≠ {want_panel}px（概念图 06 + tokens.css:244 断点）"
                )

    # ============================================================ 汇总
    print("\n── 汇总 ──")
    if env_notes:
        for n in env_notes:
            print(f"  ⚠ 环境问题：{n}")
    if defects:
        print("  ✗ 发现缺陷：")
        for x in defects:
            print(f"      · {x}")
        print(f"\nEXIT=1  产品缺陷 {len(defects)} 条")
        return 1
    print("  ✓ 4 个 Tab 全部可点、路径正确、高亮正确、内容区非空壳、非 404、无框架错误")
    print("  ✓ 桌面端 1280/1440 两档 TabBar 均消失；三栏宽度符合断点；正文栏 ≥ 432px")
    print("\nEXIT=0  通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
