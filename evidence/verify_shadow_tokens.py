#!/usr/bin/env python
"""`design-spec.md` §4.3「圆角 / 阴影 / 边框」里**阴影那半**的静态地板。

## 为什么需要它：§4.3 有五条要求，门禁只覆盖两条 —— **又是「小节级」粒度在掩盖**

| §4.3 的要求 | 出处 | 谁判 |
|---|---|---|
| 圆角四档 `--r1..--r4` = 4/6/8/12px | `:197-202` | `verify_radius_scale.py` ✅ |
| 「废弃 16px 及以上圆角」 | `:204` | `verify_radius_scale.py` ✅ |
| **阴影三档 `--s1/--s2/--s3` 的 box-shadow 值** | §4.3「--s1 · 0 1px 2px rgba(16,24,40,.06)」 | 🚨 **谁都没判**（本门禁补） |
| **令牌 → `boxShadow.s*` 的链**（改令牌要能改渲染） | §4.3「--s1 · 0 1px 2px rgba(16,24,40,.06)」 | 🚨 谁都没判（本门禁补） |
| 「优先用 1px 边框而非阴影」 | §4.3「优先用 1px 边框而非阴影」 | **取舍声明** ⇒ 不判（R1 只报） |
| 焦点环统一 `0 0 0 3px rgba(54,96,176,.22)` | `:214` | `verify_focus_ring.py` ✅ |

🚨 **§4.3 不在「CI 内零判据」名单里** —— 它被记为「已覆盖」（因为有圆角门禁）。
但它**六条要求里两条零判据**。这正是审计稿 **§10 发现 F** 说的
「**小节**被判过 ≠ **小节里每条要求**都被判过」，也是该发现的**第二个实例**
（第一个是 §4.2 的页边距）。

⚠️ **`verify_radius_scale.py` 的 R6 早就看见了这个洞，但它选了「只报」** ——
它的原话是「**只报**：`--s1..--s3` 与 `boxShadow.s*` 是否成链（「优先 1px 边框而非阴影」是取舍）」。
⇒ 本门禁把**链**升格为**判**（理由同 §4.1 门禁 A2：「令牌存在但没人用 ⇒ 改令牌等于没改」）；
「优先边框而非阴影」那半**仍然是取舍**，继续只报（R1）。

## 🚨 判据必须**归一化后**比较（否则会稳定假红）

规范与令牌**写的是同一个值**，只是格式不同：

| 出处 | 原文 |
|---|---|
| 规范 §4.3「--s1 · 0 1px 2px rgba(16,24,40,.06)」 | `` `0 1px 2px rgba(16,24,40,.06)` `` |
| 令牌 `tokens.css:172` | `--s1: 0 1px 2px rgba(16, 24, 40, 0.06);` |

差别有三处：**逗号后的空格** · **`.06` vs `0.06`** · **`.10` vs `0.1`**。
⇒ 比较前一律归一化（压空白 / 去逗号旁空白 / 小数补前导零 / 去尾随零）。
⚠️ 这条是**假红的主要来源**：直接字符串相等会把「**恰好正确**」的令牌判成缺陷
（本项目已有先例：`verify_contrast.py` 把规范那一列当精确值 ⇒ **5 条假红**）。

## 判据

- **A0 守卫**：从 §4.3 **现读**阴影表（`--sN` + 值），要求**连续** `s1..sN` 且 **N ≥ 3**。
  读不出 / 不连续 / 少于 3 档 ⇒ **exit 2**（先修本门禁的解析，别把它当产品缺陷）。
- **A1 令牌值**：`tokens.css` 的每个 `--sN` **归一化后** == 现读值。
- **A2 令牌不是死的**：`preset` 的 `boxShadow` 段里每个 `sN` 必须**指向** `var(--sN)`。
  写成硬编码字面量 ⇒ **改令牌不会改渲染** ⇒ rc 1。

## 只报不判（R 组）

- **R1** 「优先用 1px 边框而非阴影」是**取向声明**（规范原话「阴影轻飘，边框稳定」），
  **不可判** ⇒ 只报 `shadow-s2` / `shadow-s3` 的**使用点计数**，供人判断取向是否被遵守。
- **R2** `preset` 里还有三个**存量硬编码**阴影 `brand-sm` / `brand-md` / `brand-glow`
  （未令牌化，注释说「保留，改为墨蓝色调、降低强度」）⇒ 只报。
- **R3** 与 `verify_radius_scale.py` 的**归属**：它的 R6 只报链，本门禁判链 ⇒ 不重复判；
  圆角那半仍归它。
- **R4** §4.3 的**边框**半：「Card `default` = 1px 边框 + `--s1`」由
  `verify_component_refactor.py` **J6** 判 ⇒ 不重复；「优先边框而非阴影」见 R1。

用法：

    python evidence/verify_shadow_tokens.py              # 0 通过 / 1 产品缺陷 / 2 环境问题
    python evidence/verify_shadow_tokens.py --self-test  # 纯合成夹具自检（不读真实仓库）
    python evidence/verify_shadow_tokens.py --inject     # 真仓库**内存**注入反证（不碰产品文件）
    python evidence/verify_shadow_tokens.py --why 4.3    # 打印出处与归属
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from typing import NamedTuple

ROOT = pathlib.Path(__file__).resolve().parent.parent

SPEC_REL = "deliverables/ui-design/design-spec.md"
TOKENS_REL = "frontend/packages/ui/src/tokens.css"
PRESET_REL = "frontend/tailwind.preset.ts"
REQUIRED_FILES = (SPEC_REL, TOKENS_REL, PRESET_REL)

# 扫使用点时剪掉的目录（⚠️ `verify_design_tokens.py` 的 rglob 曾因不剪 `.next` 挂死 160s）
SCAN_ROOTS = ("frontend/packages", "frontend/apps")
SCAN_SKIP_DIRS = {".next", ".next_old_v14_keep", "node_modules", "dist", ".turbo", "out"}
SCAN_EXTS = {".ts", ".tsx", ".css", ".js", ".jsx", ".mjs"}

# 存量键（未令牌化，只报）
LEGACY_SHADOW_KEYS = ("brand-sm", "brand-md", "brand-glow")


class SpecUnreadable(Exception):
    """规范读不出期望值 ⇒ **环境问题（exit 2）**，不是产品缺陷。"""


# ── 注释剥离（剥注释必须在**判据函数内部**，否则自检测不到真实形态）──────────────
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//[^\n]*")


def strip_comments(text: str) -> str:
    r"""块注释换成**等量换行**（保行号），行注释用 `(?<![:\w])` 避开 `https://`。"""
    text = BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return LINE_COMMENT_RE.sub("", text)


# ── 归一化（**假红的主要来源**，见模块 docstring）──────────────────────────────
NUM_RE = re.compile(r"\d*\.\d+")


def _norm_num(m: re.Match[str]) -> str:
    t = m.group(0)
    if t.startswith("."):
        t = "0" + t
    if "." in t:
        t = t.rstrip("0").rstrip(".")
        if t == "" or t == "-":
            t = "0"
    return t


def norm_shadow(value: str) -> str:
    """把 box-shadow 字面量归一化成可比较形式。

    `0 1px 2px rgba(16,24,40,.06)` 与 `0 1px 2px rgba(16, 24, 40, 0.06)` ⇒ 同一个串。
    """
    s = value.strip().strip("`").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*,\s*", ",", s)
    s = re.sub(r"\(\s*", "(", s)
    s = re.sub(r"\s*\)", ")", s)
    return NUM_RE.sub(_norm_num, s)


# ── 源码解析 ────────────────────────────────────────────────────────────────
CSS_VAR_RE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")


def css_vars(src: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in CSS_VAR_RE.finditer(src)}


def brace_block(src: str, key: str) -> str | None:
    """取 `key: { … }` 的**配对花括号**内容（preset 里 `boxShadow` 段）。"""
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*:\s*\{{", src)
    if m is None:
        return None
    i = src.index("{", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1: j]
    return None


KEY_VAL_RE = re.compile(r"(?m)^\s*\"?([\w-]+)\"?\s*:\s*\"([^\"]+)\"\s*,?\s*$")


def block_string_keys(block: str) -> dict[str, str]:
    """把 `{ key: "value", … }` 段解成 dict（**不解析嵌套** —— 阴影值都是平铺字符串）。"""
    return {m.group(1): m.group(2).strip() for m in KEY_VAL_RE.finditer(block)}


def count_in_sources(needle: str) -> int:
    """`needle` 在 `frontend/` 两棵树里出现的次数（剪掉 `.next` / `node_modules`）。"""
    n = 0
    for rel in SCAN_ROOTS:
        base = ROOT / rel
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SCAN_SKIP_DIRS]
            for fn in filenames:
                if pathlib.Path(fn).suffix not in SCAN_EXTS:
                    continue
                try:
                    body = (pathlib.Path(dirpath) / fn).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                n += body.count(needle)
    return n


# ── 规范解析 ────────────────────────────────────────────────────────────────
SEC_43_START = r"^###\s*4\.3\s"
SEC_43_END = r"^###\s*4\.4\s"

# ⚠️ 只认「值那一格**带反引号**」的行 —— §4.3 的圆角表（`--r1` | 4px | …）值那格**没有**反引号，
#    天然被排除；这样加阴影档位时不用改本门禁。
SHADOW_ROW_RE = re.compile(
    r"^\|\s*`--(s\d+)`\s*\|\s*`([^`]+)`\s*\|\s*([^|]*?)\s*\|\s*$", re.M
)


class Expected(NamedTuple):
    shadows: dict[str, str]   # {"s1": "0 1px 2px rgba(16,24,40,0.06)", …}（**已归一化**）
    raw: dict[str, str]       # 原文（打印用）


def read_expected(spec_text: str) -> Expected:
    m = re.search(SEC_43_START, spec_text, re.M)
    if m is None:
        raise SpecUnreadable(f"规范里找不到小节标题 `{SEC_43_START}`")
    tail = spec_text[m.start():]
    e = re.search(SEC_43_END, tail, re.M)
    sec = tail[: e.start()] if e else tail

    rows = SHADOW_ROW_RE.findall(sec)
    if not rows:
        raise SpecUnreadable("§4.3 里读不出阴影表（`| `--sN` | `值` | 用途 |`）")
    names = [r[0] for r in rows]
    want = [f"s{i}" for i in range(1, len(names) + 1)]
    if names != want:
        raise SpecUnreadable(
            f"§4.3 的阴影档位不是连续的 `s1..sN`，实测 {names} ⇒ 先修本门禁的解析"
        )
    if len(names) < 3:
        raise SpecUnreadable(
            f"§4.3 的阴影表只解析出 {len(names)} 档（期望 ≥ 3：s1/s2/s3）⇒ 先修本门禁的解析"
        )
    return Expected(
        shadows={r[0]: norm_shadow(r[1]) for r in rows},
        raw={r[0]: r[1].strip() for r in rows},
    )


# ── 判据 ────────────────────────────────────────────────────────────────────
def evaluate(
    spec_text: str,
    tokens_src: str,
    preset_src: str,
    exists=None,
    usages=None,
) -> tuple[int, list[str], list[str]]:
    """**纯函数**：返回 `(rc, defects, reports)`。

    `exists` / `usages` 可注入 —— 真仓库里三个文件都在，不注入的话
    「必需文件守卫」与 R1 的计数就**永远只会走同一个分支**。
    """
    exists = exists or (lambda rel: (ROOT / rel).exists())
    usages = usages if usages is not None else count_in_sources
    tokens_src = strip_comments(tokens_src)
    preset_src = strip_comments(preset_src)

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
        "规范 §4.3 现读阴影 "
        + " · ".join(f"`--{k}` = {v}" for k, v in exp.raw.items())
    )

    tokens = css_vars(tokens_src)
    got = {k: norm_shadow(tokens.get(k, "")) for k in exp.shadows}

    # ── A1 令牌值（**归一化后**比较）──
    for k, want in exp.shadows.items():
        if not tokens.get(k):
            defects.append(
                f"`tokens.css` 里没有 `--{k}`（§4.3 要求 `{exp.raw[k]}`）"
            )
        elif got[k] != want:
            defects.append(
                f"`--{k}` = **{tokens[k].strip()}**，而 §4.3 要求 **{exp.raw[k]}**"
                "（已按归一化比较：空白 / 逗号 / 小数写法差异不算差异）"
            )

    # ── A2 令牌不是死的（preset 必须指向令牌）──
    box = brace_block(preset_src, "boxShadow")
    if box is None:
        defects.append(
            "preset 里没有 `boxShadow: { … }` 段 ⇒ §4.3 的阴影令牌**没接上**"
            "（`shadow-s*` 类不存在 ⇒ 改令牌不会改渲染）"
        )
    else:
        keys = block_string_keys(box)
        for k in exp.shadows:
            if k not in keys:
                defects.append(
                    f"preset 的 `boxShadow` 段里没有 `{k}` 键 ⇒ `shadow-{k}` 不存在"
                    f"（§4.3 的 `--{k}` 用不上）"
                )
            elif keys[k] != f"var(--{k})":
                defects.append(
                    f"preset 的 `boxShadow.{k}` = **{keys[k]}**（期望 `var(--{k})`）⇒ "
                    "**改令牌不会改渲染**"
                )
        reports.append(
            f"preset 的 `boxShadow` 段实测 {len(keys)} 个键：{' / '.join(sorted(keys))}"
        )
        legacy = [k for k in LEGACY_SHADOW_KEYS if k in keys]
        reports.append(
            f"R2 存量**未令牌化**阴影键：{(' / '.join(legacy)) if legacy else '（无）'}"
            + (
                " —— 三个都是**硬编码 rgba**（preset 注释说「保留，改为墨蓝色调、降低强度」）"
                "⇒ 不在 §4.3 表里，只报"
                if legacy
                else ""
            )
        )

    # ── R 组：只报不判 ──
    n2, n3 = usages("shadow-s2"), usages("shadow-s3")
    reports.append(
        f"R1 「优先用 1px 边框而非阴影」是**取向声明**（规范原话「阴影轻飘，边框稳定」）⇒ 不可判；"
        f"只报使用点：`shadow-s2` **{n2}** 处 · `shadow-s3` **{n3}** 处"
        "（用得多说明偏离那条取向，由人判断）"
    )
    reports.append(
        "R3 归属：**链**归本门禁判；`verify_radius_scale.py` 的 R6 是它的**只报**历史形态 ⇒ 不重复判。"
        "**圆角**那半仍归 `verify_radius_scale.py`。"
    )
    reports.append(
        "R4 §4.3 的**边框**半：「Card `default` = 1px 边框 + `--s1`」由 "
        "`verify_component_refactor.py` **J6** 判 ⇒ 不重复；「优先边框而非阴影」见 R1"
    )

    return (1 if defects else 0), defects, reports


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


# ── 合成夹具（自检用；**不读真实仓库**）─────────────────────────────────────
SYN_SPEC = """\
## 04 布局 · 间距 · 圆角 · 阴影

### 4.3 圆角 / 阴影 / 边框

| Token | 值 | 用途 |
|---|---|---|
| `--r1` | 4px | 徽章、标签、小按钮 |
| `--r4` | 12px | 模态、抽屉、输入区大框 |

废弃 16px 及以上圆角。

| Token | 值 | 用途 |
|---|---|---|
| `--s1` | `0 1px 2px rgba(16,24,40,.06)` | 卡片静置 |
| `--s2` | `0 4px 12px -2px rgba(16,24,40,.10)` | hover、下拉 |
| `--s3` | `0 16px 40px -8px rgba(16,24,40,.18)` | 模态、抽屉 |

> 法律产品**优先用 1px 边框而非阴影**——阴影轻飘，边框稳定。

焦点环统一：`0 0 0 3px rgba(54,96,176,.22)`。

### 4.4 动效

…
"""

SYN_TOKENS = """\
:root {
  --r1: 4px;
  --s1: 0 1px 2px rgba(16, 24, 40, 0.06); /* 卡片静置 */
  --s2: 0 4px 12px -2px rgba(16, 24, 40, 0.1); /* hover、下拉 */
  --s3: 0 16px 40px -8px rgba(16, 24, 40, 0.18); /* 模态、抽屉 */
}
"""

SYN_PRESET = """\
export const preset = {
  theme: {
    extend: {
      boxShadow: {
        s1: "var(--s1)",
        s2: "var(--s2)",
        s3: "var(--s3)",
        focus: "0 0 0 3px var(--focus-ring)",
        "brand-sm": "0 1px 2px 0 rgba(39, 76, 147, 0.06)",
      },
    },
  },
};
"""


def _with(spec: str = SYN_SPEC, tokens: str = SYN_TOKENS, preset: str = SYN_PRESET, usages=None):
    return evaluate(
        spec,
        tokens,
        preset,
        exists=lambda rel: True,
        usages=usages if usages is not None else (lambda s: 0),
    )


def self_test() -> int:
    arms: list[tuple[str, int, list[str]]] = []

    def arm(name: str, result: tuple[int, list[str], list[str]], want_rc: int, must: str = "") -> None:
        rc, defects, _ = result
        ok = rc == want_rc and (must in " ".join(defects) if must else True)
        arms.append((name, 0 if ok else 1, [f"期望 rc={want_rc}，实测 rc={rc}", *defects[:1]]))

    # Q1–Q5 A0 守卫
    arm("Q1 规范缺阴影表 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("| `--s1` | `0 1px 2px rgba(16,24,40,.06)` | 卡片静置 |\n", "")
              .replace("| `--s2` | `0 4px 12px -2px rgba(16,24,40,.10)` | hover、下拉 |\n", "")
              .replace("| `--s3` | `0 16px 40px -8px rgba(16,24,40,.18)` | 模态、抽屉 |\n", "")),
        2, "读不出阴影表")
    arm("Q2 阴影档位不连续（缺 s2）⇒ exit 2",
        _with(spec=SYN_SPEC.replace("| `--s2` | `0 4px 12px -2px rgba(16,24,40,.10)` | hover、下拉 |\n", "")),
        2, "不是连续的")
    arm("Q3 阴影表只剩 2 档 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("| `--s2` | `0 4px 12px -2px rgba(16,24,40,.10)` | hover、下拉 |\n", "")
              .replace("| `--s3` | `0 16px 40px -8px rgba(16,24,40,.18)` | 模态、抽屉 |\n", "")),
        2, "期望 ≥ 3")
    arm("Q4 规范缺小节标题 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("### 4.3 圆角 / 阴影 / 边框", "### 43 圆角")), 2, "找不到小节标题")
    arm("Q5 必需文件取不到 ⇒ exit 2",
        evaluate(SYN_SPEC, SYN_TOKENS, SYN_PRESET, exists=lambda rel: rel != PRESET_REL,
                 usages=lambda s: 0),
        2, "必需文件取不到")

    # Q6 控制组
    arm("Q6 全部对齐 ⇒ 绿（控制组）", _with(), 0)

    # Q7–Q11 A1（含归一化对照 —— **最关键的一组**）
    arm("Q7 `--s1` 值改掉 ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("--s1: 0 1px 2px rgba(16, 24, 40, 0.06)",
                                        "--s1: 0 2px 4px rgba(16, 24, 40, 0.06)")), 1, "--s1")
    arm("Q8 `--s2` 整个缺失 ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("  --s2: 0 4px 12px -2px rgba(16, 24, 40, 0.1); /* hover、下拉 */\n", "")),
        1, "没有 `--s2`")
    arm("Q9 🚨 **归一化对照**：令牌写成规范那种紧凑写法（`rgba(16,24,40,.06)`）⇒ **必须绿**",
        _with(tokens=SYN_TOKENS.replace("rgba(16, 24, 40, 0.06)", "rgba(16,24,40,.06)")
              .replace("rgba(16, 24, 40, 0.1)", "rgba(16,24,40,.10)")
              .replace("rgba(16, 24, 40, 0.18)", "rgba(16,24,40,.18)")),
        0)
    arm("Q9b 🚨 归一化对照：小数补零 / 去零两个方向**都**必须绿",
        _with(tokens=SYN_TOKENS.replace("rgba(16, 24, 40, 0.1)", "rgba(16, 24, 40, 0.10)")), 0)
    arm("Q9c 归一化**不能**把真差异吃掉：`.06` → `.07` ⇒ 必须红",
        _with(tokens=SYN_TOKENS.replace("rgba(16, 24, 40, 0.06)", "rgba(16, 24, 40, 0.07)")), 1, "--s1")

    # Q12–Q14 A2
    arm("Q12 preset `s1` 改成硬编码字面量 ⇒ 红（改令牌不会改渲染）",
        _with(preset=SYN_PRESET.replace('s1: "var(--s1)"', 's1: "0 1px 2px rgba(16,24,40,.06)"')),
        1, "不会改渲染")
    arm("Q13 preset 缺 `s3` 键 ⇒ 红",
        _with(preset=SYN_PRESET.replace('        s3: "var(--s3)",\n', "")), 1, "没有 `s3` 键")
    arm("Q14 preset 整个 `boxShadow` 段没了 ⇒ 红",
        _with(preset="export const preset = {};\n"), 1, "没有 `boxShadow")

    # Q15 剥注释对照（带引号的坏值放在会被读到的地方才算数）
    arm("Q15 注释里写 `--s1` 的别的值 ⇒ 必须绿（剥注释）",
        _with(tokens=SYN_TOKENS.replace("  --s1:", "  /* 反面教材 --s1: 0 9px 9px rgba(1,2,3,.9); */\n  --s1:")),
        0)

    # Q16 期望值从规范现读（一对臂）
    arm("Q16a 规范改成别的值，而令牌没跟着改 ⇒ 红",
        _with(spec=SYN_SPEC.replace("`0 1px 2px rgba(16,24,40,.06)`", "`0 3px 6px rgba(16,24,40,.06)`")),
        1, "0 3px 6px")
    arm("Q16b 规范改、令牌跟着改 ⇒ 绿（证明值来自规范）",
        _with(spec=SYN_SPEC.replace("`0 1px 2px rgba(16,24,40,.06)`", "`0 3px 6px rgba(16,24,40,.06)`"),
              tokens=SYN_TOKENS.replace("0 1px 2px rgba(16, 24, 40, 0.06)", "0 3px 6px rgba(16, 24, 40, 0.06)")),
        0)

    # Q17 新增档位时门禁要跟着走（不被写死的 3 卡住）
    arm("Q17 规范新增 `--s4` **且**令牌/preset 都有 ⇒ 绿（门禁跟着规范走）",
        _with(spec=SYN_SPEC.replace("### 4.4 动效", "| `--s4` | `0 0 0 1px rgba(16,24,40,.2)` | 分隔 |\n\n### 4.4 动效"),
              tokens=SYN_TOKENS.replace("}", "  --s4: 0 0 0 1px rgba(16, 24, 40, 0.2);\n}"),
              preset=SYN_PRESET.replace('s3: "var(--s3)",', 's3: "var(--s3)",\n        s4: "var(--s4)",')),
        0)

    # Q18 R 组只报不判
    arm("Q18 R1 使用点计数很大 ⇒ 仍绿（R 组只报不判）",
        _with(usages=lambda s: 999), 0)

    failed = [a for a in arms if a[1]]
    print("=" * 82)
    for name, bad, detail in arms:
        print(f"  [{'✗' if bad else '✓'}] {name}")
        if bad:
            for d in detail:
                print(f"        {d}")
    print("=" * 82)
    print(f"自检：{len(arms) - len(failed)}/{len(arms)} 通过")
    return 1 if failed else 0


# ── 真仓库内存注入（**不碰产品文件**）───────────────────────────────────────
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

    cases: list[tuple[str, str, str, str, int]] = [
        ("I0 控制组：一个字节都不改 ⇒ 必须绿", spec, tokens, preset, 0),
        ("I1 `--s1` 值改掉 ⇒ A1 红", spec,
         replace_once(tokens, "--s1: 0 1px 2px rgba(16, 24, 40, 0.06);",
                      "--s1: 0 2px 4px rgba(16, 24, 40, 0.06);"), preset, 1),
        ("I2 `--s3` 值改掉 ⇒ A1 红", spec,
         replace_once(tokens, "--s3: 0 16px 40px -8px rgba(16, 24, 40, 0.18);",
                      "--s3: 0 16px 40px -8px rgba(16, 24, 40, 0.28);"), preset, 1),
        ("I3 🚨 令牌改写成规范那种**紧凑写法**（同一个值）⇒ 必须绿（证明归一化生效）", spec,
         replace_once(tokens, "--s1: 0 1px 2px rgba(16, 24, 40, 0.06);",
                      "--s1: 0 1px 2px rgba(16,24,40,.06);"), preset, 0),
        ("I4 preset `s2` 脱开令牌（硬编码）⇒ A2 红", spec, tokens,
         replace_once(preset, 's2: "var(--s2)"',
                      's2: "0 4px 12px -2px rgba(16,24,40,.10)"'), 1),
        ("I5 preset 删掉 `s3` 键 ⇒ A2 红", spec, tokens,
         replace_once(preset, '        s3: "var(--s3)",\n', ""), 1),
    ]

    bad = 0
    for name, s, t, p, want in cases:
        try:
            rc, defects, _ = evaluate(s, t, p, usages=lambda _s: 0)
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
    "4.3": (
        "§4.3「圆角 / 阴影 / 边框」里**阴影那半** —— 出处 `deliverables/ui-design/design-spec.md`\n"
        "  的 `### 4.3`，阴影表三行（§4.3「--s1 · 0 1px 2px rgba(16,24,40,.06)」）+「优先用 1px 边框而非阴影」（§4.3「优先用 1px 边框而非阴影」）。\n"
        "  归属：**本门禁**判阴影的**令牌值**与**令牌→`boxShadow.s*` 的链**；\n"
        "  **圆角**归 `verify_radius_scale.py`（它的 R6 只报链，本门禁把它升格为判）；\n"
        "  **焦点环**归 `verify_focus_ring.py`；**Card 1px 边框**归 `verify_component_refactor.py` J6。\n"
        "  ⚠️ §4.3 **不在**「CI 内零判据」名单里（有圆角门禁）⇒ 但六条要求里**两条零判据**。\n"
        "     这正是审计稿 §10 发现 F 的**第二个实例**（第一个是 §4.2 的页边距）。\n"
        "  ⚠️ 比较**必须归一化**：规范写 `rgba(16,24,40,.06)`、令牌写 `rgba(16, 24, 40, 0.06)`\n"
        "     ⇒ 直接字符串相等会**稳定假红**。",
    ),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="design-spec §4.3 阴影静态地板")
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
    except OSError as exc:
        print(f"【环境】取不到必需文件：{exc}")
        return 2

    rc, defects, reports = evaluate(spec, tokens, preset)
    for r in reports:
        print(f"  · {r}")
    if defects:
        print()
        for d in defects:
            print(f"  ✗ {d}")
        print(f"\n❌ §4.3 阴影：**{len(defects)} 条欠账**")
        return rc
    print("\n✅ §4.3 阴影：**0 欠账**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
