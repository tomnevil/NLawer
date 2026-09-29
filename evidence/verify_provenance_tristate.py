#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§5「责任边界三态系统」门禁 —— 三态的**五条使用规则**逐条判据。

## 为什么要有这一条

`grep -oE '§[0-9]+(\\.[0-9]+)?' evidence/verify_*.py` 做「门禁 × 规范小节」矩阵后，
`design-spec.md` §5 是**唯一「整节已实现、却近乎零判据」**的小节：

- 三态组件**全建了**（`ProvenanceBadge` / `ProvenanceBlock` / `ProvenanceLegend`），
  产品页有 8 处徽章、2 处图例 —— **能力是真的**；
- 但 §5 的**五条使用规则**（穷尽性 / 全局图例 / 状态可迁移 / 导出保留 / 三重编码）
  **一条判据都没有**。`verify_component_wiring.py` 只在注释里提过 §5，
  它判的是「**组件被用了吗**」——那是 §6.2 的接线问题，**不是 §5 的规则**。

⇒ 这正是「**已实现 ≠ 有判据**」的第 N 次复发（后端侧同一模式已收口 8 条边界）。

## 本门禁**不**判什么（与 `verify_component_wiring.py` 的分工）

| 问题 | 归谁 |
|---|---|
| `ProvenanceBadge` / `ProvenanceLegend` 有没有被引用 | `verify_component_wiring.py`（§6.2） |
| `ProvenanceBlock` 没被用算不算缺口 | `verify_component_wiring.py`（`INLINE_OK` 等价实现） |
| **规范点名的三页是否都挂了图例** | **本门禁 F2**（§5 规则 2） |
| **三重编码是否完整** | **本门禁 F1**（§5 规则 5） |
| **三态是否只有单一真源** | **本门禁 F3**（§5 规则 1） |
| **徽章是否可点击查看流转记录** | **本门禁 F4**（§5 规则 3） |

⚠️ **F2 是接线门禁的结构性盲区**：它数 `<ProvenanceLegend` 的命中数，
问答页挂了 ⇒ 计数 > 0 ⇒ **绿**。「**文书页没挂**」这个事实在它的判据里**不可见**。
⇒ **「组件被用」与「组件被用在规范要求的地方」是两个问题**，后者的期望值只能来自 §5。

## 四条纪律（否则判据会造假红 / 假绿）

1. **期望值一律从 `design-spec.md` §5 解析，不硬编码。** 规则条数、规则文本、
   规则 2 点名的页面清单全部现读。规范改一个字，门禁跟着变（F0 守卫）。
2. **「关键词命中 ≠ 有判据」，反向也成立：关键词命中 ≠ 是同一件事。**
   全仓 `"ai"` 有 **30 处**命中，但绝大多数是**同词不同轴**：
   `Badge variant="ai"`（视觉色调）、`KpiTone = ... | "ai"`（KPI 色调）、
   `role: "user" | "ai"`（会话角色）、`tone: "ai"`（IM 接待态）。
   ⇒ F3 **只认「字符串字面量联合的取值集合恰好等于三态集合」**，不做关键词匹配。
   自检 **Q10/Q11** 专门锁这两条假阳性。
3. **门禁自己的穷尽性也要守。** F2 依赖「页面名 → 文件」映射；若规范点名了第 4 页而
   映射表没跟上，**必须红**（F0），否则新页会静默漏判。自检 **Q6** 锁这条。
4. **棘轮**：F2/F3/F4 当前都有**已登记**的欠账（任务 #45/#46/#47）。按本仓惯例，
   门禁**第一天必须是绿的**（长期红的门禁两天内必被注释掉），但棘轮有**两条相反的不等式**：
   · **不许涨**：`KNOWN_GAPS` 之外出现新欠账 ⇒ 红；
   · **不许赖**：`KNOWN_GAPS` 里的条目变成通过 ⇒ 红（提示删豁免）。

## 退出码

- `0` 通过（允许出现已登记的已知缺口）
- `1` 发现**新的**规则违反，或棘轮豁免已过期
- `2` 环境问题（规范 / 组件文件找不到，或自检失败 ⇒ **工具坏了，不是产品坏了**）

用法：

    python evidence/verify_provenance_tristate.py
    python evidence/verify_provenance_tristate.py --self-test
    python evidence/verify_provenance_tristate.py --why
    python evidence/verify_provenance_tristate.py --dump
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"
SPEC = ROOT / "deliverables" / "ui-design" / "design-spec.md"

COLORS_TS = FRONTEND / "packages" / "ui" / "src" / "theme" / "colors.ts"
BADGE_TSX = FRONTEND / "packages" / "ui" / "src" / "components" / "ProvenanceBadge.tsx"

# ⚠️ **仓库相对**键（与 `iter_sources()` 同约定）。只在这里定义一次——
# 第一版在 `main()` 里另写了一串 `packages/ui/...`，与 `iter_sources()` 的
# `frontend/packages/ui/...` 不匹配 ⇒ `files.get()` 返回空串 ⇒
# F1 报「三态 0 个」**假红**、R3 整段**静默消失**。自检抓不到（自检只测判据函数，
# 不测 `main()` 的取文件路径）⇒ 另加 `REQUIRED_FILES` 守卫见 `main()`。
COLORS_REL = "frontend/packages/ui/src/theme/colors.ts"
BADGE_REL = "frontend/packages/ui/src/components/ProvenanceBadge.tsx"

# ---------------------------------------------------------------------------
# 规范点名的页面 → 文件映射
#
# ⚠️ 键必须是 §5 规则 2 正文里出现的**页面名原文**（F0 会拿解析结果对账）。
# ⚠️ 值必须是仓库相对路径，且**要能指到「为什么是这一条路由」**。
# ---------------------------------------------------------------------------
PAGE_MAP: dict[str, str] = {
    "问答页": "frontend/apps/web/app/(app)/qa/page.tsx",
    "文书页": "frontend/apps/web/app/(app)/documents/page.tsx",
    "案件详情页": "frontend/apps/lawyer/app/(app)/cases/[id]/page.tsx",
}
PAGE_MAP_WHY: dict[str, str] = {
    "问答页": "`apps/web` 唯一带问答语义的一级路由（`智能问答` 标题，四段式输出）。",
    "文书页": "**排除法**：`find apps/*/app -name page.tsx` 全仓只有 "
              "`apps/web/app/(app)/documents/page.tsx` 一条文书路由；"
              "`lawyer/archives` 是「归档」不是「文书」，`lawyer/dispatches` 是「派单」。"
              "该页第三步「生成结果」确实渲染了 `<ProvenanceBadge state=\"ai\" />`（:400）"
              "⇒ 它是 §5 意义上的文书页，只是**缺图例**。",
    "案件详情页": "`apps/lawyer` 唯一带 `[id]` 的案件详情路由；"
                  "`phase3-implementation.md` 决策 6 讨论的就是这一页的徽章粒度。",
}

# ---------------------------------------------------------------------------
# 棘轮：已登记、不阻断 CI，但**不许涨**、**修好必须删**。
#
# ⚠️ 收录标准：**只收已登记、有任务编号的**。禁止为了过门禁往里塞。
# ---------------------------------------------------------------------------
KNOWN_GAPS: dict[str, str] = {
    "F2:文书页": "任务 **#45**。§5 规则 2 点名三页，文书页 `<ProvenanceLegend` **0 处**"
                 "（而 `<ProvenanceBadge` **1 处**，:400）⇒ 缺全局图例。"
                 "待用户拍板接法（顶栏常驻 vs 结果卡片头部）。",
    "F3:frontend/apps/web/app/(app)/contract-review/types.ts":
        "任务 **#46**。`BannerSpec.provenance` **手抄**了三态联合（:140）而没 import "
        "`ProvenanceState`。今天两者一致 ⇒ **潜伏**风险，不是线上缺陷；"
        "但若 `provenanceStates` 新增第 4 态，手抄副本**不会跟着变且 TS 不报错**"
        "（窄联合仍可赋给宽联合）⇒ 规则 1「穷尽性」静默失效。改法是一行 import。",
    "F4:产品页": "任务 **#47**。§5 规则 3 要求「徽章可点击查看流转记录」，"
                 "`onOpenHistory` 在两个组件里**都实现了**，但产品页 **0 处传入** ⇒ "
                 "8 处徽章**没有一处可点击**。⚠️ **依赖后端**：流转记录要真数据，"
                 "而 §5 决策 08 的 provenance 数据模型后端**尚未落库**（见 `backend.md`）。",
}


# ===========================================================================
# 解析：规范
# ===========================================================================
def load_spec_section(text: str, heading: str) -> str | None:
    """取 `## 05 责任边界三态系统` 到下一个 `## ` 之间的正文。"""
    m = re.search(r"^##\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    nxt = re.search(r"^##\s+", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


RULE_RE = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*——\s*(.+?)\s*$", re.M)


def parse_rules(section: str) -> list[tuple[int, str, str]]:
    """解析 §5 的「使用规则」清单 → [(编号, 规则名, 正文)]。"""
    return [(int(a), b, c) for a, b, c in RULE_RE.findall(section)]


def parse_named_pages(rule2_body: str) -> list[str]:
    """从规则 2 正文里取出点名的页面清单（「问答页、文书页、案件详情页顶栏常驻…」）。

    ⚠️ 只认「页」结尾的名词，且**必须在「顶栏」之前**——避免把后文的
    「移动端」「问题标题下方」也算成页面。
    """
    head = rule2_body.split("顶栏")[0]
    return re.findall(r"([\u4e00-\u9fa5]{2,8}页)", head)


# ===========================================================================
# 解析：组件源码
# ===========================================================================
def _block_after(text: str, opener: str) -> str | None:
    """取 `opener` 之后第一个 `{` 到配平 `}` 之间的内容。"""
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


STATE_KEY_RE = re.compile(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{", re.M)


def parse_provenance_states(text: str) -> dict[str, dict[str, str]]:
    """解析 `export const provenanceStates = { ... } as const` → {state: {label, icon}}。"""
    body = _block_after(text, "export const provenanceStates")
    if body is None:
        return {}
    out: dict[str, dict[str, str]] = {}
    keys = list(STATE_KEY_RE.finditer(body))
    for idx, m in enumerate(keys):
        end = keys[idx + 1].start() if idx + 1 < len(keys) else len(body)
        chunk = body[m.start():end]
        label = re.search(r'label:\s*"([^"]*)"', chunk)
        icon = re.search(r'icon:\s*"([^"]*)"', chunk)
        out[m.group(1)] = {
            "label": label.group(1) if label else "",
            "icon": icon.group(1) if icon else "",
        }
    return out


def parse_record_keys(text: str, decl: str) -> list[str]:
    """解析 `const <decl>: Record<...> = { a: ..., b: ... }` 的键。"""
    body = _block_after(text, decl)
    if body is None:
        return []
    return re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", body, re.M)


# ===========================================================================
# 扫描：前端源码
# ===========================================================================
SKIP_DIRS = {"node_modules", ".next", ".next_old_v14_keep", "_prev_build",
             "dist", "build", ".turbo", "__pycache__"}


def iter_sources() -> list[tuple[str, str]]:
    """收集产品页源码（`frontend/apps/**`，**排除** `components-preview`）+ 组件库内部。

    🚨 **必须用 `os.walk` 就地剪枝，不能用 `Path.rglob`**：`rglob` 会把
    `node_modules`（四端各一份）**整个遍历完**，再让调用方去过滤 ⇒ 实测 **64.6s**。
    改 `os.walk` + `dirnames[:]` 就地剪枝后降到 **~1s**。
    （同 `verify_font_stack.py` 的教训：那边 `rglob` 67.7s → `os.walk` 1.4s，
    结果逐字节一致。）
    ⚠️ **排序**：`os.walk` 的产出顺序依赖文件系统 ⇒ 末尾统一 `sorted`，
    否则两次运行可能顺序不同（本仓要求**确定性**）。
    """
    out: list[tuple[str, str]] = []
    roots = [FRONTEND / "apps", FRONTEND / "packages" / "ui" / "src"]
    for r in roots:
        if not r.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            # 就地剪枝：改 `dirnames` **本身**，`os.walk` 便不再下探这些目录。
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not fn.endswith((".ts", ".tsx")):
                    continue
                p = pathlib.Path(dirpath) / fn
                # ⚠️ 一律用**仓库相对**路径（`frontend/apps/...`）：`PAGE_MAP` 与
                # 人类可读输出都是这个约定。若这里用 `relative_to(FRONTEND)`，
                # 键会变成 `apps/...` 而 `PAGE_MAP` 是 `frontend/apps/...` ⇒
                # `files.get(rel)` **永远取不到** ⇒ F2 把三页全报成「缺图例」（假红）。
                rel = p.relative_to(ROOT).as_posix()
                if "components-preview" in rel:
                    continue
                try:
                    out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    continue
    out.sort(key=lambda kv: kv[0])
    return out


def is_product(rel: str) -> bool:
    """产品页 = `frontend/apps/**`（`components-preview` 已在 `iter_sources` 排除）。"""
    return rel.startswith("frontend/apps/")


# 一条「恰好等于三态集合」的字符串字面量联合。
UNION_RE = re.compile(
    r'(?P<union>"[A-Za-z_][A-Za-z0-9_]*"(?:\s*\|\s*"[A-Za-z_][A-Za-z0-9_]*")+)'
)
LIT_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')


def find_handcopied_unions(
    files: list[tuple[str, str]], canonical: set[str]
) -> list[tuple[str, int, str]]:
    """找「取值集合恰好等于三态集合」的**类型级**字面量联合。

    ⚠️ **结构式**，不是关键词式：只认 `"a" | "b" | "c"` 这种**联合语法**。
    这样 `Badge variant="ai"`（单个字面量，无 `|`）、`role: "user" | "ai"`
    （集合 ≠ 三态）、`const ALL: ProvenanceState[] = ["ai", ...]`
    （数组字面量，逗号分隔，**不是** `|` 联合）都不会被误伤。
    """
    hits: list[tuple[str, int, str]] = []
    for rel, text in files:
        if rel.endswith("theme/colors.ts"):
            continue  # 规范定义处自己，不算手抄
        for m in UNION_RE.finditer(text):
            lits = set(LIT_RE.findall(m.group("union")))
            if lits != canonical:
                continue
            line_no = text.count("\n", 0, m.start()) + 1
            line = text.splitlines()[line_no - 1] if line_no <= len(text.splitlines()) else ""
            if "ProvenanceState" in line:
                continue  # 已用规范类型标注 ⇒ 不是手抄
            hits.append((rel, line_no, line.strip()))
    return hits


BADGE_TAG_RE = re.compile(r"<Provenance(?:Badge|Block)\b[^>]*?/?>", re.S)


def count_history_wiring(files: list[tuple[str, str]]) -> tuple[int, int]:
    """统计产品页里「传了 `onOpenHistory`」的徽章数 / 徽章总数。"""
    wired = total = 0
    for rel, text in files:
        if not is_product(rel):
            continue
        for m in BADGE_TAG_RE.finditer(text):
            total += 1
            if "onOpenHistory" in m.group(0):
                wired += 1
    return wired, total


def count_state_literals(files: list[tuple[str, str]]) -> dict[str, int]:
    """产品页里 `state="<值>"` 的字面量分布（用于 R2 可达性报告）。"""
    out: dict[str, int] = {}
    for rel, text in files:
        if not is_product(rel):
            continue
        for v in re.findall(r'state="([a-z_]+)"', text):
            out[v] = out.get(v, 0) + 1
    return out


# ===========================================================================
# 判据
# ===========================================================================
class Report:
    def __init__(self) -> None:
        self.fail: list[tuple[str, str]] = []       # (判据 id, 说明)
        self.notes: list[str] = []                  # 只报不判
        self.ok: list[str] = []

    def add_fail(self, key: str, msg: str) -> None:
        self.fail.append((key, msg))

    def add_ok(self, msg: str) -> None:
        self.ok.append(msg)

    def add_note(self, msg: str) -> None:
        self.notes.append(msg)


def split_ratchet(
    fail: list[tuple[str, str]], known: dict[str, str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[str]]:
    """棘轮的**两条相反的不等式**（抽成纯函数 ⇒ 可自检，否则这一段不可证伪）。

    · **不许涨**：`known` **之外**的失败 ⇒ `new_hits` ⇒ 阻断（拦住新漂移）
    · **不许赖**：`known` 里的条目**不再失败** ⇒ `expired` ⇒ 也阻断（提示删豁免）
      这一条是刻意的：删一行只需 5 秒，但「修好了却永远留在豁免表里」会静默
      关掉这道防线。它不是「修好反而报错」，是「豁免必须跟着事实走」。

    自检 **Q14/Q15/Q16** 锁三条分支。
    """
    known_hits = [(k, m) for k, m in fail if k in known]
    new_hits = [(k, m) for k, m in fail if k not in known]
    expired = [k for k in known if not any(k == fk for fk, _ in fail)]
    return known_hits, new_hits, expired


def judge_f1(states: dict[str, dict[str, str]], badge_src: str, rep: Report) -> None:
    """§5 规则 5：颜色 + 文字 + 图标 三重编码。"""
    if len(states) != 3:
        rep.add_fail("F1:三态数量", f"`provenanceStates` 应有 3 态，实测 {len(states)}：{sorted(states)}")
        return
    labels, icons = [], []
    for k, v in sorted(states.items()):
        if not v["label"]:
            rep.add_fail("F1:文字", f"态 `{k}` 缺 `label`（三重编码的「文字」通道）")
        if not v["icon"]:
            rep.add_fail("F1:图标", f"态 `{k}` 缺 `icon`（三重编码的「图标」通道）")
        labels.append(v["label"])
        icons.append(v["icon"])
    if len(set(labels)) != len(labels):
        rep.add_fail("F1:文字可辨", f"三态 `label` 有重复 ⇒ 文字通道无法区分：{labels}")
    if len(set(icons)) != len(icons):
        rep.add_fail("F1:图标可辨", f"三态 `icon` 有重复 ⇒ 图标通道无法区分：{icons}")

    # 颜色通道：Badge 的 stateStyles 必须覆盖同一组键，且三串样式互不相同。
    style_keys = parse_record_keys(badge_src, "const stateStyles")
    if set(style_keys) != set(states):
        rep.add_fail("F1:颜色键",
                     f"`ProvenanceBadge.stateStyles` 的键 {sorted(style_keys)} "
                     f"≠ `provenanceStates` 的键 {sorted(states)}")
    else:
        body = _block_after(badge_src, "const stateStyles") or ""
        vals = re.findall(r'^\s*[A-Za-z_][A-Za-z0-9_]*\s*:\s*"([^"]+)"', body, re.M)
        if len(set(vals)) != len(vals):
            rep.add_fail("F1:颜色可辨", f"三态样式串有重复 ⇒ 颜色通道无法区分：{vals}")

    # 渲染结构：Badge 必须真的把 icon 与 label 放进 DOM。
    for token, why in (("{meta.icon}", "图标"), ("{meta.label}", "文字")):
        if token not in badge_src:
            rep.add_fail("F1:渲染", f"`ProvenanceBadge` 未渲染 {token}（{why}通道只存在于数据层）")

    if not rep.fail:
        rep.add_ok("F1 §5 规则 5 三重编码：3 态 × (label+icon+color) 齐全且互不相同")


def judge_f2(named: list[str], files: dict[str, str], rep: Report) -> None:
    """§5 规则 2：规范点名的每页都必须挂全局图例。"""
    for page in named:
        rel = PAGE_MAP.get(page)
        if rel is None:
            continue  # F0 已单独报
        text = files.get(rel, "")
        if "<ProvenanceLegend" in text:
            rep.add_ok(f"F2 图例：{page}（{rel}）已挂 `ProvenanceLegend`")
        else:
            rep.add_fail(f"F2:{page}",
                         f"§5 规则 2 点名「{page}」顶栏常驻三态图例，"
                         f"但 `{rel}` 里 `<ProvenanceLegend` **0 处**"
                         f"（该页徽章 {text.count('<ProvenanceBadge')} 处 ⇒ 是文书页缺图例，不是没内容）")


def judge_f3(files: list[tuple[str, str]], canonical: set[str], rep: Report) -> None:
    """§5 规则 1：穷尽性 ⇒ 三态必须只有单一真源。"""
    hits = find_handcopied_unions(files, canonical)
    for rel, line_no, line in hits:
        rep.add_fail(f"F3:{rel}",
                     f"三态联合**手抄**在 `{rel}:{line_no}` 而没 import `ProvenanceState`："
                     f"`{line}` ⇒ 新增第 4 态时此处静默不跟随（窄联合仍可赋给宽联合，TS 不报错）")
    if not hits:
        rep.add_ok("F3 §5 规则 1 穷尽性：三态无手抄副本（`ProvenanceState` 是唯一真源）")


def judge_f4(files: list[tuple[str, str]], rep: Report) -> None:
    """§5 规则 3：徽章可点击查看流转记录（留痕）。"""
    wired, total = count_history_wiring(files)
    if wired > 0:
        rep.add_ok(f"F4 §5 规则 3 可点击留痕：{wired}/{total} 处徽章接了 `onOpenHistory`")
    else:
        rep.add_fail("F4:产品页",
                     f"§5 规则 3 要求「徽章可点击查看流转记录」，"
                     f"但产品页 {total} 处徽章**无一处**传入 `onOpenHistory`"
                     f"（组件里两个 `onOpenHistory` 参数都实现了，只是没人接线）")


def judge_f0(named: list[str], rules: list[tuple[int, str, str]], rep: Report) -> None:
    """门禁自己的穷尽性守卫：规范点名的页必须全部有映射。"""
    if len(rules) != 5:
        rep.add_fail("F0:规则条数",
                     f"§5「使用规则」应解析出 5 条，实测 {len(rules)} ⇒ 规范可能改过，请复核本门禁")
    unknown = [p for p in named if p not in PAGE_MAP]
    if unknown:
        rep.add_fail("F0:页面映射",
                     f"§5 规则 2 点名了 {len(named)} 页 {named}，"
                     f"但 `PAGE_MAP` 未覆盖 {unknown} ⇒ 新页会静默漏判，请补映射")
    stale = [p for p in PAGE_MAP if p not in named]
    if stale:
        rep.add_fail("F0:映射过期",
                     f"`PAGE_MAP` 里的 {stale} 已不在 §5 规则 2 的名单里 ⇒ 请删映射")


# ===========================================================================
# 报告：只报不判（R 组）
# ===========================================================================
def report_r_group(
    files: list[tuple[str, str]], named: list[str], rep: Report, dump: bool
) -> None:
    filemap = dict(files)

    # R1 —— 规则 4「导出保留」：判据落点不在前端源码。
    cr = filemap.get("frontend/apps/web/app/(app)/contract-review/page.tsx", "")
    cr_note = "`contract-review/page.tsx:37` 明写「本轮不做导出 Word（CR-05 已降级）」" if "本轮不做导出 Word" in cr else "未找到降级说明"
    hp = filemap.get("frontend/apps/lawyer/app/(app)/cases/[id]/page.tsx", "")
    hp_note = "开庭材料包由**后端**生成（`hearing_pack_path`）" if "hearing_pack_path" in hp else "未找到后端导出路径"
    rep.add_note(
        "R1 §5 规则 4（导出保留）：**前端源码里判不了**。"
        f"{cr_note}；{hp_note} ⇒ 三态标记是否保留在 PDF/Word 里，"
        "**判据必须落在后端导出产物上**（与 `backend-cooperation-2026-09-21.md` 口径一致）。"
    )

    # R2 —— 三态可达性。
    lits = count_state_literals(files)
    reachable = []
    for rel, text in files:
        if not is_product(rel):
            continue
        if re.search(r'state=\{[^}]*\}', text) and "analysisProvenance" in text:
            reachable.append(rel)
    rep.add_note(
        f"R2 三态可达性：产品页 `state=\"…\"` 字面量分布 {lits}；"
        f"**数据驱动**（`state={{…}}`）的只有 {reachable or ['（无）']}。"
        "⇒ `verified`（律师已确认）**只在案件详情页可达**（经 `analysisProvenance()` 读 "
        "`confirmed_by` / `ai_generated===false`），其余页面结构上到不了第三态。"
        "⚠️ **这不是产品缺陷**：§5 决策 08 的 provenance 数据模型**后端未落库**，"
        "前端只能派生或写死；且 `phase3-implementation.md` 决策 6 已把案件详情页的徽章"
        "定为**分析级**（后端只有 `confirmed_by`，无分段确认字段）。"
        "⇒ 属**冻结导致的**，需与后端配合。"
    )

    # R3 —— borderStyle 死字段。
    colors_src = filemap.get(COLORS_REL, "")
    consumers = [
        rel for rel, text in files
        if rel != COLORS_REL and "borderStyle" in text
    ]
    if "borderStyle" in colors_src:
        rep.add_note(
            "R3 `provenanceStates[].borderStyle`（AI 态 `\"dashed\"`）**声明了但 0 消费者**"
            f"（`colors.ts` 之外命中 {len(consumers)} 个文件）。"
            "§2.3 与 §5 的表格都写「AI 生成：紫色**虚线上边框**」，"
            "但 §5 规则 5 只要求「颜色 + 文字 + 图标」三重编码 ⇒ "
            "**规范内部不一致（表格 vs 规则）+ 死字段**，是缺陷还是删除该字段**待拍板**。"
            "⚠️ 与 `verify_component_wiring.py` 的「视觉分歧」**互补不重复**："
            "那条讲的是**页面内联**实现用四边实线；本条讲的是**组件层**声明了虚线却没渲染。"
        )

    if dump:
        print("\n[D] R 组原始量测")
        print(f"    state 字面量分布 : {lits}")
        print(f"    数据驱动可达页面 : {reachable}")
        print(f"    borderStyle 消费者: {consumers}")


# ===========================================================================
# 自检（纯函数：不读真实仓库、不启浏览器 ⇒ 可进 CI 的 SELFTESTABLE）
# ===========================================================================
SYN_SPEC = """## 05 责任边界三态系统

### 使用规则

1. **穷尽性** —— 任何法律内容必须且只能属于三态之一。
2. **全局图例** —— 问答页、文书页、案件详情页顶栏常驻三态图例（移动端置于问题标题下方）。
3. **状态可迁移** —— AI 生成 → 律师确认 单向、留痕；徽章可点击查看流转记录。
4. **导出保留** —— 导出 PDF / Word 时三态标记必须保留。
5. **颜色不可独立承载语义** —— 必须「颜色 + 文字 + 图标」三重编码。

## 06 组件规范
"""

SYN_COLORS_OK = """
export const provenanceStates = {
  ai: { id: "ai", label: "AI 生成", icon: "✦", borderStyle: "dashed" as const },
  verified: { id: "verified", label: "律师已确认", icon: "✓", borderStyle: "solid" as const },
  pending: { id: "pending", label: "待复核", icon: "⏱", borderStyle: "solid" as const },
} as const;
"""
SYN_BADGE_OK = """
const stateStyles: Record<ProvenanceState, string> = {
  ai: "bg-ai-500/15",
  verified: "bg-verified-500/15",
  pending: "bg-pending-500/15",
};
const inner = (<><span>{meta.icon}</span><span>{meta.label}</span></>);
"""


def _syn_files(**over: str) -> dict[str, str]:
    """合成 fixture。**键必须是仓库相对路径**（与 `iter_sources()` 同一约定）。

    ⚠️ 干净对照里**文书页必须带图例**、且**必须有一处徽章接了 `onOpenHistory`** ——
    否则 Q1 会把「fixture 自己不合规」误报成「干净对照也红」。
    （第一版正是这样：Q1 与 Q11 双双变红，而根因在 fixture 不在判据。）
    """
    base = {
        "frontend/packages/ui/src/theme/colors.ts": SYN_COLORS_OK,
        "frontend/packages/ui/src/components/ProvenanceBadge.tsx": SYN_BADGE_OK,
        # 问答页：图例 + **已接线**的徽章（满足 F4）
        "frontend/apps/web/app/(app)/qa/page.tsx":
            '<ProvenanceLegend variant="compact" />\n'
            '<ProvenanceBadge state="ai" onOpenHistory={() => {}} />',
        "frontend/apps/web/app/(app)/documents/page.tsx": '<ProvenanceLegend />\n<ProvenanceBadge state="ai" />',
        "frontend/apps/lawyer/app/(app)/cases/[id]/page.tsx": "<ProvenanceLegend />",
    }
    base.update(over)
    return base


def _syn_named() -> list[str]:
    sec = load_spec_section(SYN_SPEC, "05 责任边界三态系统")
    assert sec
    rules = parse_rules(sec)
    r2 = [b for n, _, b in rules if n == 2][0]
    return parse_named_pages(r2)


def _run_arms(files: dict[str, str], named: list[str], rules: list[tuple[int, str, str]]):
    rep = Report()
    judge_f0(named, rules, rep)
    judge_f1(parse_provenance_states(files["frontend/packages/ui/src/theme/colors.ts"]),
             files["frontend/packages/ui/src/components/ProvenanceBadge.tsx"], rep)
    judge_f2(named, files, rep)
    judge_f3(list(files.items()), {"ai", "verified", "pending"}, rep)
    judge_f4(list(files.items()), rep)
    return rep


def self_test() -> int:
    print("=== §5 三态门禁 · 自检（纯函数，不读真实仓库） ===")
    arms: list[tuple[str, bool, str]] = []
    named = _syn_named()
    sec = load_spec_section(SYN_SPEC, "05 责任边界三态系统") or ""
    rules = parse_rules(sec)

    # Q0 —— 规范解析本身
    arms.append(("Q0 规范解析出 5 条规则", len(rules) == 5, f"实测 {len(rules)}"))
    arms.append(("Q0 规则 2 解析出 3 页", named == ["问答页", "文书页", "案件详情页"], f"实测 {named}"))

    # Q1 —— 干净对照：全部合规范 ⇒ 不得有任何 fail
    r = _run_arms(_syn_files(), named, rules)
    arms.append(("Q1 干净对照必须无 fail", not r.fail, f"{[k for k, _ in r.fail]}"))

    # Q2 —— 缺图标 ⇒ 必须红
    r = _run_arms(_syn_files(**{"frontend/packages/ui/src/theme/colors.ts":
                                SYN_COLORS_OK.replace('icon: "✓"', 'icon: ""')}), named, rules)
    arms.append(("Q2 缺 icon 必须红", any(k == "F1:图标" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q3 —— 两态同 label ⇒ 必须红
    r = _run_arms(_syn_files(**{"frontend/packages/ui/src/theme/colors.ts":
                                SYN_COLORS_OK.replace('label: "待复核"', 'label: "AI 生成"')}), named, rules)
    arms.append(("Q3 label 重复必须红", any(k == "F1:文字可辨" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q4 —— stateStyles 少一态 ⇒ 必须红
    r = _run_arms(_syn_files(**{"frontend/packages/ui/src/components/ProvenanceBadge.tsx":
                                SYN_BADGE_OK.replace('  pending: "bg-pending-500/15",\n', "")}), named, rules)
    arms.append(("Q4 颜色键缺失必须红", any(k == "F1:颜色键" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q5 —— Badge 不渲染 label ⇒ 必须红
    r = _run_arms(_syn_files(**{"frontend/packages/ui/src/components/ProvenanceBadge.tsx":
                                SYN_BADGE_OK.replace("{meta.label}", "")}), named, rules)
    arms.append(("Q5 未渲染 label 必须红", any(k == "F1:渲染" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q6 —— 文书页缺图例 ⇒ 必须红，且**指名是文书页**
    r = _run_arms(_syn_files(**{"frontend/apps/web/app/(app)/documents/page.tsx":
                                '<ProvenanceBadge state="ai" />'}), named, rules)
    arms.append(("Q6 文书页缺图例必须红且指名", any(k == "F2:文书页" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q7 —— 规范点名第 4 页而映射表没跟上 ⇒ F0 必须红（门禁自己的穷尽性守卫）
    spec4 = SYN_SPEC.replace("问答页、文书页、案件详情页顶栏", "问答页、文书页、案件详情页、归档页顶栏")
    sec4 = load_spec_section(spec4, "05 责任边界三态系统") or ""
    n4 = parse_named_pages([b for n, _, b in parse_rules(sec4) if n == 2][0])
    r = _run_arms(_syn_files(), n4, parse_rules(sec4))
    arms.append(("Q7 点名新页未映射必须红", any(k == "F0:页面映射" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q8 —— 手抄三态联合 ⇒ F3 必须红
    r = _run_arms(_syn_files(**{"frontend/apps/web/app/(app)/x.ts":
                                'provenance: "ai" | "verified" | "pending";'}), named, rules)
    arms.append(("Q8 手抄联合必须红", any(k.startswith("F3:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q9 —— **假阳性对照**：已用规范类型标注 ⇒ 不得红
    r = _run_arms(_syn_files(**{"frontend/apps/web/app/(app)/x.ts":
                                'p: ProvenanceState;\nconst ALL: ProvenanceState[] = ["ai", "verified", "pending"];'}),
                  named, rules)
    arms.append(("Q9 已用 ProvenanceState 不得红", not any(k.startswith("F3:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q10 —— **假阳性对照**：同词不同轴（会话角色 / KPI 色调）
    r = _run_arms(_syn_files(**{"frontend/apps/web/app/(app)/x.tsx":
                                'const t = { role: "user" | "ai" };\n<Badge variant="ai" />;\nconst c = "pending" | "brand" | "ai" | "gold";'}),
                  named, rules)
    arms.append(("Q10 同词不同轴不得红", not any(k.startswith("F3:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q11 —— 产品页接了 onOpenHistory ⇒ F4 必须绿
    r = _run_arms(_syn_files(**{"frontend/apps/web/app/(app)/qa/page.tsx":
                                '<ProvenanceLegend />\n<ProvenanceBadge state="ai" onOpenHistory={() => {}} />'}),
                  named, rules)
    arms.append(("Q11 接线后 F4 必须绿", not any(k.startswith("F4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q12 —— 组件内部有 onOpenHistory 但产品页没传 ⇒ F4 必须红
    #         （覆盖掉基线的接线，模拟「组件实现了、页面没接」的真实状态）
    r = _run_arms(_syn_files(**{"frontend/apps/web/app/(app)/qa/page.tsx":
                                '<ProvenanceLegend />\n<ProvenanceBadge state="ai" />'}),
                  named, rules)
    arms.append(("Q12 未接线 F4 必须红", any(k.startswith("F4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q13 —— 规则条数变了 ⇒ F0 必须红
    sec3 = SYN_SPEC.replace("5. **颜色不可独立承载语义** —— 必须「颜色 + 文字 + 图标」三重编码。\n", "")
    r = _run_arms(_syn_files(), named, parse_rules(sec3))
    arms.append(("Q13 规则条数变化必须红", any(k == "F0:规则条数" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q14/Q15/Q16 —— 棘轮的三条分支（抽成纯函数就是为了能在这里锁住）
    kh, nh, ex = split_ratchet([("F2:文书页", "x")], {"F2:文书页": "登记"})
    arms.append(("Q14 棘轮内失败不阻断",
                 len(kh) == 1 and not nh and not ex, f"known={len(kh)} new={len(nh)} expired={len(ex)}"))
    kh, nh, ex = split_ratchet([("F2:新页面", "x")], {"F2:文书页": "登记"})
    arms.append(("Q15 棘轮外失败必须阻断", len(nh) == 1 and not kh,
                 f"known={len(kh)} new={len(nh)} expired={len(ex)}"))
    kh, nh, ex = split_ratchet([], {"F2:文书页": "登记"})
    arms.append(("Q16 豁免过期必须阻断", ex == ["F2:文书页"] and not kh and not nh,
                 f"known={len(kh)} new={len(nh)} expired={ex}"))

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
    ap = argparse.ArgumentParser(description="§5 责任边界三态系统门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（纯函数）")
    ap.add_argument("--why", action="store_true", help="打印页面映射与规则的出处")
    ap.add_argument("--dump", action="store_true", help="打印原始量测")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    # —— 环境前提 ——
    missing = [str(p) for p in (SPEC, COLORS_TS, BADGE_TSX) if not p.exists()]
    if missing:
        print("[ENV] 缺文件，无法判据：")
        for m in missing:
            print(f"      {m}")
        print("[ENV] ⇒ **本轮结论不可信**（exit 2 = 环境问题，不是产品缺陷）")
        return 2

    spec_text = SPEC.read_text(encoding="utf-8")
    section = load_spec_section(spec_text, "05 责任边界三态系统")
    if section is None:
        print("[ENV] `design-spec.md` 里找不到 `## 05 责任边界三态系统` 小节 ⇒ 规范结构变了")
        return 2

    rules = parse_rules(section)
    r2 = [b for n, _, b in rules if n == 2]
    if not r2:
        print("[ENV] §5 里解析不出规则 2（全局图例）⇒ 规范措辞变了，请复核")
        return 2
    named = parse_named_pages(r2[0])

    print(f"[ENV] 规范：{SPEC.relative_to(ROOT).as_posix()}")
    print(f"[ENV] §5 解析出 {len(rules)} 条使用规则；规则 2 点名 {len(named)} 页：{'、'.join(named)}")
    for n, name, _ in rules:
        print(f"      规则 {n} · {name}")

    if args.why:
        print("\n[W] 页面映射的出处")
        for page in named:
            print(f"      {page} → {PAGE_MAP.get(page, '（未映射）')}")
            print(f"        理由：{PAGE_MAP_WHY.get(page, '（缺理由）')}")

    _t0 = time.perf_counter()
    files_list = iter_sources()
    _scan_sec = time.perf_counter() - _t0
    files = dict(files_list)
    # ⚠️ **计数走 stdout（确定）、耗时走 stderr（浮动）**：
    # 第一版把耗时也打进 stdout ⇒ 两次运行 0.09s vs 0.11s ⇒ **确定性告破**。
    # 判据：**要求逐字节一致的输出里不许出现浮动量**（时间戳 / 耗时 / 随机序）。
    # 而**计数本身就是剪枝是否失效的信号**——`SKIP_DIRS` 一旦没生效，
    # 这个数会从 99 涨到几千，且它是确定的。
    print(f"[ENV] 扫描 {len(files_list)} 个源码文件")
    print(f"[ENV] 扫描耗时 {_scan_sec:.2f}s（>10s ⇒ 剪枝失效，见 `iter_sources` docstring）",
          file=sys.stderr)

    # ⚠️ **取文件守卫**：`files.get(k, "")` 的默认值会让「键写错」伪装成
    # 「产品里是空的」⇒ 造出假红（实测 F1 报「三态 0 个」、R3 整段消失）。
    # 判据的输入取不到 ⇒ 属**工具问题**，必须 exit 2，不能拿空串继续判。
    REQUIRED_FILES = (COLORS_REL, BADGE_REL)
    absent = [k for k in REQUIRED_FILES if k not in files]
    if absent:
        print("[ENV] 下列必需文件未进入扫描集（键约定不一致？）：")
        for k in absent:
            print(f"      {k}")
        print(f"[ENV] 扫描集样例键：{sorted(files)[:3]}")
        print("[ENV] ⇒ **本轮结论不可信**（exit 2 = 工具问题，不是产品缺陷）")
        return 2

    states = parse_provenance_states(files[COLORS_REL])
    if not states:
        print(f"[ENV] 解析不出 `provenanceStates`（{COLORS_REL}）⇒ 源码结构变了，请复核")
        return 2
    canonical = set(states)

    rep = Report()
    judge_f0(named, rules, rep)
    judge_f1(states, files[BADGE_REL], rep)
    judge_f2(named, files, rep)
    judge_f3(files_list, canonical, rep)
    judge_f4(files_list, rep)

    print("\n=== 判据 ===")
    for msg in rep.ok:
        print(f"  ✓ {msg}")

    # —— 棘轮：已登记的不阻断，未登记的阻断；豁免过期也要红 ——
    known_hits, new_hits, expired = split_ratchet(rep.fail, KNOWN_GAPS)

    if known_hits:
        print("\n  ── 已登记欠账（棘轮，不阻断 CI；修好必须删豁免）──")
        for key, msg in known_hits:
            print(f"  • [{key}] {msg}")
            print(f"      登记：{KNOWN_GAPS[key]}")

    if new_hits:
        print("\n  ── ✗ 新的规则违反（阻断）──")
        for key, msg in new_hits:
            print(f"  ✗ [{key}] {msg}")

    if expired:
        print("\n  ── ✗ 棘轮豁免已过期（该条目现在通过了，请删豁免）──")
        for key in expired:
            print(f"  ✗ [{key}] {KNOWN_GAPS[key]}")

    print("\n=== 只报不判（R 组，不影响退出码） ===")
    report_r_group(files_list, named, rep, args.dump)
    for msg in rep.notes:
        print(f"  · {msg}")

    if new_hits or expired:
        print(f"\n[结论] exit 1 —— 新违反 {len(new_hits)} 条 · 过期豁免 {len(expired)} 条")
        return 1
    print(f"\n[结论] exit 0 —— 已登记欠账 {len(known_hits)} 条（棘轮内），无新违反")
    return 0


if __name__ == "__main__":
    sys.exit(main())
