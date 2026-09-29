#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§7「页面概念图」门禁 —— 把 8 个**页面级**「核心改动」变成可执行判据。

## 为什么需要它（第 18 个门禁）

覆盖率矩阵修掉第 ④ 处失真（**节号可以活在表格行里**）后，`design-spec.md` 的
**§7.1–§7.7 第一次进入分母**，且**全部零引用**。原因很直接：
`## 07 页面概念图` 这一章**没有任何编号子标题**，8 个小节全是**表格首列**：

    | 7.3 | 智能问答 | 三栏化 + 引用溯源常驻面板 + 会话历史 | `mockups/03-qa.html` |

⇒ 基于标题的扫描**根本不知道它们存在**，这一整类**页面级要求**从未进入任何门禁视野。
`verify_runtime_health.py` 只查**运行时错误**（console / 请求失败），不查页面结构。

## ⚠️ 本门禁**只判可精确判的那部分** —— 这是刻意的

逐页实测（见 `deliverables/ui-design/page-concept-audit.md`）后发现 §7.x 是**四种状态混在一起**：

| 状态 | 处理 |
|---|---|
| ① 已实现、结构信号**精确** | **硬判**（A2–A5） |
| ② **被后端契约阻塞**（§7.4 环比 / 质量趋势） | **绝不建判据**（会变成永久假红）⇒ `KNOWN_GAPS` 记原因 |
| ③ **未实现且无人跟踪**（§7.3 三栏化 / 会话历史） | `KNOWN_GAPS` 记原因 + 登记号 |
| ④ 规范措辞与实现不符 / 过期 | **只报**（B 组），不判 |

> 🚨 **② 是这条门禁最容易做错的地方**。`admin-gap-analysis.md:499` 已根因定位：
> 「KPI 环比 ❌ **不可做** —— `CaseOut` / `ReviewOut` / `DispatchOut` **全无时间字段**，
> 无上一周期可比」。后端已冻结 ⇒ 这是**结构性不可做**。
> 给它建判据 = 每天稳定报一条无法修的缺陷。
> 与「判据必须认识项目自己的补偿机制」同族。

## 判据与出处

| 编号 | 判据 | 出处 |
|---|---|---|
| **A0** | 守卫：§7 表格必须解析出 **8** 行（7.1–7.8），每行「核心改动」非空 | `design-spec.md` §7 表 |
| **A1** | 8 个页面的**归属文件存在** | 规范只给页面名、不给路由 ⇒ 映射表内联在 `PAGES` |
| **A2** | §7.5「**六段式**分析」⇒ `SECTIONS` 长度 == 规范里的中文段数，且 `n` 为 `1..N` 连续 | §7.5 行「六段式分析」 |
| **A3** | §7.3「引用溯源**常驻**面板」⇒ 有 `CitationPanel` 且带 `hidden` + `lg:` 可见类；令牌 `--citation-panel-w` == 规范写明的 px | §7.3 行；`§0「引用溯源 · 弹窗抽屉遮挡正文 · 桌面常驻面板」`「桌面常驻面板 **380px**」 |
| **A4** | §7.2「**KPI** + 待办列表」⇒ 使用 `KpiCard` | §7.2 行 |
| **A5** | §7.6「三栏完整 IM + **案件与委托侧栏**」⇒ 存在 `aria-label` 含「案件 / 委托」的 `aside` | §7.6 行 |

**B 组（只报不判）**：§7.1 演示账号数与规范「2×2」的差 · §7.6 `aside` 总数 ·
§7.7/§7.8 的要素（**弱信号**，关键词命中，不足以支撑硬判）。

## 退出码

`0` 通过 · `1` 产品缺陷（页面文件缺失 / 结构要素消失）· `2` 环境问题
（规范文件缺失 / §7 表解析不出 8 行 / 期望值在规范里找不到出处）。

用法：
    python evidence/verify_page_concepts.py
    python evidence/verify_page_concepts.py --self-test
    python evidence/verify_page_concepts.py --why 7.5
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

SPEC_REL = "deliverables/ui-design/design-spec.md"
TOKENS_REL = "frontend/packages/ui/src/tokens.css"
PRESET_REL = "frontend/tailwind.preset.ts"

# §7.x → (页面名, 归属文件)。**路由映射是判据的一部分**：规范只给页面名不给路由，
# 所以这张表要能被人工核对（每条都对着 §7 表行的「页面」列）。
PAGES: dict[str, tuple[str, str]] = {
    "7.1": ("登录页", "frontend/packages/ui/src/components/LoginShell.tsx"),
    "7.2": ("工作台首页", "frontend/apps/web/app/(app)/page.tsx"),
    "7.3": ("智能问答", "frontend/apps/web/app/(app)/qa/page.tsx"),
    "7.4": ("运营驾驶舱", "frontend/apps/admin/app/(app)/page.tsx"),
    "7.5": ("律师案件详情", "frontend/apps/lawyer/app/(app)/cases/[id]/page.tsx"),
    "7.6": ("IM 智能咨询", "frontend/apps/im/app/(app)/chat/page.tsx"),
    "7.7": ("文书工作台", "frontend/apps/web/app/(app)/documents/page.tsx"),
    "7.8": ("运营后台·案件管理", "frontend/apps/admin/app/(app)/cases/page.tsx"),
}

# ⚠️ 只有**规范**缺失算环境问题（exit 2）。
# **页面文件缺失是产品缺陷**（exit 1，判据 A1）—— 两者语义不同，不能混进同一个守卫。
ENV_FILES = (SPEC_REL, TOKENS_REL, PRESET_REL)

# 已登记、**明确不建判据**的缺口（每条都必须能指到出处）。
KNOWN_GAPS: dict[str, str] = {
    "7.3/三栏化": "登记 #62 —— 实现为 **2 栏**（主栏 + 引用栏）。待拍板：补实现 or 改规范。",
    "7.3/会话历史": "登记 #62 —— `会话/历史/History/history/conversations` 在该页 **0 命中**。待拍板。",
    "7.4/环比": "登记 #63 —— `admin-gap-analysis.md:499` 已根因定位**「不可做」**："
                "`CaseOut`/`ReviewOut`/`DispatchOut` **全无时间字段**，后端已冻结。**不建判据**。",
    "7.4/流失标注": "登记 #63 —— 同属需时间维度的统计，同上不可做。",
    "7.4/质量趋势": "登记 #63 —— 同上不可做。",
    "7.7/纸感编辑区": "只报：信号为关键词命中，判别力不足，**不硬判**。",
    "7.8/全租户查询": "只报：信号为关键词命中，判别力不足，**不硬判**。",
}

# ────────────────────────────── 解析器 ──────────────────────────────

# §7 表行：`| 7.3 | 智能问答 | 三栏化 + … | \`mockups/03-qa.html\` |`
ROW_RE = re.compile(r"(?m)^\|\s*(7\.\d+)\s*\|(.+?)\|\s*$")

# `const SECTIONS: {…}[] = [ … ];`
SECTIONS_RE = re.compile(r"const\s+SECTIONS\s*:[^=]*=\s*\[(.*?)\n\];", re.S)
# ⚠️ **不锚行首**。真实文件是**多行对象**（`n: 1,` 独占一行），但锚行首等于把
# 「Prettier 不会把短对象内联成一行」偷偷当成判据前提 —— 一旦被内联就会**假红**（实测踩过）。
# `(?<![\w$.])` 用来挡 `icon:` 里子串 `n:` 这种误命中（`icon:` 在真实数组体内**存在**）。
SECTION_N_RE = re.compile(r"(?<![\w$.])n\s*:\s*(\d+)\s*,")

# `<CitationPanel … />`
PANEL_CALL_RE = re.compile(r"<CitationPanel\b(.*?)/>", re.S)

# `--citation-panel-w: 380px;`
PANEL_W_RE = re.compile(r"--citation-panel-w\s*:\s*([0-9.]+)px\s*;")
# preset 里 `citation: "var(--citation-panel-w)"`
PANEL_TOKEN_USE_RE = re.compile(r"citation\s*:\s*\"var\(--citation-panel-w\)\"")

# 规范里写明的面板宽度出处：`桌面常驻面板 380px`
SPEC_PANEL_PX_RE = re.compile(r"常驻面板\s*([0-9]+)\s*px")

# `<aside … aria-label="…" …>`
ASIDE_RE = re.compile(r"<aside\b(.*?)(?:>|/>)", re.S)
ARIA_RE = re.compile(r"aria-label\s*=\s*\"([^\"]*)\"")

CN_DIGIT = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
            "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//")


def strip_comments(src: str) -> str:
    """剥掉 TS/TSX 注释；**保留行号**（块注释替换成等量换行）。

    ⚠️ 结构性判据也必须在**剥注释后**的源码上判：被注释掉的 `<aside>` /
    `<CitationPanel>` 会让判据**假绿**（「还在」但已失效）。
    反过来，注释里写「本页不含 X」会让「必须有 X」的判据**假红**。
    **两侧都要剥。**
    """
    src = BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), src)
    out: list[str] = []
    for ln in src.splitlines():
        m = LINE_COMMENT_RE.search(ln)
        out.append(ln[: m.start()] if m else ln)
    return "\n".join(out)


def load(rel: str) -> str:
    p = ROOT / rel
    if not p.exists():
        raise FileNotFoundError(rel)
    return p.read_text(encoding="utf-8", errors="replace")


def parse_spec_rows(spec: str) -> dict[str, dict[str, str]]:
    """从 §7 表解析 `{节号: {name, change, mockup}}`。"""
    rows: dict[str, dict[str, str]] = {}
    for m in ROW_RE.finditer(spec):
        sec = m.group(1)
        cells = [c.strip() for c in m.group(2).split("|")]
        if len(cells) < 3:
            continue
        rows[sec] = {"name": cells[0], "change": cells[1], "mockup": cells[2]}
    return rows


# ────────────────────────────── 判据 ──────────────────────────────


def judge_a0(rows: dict[str, dict[str, str]]) -> list[str]:
    """守卫：§7 必须解析出 7.1–7.8 共 8 行，且每行「核心改动」非空。

    解析不出来（表格被改格式 / 章节被删）⇒ **不能继续判**，否则后面的判据
    会在一张空表上「全部通过」—— 那是最典型的假绿。
    """
    want = [f"7.{i}" for i in range(1, 9)]
    miss = [s for s in want if s not in rows]
    empty = [s for s in want if s in rows and not rows[s]["change"].strip()]
    bad: list[str] = []
    if miss:
        bad.append(f"§7 表缺行：{miss}（规范格式可能变了 ⇒ 本门禁的前提不成立）")
    if empty:
        bad.append(f"§7 表这些行的「核心改动」为空：{empty}")
    return bad


def judge_a1(rows: dict[str, dict[str, str]], exists=None) -> tuple[list[str], list[str]]:
    """每个 §7.x 的归属文件必须存在。返回 (缺陷, 报告)。

    `exists` 可注入：真仓库里 8 个文件**都在** ⇒ 不注入的话这条判据**永远只会绿**
    （「一条只会绿的防线不算防线」）。自检注入恒假探针来证明它会红。
    """
    exists = exists or (lambda rel: (ROOT / rel).exists())
    bad: list[str] = []
    ok: list[str] = []
    for sec in sorted(PAGES):
        name, rel = PAGES[sec]
        if sec not in rows:
            continue
        if exists(rel):
            ok.append(f"{sec} {name} → {rel}")
        else:
            bad.append(f"{sec} {name}：归属文件不存在 `{rel}`（页面级要求未落地）")
    return bad, ok


def judge_a2(rows: dict[str, dict[str, str]], lawyer_src: str) -> tuple[list[str], str]:
    """§7.5「六段式分析」⇒ `SECTIONS` 长度 == 规范里的中文段数，且 `n` 连续。

    **期望值从规范现读**：§7.5 行写「**六段式**分析」⇒ 期望 6，不硬编码。
    """
    change = rows.get("7.5", {}).get("change", "")
    m = re.search(r"([一二两三四五六七八九十])段", change)
    if not m:
        return [], "§7.5 行里找不到「N段式」⇒ 期望值没有出处，跳过 A2"
    want = CN_DIGIT[m.group(1)]

    mm = SECTIONS_RE.search(lawyer_src)
    if not mm:
        return ["§7.5：在 `cases/[id]/page.tsx` 里找不到 `const SECTIONS = [...]`"], ""
    ns = [int(x) for x in SECTION_N_RE.findall(mm.group(1))]
    bad: list[str] = []
    if len(ns) != want:
        bad.append(f"§7.5「{m.group(1)}段式」⇒ 期望 **{want}** 段，实际 `SECTIONS` **{len(ns)}** 段")
    if ns and ns != list(range(1, len(ns) + 1)):
        bad.append(f"§7.5：`SECTIONS` 的 `n` 不连续/不从 1 开始 ⇒ {ns}")
    return bad, f"§7.5 六段式：期望 {want} / 实际 {len(ns)}（n={ns}）"


def judge_a3(rows: dict[str, dict[str, str]], spec: str,
             qa_src: str, tokens: str, preset: str) -> tuple[list[str], str]:
    """§7.3「引用溯源**常驻**面板」⇒ 桌面常驻（`hidden` + `lg:` 可见）+ 宽度令牌 == 规范值。"""
    if "常驻" not in rows.get("7.3", {}).get("change", ""):
        return [], "§7.3 行未写「常驻」⇒ 跳过 A3"

    px = SPEC_PANEL_PX_RE.search(spec)
    if not px:
        return [], "规范里找不到「常驻面板 Npx」⇒ 期望值没有出处，跳过 A3"
    want_px = px.group(1)

    bad: list[str] = []
    # ① 面板必须**桌面常驻**：带 `hidden`（窄屏默认不显示）+ `lg:` 可见类
    persistent = False
    for m in PANEL_CALL_RE.finditer(qa_src):
        props = m.group(1)
        if "hidden" in props and re.search(r"lg:(flex|block|grid)", props):
            persistent = True
            break
    if not persistent:
        bad.append("§7.3：`qa/page.tsx` 里没有「桌面常驻」的 `CitationPanel`"
                   "（需要同时含 `hidden` 与 `lg:flex|block|grid`）")
    # ② 宽度令牌
    tw = PANEL_W_RE.search(tokens)
    if not tw:
        bad.append("§7.3：`tokens.css` 里没有 `--citation-panel-w` 令牌")
    elif tw.group(1) != want_px:
        bad.append(f"§7.3：常驻面板宽度令牌 `--citation-panel-w: {tw.group(1)}px` "
                   f"≠ 规范写明的 **{want_px}px**")
    if not PANEL_TOKEN_USE_RE.search(preset):
        bad.append("§7.3：`tailwind.preset.ts` 里没有把 `citation` 宽度映射到 `var(--citation-panel-w)`")
    return bad, f"§7.3 常驻面板：规范 {want_px}px / 令牌 {tw.group(1) if tw else '—'}px"


def judge_a4(rows: dict[str, dict[str, str]], web_home_src: str) -> tuple[list[str], str]:
    """§7.2「KPI + 待办列表」⇒ 使用 `KpiCard`。"""
    if "KPI" not in rows.get("7.2", {}).get("change", ""):
        return [], "§7.2 行未写 KPI ⇒ 跳过 A4"
    n = len(re.findall(r"<KpiCard\b", web_home_src))
    if n == 0:
        return ["§7.2：`web/app/(app)/page.tsx` 没有渲染 `KpiCard`（「KPI」要素消失）"], ""
    return [], f"§7.2 KPI：`KpiCard` 渲染 {n} 处"


def judge_a5(rows: dict[str, dict[str, str]], im_src: str) -> tuple[list[str], str]:
    """§7.6「三栏完整 IM + **案件与委托侧栏**」⇒ 存在语义为「案件 / 委托」的 `aside`。"""
    change = rows.get("7.6", {}).get("change", "")
    if not re.search(r"案件.*侧栏|侧栏.*案件", change):
        return [], "§7.6 行未写「案件…侧栏」⇒ 跳过 A5"
    labels: list[str] = []
    for m in ASIDE_RE.finditer(im_src):
        a = ARIA_RE.search(m.group(1))
        if a:
            labels.append(a.group(1))
    hit = [s for s in labels if ("案件" in s or "委托" in s)]
    if not hit:
        return ([f"§7.6：`im/chat/page.tsx` 里没有语义为「案件 / 委托」的 `aside`"
                 f"（现有 aria-label：{labels}）"], "")
    return [], f"§7.6 案件侧栏：`aria-label` = {hit}（全部 aside = {labels}）"


def report_b(rows: dict[str, dict[str, str]], login_src: str, im_src: str) -> list[str]:
    """B 组：**只报不判**。"""
    out: list[str] = []
    # §7.1 演示账号数与「2×2」
    m = re.search(r"const\s+DEFAULT_DEMO_ACCOUNTS\s*:[^=]*=\s*\[(.*?)\n\];", login_src, re.S)
    if m:
        n = len(re.findall(r"username\s*:", m.group(1)))
        out.append(f"§7.1：规范写「演示账号收敛为 **2×2**」（决策 11 同），"
                   f"实现 `DEFAULT_DEMO_ACCOUNTS` = **{n}** 个、按角色分组 ⇒ **措辞与实现不符**（只报）")
    # §7.6 aside 总数
    n_aside = len(re.findall(r"<aside\b", im_src))
    out.append(f"§7.6：`<aside>` 共 **{n_aside}** 个（「三栏」= 主栏 + {n_aside} 个侧栏）（只报）")
    return out


# ────────────────────────────── 自检 ──────────────────────────────

SYN_SPEC = """
| 编号 | 页面 | 核心改动 | 高保真稿 |
|---|---|---|---|
| 7.1 | 登录页 | 品牌叙事 + 表单双栏；演示账号收敛为 2×2 | `mockups/01-login.html` |
| 7.2 | 工作台首页 | 大卡片门户 → KPI + 待办列表工作台 | `mockups/02-workspace-home.html` |
| 7.3 | 智能问答 | 三栏化 + 引用溯源常驻面板 + 会话历史 | `mockups/03-qa.html` |
| 7.4 | 运营驾驶舱 | 假进度条 → 真实数值 + 环比 + 流失标注 + 质量趋势 | `mockups/04-admin-cockpit.html` |
| 7.5 | 律师案件详情 | **新建页面**：六段式分析 + 复核流转 + 证据清单 | `mockups/05-lawyer-case.html` |
| 7.6 | IM 智能咨询 | 固定卡片 → 三栏完整 IM + 案件与委托侧栏 | `mockups/06-im-chat.html` |
| 7.7 | 文书工作台 | 纸感编辑区 + 变量高亮 + 实时风险标注 | `mockups/07-documents.html` |
| 7.8 | 运营后台·案件管理 | **新增**：平台视角的全租户案件查询（只读 + 派单） | 见下方概念说明 |

> 桌面常驻面板 380px / 移动页内展开
"""

def _syn_sections(n: int) -> str:
    """复刻**真实文件形状**（`cases/[id]/page.tsx:302`：多行对象、`n: k,` 独占一行、
    体内含 `icon: …`）。夹具必须复刻真实形状，否则自检是在测一个**虚构的输入**。
    """
    body = "".join(
        f'  {{\n    key: "k{i}",\n    n: {i},\n    title: "第{i}段",\n'
        f'    icon: <FileText className="h-4 w-4" />,\n  }},\n'
        for i in range(1, n + 1)
    )
    return ("const SECTIONS: {\n  key: SectionKey;\n  n: number;\n  title: string;\n"
            "}[] = [\n" + body + "];\n")


def _syn_sections_compact(n: int) -> str:
    """同一语义的**紧凑单行**形状 —— 判据不得依赖排版。"""
    body = "".join(
        f'  {{ key: "k{i}", n: {i}, title: "第{i}段", icon: <FileText /> }},\n'
        for i in range(1, n + 1)
    )
    return "const SECTIONS: { key: SectionKey; n: number; title: string }[] = [\n" + body + "];\n"


SYN_LAWYER_6 = _syn_sections(6)
SYN_LAWYER_5 = _syn_sections(5)

SYN_QA = """<CitationPanel citations={c} className="hidden lg:flex" />
<BottomSheet><CitationPanel citations={c} variant="inline" /></BottomSheet>
"""

SYN_TOKENS = ":root { --citation-panel-w: 380px; }"
SYN_PRESET = 'width: { citation: "var(--citation-panel-w)" },'


def self_test() -> int:
    """纯函数臂：钉住「期望值从规范现读」「守卫会咬合」「两侧都剥注释」。"""
    fails: list[str] = []
    total = 0

    def arm(name: str, got, want) -> None:
        nonlocal total
        total += 1
        if got != want:
            fails.append(f"{name}\n      实际 {got!r}\n      期望 {want!r}")

    rows = parse_spec_rows(SYN_SPEC)
    arm("Q1 解析出 8 行", len(rows), 8)
    arm("Q1b §7.3 名称", rows["7.3"]["name"], "智能问答")
    arm("Q1c §7.3 核心改动含「常驻」", "常驻" in rows["7.3"]["change"], True)
    arm("Q1d §7.5 高保真稿", rows["7.5"]["mockup"], "`mockups/05-lawyer-case.html`")

    arm("Q2 A0 守卫：8 行齐全 ⇒ 无缺陷", judge_a0(rows), [])
    bad = dict(rows)
    del bad["7.7"]
    arm("Q2b A0 守卫：缺一行 ⇒ 报出", len(judge_a0(bad)), 1)
    arm("Q2e A0 缺行 ⇒ 守卫非空（`run()` 据此 exit 2，不是 exit 1）", bool(judge_a0(bad)), True)

    # A1：**必须能红** —— 真仓库 8 个文件都在，不注入它就永远只会绿
    arm("Q2c A1：注入「文件不存在」⇒ 8 条缺陷",
        len(judge_a1(rows, lambda rel: False)[0]), 8)
    arm("Q2d A1：全部存在 ⇒ 不报", judge_a1(rows, lambda rel: True)[0], [])

    # A2：期望值必须**从规范现读**（六段式 ⇒ 6）
    arm("Q3 A2：6 段（多行真实形状）⇒ 通过", judge_a2(rows, SYN_LAWYER_6)[0], [])
    bad2 = judge_a2(rows, SYN_LAWYER_5)[0]
    arm("Q3b A2：5 段 ⇒ 红", len(bad2), 1)
    arm("Q3c A2：报出实际段数", "5" in bad2[0] if bad2 else False, True)
    # 🚨 判据不得依赖排版：紧凑单行也必须认（这次回归就是从这里来的）
    arm("Q3d A2：紧凑单行形状也认", judge_a2(rows, _syn_sections_compact(6))[0], [])

    # A3：常驻面板
    arm("Q4 A3：常驻 + 380 令牌 ⇒ 通过",
        judge_a3(rows, SYN_SPEC, SYN_QA, SYN_TOKENS, SYN_PRESET)[0], [])
    # 去掉 hidden lg:flex ⇒ 必须红
    arm("Q4b A3：面板非常驻 ⇒ 红",
        len(judge_a3(rows, SYN_SPEC, '<CitationPanel citations={c} />',
                     SYN_TOKENS, SYN_PRESET)[0]), 1)
    # 令牌值不符 ⇒ 必须红
    arm("Q4c A3：令牌 360 ≠ 规范 380 ⇒ 红",
        len(judge_a3(rows, SYN_SPEC, SYN_QA,
                     ":root { --citation-panel-w: 360px; }", SYN_PRESET)[0]), 1)

    # A4
    arm("Q5 A4：有 KpiCard ⇒ 通过", judge_a4(rows, "<KpiCard label='x' />")[0], [])
    arm("Q5b A4：无 KpiCard ⇒ 红", len(judge_a4(rows, "<div />")[0]), 1)

    # A5
    syn_im = '<aside className="x" aria-label="会话列表">a</aside>\n<aside aria-label="案件上下文">b</aside>'
    arm("Q6 A5：有案件侧栏 ⇒ 通过", judge_a5(rows, syn_im)[0], [])
    arm("Q6b A5：只有会话列表 ⇒ 红",
        len(judge_a5(rows, '<aside aria-label="会话列表">a</aside>')[0]), 1)

    # 🚨 剥注释：**被注释掉的元素不得算存在**
    commented = strip_comments('/* <aside aria-label="案件上下文">x</aside> */\n<div />')
    arm("Q7 注释里的 aside 不得被算作存在", judge_a5(rows, commented)[0] != [], True)
    # 反向：注释里写「不含 X」不得让「必须有 X」的判据假红
    syn_im2 = '<aside aria-label="案件上下文">b</aside>\n// 本页不含任何「委托」字样\n'
    arm("Q7b 注释里的「不含委托」不得致假红", judge_a5(rows, strip_comments(syn_im2))[0], [])

    # B 组只报
    syn_login = 'const DEFAULT_DEMO_ACCOUNTS: DemoAccount[] = [\n  { username: "a" },\n  { username: "b" },\n];'
    rep = report_b(rows, syn_login, syn_im)
    arm("Q8 B 组报出演示账号数", any("2" in r and "2×2" in r for r in rep), True)

    print("=" * 78)
    print(f"自检：{total - len(fails)}/{total} 通过")
    for f in fails:
        print(f"  ✗ {f}")
    print("=" * 78)
    return 1 if fails else 0


# ────────────────────────────── 主流程 ──────────────────────────────


def run(why: str | None = None) -> int:
    missing = [r for r in ENV_FILES if not (ROOT / r).exists()]
    if missing:
        print(f"[ENV] 必需文件缺失：{missing}", file=sys.stderr)
        return 2

    spec = load(SPEC_REL)
    rows = parse_spec_rows(spec)
    print("=" * 78)
    print("§7「页面概念图」门禁 —— 8 个页面级「核心改动」")
    print("=" * 78)

    guard = judge_a0(rows)
    if guard:
        for g in guard:
            print(f"[ENV] {g}", file=sys.stderr)
        return 2

    print(f"§7 表解析出 **{len(rows)}** 行（7.1–7.8）\n")

    if why:
        r = rows.get(why)
        if not r:
            print(f"[ENV] §{why} 不在 §7 表里", file=sys.stderr)
            return 2
        print(f"§{why} {r['name']}")
        print(f"  核心改动：{r['change']}")
        print(f"  高保真稿：{r['mockup']}")
        print(f"  归属文件：{PAGES.get(why, ('—', '—'))[1]}")
        gaps = {k: v for k, v in KNOWN_GAPS.items() if k.startswith(why + "/")}
        for k, v in gaps.items():
            print(f"  ⚠️ 已登记不判：{k} —— {v}")
        return 0

    defects: list[str] = []
    reports: list[str] = []

    d, ok = judge_a1(rows)
    defects += d
    reports += [f"A1 ✓ {line}" for line in ok]

    d, info = judge_a2(rows, strip_comments(load(PAGES["7.5"][1])))
    defects += d
    if info:
        reports.append(f"A2 {'✓' if not d else '✗'} {info}")

    d, info = judge_a3(rows, spec, strip_comments(load(PAGES["7.3"][1])),
                       load(TOKENS_REL), load(PRESET_REL))
    defects += d
    if info:
        reports.append(f"A3 {'✓' if not d else '✗'} {info}")

    d, info = judge_a4(rows, strip_comments(load(PAGES["7.2"][1])))
    defects += d
    if info:
        reports.append(f"A4 {'✓' if not d else '✗'} {info}")

    d, info = judge_a5(rows, strip_comments(load(PAGES["7.6"][1])))
    defects += d
    if info:
        reports.append(f"A5 {'✓' if not d else '✗'} {info}")

    print("── 判据（判）──")
    for r in reports:
        print(f"  {r}")
    print()
    print("── 只报（不判）──")
    for r in report_b(rows, strip_comments(load(PAGES["7.1"][1])),
                      strip_comments(load(PAGES["7.6"][1]))):
        print(f"  {r}")
    print()
    print("── 已登记、**明确不建判据**的缺口（每条都有出处）──")
    for k, v in KNOWN_GAPS.items():
        print(f"  ⚠️ {k} —— {v}")
    print()

    if defects:
        print("── ❌ 缺陷 ──")
        for x in defects:
            print(f"  {x}")
        print(f"\n❌ §7 页面概念图门禁：**{len(defects)}** 处欠账")
        return 1
    print("✅ §7 页面概念图门禁通过（0 欠账）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="§7「页面概念图」门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（纯函数）")
    ap.add_argument("--why", metavar="7.5", help="打印某一节的出处与归属")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    return run(args.why)


if __name__ == "__main__":
    raise SystemExit(main())
