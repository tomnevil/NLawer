#!/usr/bin/env python
"""断点门禁：把规范 §8.2 的**四档断点**与 §8.4 的**导航模式**变成可执行判据。

## 为什么需要它

盘现有探针的覆盖宽度：

| 探针 | 宽度 |
|---|---|
| `verify_mobile_safe_area.py` / `verify_tap_targets.py` / `verify_mobile_actionbar.py` | 390 |
| `verify_mobile_375.py` | 390 / **375** / 320 |
| `verify_im_tabs.py` | 390 + 1280 / 1440 |
| `verify_runtime_health.py` | 1280 |

⇒ **§8.2 的 `medium`（640–1023）整档没有任何判据**，而 **1024 这个切换点**也只被一次性探针碰过。
而 §8.2 恰在那里写下了最硬的一条规则。

## 规范依据（`design-spec.md` §8.2 / §8.4）

§8.2 表格：`compact` <640 底部 Tab Bar / `medium` 640–1023 底部 Tab Bar /
`expanded` 1024–1599 侧栏 240px 常驻 / `wide` ≥1600 侧栏 240px 常驻。
⚠️ **2026-09-23**：`wide` 原为 `≥1440`、`expanded` 原为 `1024–1439`，与 §4.2 及 preset 矛盾；
已按 **1600** 修订（详见 `verify_appshell.py` 的 R2 与 `design-spec.md` §8.2 的修订注）。

§8.2 **关键规则**：

> 侧栏在 < 1024px 时**不折叠成 64px 图标条，而是完全收起为抽屉**——
> 64px 图标条在手机上仍占用宝贵宽度，且图标无语义提示时难以辨认。

§8.4：「**运营后台**：AppBar + 只读标识，**无 Tab Bar**」。

## 判据

| 判据 | 宽度 | 断言 |
|---|---|---|
| **B1** | < 1024 | **不得**存在「贴左、够高、可见、宽 56–88px」的常驻元素（那就是 64px 图标条）；且常驻侧栏必须不可见 |
| **B2a** | ≥ 1024 | 必须有**导航出口**：常驻左列（贴左、够高、宽 ≥ 200px）**或**指向**其它**一级路由的站内链接。这是 §8.2 该行的**实质要求** |
| **B2b** | ≥ 1024 | 该常驻左列的宽度必须 ≈ **令牌 `--sidebar-w`**（**期望值取自令牌，不硬编码 240**）；**im 豁免**（见下） |
| **B3** | < 1024 | 配了 Tab 的端（web/lawyer/im）必须有可见的 `nav[aria-label="主导航"]`；**admin 必须没有** |
| **B3'** | ≥ 1024 | TabBar 必须**不可见**（`TabBar.tsx:36` 是 `lg:hidden`） |

> ⚠️ **B3' 的出处是「实现」不是「规范」**：§8.2 只规定 ≥1024 的导航形态是侧栏，
> 没写「TabBar 必须隐藏」。B3' 断言的是**实现自洽**（既然 `TabBar` 自己声明了 `lg:hidden`，
> 就不能在 ≥1024 又出现）。`verify_im_tabs.py` 阶段 B 也独立断言过同一件事。
> ⇒ 它是一条**一致性**判据，不是规范条文；将来若要改「≥1024 也保留 TabBar」，
> 应先改 `TabBar.tsx` 再改这条。

### 🚨 B2 为什么拆成 a / b（第一版判错的记录）

第一版只有一条 B2：**「≥1024 必须存在可见的 `aside`，宽 = 令牌」**。全量跑下来 im 在 1024 / 1440
两档报红 —— 但那是**判据错了，不是产品坏了**：

- **§8.2 的「要求」是「该宽度下导航可用」这个效果**；
  **`aside` 与「宽 = 240px」是「手段」**（`AppShell` 的实现方式）。
  把手段写成判据 ⇒ 「用别的方式达成了同一要求」被判成缺陷。
- im 的桌面形态**不是** AppShell，而是**满屏三栏**，有四个独立出处：
  - 概念图 `mockups/06-im-chat.html:27` → `.im{display:grid;grid-template-columns:288px 1fr 320px}`
  - §8.1 表格 → im「设计基准直接按 375px 起草，**桌面视为放大适配**」
  - 决策 12 → 「IM 端形态 ✓ 桌面三栏 + 移动端优先单栏」
  - 另有 `apps/im/app/(app)/layout.tsx:10-17` 写下的取舍理由（叠 240px 后正文只剩 432px）
- 它的最左栏是**会话列表**（内容），不是**导航侧栏**（§4.1 的墨色导航）——
  语义不同，不该套同一条判据。

第二版把 B2a 写成「必须有常驻左列」——**仍然过宽**，因为那是**手段**而非要求。
实测 im 四条路由的 ≥1024 形态（`--dump`，2026-09-20）：

| 路由 | 常驻左列 | 站内一级链接 | TabBar |
|---|---|---|---|
| `/` | 无 | `cases` `chat` | 隐藏 |
| `/chat` | **250px @1024 / 288px @1440** | 无 | 隐藏 |
| `/cases` | 无 | **仅 `cases`（自链）** | 隐藏 |
| `/me` | 无 | `cases` `chat` | 隐藏 |

⇒ 定稿为 **B2a = 导航出口存在**（左列 **或** 指向其它一级路由的链接；**自链不算出口**）
+ **B2b = 左列宽 = 令牌，im 豁免**。豁免**不是放行**：B2a 对 im 照样生效，而且**确实检出了**
`/cases` 在 ≥1024 只能链回自己（见 §9 待裁决项 11 与 `admin-gap-analysis.md` #25）。

⚠️ 另有一处**规范冲突**：决策 05「统一四端 AppShell」 vs §8.1 / 决策 12。
已登记 §9，**不擅自改规范**。

## 前提锁（前提不成立就不判）

- 页面上必须有 `aside` / `nav[aria-label="主导航"]` / `header` 之一（**存在即可**，不要求可见——
  im 在 ≥1024 时 TabBar 被 `lg:hidden` 隐藏、又没有 `aside`，若要求「可见」会把该档整体跳过、
  把问题藏起来），**且正文长度 ≥ 40 字符** —— 否则说明页面没渲染完，
  **这是「我没测成」，不是「产品坏了」**。
- 令牌 `--sidebar-w` 必须能读到 —— 读不到就不判 B2b。
- 用 `setDeviceMetricsOverride` **就地**改宽度（不重新导航），并断言 `innerWidth == 期望宽度`。

## 退出码

`0` 通过 / `1` 产品缺陷 / `2` 环境问题（含**自检失败**：那是工具坏了，不是产品坏了）

用法：

    python evidence/verify_breakpoints.py
    python evidence/verify_breakpoints.py --self-test
    python evidence/verify_breakpoints.py --shot
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 演示账号表在 `app.seed.data` 里 —— 账号**不硬编码**，从后端同一份源头读
# （与 `verify_mobile_safe_area.py:70` 同一做法）。
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

# ---------------------------------------------------------------------------
# 四档断点。768 是**此前完全没覆盖**的那一档。
# ⚠️ 2026-09-23：§8.2 的 `wide` 档由 `≥1440` 修订为 `≥1600`（规范自相矛盾，见 `verify_appshell.py` R2）。
#    本点仍是 1440（真实笔电宽度），按新档位它落在 `expanded`（1024–1599）内。
#    **不改成 1600 是刻意的**：本门禁判的是「≥1024 侧栏常驻 + 宽 = 令牌」，而 `wide` 行的
#    **导航形态与 `expanded` 完全相同**（只差内容区最大宽，那不在本门禁的判据里）
#    ⇒ 1440 已足以覆盖 `wide` 的那条要求；改宽度不会增加判别力，
#      只会让这个浏览器门禁丢掉「真实笔电宽度」这个代表值。
# ---------------------------------------------------------------------------
WIDTHS: tuple[tuple[int, int, str], ...] = (
    (390, 844, "compact（<640）"),
    (768, 1024, "medium（640–1023）← 此前无判据"),
    (1024, 900, "expanded 起点（=1024，切换点）"),
    (1440, 900, "expanded 上段（1440；`wide` 的侧栏要求与此档相同，故此处亦覆盖）"),
)

# 每端一个**稳定页面**（首页），避免依赖真实 id 解析。
# `user` 取自 `app.seed.data.DEMO_USERS`（四端各有自己的演示账号）。
APPS: dict[str, dict] = {
    "web": {"port": 3000, "path": "/", "tabs": True, "user": "ent_admin"},
    "lawyer": {"port": 3001, "path": "/", "tabs": True, "user": "lawyer_wang"},
    "admin": {"port": 3002, "path": "/", "tabs": False, "user": "admin"},  # §8.4：无 Tab Bar
    "im": {"port": 3003, "path": "/", "tabs": True, "user": "client"},
}

# ≥1024 时**额外**要探的一级路由（只探桌面两档，移动档不重复探）。
#
# 为什么 im 要四条、其余三端只要首页：
#   web / lawyer / admin 用 AppShell，**每一页**都有 240px 常驻左列 ⇒ B2a 在任何一页都自动成立，
#   多探是浪费。im 没有 AppShell，导航出口**逐页不同**（`/chat` 靠左列、`/` 与 `/me` 靠页内链接、
#   `/cases` 什么都没有）⇒ 必须逐条探，否则 `/cases` 那类「进得去出不来」会被首页的绿灯掩盖。
#   这四条正是 §8.4 给客户端定的 4 项 Tab 路由。
DESKTOP_ROUTES: dict[str, tuple[str, ...]] = {
    "web": (),
    "lawyer": (),
    "admin": (),
    "im": ("/chat", "/cases", "/me"),
}

# 64px 图标条的判定区间。§8.2 说的是「64px 图标条」，两侧留余量：
# 56px 是 TabBar 高度那一档、88px 已明显超出图标条。常驻侧栏是 240px，抽屉也是 240px，
# 因此这个区间在**正确实现**上应当是空的。
RAIL_MIN, RAIL_MAX = 56, 88

# 「常驻左列」的宽度区间（B2a）。下界 200px：§8.2 的三档常驻侧栏最小是 240px，
# im 的会话列表在 1280px 下收窄到 250px（概念图 06:154 的 `@media (max-width:1400px)`）；
# 上界 420px 用来排除「整个内容区」这类误命中。
COL_MIN, COL_MAX = 200, 420

# 🚨 B2b 的**豁免表**。豁免必须带出处，且必须只豁免 B2b（B2a 仍然生效）。
SIDEBAR_TOKEN_EXEMPT: dict[str, str] = {
    "im": (
        "im 的桌面形态是**满屏三栏**，不是 AppShell ⇒ 不适用「宽 = 令牌 --sidebar-w」。"
        "出处：概念图 06:27 `grid-template-columns:288px 1fr 320px`；"
        "§8.1「桌面视为放大适配」；决策 12「IM 端形态 ✓ 桌面三栏」。"
        "最左栏是**会话列表**（内容），不是 §4.1 的墨色导航侧栏。"
        "⚠️ 与 §4.1 / §8.2 表 / 决策 05「统一四端 AppShell」冲突，已登记 §9 待裁决。"
    ),
}

# ---------------------------------------------------------------------------
# 测量 JS。只做**观察**，不下判断（判断在 Python 侧，便于自检直接喂合成数据）。
# ---------------------------------------------------------------------------
MEASURE_JS = r"""(() => {
  const tok = getComputedStyle(document.documentElement).getPropertyValue('--sidebar-w').trim();
  const vh = innerHeight;
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const asides = [...document.querySelectorAll('aside')].map((el) => {
    const r = el.getBoundingClientRect();
    return { w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), visible: visible(el) };
  });
  // 常驻**左列**（不分 position —— AppShell 用 fixed、im 的三栏用 grid 子项，两者都算）。
  // 只做观察：B1 从里面挑 56–88px 的（图标条），B2a 从里面挑 ≥200px 的（常驻侧栏）。
  const leftBars = [...document.querySelectorAll('body *')].filter((el) => {
    if (el.getAttribute('aria-hidden') === 'true') return false;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.width > 420) return false;      // 太窄不是导航、太宽是内容区
    if (r.width > innerWidth * 0.5) return false;          // 排除根容器
    if (r.height < vh * 0.6) return false;                 // 必须够高（常驻）
    if (r.left > 40) return false;                         // 必须贴左
    return true;
  }).map((el) => {
    const r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      w: Math.round(r.width),
      isAside: el.tagName.toLowerCase() === 'aside',
      ariaLabel: el.getAttribute('aria-label') || '',
    };
  });
  const tb = document.querySelector('nav[aria-label="主导航"]');
  const shell = document.querySelector('aside, nav[aria-label="主导航"], header');
  // 页内**导航出口**：指向一级路由的站内链接（用于判「TabBar 藏起来之后还能不能走」）。
  // 只取 pathname 第一段，去重 —— 数量不重要，「有没有」才重要。
  const links = [...new Set([...document.querySelectorAll('a[href]')].map((a) => {
    const h = a.getAttribute('href') || '';
    if (!h.startsWith('/')) return null;                 // 站外 / 锚点不算
    const seg = h.split('#')[0].split('?')[0].split('/').filter(Boolean)[0];
    return seg || null;
  }).filter(Boolean))].sort();
  return {
    vw: innerWidth,
    sidebarToken: tok,
    asides,
    leftBars,
    links,
    hasShell: !!shell,
    contentLen: (document.body.innerText || '').trim().length,
    tabbarExists: !!tb,
    tabbarVisible: tb ? visible(tb) : false,
  };
})()"""


# ---------------------------------------------------------------------------
# 判据（纯函数，可被自检直接喂数据）
# ---------------------------------------------------------------------------
def judge(m: dict, app: str, w: int, tabs_expected: bool, route: str = "/") -> list[str]:
    """返回问题列表（空 = 通过）。

    `w` 宽度，`tabs_expected` 来自 §8.4 的端配置，`route` 是本次探测的路径（判自链要用）。
    """
    bad: list[str] = []
    bars = m.get("leftBars") or []
    cur_seg = next(iter([s for s in route.split("/") if s]), "")

    if w < 1024:
        # —— B1：<1024 不得出现 64px 图标条；常驻侧栏必须不可见 ——
        rails = [e for e in bars if RAIL_MIN <= e["w"] <= RAIL_MAX]
        if rails:
            bad.append(f"[B1 {w}px] 出现了「{rails[0]['w']}px 宽的贴左常驻元素」"
                       f"（tag={rails[0]['tag']}）—— §8.2 明确「不折叠成 64px 图标条」")
        vis_asides = [a for a in m["asides"] if a["visible"]]
        if vis_asides:
            bad.append(f"[B1 {w}px] <1024 仍有可见的常驻侧栏（宽 {vis_asides[0]['w']}px）"
                       f"—— §8.2 要求「完全收起为抽屉」")
    else:
        # —— B2a：≥1024 必须有**导航出口**（§8.2 该行的实质要求：该宽度下导航可用）——
        #    TabBar 在这一档被 `lg:hidden` 藏起来了（B3' 正是在断言这一点），
        #    所以必须另有一条路：**常驻左列**，或**指向其它一级路由的站内链接**。
        cols = [e for e in bars if e["w"] >= COL_MIN]
        if not cols:
            others = [x for x in (m.get("links") or []) if x != cur_seg]
            if not others:
                bad.append(
                    f"[B2a {w}px] ≥1024 时既无常驻左列，也没有指向**其它**一级路由的链接"
                    f"（页内链接={m.get('links') or []}）⇒ 该宽度下**没有导航出口**："
                    f"TabBar 已被 `lg:hidden` 隐藏，用户只能靠浏览器后退")
        elif app in SIDEBAR_TOKEN_EXEMPT:
            # 有常驻左列即可。**不**断言它宽 = 令牌（豁免出处见 SIDEBAR_TOKEN_EXEMPT）。
            pass
        else:
            # —— B2b：该左列必须就是 AppShell 导航侧栏，宽度 = 令牌值 ——
            token = m.get("sidebarToken") or ""
            if not token:
                # 前提不成立：读不到令牌 ⇒ 不判（这是「我没测成」）
                bad.append(f"[B2b {w}px] 前提不成立：读不到令牌 `--sidebar-w`")
            else:
                want = float(token.rstrip("px"))
                if abs(cols[0]["w"] - want) > 1:
                    bad.append(f"[B2b {w}px] 常驻左列宽 {cols[0]['w']}px，令牌 `--sidebar-w` 是 {want:.0f}px"
                               f"（差 {cols[0]['w'] - want:+.0f}px）")

    # —— B3 / B3'：TabBar ——
    if w < 1024:
        if tabs_expected:
            if not m["tabbarExists"]:
                bad.append(f"[B3 {w}px] 该端配了 Tab，但页面上没有 `nav[aria-label=\"主导航\"]`")
            elif not m["tabbarVisible"]:
                bad.append(f"[B3 {w}px] <1024 时 TabBar 应可见（§8.2 底部 Tab Bar），实测不可见")
        else:
            if m["tabbarVisible"]:
                bad.append(f"[B3 {w}px] 该端**不应有** Tab Bar（§8.4），实测可见")
    else:
        if m["tabbarVisible"]:
            bad.append(f"[B3' {w}px] ≥1024 时 TabBar 应不可见（`TabBar.tsx:36` 是 `lg:hidden`），实测可见")

    return bad


# ---------------------------------------------------------------------------
# 前提锁（抽成纯函数：自检与实跑走**同一条**路径，否则自检锁不住实跑）
# ---------------------------------------------------------------------------
MIN_CONTENT = 40


def premise_problems(m: dict | None, w: int) -> list[str]:
    """前提不成立时返回原因（空 = 前提成立 ⇒ 可以判）。"""
    if not m:
        return ["未取到测量结果"]
    if m.get("vw") != w:
        return [f"视口 {m.get('vw')} ≠ {w}"]
    if not m.get("hasShell"):
        return ["页面上没有 aside / nav[主导航] / header"]
    if (m.get("contentLen") or 0) < MIN_CONTENT:
        return [f"正文仅 {m.get('contentLen')} 字符（< {MIN_CONTENT}）⇒ 疑似未渲染完"]
    return []


# ---------------------------------------------------------------------------
# 自检：**合成页面**。测的是「测量 JS + 前提锁 + 判据」整条链。
# ---------------------------------------------------------------------------
# 每臂都带一段 ≥40 字符的正文，否则会撞上 MIN_CONTENT 前提锁而「不判」——
# 那样自检就绕过了实跑的路径，锁不住东西。
_FILLER = ("本段为合成夹具的填充正文，用于让页面通过「已渲染」前提锁，"
           "不含任何产品语义。")
_SHELL = "<header style='height:56px;background:#eee'>顶栏</header>"


def _tabs_html(visible_ok: bool) -> str:
    cls = "position:fixed;left:0;right:0;bottom:0;height:56px;background:#ddd"
    return f"<nav aria-label='主导航' style='{cls}'>导航</nav>"


# 一根 288px 的常驻左列，**故意不用 `aside`** —— 复刻 im 的三栏（grid 子项）。
_COL288 = "<div style='position:absolute;left:0;top:0;bottom:0;width:288px;background:#999'>会话</div>"

SELFTEST: tuple[tuple[str, str, int, str, str, bool, bool, str], ...] = (
    # key, 描述, 宽度, html, app, 期望有 tabs, 期望判红, 路由
    ("Q1", "768px 下存在 64px 图标条 ⇒ B1 **必须报红**", 768,
     _SHELL + "<div style='position:fixed;left:0;top:0;bottom:0;width:64px;background:#bbb'></div>"
     + _tabs_html(True) + _FILLER, "web", True, True, "/"),
    ("Q2", "768px 下无图标条 + 有 TabBar ⇒ **必须干净**（对照组）", 768,
     _SHELL + _tabs_html(True) + _FILLER, "web", True, False, "/"),
    ("Q3", "1280px 下 240px 常驻侧栏 ⇒ B2a/B2b **必须干净**", 1280,
     _SHELL + "<aside style='position:fixed;left:0;top:0;bottom:0;width:240px;background:#999'></aside>"
     + _FILLER,
     "web", True, False, "/"),
    ("Q4", "1280px 下无侧栏**也无链接** ⇒ B2a **必须报红**", 1280, _SHELL + _FILLER, "web", True, True, "/"),
    ("Q5", "390px 下 admin 出现 TabBar ⇒ B3 **必须报红**（§8.4 无 Tab Bar）", 390,
     _SHELL + _tabs_html(True) + _FILLER, "admin", False, True, "/"),
    # —— 下面五臂锁「豁免不是放行」+「自链不算出口」——
    ("Q9", "1280px 下 im 有 288px 常驻左列（**非 aside**）⇒ **必须干净**（B2b 豁免，B2a 仍生效）",
     1280, _SHELL + _COL288 + _FILLER, "im", True, False, "/"),
    ("Q10", "1280px 下 im **无左列且无链接** ⇒ B2a **仍须报红**（豁免不得变成放行）", 1280,
     _SHELL + _FILLER, "im", True, True, "/"),
    ("Q11", "1280px 下 im 无左列、但有指向**其它**一级路由的链接 ⇒ **必须干净**"
            "（复刻 im `/` 的实际形态）", 1280,
     _SHELL + "<a href='/chat'>咨询</a><a href='/cases'>案件</a>" + _FILLER, "im", True, False, "/"),
    ("Q12", "1280px 下 im 无左列、**只有自链**（route=/cases，唯一链接是 /cases）⇒ **必须报红**"
            "（复刻 im `/cases` 的实际形态：用户进得去出不来）", 1280,
     _SHELL + "<a href='/cases'>案件</a>" + _FILLER, "im", True, True, "/cases"),
    ("Q13", "1280px 下 web 无左列、只有自链（route=/cases）⇒ **必须报红**（同一判据对所有端生效）",
     1280, _SHELL + "<a href='/cases'>案件</a>" + _FILLER, "web", True, True, "/cases"),
)

_SELFTEST_CSS = ":root{--sidebar-w:240px}html,body{height:100%;margin:0}"


def _selftest_page(html: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_SELFTEST_CSS}</style></head><body>{html}</body></html>"
    )


async def run_self_test() -> int:
    print("自检（合成页面，与产品无关）—— 各臂分开报：")
    bad: list[str] = []
    async with Browser(headless=True, width=1280, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        for key, desc, w, html, app, tabs_expected, expect_red, route in SELFTEST:
            url = "data:text/html;charset=utf-8," + urllib.parse.quote(_selftest_page(html))
            await b.goto(url, wait=0.35)
            await b.cdp.send("Emulation.setDeviceMetricsOverride",
                             width=w, height=900, deviceScaleFactor=1, mobile=False)
            m = await b.cdp.evaluate(MEASURE_JS)
            print(f"  [{key}] {desc}")
            pre = premise_problems(m, w)
            if pre:
                # 自检里「前提不成立」= 夹具写坏了，属工具故障
                print(f"        前提不成立：{pre[0]}  ⇒ **夹具坏了，不是判据坏了**")
                bad.append(f"[{key}] 前提不成立：{pre[0]}")
                continue
            problems = judge(m, app, w, tabs_expected, route)
            got_red = bool(problems)
            ok = got_red == expect_red
            print(f"        期望{'报红' if expect_red else '干净':4s} 实测{'报红' if got_red else '干净':4s} "
                  f"{'✓' if ok else '✗'}")
            if problems:
                for p in problems:
                    print(f"          · {p}")
            if not ok:
                bad.append(f"[{key}] 期望{'报红' if expect_red else '干净'} 实测{'报红' if got_red else '干净'}")

    # —— 收口判定（纯函数臂，不需要浏览器）——
    # 这三臂锁的是**我自己刚犯过的错**：把「一组都没测到」印成「全部符合规范」。
    for key, desc, args, expect in (
        ("Q6", "收口判定：**0 组测量不得算通过**（这是「我没测成」）", (0, [], ["x@390: 无 shell 标记"]), 2),
        ("Q7", "收口判定：有缺陷 ⇒ exit 1", (4, ["bad"], []), 1),
        ("Q8", "收口判定：全通过 ⇒ exit 0", (4, [], []), 0),
    ):
        got = summary_exit(*args)
        ok = got == expect
        print(f"  [{key}] {desc}")
        print(f"        期望 exit {expect}  实测 exit {got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望 exit {expect} 实测 exit {got}")

    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **工具坏了，不是产品坏了**（exit 2）")
        for x in bad:
            print(f"  - {x}")
        return 2
    print("\n自检通过：检出能力（Q1/Q4/Q5/Q10/Q12/Q13）与对照组干净（Q2/Q3/Q9/Q11）均符合预期。")
    return 0


def summary_exit(total: int, all_bad: list[str], skipped: list[str]) -> int:
    """收口判定（抽成纯函数，便于自检锁住「0 组 ≠ 通过」这条）。

    🚨 `total == 0` 必须返回 **2**，不能返回 0 —— 那是「我没测成」，不是「产品很健康」。
    本项目已有先例：写「已访问 1 页 → 0 个问题」被当成「这一端很健康」。
    """
    if total == 0:
        return 2
    if all_bad:
        return 1
    return 0


async def login(b: Browser, base: str, username: str) -> bool:
    """与 `verify_mobile_safe_area.py:196` 同一套做法，账号取自 `app.seed.data.DEMO_USERS`。"""
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == username)
    await b.goto(f"{base}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


async def run(shot: bool) -> int:
    total, failed = 0, 0
    all_bad: list[str] = []
    skipped: list[str] = []
    env_fail: list[str] = []
    exempted: list[str] = []
    if SIDEBAR_TOKEN_EXEMPT:
        print("⚠️ B2b 豁免（**只豁免「左列宽 = 令牌」这一手段**；"
              "B2a「≥1024 必须有导航出口」对 im **照样生效**）：")
        for a, why in SIDEBAR_TOKEN_EXEMPT.items():
            print(f"  · {a}：{why}")
        print()
    async with Browser(headless=True, width=390, height=844) as b:
        await b.apply_device(safe_area=None, mobile=False)
        for app, cfg in APPS.items():
            base = f"http://localhost:{cfg['port']}{cfg['path']}"
            print(f"\n══ {app}  {base}   （Tab：{'有' if cfg['tabs'] else '无（§8.4）'}）")
            # ⚠️ Cookie **不区分端口**（同 host 共享）⇒ 换端前必须清，
            # 否则上一端的 refresh cookie 会让本端 `restoreSession()` 复原成别人的会话。
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                print(f"  [env] {app} 登录失败 ⇒ 本端**全部不判**")
                env_fail.append(f"{app}: 登录失败")
                continue
            await b.goto(base, wait=1.6)
            await b.settle(extra=1.0)
            # 探测计划：首页 × 四档，外加（仅 ≥1024 两档）各端的额外一级路由。
            plan: list[tuple[str, int, int, str]] = [
                (cfg["path"], w, h, label) for w, h, label in WIDTHS
            ]
            for r in DESKTOP_ROUTES.get(app, ()):
                plan += [(r, w, h, f"{label} @{r}") for w, h, label in WIDTHS if w >= 1024]
            cur_path = cfg["path"]
            for path, w, h, label in plan:
                if path != cur_path:
                    await b.goto(f"http://localhost:{cfg['port']}{path}", wait=1.6)
                    await b.settle(extra=1.0)
                    cur_path = path
                await b.cdp.send("Emulation.setDeviceMetricsOverride",
                                 width=w, height=h, deviceScaleFactor=1, mobile=False)
                await b.settle(0.5)
                m = await b.cdp.evaluate(MEASURE_JS)
                pre = premise_problems(m, w)
                if pre:
                    print(f"  {label:28s} 前提不成立：{pre[0]} ⇒ **不判**")
                    skipped.append(f"{app}{path}@{w}: {pre[0]}")
                    continue
                total += 1
                problems = judge(m, app, w, cfg["tabs"], path)
                if problems:
                    failed += 1
                    print(f"  {label:28s} ✗")
                    for p in problems:
                        print(f"      {p}")
                    all_bad.extend(p for p in problems)
                else:
                    tb = "TabBar可见" if m["tabbarVisible"] else "无TabBar"
                    cols = [e for e in (m.get("leftBars") or []) if e["w"] >= COL_MIN]
                    col = f"常驻左列={cols[0]['w']}px" if cols else "无常驻左列"
                    # ⚠️ 必须同时要求 `cols` 非空：im 在 ≥1024 也可能**没有**左列
                    #    （`/` 与 `/cases` 就是），那时走的是 B2a 的「页内链接」通道，
                    #    不该打豁免标记，更不该取 `cols[0]`。
                    exempt = "（B2b 已豁免）" if (w >= 1024 and cols and app in SIDEBAR_TOKEN_EXEMPT) else ""
                    if exempt:
                        exempted.append(f"{app}{path}@{w}: {cols[0]['w']}px 常驻左列，B2b 未断言（豁免）")
                    print(f"  {label:28s} ✓   {col}{exempt}  {tb}")
                if shot:
                    await b.screenshot(HERE / f"shot_breakpoint_{app}_{path.strip('/') or 'home'}_{w}.png",
                                       full=False)

    print("\n" + "=" * 74)
    print(f"测量 {total} 组；不合格 {failed} 组。")
    if skipped:
        print(f"跳过 {len(skipped)} 组（前提不成立，**不算通过也不算失败**）：")
        for s in skipped:
            print(f"  · {s}")
    if env_fail:
        print(f"环境失败 {len(env_fail)} 项：")
        for s in env_fail:
            print(f"  · {s}")
    if exempted:
        print(f"⚠️ 其中 {len(exempted)} 组走了 B2b 豁免（**不代表它符合 §8.2 的「240px 导航侧栏」**）：")
        for s in exempted:
            print(f"  · {s}")

    # 🚨 **0 组测量绝不算通过**：那是「覆盖率不足 / 我没测成」，不是「产品很健康」。
    #    （本项目已有先例：写「已访问 1 页 → 0 个问题」被当成「这一端很健康」。）
    code = summary_exit(total, all_bad, skipped)
    if code == 2:
        print("\n[env] **一组都没测到** ⇒ 这是「我没测成」，不是「产品没问题」（exit 2）")
        return 2

    if code == 1:
        print("\n缺陷：")
        for p in all_bad:
            print(f"  ✗ {p}")
        return 1

    if skipped:
        print(f"\n⚠️ 通过，但**有 {len(skipped)} 组被跳过** ⇒ 覆盖率不满，别当成全覆盖。")
    print("§8.2 四档断点与 §8.4 导航模式符合规范（限已测到的组）。")
    if exempted:
        print("⚠️ 注意：im 的 ≥1024 两档是**按豁免通过**的 —— 见上方豁免理由与 §9 待裁决项。")
    return 0


async def dump(routes: list[str]) -> int:
    """诊断模式：打印原始测量值，**不下判断**。用于核对判据的期望值出处。"""

    async with Browser(headless=True, width=1440, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        for app, cfg in APPS.items():
            base = f"http://localhost:{cfg['port']}"
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                print(f"══ {app}: 登录失败")
                continue
            paths = [p for a, p in (r.split("=", 1) for r in routes if "=" in r) if a == app] or [cfg["path"]]
            for p in paths:
                await b.goto(f"{base}{p}", wait=1.6)
                await b.settle(extra=1.0)
                print(f"\n══ {app}  {p}")
                for w, h, label in WIDTHS:
                    await b.cdp.send("Emulation.setDeviceMetricsOverride",
                                     width=w, height=h, deviceScaleFactor=1, mobile=False)
                    await b.settle(0.5)
                    m = await b.cdp.evaluate(MEASURE_JS)
                    if not m:
                        print(f"  {label:28s} —")
                        continue
                    cols = [e for e in (m.get("leftBars") or []) if e["w"] >= COL_MIN]
                    print(f"  {label:28s} vw={m['vw']} 左列={[c['w'] for c in cols]} "
                          f"链接={m.get('links')} TabBar可见={m['tabbarVisible']} "
                          f"令牌={m.get('sidebarToken')!r} 正文={m.get('contentLen')}字")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="§8.2 断点与 §8.4 导航模式门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑合成页面自检")
    ap.add_argument("--shot", action="store_true", help="每档出一张视口截图")
    ap.add_argument("--dump", action="store_true", help="诊断模式：打印原始测量值，不下判断")
    ap.add_argument("--route", action="append", default=[],
                    help="诊断模式限定路由，可重复，形如 `im=/me`")
    args = ap.parse_args()
    try:
        if args.self_test:
            return asyncio.run(run_self_test())
        if args.dump:
            return asyncio.run(dump(args.route))
        return asyncio.run(run(args.shot))
    except Exception as e:
        print(f"[env] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
