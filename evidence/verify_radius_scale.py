#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§4.3「圆角 / 阴影 / 边框」门禁 —— 圆角阶梯与「废弃 16px 及以上圆角」。

## 为什么要有这一条

`grep -oE '§[0-9]+(\\.[0-9]+)?' evidence/verify_*.py` 与 `design-spec.md` 小节求交后：
**§4.3 只被 `verify_focus_ring.py` 引到「焦点环」那一句**，
而同一节的 **圆角阶梯（`--r1..--r4`）** 与 **明文禁令（「废弃 16px 及以上圆角」）**
**零判据**（`grep -rn 'borderRadius\\|border-radius\\|圆角' evidence/*.py` 只命中
`verify_buttons.py` / `verify_grade_badges.py` **夹具字符串里的** `rounded-r2`，那不是判据）。

它与 **§4.2 间距** 结构同构：**一个令牌阶梯 + 一条明文禁令**。
§4.2 已有 `verify_spacing_scale.py`，§4.3 的圆角部分没有 ⇒ 本节按同一形状补齐。

## 🚨 本条最要紧的纪律：**「类名看起来违规」不等于「值违规」**

第一版我打算判「源码里不得出现 `rounded-2xl` / `rounded-3xl`」。**读 preset 后发现这是错的**：

`tailwind.preset.ts:180-194` 把**存量档位映射到了令牌**：

    sm: var(--r1)   DEFAULT/md: var(--r2)   lg/xl: var(--r3)
    "2xl": var(--r4)   "3xl": var(--r4)      // 注释：「存量档位收敛：卡片 12->8px，模态 16/24->12px」

⇒ `rounded-2xl` 的**解析值是 12px**（不是 Tailwind 默认的 16px）⇒ **12 < 16，合规**。
⇒ **按类名判会对合法代码产生稳定假红**（这正是「判据必须认识项目自己的补偿机制」）。
⇒ 因此本门禁一律 **先解析到最终 px 值，再判**。

## 判据

| 判据 | 内容 | 依据 |
|---|---|---|
| **R0** | **门禁自身的穷尽性守卫**：§4.3 必须解析出 4 行圆角表 + 1 条禁令（含阈值） | 规范结构 |
| **R1** | `tokens.css` 的 `--r1..--r4` **逐条等于** §4.3 表格解析出的值 | §4.3 表 |
| **R2** | **禁用值按解析值判**：所有令牌值 + preset 所有档位解析值必须 **< 阈值** | 「废弃 16px 及以上圆角」 |
| **R3** | 任意值圆角 `rounded-[…]` 必须 **< 阈值**，且**优先是 `var(--r*)`** | §4.2 的 S1 同构 |
| **R4** | preset 的每个圆角档位必须**指向令牌**（`var(--r*)`），不得是裸 px | 「圆角收敛」 |
| **R5** | **只报**：`rounded-full`（规范未表态） | 需拍板 |
| **R6** | **只报**：`--s1..--s3` 与 `boxShadow.s*` 是否成链（「优先 1px 边框而非阴影」是取舍） | §4.3 阴影 |

## 退出码

- `0` 通过（允许出现**已登记**的欠账）
- `1` 发现**新的**违反，或棘轮豁免已过期
- `2` 环境问题（规范 / 令牌 / preset 找不到，或自检失败 ⇒ **工具坏了，不是产品坏了**）

用法：

    python evidence/verify_radius_scale.py
    python evidence/verify_radius_scale.py --self-test
    python evidence/verify_radius_scale.py --why
    python evidence/verify_radius_scale.py --dump
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
TOKENS_REL = "frontend/packages/ui/src/tokens.css"
PRESET_REL = "frontend/tailwind.preset.ts"

# 不在 `apps/` 或 `packages/ui/src/` 任何一棵树里、但判据必需的文件。
EXTRA_RELS: tuple[str, ...] = (PRESET_REL,)

SKIP_DIRS = {"node_modules", ".next", ".next_old_v14_keep", "_prev_build",
             "dist", "build", ".turbo", "__pycache__"}

# ---------------------------------------------------------------------------
# 棘轮：已登记、不阻断 CI，但**不许涨**、**修好必须删**。
# 当前为空 —— 圆角系统已完整迁移（实测 0 处废弃值 / 0 处任意值）。
# ---------------------------------------------------------------------------
KNOWN_GAPS: dict[str, str] = {}

# `rounded-full` 是**明确豁免**（药丸 / 头像 / 开关），不是欠账：
# 规范那句「废弃 16px 及以上圆角」讲的是**卡片/模态那一档**，不是 `full`。
# 它的出现数走 R5「只报」，且**在自检里配了对照臂**（Q7）锁住「不得因此转红」。
FULL_EXEMPT_REASON = (
    "`rounded-full`（9999px）用于药丸 / 头像 / 开关，**规范未点名**。"
    "按字面推广会把 42 处合法用法判成「16px 及以上圆角」= 拿规范的字面否定规范没禁止的东西。"
    "⇒ **只报不判**；建议 §4.3 补一句豁免（规范类改动，待定稿）。"
)


# ===========================================================================
# 解析：规范 §4.3
# ===========================================================================
def load_section(text: str, heading: str, stop: str) -> str | None:
    """取 `heading` 到 `stop` 之间的正文。"""
    m = re.search(r"^###\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    e = re.search(r"^###\s+" + re.escape(stop) + r"\s*$", rest, re.M)
    return rest[: e.start()] if e else rest


RADIUS_ROW_RE = re.compile(r"^\|\s*`(--r[0-9])`\s*\|\s*([0-9.]+)px\s*\|", re.M)
SHADOW_ROW_RE = re.compile(r"^\|\s*`(--s[0-9])`\s*\|\s*`([^`]+)`\s*\|", re.M)
# 「废弃 16px 及以上圆角。」⇒ 阈值 = 16
BAN_RE = re.compile(r"废弃\s*([0-9]+)\s*px\s*及以上圆角")


def parse_spec(section: str) -> tuple[dict[str, float], dict[str, str], float | None]:
    radii = {k: float(v) for k, v in RADIUS_ROW_RE.findall(section)}
    shadows = {k: v for k, v in SHADOW_ROW_RE.findall(section)}
    m = BAN_RE.search(section)
    threshold = float(m.group(1)) if m else None
    return radii, shadows, threshold


# ===========================================================================
# 解析：令牌与 preset
# ===========================================================================
TOKEN_RE = re.compile(r"^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);", re.M)
VAR_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*\)")
PX_RE = re.compile(r"([0-9.]+)px")


def parse_tokens(text: str) -> dict[str, str]:
    return {k: v.strip() for k, v in TOKEN_RE.findall(text)}


def resolve_px(value: str, tokens: dict[str, str], depth: int = 0) -> float | None:
    """把 `var(--r4)` / `12px` / `calc(var(--r4))` 解析成 px 数值。

    🚨 **这是本门禁的核心**：判据判的是**解析后的值**，不是类名。
    返回 `None` 表示**解析不出来**（不是「合规」）——调用方必须区别对待。
    """
    if depth > 8:
        return None
    v = value.strip()
    m = VAR_RE.search(v)
    if m:
        raw = tokens.get(m.group(1))
        if raw is None:
            return None
        return resolve_px(raw, tokens, depth + 1)
    m = PX_RE.search(v)
    if m:
        return float(m.group(1))
    if re.fullmatch(r"0", v):
        return 0.0
    return None


def parse_preset_radius(text: str) -> dict[str, str]:
    """解析 `borderRadius: { r1: "var(--r1)", "2xl": "var(--r4)", … }`。"""
    i = text.find("borderRadius")
    if i < 0:
        return {}
    j = text.find("{", i)
    depth = 0
    end = len(text)
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    body = text[j + 1: end]
    out: dict[str, str] = {}
    for m in re.finditer(r'^\s*(?:"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))\s*:\s*"([^"]+)"', body, re.M):
        out[m.group(1) or m.group(2)] = m.group(3)
    return out


def parse_preset_shadow(text: str) -> dict[str, str]:
    i = text.find("boxShadow")
    if i < 0:
        return {}
    j = text.find("{", i)
    depth = 0
    end = len(text)
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                end = k
                break
    body = text[j + 1: end]
    out: dict[str, str] = {}
    for m in re.finditer(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]+)"', body, re.M):
        out[m.group(1)] = m.group(2)
    return out


# ===========================================================================
# 扫描：前端源码
# ===========================================================================
def iter_sources() -> list[tuple[str, str]]:
    """产品页 + 组件库源码 + **根级配置文件**。

    🚨 **必须 `os.walk` 就地剪枝**（`rglob` 会走完四份 `node_modules`，实测 64.6s ⇒ 0.8s）。
    ⚠️ 路径用**仓库相对**；末尾 `sorted` 保确定性。
    🚨 **`EXTRA_RELS` 不可省**：`tailwind.preset.ts` 在 `frontend/` **根下**，
    **不在** `apps/` 或 `packages/ui/src/` 任何一棵树里。
    漏掉它的后果不是报错，而是 `preset_radius` 变成**空字典** ⇒
    R2/R4 **空判据恒绿**（**假绿**）—— 第一版正是这样，靠 `REQUIRED_FILES` 守卫当场拦下。
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
    # 根级配置：不在上面任何一棵树里，必须显式补进来。
    for rel in EXTRA_RELS:
        p = ROOT / rel
        if p.exists():
            try:
                out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    out.sort(key=lambda kv: kv[0])
    return out


# `rounded-<档>` / `rounded-t-<档>` / `rounded-[…]`；前瞻避免吃掉 `rounded-full` 之外的词。
CLASS_RE = re.compile(
    r"\brounded(?:-[a-z]{1,2})?-"
    r"(?P<val>\[[^\]]+\]|[A-Za-z][A-Za-z0-9]*)"
)


def scan_radius_classes(files: list[tuple[str, str]]) -> dict[str, list[tuple[str, int]]]:
    """统计源码里 `rounded-*` 的取值 → [(文件, 行号)]。"""
    out: dict[str, list[tuple[str, int]]] = {}
    for rel, text in files:
        for m in CLASS_RE.finditer(text):
            val = m.group("val")
            line_no = text.count("\n", 0, m.start()) + 1
            out.setdefault(val, []).append((rel, line_no))
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
    """棘轮两条相反的不等式（抽成纯函数 ⇒ 可自检）。"""
    known_hits = [(k, m) for k, m in fail if k in known]
    new_hits = [(k, m) for k, m in fail if k not in known]
    expired = [k for k in known if not any(k == fk for fk, _ in fail)]
    return known_hits, new_hits, expired


def judge_r0(spec_radii, spec_shadows, threshold, rep: Report) -> None:
    """门禁自身的穷尽性守卫。"""
    if len(spec_radii) != 4:
        rep.add_fail("R0:圆角表",
                     f"§4.3 应解析出 4 行圆角表，实测 {len(spec_radii)} 行：{spec_radii} "
                     "⇒ 规范可能改过，请复核本门禁")
    if len(spec_shadows) != 3:
        rep.add_fail("R0:阴影表",
                     f"§4.3 应解析出 3 行阴影表，实测 {len(spec_shadows)} 行 ⇒ 请复核")
    if threshold is None:
        rep.add_fail("R0:禁令",
                     "§4.3 解析不出「废弃 N px 及以上圆角」⇒ 阈值来源断了，"
                     "**本门禁不能硬编码一个自己编的阈值**")


def judge_r1(spec_radii: dict[str, float], tokens: dict[str, str], rep: Report) -> None:
    """R1：令牌值必须逐条等于规范表。"""
    for tok, want in sorted(spec_radii.items()):
        raw = tokens.get(tok)
        if raw is None:
            rep.add_fail("R1:缺令牌", f"§4.3 点名 `{tok}` = {want:g}px，但 `tokens.css` 里没有它")
            continue
        got = resolve_px(raw, tokens)
        if got is None:
            rep.add_fail("R1:解析失败", f"`{tok}: {raw}` 解析不出 px ⇒ 判不了（不是合规）")
        elif abs(got - want) > 0.01:
            rep.add_fail("R1:值不符", f"`{tok}` 规范 {want:g}px，实测 {got:g}px（`{raw}`）")
    if not any(k.startswith("R1") for k, _ in rep.fail):
        rep.add_ok(f"R1 圆角令牌 == §4.3 表：{ {k: v for k, v in sorted(spec_radii.items())} }")


def judge_r2(spec_radii, tokens, preset_radius, threshold, rep: Report) -> None:
    """R2：**按解析值**判禁用阈值（令牌 + preset 档位）。"""
    if threshold is None:
        return
    checked = 0
    for tok in sorted(spec_radii):
        raw = tokens.get(tok)
        if raw is None:
            continue
        got = resolve_px(raw, tokens)
        checked += 1
        if got is not None and got >= threshold:
            rep.add_fail("R2:令牌越界", f"令牌 `{tok}` = {got:g}px ≥ 废弃阈值 {threshold:g}px")
    for name, val in sorted(preset_radius.items()):
        got = resolve_px(val, tokens)
        checked += 1
        if got is None:
            rep.add_fail("R2:档位解析失败",
                         f"preset 圆角档位 `{name}: {val}` 解析不出 px ⇒ 判不了")
        elif got >= threshold:
            rep.add_fail("R2:档位越界",
                         f"preset 档位 `{name}` 解析值 {got:g}px ≥ 废弃阈值 {threshold:g}px")
    if not any(k.startswith("R2") for k, _ in rep.fail):
        rep.add_ok(f"R2 无 ≥{threshold:g}px 的圆角（**按解析值判**：令牌 {checked} 项，"
                   f"含存量档位映射如 `2xl→{preset_radius.get('2xl', '?')}`）")


def judge_r3(classes, tokens, threshold, rep: Report) -> None:
    """R3：任意值圆角必须 < 阈值，且优先是令牌引用。"""
    arbitrary = {k: v for k, v in classes.items() if k.startswith("[")}
    if not arbitrary:
        rep.add_ok("R3 任意值圆角：0 处（全部走命名档）")
        return
    for cls, where in sorted(arbitrary.items()):
        inner = cls[1:-1]
        got = resolve_px(inner, tokens)
        f, ln = where[0]
        if got is None:
            rep.add_fail("R3:解析失败", f"`rounded-{cls}`（{f}:{ln}）解析不出 px ⇒ 判不了")
        elif threshold is not None and got >= threshold:
            rep.add_fail("R3:越界",
                         f"`rounded-{cls}`（{f}:{ln}）= {got:g}px ≥ {threshold:g}px")
        elif not VAR_RE.search(inner):
            rep.add_note(f"R3 任意值 `rounded-{cls}`（{f}:{ln}）用的是裸 px，"
                         "建议改令牌引用（与 §4.2 的 S1 同构；规范未强制，**只报**）")


def judge_r4(preset_radius, tokens, rep: Report) -> None:
    """R4：preset 档位必须指向令牌。"""
    bad = []
    for name, val in sorted(preset_radius.items()):
        if not VAR_RE.search(val):
            bad.append((name, val))
    if bad:
        for name, val in bad:
            rep.add_fail("R4:裸 px 档位",
                         f"preset 圆角档位 `{name}: {val}` 不是令牌引用 ⇒ "
                         "「圆角收敛」断了（档位可被单独改掉而不动令牌）")
    else:
        rep.add_ok(f"R4 preset 的 {len(preset_radius)} 个圆角档位全部指向令牌（含存量档位收敛）")


def judge_r5(classes, rep: Report, dump: bool) -> None:
    """R5（只报）：`rounded-full`。"""
    full = classes.get("full", [])
    if full:
        rep.add_note(f"R5 `rounded-full` 出现 {len(full)} 处（如 {full[0][0]}:{full[0][1]}）。"
                     f"{FULL_EXEMPT_REASON}")


def judge_r6(tokens, preset_shadow, spec_shadows, rep: Report) -> None:
    """R6（只报）：阴影令牌链。"""
    missing_tok = [k for k in sorted(spec_shadows) if k not in tokens]
    missing_preset = [k for k in sorted(spec_shadows)
                      if k.lstrip("-") not in preset_shadow]
    parts = [f"R6 §4.3 阴影令牌链：规范 {len(spec_shadows)} 条"]
    parts.append(f"`tokens.css` 缺 {missing_tok}" if missing_tok else "令牌齐")
    parts.append(f"`preset.boxShadow` 缺 {missing_preset}" if missing_preset
                 else "preset 齐")
    parts.append("「优先 1px 边框而非阴影」是设计取舍，**不判**")
    rep.add_note("；".join(parts) + "。")


# ===========================================================================
# 自检（纯函数：不读真实仓库、不启浏览器 ⇒ 可进 SELFTESTABLE）
# ===========================================================================
SYN_SPEC = """## 04 布局 · 间距 · 圆角 · 阴影

### 4.3 圆角 / 阴影 / 边框

| Token | 值 | 用途 |
|---|---|---|
| `--r1` | 4px | 徽章、标签、小按钮 |
| `--r2` | 6px | 按钮、输入框、列表项 |
| `--r3` | 8px | 卡片、面板、表格容器 |
| `--r4` | 12px | 模态、抽屉、输入区大框 |

废弃 16px 及以上圆角。

| Token | 值 | 用途 |
|---|---|---|
| `--s1` | `0 1px 2px rgba(16,24,40,.06)` | 卡片静置 |
| `--s2` | `0 4px 12px -2px rgba(16,24,40,.10)` | hover、下拉 |
| `--s3` | `0 16px 40px -8px rgba(16,24,40,.18)` | 模态、抽屉 |

### 4.4 动效
"""

SYN_TOKENS = """--r1: 4px; /* 徽章 */
--r2: 6px; /* 按钮 */
--r3: 8px; /* 卡片 */
--r4: 12px; /* 模态 */
--s1: 0 1px 2px rgba(16,24,40,.06);
--s2: 0 4px 12px -2px rgba(16,24,40,.10);
--s3: 0 16px 40px -8px rgba(16,24,40,.18);
"""

SYN_PRESET = """      borderRadius: {
        r1: "var(--r1)",
        r2: "var(--r2)",
        r3: "var(--r3)",
        r4: "var(--r4)",
        sm: "var(--r1)",
        DEFAULT: "var(--r2)",
        "2xl": "var(--r4)",
        "3xl": "var(--r4)",
      },
      boxShadow: {
        s1: "var(--s1)",
        s2: "var(--s2)",
        s3: "var(--s3)",
      },
"""


def _spec_parts():
    sec = load_section(SYN_SPEC, "4.3 圆角 / 阴影 / 边框", "4.4 动效")
    assert sec is not None
    return parse_spec(sec)


def _run(files=None, spec=SYN_SPEC, tokens_src=SYN_TOKENS, preset_src=SYN_PRESET):
    sec = load_section(spec, "4.3 圆角 / 阴影 / 边框", "4.4 动效") or ""
    radii, shadows, thr = parse_spec(sec)
    tokens = parse_tokens(tokens_src)
    preset_r = parse_preset_radius(preset_src)
    preset_s = parse_preset_shadow(preset_src)
    classes = scan_radius_classes(files or [])
    rep = Report()
    judge_r0(radii, shadows, thr, rep)
    judge_r1(radii, tokens, rep)
    judge_r2(radii, tokens, preset_r, thr, rep)
    judge_r3(classes, tokens, thr, rep)
    judge_r4(preset_r, tokens, rep)
    judge_r5(classes, rep, False)
    judge_r6(tokens, preset_s, shadows, rep)
    return rep


def self_test() -> int:
    print("=== §4.3 圆角门禁 · 自检（纯函数，不读真实仓库） ===")
    arms: list[tuple[str, bool, str]] = []

    radii, shadows, thr = _spec_parts()
    arms.append(("Q0 解析出 4 行圆角表", len(radii) == 4, f"{radii}"))
    arms.append(("Q0 解析出 3 行阴影表", len(shadows) == 3, f"{len(shadows)}"))
    arms.append(("Q0 禁令阈值 == 16", thr == 16.0, f"{thr}"))

    # Q1 干净对照
    r = _run(files=[("a.tsx", '<div className="rounded-r2 rounded-r3" />')])
    arms.append(("Q1 干净对照必须无 fail", not r.fail, f"{[k for k, _ in r.fail]}"))

    # Q2 令牌值不符 ⇒ 必须红
    r = _run(tokens_src=SYN_TOKENS.replace("--r3: 8px", "--r3: 10px"))
    arms.append(("Q2 令牌值漂移必须红", any(k.startswith("R1:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q3 令牌越界（≥16px）⇒ 必须红
    r = _run(tokens_src=SYN_TOKENS.replace("--r4: 12px", "--r4: 20px"))
    arms.append(("Q3 令牌 ≥16px 必须红", any(k.startswith("R2:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q4 🚨 **关键对照**：preset 把 `2xl` 映射到 `var(--r4)`=12px ⇒ **不得红**
    #    （这锁住「按解析值判、不按类名判」这条纪律）
    r = _run(files=[("a.tsx", '<div className="rounded-2xl rounded-3xl" />')])
    arms.append(("Q4 存量档位映射到令牌不得红",
                 not any(k.startswith("R2") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q5 preset 档位指向 20px ⇒ 必须红（档位越界，即使类名是 `2xl`）
    r = _run(preset_src=SYN_PRESET.replace('"2xl": "var(--r4)"', '"2xl": "20px"'))
    arms.append(("Q5 档位越界必须红", any(k.startswith("R2:档位") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q6 任意值 ≥16px ⇒ 必须红
    r = _run(files=[("a.tsx", '<div className="rounded-[20px]" />')])
    arms.append(("Q6 任意值越界必须红", any(k.startswith("R3:越界") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q7 🚨 假阳性对照：`rounded-full` 不得因此转红（只报）
    r = _run(files=[("a.tsx", '<div className="rounded-full" />')])
    arms.append(("Q7 rounded-full 不得红（只报）",
                 not r.fail and any("rounded-full" in n for n in r.notes),
                 f"fail={[k for k, _ in r.fail]} notes={len(r.notes)}"))

    # Q8 假阳性对照：非圆角类（`border` / `rounded` 裸词）不得被当成圆角档位
    r = _run(files=[("a.tsx", '<div className="border rounded p-4" />')])
    arms.append(("Q8 非圆角类不得误报", not r.fail, f"{[k for k, _ in r.fail]}"))

    # Q9 preset 档位写成裸 px ⇒ R4 必须红
    r = _run(preset_src=SYN_PRESET.replace('r1: "var(--r1)"', 'r1: "4px"'))
    arms.append(("Q9 档位裸 px 必须红", any(k.startswith("R4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q10 规范圆角表只剩 3 行 ⇒ R0 必须红（门禁自身守卫）
    r = _run(spec=SYN_SPEC.replace("| `--r4` | 12px | 模态、抽屉、输入区大框 |\n", ""))
    arms.append(("Q10 规范表行数变化必须红", any(k == "R0:圆角表" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q11 规范删掉禁令句 ⇒ R0 必须红（**不许自己编阈值**）
    r = _run(spec=SYN_SPEC.replace("废弃 16px 及以上圆角。\n", ""))
    arms.append(("Q11 禁令句消失必须红", any(k == "R0:禁令" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # Q12 棘轮三分支
    kh, nh, ex = split_ratchet([("R2:x", "m")], {"R2:x": "登记"})
    arms.append(("Q12 棘轮内不阻断", len(kh) == 1 and not nh and not ex, f"{len(kh)}/{len(nh)}/{len(ex)}"))
    kh, nh, ex = split_ratchet([("R2:y", "m")], {"R2:x": "登记"})
    arms.append(("Q13 棘轮外阻断", len(nh) == 1 and not kh, f"{len(kh)}/{len(nh)}/{len(ex)}"))
    kh, nh, ex = split_ratchet([], {"R2:x": "登记"})
    arms.append(("Q14 豁免过期阻断", ex == ["R2:x"] and not kh and not nh, f"{ex}"))

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
    ap = argparse.ArgumentParser(description="§4.3 圆角阶梯 / 废弃值门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（纯函数）")
    ap.add_argument("--why", action="store_true", help="打印期望值出处")
    ap.add_argument("--dump", action="store_true", help="打印原始量测")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    for p in (SPEC, FRONTEND / "packages" / "ui" / "src" / "tokens.css",
              FRONTEND / "tailwind.preset.ts"):
        if not p.exists():
            print(f"[ENV] 缺文件：{p} ⇒ **本轮结论不可信**（exit 2 = 环境问题）")
            return 2

    spec_text = SPEC.read_text(encoding="utf-8")
    section = load_section(spec_text, "4.3 圆角 / 阴影 / 边框", "4.4 动效")
    if section is None:
        print("[ENV] `design-spec.md` 里找不到 `### 4.3 圆角 / 阴影 / 边框` ⇒ 规范结构变了")
        return 2

    spec_radii, spec_shadows, threshold = parse_spec(section)
    print(f"[ENV] 规范：{SPEC.relative_to(ROOT).as_posix()}")
    print(f"[ENV] §4.3 解析：圆角 {len(spec_radii)} 条 {spec_radii}；"
          f"阴影 {len(spec_shadows)} 条；废弃阈值 {threshold:g}px" if threshold
          else "[ENV] §4.3 解析：**阈值缺失**")

    files = dict(iter_sources())
    REQUIRED = (TOKENS_REL, PRESET_REL)
    absent = [k for k in REQUIRED if k not in files]
    if absent:
        print("[ENV] 下列必需文件未进入扫描集（键约定不一致？）：")
        for k in absent:
            print(f"      {k}")
        print(f"[ENV] 扫描集样例键：{sorted(files)[:3]}")
        print("[ENV] ⇒ **本轮结论不可信**（exit 2 = 工具问题，不是产品缺陷）")
        return 2

    tokens = parse_tokens(files[TOKENS_REL])
    preset_radius = parse_preset_radius(files[PRESET_REL])
    preset_shadow = parse_preset_shadow(files[PRESET_REL])
    classes = scan_radius_classes(list(files.items()))

    if args.why:
        print("\n[W] 期望值出处")
        print("      圆角表  ← §4.3 第一张表（`design-spec.md`）")
        for k, v in sorted(spec_radii.items()):
            print(f"        {k} = {v:g}px")
        print(f"      禁令    ← §4.3「废弃 {threshold:g}px 及以上圆角」")

    rep = Report()
    judge_r0(spec_radii, spec_shadows, threshold, rep)
    judge_r1(spec_radii, tokens, rep)
    judge_r2(spec_radii, tokens, preset_radius, threshold, rep)
    judge_r3(classes, tokens, threshold, rep)
    judge_r4(preset_radius, tokens, rep)
    judge_r5(classes, rep, args.dump)
    judge_r6(tokens, preset_shadow, spec_shadows, rep)

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

    print("\n=== 只报不判（不影响退出码） ===")
    for msg in rep.notes:
        print(f"  · {msg}")

    if args.dump:
        print("\n[D] 圆角类词汇表（按出现次数）")
        for cls, where in sorted(classes.items(), key=lambda kv: -len(kv[1])):
            got = resolve_px(cls[1:-1], tokens) if cls.startswith("[") else None
            extra = f"  → 解析 {got:g}px" if got is not None else ""
            print(f"    {len(where):>4}  rounded-{cls}{extra}   首见 {where[0][0]}:{where[0][1]}")
        print("\n[D] preset 档位解析值")
        for name, val in sorted(preset_radius.items()):
            got = resolve_px(val, tokens)
            print(f"    {name:<10} {val:<16} → {got:g}px" if got is not None
                  else f"    {name:<10} {val:<16} → **解析失败**")

    if new_hits or expired:
        print(f"\n[结论] exit 1 —— 新违反 {len(new_hits)} 条 · 过期豁免 {len(expired)} 条")
        return 1
    print(f"\n[结论] exit 0 —— 已登记欠账 {len(known_hits)} 条（棘轮内），无新违反")
    return 0


if __name__ == "__main__":
    sys.exit(main())
