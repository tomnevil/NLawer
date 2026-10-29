#!/usr/bin/env python
"""对比度门禁 —— WCAG 1.4.3 / 规范 §2 声明的对比度，此前**没有任何判据**。

用法：
    python evidence/verify_contrast.py --self-test
    python evidence/verify_contrast.py                  # T1–T3（纯计算）+ T4（渲染级）
    python evidence/verify_contrast.py --tokens-only     # 只跑 T1–T3（不开浏览器，秒级）
    python evidence/verify_contrast.py --dump            # 打原始测量值，不下判断
    python evidence/verify_contrast.py --app im

退出码：`0` 通过 / `1` 产品缺陷 / `2` 环境或测量问题。

═══════════════════════════════════════════════════════════════════════════════
为什么需要这个门禁
═══════════════════════════════════════════════════════════════════════════════

**① 规范里有现成的期望值表，却没人复核过。**
`design-spec.md` §2.1/2.2 的表格有一列就是**对比度**（`--ink-600` `#4A525C` `7.5:1 ✓`、
`--brand-600` `#274C93` `8.3:1 ✓`…），§2.3 在「用途」列里也写了（`--verified-600` 6.0:1 等）。
`§9「可访问性：键盘可达、焦点环统一、对比度复核」` 的可访问性清单里点了「**对比度复核**」。
⇒ **期望值出处明确**，但 `evidence/` 里**一条对比度判据都没有**
（唯一命中是 `verify_dark_mode.py` 算亮度做「浅色孤岛」，且它自己声明了
「已知边界：**不含对比度判据**（D4，WCAG 1.4.3）」——本门禁补的就是这条边界）。

**② 这里「静态检查」是恰当的，不是偷懒。**
两个令牌之间的对比度是它们色值的**纯函数** ⇒ 不需要浏览器。
（与深色模式 / `prefers-reduced-motion` 不同：那两个由浏览器解析，静态覆盖不到。
**分清哪些该静态、哪些必须渲染级，本身就是判据设计的一部分。**）

**③ 「4.5:1」这个门槛不是我拍的，是从规范里推导出来的。**
规范表里**所有标 `✓` 的声明值都 ≥ 4.5**（最低是 `--ink-500` 的 4.7），
**所有未标 `✓` 的数值都 < 4.5**（`--ink-400` 2.5、`--brand-400` 3.3）。
⇒ 本项目自定的合格线就是 4.5。判据 T0 把这个推导**本身**变成一条断言。

═══════════════════════════════════════════════════════════════════════════════
判据
═══════════════════════════════════════════════════════════════════════════════

  T0   门槛自洽（**从规范推导，不硬编码**）：标 `✓` 的声明值必须全部 ≥ 4.5，
       未标 `✓` 的数值必须全部 < 4.5。违反 ⇒ **exit 2**（说明「4.5 是本项目合格线」
       这个前提不成立，T2/T4 的门槛就失去出处）。

  T1a  规范-实现**漂移**：`tokens.css` 里该令牌的色值必须等于规范表里写的 hex。
  T1'  规范标记与 WCAG 重算**自洽**（**定性**）：标 `✓` 的必须 ≥ 4.5、标「禁用」或未标 `✓` 的必须 < 4.5。
       ⚠️ **不做精确匹配**——实测规范那一列是**近似值、不是 WCAG 公式算出来的**：
       `--ink-600` 实算 **7.92**（规范写 7.5）、`--ink-800` 实算 **15.22**（规范写 13.9）。
       9 行里只有 4 行能对上（Δ≤0.1）。精确匹配是**过度断言** ⇒ 曾产出 **5 条假红**。
       数值偏差只作**文档问题**报出（阈值 `DOC_TOL`），**不判产品缺陷**。
       ⚠️ 只对**白底**断言（出处表头写的是「白底对比度」）。拿 `--surface-page`
       （`#F7F8F9`，不是纯白）去断言是**比出处更宽**（见坑 17）。

  T2   深色档正文可读性：`.dark` 块里 `--text-primary` / `--text-secondary` 对
       `--surface-page` / `--surface-card` 必须 ≥ 4.5:1。
       出处：WCAG 1.4.3 + 本项目自定的 4.5 线（T0 已证）。规范表只给了浅色档 ⇒ 深色档用 T0 的线。

  T3   语义状态「文本用色 × 其浅底」：规范 §2.3 **逐条声明**过的三对，逐对复核：
       `--verified-600`/`--verified-50`、`--pending-600`/`--pending-50`、`--danger-500`/`--danger-50`。
       断言的是 **≥ 4.5 线**（T0 已证）；规范声明的数值只用于记**文档偏差**（同 T1'）。

  T4   渲染级（**硬**，**按字号取门槛**）：`is_large_text(fs, fw)`（`≥24px` 或 `≥18.66px` 粗体）
       ⇒ 门槛 **3.0**；其余 ⇒ 门槛 **4.5**。低于门槛即红。
       ⚠️ 判的是**运行时有效**对比度（背景是**实际合成出来的**那一层），**不是**规范表的「白底」——
       二者可以不同：`--ink-500` 在白底 4.74，放到 `--surface-subtle` 上只有 **4.18**。
       落在 `[门槛, 门槛+0.15)` 的报「**临界**」不判（测量两位小数，舍入可能翻转）。
       ⚠️ **第一版判据太窄**：原稿把「3.0–4.5」一律「只报不判」，理由是「可能是大字号」——
       但大字号**是能判的**（`fs`/`fw` 就在手边）⇒ 12px 链接（3.05:1）这种硬缺陷被放过。
       这是「判据比出处更窄」：出处本来就规定了大字号的界线，判据却没实现它。

⚠️ 已知边界（**没测的东西不假装测了**）：
  · T1/T3 只复核规范**声明过**的组合；未声明的（`--ai-*` / `--gold-*` / `--info-*` 的文本对）
    只在 `--dump` 里**报出数值**，不作断言。
  · T4 排除：背景含 `background-image`（渐变/图片，算不出）、
    元素或祖先 `opacity < 1`、`-webkit-text-fill-color: transparent`（渐变文字）、
    `disabled` / `aria-hidden` / `visibility:hidden`、非文本标签。
  · T4 不判「非文本对比度」（WCAG 1.4.11：边框/图标/焦点环 ≥ 3:1）——那是另一条判据。
  · 只测 390 / 1280 两档、每端 2 页、浅色 + 深色两档主题。
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# 演示账号表在 `app.seed.data` 里 —— 账号**不硬编码**，从后端同一份源头读。
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

ROOT = HERE.parents[0]
SPEC_MD = ROOT / "deliverables" / "ui-design" / "design-spec.md"
TOKENS_CSS = ROOT / "frontend" / "packages" / "ui" / "src" / "tokens.css"

WHITE = (255.0, 255.0, 255.0)
AA_TEXT = 4.5          # T0 会证明这个值来自规范自身
AA_LARGE = 3.0         # WCAG 对 ≥24px / ≥18.66px 粗体 的下限
# 规范表数值与 WCAG 重算的偏差超过它 ⇒ 记为**文档问题**（不是产品缺陷）。
# ⚠️ 这个值**不是**用来「糊掉 off-by-one」的容差：判据 T1'/T3 断言的是**定性**的
# `✓`/`禁用` 与 4.5 线，**不做精确匹配**（实测规范那一列是近似值，最大差 1.31）。
DOC_TOL = 0.5

VIEWPORTS: tuple[tuple[int, int, bool, str], ...] = ((390, 844, True, "390"), (1280, 900, False, "1280"))
APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin", "pages": ("/", "/qa")},
    "lawyer": {"port": 3001, "user": "lawyer_wang", "pages": ("/", "/cases")},
    "admin": {"port": 3002, "user": "admin", "pages": ("/", "/audit")},
    "im": {"port": 3003, "user": "client", "pages": ("/", "/chat")},
}
MIN_CONTENT = 40


# ---------------------------------------------------------------------------
# 色彩数学（WCAG 2.x 相对亮度）
# ---------------------------------------------------------------------------
def _f(x: float) -> float:
    return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4


def luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * _f(r) + 0.7152 * _f(g) + 0.0722 * _f(b)


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))


def triple_to_rgb(v: str) -> tuple[float, float, float] | None:
    """`74 82 92; /* #4A525C 正文辅助 */` → 三元组。带 alpha 或非数值返回 `None`。

    🚨 **必须先切掉行尾注释**：`tokens.css` 的每个令牌都长成
    `74 82 92; /* #4A525C 正文辅助（7.5:1） */`，直接按 `[\\s,]+` 切会得到
    `['74','82','92;','/*',…]` ⇒ `float('92;')` 抛 `ValueError` ⇒ 返回 `None`，
    于是 T1' 对**每一个**令牌都报「不是纯三元组」。
    （这个 bug 曾被「`judge_tokens` 返回元组、`bool(元组)` 恒真」掩盖了一轮。）
    """
    head = (v or "").split("/*", 1)[0]                       # 去掉行尾注释
    parts = re.split(r"[\s,]+", head.strip().rstrip(";").strip())
    if len(parts) != 3:                                      # 带 alpha（4 段）也算「不是纯三元组」
        return None
    try:
        return tuple(float(x) for x in parts)  # type: ignore[return-value]
    except ValueError:
        return None


def hex_in_comment(v: str) -> str | None:
    """从 `74 82 92; /* #4A525C 正文辅助 */` 里取注释中的 hex。"""
    m = re.search(r"#([0-9A-Fa-f]{6})\b", v or "")
    return "#" + m.group(1).upper() if m else None


# ---------------------------------------------------------------------------
# 解析 tokens.css（与 `verify_dark_mode.py` 同一套做法）
# ---------------------------------------------------------------------------
# 声明正则：**故意把 `;` 之后的行尾注释也吃进来**（理由见 `_block`）。
DECL_RE = re.compile(r"--([\w-]+)\s*:\s*([^;{}]+(?:;[^\n{}]*)?)")


def _block(css: str, selector: str) -> dict[str, str]:
    """返回选择器块内的 `{变量名: 声明文本}`。

    ⚠️ 声明文本**故意包含 `;` 之后的行尾注释**，不是 CSS 语义上的「到第一个 `;` 为止」。
    原因：`tokens.css` 把**规范对应的 hex 写在分号之后**——
    `--ink-600: 74 82 92; /* #4A525C 正文辅助、表单标签（7.5:1） */`。
    若按 `[^;]+` 取，注释被切掉 ⇒ `hex_in_comment()` **恒返回 `None`**
    ⇒ 判据 **T1a（规范-实现漂移）结构上不可能变红**——它一直报绿，却一次都没核对过。
    （实测踩到：`--dump` 打出 9 行全是 `令牌=None`。见坑 28。）
    取整行后 `triple_to_rgb()`（自己会剥注释与结尾 `;`）与 `hex_in_comment()` 都能用。

    假设：块内**没有嵌套规则块**（`tokens.css` 的 `:root` / `.dark` 都满足——
    唯二的嵌套块是 `@media`，它们在两个块之外）。
    """
    m = re.search(re.escape(selector) + r"\s*\{", css)
    if not m:
        raise KeyError(f"tokens.css 里找不到 `{selector}` 块")
    i, depth = m.end(), 1
    while i < len(css) and depth:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    return {mm.group(1): mm.group(2).strip() for mm in DECL_RE.finditer(css[m.end(): i - 1])}


def load_tokens(path: pathlib.Path = TOKENS_CSS) -> tuple[dict[str, str], dict[str, str]]:
    """返回 `(浅色档, 深色档)` —— 即 `(:root, .dark)`。**顺序不能反**。"""
    css = path.read_text(encoding="utf-8")
    return _block(css, ":root"), _block(css, ".dark")


LIGHT, DARK = load_tokens()   # ⚠️ 顺序：`load_tokens()` 返回 `(:root, .dark)` = `(浅, 深)`


# ---------------------------------------------------------------------------
# 解析规范 §2 的对比度表 —— **期望值从这里来**
# ---------------------------------------------------------------------------
def _ratio_of(cell: str) -> tuple[float | None, str]:
    """从单元格里取对比度数值与标记。返回 `(值或 None, 标记)`，标记 ∈ {`✓`, `禁用`, ``}。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*:\s*1", cell or "")
    val = float(m.group(1)) if m else None
    mark = "✓" if "✓" in (cell or "") else ("禁用" if "禁" in (cell or "") else "")
    return val, mark


def spec_rows(path: pathlib.Path = SPEC_MD) -> list[dict]:
    """解析 §2.1 / §2.2 的四列表：`| Token | 色值 | 对比度 | 用途 |`。

    §2.3 是**三列表**（`| Token | 色值 | 底色 | 用途 |`），由 `STATUS_TEXT_PAIRS` 显式登记。
    """
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*`(--[\w-]+)`\s*\|\s*`(#[0-9A-Fa-f]{6})`\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*$",
                     line)
        if not m:
            continue
        token, hexv, ratio_cell, usage = m.group(1), m.group(2), m.group(3), m.group(4)
        val, mark = _ratio_of(ratio_cell)
        if val is None:
            continue          # `—` 的行（纯底色/描边）不参与
        rows.append({"token": token, "hex": hexv.upper(), "declared": val,
                     "mark": mark, "usage": usage})
    return rows


SPEC_ROWS = spec_rows()

# 判据 T3：§2.3 的「文本用色 × 其浅底」（规范把值写在「用途」列里）
STATUS_TEXT_PAIRS: tuple[tuple[str, str, float, str], ...] = (
    ("verified-600", "verified-50", 6.0, "§2.3「--verified-500 · #1E8E6E」"),
    ("pending-600", "pending-50", 5.5, "§2.3「--pending-500 · #B4791F」"),
    ("danger-500", "danger-50", 5.4, "§2.3「--danger-500 · #C0392B」"),
)

# 判据 T2：深色档「正文色 × 表面」
DARK_TEXT_PAIRS: tuple[tuple[str, str], ...] = (
    ("text-primary", "surface-page"), ("text-primary", "surface-card"),
    ("text-secondary", "surface-page"), ("text-secondary", "surface-card"),
)


# ---------------------------------------------------------------------------
# T0 / T1 / T2 / T3（纯函数，可被自检直接喂数据）
# ---------------------------------------------------------------------------
def threshold_sanity(rows: list[dict], aa: float = AA_TEXT) -> list[str]:
    """**T0 门槛自洽**：证明「4.5 是本项目的合格线」这个前提成立。

    与命名无关：只看 `✓` 标记与数值的关系。若规范改成「3.5 就算合格」，
    这条会红 ⇒ 提醒「门槛的出处变了」，而不是继续拿旧门槛断言。
    """
    bad: list[str] = []
    ok_vals = [r["declared"] for r in rows if r["mark"] == "✓"]
    if not ok_vals:
        return ["规范表里一条 `✓` 都没有 ⇒ 推导不出合格线"]
    below = [f"{r['token']} {r['declared']}" for r in rows if r["mark"] == "✓" and r["declared"] < aa]
    above = [f"{r['token']} {r['declared']}" for r in rows if r["mark"] != "✓" and r["declared"] >= aa]
    if below:
        bad.append(f"标 `✓` 却低于 {aa}：{below} ⇒ 「{aa} 是合格线」这个前提不成立")
    if above:
        bad.append(f"未标 `✓` 却不低于 {aa}：{above} ⇒ 同上")
    return bad


def judge_tokens(rows: list[dict], light: dict[str, str],
                 dark: dict[str, str]) -> tuple[list[str], list[dict]]:
    """T1 / T2 / T3。返回 `(problems, doc_notes)`。

    `doc_notes` = **文档质量问题**（规范表数值不精确），**不算产品缺陷**。

    🚨 **实测教训（自检的对照组抓出来的）**：我最初把 T1 写成
    「规范声明的数值必须**精确等于** WCAG 公式重算值（±0.15）」⇒ 自检对照组 Q1 **报红**。
    逐行验算后真相是：**规范那一列是近似值，不是 WCAG 公式算出来的**——
    用 WCAG 公式重算 `--ink-600` 得 **7.92**（规范写 7.5）、`--ink-800` 得 **15.21**（规范写 13.9）。
    9 行里只有 4 行能对上（Δ≤0.1），其余最大差 **1.31**。

    ⇒ 精确匹配是**过度断言**（会产出 5 条假红）。**可复现的**是**定性断言**：
    标 `✓` 的必须 ≥ 4.5、标「禁用」或未标 `✓` 的必须 < 4.5。
    **9 行全部满足这条**（实测：最低的 ✓ 是 `--brand-500` 6.08，最高的非 ✓ 是 `--brand-400` 3.90）。
    数值偏差只作**文档问题**报出，**不判产品**。
    """
    bad: list[str] = []
    notes: list[dict] = []

    # —— T1a：规范-实现漂移 + T1'：规范标记与 WCAG 重算是否自洽 ——
    for r in rows:
        raw = light.get(r["token"].lstrip("-"))
        if raw is None:
            bad.append(f"[T1a] `{r['token']}` 在 `tokens.css` 的 `:root` 里找不到")
            continue
        got_hex = hex_in_comment(raw)
        if got_hex and got_hex != r["hex"]:
            bad.append(f"[T1a] 规范-实现**漂移**：`{r['token']}` 规范写 `{r['hex']}`，"
                       f"`tokens.css` 实际 `{got_hex}`（{raw.split('/*')[0].strip()}）")
        # 用**令牌的实际色值**重算（它才是运行时生效的那个）
        rgb = triple_to_rgb(raw)
        if not rgb:
            bad.append(f"[T1'] `{r['token']}` 的令牌值不是纯三元组（`{raw}`）")
            continue
        got = contrast(rgb, WHITE)
        if r["mark"] == "✓" and got < AA_TEXT:
            bad.append(f"[T1'] `{r['token']}` 标了 `✓`（可用作文本），但 WCAG 重算只有 "
                       f"**{got:.2f}:1** < {AA_TEXT}")
        if r["mark"] != "✓" and got >= AA_TEXT:
            bad.append(f"[T1'] `{r['token']}` 未标 `✓`（规范定位为"
                       f"{r['usage'][:16] or '非文本'}），但 WCAG 重算有 **{got:.2f}:1** ≥ {AA_TEXT}"
                       f" ⇒ 要么该标 `✓`，要么它被误用")
        if abs(got - r["declared"]) > DOC_TOL:
            notes.append({"kind": "规范表数值不精确", "token": r["token"],
                          "declared": r["declared"], "computed": round(got, 2),
                          "delta": round(got - r["declared"], 2)})

    # —— T2：深色档正文可读性 ——
    for fg, bg in DARK_TEXT_PAIRS:
        f, b = dark.get(fg), dark.get(bg)
        if not f or not b:
            bad.append(f"[T2] 深色档读不到 `--{fg}` / `--{bg}`")
            continue
        frgb, brgb = triple_to_rgb(f), triple_to_rgb(b)
        if not frgb or not brgb:
            bad.append(f"[T2] 深色档 `--{fg}` / `--{bg}` 不是纯三元组（`{f}` / `{b}`）")
            continue
        got = contrast(frgb, brgb)
        if got < AA_TEXT:
            bad.append(f"[T2] 深色档 `--{fg}` 对 `--{bg}` = **{got:.2f}:1** < {AA_TEXT}"
                       f"（WCAG 1.4.3 + 本项目 4.5 线，见 T0）")

    # —— T3：语义状态文本对（**断言 ≥ 4.5 线**，声明值只用于记偏差）——
    for fg, bg, declared, src in STATUS_TEXT_PAIRS:
        f, b = light.get(fg), light.get(bg)
        if not f or not b:
            bad.append(f"[T3] 读不到 `--{fg}` / `--{bg}`")
            continue
        frgb, brgb = triple_to_rgb(f), triple_to_rgb(b)
        if not frgb or not brgb:
            continue
        got = contrast(frgb, brgb)
        if got < AA_TEXT:
            bad.append(f"[T3] `--{fg}` 对 `--{bg}` = **{got:.2f}:1** < {AA_TEXT}"
                       f"（规范 {src} 声明 {declared}:1）")
        if abs(got - declared) > DOC_TOL:
            notes.append({"kind": "规范表数值不精确", "token": f"{fg} on {bg}",
                          "declared": declared, "computed": round(got, 2),
                          "delta": round(got - declared, 2)})
    return bad, notes


def t1a_coverage(rows: list[dict], light: dict[str, str]) -> tuple[int, list[str]]:
    """**T1a 的有效性控制**：返回 `(取到 hex 的行数, 取不到 hex 的令牌名)`。

    🚨 一条**结构上不可能变红**的判据不算判据。T1a 的全部检出能力都挂在
    「`hex_in_comment(令牌声明)` 能取到 hex」上；取不到时它是 `if got_hex and …` ⇒ **静默跳过**。
    实测踩到过：`_block` 把行尾注释切掉 ⇒ 9 行**全部**取不到 ⇒ T1a 一直报绿、
    却一次都没核对过（见坑 28）。

    ⇒ 把「核对了几行」变成可观测的量：`0 行 ⇒ exit 2`（这是「我没测成」，不是「产品没问题」）。
    取不到的**个别**令牌只记**覆盖率缺口**（比如某令牌本就没写 hex 注释），不算缺陷。
    """
    hit, miss = 0, []
    for r in rows:
        raw = light.get(r["token"].lstrip("-"))
        if raw is not None and hex_in_comment(raw):
            hit += 1
        else:
            miss.append(r["token"])
    return hit, miss


def is_large_text(fs: float, fw: str) -> bool:
    """WCAG 2.1 对「**大字号文本**」的定义：`≥ 24px`，或 `≥ 18.66px` **且粗体**。

    出处：SC 1.4.3 的 large-scale text 定义（18pt = 24px；14pt bold = 18.66px）。
    ⇒ 只有它才配拿 3.0 的门槛；**其余文本一律 4.5**。
    """
    try:
        w = int(float(str(fw).strip() or 400))
    except ValueError:
        w = 400
    return fs >= 24 or (fs >= 18.66 and w >= 700)


BORDERLINE = 0.15          # 离门槛这么近 ⇒ 报「临界」，不判（测量本身有两位小数舍入）


def judge_render(m: dict) -> tuple[list[str], list[dict]]:
    """**T4：按字号取门槛**（WCAG 1.4.3 的完整形式）。

    ⚠️ **第一版判据太窄，已修正**：原稿把「3.0–4.5」一律列为「只报不判」，
    理由是「可能是大字号」。但大字号**是能判的**——`fs` / `fw` 都在手边。
    于是 12px 的链接（3.05:1）这种**硬缺陷**被放过了。这正是「判据比出处更窄」：
    出处（WCAG）本来就规定了大字号的界线，我却没去实现它。

    - 大字号（`is_large_text`）门槛 **3.0**；其余门槛 **4.5**。`< 门槛` ⇒ 缺陷。
    - 落在 `[门槛, 门槛+BORDERLINE)` ⇒ 报「**临界**」不判（两位小数舍入可能翻转）。

    ⚠️ 判的是**运行时有效**对比度：背景是**实际合成出来的**那层，
    不是规范表里的「白底」。二者可以不同 —— 例如 `--ink-500` 在白底是 4.74（规范标 4.7 ✓），
    放到 `--surface-subtle` 上就掉到 4.18。**规范只声明了白底**，所以这一层必须靠渲染级判。
    """
    problems: list[str] = []
    notes: list[dict] = []
    for it in m.get("texts") or []:
        fs = float(it.get("fs") or 0)
        big = is_large_text(fs, it.get("fw"))
        need = AA_LARGE if big else AA_TEXT
        r = it["ratio"]
        where = (f"<{it['tag']}> {it['fs']}px/{it['fw']} class=`{it['cls'][:60]}` "
                 f"文字=`{it['txt'][:24]}`")
        if r < need:
            kind = "大字号" if big else "正文尺寸"
            problems.append(f"[T4] 对比度 **{r:.2f}:1** < {need}（{kind} {it['fs']}px/"
                            f"{it['fw']} 的门槛）：{where}  fg={it['fg']} bg={it['bg']}")
        elif r < need + BORDERLINE:
            notes.append({"ratio": r, "need": need, "where": where,
                          "fg": it["fg"], "bg": it["bg"]})
    return problems, notes


def summary_exit(total: int, all_bad: list[str], control_broken: list[str]) -> int:
    """收口判定（抽成纯函数，便于自检锁住）。

    🚨 `total == 0` 必须返回 **2**（「我没测成」≠「产品健康」）。
    🚨 前提被破坏 ⇒ **2**：测量本身不可信，此时报红报绿都没有意义。
    """
    if control_broken:
        return 2
    if total == 0:
        return 2
    if all_bad:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 渲染级测量 JS（T4）—— 有效前景/背景，含 alpha 合成
# ---------------------------------------------------------------------------
MEASURE_JS = r"""(() => {
  const parse = (s) => {
    const m = (s || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 };
  };
  const over = (top, bot) => [0,1,2].map(k => top.rgb[k]*top.a + bot[k]*(1-top.a));

  const effBg = (el) => {
    const layers = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const s = getComputedStyle(node);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;  // 渐变/图片 ⇒ 算不出
      const c = parse(s.backgroundColor);
      if (c && c.a > 0) layers.push(c);
      node = node.parentElement;
    }
    let base = [255, 255, 255];
    for (let i = layers.length - 1; i >= 0; i--) base = over(layers[i], base);
    return base;
  };

  const lum = (rgb) => {
    const f = (x) => { x /= 255; return x <= 0.03928 ? x/12.92 : Math.pow((x+0.055)/1.055, 2.4); };
    return 0.2126*f(rgb[0]) + 0.7152*f(rgb[1]) + 0.0722*f(rgb[2]);
  };
  const ratio = (a, b) => {
    const la = lum(a), lb = lum(b);
    const hi = Math.max(la, lb), lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  };

  const out = {vw: innerWidth, texts: [], frozen: false,
               durProbe: (getComputedStyle(document.documentElement)
                          .getPropertyValue('--dur-fast') || '').trim(),
               contentLen: document.body ? document.body.innerText.length : 0};
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','OPTION','TITLE','HEAD']);

  for (const el of document.querySelectorAll('*')) {
    if (SKIP.has(el.tagName)) continue;
    // —— 只取**直接**文本节点 ——
    let txt = '';
    for (const n of el.childNodes) if (n.nodeType === 3) txt += n.textContent;
    txt = txt.replace(/\s+/g, ' ').trim();
    if (txt.length < 2) continue;
    // —— 可见性 ——
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none') continue;
    if (el.closest('[aria-hidden="true"]')) continue;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
    if ((s.webkitTextFillColor || '').includes('rgba(0, 0, 0, 0)')) continue;  // 渐变文字
    // —— 累积 opacity：< 1 时对比度要另行合成，本判据不支持 ⇒ 排除并计数 ——
    let op = 1, node = el;
    while (node && node.nodeType === 1) { op *= parseFloat(getComputedStyle(node).opacity || '1'); node = node.parentElement; }
    if (op < 0.999) continue;

    const bg = effBg(el);
    if (!bg) continue;                       // 背景含渐变/图片 ⇒ 排除
    const fg = parse(s.color);
    if (!fg) continue;
    const fgEff = fg.a >= 0.999 ? fg.rgb : over(fg, bg);
    const cr = ratio(fgEff, bg);
    out.texts.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.getAttribute('class') || '').slice(0, 120),
      txt: txt.slice(0, 60),
      fs: Math.round(parseFloat(s.fontSize) * 10) / 10,
      fw: s.fontWeight,
      fg: 'rgb(' + fgEff.map(Math.round).join(',') + ')',
      bg: 'rgb(' + bg.map(Math.round).join(',') + ')',
      ratio: Math.round(cr * 100) / 100,
    });
  }
  return out;
})()"""


# ---------------------------------------------------------------------------
# 取证 JS（`--why`）—— 判「深底深字」之前，先把浏览器真实解析出的颜色链打出来
# ---------------------------------------------------------------------------
WHY_JS = r"""(() => {
  const needle = __NEEDLE__;
  const parse = (s) => {
    const m = (s || '').match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(x => parseFloat(x));
    return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 };
  };
  const chain = (el) => {
    const out = []; let n = el;
    while (n && n.nodeType === 1) {
      const s = getComputedStyle(n);
      out.push({ tag: n.tagName.toLowerCase(),
                 cls: (n.getAttribute('class') || '').slice(0, 90),
                 bg: s.backgroundColor,
                 bgimg: s.backgroundImage === 'none' ? null : s.backgroundImage.slice(0, 44),
                 color: s.color });
      n = n.parentElement;
    }
    return out;
  };
  const out = [];
  for (const el of document.querySelectorAll('*')) {
    // 只看**直接文本节点**里含 needle 的（与 T4 的口径一致）
    const own = Array.from(el.childNodes).filter(n => n.nodeType === 3)
                  .map(n => n.textContent).join('').replace(/\s+/g, ' ').trim();
    if (!own.includes(needle)) continue;
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    out.push({ tag: el.tagName.toLowerCase(), cls: (el.getAttribute('class') || ''),
               txt: own.slice(0, 46), fs: s.fontSize, fw: s.fontWeight,
               color: s.color, opacity: s.opacity,
               w: Math.round(r.width), h: Math.round(r.height),
               kids: el.children.length,
               kidTxt: Array.from(el.children)
                         .map(c => c.tagName.toLowerCase() + '«' + (c.textContent || '').trim().slice(0, 14) + '»')
                         .slice(0, 5),
               chain: chain(el).slice(0, 7) });
  }
  return { n: out.length, items: out.slice(0, 8), colorScheme: getComputedStyle(document.documentElement).colorScheme };
})()"""


# ---------------------------------------------------------------------------
# 自检：纯函数臂（本门禁的判据层几乎全是纯计算 ⇒ 自检不需要浏览器）
# ---------------------------------------------------------------------------
def _row(token: str, hexv: str, declared: float, mark: str = "") -> dict:
    return {"token": token, "hex": hexv, "declared": declared, "mark": mark, "usage": ""}


def _mk_meas(**kw) -> dict:
    base = {"vw": 1280, "frozen": False, "durProbe": "120ms", "contentLen": 300,
            "texts": [{"tag": "p", "cls": "x", "txt": "正文示例", "fs": 15, "fw": "400",
                       "fg": "rgb(20,24,29)", "bg": "rgb(255,255,255)", "ratio": 16.8}]}
    base.update(kw)
    return base


# 自检夹具：**合成 CSS**（与产品无关），但**必须交给 `_block` 解析**再喂给判据。
_FIXTURE_LIGHT = """\
:root {
  --ink-400: 154 163 174; /* #9AA3AE 占位符 */
  --ink-500: 107 116 128; /* #6B7480 次要文本 */
  --ink-600: 74 82 92; /* #4A525C 正文辅助 */
  --ink-800: 33 38 45; /* #21262D 标题 */
  --verified-600: 22 112 85; /* #167055 */
  --verified-50: 237 248 244; /* #EDF8F4 */
  --pending-600: 143 95 20; /* #8F5F14 */
  --pending-50: 253 246 233; /* #FDF6E9 */
  --danger-500: 192 57 43; /* #C0392B */
  --danger-50: 253 240 238; /* #FDF0EE */
}
"""
_FIXTURE_DARK = """\
.dark {
  --text-primary: 242 244 246; /* #F2F4F6 */
  --text-secondary: 205 211 218; /* #CDD3DA */
  --surface-page: 11 14 18; /* #0B0E12 */
  --surface-card: 20 24 29; /* #14181D */
}
"""


async def run_self_test() -> int:
    print("自检（合成数据，与产品无关）—— 各臂分开报：")
    bad: list[str] = []

    # —— Q0：解析出的规范表**语义自检** ——
    for key, desc, rows, expect_bad in (
        ("Q0a", f"真实 `design-spec.md` 解析到 {len(SPEC_ROWS)} 行 ⇒ T0 门槛自洽**必须干净**",
         SPEC_ROWS, False),
        ("Q0b", "**人为塞一行**「✓ 但只有 3.0」⇒ T0 **必须报出**"
                "（锁住「合格线的出处变了要被发现」）",
         SPEC_ROWS + [_row("--fake-600", "#000000", 3.0, "✓")], True),
        ("Q0c", "**人为塞一行**「未标 ✓ 却高达 9.0」⇒ T0 **必须报出**",
         SPEC_ROWS + [_row("--fake-700", "#000000", 9.0, "")], True),
    ):
        probs = threshold_sanity(rows)
        got = bool(probs)
        ok = got == expect_bad
        print(f"  [{key}] {desc}")
        print(f"        期望{'报出' if expect_bad else '干净':4s} 实测{'报出' if got else '干净':4s} "
              f"{'✓' if ok else '✗'}")
        for p in probs[:1]:
            print(f"          · {p[:120]}")
        if not ok:
            bad.append(f"[{key}] 期望{'报出' if expect_bad else '干净'} 实测{'报出' if got else '干净'}")

    # —— Q0d：**T1a 的有效性控制必须真的会报 0** ——
    # 🚨 这条臂是拿血换的：Q2 曾一直绿，而真实的 T1a 是**死判据**。
    #    根因是**夹具的形状不是被测解析器的输出形状**（我手写 `"74 82 92; /* #4A525C */"`，
    #    而当时的 `_block` 用 `[^;]+` 取值、把注释切掉了）。
    #    ⇒ 夹具一律由 `_block` 产出；并单独锁住「取不到 hex 时 `t1a_coverage` 报 0」。
    fx_light = _block(_FIXTURE_LIGHT, ":root")
    fx_dark = _block(_FIXTURE_DARK, ".dark")
    good = _row("--ink-600", "#4A525C", 7.5, "✓")
    # 模拟「修好前的 `_block`」：按 `;` 切开、丢掉行尾注释
    _stripped = {k: v.split(";")[0].strip() for k, v in fx_light.items()}
    for key, desc, args, expect_hit in (
        ("Q0d1", "夹具由 `_block` 产出 ⇒ `hex_in_comment` **必须能取到** hex（核对 1 行）",
         (fx_light,), 1),
        ("Q0d2", "**故意喂「注释被切掉」的解析结果**（模拟修好前的 `_block`）⇒ "
                 "`t1a_coverage` **必须报 0 行**（否则「死判据」测不出来）",
         (_stripped,), 0),
    ):
        hit, miss = t1a_coverage([good], *args)
        ok = hit == expect_hit
        print(f"  [{key}] {desc}")
        print(f"        期望核对 {expect_hit} 行  实测核对 {hit} 行  取不到 {miss}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望核对 {expect_hit} 行 实测 {hit} 行")

    # —— Q1–Q6：T1a/T1'/T2/T3 的检出能力与对照组 ——
    # 🚨 **夹具必须由「被测的那个解析器」产出**（`_block`），不能手写字符串——见 Q0d1/Q0d2。
    # 🚨 夹具用**合成 CSS**（含 T3 需要的语义对），不用真实 `LIGHT`：
    #    自检失败的信息是「**工具坏了**」，若把「真实令牌层是否有缺陷」也塞进来，
    #    那条信息就会说谎（纪律：证据工具必须区分「产品坏了」与「我没测成」）。
    #    真实数据由 `--tokens-only` 负责。
    # 🚨 **`expect_note=None` = 本臂不检查文档问题**（合成夹具的 `danger-500` 对 `danger-50`
    #    实算 4.89 / 声明 5.4，Δ=-0.51 ⇒ 恒定带一条文档问题，与 Q1、Q5 要验的东西无关）。
    drift = _block(_FIXTURE_LIGHT.replace("74 82 92", "74 82 93").replace("#4A525C", "#4A525D"), ":root")
    lowcon = _block(_FIXTURE_LIGHT.replace("107 116 128", "148 148 148").replace("#6B7480", "#949494"), ":root")
    mislab = _block(_FIXTURE_LIGHT.replace("154 163 174", "118 118 118").replace("#9AA3AE", "#767676"), ":root")
    badver = _block(_FIXTURE_LIGHT.replace("22 112 85", "180 180 180"), ":root")
    cases: tuple[tuple[str, str, list[dict], dict, dict, bool, bool | None], ...] = (
        ("Q1", "规范值与令牌一致、声明值也算得对 ⇒ **必须干净**（对照组）",
         [good], fx_light, fx_dark, False, None),
        ("Q2", "令牌实际色值 ≠ 规范 hex ⇒ T1a **必须报红**（规范-实现漂移）",
         [good], drift, fx_dark, True, None),
        ("Q3a", "标了 `✓`（声明 4.7）但该 hex 实算只有 ~3.0 ⇒ T1' **必须报红**"
                "（标了「可用作文本」其实不够）",
         [_row("--ink-500", "#949494", 4.7, "✓")], lowcon, fx_dark, True, None),
        ("Q3b", "未标 `✓`（声明 2.5）但该 hex 实算有 ~4.5 ⇒ T1' **必须报红**"
                "（标记与事实不符：要么该标 ✓，要么它被误用）",
         [_row("--ink-400", "#767676", 2.5, "")], mislab, fx_dark, True, None),
        ("Q3c", "声明 13.9 而实算 15.22（Δ=1.32）⇒ **不判红**，只记一条**文档问题** —— "
                "这条臂锁住「数值偏差不算产品缺陷」（我最初写成精确匹配，就是被这 5 条假红咬的）",
         [_row("--ink-800", "#21262D", 13.9, "✓")], fx_light, fx_dark, False, True),
        ("Q4", "深色档 `--text-primary` 被改成深色 ⇒ T2 **必须报红**（正文读不出）",
         [], fx_light, {**fx_dark, "text-primary": "20 24 29"}, True, None),
        ("Q5", "深色档正常 ⇒ T2 **必须干净**（对照组）",
         [], fx_light, fx_dark, False, None),
        ("Q6", "`--verified-600` 对浅底算出来只有 ~1.9，规范声明 6.0 ⇒ T3 **必须报红**",
         [], badver, fx_dark, True, None),
    )
    for key, desc, rows, light, dark, expect_red, expect_note in cases:
        probs, notes = judge_tokens(rows, light, dark)
        got_red, got_note = bool(probs), bool(notes)
        ok = got_red == expect_red and (expect_note is None or got_note == expect_note)
        print(f"  [{key}] {desc}")
        exp = f"{'报红' if expect_red else '干净'}/{'-' if expect_note is None else ('报文档' if expect_note else '不报')}"
        gt = f"{'报红' if got_red else '干净'}/{'-' if expect_note is None else ('报文档' if got_note else '不报')}"
        print(f"        期望{exp:9s} 实测{gt:9s} {'✓' if ok else '✗'}")
        for p in probs[:2]:
            print(f"          · 缺陷 {p[:126]}")
        for n in notes[:1]:
            print(f"          · 文档 {n['token']}：声明 {n['declared']} 实算 {n['computed']}"
                  f"（Δ{n['delta']:+}）")
        if not ok:
            bad.append(f"[{key}] 期望 {exp} 实测 {gt}")

    # —— Q7–Q9：T4 渲染级判据（纯函数喂数据）——
    def _t(ratio: float, fs: int = 15, fw: str = "400") -> dict:
        return {"tag": "p", "cls": "text-ink-400", "txt": "一段正文文字", "fs": fs, "fw": fw,
                "fg": "rgb(154,163,174)", "bg": "rgb(255,255,255)", "ratio": ratio}

    for key, desc, meas, expect_red, expect_note in (
        ("Q7", "对比度 2.5、15px ⇒ **必须报红**（正文尺寸门槛 4.5）",
         _mk_meas(texts=[_t(2.5)]), True, False),
        ("Q8a", "对比度 4.0、**15px** ⇒ **必须报红** —— 这是**升级判据后新增的检出能力**"
                "（旧版把 3.0–4.5 一律「只报不判」，把这类硬缺陷放过了）",
         _mk_meas(texts=[_t(4.0)]), True, False),
        ("Q8b", "对比度 4.0、**26px**（≥24px 算大字号）⇒ **不判红**（门槛降到 3.0）",
         _mk_meas(texts=[_t(4.0, fs=26)]), False, False),
        ("Q8c", "对比度 4.0、**19px/700**（≥18.66px **粗体**算大字号）⇒ **不判红**",
         _mk_meas(texts=[_t(4.0, fs=19, fw="700")]), False, False),
        ("Q8d", "对比度 4.0、**19px/400**（够大但**不粗**）⇒ **必须报红**"
                "（锁住「粗体才算大字号」这条界线）",
         _mk_meas(texts=[_t(4.0, fs=19, fw="400")]), True, False),
        ("Q8e", "对比度 4.0、**18px/700**（差 0.66px 到 18.66）⇒ **必须报红**"
                "（锁住 18.66px 这个下界，别写成 18）",
         _mk_meas(texts=[_t(4.0, fs=18, fw="700")]), True, False),
        ("Q8f", "对比度 **4.55**、15px（离门槛 0.05）⇒ **不判红**但报「临界」"
                "（两位小数舍入可能翻转，不拿它定罪）",
         _mk_meas(texts=[_t(4.55)]), False, True),
        ("Q9", "对比度 16.8 ⇒ **必须干净且不报**（对照组）",
         _mk_meas(texts=[_t(16.8)]), False, False),
    ):
        probs, notes = judge_render(meas)
        got_red, got_note = bool(probs), bool(notes)
        ok = got_red == expect_red and got_note == expect_note
        print(f"  [{key}] {desc}")
        print(f"        期望{'报红' if expect_red else '干净':4s}/"
              f"{'报出' if expect_note else '不报':4s} 实测{'报红' if got_red else '干净':4s}/"
              f"{'报出' if got_note else '不报':4s} {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望{expect_red}/{expect_note} 实测{got_red}/{got_note}")

    # —— Q10–Q13：收口判定 ——
    for key, desc, args, expect in (
        ("Q10", "收口判定：**前提被破坏 ⇒ exit 2**", (8, [], ["门槛不成立"]), 2),
        ("Q11", "收口判定：**0 组测量不得算通过**", (0, [], []), 2),
        ("Q12", "收口判定：有缺陷 ⇒ exit 1", (8, ["bad"], []), 1),
        ("Q13", "收口判定：全通过 ⇒ exit 0", (8, [], []), 0),
    ):
        got = summary_exit(*args)
        ok = got == expect
        print(f"  [{key}] {desc}")
        print(f"        期望 exit {expect}  实测 exit {got}  {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望 exit {expect} 实测 exit {got}")

    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **工具坏了，不是产品坏了**（exit 2）")
        for x in bad:
            print(f"  - {x}")
        return 2
    print("\n自检通过：门槛自洽（Q0a–Q0c）、**T1a 有效性控制**（Q0d1/Q0d2）、"
          "规范-实现漂移与定性自洽（Q1/Q2/Q3a/Q3b/Q3c）、深色正文（Q4/Q5）、语义状态对（Q6）、"
          "**渲染级按字号取门槛**（Q7–Q9）、收口判定（Q10–Q13）。")
    return 0


# ---------------------------------------------------------------------------
# 实跑
# ---------------------------------------------------------------------------
async def login(b: Browser, base: str, username: str) -> bool:
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == username)
    await b.goto(f"{base}/login", wait=1.5)
    await b.type_into("#login-username", u["username"])
    await b.type_into("#login-password", u["password"])
    await asyncio.sleep(0.4)
    await b.click_text("登录")
    await asyncio.sleep(4.0)
    return "/login" not in (await b.cdp.evaluate("location.href") or "")


def _premise(m: dict | None, vw: int) -> list[str]:
    if not m:
        return ["未取到测量结果"]
    if m.get("vw") != vw:
        return [f"视口 {m.get('vw')} ≠ {vw}"]
    if not (m.get("durProbe") or "").strip():
        return ["读不到 `--dur-fast` ⇒ `tokens.css` 没加载（页面不是应用，或停在错误页）"]
    if (m.get("contentLen") or 0) < MIN_CONTENT:
        return [f"正文仅 {m.get('contentLen')} 字符 ⇒ 疑似未渲染完"]
    if not (m.get("texts") or []):
        return ["一个可判文本元素都没有 ⇒ 测量没取到东西"]
    return []


async def run_tokens_only() -> int:
    """只跑 T0–T3（纯计算，秒级）。"""
    sane = threshold_sanity(SPEC_ROWS)
    if sane:
        print("[env] **T0 门槛自洽失败** ⇒ 「4.5 是本项目合格线」的前提不成立（exit 2）：")
        for s in sane:
            print(f"  · {s}")
        return 2
    print(f"T0 门槛自洽 ✓ —— 合格线 {AA_TEXT} 来自规范自身的 `✓` 标记"
          f"（{len(SPEC_ROWS)} 行声明，最低的 ✓ 是 "
          f"{min(r['declared'] for r in SPEC_ROWS if r['mark'] == '✓')}）\n")
    # —— T1a 的有效性控制：**先证明这条判据能变红**，再拿它的绿当结论 ——
    cov, missing = t1a_coverage(SPEC_ROWS, LIGHT)
    if cov == 0:
        print(f"[env] **T1a 有效性控制失败**：规范表 {len(SPEC_ROWS)} 行里**一行 hex 都取不到**\n"
              f"      ⇒ T1a（规范-实现漂移）结构上不可能变红，它的绿没有信息量\n"
              f"      ⇒ 这是「我没测成」，不是「产品没问题」（exit 2）。取不到的令牌：")
        for t in missing:
            print(f"        · {t}")
        return 2
    probs, notes = judge_tokens(SPEC_ROWS, LIGHT, DARK)
    if probs:
        print(f"T1–T3 缺陷 {len(probs)} 条：")
        for p in probs:
            print(f"  ✗ {p}")
        return 1
    print(f"T1a：**实际核对 {cov}/{len(SPEC_ROWS)} 行** —— 令牌色值 == 规范 hex ✓")
    if missing:
        print(f"     ⚠️ 覆盖率缺口：{missing} 取不到 hex ⇒ T1a 对它们**不判**（不是通过）")
    print(f"T1'：标 `✓` 的全部 ≥ {AA_TEXT}、未标 `✓` 的全部 < {AA_TEXT}（定性自洽）✓")
    print(f"T2：深色档 {len(DARK_TEXT_PAIRS)} 对正文×表面 ≥ {AA_TEXT} ✓")
    print(f"T3：语义状态文本对 {len(STATUS_TEXT_PAIRS)} 对 ≥ {AA_TEXT} ✓")
    if notes:
        print(f"\n⚠️ **文档问题**（不是产品缺陷）：规范表声明值与 WCAG 重算差 > {DOC_TOL} 的有 "
              f"{len(notes)} 条 —— 那一列是**近似值**，只建议规范侧改数：")
        for n in notes:
            print(f"   · {n['token']:22s} 规范 {n['declared']:5.1f}:1  实算 {n['computed']:5.2f}:1"
                  f"  （Δ{n['delta']:+.2f}）")
    return 0


async def run(only: str | None, tokens_only: bool, dump: bool) -> int:
    if tokens_only:
        return await run_tokens_only()

    sane = threshold_sanity(SPEC_ROWS)
    if sane:
        print("[env] **T0 门槛自洽失败** ⇒ 合格线的出处不成立（exit 2）：")
        for s in sane:
            print(f"  · {s}")
        return 2

    total, all_bad, control_broken, skipped = 0, [], [], []
    bad_groups: list[str] = []
    notes: list[dict] = []
    # T1a 的有效性控制：**先证明这条判据能变红**，再拿它的绿当结论。
    cov, missing = t1a_coverage(SPEC_ROWS, LIGHT)
    if cov == 0:
        print(f"[env] **T1a 有效性控制失败**：规范表 {len(SPEC_ROWS)} 行里一行 hex 都取不到\n"
              f"      ⇒ T1a（规范-实现漂移）结构上不可能变红，它的绿没有信息量 ⇒ 「我没测成」（exit 2）")
        return 2

    print(f"期望值来源：`{SPEC_MD.relative_to(ROOT)}` §2.1/2.2/2.3 的对比度列")
    print(f"T0 推导出的合格线：{AA_TEXT}（规范所有 `✓` 行都 ≥ 它、所有非 `✓` 行都 < 它）")
    print(f"T1'/T3 断言的是**定性**（标 `✓` ⇒ ≥ {AA_TEXT}）；声明值偏差 > {DOC_TOL} 只记**文档问题**")
    print(f"T4 按字号取门槛：大字号（≥24px 或 ≥18.66px 粗体）{AA_LARGE}、其余 {AA_TEXT}；"
          f"离门槛 <{BORDERLINE} 报临界不判\n")

    for vw, vh, mobile, vtag in VIEWPORTS:
        async with Browser(headless=True, width=vw, height=vh) as b:
            await b.apply_device(safe_area=None, mobile=mobile)
            for app, cfg in APPS.items():
                if only and app != only:
                    continue
                base = f"http://localhost:{cfg['port']}"
                try:
                    await b.cdp.send("Network.clearBrowserCookies")
                except Exception:
                    pass
                if not await login(b, base, cfg["user"]):
                    print(f"  [env] {app} 登录失败 ⇒ 本端不判")
                    skipped.append(f"{app}: 登录失败")
                    continue
                for path in cfg["pages"]:
                    for theme in ("light", "dark"):
                        await b.cdp.evaluate(f"localStorage.setItem('nlaw-theme', {theme!r})")
                        await b.goto(f"{base}{path}", wait=1.6)
                        await b.settle(extra=1.2)
                        m = await b.cdp.evaluate(MEASURE_JS)
                        label = f"{app}{path} @{vtag}/{theme}"
                        pre = _premise(m, vw)
                        if pre:
                            print(f"  {label:26s} 前提不成立：{pre[0]} ⇒ **不判**")
                            skipped.append(f"{label}: {pre[0]}")
                            continue
                        total += 1
                        probs, nt = judge_render(m)
                        notes.extend({"label": label, **x} for x in nt)
                        if probs:
                            print(f"  {label:26s} ✗  文本元素 {len(m['texts'])}")
                            for p in probs:
                                print(f"      {p}")
                            bad_groups.append(label)
                            all_bad.extend(f"{label} {p}" for p in probs)
                        else:
                            print(f"  {label:26s} ✓  文本元素 {len(m['texts'])}"
                                  f"  区间内 {len(nt)}")

    print("\n" + "=" * 74)
    tok, tok_notes = judge_tokens(SPEC_ROWS, LIGHT, DARK)
    print(f"[T1–T3 令牌层] T1a **实际核对 {cov}/{len(SPEC_ROWS)} 行**"
          f"（覆盖率缺口：{missing or '无'}）")
    print(f"                规范表 {len(SPEC_ROWS)} 行 + 深色 {len(DARK_TEXT_PAIRS)} 对 + "
          f"状态对 {len(STATUS_TEXT_PAIRS)} 对 ⇒ 缺陷 {len(tok)} 条")
    for p in tok:
        print(f"  ✗ {p}")
    if tok_notes:
        print(f"  ⚠️ 另有**文档问题** {len(tok_notes)} 条（规范表数值是近似值，**不算产品缺陷**）：")
        for n in tok_notes:
            print(f"     · {n['token']:22s} 规范 {n['declared']:5.1f}:1  实算 {n['computed']:5.2f}:1"
                  f"  （Δ{n['delta']:+.2f}）")
    print(f"\n[T4 渲染层] 测量 {total} 组；**不合格 {len(bad_groups)} 组 / 缺陷 {len(all_bad)} 条**。")
    if notes:
        notes.sort(key=lambda x: x["ratio"])
        print(f"⚠️ T4 **临界不判**：{len(notes)} 处离门槛不到 {BORDERLINE}"
              f"（两位小数舍入可能翻转）。最低 5 条：")
        for x in notes[:5]:
            print(f"   · {x['ratio']:.2f}:1（门槛 {x['need']}）  {x['where'][:110]}")
    if skipped:
        print(f"跳过 {len(skipped)} 组：")
        for s in skipped:
            print(f"  · {s}")

    broken = control_broken + tok
    code = summary_exit(total, all_bad, broken)
    if code == 2:
        print("\n[env] **一组都没测到** ⇒ 这是「我没测成」，不是「产品没问题」（exit 2）")
        return 2
    if code == 1:
        return 1
    if skipped:
        print(f"\n⚠️ 通过，但**有 {len(skipped)} 组被跳过** ⇒ 覆盖率不满。")
    print("对比度：规范-实现漂移、规范表定性自洽、深色正文、语义状态对、渲染级（**按字号取门槛**）"
          " —— 限已测到的组。")
    print("⚠️ 已知边界：不含非文本对比度（WCAG 1.4.11）；T4 排除渐变/图片背景、半透明、渐变文字、"
          "禁用/隐藏元素；未声明的令牌组合只报数值不判。")
    return 0


async def dump(only: str | None) -> int:
    """诊断模式：打原始数值，**不下判断**。"""
    print("── T1/T3：规范声明 vs 实算 ──")
    for r in SPEC_ROWS:
        raw = LIGHT.get(r["token"].lstrip("-"), "")
        rgb = triple_to_rgb(raw)
        got = contrast(rgb, WHITE) if rgb else float("nan")
        print(f"  {r['token']:18s} 规范 {r['hex']} 声明 {r['declared']:5.1f}:1 "
              f"{r['mark']:2s}  实算 {got:5.2f}:1   令牌={hex_in_comment(raw)}")
    print("\n── T3：语义状态文本对 ──")
    for fg, bg, declared, src in STATUS_TEXT_PAIRS:
        f, bk = triple_to_rgb(LIGHT.get(fg, "")), triple_to_rgb(LIGHT.get(bg, ""))
        got = contrast(f, bk) if f and bk else float("nan")
        print(f"  --{fg:14s} on --{bg:14s} 规范 {declared:4.1f}:1  实算 {got:5.2f}:1   （{src}）")
    print("\n── 未声明的语义组合（**只报不判**）──")
    for fg, bg in (("ai-500", "ai-50"), ("gold-500", "gold-50"), ("info-500", "info-50"),
                   ("text-muted", "surface-page"), ("text-faint", "surface-page"),
                   ("link", "surface-page")):
        f, bk = triple_to_rgb(LIGHT.get(fg, "")), triple_to_rgb(LIGHT.get(bg, ""))
        if f and bk:
            print(f"  --{fg:14s} on --{bg:14s} 实算 {contrast(f, bk):5.2f}:1")
    print("\n── T2：深色档正文 × 表面 ──")
    for fg, bg in DARK_TEXT_PAIRS:
        f, bk = triple_to_rgb(DARK.get(fg, "")), triple_to_rgb(DARK.get(bg, ""))
        if f and bk:
            print(f"  --{fg:14s} on --{bg:14s} 实算 {contrast(f, bk):5.2f}:1")

    if only == "__tokens__":
        return 0
    print("\n── T4：渲染级（最低 15 个）──")
    for vw, vh, mobile, vtag in VIEWPORTS:
        async with Browser(headless=True, width=vw, height=vh) as b:
            await b.apply_device(safe_area=None, mobile=mobile)
            for app, cfg in APPS.items():
                if only and app != only:
                    continue
                base = f"http://localhost:{cfg['port']}"
                try:
                    await b.cdp.send("Network.clearBrowserCookies")
                except Exception:
                    pass
                if not await login(b, base, cfg["user"]):
                    continue
                await b.cdp.evaluate("localStorage.setItem('nlaw-theme', 'light')")
                await b.goto(f"{base}{cfg['pages'][0]}", wait=1.6)
                await b.settle(extra=1.2)
                m = await b.cdp.evaluate(MEASURE_JS)
                rows = sorted(m.get("texts") or [], key=lambda x: x["ratio"])[:15]
                print(f"\n── {app}{cfg['pages'][0]} @{vtag} 文本元素 {len(m.get('texts') or [])}，最低 {len(rows)}：")
                for it in rows:
                    print(f"   {it['ratio']:6.2f}:1  <{it['tag']}> {it['fs']}px/{it['fw']} "
                          f"fg={it['fg']} bg={it['bg']} class=`{it['cls'][:56]}`  `{it['txt'][:20]}`")
    return 0


async def why(app: str, path: str | None, needle: str) -> int:
    """**取证模式**：把「含 `needle` 的可见文本元素」的颜色链打出来，**不下判断**。

    为什么需要它：判「深底深字」这类缺陷之前，必须先证明**浏览器真实解析出的**前景/背景
    是什么、那个颜色是**从哪一层**继承/覆盖来的。否则很容易把「我的测量错了」
    报成产品缺陷（本门禁的 T4a 就是靠它复核的）。
    """
    import json as _json

    if app not in APPS:
        print(f"[env] 未知端 `{app}`（可选：{'/'.join(APPS)}）")
        return 2
    cfg = APPS[app]
    js = WHY_JS.replace("__NEEDLE__", _json.dumps(needle, ensure_ascii=False))
    base = f"http://localhost:{cfg['port']}"
    rc = 2
    for vw, vh, mobile, vtag in VIEWPORTS:
        async with Browser(headless=True, width=vw, height=vh) as b:
            await b.apply_device(safe_area=None, mobile=mobile)
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                print(f"  [env] {app} 登录失败 ⇒ 本端不判")
                continue
            for theme in ("light", "dark"):
                for p in ([path] if path else cfg["pages"]):
                    await b.cdp.evaluate(f"localStorage.setItem('nlaw-theme', {theme!r})")
                    await b.goto(f"{base}{p}", wait=1.6)
                    await b.settle(extra=1.2)
                    m = await b.cdp.evaluate(js) or {}
                    items = m.get("items") or []
                    print(f"\n── {app}{p} @{vtag}/{theme}  color-scheme={m.get('colorScheme')}  "
                          f"命中 {m.get('n', 0)} 个（直接文本节点含「{needle}」）──")
                    if not items:
                        print("   （无命中）")
                    for it in items:
                        print(f"   <{it['tag']}> {it['fs']}/{it['fw']} color={it['color']} "
                              f"opacity={it['opacity']} {it['w']}×{it['h']} "
                              f"子元素 {it['kids']} {it['kidTxt']}")
                        print(f"      class=`{it['cls']}`")   # 取证模式：**打全**，不截断
                        _fs = float(re.sub(r"[^\d.]", "", it["fs"]) or 0)
                        print(f"      文本=`{it['txt']}`  "
                              f"大字号={is_large_text(_fs, it['fw'])}（{it['fs']}/{it['fw']}）")
                        for i, c in enumerate(it["chain"]):
                            who = "自身" if i == 0 else f"父{i}"
                            print(f"      {who:4s} <{c['tag']}> bg={c['bg']} color={c['color']}"
                                  f"{'  ⚠️bgimg=' + str(c['bgimg']) if c['bgimg'] else ''}"
                                  f"  `{c['cls'][:66]}`")
                    rc = 0
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description="对比度门禁（WCAG 1.4.3 / 规范 §2 声明）")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（合成数据）")
    ap.add_argument("--tokens-only", action="store_true", help="只跑 T0–T3（不开浏览器）")
    ap.add_argument("--dump", action="store_true", help="诊断模式：打原始数值，不下判断")
    ap.add_argument("--why", default=None, metavar="TEXT",
                    help="取证模式：打印含该文本的元素颜色链（配 --app / --path），不下判断")
    ap.add_argument("--path", default=None, help="配合 --why：只测该路径")
    ap.add_argument("--app", default=None, help="只测某一端（web/lawyer/admin/im）")
    a = ap.parse_args()
    if a.self_test:
        return asyncio.run(run_self_test())
    if a.why:
        return asyncio.run(why(a.app or "web", a.path, a.why))
    if a.dump:
        return asyncio.run(dump(a.app))
    return asyncio.run(run(a.app, a.tokens_only, a.dump))


if __name__ == "__main__":
    raise SystemExit(main())
