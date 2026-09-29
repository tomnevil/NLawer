#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""im 移动端导航的**静态不变量**门禁 —— `im-mobile-nav-spec.md` §6.1 / §6.2 / §6.3。

## 为什么需要它：一个「CI 级地板」

§6.1 / §6.2 / §6.3 的判据**不是不存在**，而是**全在渲染级探针里**：

| 节 | 谁在判 | 在 PR 上跑吗 |
|---|---|---|
| §6.2 安全区不双算 | `verify_im_safe_area.py` | ⚠️ **只在主干跑**（`browser-all`） |
| §6.1 TabBar 桌面隐藏 | `verify_im_tabs.py` | ⚠️ **只在主干跑**（同上） |
| §6.3 高度与滚动 | **没人判** | — |

📌 **2026-09-26 状态更新**（拍板 §11.4-③）：这两条**已接进 CI**（`browser-all` job，
见 `run_ci_probes.CI_ELSEWHERE`），但那个 job **只在主干 / 手动触发**
（四端生产构建 + 探针 ≈22–25 min）⇒ **PR 上一条守 §6.1/§6.2 的判据都没有**；
而 §6.3 仍然哪里都没判。
（`verify_im_safe_area.py` 的 docstring 里出现过 `h-dvh`，但那句是在**描述**根容器，
不是断言 —— 用「关键词命中」当判据正是本项目的坑 14。）

⇒ **本静态地板的理由不但没消失，反而更硬**：它是 **PR 上唯一**守 §6.1/§6.2 的那条。

⇒ 本门禁补一条**静态地板**：源码级、不需要浏览器、**可进 CI**。

## 与渲染级探针的**分工**（不是重复）

| 层 | 拦什么 | 为什么不能合并 |
|---|---|---|
| **本门禁（静态）** | 「**改错了**」—— Tab 少一项、根容器又把 `paddingBottom` 加回来、`h-dvh` 被换成 `min-h-dvh` | 静态可判、秒级、**CI 每轮都跑** |
| 渲染探针（动态） | 「**量出来不对**」—— 真机上 34px 空白带、三栏宽度被挤 | 几何量只有真浏览器有值（`env()` 在桌面恒为 `0px`） |

**两者都要有**：静态地板拦不住几何回归，渲染探针**在 PR 上不跑**（只在主干）。

## 判据（期望值一律从**规范现读**，不硬编码）

- **A0 守卫**：规范读不出「N 项 Tab」或 §4 映射表 ⇒ **exit 2**（判据跑不了，不是产品坏了）
- **A1 §8.4 / §4**：`TABS` 恰好 N 项；名字与**顺序** == 规范现读；`href` == 规范 §4 表现读；
  `id` == 一级路径段（§4 的约束：「tab id 必须等于一级路径段」）
- **A2 §6.1**：`TabBar.tsx` 必须仍带 `lg:hidden` —— §6.1 明写「组件自带 `lg:hidden`，
  **不需要额外判断**」。这条前提一旦被去掉，**三端**（web / lawyer / im）的桌面端
  都会多出一条底栏，且**没有任何静态门禁会红**。
- **A3 §6.2**：根容器的 `style` **不得**含 `paddingBottom`（`TabBar` 内部已经加过一次）；
  内容区**必须**同时有 `pb-[var(--actionbar-bottom)]` 与 `lg:pb-[var(--safe-bottom)]`
  （移动端让位 56px+安全区、桌面端只让安全区）
- **A4 §6.3**：根容器**必须**有 `h-dvh` 与 `overflow-hidden`；全文件**不得**出现 `min-h-dvh`
- **A5 §4**：四个落地页文件必须存在（Tab 指向 404 是本项目 `#15` 的原缺陷形态）
- **B 组只报**：四页行数（信息性，不判）

## 退出码

`0` 通过 · `1` **产品缺陷** · `2` 环境问题（规范读不出期望值 / 源码结构解析不了）

⚠️ **A0 与 A1 的边界**：`TABS` **数组整个不存在** ⇒ **exit 1**（TabBar 根本没接上，是产品缺陷）；
`TABS` 在但**条目解析不出来** ⇒ **exit 2**（判据无法运行，先修工具）。
「产品坏了」与「我没测成」必须分开 —— 混成一个布尔会把两者互相报错。

用法：
    python evidence/verify_im_mobile_nav.py
    python evidence/verify_im_mobile_nav.py --self-test
    python evidence/verify_im_mobile_nav.py --why 6.2
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

SPEC_REL = "deliverables/ui-design/im-mobile-nav-spec.md"
LAYOUT_REL = "frontend/apps/im/app/(app)/layout.tsx"
TABBAR_REL = "frontend/packages/ui/src/components/TabBar.tsx"

#: §5.1–§5.4 的落地页（规范 §4 表逐行给出）
PAGES: dict[str, str] = {
    "5.1": "frontend/apps/im/app/(app)/page.tsx",
    "5.2": "frontend/apps/im/app/(app)/chat/page.tsx",
    "5.3": "frontend/apps/im/app/(app)/cases/page.tsx",
    "5.4": "frontend/apps/im/app/(app)/me/page.tsx",
}

# ── 剥注释 ────────────────────────────────────────────────────────────────
# 🚨 **必须剥**：`layout.tsx` 的注释里**明写了** `paddingBottom`、`--safe-bottom`、
# 甚至「不能改成 `min-h-dvh`」—— 不剥的话这些「声明缺席」的注释本身就会**假红**
# （本项目的坑 14：项目用该字面量声明它的缺席）。
# 块注释**换成等量换行**，保住行号（报错要能指到行）。
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# ⚠️ 负向后顾 `(?<![:\w])` 防止把 `https://` 里的 `//` 当注释起点。
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//[^\n]*")


def strip_comments(text: str) -> str:
    text = BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return LINE_COMMENT_RE.sub("", text)


# ── 从**规范**现读期望值 ───────────────────────────────────────────────────
#: 「客户端 4 项 Tab：工作台 / 咨询 / 我的案件 / 我的」
SPEC_TABS_RE = re.compile(r"客户端\s*(\d+)\s*项\s*Tab\s*[：:]\s*\*{0,2}([^*；|]+)")
#: §4 映射表的行：`| 1 | 工作台 | `/` | **新建** | 待建 |`
SPEC_MAP_ROW_RE = re.compile(r"(?m)^\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*`([^`]+)`\s*\|")

# ── 从**源码**解析结构 ────────────────────────────────────────────────────
TABS_BLOCK_RE = re.compile(r"const\s+TABS\s*:[^=]*=\s*\[(.*?)\n\];", re.S)
TAB_ENTRY_RE = re.compile(
    r'\{\s*id:\s*"([^"]*)"\s*,\s*label:\s*"([^"]*)"\s*,\s*href:\s*"([^"]*)"\s*\}'
)
ROOT_ID_RE = re.compile(r'const\s+ROOT_ID\s*=\s*"([^"]*)"')
#: 根容器的 `style`：以 `--safe-top` 为锚（三个安全区里只有它会出现在根容器上）
ROOT_STYLE_RE = re.compile(r"style=\{\{([^{}]*--safe-top[^{}]*)\}\}", re.S)


class SpecUnreadable(Exception):
    """规范里读不出期望值 ⇒ 判据无法运行 ⇒ exit 2。"""


def read_expected(spec_text: str) -> tuple[int, list[str], dict[str, str]]:
    """从规范现读：Tab 数、名字（有序）、名字→href。"""
    m = SPEC_TABS_RE.search(spec_text)
    if not m:
        raise SpecUnreadable(
            "规范里找不到「客户端 N 项 Tab：…」这句 ⇒ 无法确定 Tab 的数量与名字"
        )
    n = int(m.group(1))
    names = [x.strip() for x in m.group(2).split("/") if x.strip()]
    if len(names) != n:
        raise SpecUnreadable(
            f"规范自相矛盾：说 {n} 项，却列了 {len(names)} 个名字 {names}"
        )
    # §4 映射表
    sec4 = _section(spec_text, r"^##\s*4\.", r"^##\s*5\.")
    hrefs: dict[str, str] = {}
    for row in SPEC_MAP_ROW_RE.finditer(sec4):
        hrefs[row.group(1).strip()] = row.group(2).strip()
    if not hrefs:
        raise SpecUnreadable("规范 §4「路由映射方案」的表读不出任何 `Tab → href` 行")
    missing = [x for x in names if x not in hrefs]
    if missing:
        raise SpecUnreadable(f"规范 §4 表里缺少这些 Tab 的路由：{missing}")
    return n, names, hrefs


def _section(text: str, start_pat: str, end_pat: str) -> str:
    s = re.search(start_pat, text, re.M)
    if not s:
        return ""
    e = re.search(end_pat, text[s.end():], re.M)
    return text[s.end(): s.end() + e.start()] if e else text[s.end():]


def parse_tabs(layout_src: str) -> tuple[str, list[tuple[str, str, str]]]:
    """返回 (`found` | `absent` | `unparsable`, 条目)。"""
    if "TABS" not in layout_src:
        return "absent", []
    m = TABS_BLOCK_RE.search(layout_src)
    if not m:
        return "unparsable", []
    entries = TAB_ENTRY_RE.findall(m.group(1))
    if not entries:
        return "unparsable", []
    return "found", entries


def root_open_tag(src: str) -> str | None:
    """根容器的**开标签**（从 `<div` 到 `style={{…}}` 结束）。

    🚨 **必须锚到根容器**，不能全文件找 `h-dvh`：`layout.tsx` 的**会话恢复占位**
    （`if (loading)` 分支）里**也有一个 `h-dvh`** —— 全文件搜会在根容器被改坏时
    **假绿**（占位那个把它顶上）。
    """
    m = ROOT_STYLE_RE.search(src)
    if not m:
        return None
    start = src.rfind("<div", 0, m.start())
    if start < 0:
        return None
    return src[start: m.end()]


# ── 判据 ──────────────────────────────────────────────────────────────────
def evaluate(
    spec_text: str,
    layout_src: str,
    tabbar_src: str,
    exists=None,
) -> tuple[int, list[str], list[str]]:
    """**纯函数**：返回 `(rc, defects, reports)`。

    `exists` 可注入（默认查真实文件系统）—— 真仓库里四个页面**都在**，
    不注入的话 A5 就**永远只会绿**，等于没有判据（见 `--self-test` 的 Q21）。

    🚨 **剥注释必须在判据函数内部**，不能只在 `main()` 里做 ——
    否则自检传进来的是**原始**文本，注释里的字面量永远测不到。
    实测（本门禁第一版就是错的）：Q18 会**假红**（注释里写着「不能改成 `min-h-dvh`」，
    被当成真的出现了 `min-h-dvh`），而 Q19/Q20 只是**碰巧**绿 ——
    于是「剥注释真的接在主流程上」这件事**没有任何臂在守**。
    这正是坑 14 那条纪律：`strip_comments()` 必须在**判据函数内部**。
    """
    exists = exists or (lambda rel: (ROOT / rel).exists())
    layout_src = strip_comments(layout_src)
    tabbar_src = strip_comments(tabbar_src)
    defects: list[str] = []
    reports: list[str] = []

    # ── A0 守卫 ──
    try:
        n, names, hrefs = read_expected(spec_text)
    except SpecUnreadable as exc:
        return 2, [f"【环境】{exc}"], []

    reports.append(f"规范现读：{n} 项 Tab = {' / '.join(names)}")

    # ── A1 TABS ──
    state, entries = parse_tabs(layout_src)
    if state == "absent":
        # 产品缺陷：TabBar 根本没接上（不是「我没测成」）
        defects.append("`layout.tsx` 里没有 `TABS` —— TabBar 没有接上（§8.4 要求 4 项 Tab）")
        entries = []
    elif state == "unparsable":
        return 2, [
            "【环境】`TABS` 数组存在但条目解析不出来 ⇒ 判据无法运行"
            "（先修本门禁的正则，别把它当产品缺陷）"
        ], reports
    else:
        if len(entries) != n:
            defects.append(
                f"`TABS` 有 {len(entries)} 项，规范 §8.4 要求 {n} 项"
                f"（实际：{[e[1] for e in entries]}）"
            )
        got_names = [e[1] for e in entries]
        if got_names != names:
            defects.append(f"Tab 名字/顺序不符：规范 {' / '.join(names)}，实际 {' / '.join(got_names)}")
        root_id_m = ROOT_ID_RE.search(layout_src)
        root_id = root_id_m.group(1) if root_id_m else None
        if root_id is None:
            defects.append("`layout.tsx` 里找不到 `const ROOT_ID = \"…\"`（§4 的激活态依赖它）")
        for tid, label, href in entries:
            want = hrefs.get(label)
            if want is None:
                defects.append(f"Tab「{label}」在规范 §4 表里没有对应路由")
                continue
            if href != want:
                defects.append(f"Tab「{label}」的 href 是 {href!r}，规范 §4 要求 {want!r}")
            seg = href.strip("/").split("/")[0] if href.strip("/") else root_id
            if seg is not None and tid != seg:
                defects.append(
                    f"Tab「{label}」的 id={tid!r} != 一级路径段 {seg!r} ⇒ 激活态会**静默失效**（§4）"
                )

    # ── A2 §6.1 TabBar 自带 lg:hidden ──
    if "lg:hidden" not in tabbar_src:
        defects.append(
            "`TabBar.tsx` 丢了 `lg:hidden` —— §6.1 的前提是「组件自带 `lg:hidden`，"
            "im 不需要额外判断」；去掉后**三端桌面端**都会多出一条底栏"
        )

    # ── A3 §6.2 安全区不双算（静态形态）──
    tag = root_open_tag(layout_src)
    if tag is None:
        return 2, [
            "【环境】找不到根容器的 `style={{…--safe-top…}}` ⇒ 无法定位根容器"
        ], reports
    if "paddingBottom" in tag:
        defects.append(
            "根容器的 `style` 里出现了 `paddingBottom` —— `TabBar` 内部已经加过 "
            "`var(--safe-bottom)`，再加一次就是**双算**（§6.2 明写「必须处理」）"
        )
    if "safe-bottom" in tag:
        defects.append("根容器的 `style` 里出现了 `safe-bottom` —— 同上，属双算（§6.2）")
    if "pb-[var(--actionbar-bottom)]" not in layout_src:
        defects.append(
            "内容区丢了 `pb-[var(--actionbar-bottom)]` —— 移动端必须按 "
            "`tabbar-h + safe-bottom` 让位（§6.2）"
        )
    if "lg:pb-[var(--safe-bottom)]" not in layout_src:
        defects.append(
            "内容区丢了 `lg:pb-[var(--safe-bottom)]` —— 桌面端 TabBar 被隐藏，"
            "只需让安全区（§6.2）"
        )

    # ── A4 §6.3 高度与滚动 ──
    if "h-dvh" not in tag:
        defects.append("根容器没有 `h-dvh`（§6.3：页面级高度不变）")
    if "overflow-hidden" not in tag:
        defects.append("根容器没有 `overflow-hidden`（§6.3：三栏各自滚动、页面本身不滚动）")
    if "min-h-dvh" in layout_src:
        defects.append(
            "出现 `min-h-dvh` —— §6.3 明写「**不能**改成 `min-h-dvh` 让页面滚动」，"
            "否则丢掉「三栏各自滚动」这个核心体验"
        )

    # ── A5 §4 四个落地页 ──
    for sec, rel in PAGES.items():
        if not exists(rel):
            defects.append(f"§{sec} 的落地页不存在：`{rel}`（Tab 会指向 404）")

    # ── B 组只报 ──
    for sec, rel in PAGES.items():
        p = ROOT / rel
        reports.append(
            f"  §{sec}  {rel}  "
            + (f"{len(p.read_text(encoding='utf-8').splitlines())} 行" if p.exists() else "**缺失**")
        )

    return (1 if defects else 0), defects, reports


# ── 自检（合成夹具；全部**不碰**产品文件）────────────────────────────────
SYN_SPEC = """\
| §8.4 | **客户端 4 项 Tab：工作台 / 咨询 / 我的案件 / 我的**；Tab Bar 不超过 5 项 | 已定 |

## 4. 路由映射方案（建议）

| # | Tab | 路由 | 落地页 | 状态 |
|---|---|---|---|---|
| 1 | 工作台 | `/` | **新建** | 待建 |
| 2 | 咨询 | `/chat` | **搬迁** | 待搬 |
| 3 | 我的案件 | `/cases` | **新建** | 待建 |
| 4 | 我的 | `/me` | **新建** | 待建 |

## 5. 四个页面的内容

正文。
"""

#: 复刻**真实形状**（多行对象；见坑 41：夹具的形状决定它能逮到哪种回归）
SYN_LAYOUT = """\
const TABS: TabBarItem[] = [
  { id: "home", label: "工作台", href: "/" },
  { id: "chat", label: "咨询", href: "/chat" },
  { id: "cases", label: "我的案件", href: "/cases" },
  { id: "me", label: "我的", href: "/me" },
];

const ROOT_ID = "home";

export default function L() {
  return (
    <div
      className="flex h-dvh flex-col overflow-hidden bg-surface"
      style={{
        paddingTop: "var(--safe-top)",
        paddingLeft: "var(--safe-left)",
        paddingRight: "var(--safe-right)",
      }}
    >
      <div className="min-h-0 flex-1 pb-[var(--actionbar-bottom)] lg:pb-[var(--safe-bottom)]">
        {children}
      </div>
      <TabBar items={TABS} />
    </div>
  );
}
"""

SYN_TABBAR = """\
<nav className="fixed inset-x-0 bottom-0 z-tabbar lg:hidden" style={{ paddingBottom: "var(--safe-bottom)" }} aria-label="主导航" />
"""


def _with(layout: str = SYN_LAYOUT, tabbar: str = SYN_TABBAR, spec: str = SYN_SPEC):
    return evaluate(spec, layout, tabbar, exists=lambda rel: True)


def self_test() -> int:
    arms: list[tuple[str, bool, int, str]] = []  # (名, 是否应通过, 期望 rc, 说明)

    def add(name: str, ok: bool, rc: int, note: str = "") -> None:
        arms.append((name, ok, rc, note))

    def rc_of(res: tuple[int, list[str], list[str]]) -> int:
        return res[0]

    # Q1–Q3 守卫
    add("Q1 规范读不出「N 项 Tab」⇒ exit 2", rc_of(_with(spec="没有这句话\n")) == 2, 2)
    add("Q2 §4 表读不出 ⇒ exit 2", rc_of(_with(spec=SYN_SPEC.split("## 4.")[0] + "\n## 4. 路由映射方案\n\n（无表）\n## 5.\n")) == 2, 2)
    add("Q3 TABS 数组在、条目解析不出 ⇒ exit 2", rc_of(_with(layout=SYN_LAYOUT.replace(
        '  { id: "home", label: "工作台", href: "/" },\n'
        '  { id: "chat", label: "咨询", href: "/chat" },\n'
        '  { id: "cases", label: "我的案件", href: "/cases" },\n'
        '  { id: "me", label: "我的", href: "/me" },\n', "  ...spread,\n"))) == 2, 2)
    add("Q4 TABS 整个不存在 ⇒ exit 1（产品缺陷）", rc_of(_with(layout=SYN_LAYOUT.replace("TABS", "ITEMS"))) == 1, 1)
    add("Q5 根容器 style 定位不到 ⇒ exit 2", rc_of(_with(layout=SYN_LAYOUT.replace("--safe-top", "--safe-xx"))) == 2, 2)

    # Q6 全绿对照
    add("Q6 真实形状的合成夹具 ⇒ exit 0", rc_of(_with()) == 0, 0)

    # Q7–Q10 A1
    add("Q7 少一项 Tab ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace('  { id: "me", label: "我的", href: "/me" },\n', ""))) == 1, 1)
    add("Q8 顺序颠倒 ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace('  { id: "chat", label: "咨询", href: "/chat" },\n  { id: "cases", label: "我的案件", href: "/cases" },', '  { id: "cases", label: "我的案件", href: "/cases" },\n  { id: "chat", label: "咨询", href: "/chat" },'))) == 1, 1)
    add("Q9 href 与 §4 表不符 ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace('href: "/cases"', 'href: "/my-cases"'))) == 1, 1)
    add("Q10 id != 一级路径段 ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace('id: "cases"', 'id: "case_list"'))) == 1, 1)

    # Q11–Q14 A2/A3
    add("Q11 TabBar 丢 lg:hidden ⇒ exit 1", rc_of(_with(tabbar=SYN_TABBAR.replace(" lg:hidden", ""))) == 1, 1)
    add("Q12 根容器加 paddingBottom ⇒ exit 1（双算）", rc_of(_with(layout=SYN_LAYOUT.replace('        paddingRight: "var(--safe-right)",', '        paddingRight: "var(--safe-right)",\n        paddingBottom: "var(--safe-bottom)",'))) == 1, 1)
    add("Q13 内容区丢 actionbar 令牌 ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace("pb-[var(--actionbar-bottom)] ", ""))) == 1, 1)
    add("Q14 内容区丢桌面回退 ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace(" lg:pb-[var(--safe-bottom)]", ""))) == 1, 1)

    # Q15–Q17 A4
    add("Q15 根容器换 min-h-dvh ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace('className="flex h-dvh flex-col overflow-hidden bg-surface"', 'className="flex min-h-dvh flex-col bg-surface"'))) == 1, 1)
    add("Q16 根容器丢 overflow-hidden ⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace("overflow-hidden ", ""))) == 1, 1)
    add("Q17 只有占位分支有 h-dvh（根容器没有）⇒ exit 1", rc_of(_with(layout=SYN_LAYOUT.replace('className="flex h-dvh flex-col overflow-hidden bg-surface"', 'className="flex flex-col overflow-hidden bg-surface"').replace("const ROOT_ID", 'const SPINNER = <div className="flex h-dvh" />;\nconst ROOT_ID'))) == 1, 1)

    # Q18–Q20 对照臂：注释里的字面量**不得**造成假红
    add("Q18 注释写「不能改成 min-h-dvh」⇒ 仍 exit 0", rc_of(_with(layout=SYN_LAYOUT.replace("const ROOT_ID", "// §6.3：不能改成 min-h-dvh 让页面滚动\nconst ROOT_ID"))) == 0, 0)
    add("Q19 注释写「根容器不得加 paddingBottom: var(--safe-bottom)」⇒ 仍 exit 0", rc_of(_with(layout=SYN_LAYOUT.replace("const ROOT_ID", "// ⚠️ 根容器不得加 paddingBottom: var(--safe-bottom)\nconst ROOT_ID"))) == 0, 0)
    add("Q20 块注释里写 --safe-top 不得被当成根容器 ⇒ 仍 exit 0", rc_of(_with(layout=SYN_LAYOUT.replace("const ROOT_ID", "/**\n * 说明：style={{ paddingBottom: 'var(--safe-bottom)' }} 是错的\n * 锚点 --safe-top 在真容器上\n */\nconst ROOT_ID"))) == 0, 0)

    # Q21 A5 注入臂（真仓库四个页面都在 ⇒ 不注入就永远只会绿）
    def _no_pages(rel: str) -> bool:
        return False

    res21 = evaluate(SYN_SPEC, SYN_LAYOUT, SYN_TABBAR, exists=_no_pages)
    add("Q21 四个落地页都不存在 ⇒ exit 1 且 4 条缺陷",
        res21[0] == 1 and len([d for d in res21[1] if "落地页不存在" in d]) == 4, 1)

    # Q22 对照：exists 恒真时**不得**报落地页缺陷
    add("Q22 exists 恒真 ⇒ 无落地页缺陷", not [d for d in _with()[1] if "落地页不存在" in d], 0)

    failed = [a for a in arms if not a[1]]
    for name, ok, _rc, _note in arms:
        print(f"  {'✓' if ok else '✗'} {name}")
    print()
    print("=" * 82)
    if failed:
        print(f"自检：{len(arms) - len(failed)}/{len(arms)} 通过 —— **有 {len(failed)} 条臂红了**")
        print("=" * 82)
        return 2
    print(f"自检：{len(arms)}/{len(arms)} 通过")
    print("=" * 82)
    return 0


WHY: dict[str, str] = {
    "6.1": "TabBar 接入方式：组件自带 `lg:hidden` ⇒ 桌面端自动不渲染，不需要额外判断。\n"
           "      出处：`im-mobile-nav-spec.md` §6.1；被 A2 判（`TabBar.tsx` 必须仍带 `lg:hidden`）。",
    "6.2": "⚠️ 安全区会被**双重计算**（必须处理）：`TabBar` 内部已加 `paddingBottom: var(--safe-bottom)`，\n"
           "      根容器**不得**再加；改由内容区按断点让位。\n"
           "      出处：`im-mobile-nav-spec.md` §6.2；被 A3 判（静态）+ `verify_im_safe_area.py` 判（渲染级）。",
    "6.3": "高度与滚动：页面级高度不变，**不能**改成 `min-h-dvh` 让页面滚动。\n"
           "      出处：`im-mobile-nav-spec.md` §6.3；被 A4 判（此前**无任何判据**）。",
    "8.4": "客户端 4 项 Tab：工作台 / 咨询 / 我的案件 / 我的。\n"
           "      出处：`design-spec.md` §8.4（`im-mobile-nav-spec.md` 的「规范依据」表里引用）；被 A1 判。\n"
           "      ⚠️ **本段（帮助文案）里的节号只写「本门禁真正在判的节」** —— 覆盖率报表把"
           "**判据代码体里的字符串字面量**算作覆盖（报错文案写「某节要求…」是正当覆盖），"
           "所以散文里的**交叉引用**会被记成**对那份文档的覆盖** ⇒ **假覆盖**。"
           "实测：第一版这句写着「（本稿的第 1 节引用）」，报表立刻把 `design-spec.md` 的"
           "**第 1 节**记成已覆盖（少了一个真候选）。⇒ **写 `--why` / 报错文案时，"
           "只写你真正在判的节号**；指别人的节要用文字描述，别写符号。",
    "4": "路由映射方案：`tab id` 必须等于一级路径段（激活态按 `pathname` 首段推）。\n"
         "    出处：`im-mobile-nav-spec.md` §4；被 A1 判。",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="im 移动端导航静态不变量门禁（§6.1/§6.2/§6.3）")
    ap.add_argument("--self-test", action="store_true", help="跑合成夹具自检")
    ap.add_argument("--why", metavar="SEC", help="打印某节的出处与归属")
    args = ap.parse_args(argv)

    if args.why:
        key = args.why.strip().lstrip("§")
        if key in WHY:
            print(f"§{key} —— {WHY[key]}")
            return 0
        print(f"未知节号 §{key}；已知：{', '.join('§' + k for k in WHY)}")
        return 2

    if args.self_test:
        return self_test()

    spec_path = ROOT / SPEC_REL
    if not spec_path.exists():
        print(f"✗ 规范不存在：{SPEC_REL} —— 环境问题")
        return 2
    spec_text = spec_path.read_text(encoding="utf-8")

    print("── im 移动端导航静态不变量（`im-mobile-nav-spec.md` §6.1/§6.2/§6.3）──")

    missing_product = [rel for rel in (LAYOUT_REL, TABBAR_REL) if not (ROOT / rel).exists()]
    if missing_product:
        for rel in missing_product:
            print(f"  ✗ 源码文件不存在：`{rel}`")
        print("\n✗ 产品缺陷：im 的外壳 / TabBar 组件缺失。")
        return 1

    # ⚠️ **不在这里剥注释**：`evaluate()` 内部自己剥 ——
    # 否则自检传的是原始文本，「剥注释」这一步就没有任何臂在守（见 evaluate 的 docstring）。
    layout_src = (ROOT / LAYOUT_REL).read_text(encoding="utf-8")
    tabbar_src = (ROOT / TABBAR_REL).read_text(encoding="utf-8")

    rc, defects, reports = evaluate(spec_text, layout_src, tabbar_src)
    for line in reports:
        print(f"  {line}" if not line.startswith("  ") else line)

    print()
    if rc == 2:
        for d in defects:
            print(f"  {d}")
        print("\n⚠️ 环境问题：判据没能运行 —— **不是产品缺陷**。")
        return 2

    if defects:
        print(f"✗ 发现 {len(defects)} 条缺陷：")
        for d in defects:
            print(f"    · {d}")
        return 1

    print("✓ §6.1 / §6.2 / §6.3 静态不变量全部成立，四个落地页都在。")
    print("  ⚠️ 本门禁**不替代**渲染级探针：几何量（34px 空白带 / 三栏宽度）只有真浏览器有值。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
