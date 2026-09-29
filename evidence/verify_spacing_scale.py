#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§4.2「间距（4pt 网格）」门禁 —— 游离值 / 任意值间距。

## 为什么这个维度之前没有门禁

第九轮覆盖率审计逐节枚举 `design-spec.md` 时发现：§4.2 有**完整刻度 + 一条明确的禁令**
（`§4.2「禁止出现 5px / 13px / 18px 等游离值」`「禁止出现 **5px / 13px / 18px** 等游离值」），但
`grep -E "间距|游离值|4pt" evidence/verify_*.py` **只命中注释**，没有任何判据。

## 出处（刻度与禁令**从规范文件解析**，不硬编码）

| 判据 | 内容 | 出处 |
|---|---|---|
| **S1** | 任意值间距（`p-[13px]` / `gap-[7px]`）**必须是令牌引用**（`var(...)` / `calc(var(...))`） | §4.2「刻度：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80」 刻度 + §4.2「禁止出现 5px / 13px / 18px 等游离值」 禁令 ⇒ 手写魔数就是「游离值」 |
| **S2** | **间距属性**上出现规范**点名禁止**的值（5 / 13 / 18px）⇒ 缺陷 | §4.2「禁止出现 5px / 13px / 18px 等游离值」「禁止出现 5px / 13px / 18px 等游离值」 |
| **S3** | **定位属性**（`top/left/…`）上出现游离值 ⇒ **只报不判** | ✅ **规范已表态**：§4.2 于 2026-09-26 补「**范围界定** —— 游离值指**间距**，装饰性定位偏移**不在此列**」（§9 第 24 项裁定 ②）⇒ S3 属**规范外**信息，**不再是被裁决项** |
| **S4** | 命名档里**离 4pt 网格**的取值（2 / 10 / 14 / 28 / 44 / 56）⇒ **只报不判** | 见下「规范内部 tension」 |

## 🚨 判据设计的坑

1. **刻度必须从 `design-spec.md` 解析**，不能硬编码 —— 改规格时门禁要跟着变。
   第一版我自己手写了一份刻度（还把 `28/44/56` 当成合法），**那是我的刻度，不是规范的**。
2. 🚨 **「命名档」与「任意值」是两条不同的路径，只查一条会漏**：
   第一版只扫 `p-*` / `m-*` / `gap-*` 这类**命名档** ⇒ 报「规范点名的 5px：**0 处**」。
   补上**任意值**路径后才发现 `top-[5px]` **真的存在**（`lawyer/cases/[id]:1272`）。
   ⇒ **「0 处」在下结论前必须确认：我把所有写法都扫了吗？**
3. **`gap == 0` 与 `gap == 2` 性质不同**（相邻间距那条判据的坑，见 S3 说明）：
   0 通常是**一个复合控件的内部**（分段控件 / 标签栏 / 页码），
   2–6px 才是规范说的「误触」风险。只量「< 8px」会把前者淹没后者。
4. **规范内部 tension（只报不判）**：刻度是 **4pt**（§4.2「刻度：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80」），但同一节 §4.2「图标与文本间距 6–8px」 又**明文允许
   6px**（「图标与文本间距 6–8px」）—— 6 不是 4 的倍数。而 Tailwind 的**默认间距刻度是 2pt**
   （`0.5`=2 / `1.5`=6 / `2.5`=10 / `3.5`=14 / `7`=28 / `11`=44 / `14`=56），
   命名档里的半档**都来自它**。⇒ 「等游离值」若按字面推广，会把 400+ 处 Tailwind 半档
   全判成缺陷 —— **那是拿规范的字面去否定规范自己允许的 6px**。⇒ S4 **只报不判**。

用法：
    python evidence/verify_spacing_scale.py --self-test     # 纯函数自测，不起浏览器
    python evidence/verify_spacing_scale.py --source-only   # 只扫源码层（秒级，默认）
    python evidence/verify_spacing_scale.py --why           # 打出处 + 从规范解析出的刻度
    python evidence/verify_spacing_scale.py --dump          # 打印原始命中

退出码：0 = 通过；1 = 产品缺陷；2 = 环境问题
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[0]
FE = ROOT / "frontend"
SPEC = ROOT / "deliverables" / "ui-design" / "design-spec.md"

#: 间距属性（§4.2 讲的就是这些）
SPACING_PROPS = ("px", "py", "pt", "pb", "pl", "pr", "p",
                 "mx", "my", "mt", "mb", "ml", "mr", "m",
                 "gap-x", "gap-y", "gap", "space-x", "space-y")
#: 定位属性（S3：只报不判）
POSITION_PROPS = ("inset-x", "inset-y", "inset", "top", "left", "right", "bottom")

#: Tailwind 默认命名间距档 → px（**这是 Tailwind 的刻度，不是规范的**）
NAMED_PX: dict[str, float] = {
    "0": 0.0, "0.5": 2.0, "1": 4.0, "1.5": 6.0, "2": 8.0, "2.5": 10.0,
    "3": 12.0, "3.5": 14.0, "4": 16.0, "5": 20.0, "6": 24.0, "7": 28.0,
    "8": 32.0, "9": 36.0, "10": 40.0, "11": 44.0, "12": 48.0, "14": 56.0,
    "16": 64.0, "20": 80.0, "24": 96.0,
}

PREVIEW_MARKER = "components-preview"


def _alt(props: tuple[str, ...]) -> str:
    return "|".join(sorted(props, key=len, reverse=True))


NAMED_RE = re.compile(rf"(?<![\w-])(-?)({_alt(SPACING_PROPS + POSITION_PROPS)})-"
                      r"(\d+(?:\.\d+)?)\b")
ANY_RE = re.compile(rf"(?<![\w-])(-?)({_alt(SPACING_PROPS + POSITION_PROPS)})-"
                    r"\[([^\]]+)\]")


# ─────────────────────── 从规范解析（期望值的出处） ───────────────────────

def parse_spec_scale(spec_src: str) -> set[float]:
    """`§4.2「刻度：4 / 8 / 12 / 16 / 20 / 24」`「刻度：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80」"""
    m = re.search(r"刻度[：:]\s*([0-9][0-9\s/]*)", spec_src)
    if not m:
        return set()
    return {float(x) for x in re.findall(r"\d+(?:\.\d+)?", m.group(1))}


def parse_spec_forbidden(spec_src: str) -> set[float]:
    """`§4.2「禁止出现 5px / 13px / 18px 等游离值」`「禁止出现 5px / 13px / 18px 等游离值」"""
    m = re.search(r"禁止出现([^。\n]*)", spec_src)
    if not m:
        return set()
    return {float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*px", m.group(1))}


def parse_spec_icon_text_band(spec_src: str) -> set[float]:
    """`§4.2「图标与文本间距 6–8px」`「图标与文本间距 6–8px」⇒ 该区间内的整数值被**明文允许**。

    ⚠️ 源码看不出「这一处 gap 是不是图标与文本」⇒ 只能整段放行。这是**已声明的放宽**。
    """
    m = re.search(r"图标与文本间距\s*(\d+)\s*[–\-~]\s*(\d+)\s*px", spec_src)
    if not m:
        return set()
    lo, hi = int(m.group(1)), int(m.group(2))
    return {float(v) for v in range(lo, hi + 1)}


# ─────────────────────────── 纯函数判据 ───────────────────────────

def judge_named(prop: str, raw_key: str, scale: set[float],
                forbidden: set[float], allowed_extra: set[float]
                ) -> tuple[str, str]:
    """命名档（`p-2.5` / `gap-1.5`）。返回 `(级别, 说明)`。"""
    px = NAMED_PX.get(raw_key)
    if px is None:
        return ("ok", "")
    if px == 0 or px in scale or px in allowed_extra:
        return ("ok", "")
    if px in forbidden:
        return ("red", f"`{prop}-{raw_key}` = **{px:g}px**，"
                       f"而规范**点名禁止**该值（§4.2「禁止出现 5px / 13px / 18px 等游离值」）")
    return ("note", f"`{prop}-{raw_key}` = {px:g}px 不在 §4.2 的 4pt 刻度上"
                    f"（Tailwind 默认刻度是 **2pt**，半档都来自它）⇒ 规范内部 tension，只报不判")


def judge_any(prop: str, inner: str, forbidden: set[float]) -> tuple[str, str]:
    """任意值（`p-[13px]` / `pb-[var(--safe-bottom)]`）。"""
    if "var(" in inner or "calc(" in inner or "env(" in inner:
        return ("ok", "")                       # 令牌引用 ⇒ 合法
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", inner.strip())
    if not m:
        return ("ok", "")                       # auto / 100% / clamp(...) 等，不在本判据范围
    px = float(m.group(1))
    if px in forbidden:
        return ("red", f"`{prop}-[{inner}]` 是**手写魔数**且 {px:g}px 被规范**点名禁止**"
                       f"（§4.2）")
    return ("red", f"`{prop}-[{inner}]` 是**手写魔数**（非令牌引用）"
                   f"⇒ 即使 {px:g}px 未被点名，也属 §4.2 说的「游离值」")


# ─────────────────────────── 源码层扫描 ───────────────────────────

def collect_tsx(root: pathlib.Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for pat in ("apps/**/*.tsx", "packages/**/*.tsx"):
        for p in sorted(root.glob(pat)):
            if "node_modules" in p.parts or ".next" in p.parts:
                continue
            try:
                out.append((str(p.relative_to(root)).replace("\\", "/"),
                            p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


def scan(files: list[tuple[str, str]]) -> list[dict]:
    hits: list[dict] = []
    for rel, src in files:
        for i, line in enumerate(src.splitlines(), 1):
            for m in NAMED_RE.finditer(line):
                hits.append({"kind": "named", "path": rel, "line": i,
                             "prop": m.group(2), "key": m.group(3),
                             "raw": m.group(0),
                             "preview": PREVIEW_MARKER in rel})
            for m in ANY_RE.finditer(line):
                hits.append({"kind": "any", "path": rel, "line": i,
                             "prop": m.group(2), "inner": m.group(3),
                             "raw": m.group(0),
                             "preview": PREVIEW_MARKER in rel})
    return hits


def run_source(files: list[tuple[str, str]], scale: set[float], forbidden: set[float],
               allowed_extra: set[float], dump: bool = False
               ) -> tuple[list[tuple[str, str, str]], list[str], list[str]]:
    hits = scan(files)
    bad: list[tuple[str, str, str]] = []
    notes: list[str] = []
    stats: list[str] = []
    #: S4 命名半档按 px 值聚合 —— 265 处逐条刷屏没有信息量，根因只有一条。
    off_by_px: dict[float, list[str]] = {}

    for h in hits:
        prop = h["prop"]
        is_pos = prop in POSITION_PROPS
        if h["kind"] == "named":
            lvl, msg = judge_named(prop, h["key"], scale, forbidden, allowed_extra)
            crit = "S4" if lvl == "note" else "S2"
        else:
            lvl, msg = judge_any(prop, h["inner"], forbidden)
            crit = "S1"
        if is_pos:
            crit = "S3"                       # 定位属性一律只报（§4.2 已明文排除：游离值指间距）
        if lvl == "ok":
            continue

        loc = f"{h['path']}:{h['line']}"
        if is_pos or h["preview"]:
            tag = "预览页·不计缺陷" if h["preview"] else "定位属性·只报不判"
            notes.append(f"[{crit}·{tag}] {loc}  {msg}")
        elif lvl == "note":
            off_by_px.setdefault(NAMED_PX.get(h["key"], 0), []).append(loc)
        else:
            bad.append((loc, crit, msg))

    named_hits = [h for h in hits if h["kind"] == "named"]
    any_hits = [h for h in hits if h["kind"] == "any"]
    stats.append(f"命名档命中 {len(named_hits)} 处 · 任意值命中 {len(any_hits)} 处"
                 f"（其中 {sum(1 for h in any_hits if h['prop'] in POSITION_PROPS)} 处是定位属性）")
    if off_by_px:
        tot = sum(len(v) for v in off_by_px.values())
        stats.append(f"S4 命名半档离 4pt 网格：{tot} 处（**只报不判**）按取值聚合 ——")
        for px in sorted(off_by_px):
            stats.append(f"    {px:>4g}px  {len(off_by_px[px]):>4} 处")
        stats.append("    ⇒ 根因：Tailwind 默认间距刻度是 **2pt**，而规范要 **4pt**；"
                     "且规范 :200 自己又明文允许 6px（半档）⇒ **规范内部 tension**")
    if dump:
        print("\n" + json.dumps(hits, ensure_ascii=False, indent=2))
    return bad, notes, stats


# ─────────────────────────── 自测 ───────────────────────────

FIX_SCALE = {4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 32.0, 40.0, 48.0, 64.0, 80.0}
FIX_FORBIDDEN = {5.0, 13.0, 18.0}
FIX_ALLOWED = {6.0, 7.0, 8.0}

SPEC_SNIPPET = """
### 4.2 间距（4pt 网格）

刻度：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80

- 桌面页面内边距 24px；**移动端 16px**
- 图标与文本间距 6–8px
- 禁止出现 5px / 13px / 18px 等游离值
"""


def self_test() -> int:
    n = 0
    fails: list[str] = []

    def arm(name: str, got, want) -> None:
        nonlocal n
        n += 1
        if got != want:
            fails.append(f"{name}: got={got!r} want={want!r}")

    # ── 从规范解析（期望值必须来自出处）──
    sc = parse_spec_scale(SPEC_SNIPPET)
    arm("解析刻度：11 个值", len(sc), 11)
    arm("刻度含 4/8/12", {4.0, 8.0, 12.0} <= sc, True)
    arm("刻度含 80", 80.0 in sc, True)
    # ⚠️ 第一版我自己手写刻度时把 28/44/56 当成合法 —— 规范里**没有**它们
    arm("刻度**不含** 28（不是 4 的倍数… 是 4 的倍数但规范没列）", 28.0 in sc, False)
    arm("刻度不含 36", 36.0 in sc, False)
    fb = parse_spec_forbidden(SPEC_SNIPPET)
    arm("解析禁令：{5,13,18}", fb, {5.0, 13.0, 18.0})
    band = parse_spec_icon_text_band(SPEC_SNIPPET)
    arm("解析图标文本区间：{6,7,8}", band, {6.0, 7.0, 8.0})

    # ── S1：任意值必须是令牌引用 ──
    lvl, msg = judge_any("p", "13px", FIX_FORBIDDEN)
    arm("S1-a p-[13px] ⇒ 红", lvl, "red")
    arm("S1-a2 说明里点名禁令", "点名禁止" in msg, True)
    lvl, msg = judge_any("p", "7px", FIX_FORBIDDEN)
    arm("S1-b p-[7px] ⇒ 红（手写魔数，未被点名也算游离值）", lvl, "red")
    arm("S1-b2 说明里说「手写魔数」", "手写魔数" in msg, True)
    lvl, _ = judge_any("pb", "var(--safe-bottom)", FIX_FORBIDDEN)
    arm("S1-c pb-[var(--safe-bottom)] ⇒ ok", lvl, "ok")
    lvl, _ = judge_any("top", "calc(var(--topbar-h)-4px)", FIX_FORBIDDEN)
    arm("S1-d calc(var(...)) ⇒ ok", lvl, "ok")
    lvl, _ = judge_any("p", "auto", FIX_FORBIDDEN)
    arm("S1-e p-[auto] ⇒ ok（不在判据范围）", lvl, "ok")

    # ── S2：命名档上规范点名的值 ──
    # ⚠️ Tailwind 命名档里**没有** 5 / 13 / 18px ⇒ 这一支在命名档上恒为空，
    #    真正的 S2 命中只在任意值路径（见 S1-a）。这本身是「两条路径」的证明。
    #    ⚠️ 注意判的是**取值**不是**键名**：Tailwind 的键 `5` = **20px**（第一版判错了）。
    _vals = set(NAMED_PX.values())
    arm("S2-a 命名档的取值里没有 5px", 5.0 in _vals, False)
    arm("S2-b 命名档的取值里没有 13px", 13.0 in _vals, False)
    arm("S2-c 命名档有 2.5=10px", NAMED_PX.get("2.5"), 10.0)
    arm("S2-d 键 `5` 其实是 20px（键名≠取值）", NAMED_PX.get("5"), 20.0)

    # ── 命名档判定 ──
    arm("N-a p-1 (4px) ⇒ ok", judge_named("p", "1", FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)[0], "ok")
    arm("N-b gap-1.5 (6px) ⇒ ok（§4.2:200 明文允许）",
        judge_named("gap", "1.5", FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)[0], "ok")
    arm("N-c p-2.5 (10px) ⇒ note（只报不判）",
        judge_named("p", "2.5", FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)[0], "note")
    arm("N-d p-7 (28px) ⇒ note", judge_named("p", "7", FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)[0], "note")
    arm("N-e p-0 (0px) ⇒ ok", judge_named("p", "0", FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)[0], "ok")
    arm("N-f p-4 (16px) ⇒ ok", judge_named("p", "4", FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)[0], "ok")

    # ── 扫描器：两条路径都要抓到 ──
    files = [("apps/x/page.tsx",
              'a className="p-4 gap-1.5"\n'
              'b className="p-[13px]"\n'
              'c className="pb-[var(--safe-bottom)]"\n'
              'd className="top-[5px]"\n'
              'e className="p-2.5"\n')]
    hits = scan(files)
    named = [h for h in hits if h["kind"] == "named"]
    anyv = [h for h in hits if h["kind"] == "any"]
    arm("扫描：命名档 3 个（p-4 / gap-1.5 / p-2.5）", len(named), 3)
    arm("扫描：任意值 3 个（p-[13px] / pb-[var] / top-[5px]）", len(anyv), 3)
    arm("扫描：抓到 top-[5px]（定位）", any("top" == h["prop"] for h in anyv), True)
    arm("扫描：抓到 p-[13px]", any(h["raw"] == "p-[13px]" for h in anyv), True)
    arm("扫描：var 引用被抓但会判 ok",
        any("var(" in h["inner"] for h in anyv), True)

    # ── 🚨 源码层故障注入：S1/S2 在产品上**全绿**，必须证明它们**本来会红** ──
    #    （「一条只会绿的防线不算防线」；同族见 `verify_font_scale.py --render-self-test`）
    def _run(src: str):
        return run_source([("apps/x/page.tsx", src)], FIX_SCALE, FIX_FORBIDDEN, FIX_ALLOWED)

    bad1, _, _ = _run('a className="p-[13px]"')
    arm("注入 p-[13px] ⇒ 1 条缺陷", len(bad1), 1)
    arm("注入的缺陷判为 S1", bad1[0][1] if bad1 else None, "S1")
    arm("注入 p-[13px] 的说明点名禁令", ("点名禁止" in bad1[0][2]) if bad1 else False, True)
    bad2, _, _ = _run('a className="p-[5px]"')
    arm("注入 p-[5px]（规范点名） ⇒ 1 条缺陷", len(bad2), 1)
    bad2b, _, _ = _run('a className="p-[7px]"')
    arm("注入 p-[7px]（未点名的手写魔数） ⇒ 1 条缺陷", len(bad2b), 1)
    bad3, _, _ = _run('a className="mt-4 gap-2 px-3 py-0.5"')
    arm("对照：合规 + 半档 ⇒ **0** 条缺陷", len(bad3), 0)
    bad4, notes4, _ = _run('a className="top-[5px]"')
    arm("定位 top-[5px] ⇒ 0 条缺陷（只报不判）", len(bad4), 0)
    arm("定位 top-[5px] ⇒ 进 notes 1 条", len(notes4), 1)
    bad5, notes5, _ = _run('a className="pb-[var(--safe-bottom)]"')
    arm("令牌引用 ⇒ 0 缺陷 0 提示", (len(bad5), len(notes5)), (0, 0))

    # ── 控制臂：判据常量 == 规范解析结果（防止我手写的刻度漂移）──
    arm("控制臂：FIX_SCALE == 规范解析刻度", FIX_SCALE, sc)
    arm("控制臂：FIX_FORBIDDEN == 规范解析禁令", FIX_FORBIDDEN, fb)

    print(f"自测 {n - len(fails)}/{n} 通过")
    for f in fails:
        print(f"  ✗ {f}")
    return 0 if not fails else 1


# ─────────────────────────── main ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="§4.2 间距 4pt 门禁")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--why", action="store_true")
    a = ap.parse_args()

    try:
        spec_src = SPEC.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[ENV] 读不到 {SPEC}：{e}")
        return 2
    scale = parse_spec_scale(spec_src)
    forbidden = parse_spec_forbidden(spec_src)
    allowed = parse_spec_icon_text_band(spec_src)
    if not scale or not forbidden:
        print("[ENV] 从 design-spec.md 解析不出刻度 / 禁令（选择器失效？）")
        return 2

    if a.why:
        print(f"出处：{SPEC.relative_to(ROOT).as_posix()} §4.2")
        print(f"  刻度（解析自 :195）：{sorted(scale)}")
        print(f"  点名禁止（解析自 :201）：{sorted(forbidden)}")
        print(f"  明文允许（解析自 :200「图标与文本间距 6–8px」）：{sorted(allowed)}")
        print(f"  ⚠️ 定位属性 {'/'.join(POSITION_PROPS)} ⇒ **只报不判**（§4.2 讲的是间距）")
        print("  ⚠️ 命名半档（Tailwind 2pt 默认刻度）⇒ **只报不判**（规范内部 tension）")
        print(f"  预览页口径：路径含 `{PREVIEW_MARKER}` 的不计产品缺陷")
        return 0

    if a.self_test:
        return self_test()

    print("§4.2「间距（4pt 网格）」门禁")
    print(f"  刻度 {len(scale)} 个值（解析自 §4.2「刻度：4 / 8 / 12 / 16 / 20 / 24」）· "
          f"点名禁止 {sorted(forbidden)}（:201）· 明文允许 {sorted(allowed)}（:200）")
    print("\n── 源码层 S1/S2/S3/S4 ──")
    files = collect_tsx(FE)
    bad, notes, stats = run_source(files, scale, forbidden, allowed, dump=a.dump)
    for s in stats:
        print(f"  {s}")

    if bad:
        groups: dict[tuple[str, str], list[str]] = {}
        for loc, crit, msg in bad:
            groups.setdefault((crit, msg), []).append(loc)
        print(f"\n  ✗ 源码层缺陷 {len(bad)} 条，聚为 {len(groups)} 条根因：")
        for (crit, msg), locs in sorted(groups.items()):
            print(f"    [{crit}] {msg}")
            print(f"          @ {len(set(locs))} 处：{', '.join(sorted(set(locs))[:6])}")
    else:
        print("\n  ✓ 源码层未发现 S1/S2 缺陷")

    if notes:
        print(f"\n  · 只报不判 {len(notes)} 条（**不是缺陷**，供拍板）：")
        for t in notes[:25]:
            print(f"    {t}")
        if len(notes) > 25:
            print(f"    …（另有 {len(notes) - 25} 条同类，见 --dump）")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
