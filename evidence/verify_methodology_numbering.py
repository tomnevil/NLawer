#!/usr/bin/env python
"""**编号不变量门禁**：`.workbuddy-ai/memory/methodology.md` 的编号不许再乱。

## 为什么需要它

2026-09-26 之前该文件出现过三种「乱」，都是**并行会话各写各的**造成的：

1. 用**标题式编号**（`## 184.`）追加 ⇒ 与扁平列表（`184.`）**重号**；
2. 标题式条目被排在扁平 `188.`–`204.` **之后** ⇒ 读到 204 再跳回 184；
3. 一条 `### #183` **插在扁平列表中间**。

整理后定为两套编号：**扁平 `1.`–`204.`（冻结）** + **标题式 `## 205.`–（`## NNN.` 追加到末尾）**。
⇒ **本门禁就是把这套约定变成判据**，让「下次再乱」当场变红，而不是等人读到才发现。

## 判据

| 编号 | 内容 | 档 |
|---|---|---|
| **M1** | 扁平区编号 `1..N` **连续、无缺口、无重号、且按文件顺序递增** | 判红 |
| **M2** | 标题式 `## NNN.` **严格递增、无重号、无缺口** | 判红 |
| **M3** | 标题式**最小号 == 扁平最大号 + 1**（接得上、且不重叠） | 判红 |
| **M4** | 扁平区内**不得残留**插入式条目标题（`### #NNN` / `**#NNN**`） | 判红 |
| **M5** | 首行**自述**条数（扁平 N 条 / 续编 M 条 / 区间 `（a–b）`）与实测一致 | 判红 |
| **M6** | 其它标题里的 `（a–b）` 区间与实测续编区间一致 | **只报** |
| **M7** | 活文件里 `#旧号` 且**同行命中该条目的关键词** ⇒ 疑似旧编号未换算 | 判红 |
| **M7s** | 活文件里出现**歧义号段**（旧号集合）的其它行 | **只报** |

🚨 **为什么「歧义号段」要单独处理**：205–209 是从旧 `#183`–`#187` 换算来的，而扁平列表里
**各有同号的 183–187**（内容完全不同）。⇒ 一个裸 `#187` 光看号码**分不出**指哪个。
⇒ M7 **只在同行还能命中条目关键词时判红**（例：「清空开发库」+ `#187` = 旧引用未换算）；
否则只报（M7s），交人工判语义。**宁可少判，不可错判。**

⚠️ 与 `verify_spec_refs.py` 的 G5 同族：**天生歧义的东西不做成棘轮**，做了必然假红。

## 用法

```bash
python evidence/verify_methodology_numbering.py                # 判红（CI）
python evidence/verify_methodology_numbering.py --self-test     # 合成夹具自检
python evidence/verify_methodology_numbering.py --inject        # 真实文件上做注入反证（不进 CI）
python evidence/verify_methodology_numbering.py --report-only   # 只打印，不判红
```

退出码：`0` 通过 / `1` 有必判违规 / `2` 环境问题或自检失败。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ".workbuddy-ai/memory/methodology.md"

#: 扁平区 = 第一个 `## N.` 标题**之前**的全部 `^N. ` 行（与本文件里记的 awk 口径一致）。
RX_FLAT = re.compile(r"^(\d+)\. ")
RX_TITLED = re.compile(r"^## (\d+)\. ")
#: 历史上造成「插在中间」的两种写法 —— 扁平区内不许再出现。
RX_INSERT = re.compile(r"^(#{3,4}\s*#\d+|\*\*#\d+\*\*)")
#: 标题里的「原 `#184`」/「原 `### #183`」
RX_OLD = re.compile(r"原\s*`*(?:#{2,4}\s*)?#(\d+)")
#: 文首映射表的行：`| `### #183` | `## 205.` | 条目描述 | 位置变化 |`
RX_ROW = re.compile(r"^\|\s*`[^`]*#(\d+)[^`]*`\s*\|\s*`[^`]*#(\d+)[^`]*`\s*\|\s*(.+?)\s*\|")
RX_RANGE = re.compile(r"（(\d+)[–—\-](\d+)）")
RX_FLAT_N = re.compile(r"自我纠错纪律\s*(\d+)\s*条")
RX_TITLED_N = re.compile(r"标题式续编\s*(\d+)\s*条")

SKIP_DIR = {".git", "node_modules", ".next", "dist", "__pycache__", ".venv", "_tmp_tests", "storage"}
SCAN_SUFFIX = {".md", ".py", ".ts", ".tsx"}
#: append-only 的每日日志与历史存证**不改**（也不该改）⇒ 不参与 M7。
EXCLUDE = (
    re.compile(r"\.workbuddy-ai[\\/]memory[\\/]\d{4}-\d{2}-\d{2}\.md$"),
    re.compile(r"evidence[\\/][^\\/]+\.txt$"),
)


# ------------------------------------------------------------------ 归一化

def _sq(s: str) -> str:
    """去排版标记 + 去全部空白 ⇒ 只比「引用」本身。"""
    s = s.replace("**", "").replace("`", "")
    return re.sub(r"\s+", "", s)


def keywords(s: str) -> set[str]:
    """从条目描述里抽关键词：`「」` 内的短语 + 按标点/空格切出的段（长度 ≥ 4）。"""
    s = s.replace("**", "").replace("`", "")
    out: set[str] = set()
    for q in re.findall(r"「([^」]{2,})」", s):
        out.add(_sq(q))
    for seg in re.split(r"[，。；、：—–\-|/（）()]+|\s+", s):
        t = _sq(seg)
        if len(t) < 4 or t.isdigit() or len(t) > 40:
            continue
        #: 🚨 **必须排除含 `#` 的段**：标题末尾的「（原 `#187`）」会被抽成关键词 `#187`，
        #: 于是**任何**含 `#187` 的行都「命中自己」 ⇒ M7 变成**恒红**（真实仓库实测 34 条全是这么来的）。
        #: 与 `verify_spec_refs.py` 的 G3 同族洞：**命中了声明，不是引用**。
        if "#" in t or "原" in t:
            continue
        out.add(t)
    return {k for k in out if len(k) >= 3}


# ------------------------------------------------------------------ 解析

def parse_doc(lines: list[str]) -> tuple[int, list[int], list[int], list[str], list[str]]:
    """→ (第一个 `## N.` 的行号, 扁平编号序列, 标题式编号序列, 扁平区插入式标题, 区间标题)"""
    first = next((i for i, ln in enumerate(lines, 1) if RX_TITLED.match(ln)), len(lines) + 1)
    flat = [int(RX_FLAT.match(ln).group(1)) for ln in lines[: first - 1] if RX_FLAT.match(ln)]
    titled = [int(RX_TITLED.match(ln).group(1)) for ln in lines if RX_TITLED.match(ln)]
    inserts = [f"L{i} {ln[:60]}" for i, ln in enumerate(lines[: first - 1], 1) if RX_INSERT.match(ln)]
    #: 🚨 **排除首行**：首行的区间由 M5 判红，M6 再算一次会让同一处错报两遍（自检 Q8 实测踩到）。
    ranges = [ln for ln in lines[1:] if ln.startswith("#") and RX_RANGE.search(ln)]
    return first, flat, titled, inserts, ranges


def build_old_map(lines: list[str]) -> dict[str, tuple[str, set[str]]]:
    """旧号 → (新号, 关键词)。**从文件里现读**，不硬编码 —— 改条目措辞后自动跟着走。"""
    m: dict[str, tuple[str, set[str]]] = {}
    for ln in lines:
        r = RX_ROW.match(ln)
        if r:
            old, new, desc = r.group(1), r.group(2), r.group(3)
            cur = m.get(old, (new, set()))
            m[old] = (new, cur[1] | keywords(desc))
            continue
        t = RX_TITLED.match(ln)
        if t:
            o = RX_OLD.search(ln)
            if o:
                old = o.group(1)
                cur = m.get(old, (t.group(1), set()))
                m[old] = (cur[0], cur[1] | keywords(ln))
    return m


def _seq_bad(seq: list[int], label: str) -> list[str]:
    if not seq:
        return [f"{label} 一条都没有 ⇒ **前提没了**（解析口径变了？）"]
    out: list[str] = []
    dup = sorted({n for n in seq if seq.count(n) > 1})
    if dup:
        out.append(f"{label} **重号**：{dup}")
    missing = sorted(set(range(min(seq), max(seq) + 1)) - set(seq))
    if missing:
        out.append(f"{label} **缺口**：{missing}")
    if seq != sorted(seq):
        out.append(f"{label} **不是按文件顺序递增**（读到后面跳回前面）")
    if seq and seq[0] != 1 and label == "M1 扁平":
        out.append(f"{label} 不是从 1 开始（首号 {seq[0]}）")
    return out


# ------------------------------------------------------------------ 核心判定（纯函数）

def analyze(
    lines: list[str], live_docs: dict[str, str] | None = None
) -> tuple[list[str], list[str]]:
    """→ (判红, 只报)。`live_docs=None` ⇒ 只判文件内部（自检用）。

    `live_docs` = `{相对路径: 全文}`，**传 dict 而不是传 root**，是为了让 M7 也能被自检/注入覆盖
    （合成夹具直接喂进去，不用真的往磁盘上写文件）。
    """
    hard: list[str] = []
    soft: list[str] = []

    first, flat, titled, inserts, ranges = parse_doc(lines)
    if not flat or not titled:
        # 前提锁：解析不到 ⇒ **不许**当成「没有违规」
        return ([f"M0 前提没了：扁平 {len(flat)} 条 / 标题式 {len(titled)} 条 ⇒ 解析口径变了"], [])

    hard += _seq_bad(flat, "M1 扁平")
    hard += _seq_bad(titled, "M2 标题式")

    if min(titled) != max(flat) + 1:
        hard.append(f"M3 标题式最小号 {min(titled)} ≠ 扁平最大号 {max(flat)} + 1 ⇒ 接不上或重叠")

    for x in inserts:
        hard.append(f"M4 扁平区里残留插入式条目标题：{x}")

    # M5 首行自述
    head = lines[0] if lines else ""
    mf, mt, mr = RX_FLAT_N.search(head), RX_TITLED_N.search(head), RX_RANGE.search(head)
    if not (mf and mt and mr):
        hard.append(f"M5 首行解析不到自述条数/区间：`{head[:80]}` ⇒ 前提没了")
    else:
        self_flat, self_titled = int(mf.group(1)), int(mt.group(1))
        self_rng = (int(mr.group(1)), int(mr.group(2)))
        if self_flat != len(flat):
            hard.append(f"M5 首行自述扁平 {self_flat} 条，实测 {len(flat)} 条")
        if self_titled != len(titled):
            hard.append(f"M5 首行自述续编 {self_titled} 条，实测 {len(titled)} 条")
        if self_rng != (min(titled), max(titled)):
            hard.append(f"M5 首行自述区间 {self_rng[0]}–{self_rng[1]}，实测 {min(titled)}–{max(titled)}")

    # M6 只报：其它标题里的区间
    for ln in ranges:
        m = RX_RANGE.search(ln)
        if m and (int(m.group(1)), int(m.group(2))) != (min(titled), max(titled)):
            soft.append(f"M6 标题区间过时：`{ln[:70]}` ⇒ 实测 {min(titled)}–{max(titled)}")

    if live_docs is None:
        return (hard, soft)

    # M7 / M7s：活文件里的歧义号引用
    old_map = build_old_map(lines)
    if not old_map:
        return (hard + ["M0 映射表解析不到 ⇒ 旧号集合为空，M7 无意义"], soft)
    for rel, text in sorted(live_docs.items()):
        for i, ln in enumerate(text.split("\n"), 1):
            if len(ln) > 400:
                continue
            for old, (new, kws) in old_map.items():
                if not re.search(rf"#{old}\b", ln):
                    continue
                hit = [k for k in kws if len(k) >= 4 and k in _sq(ln)]
                #: 同行出现「原」⇒ 这大概率是**换算声明**（「已记入 #206（原 `#184`）」），
                #: **降为只报**，不判红 —— 声明与引用长得一样，机器分不出，交人工。
                if hit and "原" not in ln:
                    hard.append(
                        f"M7 {rel}:{i}  `#{old}` 同行命中「{new}」条目关键词 {hit[:2]} "
                        f"⇒ 疑似旧编号未换算，应写 `#{new}`\n      {ln.strip()[:110]}"
                    )
                else:
                    soft.append(f"M7s {rel}:{i}  `#{old}` —— 歧义号段（扁平 {old} 与续编 {new} 同号），需人工判语义")
    return (hard, soft)


def collect_live_docs(root: Path) -> dict[str, str]:
    """扫活文件。两个**声明型**文件不参与：`methodology.md`（映射表）与**本门禁自己**（规则说明）。"""
    docs: dict[str, str] = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIR]
        for f in fn:
            p = Path(dp) / f
            if p.suffix.lower() not in SCAN_SUFFIX:
                continue
            rel = p.relative_to(root).as_posix()
            if rel == SPEC or rel.endswith("verify_methodology_numbering.py"):
                continue
            if any(rx.search(rel) for rx in EXCLUDE):
                continue
            try:
                docs[rel] = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
    return docs


# ------------------------------------------------------------------ 自检

FIXTURE = "\n".join([
    "# X 方法论：自我纠错纪律 3 条 + 标题式续编 2 条（4–5）",   # 1
    "",                                                        # 2
    "## 自我纠错纪律（3 条）",                                   # 3
    "1. 第一条",                                                # 4
    "2. 第二条",                                                # 5
    "3. 第三条",                                                # 6
    "## 4. 「路由是否注册」不能查 `app.routes`（原 `### #183`）",  # 7
    "## 5. 从仓库根跑全量 pytest 会清空开发库（原 `#187`）",      # 8
])

#: (名, 说明, 夹具文本, 期望 (判红条数, 只报条数))
SELFTEST: tuple[tuple[str, str, str, tuple[int, int]], ...] = (
    ("Q1", "健康的文档 ⇒ 0 判红 0 只报", FIXTURE, (0, 0)),
    # ⚠️ 夹具必须**只带那一条病**：多带一条就会触发第二条判据（M3 最容易被顺带踩到，
    #    因为「扁平最大号 + 1 == 标题式最小号」对扁平/标题式的数量都敏感）。
    ("Q2", "扁平缺号（缺 2）⇒ M1 判红",
     "# X：自我纠错纪律 3 条 + 标题式续编 1 条（5–5）\n1. a\n3. c\n4. d\n## 5. t（原 `#187`）", (1, 0)),
    ("Q3", "扁平重号 ⇒ M1 判红",
     "# X：自我纠错纪律 3 条 + 标题式续编 1 条（3–3）\n1. a\n2. b\n2. b2\n## 3. t（原 `#187`）", (1, 0)),
    ("Q4", "标题式跳跃（4,6）⇒ M2 判红",
     "# X：自我纠错纪律 3 条 + 标题式续编 2 条（4–6）\n1. a\n2. b\n3. c\n## 4. t（原 `#187`）\n## 6. u", (1, 0)),
    ("Q5", "标题式不接扁平（扁平最大 1，标题式从 9 起）⇒ M3 判红",
     "# X：自我纠错纪律 1 条 + 标题式续编 1 条（9–9）\n1. a\n## 9. t（原 `#187`）", (1, 0)),
    ("Q6", "扁平区里插了 `### #183` ⇒ M4 判红",
     "# X：自我纠错纪律 2 条 + 标题式续编 1 条（3–3）\n1. a\n### #183 插在中间\n2. b\n## 3. t（原 `#187`）", (1, 0)),
    ("Q7", "首行自述条数与实测不符 ⇒ M5 判红",
     "# X：自我纠错纪律 9 条 + 标题式续编 1 条（4–4）\n1. a\n2. b\n3. c\n## 4. t（原 `#187`）", (1, 0)),
    ("Q8", "首行自述区间与实测不符 ⇒ M5 判红",
     "# X：自我纠错纪律 3 条 + 标题式续编 2 条（4–9）\n1. a\n2. b\n3. c\n## 4. t（原 `#187`）\n## 5. u", (1, 0)),
    ("Q9", "附录标题区间过时 ⇒ **只报** M6（不是判红；首行区间归 M5，不重复算）",
     "# X：自我纠错纪律 3 条 + 标题式续编 2 条（4–5）\n## 附录（4–4）\n1. a\n2. b\n3. c\n"
     "## 4. t（原 `#187`）\n## 5. u", (0, 1)),
    ("Q10", "**只判文件内部**（不传 `live_docs`）⇒ 不做 M7，0 判红", FIXTURE, (0, 0)),
)

#: M7 要在「活文件」上判 ⇒ 用 dict 喂合成文档（**不落磁盘**）。
#: 夹具文档用 FIXTURE（它含 `## 5. …会清空开发库（原 `#187`）` ⇒ 旧号 187 → 新号 5）。
SELFTEST_LIVE: tuple[tuple[str, str, str, tuple[int, int]], ...] = (
    ("Q11", "活文件里 `#187` 且同行命中「会清空开发库」⇒ M7 **判红**",
     "（**本机跑全量 pytest 会清空开发库** #187）", (1, 0)),
    ("Q12", "活文件里 `#187` 但**没有**条目关键词 ⇒ **只报** M7s（可能是扁平 187）",
     "纪律 #187 讲的是候选池枯竭", (0, 1)),
    ("Q13", "同行含「原」⇒ 那是**换算声明**，**降为只报**不判红",
     "已记入 `methodology.md` **#5**（原 `#187`）—— 就算带上「清空开发库」也不许判红", (0, 1)),
)


def run_self_test() -> int:
    bad: list[str] = []
    cases = [(k, d, t, e, None) for k, d, t, e in SELFTEST]
    cases += [(k, d, t, e, {"live/a.md": t}) for k, d, t, e in SELFTEST_LIVE]
    for key, desc, text, (eh, es), live in cases:
        hard, soft = analyze(FIXTURE.split("\n"), live_docs=live) if live else analyze(text.split("\n"))
        got = (len(hard), len(soft))
        ok = got == (eh, es)
        print(f"  [{'✓' if ok else '✗'}] {key} {desc}")
        print(f"        期望 判红{eh}/只报{es}   实测 判红{got[0]}/只报{got[1]}")
        if not ok:
            for x in hard + soft:
                print(f"          · {x}")
            bad.append(f"{key} 期望 {(eh, es)} 实测 {got}")
    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **工具坏了，不是产品坏了**（exit 2）")
        for x in bad:
            print(f"  - {x}")
        return 2
    print(f"\n自检 {len(cases)} 臂全绿")
    return 0


# ------------------------------------------------------------------ 注入反证

#: (名, 说明, 对真实文件行序列做的改动, 期望判红条数)
INJECT: tuple[tuple[str, str, str], ...] = (
    ("I0", "原文不动 ⇒ 0（对照组）", "keep"),
    ("I1", "扁平删掉一条 ⇒ M1 红", "flat_gap"),
    ("I2", "标题式改成重号 ⇒ M2 红", "titled_dup"),
    ("I3", "首行自述条数改错 ⇒ M5 红", "head_wrong"),
    ("I4", "扁平区里插入 `### #183` ⇒ M4 红", "insert_heading"),
    ("I5", "**真实映射表** + 一条合成坏行（`#187` + 条目关键词）⇒ M7 红", "m7_line"),
)

#: I5 的合成坏行：故意写成「语义属于 209、号码写的却是旧号 187」。
INJECT_M7_LINE = "（**本机跑全量 pytest 会清空开发库** #187）"


def _mutate(lines: list[str], how: str) -> list[str]:
    out = list(lines)
    if how == "keep":
        return out
    first = next(i for i, ln in enumerate(out) if RX_TITLED.match(ln))
    if how == "flat_gap":
        for i, ln in enumerate(out[:first]):
            if RX_FLAT.match(ln):
                out[i] = "（本条被注入删掉）"
                break
    elif how == "titled_dup":
        n = [int(RX_TITLED.match(ln).group(1)) for ln in out if RX_TITLED.match(ln)]
        for i, ln in enumerate(out):
            m = RX_TITLED.match(ln)
            if m and int(m.group(1)) == max(n):
                out[i] = ln.replace(f"## {m.group(1)}.", f"## {min(n)}.", 1)
                break
    elif how == "head_wrong":
        out[0] = out[0].replace("条", "条", 1)
        out[0] = RX_FLAT_N.sub("自我纠错纪律 999 条", out[0], count=1)
    elif how == "insert_heading":
        out.insert(first - 1, "### #183 注入：插在扁平列表中间")
    return out


def run_injection() -> int:
    spec = ROOT / SPEC
    try:
        base = spec.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        print(f"读不到 {SPEC}：{e} ⇒ exit 2")
        return 2
    # 前提锁：原文必须是健康的，否则注入结论不可信
    hard0, _ = analyze(base)
    if hard0:
        print(f"原文自身就有 {len(hard0)} 条判红 ⇒ 注入反证**不可信**（先修原文）")
        for x in hard0[:5]:
            print(f"  · {x}")
        return 2
    #: 🚨 **前提锁**：I5 依赖「坏行里真有 209 条目的关键词」。规范改措辞 ⇒ 前提没了 ⇒
    #: **exit 2**，而不是让这条臂**静默变绿**（变绿了它就成了「假绿的臂」）。
    om = build_old_map(base)
    if "187" in om:
        kws = [k for k in om["187"][1] if len(k) >= 4 and k in _sq(INJECT_M7_LINE)]
        if not kws:
            print(f"I5 前提没了：`{INJECT_M7_LINE}` 不含 #{om['187'][0]} 条目的任何关键词 ⇒ exit 2")
            return 2
    else:
        print("I5 前提没了：真实文件里解析不到旧号 187 ⇒ exit 2")
        return 2

    bad: list[str] = []
    for key, desc, how in INJECT:
        want = 0 if how == "keep" else 1
        if how == "m7_line":
            hard, _ = analyze(base, live_docs={"live/injected.md": INJECT_M7_LINE})
        else:
            hard, _ = analyze(_mutate(base, how))
        ok = len(hard) >= want and (how == "keep" or len(hard) > 0)
        print(f"  [{'✓' if ok else '✗'}] {key} {desc} —— 期望 ≥{want}，实测 {len(hard)}")
        for x in hard[:3]:
            print(f"          · {x[:120]}")
        if not ok:
            bad.append(key)
    if bad:
        print(f"\n注入反证失败 {len(bad)} 臂 ⇒ exit 2")
        return 2
    print(f"\n注入反证 {len(INJECT)} 臂全绿（真实文件上做的，**不碰磁盘**）")
    return 0


# ------------------------------------------------------------------ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--inject", action="store_true")
    ap.add_argument("--report-only", action="store_true", help="只打印，不判红")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test()
    if args.inject:
        return run_injection()

    spec = ROOT / SPEC
    if not spec.exists():
        print(f"找不到 {SPEC} ⇒ exit 2")
        return 2
    try:
        lines = spec.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        print(f"读不到 {SPEC}：{e} ⇒ exit 2")
        return 2

    hard, soft = analyze(lines, live_docs=collect_live_docs(ROOT))
    first, flat, titled, _, _ = parse_doc(lines)
    print(f"扁平 `1.`–`{max(flat)}.`（{len(flat)} 条，止于 L{first - 1}） · "
          f"标题式 `## {min(titled)}.`–`## {max(titled)}.`（{len(titled)} 条） · 合计 {len(flat) + len(titled)} 条")

    if hard:
        print(f"\n【发现 {len(hard)} 条必判违规】")
        for x in hard:
            print(f"  {x}")
    if soft:
        print(f"\n【只报 {len(soft)} 条 —— 不判红，交人工】")
        for x in soft[:40]:
            print(f"  {x}")
    if not hard and not soft:
        print("\n编号不变量全部成立 ✓")

    if args.report_only:
        return 0
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
