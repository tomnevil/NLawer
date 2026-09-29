#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§6.3「现有组件改造要点」门禁 —— 六条改造要点的**已实现子集**。

## 为什么要有这一条

`grep -oE '§[0-9]+(\\.[0-9]+)?' evidence/verify_*.py` 与 `design-spec.md` 小节求交后，
**§6.3 是最后一个「零引用」的可判小节**（§2.5 已由 `verify_design_tokens.py` 覆盖、
§8.6 是**功能缺口**不是覆盖率缺口，见 `design-audit.md`）。
§6.3 的六条 bullet 各带**明确数值**（240px / 56px / 8px / 28px / 5 变体 3 尺寸 / …），
此前**没有一条判据**。

## 🚨 与邻门禁的**分工表**（不划清就会造重复判据）

| §6.3 的要求 | 归谁 |
|---|---|
| Sidebar 宽度 240px | `verify_breakpoints.py` **B2b**（它判「左列宽 ≈ 令牌 `--sidebar-w`」） |
| Card 圆角 8px | `verify_radius_scale.py`（§4.3 门禁，`rounded-r3` 解析 = 8px） |
| Button「5 变体 3 尺寸」 | `verify_buttons.py` **B2/B3** |
| KpiCard `color` 语义色调 | `verify_grade_badges.py`（同族的语义色判据） |
| **其余（下表 J1–J10）** | **本门禁** |

⇒ 本门禁**只判残差**。**判「已经有人判过的东西」= 改一处红两条，排查时误导。**

## 判据（期望值一律从 §6.3 现读，不硬编码）

| 判据 | 内容 | 出处 |
|---|---|---|
| **J0** | **门禁自身守卫**：§6.3 必须解析出 **6** 条 bullet | 规范结构 |
| **J1** | Sidebar 底色 == 规范反引号里的色名（`brand-950`） | §6.3 Sidebar |
| **J2** | Sidebar **激活项左侧 3px 金色指示条**（宽度 == 规范值、颜色 == 规范色） | §6.3 Sidebar |
| **J3** | 顶栏高度令牌 == §6.3 的 `56px`，且 `AppShell` 用它 | §6.3 Header |
| **J4** | `AppShell` **不含 `dark:` 变体**（「去掉 `dark:` 硬编码」） | §6.3 Header |
| **J5** | Card 的 `hover` **不得引入位移/缩放**（「移除 hover 位移」） | §6.3 Card |
| **J6** | Card `default` 变体必须含 1px 边框 + `--s1` 阴影 | §6.3 Card |
| **J7** | KpiCard 数值字号 == §6.3 的 `28px`，且带**等宽**类 | §6.3 KpiCard |
| **J8** | KpiCard 单位以**小字**呈现 | §6.3 KpiCard |
| **J9** | Button **无渐变**（「去渐变」） | §6.3 Button |
| **J10** | LoginShell 含**网格纹理** + **角色分段切换**（`SegmentedControl`） | §6.3 LoginShell |
| **R1–R3** | **只报**：KpiCard「迷你趋势线」缺失 · Button「loading 态」缺失 · LoginShell 手机号登录**依赖后端短信端点** | §6.3（**功能缺口 / 依赖**，不是判据） |

⚠️ **R 组是「功能缺口」，不是「覆盖率缺口」** —— 门禁判不了「该不该做」，
只把它**摆出来**并指向出处。**别把只报项当成已解决。**

## 退出码

- `0` 通过（允许出现**已登记**的欠账）
- `1` 发现**新的**违反，或棘轮豁免已过期
- `2` 环境问题（规范 / 组件文件找不到，或自检失败 ⇒ **工具坏了，不是产品坏了**）

用法：

    python evidence/verify_component_refactor.py
    python evidence/verify_component_refactor.py --self-test
    python evidence/verify_component_refactor.py --why
    python evidence/verify_component_refactor.py --dump
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"
SPEC = ROOT / "deliverables" / "ui-design" / "design-spec.md"

# ⚠️ **仓库相对**键，与 `iter_sources()` 同约定（模块级只定义一次）。
SHELL_REL = "frontend/packages/ui/src/components/AppShell.tsx"
CARD_REL = "frontend/packages/ui/src/components/Card.tsx"
KPI_REL = "frontend/packages/ui/src/components/KpiCard.tsx"
BUTTON_REL = "frontend/packages/ui/src/components/Button.tsx"
LOGIN_REL = "frontend/packages/ui/src/components/LoginShell.tsx"
TOKENS_REL = "frontend/packages/ui/src/tokens.css"

# 不在 `apps/` 或 `packages/ui/src/` 任何一棵树里、但判据必需的文件。
EXTRA_RELS: tuple[str, ...] = ()

REQUIRED_FILES: tuple[str, ...] = (
    SHELL_REL, CARD_REL, KPI_REL, BUTTON_REL, LOGIN_REL, TOKENS_REL,
)

SKIP_DIRS = {"node_modules", ".next", ".next_old_v14_keep", "_prev_build",
             "dist", "build", ".turbo", "__pycache__"}

# ---------------------------------------------------------------------------
# 棘轮：已登记、不阻断 CI，但**不许涨**、**修好必须删**。
# 当前为空 —— J1–J10 实测**全部通过**（六条改造要点里「已实现」的那部分都真的做了）。
# ---------------------------------------------------------------------------
KNOWN_GAPS: dict[str, str] = {}

# 「位移 / 缩放」类工具类的判据：Tailwind 的 translate / scale / rotate。
MOVE_RE = re.compile(r"(?:^|[\s:])(?:-)?(?:translate|scale|rotate)(?:-[xy])?(?:-|$|\[)")
GRADIENT_RE = re.compile(r"(?:^|[\s:])(?:bg-gradient|from-|via-|to-(?!ken))")
DARK_VARIANT_RE = re.compile(r"(?:^|[\s\"'`])dark:")

# ---------------------------------------------------------------------------
# 注释剥离：J4 / J5 / J9 判的是「源码里**有没有**某个类名」，
# 而项目**恰恰会用这个字面量来声明它的缺席** ——
#   AppShell.tsx:373  `* 深色模式完全由 tokens.css 的 CSS 变量驱动，本组件不含任何 dark: 变体。`
# 直接扫全文 ⇒ 把「证明自己干净」的那句话判成违规 = **稳定假红**。
# ⇒ 判据必须认识项目自己的写法：先在**剥掉注释**的源码上判。
# ---------------------------------------------------------------------------
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# `//` 行注释：排除 `https://`（前面是 `:`）与标识符内部的 `//`
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//")


def strip_comments(src: str) -> str:
    """剥掉 TS/TSX 注释；**保留行号**（块注释替换成等量换行），便于报 `file:line`。"""
    src = BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), src)
    out: list[str] = []
    for ln in src.splitlines():
        m = LINE_COMMENT_RE.search(ln)
        out.append(ln[: m.start()] if m else ln)
    return "\n".join(out)


# ===========================================================================
# 解析：规范 §6.3
# ===========================================================================
def load_section(text: str, heading: str, stop: str) -> str | None:
    m = re.search(r"^###\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    e = re.search(r"^##\s+" + re.escape(stop) + r"\s*$", rest, re.M)
    return rest[: e.start()] if e else rest


BULLET_RE = re.compile(r"^-\s+\*\*(?P<name>[^*]+)\*\*：(?P<body>.+)$", re.M)
BACKTICK_RE = re.compile(r"`([^`]+)`")
# 「256→240px」取箭头后的值；「统一 56px」取唯一值
ARROW_PX_RE = re.compile(r"→\s*([0-9]+)px")
ANY_PX_RE = re.compile(r"([0-9]+)px")


def parse_bullets(section: str) -> dict[str, str]:
    """§6.3 的六条 bullet → {组件名: 正文}。"""
    return {m.group("name").strip(): m.group("body").strip()
            for m in BULLET_RE.finditer(section)}


def spec_px(body: str) -> float | None:
    """从 bullet 正文取「目标 px」：优先箭头后的值，否则取唯一出现的值。"""
    m = ARROW_PX_RE.search(body)
    if m:
        return float(m.group(1))
    vals = {float(v) for v in ANY_PX_RE.findall(body)}
    return vals.pop() if len(vals) == 1 else None


def spec_backticks(body: str) -> list[str]:
    return BACKTICK_RE.findall(body)


# ===========================================================================
# 解析：源码
# ===========================================================================
def parse_tokens(text: str) -> dict[str, str]:
    return {k: v.strip() for k, v in
            re.findall(r"^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);", text, re.M)}


def block_after(text: str, opener: str) -> str | None:
    i = text.find(opener)
    if i < 0:
        return None
    j = text.find("{", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j + 1: k]
    return None


def iter_sources() -> list[tuple[str, str]]:
    """产品页 + 组件库源码 + 树外必需文件。

    🚨 **`os.walk` 就地剪枝**（`rglob` 会走完四份 `node_modules`）；末尾 `sorted` 保确定性。
    """
    out: list[tuple[str, str]] = []
    roots = [FRONTEND / "apps", FRONTEND / "packages" / "ui" / "src"]
    for r in roots:
        if not r.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not fn.endswith((".ts", ".tsx", ".css")):
                    continue
                p = pathlib.Path(dirpath) / fn
                rel = p.relative_to(ROOT).as_posix()
                if "components-preview" in rel:
                    continue
                try:
                    out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    continue
    for rel in EXTRA_RELS:
        p = ROOT / rel
        if p.exists():
            try:
                out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    out.sort(key=lambda kv: kv[0])
    return out


# ===========================================================================
# 判据
# ===========================================================================
class Report:
    def __init__(self) -> None:
        self.fail: list[tuple[str, str]] = []
        self.ok: list[str] = []
        self.notes: list[str] = []

    def add_fail(self, key: str, msg: str) -> None:
        self.fail.append((key, msg))

    def add_ok(self, msg: str) -> None:
        self.ok.append(msg)

    def add_note(self, msg: str) -> None:
        self.notes.append(msg)


def split_ratchet(fail, known):
    known_hits = [(k, m) for k, m in fail if k in known]
    new_hits = [(k, m) for k, m in fail if k not in known]
    expired = [k for k in known if not any(k == fk for fk, _ in fail)]
    return known_hits, new_hits, expired


def judge_j0(bullets: dict[str, str], rep: Report) -> None:
    if len(bullets) != 6:
        rep.add_fail("J0:bullet 数",
                     f"§6.3 应解析出 6 条 bullet，实测 {len(bullets)} 条：{sorted(bullets)} "
                     "⇒ 规范可能改过，请复核本门禁")
    for name in ("Sidebar", "Header", "Card", "KpiCard", "Button", "LoginShell"):
        if name not in bullets:
            rep.add_fail("J0:缺组件", f"§6.3 里找不到 `{name}` 那条 bullet")


def judge_j1(bullets, src, rep: Report) -> None:
    """Sidebar 底色 == 规范反引号里的色名。"""
    body = bullets.get("Sidebar", "")
    toks = [t for t in spec_backticks(body) if re.fullmatch(r"[a-z]+-[0-9]{2,3}", t)]
    if not toks:
        rep.add_fail("J1:期望值", "§6.3 的 Sidebar 条目里解析不出「底色令牌名」（形如 `brand-950`）")
        return
    want = toks[0]
    if f"bg-{want}" in src:
        rep.add_ok(f"J1 Sidebar 底色 == `{want}`（期望值取自 §6.3 反引号）")
    else:
        rep.add_fail("J1:底色", f"§6.3 要求 Sidebar 底色 `{want}`，但 `AppShell.tsx` 里找不到 `bg-{want}`")


def judge_j2(bullets, src, rep: Report) -> None:
    """激活项左侧 3px 金色指示条。"""
    body = bullets.get("Sidebar", "")
    m = re.search(r"左侧\s*([0-9]+)px\s*(金色|金)\s*指示条", body)
    if not m:
        rep.add_fail("J2:期望值", "§6.3 的 Sidebar 条目里解析不出「N px 金色指示条」")
        return
    want_w = m.group(1)
    has_w = re.search(r"w-\[" + want_w + r"px\]", src) is not None
    has_gold = "bg-gold-500" in src
    if has_w and has_gold:
        rep.add_ok(f"J2 Sidebar 激活项指示条：`w-[{want_w}px]` + `bg-gold-500`（宽度取自 §6.3）")
    else:
        miss = []
        if not has_w:
            miss.append(f"缺 `w-[{want_w}px]`")
        if not has_gold:
            miss.append("缺 `bg-gold-500`")
        rep.add_fail("J2:指示条",
                     f"§6.3 要求激活项左侧 {want_w}px 金色指示条，实测 {'、'.join(miss)}")


def judge_j3(bullets, src, tokens, rep: Report) -> None:
    """顶栏高度令牌 == §6.3 的 56px。"""
    body = bullets.get("Header", "")
    want = spec_px(body)
    if want is None:
        rep.add_fail("J3:期望值", "§6.3 的 Header 条目里解析不出「统一 N px」")
        return
    raw = tokens.get("--topbar-h")
    if raw is None:
        rep.add_fail("J3:缺令牌", "`tokens.css` 里没有 `--topbar-h`")
        return
    m = re.search(r"([0-9.]+)px", raw)
    got = float(m.group(1)) if m else None
    if got is None:
        rep.add_fail("J3:解析失败", f"`--topbar-h: {raw}` 解析不出 px ⇒ 判不了（不是合规）")
    elif abs(got - want) > 0.01:
        rep.add_fail("J3:高度不符", f"§6.3 要求顶栏 {want:g}px，`--topbar-h` = {got:g}px")
    elif "topbar" not in src and "--topbar-h" not in src:
        rep.add_fail("J3:未引用", f"`--topbar-h` = {got:g}px 合规范，但 `AppShell.tsx` 没引用它")
    else:
        rep.add_ok(f"J3 顶栏高度 == §6.3 的 {want:g}px（`--topbar-h`，且 AppShell 引用）")


def judge_j4(bullets, src, rep: Report) -> None:
    """AppShell 不含 `dark:` 变体。

    ⚠️ 必须在**剥注释后**的源码上判：项目用 `dark:` 这个字面量来声明它的缺席
    （`AppShell.tsx:373` 的 JSDoc），扫全文会把「自证干净」判成违规。
    """
    src = strip_comments(src)
    hits = DARK_VARIANT_RE.findall(src)
    if hits:
        lines = [i + 1 for i, ln in enumerate(src.splitlines()) if DARK_VARIANT_RE.search(ln)]
        rep.add_fail("J4:dark 变体",
                     f"§6.3 要求「去掉 `dark:` 硬编码」，但 `AppShell.tsx` 里有 {len(hits)} 处"
                     f"（行 {lines[:5]}）")
    else:
        rep.add_ok("J4 `AppShell.tsx` 无 `dark:` 变体（深色完全由 CSS 变量驱动）")


def judge_j5(bullets, src, rep: Report) -> None:
    """Card hover 不得引入位移/缩放。"""
    # 只看 hover: 前缀后面的类；同样先剥注释（`Card.tsx` 注释里就写了「不再有位移」）
    src = strip_comments(src)
    bad = []
    for i, ln in enumerate(src.splitlines()):
        for m in re.finditer(r"hover:([^\s\"'`]+)", ln):
            if MOVE_RE.search(m.group(1)):
                bad.append((i + 1, m.group(0)))
    if bad:
        rep.add_fail("J5:hover 位移",
                     f"§6.3 要求 Card「移除 hover 位移」，实测 {len(bad)} 处：{bad[:3]}")
    else:
        rep.add_ok("J5 Card 的 hover 无位移/缩放（只有边框与阴影变化）")


def judge_j6(bullets, src, rep: Report) -> None:
    """Card default 变体必须含 1px 边框 + --s1。"""
    body = block_after(src, "const variantStyles")
    if body is None:
        rep.add_fail("J6:结构", "`Card.tsx` 里找不到 `variantStyles` 对象 ⇒ 结构变了")
        return
    m = re.search(r'default:\s*"([^"]+)"', body)
    if not m:
        rep.add_fail("J6:结构", "`variantStyles` 里找不到 `default` 变体")
        return
    cls = m.group(1)
    has_border = "border" in cls
    has_s1 = "shadow-s1" in cls
    if has_border and has_s1:
        rep.add_ok(f"J6 Card `default` = 1px 边框 + `--s1`（`{cls}`）")
    else:
        miss = []
        if not has_border:
            miss.append("缺 `border`")
        if not has_s1:
            miss.append("缺 `shadow-s1`")
        rep.add_fail("J6:Card default", f"§6.3 要求「1px 边框 + `--s1`」，实测 {'、'.join(miss)}（`{cls}`）")


def judge_j7(bullets, src, rep: Report) -> None:
    """KpiCard 数值字号 == 28px 且等宽。"""
    body = bullets.get("KpiCard", "")
    want = spec_px(body)
    if want is None:
        rep.add_fail("J7:期望值", "§6.3 的 KpiCard 条目里解析不出「数值 N px」")
        return
    has_size = f"text-[{want:g}px]" in src
    # 等宽：项目用 `num` 工具类（`font-mono` + `tabular-nums`）
    has_mono = ("num" in src and re.search(r'className="num', src) is not None) \
        or "tabular-nums" in src or "font-mono" in src
    if has_size and has_mono:
        rep.add_ok(f"J7 KpiCard 数值 == §6.3 的 {want:g}px 且带等宽类")
    else:
        miss = []
        if not has_size:
            miss.append(f"缺 `text-[{want:g}px]`")
        if not has_mono:
            miss.append("缺等宽类（`num` / `tabular-nums`）")
        rep.add_fail("J7:数值", f"§6.3 要求「数值 {want:g}px 等宽」，实测 {'、'.join(miss)}")


def judge_j8(bullets, src, rep: Report) -> None:
    """KpiCard 单位以小字呈现。"""
    if re.search(r"\{unit\s*&&\s*<span[^>]*text-(?:caption|label|body-sm)", src):
        rep.add_ok("J8 KpiCard 单位以**小字**呈现（`unit` → caption/label/body-sm 档）")
    else:
        rep.add_fail("J8:单位小字",
                     "§6.3 要求「数值 28px 等宽 + **单位小字**」，"
                     "但 `KpiCard.tsx` 里找不到 `{unit && <span className=\"…text-<小档>…\">}` 结构")


def judge_j9(bullets, src, rep: Report) -> None:
    """Button 无渐变。"""
    src = strip_comments(src)  # 剥注释：注释里提一句 `bg-gradient-to-r` 不该判红
    bad = [i + 1 for i, ln in enumerate(src.splitlines()) if GRADIENT_RE.search(ln)]
    if bad:
        rep.add_fail("J9:渐变", f"§6.3 要求 Button「去渐变」，实测 {len(bad)} 处（行 {bad[:5]}）")
    else:
        rep.add_ok("J9 Button 无渐变类（`bg-gradient-*` / `from-*` / `via-*`）")


def judge_j10(bullets, src, rep: Report) -> None:
    """LoginShell 网格纹理 + 角色分段切换。"""
    body = bullets.get("LoginShell", "")
    parts = []
    # 剥注释：这是「**必须有**」的判据，注释里提到 `linear-gradient` 会造成**假绿**
    src = strip_comments(src)
    has_grid = bool(re.search(r"repeating-linear-gradient|linear-gradient", src))
    parts.append(("网格纹理", has_grid))
    has_seg = "SegmentedControl" in src
    parts.append(("角色分段切换", has_seg))
    if "网格" in body and not has_grid:
        rep.add_fail("J10:网格纹理", "§6.3 要求 LoginShell「墨蓝径向渐变 + **网格纹理**」，实测无")
    if "分段切换" in body and not has_seg:
        rep.add_fail("J10:分段切换", "§6.3 要求 LoginShell「新增**角色分段切换**」，实测无 `SegmentedControl`")
    if all(ok for _, ok in parts):
        rep.add_ok("J10 LoginShell：网格纹理 + 角色分段切换（`SegmentedControl`）均在")


def report_r_group(spec_body, srcs, rep: Report) -> None:
    """只报：功能缺口 / 依赖（**不是判据**）。"""
    kpi_body = spec_body.get("KpiCard", "")
    if "趋势线" in kpi_body and not re.search(r"sparkline|Sparkline|trend|Trend", srcs[KPI_REL]):
        rep.add_note(
            "R1 §6.3 KpiCard「新增环比与**迷你趋势线**」：**环比已做**（`change` → ↑/↓ N%），"
            "但 `KpiCard.tsx` 里**没有趋势线**（无 sparkline / trend 相关实现）"
            "⇒ **功能缺口**，不是覆盖率缺口。**门禁判不了「该不该做」**，只登记。"
        )
    btn_body = spec_body.get("Button", "")
    if "loading" in btn_body and "loading" not in srcs[BUTTON_REL]:
        rep.add_note(
            "R2 §6.3 Button「补 **loading 态**」：`Button.tsx` 里**没有 `loading` 属性/状态**"
            "（`variant` 有 7 个、尺寸 3 个，都已有判据）⇒ **功能缺口**，登记。"
        )
    if "手机号" in spec_body.get("LoginShell", ""):
        rep.add_note(
            "R3 §6.3 LoginShell「移动端改为**手机号 + 验证码**为主路径」：组件已实现"
            "（`modes` / `phone` / `SegmentedControl`），但 `LoginShell.tsx:37` 注明"
            "「`phone` **需要后端提供短信下发与验证码校验端点**」⇒ **依赖后端**，"
            "不是前端待办（与 `backend-cooperation-2026-09-21.md` 口径一致）。"
        )


# ===========================================================================
# 自检（纯函数：不读真实仓库、不启浏览器 ⇒ 可进 SELFTESTABLE）
# ===========================================================================
SYN_SPEC = """## 06 组件规范

### 6.3 现有组件改造要点

- **Sidebar**：宽度 256→240px，底色 `brand-950`，Logo 区去渐变改描边金线，新增分组标题 / 折叠态 / 底部用户区，激活项左侧 3px 金色指示条
- **Header**：统一 56px，补齐面包屑 / 搜索 / 通知 / 用户菜单，去掉 `dark:` 硬编码
- **Card**：圆角 12→8px，移除 hover 位移，默认 1px 边框 + `--s1`
- **KpiCard**：数值 28px 等宽 + 单位小字，新增环比与迷你趋势线，`color` 改为语义 token
- **Button**：5 变体 3 尺寸，补 loading 态，去渐变
- **LoginShell**：左栏改墨蓝径向渐变 + 网格纹理，演示账号改 2×2 紧凑列表，新增角色分段切换；**移动端改为手机号 + 验证码为主路径**

---

## 07 页面概念图
"""

SYN_SHELL = """
const nav = (
  <aside className="fixed inset-y-0 left-0 w-sidebar flex-col bg-brand-950">
    <span className="border border-gold-500/60"><Svg className="text-gold-500" /></span>
    <div>{group.title}</div>
    <span className="absolute left-0 w-[3px] rounded-full bg-gold-500" />
    <div style={{ height: "var(--topbar-h)" }}>面包屑 搜索 通知 用户菜单</div>
  </aside>
);
const [collapsed, setCollapsed] = useState(false);
"""
SYN_CARD = """
const variantStyles: Record<string, string> = {
  default: "bg-surface border border-line shadow-s1",
  glass: "bg-brand-950/90 border border-sidebar-border",
};
const hoverCls = "hover:border-brand-400/60 hover:shadow-s2";
"""
SYN_KPI = """
const x = (
  <div>
    <span className="num text-[28px] font-semibold">{value}</span>
    {unit && <span className="text-body-sm text-ink-500">{unit}</span>}
    {change !== undefined && <span>↑ {change}%</span>}
  </div>
);
"""
SYN_BUTTON = """
const variantStyles: Record<string, string> = {
  primary: "bg-solid-brand text-white",
  ghost: "bg-transparent",
};
"""
SYN_LOGIN = """
const grid = "repeating-linear-gradient(115deg, #fff 0 1px, transparent 1px 22px)";
<SegmentedControl value={mode} />
{isPhoneMode ? "手机号登录" : "登录"}
"""


def _syn_srcs(**over: str) -> dict[str, str]:
    base = {
        SHELL_REL: SYN_SHELL, CARD_REL: SYN_CARD, KPI_REL: SYN_KPI,
        BUTTON_REL: SYN_BUTTON, LOGIN_REL: SYN_LOGIN,
        TOKENS_REL: "--topbar-h: 56px;\n--sidebar-w: 240px;\n",
    }
    base.update(over)
    return base


def _run(spec=SYN_SPEC, srcs=None):
    srcs = srcs or _syn_srcs()
    sec = load_section(spec, "6.3 现有组件改造要点", "07 页面概念图") or ""
    bullets = parse_bullets(sec)
    tokens = parse_tokens(srcs[TOKENS_REL])
    rep = Report()
    judge_j0(bullets, rep)
    judge_j1(bullets, srcs[SHELL_REL], rep)
    judge_j2(bullets, srcs[SHELL_REL], rep)
    judge_j3(bullets, srcs[SHELL_REL], tokens, rep)
    judge_j4(bullets, srcs[SHELL_REL], rep)
    judge_j5(bullets, srcs[CARD_REL], rep)
    judge_j6(bullets, srcs[CARD_REL], rep)
    judge_j7(bullets, srcs[KPI_REL], rep)
    judge_j8(bullets, srcs[KPI_REL], rep)
    judge_j9(bullets, srcs[BUTTON_REL], rep)
    judge_j10(bullets, srcs[LOGIN_REL], rep)
    report_r_group(bullets, srcs, rep)
    return rep


def self_test() -> int:
    print("=== §6.3 组件改造要点门禁 · 自检（纯函数，不读真实仓库） ===")
    arms: list[tuple[str, bool, str]] = []

    sec = load_section(SYN_SPEC, "6.3 现有组件改造要点", "07 页面概念图") or ""
    bullets = parse_bullets(sec)
    arms.append(("Q0 解析出 6 条 bullet", len(bullets) == 6, f"{sorted(bullets)}"))
    arms.append(("Q0 Sidebar 期望值解析",
                 spec_px(bullets.get("Sidebar", "")) == 240.0, f"{spec_px(bullets.get('Sidebar',''))}"))
    arms.append(("Q0 Header 期望值解析",
                 spec_px(bullets.get("Header", "")) == 56.0, f"{spec_px(bullets.get('Header',''))}"))
    arms.append(("Q0 KpiCard 期望值解析",
                 spec_px(bullets.get("KpiCard", "")) == 28.0, f"{spec_px(bullets.get('KpiCard',''))}"))

    r = _run()
    arms.append(("Q1 干净对照必须无 fail", not r.fail, f"{[k for k, _ in r.fail]}"))

    # J1 底色错
    r = _run(srcs=_syn_srcs(**{SHELL_REL: SYN_SHELL.replace("bg-brand-950", "bg-slate-900")}))
    arms.append(("Q2 底色漂移必须红", any(k.startswith("J1:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J2 指示条
    r = _run(srcs=_syn_srcs(**{SHELL_REL: SYN_SHELL.replace("w-[3px]", "w-[2px]")}))
    arms.append(("Q3 指示条宽度错必须红", any(k.startswith("J2:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J3 顶栏高度
    r = _run(srcs=_syn_srcs(**{TOKENS_REL: "--topbar-h: 64px;\n"}))
    arms.append(("Q4 顶栏高度漂移必须红", any(k.startswith("J3:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J4 dark:
    r = _run(srcs=_syn_srcs(**{SHELL_REL: SYN_SHELL + '\n<div className="dark:bg-black" />\n'}))
    arms.append(("Q5 出现 dark: 必须红", any(k.startswith("J4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J5 hover 位移
    r = _run(srcs=_syn_srcs(**{CARD_REL: SYN_CARD + '\nconst x = "hover:-translate-y-0.5";\n'}))
    arms.append(("Q6 hover 位移必须红", any(k.startswith("J5:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J5 假阳性对照：hover 只改边框/阴影 ⇒ 不得红
    r = _run()
    arms.append(("Q7 hover 边框阴影不得红", not any(k.startswith("J5") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J6 Card default 缺 shadow-s1
    r = _run(srcs=_syn_srcs(**{CARD_REL: SYN_CARD.replace("shadow-s1", "")}))
    arms.append(("Q8 Card 缺 --s1 必须红", any(k.startswith("J6:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J7 数值字号
    r = _run(srcs=_syn_srcs(**{KPI_REL: SYN_KPI.replace("text-[28px]", "text-[24px]")}))
    arms.append(("Q9 KpiCard 字号漂移必须红", any(k.startswith("J7:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J8 单位小字
    r = _run(srcs=_syn_srcs(**{KPI_REL: SYN_KPI.replace(
        '{unit && <span className="text-body-sm text-ink-500">{unit}</span>}', "{unit}")}))
    arms.append(("Q10 单位非小字必须红", any(k.startswith("J8:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J9 渐变
    r = _run(srcs=_syn_srcs(**{BUTTON_REL: SYN_BUTTON.replace("bg-solid-brand", "bg-gradient-to-r from-indigo-600")}))
    arms.append(("Q11 Button 渐变必须红", any(k.startswith("J9:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J10 分段切换
    r = _run(srcs=_syn_srcs(**{LOGIN_REL: SYN_LOGIN.replace("<SegmentedControl value={mode} />", "")}))
    arms.append(("Q12 缺分段切换必须红", any(k.startswith("J10:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # J0 守卫：规范少一条 bullet
    r = _run(spec=SYN_SPEC.replace(
        "- **Button**：5 变体 3 尺寸，补 loading 态，去渐变\n", ""))
    arms.append(("Q13 规范 bullet 数变化必须红", any(k == "J0:bullet 数" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # ---- 注释剥离（J4/J5/J9 的**假阳性**对照）----------------------------
    # 项目**用 `dark:` 这个字面量来声明它的缺席**（AppShell.tsx:373 的 JSDoc）
    # ⇒ 这三条臂证明「判据认识项目自己的写法」。没有它们，剥离注释这个修复
    #   本身就没有判据（修好了也可能被下一次改动悄悄弄坏）。
    r = _run(srcs=_syn_srcs(**{SHELL_REL: SYN_SHELL + "\n/**\n * 本组件不含任何 dark: 变体。\n */\n"}))
    arms.append(("Q18 注释里的 dark: 不得红", not any(k.startswith("J4") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    r = _run(srcs=_syn_srcs(**{CARD_REL: SYN_CARD + '\n// hover 不再有 translate-y-0.5 位移\n'}))
    arms.append(("Q19 注释里的 hover 位移不得红", not any(k.startswith("J5") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    r = _run(srcs=_syn_srcs(**{BUTTON_REL: SYN_BUTTON + "\n/* 已去掉 bg-gradient-to-r */\n"}))
    arms.append(("Q20 注释里的渐变不得红", not any(k.startswith("J9") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # 对照组：剥注释**不许**把真实违规一起剥掉（Q5/Q6/Q11 已各自证明）
    # 这里再加一条「注释 + 真实违规同存」⇒ 必须仍然红，防止剥离写成「整行丢弃」
    r = _run(srcs=_syn_srcs(**{SHELL_REL: SYN_SHELL + '\n/**\n * 不含 dark: 变体\n */\n<div className="dark:bg-black" />\n'}))
    arms.append(("Q21 注释+真实违规同存必须红", any(k.startswith("J4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # R 组：只报不判（不得进 fail）
    r = _run()
    arms.append(("Q14 R 组只报不进 fail",
                 not r.fail and any("趋势线" in n for n in r.notes),
                 f"fail={len(r.fail)} notes={len(r.notes)}"))

    # 棘轮三分支
    kh, nh, ex = split_ratchet([("J1:x", "m")], {"J1:x": "登记"})
    arms.append(("Q15 棘轮内不阻断", len(kh) == 1 and not nh and not ex, f"{len(kh)}/{len(nh)}/{len(ex)}"))
    kh, nh, ex = split_ratchet([("J1:y", "m")], {"J1:x": "登记"})
    arms.append(("Q16 棘轮外阻断", len(nh) == 1 and not kh, f"{len(kh)}/{len(nh)}/{len(ex)}"))
    kh, nh, ex = split_ratchet([], {"J1:x": "登记"})
    arms.append(("Q17 豁免过期阻断", ex == ["J1:x"] and not kh and not nh, f"{ex}"))

    bad = 0
    for name, ok, detail in arms:
        print(f"  {'✓' if ok else '✗'} [{name}]" + ("" if ok else f"  ← {detail}"))
        bad += 0 if ok else 1
    print(f"\n自检 {len(arms) - bad}/{len(arms)} 通过")
    return 1 if bad else 0


# ===========================================================================
# 主流程
# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description="§6.3 现有组件改造要点门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（纯函数）")
    ap.add_argument("--why", action="store_true", help="打印期望值出处")
    ap.add_argument("--dump", action="store_true", help="打印解析出的规范条目")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if not SPEC.exists():
        print(f"[ENV] 缺规范文件：{SPEC} ⇒ **本轮结论不可信**（exit 2 = 环境问题）")
        return 2

    spec_text = SPEC.read_text(encoding="utf-8")
    section = load_section(spec_text, "6.3 现有组件改造要点", "07 页面概念图")
    if section is None:
        print("[ENV] `design-spec.md` 里找不到 `### 6.3 现有组件改造要点` ⇒ 规范结构变了")
        return 2

    bullets = parse_bullets(section)
    print(f"[ENV] 规范：{SPEC.relative_to(ROOT).as_posix()}")
    print(f"[ENV] §6.3 解析出 {len(bullets)} 条改造要点：{'、'.join(bullets)}")

    if args.why:
        print("\n[W] 期望值出处（一律从 §6.3 正文解析，不硬编码）")
        for name, body in bullets.items():
            px = spec_px(body)
            print(f"      {name:<12} 目标 px = {px if px is not None else '（无唯一值）'}")
        print("      Sidebar 底色 ← 反引号里的色名")

    files = dict(iter_sources())
    absent = [k for k in REQUIRED_FILES if k not in files]
    if absent:
        print("[ENV] 下列必需文件未进入扫描集（键约定不一致？）：")
        for k in absent:
            print(f"      {k}")
        print(f"[ENV] 扫描集样例键：{sorted(files)[:3]}")
        print("[ENV] ⇒ **本轮结论不可信**（exit 2 = 工具问题，不是产品缺陷）")
        return 2

    tokens = parse_tokens(files[TOKENS_REL])
    rep = Report()
    judge_j0(bullets, rep)
    judge_j1(bullets, files[SHELL_REL], rep)
    judge_j2(bullets, files[SHELL_REL], rep)
    judge_j3(bullets, files[SHELL_REL], tokens, rep)
    judge_j4(bullets, files[SHELL_REL], rep)
    judge_j5(bullets, files[CARD_REL], rep)
    judge_j6(bullets, files[CARD_REL], rep)
    judge_j7(bullets, files[KPI_REL], rep)
    judge_j8(bullets, files[KPI_REL], rep)
    judge_j9(bullets, files[BUTTON_REL], rep)
    judge_j10(bullets, files[LOGIN_REL], rep)

    print(f"[ENV] 扫描 {len(files)} 个源码文件")

    print("\n=== 判据 ===")
    for msg in rep.ok:
        print(f"  ✓ {msg}")

    known_hits, new_hits, expired = split_ratchet(rep.fail, KNOWN_GAPS)

    if known_hits:
        print("\n  ── 已登记欠账（棘轮，不阻断 CI；修好必须删豁免）──")
        for key, msg in known_hits:
            print(f"  • [{key}] {msg}\n      登记：{KNOWN_GAPS[key]}")

    if new_hits:
        print("\n  ── ✗ 新的规则违反（阻断）──")
        for key, msg in new_hits:
            print(f"  ✗ [{key}] {msg}")

    if expired:
        print("\n  ── ✗ 棘轮豁免已过期（该条目现在通过了，请删豁免）──")
        for key in expired:
            print(f"  ✗ [{key}] {KNOWN_GAPS[key]}")

    print("\n=== 只报不判（功能缺口 / 依赖，**不是判据**） ===")
    report_r_group(bullets, files, rep)
    for msg in rep.notes:
        print(f"  · {msg}")

    if args.dump:
        print("\n[D] §6.3 各条要点解析结果")
        for name, body in bullets.items():
            print(f"    {name:<12} px={spec_px(body)}  反引号={spec_backticks(body)}")

    if new_hits or expired:
        print(f"\n[结论] exit 1 —— 新违反 {len(new_hits)} 条 · 过期豁免 {len(expired)} 条")
        return 1
    print(f"\n[结论] exit 0 —— 已登记欠账 {len(known_hits)} 条（棘轮内），无新违反")
    return 0


if __name__ == "__main__":
    sys.exit(main())
