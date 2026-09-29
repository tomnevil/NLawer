#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""probe_mono_coverage.py —— #58 第一步：**小样本实测**「全站等宽」的静态可判性。

════════════════════════════════════════════════════════════════════════
这个脚本**不是门禁**，它**只测量、不判定、不设退出码语义**（恒 exit 0）。
它的唯一目的是回答一个决策问题：

    §1 原则 4「金额、案号、日期、条款号全部等宽 + tabular-nums」
    —— 能不能用**静态规则**判，而**不产生稳定假红**？

════════════════════════════════════════════════════════════════════════
为什么要先测（而不是直接写门禁）：

  上一遍覆盖率审计把「§1 原则 4 全站等宽」记为**真·零判据**
  （`verify_component_refactor.py` 的 J7/J8 **只覆盖 KpiCard**，且引的是 §6.3）。

  但它**不适合直接写门禁**，因为「哪些字符串算金额/案号/日期」需要**语义分类**。
  同族的前车之鉴（`design-audit.md` §14.4 候选② 明写）：
  「每页 ≤1 primary」那条规则写出来就是**稳定假红** ——
  规则本身能跑、结果每次都红、但红的**不是缺陷**。那种门禁的结局是被加进豁免名单，
  然后**再也没人看**（`methodology` #184 / `README` 坑 58：「只报不判」是双重豁免）。

  ⇒ 所以顺序必须是：**先量一批真实数据 → 看假红率 → 再决定写不写门禁**。

════════════════════════════════════════════════════════════════════════
测量口径（**读的时候要盯住这三条，否则会误读结果**）：

  1. **锚点 = 高精度内容锚点**：只挑那些「**出现即几乎必然是在渲染金额/案号/日期**」的
     标识符（格式化函数调用 + 明确语义的字段名）。**不**用「任意数字字面量」这种宽锚点
     —— 那种锚点会把 `{i + 1}`、`{items.length}` 全捞进来。
  2. **覆盖判定 = 就近 className**：先看**同一行**有没有 `className` 含 `num`；
     没有再**向前回溯 6 行**找**最近**的一个 `className`（多行 JSX 的常见形态）。
     三个档分别计数：`same-line` / `window` / `none`。
     ⚠️ `window` 档会**误判为覆盖**（外层容器的 `num` 被算到内层头上）⇒
     它让结果是**偏乐观**的（假绿方向），**不是**偏红方向。这一点决定了结论怎么用。
  3. **只统计 JSX 文本位置**：脚本按行粗筛，**不做**完整 AST 解析。
     局限见文件末尾「未验证边界」。

════════════════════════════════════════════════════════════════════════
用法（**必须从仓库根、用后端 venv 的 python 跑**）：

    cd <仓库根>
    backend/.venv/Scripts/python.exe evidence/probe_mono_coverage.py
    backend/.venv/Scripts/python.exe evidence/probe_mono_coverage.py --list   # 列出未覆盖点

登记：`run_ci_probes.py` 的 `ONE_SHOT`（「一次性：拍板前取证」）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                      # evidence/ 的上一层 = 仓库根
FRONTEND = ROOT / "frontend"

SKIP_DIRS = {".next", "node_modules", "dist", ".turbo", "__pycache__"}

# ── 锚点：高精度内容锚点（出现即几乎必然在渲染 金额/案号/日期）──────────────
# 分四类，与 §1 原则 4 的四项一一对应；「条款号」没有稳定标识符 ⇒ 本遍**不测**
# （这条缺口本身也是结论的一部分，见输出末尾）。
ANCHORS: list[tuple[str, str, re.Pattern[str]]] = [
    ("金额", "fmtAmount(", re.compile(r"\bfmtAmount\s*\(")),
    ("金额", "yuan(", re.compile(r"\byuan\s*\(")),
    ("金额", "claim_amount", re.compile(r"\bclaim_amount\b")),
    ("金额", "price_cents", re.compile(r"\bprice_cents\b")),
    ("金额", "total_cents", re.compile(r"\btotal_cents\b")),
    ("日期", "fmtDate(", re.compile(r"\bfmtDate(?:Time)?\s*\(")),
    ("日期", "fmtTime(", re.compile(r"\bfmtTime\s*\(")),
    ("日期", "created_at", re.compile(r"\bcreated_at\b")),
    ("日期", "updated_at", re.compile(r"\bupdated_at\b")),
    ("日期", "due_at", re.compile(r"\bdue_at\b")),
    ("日期", "deadline", re.compile(r"\bdeadline\b")),
    ("案号", "case_no", re.compile(r"\bcase_no\b")),
    ("案号", "order_no", re.compile(r"\border_no\b")),
]

NUM_TOKEN = re.compile(r"(?<![\w-])num(?![\w-])")
CLASSNAME_RE = re.compile(r"className\s*=")
WINDOW = 6          # 向前回溯行数
MAX_LIST = 60       # --list 最多列多少条

# ── 形状分桶：把「锚点出现在**非渲染位**」的假红自动分出去 ────────────────────
# 🚨 这一步是**本实测的核心**。原始「未覆盖 70.6%」这个数字**单独看是误导性的**：
#    锚点 `case_no` 大量出现在**类型声明 / 列元数据 / 计算式**里，那些位置
#    **根本不存在「加不加等宽」这回事** ⇒ 直接当缺陷报就是**稳定假红**。
#    所以先分桶，只让剩下的桶进入人工分诊。
CJK = re.compile(r"[\u4e00-\u9fff]")

BUCKET_LABEL = {
    "def":     "函数/常量**定义**行（`function fmtDate(` / `const yuan = (`）",
    "type":    "TS **类型声明**（`order_no: string;`）",
    "key":     "**列元数据**（`key: \"price_cents\",`）",
    "comment": "**注释/说明**行",
    "prop":    "**组件属性值**（`<KpiCard value={yuan(...)}`）⇒ 等宽可能在组件内部",
    "logic":   "**非 JSX 逻辑**（`reduce(...)` / `daysLeft(...)` / 赋值）",
    "jsx":     "**JSX 文本位**（`{row.case_no}`）⇒ **最可能是真渲染点**",
    "other":   "其他（需人工看）",
}


def bucket(line: str) -> str:
    """按**行形状**粗分桶。⚠️ 这是启发式，不是 AST —— 见「未验证边界」。"""
    s = line.strip()
    if s.startswith("//") or s.startswith("*") or s.startswith("/*"):
        return "comment"
    if re.match(r"^(export\s+)?(async\s+)?function\s+\w+\s*\(", s):
        return "def"
    if re.match(r"^(export\s+)?(const|let)\s+\w+\s*=\s*\(", s):
        return "def"
    if re.match(r"^(readonly\s+)?\w+\??\s*:\s*[^=]+;?$", s) and "=>" not in s:
        return "type"
    if re.match(r"^key\s*:\s*", s):
        return "key"
    # 含反引号的中文说明行（本项目注释常写成裸中文段）
    if "`" in s and CJK.search(s) and "<" not in s:
        return "comment"
    if re.match(r"^\{.*\},?$", s):
        return "jsx"
    if re.search(r"<[A-Za-z][^>]*\w+=\{", s) and "className" not in s:
        return "prop"
    if not re.search(r"[<>]", s):
        return "logic"
    return "other"


def iter_tsx() -> list[Path]:
    """⚠️ 用 `os.walk` + **原地剪枝**，不用 `rglob`。

    `rglob` 是**先遍历完再过滤** —— `frontend/` 下 `node_modules` 体量巨大，
    这个坑本项目已经踩过（`verify_design_tokens.py` 的 `rglob` 挂死，曾被当挂死，
    见 `run_ci_probes.py` 阶段 1 的注释）。剪枝版把 `dirnames[:]` 就地改写。
    """
    import os

    out: list[Path] = []
    if not FRONTEND.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(FRONTEND):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".tsx"):
                continue
            if ".test." in fn or ".spec." in fn:
                continue
            out.append(Path(dirpath) / fn)
    return sorted(out)


def rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix()
    except ValueError:
        return p.as_posix()


def has_num(text: str) -> bool:
    """该行是否出现 `num` 类令牌（`className` 上下文里）。"""
    if not CLASSNAME_RE.search(text):
        return False
    return bool(NUM_TOKEN.search(text))


def nearest_classname_has_num(lines: list[str], idx: int) -> bool:
    """从 idx 向前（含自身）找**最近**一个含 `className` 的行，看它有没有 `num`。"""
    lo = max(0, idx - WINDOW)
    for j in range(idx, lo - 1, -1):
        if CLASSNAME_RE.search(lines[j]):
            return has_num(lines[j])
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="§1 原则 4「全站等宽」静态可判性实测（只测量）")
    ap.add_argument("--list", action="store_true", help="列出未覆盖的渲染点")
    args = ap.parse_args()

    files = iter_tsx()
    print("=" * 78)
    print("probe_mono_coverage.py —— §1 原则 4「全站等宽」静态可判性实测（**只测量**）")
    print("=" * 78)
    print(f"语料：{rel(FRONTEND)} 下 {len(files)} 个 .tsx"
          f"（已排除 {', '.join(sorted(SKIP_DIRS))} 与 *.test.*）")
    print(f"回溯窗口：向前 {WINDOW} 行找最近 className")
    print()

    total = {"same": 0, "win": 0, "none": 0}
    per_anchor: dict[tuple[str, str], dict[str, int]] = {}
    uncovered: list[tuple[str, str, str, int, str, str]] = []
    covered_by_anchor_any: dict[str, int] = {}

    for f in files:
        try:
            raw = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = raw.splitlines()
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            # 跳过纯注释行（粗筛，避免把注释里的示例当渲染点）
            if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                continue
            for kind, name, pat in ANCHORS:
                if not pat.search(line):
                    continue
                key = (kind, name)
                st = per_anchor.setdefault(key, {"same": 0, "win": 0, "none": 0})
                if has_num(line):
                    st["same"] += 1
                    total["same"] += 1
                elif nearest_classname_has_num(lines, i):
                    st["win"] += 1
                    total["win"] += 1
                else:
                    st["none"] += 1
                    total["none"] += 1
                    uncovered.append((kind, name, rel(f), i + 1, line.strip()[:120],
                                      bucket(line)))
                covered_by_anchor_any[name] = covered_by_anchor_any.get(name, 0) + 1

    grand = sum(total.values())
    # ⚠️ `grand` 是**锚点命中行次**，同一行命中两个锚点会**重复计数**
    #    （例：`render: (_v,row) => fmtAmount(row.claim_amount)` 同时命中
    #     `fmtAmount(` 与 `claim_amount`）。所以另报一个**去重行数**。
    uniq_uncovered = {(r[2], r[3]) for r in uncovered}
    print("── 总览 ──")
    print(f"  锚点命中（行次）合计        : {grand}")
    print(f"  ① 同行即含 num              : {total['same']}")
    print(f"  ② 同行无、回溯窗口内命中 num : {total['win']}   ⚠️ 这一档**可能误判为覆盖**")
    print(f"  ③ 两者都没有（= 未覆盖）    : {total['none']}"
          f"（**去重后 {len(uniq_uncovered)} 行**，同一行命中多锚点会重复计数）")
    if grand:
        print(f"  ⇒ 覆盖（①+②）: {total['same'] + total['win']}/{grand}"
              f" = {(total['same'] + total['win']) / grand:.1%}")
        print(f"  ⇒ 未覆盖     : {total['none']}/{grand} = {total['none'] / grand:.1%}")
    print()

    print("── 按锚点拆开（kind / anchor / 同行 / 窗口 / 未覆盖）──")
    for (kind, name), st in sorted(per_anchor.items(), key=lambda kv: (-kv[1]["none"], kv[0])):
        print(f"  {kind}  {name:<16} {st['same']:>4} {st['win']:>5} {st['none']:>6}")
    print()

    # ── 形状分桶：这一步才把「稳定假红」从「疑似真缺陷」里分出来 ──────────────
    by_bucket: dict[str, list[tuple[str, str, str, int, str, str]]] = {}
    for row in uncovered:
        by_bucket.setdefault(row[5], []).append(row)

    print("── 🚨 未覆盖点**按行形状分桶**（决定「写不写门禁」的就是这张表）──")
    print(f"  {'桶':<9} {'条数':>5}   说明")
    for b in ("jsx", "other", "logic", "prop", "def", "type", "key", "comment"):
        if b in by_bucket:
            print(f"  {b:<9} {len(by_bucket[b]):>5}   {BUCKET_LABEL[b]}")
    # 假红桶 = 那些「位置本身不存在加不加等宽这回事」的
    fake_buckets = ("def", "type", "key", "comment")
    n_fake = sum(len(by_bucket.get(b, [])) for b in fake_buckets)
    n_real = len(uncovered) - n_fake
    print()
    print(f"  ⇒ **确定假红**（def/type/key/comment 四桶，锚点不在渲染位）: {n_fake}/{len(uncovered)}")
    print(f"  ⇒ **需人工分诊**（其余桶）: {n_real}/{len(uncovered)}"
          f" —— 这才是「全站等宽」静态判据的**真实噪声底**")
    print()

    # 分布：未覆盖点落在哪些文件（看是否集中在少数几个文件 ⇒ 可能是一类系统性写法）
    if uncovered:
        by_file: dict[str, int] = {}
        for _k, _n, fp, _ln, _t, _b in uncovered:
            by_file[fp] = by_file.get(fp, 0) + 1
        print(f"── 未覆盖点分布（共 {len(uncovered)} 条，落在 {len(by_file)} 个文件）──")
        for fp, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:20]:
            print(f"  {n:>3}  {fp}")
        print()

    if args.list:
        # 先列「需人工分诊」的桶（jsx/other/prop/logic），假红桶放最后
        order = ["jsx", "other", "prop", "logic", "def", "type", "key", "comment"]
        rows = sorted(uncovered, key=lambda r: order.index(r[5]) if r[5] in order else 99)
        print(f"── 未覆盖明细（按桶排序，最多 {MAX_LIST} 条）──")
        shown = 0
        for kind, name, fp, ln, txt, b in rows:
            if shown >= MAX_LIST:
                print(f"  …（还有 {len(rows) - shown} 条，见 --list 全量）")
                break
            print(f"  [{b}/{kind}/{name}] {fp}:{ln}")
            print(f"        {txt}")
            shown += 1
        print()

    print("── 读数须知（别把下面三条读反）──")
    print("  · ② 档让结果**偏乐观**（外层容器的 num 被算到内层头上）⇒ 真实未覆盖率 **≥** ③ 的数字。")
    print("  · ③ 档**不等于缺陷**：要先分桶，只有 `jsx`/`other` 桶才可能是真渲染点。")
    print("  · `prop` 桶要**单独看**：`<KpiCard value={yuan(...)}>` 的等宽是**组件内部**加的")
    print("    ⇒ 按「调用点必须写 num」判，这一桶**全是假红**。")
    print("  · 「条款号」四类里唯一**没有稳定标识符**的 ⇒ 本遍**没测**（本身就是一条缺口）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
