#!/usr/bin/env python
"""`design-spec.md` §4.1「AppShell（桌面形态）」的**静态地板**。

## 为什么需要它：判据**不是不存在**，而是**PR 上跑不了**

§4.1 的覆盖者是 `verify_breakpoints.py` —— 它**需要真浏览器**（CDP 测量 `env()`/媒体查询），
**而且没有免浏览器开关**（只有 `--self-test` / `--shot` / `--dump` / `--route`）。

📌 **2026-09-26 状态更新**（拍板 §11.4-③）：它**已接进 CI**（`browser-all` job，
`run_ci_probes.CI_ELSEWHERE`），但那个 job **只在主干 / 手动触发**
（四端生产构建 + 探针 ≈22–25 min，挂 PR 会拖垮反馈回路）
⇒ **PR 上仍然一条守 §4.1 的判据都没有**，而 PR 才是「不让问题进主干」的主战场。
⇒ 本静态地板的理由**不但没消失，反而更硬**：它是**PR 上唯一**守 §4.1 的那条。

同 `verify_im_mobile_nav.py` 的先例：**渲染级探针守「量出来不对」，静态地板守「改错了」** ——
互补，不重复。几何量只有真浏览器有值；而「令牌被改成了别的数」「类名没接上令牌」
「抽屉形态被删掉」这些**在源码层就能判**。

## 分工表（**不重叠**）

| 要求 | 谁判 | 在哪 |
|---|---|---|
| 侧栏**渲染宽** ≈ 令牌 `--sidebar-w` | `verify_breakpoints.py` **B2b** | CDP，**只在主干跑**（`browser-all`） |
| `--topbar-h` == 56px **且** AppShell 真引用 | `verify_component_refactor.py` **J3** | GATED（归属 §6.3） |
| 侧栏激活项 3px 金色指示条 | `verify_component_refactor.py` **J2** | GATED |
| **本门禁** | 令牌值 / preset 映射 / 类真的被用 / 抽屉形态 / 图标条禁令 | GATED |

⚠️ **「顶部 56px」本门禁不判** —— J3 已经在 CI 里判了；重复判只会产生**两处要同步的期望值**。
但 A0 仍会**从 §4.1 现读**它并**打印**出来（**看得见 ≠ 判**）。

## 判据

- **A0 守卫**：从 §4.1 **现读**期望值（侧栏 px · 顶栏 px · 内容最大宽三档 · 图标条禁令 px · 三端清单）。
  任一条读不出 ⇒ **exit 2** —— 绝不在**空期望值**上「全部通过」。
- **A1 令牌值**：`tokens.css` 的 `--sidebar-w` == 现读值；
  `--content-{workbench,wide,reading}` == 现读三档。
- **A2 令牌不是死的**：preset 必须把 `width.sidebar` 映射到 `var(--sidebar-w)`，
  **且** `AppShell.tsx` 真的用了 `w-sidebar`（否则令牌存在但没人用 ⇒ **改令牌等于没改**）。
- **A3a 常驻侧栏形态**：侧栏元素的 className 必须**同时**带 `hidden` 与 `lg:flex`（≥1024px 常驻）。
- **A3b 抽屉子树形态**：把 `{drawerOpen && ( … )}` 的**配对括号内容**取出来（= 抽屉子树），
  要求 `lg:hidden` 出现在**子树内部**、且**在第一个 `z-drawer` 之前**（⇒ 它落在**祖先**元素上）。
  ⚠️ 第一版写的是「`{drawerOpen &&` **之后**任意处有 `lg:hidden`」——
  而顶栏那个「打开导航」汉堡按钮（`lg:hidden`）**也在锚点之后** ⇒ 删掉抽屉容器的守卫它照样绿。
  注入臂 **I5 实测 rc=0（期望 1）** 才把它暴露出来：这是「一条只会绿的判据」的**锚点范围**版本
  （坑 33 家族，只是这次不在谓词的同义反复里，而在**搜索范围**里）。
  ⚠️ 语义必须分清（**0 处 = 缺陷 / 解析不了 = 环境**）：
  · 括号**不配对** ⇒ **exit 2**（我的解析失败）；
  · 括号配好但子树里**没有** `z-drawer` ⇒ **rc 1 产品缺陷**（抽屉被提出条件渲染了）；
  · 抽屉侧栏整个消失（`drawers` 为空）⇒ **跳过本组**，由 A2 段的「找不到抽屉侧栏」出 rc 1。
  三条都不能混：把产品缺陷判成 exit 2 会**假阴性**，把解析失败判成 rc 1 会**假红**。
- **A4 图标条禁令**：`AppShell.tsx`（**剥注释**）不得出现 §4.1 现读的那个 px 宽
  —— 判据是 `w-{px/4}`（Tailwind 间距刻度）与 `w-[{px}px]`（任意值）两种写法。
  ⚠️ 这是**明文禁令**（§4.1 与第 8.2 节的关键规则**各写了一遍**）。
  ⚠️ 判据范围是**整个文件**（今天实测 0 处命中）⇒ 若将来出现**合法的**同值宽度
  （例如一个 64px 头像），要把范围收窄到**侧栏相关元素**，**别把它当产品缺陷报**。

## 只报不判（R 组）

- **R1**：§4.1 点名三端（web / lawyer / admin），但 `AppShell` 只提供 `apps` prop 的**机制**，
  **清单在调用方** ⇒ 本条只报「机制在不在」，不判清单。
- **R2** ✅ **第 8.2 节的 `wide` 断点（2026-09-23 已裁定 = 1600）** ——
  裁定前规范自己矛盾：§4.2 写「断点 640 / 1024 / 1280 / **1600**」，第 8.2 节的表写 `wide ≥ **1440px**`；
  而 preset 的 `screens.wide = 1600px`、全仓 **0 处** `wide:` 工具类（**死配置**）。
  ⇒ 裁定**按 1600 对齐**（1600 有三处实现支撑：`screens.wide` / `maxWidth.wide` / `--content-wide`；
  1440 在实现里**根本不存在**）：§8.2 的 `wide` 改为 `≥1600`，`expanded` 上界**一并**改为 `1599`
  —— **只改 `wide` 会留下 1440–1599 的空档**（我原先建议的「单点改动」是错的）。
  ⇒ 本门禁仍**不判断点**，只报现值。
  ⚠️ **本行是「裁定记录」不是「现读比对」**：它**不会**发现 §8.2 又改回 1440（只报不判、不解析 §8.2）。
  若要把这条变成真判据，需让 R2 现读 §8.2 的 `wide` 行 —— 属仪器改动，**未做**。
  ⚠️ R2 的文案**刻意不写 `§` 符号**：覆盖率报表把「判据代码体里的 `§x.y` 字面量」算作**覆盖**，
  在**只报**的文案里写 `§8.2` 会造出**假覆盖**（见 `evidence/README.md` 坑 38）。

用法：

    python evidence/verify_appshell.py              # 0 通过 / 1 产品缺陷 / 2 环境问题
    python evidence/verify_appshell.py --self-test  # 纯合成夹具自检（不读真实仓库）
    python evidence/verify_appshell.py --inject     # 真仓库**内存**注入反证（不碰产品文件）
    python evidence/verify_appshell.py --why 4.1    # 打印出处与归属
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import NamedTuple

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

SPEC_REL = "deliverables/ui-design/design-spec.md"
TOKENS_REL = "frontend/packages/ui/src/tokens.css"
PRESET_REL = "frontend/tailwind.preset.ts"
SHELL_REL = "frontend/packages/ui/src/components/AppShell.tsx"

REQUIRED_FILES = (SPEC_REL, TOKENS_REL, PRESET_REL, SHELL_REL)

# 规范里「内容区最大宽」三个档位的**标签 → 令牌名后缀**。
# ⚠️ 这是**按语义**做的映射（阅读型页面→reading / 工作台→workbench / 驾驶舱→wide），
#    它的**佐证**是 preset 自己的 `maxWidth` 键名恰好是 reading / workbench / wide
#    （外加 citation）。映射错了 A1 会**稳定假红**，所以 A0 会先断言这三个令牌键存在。
CONTENT_TOKEN = {
    "工作台": "workbench",
    "驾驶舱": "wide",
    "阅读型页面": "reading",
}


class SpecUnreadable(Exception):
    """规范读不出期望值 ⇒ **环境问题（exit 2）**，不是产品缺陷。"""


# ── 注释剥离（剥注释必须在**判据函数内部**，否则自检测不到真实形态）──────────────
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//[^\n]*")


def strip_comments(text: str) -> str:
    r"""块注释换成**等量换行**（保行号），行注释用 `(?<![:\w])` 避开 `https://`。"""
    text = BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return LINE_COMMENT_RE.sub("", text)


# ── 规范解析 ────────────────────────────────────────────────────────────────
SEC_41_START = r"^###\s*4\.1\s"
SEC_41_END = r"^###\s*4\.2\s"

SIDEBAR_RE = re.compile(r"左侧\s*\*\*(\d+)px\*\*")
TOPBAR_RE = re.compile(r"顶部\s*\*\*(\d+)px\*\*")
CONTENT_RE = re.compile(
    r"内容区最大宽\s*\*\*(\d+)\*\*（([^）]+)）\s*/\s*"
    r"\*\*(\d+)\*\*（([^）]+)）\s*/\s*\*\*(\d+)\*\*（([^）]+)）"
)
BAN_RE = re.compile(r"不做\s*\*{0,2}(\d+)px\*{0,2}\s*图标条")
APPS_RE = re.compile(r"打通\s*([A-Za-z/\s]+?)\s*三端")


class Expected(NamedTuple):
    sidebar_px: int
    topbar_px: int
    content: dict[str, int]
    ban_px: int
    apps: tuple[str, ...]


def _section(text: str, start_pat: str, end_pat: str) -> str:
    m = re.search(start_pat, text, re.M)
    if m is None:
        raise SpecUnreadable(f"规范里找不到小节标题 `{start_pat}`")
    rest = text[m.end():]
    e = re.search(end_pat, rest, re.M)
    return rest[: e.start()] if e else rest


def read_expected(spec_text: str) -> Expected:
    """**从规范现读**，一个字都不硬编码（改规范 ⇒ 判据跟着改）。"""
    sec = _section(spec_text, SEC_41_START, SEC_41_END)

    m = SIDEBAR_RE.search(sec)
    if m is None:
        raise SpecUnreadable("§4.1 里读不出「左侧 **Npx**」（侧栏宽度）")
    sidebar_px = int(m.group(1))

    m = TOPBAR_RE.search(sec)
    if m is None:
        raise SpecUnreadable("§4.1 里读不出「顶部 **Npx**」（顶栏高度）")
    topbar_px = int(m.group(1))

    m = CONTENT_RE.search(sec)
    if m is None:
        raise SpecUnreadable("§4.1 里读不出「内容区最大宽 **A**（x）/ **B**（y）/ **C**（z）」")
    pairs = [(m.group(1), m.group(2)), (m.group(3), m.group(4)), (m.group(5), m.group(6))]
    content: dict[str, int] = {}
    for px, label in pairs:
        label = label.strip()
        tok = CONTENT_TOKEN.get(label)
        if tok is None:
            raise SpecUnreadable(
                f"§4.1 的内容宽档位「{label}」没有登记映射"
                f"（已登记：{' / '.join(CONTENT_TOKEN)}）⇒ 先修本门禁的映射表"
            )
        content[tok] = int(px)

    m = BAN_RE.search(sec)
    if m is None:
        raise SpecUnreadable("§4.1 里读不出图标条禁令（「不做 **Npx** 图标条」）")
    ban_px = int(m.group(1))

    m = APPS_RE.search(sec)
    if m is None:
        raise SpecUnreadable("§4.1 里读不出「打通 a / b / c 三端」")
    apps = tuple(x.strip() for x in m.group(1).split("/") if x.strip())
    if len(apps) != 3:
        raise SpecUnreadable(f"§4.1 的三端清单解析出 {len(apps)} 个（期望 3）：{apps}")

    return Expected(sidebar_px, topbar_px, content, ban_px, apps)


# ── 源码解析 ────────────────────────────────────────────────────────────────
CSS_VAR_RE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")
STRING_LIT_RE = re.compile(r'"([^"\n]*)"' + r"|'([^'\n]*)'" + r"|`([^`]*)`")


def css_vars(src: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in CSS_VAR_RE.finditer(src)}


def px_int(value: str | None) -> int | None:
    if value is None:
        return None
    m = re.fullmatch(r"(\d+)px", value.strip())
    return int(m.group(1)) if m else None


def string_literals(src: str) -> list[str]:
    out: list[str] = []
    for m in STRING_LIT_RE.finditer(src):
        out.append(next(g for g in m.groups() if g is not None))
    return out


def matched_block(src: str, open_idx: int, open_ch: str = "(", close_ch: str = ")") -> str | None:
    """取 `open_idx` 处那个开括号的**配对**内容（含嵌套）。不配对 ⇒ `None`。

    ⚠️ 只在**括号自身平衡**的文本上可靠：本项目实测抽屉子树里的
    `var(--safe-left)` / `calc(...)` 都是成对的 ⇒ 深度能正确回到 0。
    解析失败一律由调用方转成 **exit 2**（**不是**产品缺陷）。
    """
    if open_idx < 0 or open_idx >= len(src) or src[open_idx] != open_ch:
        return None
    depth = 0
    for j in range(open_idx, len(src)):
        if src[j] == open_ch:
            depth += 1
        elif src[j] == close_ch:
            depth -= 1
            if depth == 0:
                return src[open_idx + 1: j]
    return None


def brace_block(src: str, key: str) -> str | None:
    """取 `key: { … }` 的**配对花括号**内容（preset 里 `width` 段）。"""
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*\{{", src)
    if m is None:
        return None
    return matched_block(src, src.index("{", m.start()), "{", "}")


def forbidden_width_literals(px: int) -> list[str]:
    """规范说「不做 Npx 图标条」⇒ 禁止的两种写法：Tailwind 刻度类与任意值类。"""
    out = [f"w-[{px}px]"]
    if px % 4 == 0:  # Tailwind 间距刻度 = 4px × n
        out.append(f"w-{px // 4}")
    return out


# ── 判据 ────────────────────────────────────────────────────────────────────
def evaluate(
    spec_text: str,
    tokens_src: str,
    preset_src: str,
    shell_src: str,
    exists=None,
) -> tuple[int, list[str], list[str]]:
    """**纯函数**：返回 `(rc, defects, reports)`。

    🚨 **剥注释在判据函数内部**（不是 `main()` 里）—— `AppShell.tsx` 的 JSDoc **明写了**
    「≥ 1024px 左侧 240px 墨色侧栏常驻（可整栏收起，**不做 64px 图标条**）」。
    不剥注释，A4 会**当场假红**（项目用那个字面量来**声明它的缺席** —— 与
    `verify_component_refactor.py` 的 J4 是同一个形状）。

    `exists` 可注入（默认查真实文件系统）—— 真仓库里四个文件都在，
    不注入的话「必需文件守卫」就**永远只会绿**。
    """
    exists = exists or (lambda rel: (ROOT / rel).exists())
    tokens_src = strip_comments(tokens_src)
    preset_src = strip_comments(preset_src)
    shell_src = strip_comments(shell_src)

    defects: list[str] = []
    reports: list[str] = []

    # ── A0 守卫：必需文件 + 期望值 ──
    missing = [rel for rel in REQUIRED_FILES if not exists(rel)]
    if missing:
        return 2, [f"【环境】必需文件取不到：{' / '.join(missing)}"], []
    try:
        exp = read_expected(spec_text)
    except SpecUnreadable as exc:
        return 2, [f"【环境】{exc}"], []

    reports.append(
        f"规范 §4.1 现读：侧栏 {exp.sidebar_px}px · 顶栏 {exp.topbar_px}px · "
        f"内容最大宽 {' / '.join(f'{k}={v}px' for k, v in exp.content.items())} · "
        f"图标条禁令 {exp.ban_px}px · 三端 {' / '.join(exp.apps)}"
    )
    # 「顶栏 56px」**只报不判** —— 它在 CI 里由 `verify_component_refactor.py` 的 J3 判（归属 §6.3）。
    reports.append(
        f"（顶部 {exp.topbar_px}px 本门禁**不判**：`verify_component_refactor.py` J3 已在 CI 里判，避免两处期望值）"
    )

    # ── A1 令牌值 ──
    vars_ = css_vars(tokens_src)
    got_sidebar = px_int(vars_.get("sidebar-w"))
    if got_sidebar is None:
        defects.append(f"`tokens.css` 里没有可解析的 `--sidebar-w`（§4.1 要求 {exp.sidebar_px}px）")
    elif got_sidebar != exp.sidebar_px:
        defects.append(
            f"`--sidebar-w` = **{got_sidebar}px**，而 §4.1 要求 **{exp.sidebar_px}px**"
        )
    for tok, want in exp.content.items():
        got = px_int(vars_.get(f"content-{tok}"))
        if got is None:
            defects.append(f"`tokens.css` 里没有可解析的 `--content-{tok}`（§4.1 要求 {want}px）")
        elif got != want:
            defects.append(
                f"`--content-{tok}` = **{got}px**，而 §4.1 要求 **{want}px**"
            )

    # ── A2 令牌不是死的 ──
    # ⚠️ Tailwind 的 `w-*` 刻度来自 `width` **或** `spacing` ——
    #    本项目**实测**用的是 **`spacing`**（`spacing.sidebar: "var(--sidebar-w)"`）。
    #    第一版只找 `width` 段 ⇒ **真仓库当场红，而产品其实是对的**：
    #    这正是「断言之前先确认期望值/出处的出处」那条纪律（差点把自己的判据错误说成产品缺陷）。
    scales: dict[str, str] = {}
    for key in ("width", "spacing"):
        blk = brace_block(preset_src, key)
        if blk is not None:
            scales[key] = blk
    if not scales:
        defects.append(
            "preset 里既没有 `width: { … }` 也没有 `spacing: { … }` 段"
            "（§4.1 的侧栏宽要靠它接上令牌）"
        )
    else:
        hit = [
            k for k, blk in scales.items()
            if re.search(r"\bsidebar\s*:\s*\"var\(--sidebar-w\)\"", blk)
        ]
        if not hit:
            defects.append(
                f"preset 的 {' / '.join(sorted(scales))} 段里没有 `sidebar: \"var(--sidebar-w)\"` ⇒ "
                "**改令牌不会改渲染**（§4.1 的 240px 失效）"
            )
        else:
            reports.append(f"`w-sidebar` 的令牌映射实测在 preset 的 `{hit[0]}` 段（`width` / `spacing` 都接受）")
    sidebar_lits = [s for s in string_literals(shell_src) if re.search(r"\bw-sidebar\b", s)]
    residents = [s for s in sidebar_lits if re.search(r"\bz-sidebar\b", s)]
    drawers = [s for s in sidebar_lits if re.search(r"\bz-drawer\b", s)]
    # 🚨 锚点必须**唯一且可区分**：`AppShell.tsx` 里**有两个** `<aside>`（桌面常驻 + 移动抽屉），
    #    两者都带 `w-sidebar`。实测第一版只按 `w-sidebar` 定位 ⇒ 命中 **2** 处 ⇒ 守卫当场咬合。
    # ⚠️ 语义要分清：**0 处 = 东西没了（产品缺陷，rc 1）**；**>1 处 = 我没法定位（环境/仪器，exit 2）**。
    #    把 0 处判成 exit 2 会把「侧栏被删了」误报成环境问题（假阴性）。
    if not sidebar_lits:
        defects.append(
            "`AppShell.tsx` 里没有任何字符串含 `w-sidebar` ⇒ "
            "侧栏宽度没有走令牌（§4.1 的 240px 失效）"
        )
        sidebar_cls = ""
        drawers = []
    elif len(residents) == 0:
        defects.append("找不到**桌面常驻侧栏**（含 `z-sidebar` + `w-sidebar` 的字面量）⇒ §4.1 的桌面形态没了")
        sidebar_cls = ""
        drawers = []
    elif len(drawers) == 0:
        defects.append("找不到**抽屉侧栏**（含 `z-drawer` + `w-sidebar` 的字面量）⇒ §4.1 的移动抽屉没了")
        sidebar_cls = ""
    elif len(residents) > 1 or len(drawers) > 1:
        return 2, [
            f"【环境】侧栏锚点不唯一：含 `w-sidebar` 的字面量共 {len(sidebar_lits)} 处，"
            f"其中带 `z-sidebar`（常驻）{len(residents)} 处、带 `z-drawer`（抽屉）{len(drawers)} 处"
            "（期望各 1）⇒ A2/A3 会**静默空转**（先修本门禁的定位方式，别把它当产品缺陷）"
        ], reports
    else:
        sidebar_cls = residents[0]

    # ── A3 抽屉形态 ──
    if sidebar_cls:
        if not re.search(r"(?:^|\s)hidden(?:\s|$)", sidebar_cls):
            defects.append(
                "常驻侧栏的 className 里没有 `hidden` ⇒ <1024px 不会收起（§4.1：< 1024px 收起为抽屉）"
            )
        if not re.search(r"\blg:flex\b", sidebar_cls):
            defects.append(
                "常驻侧栏的 className 里没有 `lg:flex` ⇒ ≥1024px 不会常驻（§4.1：桌面形态常驻侧栏）"
            )
    # 两个侧栏（常驻 + 抽屉）的**宽度类必须恰好都是 `w-sidebar`** ——
    # 否则抽屉会自带一个别的宽度，§4.1 的 240px 就不再是单一真源。
    # ⚠️ 这一条**必须可证伪**：写成「`drawers[0]` 里含 `w-sidebar`」是同义反复
    #    （`drawers` 就是按含 `w-sidebar` 筛出来的）⇒ **一条只会绿的判据**（坑 33 家族）。
    for tag, lit in (("常驻侧栏", sidebar_cls), ("抽屉侧栏", drawers[0] if drawers else "")):
        if not lit:
            continue
        widths = sorted(set(re.findall(r"(?:^|\s)(w-[^\s]+)", lit)))
        if widths != ["w-sidebar"]:
            defects.append(
                f"{tag}的宽度类不是恰好 `w-sidebar`，实测 {widths} ⇒ "
                "§4.1 的 240px 不再是单一真源（两个侧栏会各自漂移）"
            )
    # ── A3b 抽屉子树 ──
    # ⚠️ 判据必须**限定在抽屉子树内**。第一版写的是「`{drawerOpen &&` 之后**任意处**有 `lg:hidden`」，
    #    而顶栏那个「打开导航」汉堡按钮（`lg:hidden`，实测 `AppShell.tsx` l.507）**也在锚点之后**
    #    ⇒ 把抽屉容器（l.454）的守卫删掉它照样绿。**注入臂 I5 实测 rc=0（期望 1）** 才暴露出来。
    #    ⇒ 这是「一条只会绿的判据」的**搜索范围**版本（坑 33 家族）：谓词本身没问题，
    #      但**它在哪段文本里找**决定了它能不能被证伪。
    # ⚠️ 只在 `drawers` 非空时才做 —— 抽屉侧栏整个没了是**产品缺陷**（A2 段已报），
    #    这里再判会变成 exit 2，把真缺陷盖成环境问题（假阴性）。
    if drawers:
        anchor = "{drawerOpen &&"
        n_drawer = shell_src.count(anchor)
        if n_drawer != 1:
            return 2, [
                f"【环境】`{anchor}` 出现 {n_drawer} 次（期望 1）⇒ 抽屉锚点失效，"
                "A3b 会**静默空转**（先修本门禁的锚点，别把它当产品缺陷）"
            ], reports
        i = shell_src.index(anchor)
        amp = shell_src.find("&&", i)
        paren = shell_src.find("(", amp + 2) if amp >= 0 else -1
        block = matched_block(shell_src, paren)
        # ⚠️ 只有**括号不配对**才是环境问题（`block is None`）—— 那是我的解析失败。
        if block is None:
            return 2, [
                "【环境】取不到抽屉子树（`{drawerOpen && ( … )}` 配对括号解析失败）⇒ "
                "A3b 会**静默空转**（先修本门禁的解析，别把它当产品缺陷）"
            ], reports
        # ⚠️ 过冲哨兵：括号**少一个**不会让配对失败，只会让它**向外扩张**到下一层 ——
        #    第一版 Q12d 就是这么**假绿**的（删掉 `)}` 的 `)` 后，扫描器一路吃到函数末尾的 `);`，
        #    于是子树里又混进了主区的汉堡按钮 `lg:hidden` ⇒ A3b 重新变得不可证伪）。
        #    真文件里抽屉块之后是**主区**（顶栏 `z-topbar`）⇒ 子树里出现它 = 配对过冲 ⇒ exit 2。
        if "z-topbar" in shell_src and "z-topbar" in block:
            return 2, [
                "【环境】抽屉子树里出现了**主区**的 `z-topbar` ⇒ 配对括号**过冲**了"
                "（子树被扩张到抽屉块之外）⇒ A3b 会**静默空转**（先修本门禁的解析，别把它当产品缺陷）"
            ], reports
        # ⚠️ 括号配好了、但子树里**没有** `z-drawer` ⇒ 那是**产品缺陷**（rc 1），**不是** exit 2：
        #    抽屉节点被提出条件渲染了（`drawers` 非空 ⇒ 它还在文件里，只是不在 `{drawerOpen &&` 里）。
        #    把它判成 exit 2 会把「抽屉无条件渲染」这个真缺陷盖成环境问题（假阴性）。
        if "z-drawer" not in block:
            defects.append(
                "`{drawerOpen &&` 的子树里没有 `z-drawer` ⇒ 抽屉不再受**条件渲染**控制"
                "（会被无条件渲染；§4.1：< 1024px 才出抽屉）"
            )
        else:
            guard = block.find("lg:hidden")
            first_drawer = block.find("z-drawer")
            if guard < 0:
                defects.append(
                    "抽屉子树里没有 `lg:hidden` ⇒ 桌面端也会渲染"
                    "（§4.1：< 1024px 收起为抽屉，桌面不出现）"
                )
            elif guard > first_drawer:
                defects.append(
                    "抽屉子树里的 `lg:hidden` 出现在第一个 `z-drawer` **之后** ⇒ "
                    "它没落在**祖先**元素上（遮罩 / 抽屉侧栏会在桌面端漏出）"
                )
            else:
                reports.append(
                    f"抽屉子树：`lg:hidden` 在第一个 `z-drawer` 之前（偏移 {guard} < {first_drawer}）"
                    "⇒ 守卫落在祖先元素上"
                )

    # ── A4 图标条禁令 ──
    banned = forbidden_width_literals(exp.ban_px)
    for lit in banned:
        n = sum(1 for s in string_literals(shell_src) if re.search(rf"(?:^|\s){re.escape(lit)}(?:\s|$)", s))
        if n:
            defects.append(
                f"`AppShell.tsx` 出现 `{lit}`（= {exp.ban_px}px）⇒ "
                f"违反 §4.1 的明文禁令「不做 {exp.ban_px}px 图标条」（{n} 处）"
            )
    reports.append(f"图标条禁令检查的字面量：{' / '.join(banned)}（实测命中 0 处）")

    # ── R 组：只报不判 ──
    has_mech = bool(re.search(r"\bapps\s*\?:", shell_src)) and bool(
        re.search(r"\bonAppChange\s*\?:", shell_src)
    )
    reports.append(
        f"R1 应用切换器**机制**：{'在' if has_mech else '**不在**'}（`apps` + `onAppChange` prop）；"
        f"§4.1 点名的三端 = {' / '.join(exp.apps)} —— "
        "**清单在调用方**，本条只报不判（`AppShell` 是共享组件，不该知道谁在用）"
    )
    wide_screens = re.search(r"\bwide\s*:\s*\"(\d+px)\"", preset_src)
    reports.append(
        "R2 ✅ 第 8.2 节的 `wide` 断点**已于 2026-09-23 裁定为 1600**"
        "（裁定前规范自己矛盾：§4.2 写「断点 640 / 1024 / 1280 / 1600」，"
        "第 8.2 节的表写 `wide ≥ 1440px`；现 §8.2 已改为 `≥1600` + `expanded` 上界 `1599`）；"
        "preset 现值 = "
        f"{wide_screens.group(1) if wide_screens else '（读不到）'}"
        "，且全仓 **0 处** `wide:` 工具类（**死配置**）"
        "⇒ **本门禁不判断点**（⚠️ 本行是**裁定记录**，不现读 §8.2 ⇒ 改回去它不会发现）"
    )

    return (1 if defects else 0), defects, reports


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


# ── 自检（**纯合成夹具**，不读真实仓库）─────────────────────────────────────────
SYN_SPEC = """\
# 合成规范

## 04 布局

### 4.1 AppShell（桌面形态）

- 左侧 **240px** 墨色导航（< 1024px 收起为抽屉，**不做 64px 图标条**）
- 顶部 **56px** 栏：面包屑 + 全局搜索（⌘K）+ 通知 + 用户菜单
- 内容区最大宽 **1280**（工作台）/ **1600**（驾驶舱）/ **720**（阅读型页面）
- 侧栏顶部含**应用切换器**，打通 web / lawyer / admin 三端

### 4.2 间距（4pt 网格）

刻度：4 / 8 / 12
"""

SYN_TOKENS = """\
:root {
  --sidebar-w: 240px;
  --topbar-h: 56px;
  --content-reading: 720px;
  --content-workbench: 1280px;
  --content-wide: 1600px;
}
"""

SYN_PRESET = """\
export const preset = {
  theme: {
    extend: {
      /* -------------------------------------------------------------- 宽度 */
      spacing: {
        sidebar: "var(--sidebar-w)",
        topbar: "var(--topbar-h)",
      },
      maxWidth: {
        reading: "var(--content-reading)",
      },
    },
  },
};
"""

SYN_SHELL = """\
/** JSDoc：≥ 1024px 左侧 240px 墨色侧栏常驻（**不做 64px 图标条**） */
export const AppShell = ({ apps, onAppChange }) => (
  <>
    <aside className="fixed inset-y-0 left-0 z-sidebar hidden w-sidebar flex-col lg:flex" />
    {drawerOpen && (
      <div className="lg:hidden">
        <aside className="fixed inset-y-0 left-0 z-drawer flex w-sidebar flex-col" />
      </div>
    )}
  </>
);
"""


def _with(
    spec: str = SYN_SPEC,
    tokens: str = SYN_TOKENS,
    preset: str = SYN_PRESET,
    shell: str = SYN_SHELL,
):
    return evaluate(spec, tokens, preset, shell, exists=lambda rel: True)


def self_test() -> int:
    arms: list[tuple[str, int, list[str]]] = []

    def arm(name: str, result: tuple[int, list[str], list[str]], want_rc: int, must: str = "") -> None:
        rc, defects, _ = result
        ok = rc == want_rc and (must in " ".join(defects) if must else True)
        arms.append((name, 0 if ok else 1, [f"期望 rc={want_rc}，实测 rc={rc}", *defects[:1]]))

    # Q1-Q3 守卫：期望值读不出 ⇒ exit 2
    arm("Q1 规范缺「左侧 **Npx**」⇒ exit 2",
        _with(spec=SYN_SPEC.replace("左侧 **240px** 墨色导航", "左侧墨色导航")), 2, "读不出")
    arm("Q2 规范缺内容宽三档 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("内容区最大宽 **1280**（工作台）/ **1600**（驾驶舱）/ **720**（阅读型页面）", "内容区自适应")),
        2, "读不出")
    arm("Q3 规范缺图标条禁令 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("，**不做 64px 图标条**", "")), 2, "读不出")
    arm("Q3b 规范缺小节标题 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("### 4.1 AppShell（桌面形态）", "### 41 AppShell")), 2, "找不到小节标题")
    arm("Q3c 内容宽档位标签没登记 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("（工作台）", "（控制台）")), 2, "没有登记映射")
    arm("Q3d 三端清单不是 3 个 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("web / lawyer / admin 三端", "web / lawyer 三端")), 2, "期望 3")
    arm("Q3e 必需文件取不到 ⇒ exit 2",
        evaluate(SYN_SPEC, SYN_TOKENS, SYN_PRESET, SYN_SHELL, exists=lambda rel: rel != SHELL_REL),
        2, "必需文件取不到")

    # Q4 控制组
    arm("Q4 全部对齐 ⇒ 绿（控制组）", _with(), 0)

    # Q5-Q6 A1
    arm("Q5 `--sidebar-w: 200px` ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("--sidebar-w: 240px", "--sidebar-w: 200px")), 1, "--sidebar-w")
    arm("Q6 `--content-workbench: 1200px` ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("--content-workbench: 1280px", "--content-workbench: 1200px")),
        1, "content-workbench")
    arm("Q6b `--content-wide` 整个缺失 ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("  --content-wide: 1600px;\n", "")), 1, "content-wide")

    # Q7-Q8 A2
    arm("Q7 preset 不映射 `var(--sidebar-w)` ⇒ 红",
        _with(preset=SYN_PRESET.replace('sidebar: "var(--sidebar-w)"', 'sidebar: "240px"')),
        1, "sidebar")
    arm("Q7b preset 既没有 `spacing` 也没有 `width` 段 ⇒ 红",
        _with(preset="export const preset = {};\n"), 1, "也没有")
    arm("Q7c 映射写在 `width` 段里 ⇒ **也接受**（Tailwind 两处都算刻度）",
        _with(preset=SYN_PRESET.replace("spacing: {", "width: {")), 0)
    arm("Q8 shell 没有 `w-sidebar` ⇒ 红",
        _with(shell=SYN_SHELL.replace("w-sidebar", "w-[240px]")), 1, "w-sidebar")
    arm("Q8b 常驻侧栏整个没了（只剩抽屉）⇒ 红",
        _with(shell=SYN_SHELL.replace(
            '    <aside className="fixed inset-y-0 left-0 z-sidebar hidden w-sidebar flex-col lg:flex" />\n', "")),
        1, "常驻侧栏")
    arm("Q8c 抽屉侧栏整个没了 ⇒ 红",
        _with(shell=SYN_SHELL.replace(
            '        <aside className="fixed inset-y-0 left-0 z-drawer flex w-sidebar flex-col" />\n', "")),
        1, "抽屉侧栏")

    # Q9-Q12 A3
    arm("Q9 常驻侧栏丢 `hidden` ⇒ 红",
        _with(shell=SYN_SHELL.replace("z-sidebar hidden w-sidebar", "z-sidebar w-sidebar")),
        1, "hidden")
    arm("Q10 常驻侧栏丢 `lg:flex` ⇒ 红",
        _with(shell=SYN_SHELL.replace("lg:flex", "lg:block")), 1, "lg:flex")
    arm("Q11 `{drawerOpen &&` 锚点 0 处 ⇒ exit 2（仪器前提失效）",
        _with(shell=SYN_SHELL.replace("{drawerOpen &&", "{open &&")), 2, "抽屉锚点失效")
    arm("Q12 `{drawerOpen &&` 之后没有 `lg:hidden` ⇒ 红",
        _with(shell=SYN_SHELL.replace('className="lg:hidden"', 'className="block"')), 1, "lg:hidden")
    arm("Q12b `lg:hidden` 只在抽屉**之前**出现 ⇒ 仍红",
        _with(shell=SYN_SHELL.replace('className="lg:hidden"', 'className="block"')
              .replace('z-sidebar hidden w-sidebar', 'z-sidebar hidden lg:hidden w-sidebar')),
        1, "lg:hidden")
    # ⚠️ 以下四条守的是 A3b 的**搜索范围**（第一版就栽在这里：范围太宽 ⇒ 判据不可证伪）。
    arm("Q12c `lg:hidden` 挪到抽屉 `aside` 上（在第一个 `z-drawer` **之后**）⇒ 红",
        _with(shell=SYN_SHELL.replace('className="lg:hidden"', 'className="block"')
              .replace("z-drawer flex w-sidebar", "z-drawer flex lg:hidden w-sidebar")),
        1, "祖先")
    # ⚠️ 「括号坏掉」的夹具必须**把闭括号全去掉**：删一个 `)` 只会让配对**向外扩张**到下一层
    #    （第一版 Q12d 就是这么假绿的 —— 扫描器一路吃到函数末尾的 `);` ⇒ rc 0）。
    arm("Q12d 抽屉子树的闭括号**全去掉** ⇒ 真·不配对 ⇒ exit 2",
        _with(shell=SYN_SHELL.replace("    )}\n  </>\n);", "    }\n  </>\n;")),
        2, "配对括号解析失败")
    arm("Q12g 只删一个 `)` ⇒ 配对**过冲**（靠 `z-topbar` 哨兵拦下）⇒ exit 2",
        _with(shell=SYN_SHELL.replace("    )}\n  </>\n);", "    }\n  </>\n);")
              .replace("  </>", '    <header className="z-topbar" />\n  </>')),
        2, "过冲")
    arm("Q12e 抽屉子树整个没了 ⇒ rc 1（**不是** exit 2，否则把真缺陷盖成环境问题）",
        _with(shell=SYN_SHELL.replace(
            '      <div className="lg:hidden">\n'
            '        <aside className="fixed inset-y-0 left-0 z-drawer flex w-sidebar flex-col" />\n'
            '      </div>\n', "")),
        1, "抽屉侧栏")
    arm("Q12f 抽屉被提到条件渲染**之外** ⇒ rc 1（子树里没有 `z-drawer`）",
        _with(shell=SYN_SHELL.replace(
            '    {drawerOpen && (\n'
            '      <div className="lg:hidden">\n'
            '        <aside className="fixed inset-y-0 left-0 z-drawer flex w-sidebar flex-col" />\n'
            '      </div>\n'
            '    )}\n',
            '    {drawerOpen && (null)}\n'
            '    <aside className="fixed inset-y-0 left-0 z-drawer flex w-sidebar flex-col" />\n')),
        1, "条件渲染")

    # Q13-Q15 A4（含「期望值从规范现读」的一对臂）
    # ⚠️ 注入点放在**非侧栏**元素上：放在侧栏上会**同时**触发「宽度类必须恰好 w-sidebar」，
    #    那样这条臂就**不再只测 A4** 了（一条臂测两件事 = 红了也分不清是谁）。
    arm("Q13 非侧栏元素出现 `w-16`（=64px）⇒ A4 红",
        _with(shell=SYN_SHELL.replace("  </>", '    <div className="w-16" />\n  </>')), 1, "w-16")
    arm("Q13b 非侧栏元素出现 `w-[64px]` ⇒ A4 红",
        _with(shell=SYN_SHELL.replace("  </>", '    <div className="w-[64px]" />\n  </>')), 1, "w-[64px]")
    arm("Q15a `w-14`（=56px）在禁令为 64px 时 **不得**红",
        _with(shell=SYN_SHELL.replace("  </>", '    <div className="w-14" />\n  </>')), 0)
    arm("Q15b 规范禁令改成 56px ⇒ 同一个 `w-14` **必须**红（证明从规范现读）",
        _with(spec=SYN_SPEC.replace("不做 64px 图标条", "不做 56px 图标条"),
              shell=SYN_SHELL.replace("  </>", '    <div className="w-14" />\n  </>')), 1, "w-14")

    # Q14 剥注释的对照臂（**注释里的违规不得红**）
    arm("Q14 注释里写 `w-16` 与「不做 64px 图标条」⇒ 必须绿",
        _with(shell=SYN_SHELL.replace("export const AppShell",
                                      "// 注意：本组件不得出现 w-16\n/* 也不得 w-[64px] */\nexport const AppShell")),
        0)

    # Q17 宽度类必须是「恰好 w-sidebar」（两个侧栏共用同一令牌）
    arm("Q17 常驻侧栏多出一个 `w-[300px]` ⇒ 红",
        _with(shell=SYN_SHELL.replace("hidden w-sidebar flex-col", "hidden w-sidebar w-[300px] flex-col")),
        1, "不是恰好")
    arm("Q17b 抽屉侧栏多出一个 `w-[280px]` ⇒ 红",
        _with(shell=SYN_SHELL.replace("z-drawer flex w-sidebar flex-col",
                                      "z-drawer flex w-sidebar w-[280px] flex-col")),
        1, "不是恰好")

    # Q16 R1 只报不判
    rc, _, reports = _with(shell=SYN_SHELL.replace("({ apps, onAppChange })", "({})"))
    arms.append((
        "Q16 R1「三端清单在调用方」只报不判（没有 apps 机制时 rc 仍为 0）",
        0 if (rc == 0 and any("R1" in r for r in reports)) else 1,
        [f"rc={rc}"],
    ))

    failed = [a for a in arms if a[1]]
    for name, bad, detail in arms:
        print(f"  [{'✗' if bad else '✓'}] {name}")
        if bad:
            for d in detail:
                print(f"        {d}")
    print("=" * 82)
    print(f"自检：{len(arms) - len(failed)}/{len(arms)} 通过")
    return 1 if failed else 0


# ── 真仓库内存注入（**不碰产品文件**）───────────────────────────────────────────
def replace_once(src: str, old: str, new: str) -> str:
    n = src.count(old)
    if n != 1:
        raise AssertionError(f"注入锚点命中 {n} 次（期望 1）：{old!r}")
    return src.replace(old, new)


def inject_arms() -> int:
    """真仓库文本**在内存里**注入 —— 每条都**断言锚点命中 1 次**。

    ⚠️ 锚点不唯一会让整条臂**静默空转**（看着「没红」，其实是没注入进去）。
    """
    spec = read_text(SPEC_REL)
    tokens = read_text(TOKENS_REL)
    preset = read_text(PRESET_REL)
    shell = read_text(SHELL_REL)

    def move_drawer_guard(src: str) -> str:
        """把 `lg:hidden` 从抽屉**容器**挪到内层 `aside` 上（仍在子树内，但落在 `z-drawer` **之后**）。"""
        s = replace_once(src, '<div className="lg:hidden">', '<div className="block">')
        return replace_once(
            s,
            "z-drawer flex w-sidebar flex-col bg-brand-950 shadow-s3",
            "z-drawer flex w-sidebar flex-col bg-brand-950 shadow-s3 lg:hidden",
        )

    cases: list[tuple[str, str, str, str, str, int]] = [
        ("I0 控制组：一个字节都不改 ⇒ 必须绿", spec, tokens, preset, shell, 0),
        ("I1 `--sidebar-w: 240px` → 200px ⇒ A1 红", spec,
         replace_once(tokens, "--sidebar-w: 240px;", "--sidebar-w: 200px;"), preset, shell, 1),
        ("I2 `--content-wide: 1600px` → 1440px ⇒ A1 红", spec,
         replace_once(tokens, "--content-wide: 1600px;", "--content-wide: 1440px;"), preset, shell, 1),
        ("I3 preset `width.sidebar` 脱开令牌 ⇒ A2 红", spec, tokens,
         replace_once(preset, 'sidebar: "var(--sidebar-w)"', 'sidebar: "240px"'), shell, 1),
        ("I4 侧栏丢 `lg:flex` ⇒ A3a 红", spec, tokens, preset,
         replace_once(shell, "hidden w-sidebar flex-col bg-brand-950 lg:flex",
                      "hidden w-sidebar flex-col bg-brand-950"), 1),
        ("I5 抽屉**容器**丢 `lg:hidden`（汉堡按钮那处 `lg:hidden` 顶不上）⇒ A3b 红", spec, tokens, preset,
         replace_once(shell, '        <div className="lg:hidden">', '        <div className="block">'), 1),
        ("I6 侧栏加 `w-16`（64px 图标条）⇒ A4 红", spec, tokens, preset,
         replace_once(shell, "z-sidebar hidden w-sidebar", "z-sidebar hidden w-sidebar w-16"), 1),
        ("I7 `lg:hidden` 从容器挪到内层 `aside`（子树内但在 `z-drawer` 之后）⇒ A3b 红",
         spec, tokens, preset, move_drawer_guard(shell), 1),
    ]

    bad = 0
    for name, s, t, p, sh, want in cases:
        try:
            rc, defects, _ = evaluate(s, t, p, sh)
        except AssertionError as exc:
            print(f"  [✗] {name}\n        {exc}")
            bad += 1
            continue
        ok = rc == want
        print(f"  [{'✓' if ok else '✗'}] {name}")
        if not ok:
            bad += 1
            print(f"        期望 rc={want}，实测 rc={rc}；缺陷：{defects[:2]}")
    print("=" * 82)
    print(f"注入反证：{len(cases) - bad}/{len(cases)} 符合预期")
    return 1 if bad else 0


WHY: dict[str, str] = {
    "4.1": (
        "§4.1「AppShell（桌面形态）」—— 出处 `deliverables/ui-design/design-spec.md`"
        " 的 `### 4.1`（左侧 240px / 顶部 56px / 内容最大宽 1280·1600·720 / 三端切换器）。\n"
        "  归属：**本门禁**判令牌值与源码形态；**渲染宽**由 `verify_breakpoints.py` B2b"
        "（CDP，**只在主干跑** · `browser-all`）判；\n"
        "  「顶部 56px」由 `verify_component_refactor.py` J3 判（归属 §6.3）。\n"
        "  ✅ 第 8.2 节的 `wide` 断点**不在本门禁**（2026-09-23 已裁定 = 1600，详见上面 R2）。"
    ),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="design-spec §4.1 静态地板")
    ap.add_argument("--self-test", action="store_true", help="跑合成夹具自检（纯函数，不读真实仓库）")
    ap.add_argument("--inject", action="store_true", help="真仓库内存注入反证（不碰产品文件）")
    ap.add_argument("--why", metavar="SEC", help="打印某节的出处与归属")
    args = ap.parse_args(argv)

    if args.why:
        key = args.why.strip().lstrip("§")
        print(WHY.get(key, f"（本门禁只登记了 {' / '.join(WHY)}；没有 {key}）"))
        return 0
    if args.self_test:
        return self_test()
    if args.inject:
        return inject_arms()

    try:
        spec = read_text(SPEC_REL)
        tokens = read_text(TOKENS_REL)
        preset = read_text(PRESET_REL)
        shell = read_text(SHELL_REL)
    except OSError as exc:
        print(f"【环境】取不到必需文件：{exc}")
        return 2

    rc, defects, reports = evaluate(spec, tokens, preset, shell)
    for ln in reports:
        print(f"  · {ln}")
    if rc == 2:
        print()
        for d in defects:
            print(f"🚨 {d}")
        print("\n⇒ exit 2：**环境/仪器问题**，不是产品缺陷")
        return 2
    if defects:
        print(f"\n❌ §4.1 静态地板：**{len(defects)} 条缺陷**")
        for d in defects:
            print(f"  ✗ {d}")
        return 1
    print("\n✅ §4.1 静态地板：**0 欠账**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
