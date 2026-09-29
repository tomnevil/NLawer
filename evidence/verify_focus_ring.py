#!/usr/bin/env python3
r"""§4.3「焦点环统一」门禁（第六个维度）。

## 为什么要有这条

`§4.3「焦点环统一：0 0 0 3px」`（§4.3 末行）写着一句**带具体值**的硬要求：

> 焦点环统一：`0 0 0 3px rgba(54,96,176,.22)`

并且 §9「可访问性：键盘可达、焦点环统一、对比度复核」（§9 阶段四 · 可访问性）把它列进验收清单：
「键盘可达、**焦点环统一**、对比度复核、`prefers-reduced-motion`、移动端 VoiceOver / TalkBack」。

**而 `evidence/` 下 19 个门禁一条都没判它** —— 盘法：
`grep -rln 'focus\|outline\|ring' evidence/*.py` 的命中全是**无关**的
（探针自己聚焦输入框、`verify_tap_targets.py` 用 `elementFromPoint` 等），
**没有一条断言过焦点环的值**。

## 出处（不自己发明期望值）

| 判据 | 期望 | 出处 |
|---|---|---|
| 焦点环 | `0 0 0 3px rgba(54,96,176,.22)` | §4.3 末行（`§4.3「焦点环统一：0 0 0 3px」`） |
| 「统一」 | 全站只有**一种**环宽、**一种**环色 | 同上（措辞就是「统一」） |
| 焦点环**可见** | 可聚焦元素必须有可见焦点指示 | §9 阶段四「键盘可达」+ WCAG 2.4.7 (AA) |
| 令牌自洽 | `--focus-ring` == `rgba(54,96,176,.22)`，且 `--brand-500` == `54 96 176` | §4.3 + `tokens.css:48` 自述（「#3660B0 **焦点环**、进度条…」） |

`0 0 0 3px rgba(54,96,176,.22)` **不是我编的**：它等于 `--brand-500`（`54 96 176`）@ **22%**，
而 `--brand-500` 的注释自己写着「**焦点环**」。三者（规范值 / 令牌 / 令牌注释）互相印证。

## 为什么必须分「令牌层 / 源码层 / 渲染层」

1. **令牌层**：`--focus-ring` 与 `boxShadow.focus` 的值是不是规范那个 —— 静态可判。
2. **源码层**：**「统一」是一条关于「集合大小」的判据** —— 要收集全站焦点环的
   (环宽, 环色) 取值集合，断言 `len(set) == 1`。单看一处永远看不出「不统一」。
3. **渲染层**：`:focus-visible` 是**伪类**，只有在**真的把它逼出来**之后才知道
   浏览器算出的 `box-shadow` / `outline` 是什么。静态读源码**证明不了**运行时。
   ⇒ 用 CDP 的 `CSS.forcePseudoState(['focus-visible'])` + `CSS.getComputedStyleForNode`
   直接读**强制伪类之后**的计算样式（这是唯一不靠「模拟键盘 Tab」的办法）。

## 判据表

| 编号 | 层 | 检查 | 期望 |
|---|---|---|---|
| **F1** | 令牌 | `boxShadow.focus` == `0 0 0 3px var(--focus-ring)` | §4.3 末行 |
| **F2** | 令牌 | `--focus-ring` == `rgba(54,96,176,.22)` 且 `--brand-500` == `54 96 176` | §4.3 + `tokens.css:48` |
| **F3** | 源码 | 焦点环**环宽**取值集合只有 1 种，且 == 3px | §4.3「统一」+ `3px` |
| **F4** | 源码 | 焦点环**环色/透明度**取值集合只有 1 种，且 == `brand-500` @ 22% | 同上 |
| **F5** | 源码 | 规范令牌（`shadow-focus`）**有调用点** | 防「定义了没人用」 |
| **F6** | 渲染 | 规范焦点环**真的出现在** ≥1 个可聚焦元素上 | §4.3 |
| **F7** | 渲染 | 每个**自身可见**的可聚焦元素都必须有**可见**焦点指示 | §9「键盘可达」/ WCAG 2.4.7 |
| **F8** | 渲染 | 焦点环取值**分布**（只报不判，供核对与登记例外） | — |

> **F4 的边界（不许比出处更宽）**：`focus-visible:ring-danger-500/30`（危险/破坏性动作）
> 是**有意的设计取舍**，规范**没有**声明例外 —— 所以它**只报不判**，
> 并登记为「**规范缺口：未声明危险态焦点环**」。

## 🚨 两个正则陷阱（本门禁自检当场抓到）

**陷阱 1：环宽正则把「环色」读成了「环宽」。** 初版

    RING_W_RE = (?:focus|…):(ring(?:-\d+|\[[^\]]+\])?)(?![a-zA-Z0-9\[])

对 `focus:ring-brand-500/30`：`ring` 后面是 `-`，而 `-` **不在**负向字符类里
⇒ 匹配成功、捕获 `ring` ⇒ 被换算成 **3px**。实测后果：真实仓库里
`focus:ring-brand-500/30` 有 40 处，于是环宽集合变成 `{ring-2: 2px, ring: 3px}`
⇒ **F3 会报一条完全虚假的「环宽不统一」**。`-` 必须进负向类。

**陷阱 2：环色正则把「修饰符」读成了「颜色」。** `ring-[A-Za-z][\w-]*` 会把
`focus:ring-inset`（内阴影修饰符）、`focus:ring-offset-2`、`focus:ring-offset-surface`
（偏移工具类）当成颜色 ⇒ F4 的明细里混进 `inset` / `offset-2`。
真实仓库里这三者各 1–2 处，混进来只会让「不统一」的**明细不可信**。

**陷阱 3（不是正则）：夹具写错了形式，掩盖了崩溃。** 初版夹具写成
`focus:ring-2 ring-brand-500/30`（环色**不带** `focus:` 前缀）——
于是 `colors` 恒为空集，`judge_uniform` 里 `cov += len(colors and sum(...) or [])`
的**崩溃分支永远走不到**，自检 33 条全绿的同时 `--source-only` 直接 `TypeError`。
⇒ **夹具必须照抄真实源码的形式**，否则自检只是「在测另一份东西」。
实测真实形式：本仓库焦点环色 **100% 带 `focus*:` 前缀**；
不带前缀的 `ring-<色>`（`ring-pending-500/20`、`ring-brand-500/70`）全是
**选中/激活态**（`current && …` / `active && …`），**不是焦点环** ⇒
把它们算进焦点环色集合会造出假红，故本判据**要求前缀**，并把
「与 `focus*:ring` 同行的裸环色」降级为**只报**（覆盖率缺口的可见化）。

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（读不到 preset/tokens / CDP 的 CSS 域不可用 / 覆盖率 0）

用法：
    python evidence/verify_focus_ring.py                 # 全跑（含浏览器）
    python evidence/verify_focus_ring.py --source-only    # F1–F5（不开浏览器）
    python evidence/verify_focus_ring.py --self-test      # 证明每条判据都会红
    python evidence/verify_focus_ring.py --dump           # 打原始数值，不下判断
    python evidence/verify_focus_ring.py --why 文本        # 取证：某个可聚焦元素的焦点态
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

ROOT = HERE.parents[0]
FRONTEND = ROOT / "frontend"
SPEC_MD = ROOT / "deliverables" / "ui-design" / "design-spec.md"
PRESET_TS = FRONTEND / "tailwind.preset.ts"
TOKENS_CSS = FRONTEND / "packages" / "ui" / "src" / "tokens.css"

# §4.3 末行的规范焦点环
SPEC_RING = "0 0 0 3px rgba(54, 96, 176, 0.22)"
SPEC_RING_W = 3.0                       # px
SPEC_RING_RGB = (54.0, 96.0, 176.0)     # == --brand-500
SPEC_RING_ALPHA = 0.22
BRAND_500 = "54 96 176"

# Tailwind 的 ring 宽度刻度（`ring` 无数字时 = 3px）
RING_WIDTHS = {"ring": 3.0, "ring-0": 0.0, "ring-1": 1.0, "ring-2": 2.0,
               "ring-4": 4.0, "ring-8": 8.0}

FOCUSABLE = ('a[href], button, input, select, textarea, summary, '
             '[tabindex]:not([tabindex="-1"]), [role="button"], [role="tab"], '
             '[role="menuitem"], [contenteditable="true"]')

# 每页最多逼出多少个可聚焦元素的焦点态（CDP 每个元素要 3 次调用，全量会很慢）
FOCUS_SAMPLE = 40

# 一次 `Runtime.evaluate` 取回**全部**可聚焦元素的 class 与可见性（文档序，与
# `DOM.querySelectorAll` 同序）—— 用来决定采样对象，避免逐个元素问一遍。
JS_FOCUSABLE_INFO = r"""(() => {
  const els = Array.from(document.querySelectorAll(%s));
  return els.map(el => {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return {
      cls: el.getAttribute('class') || '',
      tag: el.tagName.toLowerCase(),
      visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden'
               && cs.display !== 'none' && parseFloat(cs.opacity) > 0,
    };
  });
})()"""

APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin",
            "pages": ("/", "/qa", "/documents", "/contract-review", "/compliance",
                      "/knowledge", "/billing")},
    "lawyer": {"port": 3001, "user": "lawyer_wang",
               "pages": ("/", "/dispatches", "/cases", "/cases/@first-case",
                         "/reviews", "/archives", "/notifications")},
    "admin": {"port": 3002, "user": "admin",
              "pages": ("/", "/reviews", "/cases", "/dispatches", "/compliance",
                        "/billing", "/complaints", "/audit")},
    "im": {"port": 3003, "user": "client",
           "pages": ("/", "/chat", "/cases", "/cases/@first-case", "/me")},
}
MIN_CONTENT = 40


# ---------------------------------------------------------------------------
# 令牌层解析
# ---------------------------------------------------------------------------
def _brace_block(text: str, key: str) -> str:
    i = text.find(key)
    if i < 0:
        return ""
    j = text.find("{", i)
    if j < 0:
        return ""
    depth, k = 0, j
    while k < len(text):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j + 1:k]
        k += 1
    return ""


def parse_preset_shadow(text: str | None = None) -> dict[str, str]:
    """`tailwind.preset.ts` 的 `boxShadow` 块 → `{键: 值}`。"""
    if text is None:
        text = PRESET_TS.read_text(encoding="utf-8", errors="ignore")
    block = _brace_block(text, "boxShadow")
    out: dict[str, str] = {}
    for m in re.finditer(r'["\']?([\w-]+)["\']?\s*:\s*"([^"]*)"', block):
        out[m.group(1)] = m.group(2)
    return out


def parse_tokens(text: str | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """`tokens.css` 的浅色（`:root`）与深色（`.dark`）变量 → `(light, dark)`。"""
    if text is None:
        text = TOKENS_CSS.read_text(encoding="utf-8", errors="ignore")
    light = _block_vars(text, ":root")
    dark = _block_vars(text, ".dark")
    return light, dark


def _block_vars(css: str, selector: str) -> dict[str, str]:
    """取某个选择器块里的 `--x: v;`。

    🚨 选择器必须**紧跟** `{`（`re.escape(sel) + r"\\s*\\{"`），不能用裸
    `css.find(".dark")` —— `tokens.css:12` 的注释里就有 `.dark .xxx { !important }`，
    裸 `find` 会命中注释、把注释里的 `{...}` 当成块体 ⇒ **深色档整个读空**
    （实测：`--focus-ring(dark)` 打出 `None`，而真实值在 `:353`）。
    这与 `verify_contrast.py:186` 的写法一致 —— 那个门禁因为要求紧跟 `{`，没踩这个坑。
    """
    m = re.search(re.escape(selector) + r"\s*\{", css)
    if not m:
        return {}
    j, depth, k = m.end() - 1, 0, m.end() - 1
    while k < len(css):
        if css[k] == "{":
            depth += 1
        elif css[k] == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    body = css[j + 1:k]
    out: dict[str, str] = {}
    for mm in re.finditer(r"--([\w-]+)\s*:\s*([^;{}]+)", body):
        out.setdefault(mm.group(1), mm.group(2).strip())
    return out


VAR_RE = re.compile(r"var\(\s*--([\w-]+)\s*(?:,[^)]*)?\)")


def resolve_vars(value: str, *maps: dict[str, str]) -> str:
    """把 `var(--x)` 展开成令牌值（按 `maps` 顺序取第一个命中的）。

    🚨 **F1 必须比「展开后的值」，不能比字面量**：preset 写的是
    `0 0 0 3px var(--focus-ring)`，规范写的是 `0 0 0 3px rgba(54,96,176,.22)` ——
    两者**语义相等**（F2 已证明 `--focus-ring` 就是那个值）。
    按字面量比会把一个**完全正确**的 preset 报成缺陷（实测踩到：假红一条），
    还会误导人去「修」一个本来就对的文件。
    展开不了的 `var()` 原样保留 ⇒ 与规范不等 ⇒ 报红（并另给一条 note）。
    """
    def rep(m: re.Match) -> str:
        name = m.group(1)
        for mp in maps:
            if name in mp:
                return mp[name].split(";")[0].strip()
        return m.group(0)
    out = value or ""
    for _ in range(6):                      # 允许令牌套令牌
        nxt = VAR_RE.sub(rep, out)
        if nxt == out:
            break
        out = nxt
    return out


def norm_color(v: str) -> str:
    """把 `rgba(54,96,176,.22)` / `rgba(54, 96, 176, 0.22)` 归一成同一种写法。"""
    m = re.search(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)"
                  r"(?:[,/\s]+([\d.]+))?\s*\)", v or "")
    if not m:
        return (v or "").strip()
    r, g, b = (float(m.group(i)) for i in (1, 2, 3))
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return f"rgba({r:g}, {g:g}, {b:g}, {a:g})"


def norm_shadow(v: str) -> str:
    """把 box-shadow 归一（去多余空白 + 归一颜色写法），便于比对。"""
    s = re.sub(r"\s+", " ", (v or "").strip())
    s = re.sub(r"rgba?\([^)]*\)", lambda m: norm_color(m.group(0)), s)
    return s


# ---------------------------------------------------------------------------
# 源码层：焦点环取值集合
# ---------------------------------------------------------------------------
# 🚨 环宽：负向类里**必须有 `-`**，否则 `focus:ring-brand-500/30` 会被读成 `ring`(3px)。
#    见 docstring「陷阱 1」——实测会造出 40 处虚假的「环宽不统一」。
RING_W_RE = re.compile(
    r"(?:focus|focus-visible|focus-within):(ring(?:-\d+(?:\.\d+)?|\[[^\]]+\])?)"
    r"(?![\w\[-])")
# 🚨 环色：必须排掉 `inset` / `offset-*`（修饰符与偏移工具类，不是颜色）。见「陷阱 2」。
RING_C_RE = re.compile(
    r"(?:focus|focus-visible|focus-within):"
    r"(ring-(?!inset(?![\w-])|offset-)[A-Za-z][\w-]*(?:/\d+)?)")
# 与 `focus*:ring` **同行**但**不带前缀**的裸环色 ⇒ 只报（可能是被漏判的焦点环，
# 也可能是同元素上的选中态，判不了 ⇒ 不进判据，只让覆盖率缺口可见）。
BARE_C_RE = re.compile(r"(?<![\w:-])(ring-(?!inset(?![\w-])|offset-)[A-Za-z][\w-]*(?:/\d+)?)")


def source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for pat in ("apps/*/app/**/*.tsx", "apps/*/app/**/*.ts", "apps/*/components/**/*.tsx",
                "packages/*/src/**/*.tsx", "packages/*/src/**/*.ts",
                "packages/*/src/**/*.css"):
        for p in FRONTEND.glob(pat):
            if ".next" in p.parts or "node_modules" in p.parts:
                continue
            files.append(p)
    return sorted(set(files))


def scan_rings() -> tuple[dict[str, list[str]], dict[str, list[str]], int, list[str]]:
    """扫出全站焦点环的**环宽**与**环色**取值
    → `(宽度→出处, 颜色→出处, shadow-focus 命中数, 同行裸环色)`。

    ⚠️ 这里用**裸文本正则**而不是 className 块解析：`ring-2`（宽度组）与
    `ring-brand-500/30`（颜色组）在 twMerge 里是**不同组**，不会被互吞；
    本判据只看「出现过哪些值」，不需要 `cn()` 的合并语义。
    """
    widths: dict[str, list[str]] = {}
    colors: dict[str, list[str]] = {}
    bare: list[str] = []
    focus_shadow = 0
    for p in source_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        for i, line in enumerate(text.splitlines(), 1):
            for m in RING_W_RE.finditer(line):
                widths.setdefault(m.group(1), []).append(f"{rel}:{i}")
            for m in RING_C_RE.finditer(line):
                colors.setdefault(m.group(1), []).append(f"{rel}:{i}")
            if re.search(r"(?:focus|focus-visible|focus-within):ring", line):
                for m in BARE_C_RE.finditer(line):
                    bare.append(f"{m.group(1)} @ {rel}:{i}")
            if re.search(r"(?:^|[\s\"'`])shadow-focus(?:[\s\"'`]|$)", line):
                focus_shadow += 1
    return widths, colors, focus_shadow, bare


def ring_width_px(tok: str) -> float | None:
    if tok in RING_WIDTHS:
        return RING_WIDTHS[tok]
    m = re.match(r"ring-\[([\d.]+)px\]", tok)
    return float(m.group(1)) if m else None


def ring_color_norm(tok: str) -> str:
    """`ring-brand-500/30` → `brand-500@30%`（规范值 = `brand-500@22%`）。"""
    body = tok[len("ring-"):]
    m = re.match(r"([\w-]+?)(?:/(\d+))?$", body)
    if not m:
        return body
    name, op = m.group(1), m.group(2)
    return f"{name}@{op}%" if op else name


# ---------------------------------------------------------------------------
# 判据（静态）
# ---------------------------------------------------------------------------
def judge_tokens(shadows: dict[str, str], light: dict[str, str],
                 dark: dict[str, str]) -> tuple[list[str], list[str]]:
    """F1 / F2。返回 `(problems, notes)`。"""
    problems: list[str] = []
    notes: list[str] = []

    got = shadows.get("focus")
    if got is None:
        problems.append("[F1] preset 的 `boxShadow` 里**没有** `focus` 键 ⇒ 规范焦点环没有令牌")
    else:
        got_r = resolve_vars(got, light, dark)
        if norm_shadow(got_r) != norm_shadow(SPEC_RING):
            problems.append(f"[F1] `boxShadow.focus` 展开令牌后 = `{got_r}`"
                            f"（原文 `{got}`），规范是 `{SPEC_RING}`")
        elif got_r != got:
            notes.append(f"[note] `boxShadow.focus` 用令牌间接表达（`{got}`），"
                         f"展开后 = `{got_r}` ⇒ 与规范**语义相等**（已按展开值比对）")
        if VAR_RE.search(got_r):
            problems.append(f"[F1] `boxShadow.focus` 里的 `var()` 展开不了：`{got_r}`"
                            " ⇒ 令牌缺失，规范焦点环实际取不到值")

    fr = light.get("focus-ring")
    if fr is None:
        problems.append("[F2] `tokens.css` 浅色档没有 `--focus-ring`")
    elif norm_color(fr) != norm_color(SPEC_RING_RGB and
                                      f"rgba({SPEC_RING_RGB[0]:g}, {SPEC_RING_RGB[1]:g}, "
                                      f"{SPEC_RING_RGB[2]:g}, {SPEC_RING_ALPHA:g})"):
        problems.append(f"[F2] `--focus-ring` = `{fr}`，规范是 `rgba(54, 96, 176, 0.22)`")

    b5 = light.get("brand-500")
    if b5 is None:
        problems.append("[F2] `tokens.css` 浅色档没有 `--brand-500`")
    elif b5.strip() != BRAND_500:
        problems.append(f"[F2] `--brand-500` = `{b5}`，规范值 `rgba(54,96,176,.22)` 要求它 == `{BRAND_500}`")

    if "focus-ring" in dark:
        notes.append(f"[note] 深色档 `--focus-ring` = `{dark['focus-ring']}`"
                     f"（规范只声明了浅色档 ⇒ 深色档属**规范缺口**，本判据不判）")
    return problems, notes


def judge_uniform(widths: dict[str, list[str]], colors: dict[str, list[str]],
                  focus_shadow: int, bare: list[str] | None = None
                  ) -> tuple[list[str], list[str], int]:
    """F3 / F4 / F5。返回 `(problems, notes, 覆盖率)`。"""
    problems: list[str] = []
    notes: list[str] = []
    cov = 0

    # ---- F3 环宽统一 ----
    width_px = {}
    for tok, where in widths.items():
        px = ring_width_px(tok)
        width_px[tok] = px
        cov += len(where)
    known = {t: p for t, p in width_px.items() if p is not None}
    if len(known) > 1:
        detail = "、".join(f"`{t}`({p:g}px ×{len(widths[t])})"
                          for t, p in sorted(known.items(), key=lambda x: x[1]))
        problems.append(f"[F3] 焦点环**环宽不统一**：{detail} —— 规范要求全站一种（`3px`）")
    elif len(known) == 1:
        tok, px = next(iter(known.items()))
        if abs(px - SPEC_RING_W) > 1e-9:
            problems.append(f"[F3] 焦点环环宽 `{tok}` = **{px:g}px**，规范是 **{SPEC_RING_W:g}px**"
                            f"（{len(widths[tok])} 处）")
    else:
        notes.append("[F3] 源码里一个焦点环宽度工具类都没扫到 ⇒ 覆盖率 0")
    unknown = [t for t, p in width_px.items() if p is None]
    if unknown:
        notes.append("[F3] 认不出宽度的环宽类（只报）：" + "、".join(f"`{t}`" for t in unknown))

    # ---- F4 环色统一（危险态只报）----
    danger = {t: w for t, w in colors.items() if "danger" in t}
    normal = {t: w for t, w in colors.items() if "danger" not in t}
    cov += sum(len(v) for v in colors.values())
    norm_set = {ring_color_norm(t) for t in normal}
    if len(norm_set) > 1:
        detail = "、".join(f"`{ring_color_norm(t)}`(×{len(normal[t])})"
                          for t in sorted(normal, key=lambda x: -len(normal[x])))
        problems.append(f"[F4] 焦点环**环色不统一**：{detail} —— 规范要求全站一种"
                        f"（`{ring_color_norm('ring-brand-500/22')}`）")
    elif len(norm_set) == 1:
        only = next(iter(norm_set))
        want = ring_color_norm("ring-brand-500/22")
        if only != want:
            problems.append(f"[F4] 焦点环环色 = `{only}`，规范是 `{want}`"
                            f"（`0 0 0 3px rgba(54,96,176,.22)` = `--brand-500` @ 22%）")
    if danger:
        notes.append("[note] 危险态焦点环（**只报不判**，规范未声明例外 ⇒ 规范缺口）："
                     + "、".join(f"`{t}`(×{len(w)})" for t, w in danger.items()))
    if bare:
        notes.append(f"[note] 与 `focus*:ring` **同行但不带前缀**的裸环色 {len(bare)} 处"
                     "（只报：可能是被漏判的焦点环，也可能是同元素上的选中/激活态）："
                     + "、".join(f"`{b}`" for b in bare[:6])
                     + ("…" if len(bare) > 6 else ""))

    # ---- F5 规范令牌有调用点 ----
    if focus_shadow == 0:
        problems.append("[F5] 规范焦点环令牌 `shadow-focus` **调用点 0 处**"
                        " —— preset 里定义了 `0 0 0 3px var(--focus-ring)`，却没人用它")
    return problems, notes, cov


# ---------------------------------------------------------------------------
# 渲染层：强制 `:focus-visible` 后读计算样式
# ---------------------------------------------------------------------------
FOCUSISH_RE = re.compile(r"focus|ring")


STYLE_KEYS = ("outline", "outline-style", "outline-width", "outline-color",
              "box-shadow")


def _slim(p: dict) -> dict:
    return {k: p.get(k, "") for k in STYLE_KEYS}


async def _read_with(b: Browser, nid: int, pseudo: str | None) -> dict:
    """把伪类逼出来（或清掉）之后读 5 个样式属性。"""
    await b.cdp.send("CSS.forcePseudoState", nodeId=nid,
                     forcedPseudoClasses=[pseudo] if pseudo else [])
    try:
        cs = await b.cdp.send("CSS.getComputedStyleForNode", nodeId=nid)
        return _slim({p["name"]: p["value"] for p in cs.get("computedStyle", [])})
    finally:
        if pseudo:
            try:
                await b.cdp.send("CSS.forcePseudoState", nodeId=nid,
                                 forcedPseudoClasses=[])
            except Exception:                               # noqa: BLE001
                pass


def parent_map(root: dict) -> dict[int, int]:
    """从 `DOM.getDocument(depth=-1)` 的根节点建 `nodeId → parentId`。

    🚨 这里有**两个**都踩过的坑，换写法前先看：

    1. **`DOM.describeNode().node.parentId` 恒为 `None`**（即使节点确有父节点）
       ⇒ 祖先回溯第一级就 `break`，「查祖先」变成**静默失效**，
       把有委托焦点环的元素误报成缺陷（实测：im 的输入框靠父级
       `focus-within:ring-2`，被误报成「无焦点指示」）。
    2. **`DOM.getFlattenedDocument` 的 nodeId 与 `getDocument`/`querySelectorAll`
       不是同一套 id 空间** —— 实测同一个 `<textarea>` 在父表里**查不到**
       （`nid in pm == False`）⇒ 父表白建。
       更阴的是它还会让**先前** `querySelectorAll` 拿到的 id 失效
       （`CSS.forcePseudoState → Could not find node with given id`），
       于是 9/9 个元素全部测量失败、被静默 `continue` 吞掉。

    ⇒ 只能用 `getDocument(depth=-1)` 返回的树自己遍历，且**与 `querySelectorAll`
    共用同一个 root**。
    """
    out: dict[int, int] = {}
    stack = [root]
    while stack:
        n = stack.pop()
        nid = n.get("nodeId")
        for ch in n.get("children") or []:
            cid = ch.get("nodeId")
            if nid is not None and cid is not None:
                out[cid] = nid
            stack.append(ch)
    return out


async def _ancestor_indicator(b: Browser, nid: int, parents: dict[int, int],
                              levels: int = 4) -> str:
    """父链上 `focus-within` 是否带来**可见新增** ⇒ 返回「委托给谁」，否则空串。

    为什么需要它：`focus-within:ring-2`（本仓库有）这类**委托式**焦点指示，
    子元素自己一点变化都没有 —— 只看子元素会把正确的实现报成缺陷（假红）。
    只为**自检不过**的元素走这一趟，所以代价与缺陷数成正比。
    """
    cur = nid
    for _ in range(levels):
        p = parents.get(cur)
        if p is None:
            return ""
        cur = p
        base = await _read_with(b, cur, None)
        fw = await _read_with(b, cur, "focus-within")
        if visible_change(base, fw):
            dd = await b.cdp.send("DOM.describeNode", nodeId=cur)
            nn = dd.get("node") or {}
            flat = nn.get("attributes") or []
            attrs = {flat[i]: flat[i + 1] for i in range(0, len(flat) - 1, 2)}
            return (f"祖先 <{(nn.get('nodeName') or '').lower()}>"
                    f".focus-within 有变化 class=`{attrs.get('class', '')[:60]}`")
    return ""


async def probe_focus(b: Browser, limit: int = FOCUS_SAMPLE) -> dict:
    """用 CDP 的 `CSS.forcePseudoState` 把 `:focus-visible` 逼出来，再读计算样式。

    为什么不 `el.focus()`：`:focus-visible` 是否匹配取决于**焦点来源**
    （Chrome 对「脚本调用 `.focus()`」通常**不**判为键盘来源）⇒ 逼不出来就测不到。
    为什么不 `Input.dispatchKeyEvent` 模拟 Tab：只覆盖「第一个」可聚焦元素，
    且顺序依赖 DOM，拿不到全貌。

    **判据用「变化量」而不是「焦点态的绝对值」**：每个元素都读**未聚焦态**与
    **强制 `:focus-visible` 态**两份样式，焦点指示 = 两者**有差异**。
    🚨 实测踩到：初版只看焦点态「有没有 box-shadow」⇒ 一个只有**装饰性基础阴影**
    （`rgba(16,24,40,.1)` 卡片阴影）的元素被判成「有焦点指示」⇒ **假绿 15 处**。
    基础阴影在未聚焦时也在，它不是焦点指示。

    **采样策略（两个用途分开取）**：
    - F7（每个可见可聚焦元素都要有指示）用「前 `limit` 个**可见**元素」——无偏；
    - F6（规范焦点环是否真的出现）额外**瞄准** class 里含 `focus`/`ring` 的元素 ——
      否则一个 200 个可聚焦元素的页面上，抽样很可能一个焦点环都碰不到，
      会把「没抽到」误报成「产品没做」（**覆盖率不足伪装成产品缺陷**）。
    两类取并集，去重后逐个逼出伪类。
    """
    await b.cdp.send("DOM.enable")
    await b.cdp.send("CSS.enable")
    # 一次 `getDocument(depth=-1)`：既拿 root，也建父链 —— **必须与 `querySelectorAll`
    # 共用同一个 root**，否则 id 空间不一致（见 `parent_map` 的坑 2）。
    doc = await b.cdp.send("DOM.getDocument", depth=-1, pierce=False)
    root = doc["root"]["nodeId"]
    parents = parent_map(doc["root"])
    anc_ok = bool(parents)
    q = await b.cdp.send("DOM.querySelectorAll", nodeId=root, selector=FOCUSABLE)
    ids = list(q.get("nodeIds") or [])
    info = await b.cdp.evaluate(JS_FOCUSABLE_INFO % json.dumps(FOCUSABLE)) or []
    aligned = len(info) == len(ids)
    if not aligned:
        info = [{} for _ in ids]

    picked: list[int] = []
    seen: set[int] = set()

    def take(nid: int) -> None:
        if nid not in seen:
            seen.add(nid)
            picked.append(nid)

    vis = [ids[i] for i in range(len(ids)) if (info[i] or {}).get("visible")]
    for nid in vis[:limit]:
        take(nid)
    n_focusish = 0
    for i, nid in enumerate(ids):
        if FOCUSISH_RE.search((info[i] or {}).get("cls") or ""):
            n_focusish += 1
            take(nid)

    pos = {nid: i for i, nid in enumerate(ids)}
    out = []
    errs: list[str] = []
    for nid in picked:
        try:
            base = await _read_with(b, nid, None)
            foc = await _read_with(b, nid, "focus-visible")
            at = await b.cdp.send("DOM.describeNode", nodeId=nid)
            node = at.get("node", {})
            flat = node.get("attributes") or []
            attrs = {flat[i]: flat[i + 1] for i in range(0, len(flat) - 1, 2)}
            i = pos.get(nid)
            visible = bool(info[i].get("visible")) if (
                aligned and i is not None and i < len(info)) else True
            rec = {
                "tag": (node.get("nodeName") or "").lower(),
                "cls": attrs.get("class", ""),
                "visible": visible,
                "base": base, "focus": foc,
            }
            if visible and is_no_indicator(rec):
                rec["ancestor"] = (await _ancestor_indicator(b, nid, parents)
                                   if anc_ok else "")
                rec["anc_checked"] = anc_ok
            out.append(rec)
        except Exception as e:                              # noqa: BLE001
            # 🚨 **不要静默 `continue`**：实测这个 `except` 曾把「9 个元素一个都没测成」
            # 伪装成「采样 0」⇒ 缺陷被报成「覆盖率不足」，真实原因（异常）完全不可见。
            errs.append(f"{type(e).__name__}: {e}")
            continue
    if errs:
        print(f"[env] {len(errs)}/{len(picked)} 个可聚焦元素**测量失败**，"
              f"首个原因：{errs[0]}")
    return {"nodes": out, "focusable": len(ids), "sampled": len(picked),
            "visible_focusable": len(vis), "focusish": n_focusish,
            "aligned": aligned, "anc_ok": anc_ok, "errors": errs,
            "vw": await b.cdp.evaluate("innerWidth")}


def outline_invisible(st: dict) -> bool:
    """某个状态（`_slim` 出来的字典）里 `outline` 是否**看不见**。

    🚨 `outline-style: auto` **算看得见**（浏览器默认焦点环），不是「没有」。
    实测踩到：初版把「`outline` shorthand 为空」也当作「无 outline」，
    于是夹具里 `outline-style: auto` + `outline=""` 被判成「无指示」⇒ V5 假红。
    另两种**真的看不见**的情况也要判：`outline-width: 0px`、`outline-color` 全透明。
    """
    style = (st.get("outline-style") or "").strip().lower()
    if not style:
        # CDP 没给 `outline-style` ⇒ 退回 shorthand（空串 = 没有）
        return (st.get("outline") or "").strip().lower() in ("", "none")
    if style == "auto":
        return False
    if style in ("none", "initial"):
        return True
    if (st.get("outline-width") or "").strip().lower() in ("", "0", "0px"):
        return True
    c = (st.get("outline-color") or "").strip().lower()
    return bool(re.match(r"rgba\([^)]*,\s*0\s*\)$", c))


def outline_visible(st: dict) -> bool:
    return not outline_invisible(st)


def visible_outline(st: dict) -> str | None:
    """可见 outline 的签名；看不见时返回 `None`。"""
    if outline_invisible(st):
        return None
    return (f"{(st.get('outline-style') or '').strip().lower()} "
            f"{(st.get('outline-width') or '').strip().lower()} "
            f"{(st.get('outline-color') or '').strip().lower()}")


SHADOW_PART_RE = re.compile(r",(?![^()]*\))")


def visible_shadow_parts(st: dict) -> frozenset:
    """`box-shadow` 里**真的看得见**的那些段（全透明段剔除）。

    为什么要逐段看：Tailwind 的 ring 用 `var(--tw-ring-offset-shadow),
    var(--tw-ring-shadow), var(--tw-shadow)` 三段拼出来，未聚焦时前两段是
    `rgba(0, 0, 0, 0) 0px 0px 0px 0px`（全透明占位）——
    「字符串不等于 `none`」并不代表看得见。
    """
    s = (st.get("box-shadow") or "").strip().lower()
    if s in ("none", "", "initial"):
        return frozenset()
    out = set()
    for part in SHADOW_PART_RE.split(s):
        part = re.sub(r"\s+", " ", part.strip())
        if not part:
            continue
        m = re.search(r"rgba?\([^)]*\)", part)
        if m and (re.search(r",\s*0(?:\.0+)?\s*\)$", m.group(0))
                  or re.search(r"/\s*0(?:\.0+)?%?\s*\)$", m.group(0))):
            continue                    # 这一段全透明
        out.add(part)
    return frozenset(out)


def shadow_visible(st: dict) -> bool:
    return bool(visible_shadow_parts(st))


def style_diff(a: dict, b: dict) -> str:
    """两个状态之间**变了什么**（空串 = 完全没变）。**只用于报错信息**。"""
    parts: list[str] = []
    for k in STYLE_KEYS:
        x, y = (a.get(k) or ""), (b.get(k) or "")
        if x != y:
            parts.append(f"{k} {x or '—'}→{y or '—'}")
    return "; ".join(parts)


def visible_change(base: dict, foc: dict) -> str:
    """焦点指示 = 焦点态**新增**了未聚焦态没有的**可见**指示（空串 = 没有）。

    🚨 判据必须是「**新增的可见量**」，三个更弱的写法都被实测证伪过：

    | 更弱的写法 | 实测后果 |
    |---|---|
    | 焦点态 `box-shadow != none` 就算有 | 装饰性基础阴影被当成焦点环 ⇒ **15 处假绿** |
    | 只要两个状态的**字符串**不同就算有 | Tailwind 的 ring 占位槽让字符串必然不同（`rgba(0,0,0,0) 0 0 0 0` 多出来）⇒ **同样 15 处假绿** |
    | 「有差异」且「焦点态整体可见」 | **基础阴影被移除**也算「有差异」，焦点态剩下的东西不可见 ⇒ **假绿** |

    所以：把两边的**可见阴影段集合**与**可见 outline 签名**都算出来，
    断言焦点态这一侧出现了 base 侧没有的东西。
    """
    gains: list[str] = []
    for x in sorted(visible_shadow_parts(foc) - visible_shadow_parts(base)):
        gains.append(f"+box-shadow({x})")
    ob, of = visible_outline(base), visible_outline(foc)
    if of is not None and of != ob:
        gains.append(f"outline({ob or '不可见'} → {of})")
    return "；".join(gains)


def focus_indicator(n: dict) -> str:
    """**自身**的焦点指示（空串 = 没有）。"""
    return visible_change(n.get("base") or {}, n.get("focus") or {})


def is_no_indicator(n: dict) -> bool:
    """**自身**在 `:focus-visible` 下没有任何可见焦点指示。"""
    return not focus_indicator(n)


def judge_focus(m: dict | None) -> tuple[list[str], list[str], list[dict], int]:
    """F6 / F7 / F8。返回 `(problems, notes, 分布, 覆盖率)`。"""
    if not m:
        return ["[F6] 未取到焦点态测量结果"], [], [], 0
    nodes = m.get("nodes") or []
    problems: list[str] = []
    notes: list[str] = []
    dist: dict[str, int] = {}
    for n in nodes:
        f = n.get("focus") or {}
        # ⚠️ outline 放在**前面**：box-shadow 字符串很长，打印时会被截断，
        # 把 outline 部分切掉会让人误判（实测：我自己因此把「有 UA 焦点环」的
        # 15 个元素看成「没有 outline」）。
        key = ""
        st = (f.get("outline-style") or "").lower()
        if st and st != "none":
            key = f"outline({st} {f.get('outline-width')}) + "
        key += norm_shadow(f.get("box-shadow") or "none")
        if not n.get("visible", True):
            key = f"[不可见] {key}"
        dist[key] = dist.get(key, 0) + 1

    # ---- F6：规范焦点环在**可见**元素上出现过吗 ----
    spec_hits = sum(v for k, v in dist.items()
                    if norm_shadow(SPEC_RING) in k and not k.startswith("[不可见]"))
    if spec_hits == 0:
        problems.append(f"[F6] **规范焦点环 `{SPEC_RING}` 在渲染层一次都没出现**"
                        f"（采样 {len(nodes)} 个可聚焦元素，其中可见 {m.get('visible_focusable')} 个）"
                        f" ⇒ 规范声明的焦点环没有被任何元素使用")
    # ---- F7：每个**自身可见**的可聚焦元素都要有可见指示（或委托给祖先）----
    no_ind = [n for n in nodes
              if n.get("visible", True) and is_no_indicator(n) and not n.get("ancestor")]
    caveat = ("" if m.get("anc_ok", True) else
              "（⚠️ 祖先 `focus-within` **未能检查**（父链取不到）⇒ 可能其实是委托模式，须人工确认）")
    if no_ind and not m.get("anc_ok", True):
        notes.append("[note] F7 的结论**未被祖先检查限定**：父链取不到 ⇒ "
                     "「无焦点指示」有可能是委托给祖先的假红，须人工确认")
    for n in no_ind[:10]:
        f = n.get("focus") or {}
        raw = style_diff(n.get("base") or {}, f) or "（字符串完全没变）"
        problems.append(f"[F7] <{n['tag']}> 在 `:focus-visible` 下**没有任何可见焦点指示**"
                        f"（焦点态相对未聚焦态**没有新增可见量**；原始差异：{raw}）"
                        f" class=`{n['cls'][:60]}`{caveat}")
    if len(no_ind) > 10:
        problems.append(f"[F7] …另有 {len(no_ind) - 10} 个同类（共 {len(no_ind)}）")
    deleg = [n for n in nodes if n.get("ancestor")]
    if deleg:
        notes.append(f"[note] {len(deleg)} 个元素**自身**无变化、但祖先 `focus-within` 有变化"
                     "（委托式焦点指示，不判）："
                     + "、".join(f"<{n['tag']}>→{n['ancestor'][:40]}" for n in deleg[:4]))
    hidden = [n for n in nodes if not n.get("visible", True)]
    if hidden:
        notes.append(f"[note] 采样里 {len(hidden)} 个可聚焦元素**自身不可见**"
                     "（`sr-only`/`peer` 这类委托模式）⇒ F7 不对它们判，"
                     "其焦点指示由可见的兄弟/祖先负责")
    if not m.get("aligned"):
        notes.append("[note] 可聚焦元素清单与 DOM 顺序**未对齐** ⇒ 可见性回填不可靠，F7 已按「可见」保守处理")
    if nodes and not dist:
        notes.append("[F8] 分布为空 ⇒ 采样没取到东西")
    return problems, notes, [{"k": k, "n": v} for k, v in
                             sorted(dist.items(), key=lambda x: -x[1])], len(nodes)


# ---------------------------------------------------------------------------
# 收口
# ---------------------------------------------------------------------------
def summary_exit(total: int, problems: list[str], env_broken: list[str]) -> int:
    """`0` 通过 · `1` 产品缺陷 · `2` 环境问题。覆盖率 0 / 前提破坏 ⇒ **2**。"""
    if env_broken:
        return 2
    if total == 0:
        return 2
    if problems:
        return 1
    return 0


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


FIRST_CASE_JS = r"""(() => {
  const a = document.querySelector('a[href^="/cases/"]');
  if (a) return { href: a.getAttribute('href') };
  const tr = document.querySelector('tbody tr') || document.querySelector('[role="row"]');
  if (tr) { tr.click(); return { clicked: true }; }
  return {};
})()"""


async def resolve_path(b: Browser, base: str, path: str) -> str:
    """`@first-case` → 真实 id（照搬 `verify_runtime_health.py` + 一条点击回退）。"""
    if "@first-case" not in path:
        return path
    await b.goto(f"{base}/cases", wait=1.6)
    await b.settle(extra=1.0)
    r = await b.cdp.evaluate(FIRST_CASE_JS) or {}
    href = r.get("href")
    if not href and r.get("clicked"):
        # `router.push` 不产生导航请求 ⇒ 不能用 `settle()`，必须显式轮询
        for _ in range(12):
            await asyncio.sleep(0.5)
            href = await b.cdp.evaluate("location.pathname")
            if href and href.startswith("/cases/") and href != "/cases":
                break
    if not href or not href.startswith("/cases/") or href == "/cases":
        raise RuntimeError(f"{base}/cases 上取不到案件详情路径（动态路由测不到）")
    return path.replace("@first-case", href.rsplit("/", 1)[-1])


def run_static(verbose: bool = True) -> tuple[list[str], list[str], int]:
    shadows = parse_preset_shadow()
    light, dark = parse_tokens()
    widths, colors, focus_shadow, bare = scan_rings()

    problems: list[str] = []
    notes: list[str] = []
    p1, n1 = judge_tokens(shadows, light, dark)
    problems += p1
    notes += n1
    p2, n2, cov = judge_uniform(widths, colors, focus_shadow, bare)
    problems += p2
    notes += n2

    if verbose:
        print("── 令牌层 ──")
        print(f"   boxShadow.focus     = `{shadows.get('focus')}`")
        print(f"   --focus-ring(light) = `{light.get('focus-ring')}`")
        print(f"   --focus-ring(dark)  = `{dark.get('focus-ring')}`")
        print(f"   --brand-500(light)  = `{light.get('brand-500')}`")
        print(f"   规范焦点环          = `{SPEC_RING}`")
        print("── 源码层：焦点环取值集合 ──")
        print(f"   环宽（{len(widths)} 种）：")
        for t, w in sorted(widths.items(), key=lambda x: -len(x[1])):
            print(f"     {t:22s} → {ring_width_px(t)} px   ×{len(w):3d}   例：{w[0]}")
        print(f"   环色（{len(colors)} 种）：")
        for t, w in sorted(colors.items(), key=lambda x: -len(x[1])):
            print(f"     {t:28s} → {ring_color_norm(t):20s} ×{len(w):3d}   例：{w[0]}")
        print(f"   `shadow-focus` 命中：{focus_shadow}")
        if bare:
            print(f"   与 `focus*:ring` 同行但**不带前缀**的裸环色（只报）：{len(bare)} 处")
            for x in bare[:8]:
                print(f"     {x}")
    return problems, notes, cov


async def run_render(only: str | None, verbose: bool = True
                     ) -> tuple[list[str], list[str], list[dict], int, list[str]]:
    problems: list[str] = []
    notes: list[str] = []
    dist: dict[str, int] = {}
    cov = 0
    env_break: list[str] = []
    for app, cfg in APPS.items():
        if only and app != only:
            continue
        base = f"http://localhost:{cfg['port']}"
        async with Browser(headless=True, width=1280, height=900) as b:
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:                               # noqa: BLE001
                pass
            if not await login(b, base, cfg["user"]):
                env_break.append(f"{app} 登录失败 ⇒ 整端未测")
                print(f"[env] {app} 登录失败 ⇒ 整端跳过")
                continue
            for raw in cfg["pages"]:
                try:
                    path = await resolve_path(b, base, raw)
                except Exception as e:                      # noqa: BLE001
                    print(f"[env] {app}{raw} 解析失败：{e}")
                    continue
                await b.goto(f"{base}{path}", wait=1.8)
                await b.settle(extra=1.4)
                try:
                    m = await probe_focus(b)
                except Exception as e:                      # noqa: BLE001
                    notes.append(f"[env] {app}{path} 焦点态探测失败（CSS 域不可用？）：{e}")
                    continue
                p, n, d, c = judge_focus(m)
                problems += [f"{app}{path} {x}" for x in p]
                notes += n
                cov += c
                for it in d:
                    dist[it["k"]] = dist.get(it["k"], 0) + it["n"]
                if verbose:
                    print(f"── {app}{path}  可聚焦 {m.get('focusable')} 个"
                          f"（可见 {m.get('visible_focusable')}，带 focus/ring 类 {m.get('focusish')}，"
                          f"实测 {m.get('sampled')}），焦点环取值 {len(d)} 种")
    return problems, notes, [{"k": k, "n": v} for k, v in
                             sorted(dist.items(), key=lambda x: -x[1])], cov, env_break


async def dump(only: str | None) -> int:
    """诊断模式：打原始数值，**不下判断**。"""
    run_static()
    if only == "__static__":
        return 0
    print("\n── 渲染层：`:focus-visible` 强制后的取值分布 ──")
    _, _, dist, cov, _ = await run_render(only, verbose=True)
    print(f"\n   合计采样 {cov} 个可聚焦元素，取值 {len(dist)} 种：")
    for it in dist:
        mark = "  ← 规范值" if norm_shadow(SPEC_RING) in it["k"] else ""
        print(f"     ×{it['n']:4d}  `{it['k'][:90]}`{mark}")
    return 0


async def why(app: str, path: str | None, needle: str) -> int:
    """**取证模式**：把含 `needle` 的可聚焦元素的焦点态打出来，**不下判断**。"""
    if app not in APPS:
        print(f"[env] 未知端 `{app}`（可选：{'/'.join(APPS)}）")
        return 2
    cfg = APPS[app]
    base = f"http://localhost:{cfg['port']}"
    path = path or cfg["pages"][0]
    async with Browser(headless=True, width=1280, height=900) as b:
        try:
            await b.cdp.send("Network.clearBrowserCookies")
        except Exception:                                   # noqa: BLE001
            pass
        if not await login(b, base, cfg["user"]):
            print("[env] 登录失败")
            return 2
        await b.goto(f"{base}{path}", wait=1.8)
        await b.settle(extra=1.4)
        try:
            m = await probe_focus(b, limit=400)
        except Exception as e:                              # noqa: BLE001
            print(f"[env] 焦点态探测失败：{e}")
            return 2
    hits = [n for n in m["nodes"] if needle.lower() in (n.get("cls") or "").lower()
            or needle.lower() in n["tag"]]
    print(f"── {app}{path}  可聚焦 {m['focusable']} 个，命中 `{needle}` {len(hits)} 个")
    for n in hits[:12]:
        b0, f0 = n.get("base") or {}, n.get("focus") or {}
        print(f"   <{n['tag']}> 可见={n.get('visible')} class=`{n['cls'][:90]}`")
        print(f"      未聚焦 outline = `{b0.get('outline')}`   box-shadow = `{b0.get('box-shadow')}`")
        print(f"      聚焦后 outline = `{f0.get('outline')}`   box-shadow = `{f0.get('box-shadow')}`")
        d = focus_indicator(n)
        spec = norm_shadow(SPEC_RING) in norm_shadow(f0.get("box-shadow") or "")
        print(f"      → 焦点指示：{d or '**无**'}；是否规范焦点环：{'是' if spec else '**否**'}")
        if n.get("ancestor"):
            print(f"      → 祖先委托：{n['ancestor']}")
    if not hits:
        print("   （无命中 ⇒ 换一个 `needle`，或该页没有这类元素）")
    return 0


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
FIXTURE_PRESET = """
boxShadow: {
  s1: "var(--s1)",
  s2: "var(--s2)",
  s3: "var(--s3)",
  focus: "0 0 0 3px var(--focus-ring)",
  "brand-sm": "0 1px 2px 0 rgba(39, 76, 147, 0.06)",
},
"""

FIXTURE_TOKENS = """
:root {
  --brand-500: 54 96 176; /* #3660B0 焦点环、进度条、图表主序列 */
  --focus-ring: rgba(54, 96, 176, 0.22);
  --r1: 4px;
}
/* 深色档：注释里也写了一个 `.dark .xxx { !important }` —— 照抄 tokens.css:12 的写法，
   用来证明解析器**不会**被注释里的块劫持。 */
.dark {
  --focus-ring: rgba(110, 148, 214, 0.3);
}
"""

# 🚨 夹具必须照抄**真实源码的形式**：本仓库焦点环色 100% 带 `focus*:` 前缀。
# 初版夹具写成 `focus:ring-2 ring-brand-500/30`（色不带前缀）⇒ `colors` 恒空
# ⇒ 掩盖了 `judge_uniform` 里的 `TypeError` 崩溃。见 docstring「陷阱 3」。
FIXTURE_SRC_UNIFORM = (
    'className="focus:ring-[3px] focus:ring-brand-500/22 shadow-focus"\n')
FIXTURE_SRC_SPLIT = (
    'className="focus:ring-2 focus:ring-brand-500/30"\n'
    'className="focus-visible:ring-2 focus-visible:ring-brand-500/40"\n'
    'className="focus-within:ring-2 focus-within:ring-brand-500/25"\n')


def _scan_text(text: str) -> tuple[dict[str, list[str]], dict[str, list[str]],
                                   int, list[str]]:
    widths: dict[str, list[str]] = {}
    colors: dict[str, list[str]] = {}
    bare: list[str] = []
    n = 0
    for i, line in enumerate(text.splitlines(), 1):
        for m in RING_W_RE.finditer(line):
            widths.setdefault(m.group(1), []).append(f"F:{i}")
        for m in RING_C_RE.finditer(line):
            colors.setdefault(m.group(1), []).append(f"F:{i}")
        if re.search(r"(?:focus|focus-visible|focus-within):ring", line):
            for m in BARE_C_RE.finditer(line):
                bare.append(f"{m.group(1)} @ F:{i}")
        if re.search(r"(?:^|[\s\"'`])shadow-focus(?:[\s\"'`]|$)", line):
            n += 1
    return widths, colors, n, bare


def self_test() -> int:
    """每条判据都要有**会红**的臂 + **对照（应绿）**的臂。"""
    fails: list[str] = []
    n = 0

    def arm(name: str, cond: bool, detail: str = "") -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{name} {detail}")

    # ---- 解析器 ----
    sh = parse_preset_shadow(FIXTURE_PRESET)
    arm("P1 parse_preset_shadow 解出 focus",
        sh.get("focus") == "0 0 0 3px var(--focus-ring)", f"→ {sh}")
    arm("P2 parse_preset_shadow 解出 s1/s2/s3",
        {"s1", "s2", "s3"} <= set(sh), f"→ {set(sh)}")
    lt, dk = parse_tokens(FIXTURE_TOKENS)
    arm("P3 parse_tokens 浅色档 --focus-ring",
        lt.get("focus-ring") == "rgba(54, 96, 176, 0.22)", f"→ {lt.get('focus-ring')}")
    arm("P4 parse_tokens 深色档独立解析（不被浅色覆盖）",
        dk.get("focus-ring") == "rgba(110, 148, 214, 0.3)", f"→ {dk.get('focus-ring')}")
    arm("P5 注释写在分号之后也能取到值",
        lt.get("brand-500") == "54 96 176", f"→ {lt.get('brand-500')}")
    arm("P6 注释里的 `.dark .xxx { !important }` **不得**劫持 `.dark` 块",
        dk.get("focus-ring") == "rgba(110, 148, 214, 0.3)", f"→ {dk}")
    arm("P7 选择器后面不跟 `{` 的命中不算块（对照）",
        _block_vars(FIXTURE_TOKENS, ".darkish") == {}, "→ 不应命中")
    arm("P8 `resolve_vars` 展开令牌",
        resolve_vars("0 0 0 3px var(--focus-ring)", lt, dk) ==
        "0 0 0 3px rgba(54, 96, 176, 0.22)",
        f"→ {resolve_vars('0 0 0 3px var(--focus-ring)', lt, dk)}")
    arm("P9 `resolve_vars` 展开不了的 `var()` 原样保留",
        resolve_vars("0 0 0 3px var(--nope)", lt) == "0 0 0 3px var(--nope)",
        f"→ {resolve_vars('0 0 0 3px var(--nope)', lt)}")

    # ---- 归一 ----
    arm("N1 颜色写法归一（空格/前导零）",
        norm_color("rgba(54,96,176,.22)") == norm_color("rgba(54, 96, 176, 0.22)"),
        f"→ {norm_color('rgba(54,96,176,.22)')}")
    arm("N2 阴影归一",
        norm_shadow("0  0 0 3px   rgba(54,96,176,.22)") == norm_shadow(SPEC_RING),
        f"→ {norm_shadow('0  0 0 3px   rgba(54,96,176,.22)')}")
    arm("N3 环宽换算：`ring`（无数字）= 3px",
        ring_width_px("ring") == 3.0, f"→ {ring_width_px('ring')}")
    arm("N4 环宽换算：`ring-2` = 2px",
        ring_width_px("ring-2") == 2.0, f"→ {ring_width_px('ring-2')}")
    arm("N5 环色归一：`ring-brand-500/30` → `brand-500@30%`",
        ring_color_norm("ring-brand-500/30") == "brand-500@30%",
        f"→ {ring_color_norm('ring-brand-500/30')}")

    # ---- F1 / F2 ----
    p, _ = judge_tokens({"focus": SPEC_RING}, {"focus-ring": "rgba(54, 96, 176, 0.22)",
                                               "brand-500": "54 96 176"}, {})
    arm("T1 令牌全对 ⇒ 绿（对照）", p == [], f"→ {p}")
    p, _ = judge_tokens({"focus": "0 0 0 2px var(--focus-ring)"},
                        {"focus-ring": "rgba(54, 96, 176, 0.22)", "brand-500": "54 96 176"}, {})
    arm("T2 boxShadow.focus 环宽错 ⇒ 红", any("F1" in x for x in p), f"→ {p}")
    p, _ = judge_tokens({"focus": SPEC_RING}, {"focus-ring": "rgba(54, 96, 176, 0.3)",
                                               "brand-500": "54 96 176"}, {})
    arm("T3 --focus-ring 透明度错 ⇒ 红", any("F2" in x for x in p), f"→ {p}")
    p, _ = judge_tokens({"focus": SPEC_RING}, {"focus-ring": "rgba(54, 96, 176, 0.22)",
                                               "brand-500": "78 119 196"}, {})
    arm("T4 --brand-500 不是 54 96 176 ⇒ 红", any("F2" in x for x in p), f"→ {p}")
    p, _ = judge_tokens({}, {"focus-ring": "rgba(54, 96, 176, 0.22)",
                             "brand-500": "54 96 176"}, {})
    arm("T5 preset 缺 focus 键 ⇒ 红", any("F1" in x for x in p), f"→ {p}")
    # 🚨 令牌间接表达（preset 的**真实**写法）不得被报成缺陷 —— 否则是一条假红
    p, notes = judge_tokens({"focus": "0 0 0 3px var(--focus-ring)"},
                            {"focus-ring": "rgba(54, 96, 176, 0.22)",
                             "brand-500": "54 96 176"}, {})
    arm("T6 用 `var(--focus-ring)` 间接表达 ⇒ 绿（对照，证明按展开值比对）",
        p == [] and any("语义相等" in x for x in notes), f"→ p={p} notes={notes}")
    p, _ = judge_tokens({"focus": "0 0 0 3px var(--nope)"},
                        {"focus-ring": "rgba(54, 96, 176, 0.22)",
                         "brand-500": "54 96 176"}, {})
    arm("T7 `var()` 指向缺失令牌 ⇒ 红", any("F1" in x for x in p), f"→ {p}")

    # ---- F3 / F4 / F5 ----
    w, c, fs, br = _scan_text(FIXTURE_SRC_UNIFORM)
    p, notes, cov = judge_uniform(w, c, fs, br)
    arm("U1 统一且等于规范 ⇒ 绿（对照）", p == [], f"→ {p}")
    arm("U2 覆盖率 > 0", cov > 0, f"→ {cov}")
    w, c, fs, br = _scan_text(FIXTURE_SRC_SPLIT)
    p, _, _ = judge_uniform(w, c, fs, br)
    arm("U3 环宽 2px ≠ 3px ⇒ 红", any("F3" in x for x in p), f"→ {p}")
    arm("U4 环色 30/40/25% 不统一 ⇒ 红", any("F4" in x for x in p), f"→ {p}")
    arm("U5 无 `shadow-focus` 调用点 ⇒ 红", any("F5" in x for x in p), f"→ {p}")
    # 危险态：正常环取规范值 + 有 `shadow-focus` ⇒ 期望**全绿**（危险态被排除）
    w2, c2, _, _ = _scan_text(
        'className="focus:ring-[3px] focus:ring-brand-500/22 shadow-focus"\n'
        'className="focus-visible:ring-[3px] focus-visible:ring-danger-500/30"\n')
    p, notes, _ = judge_uniform(w2, c2, 1, [])
    arm("U6 危险态环**只报不判**（此时应全绿）", p == [], f"→ {p}")
    arm("U7 危险态环必须留在 notes 里（不能静默）",
        any("danger" in x for x in notes), f"→ {notes}")
    w3, c3, _, _ = _scan_text(
        'className="focus:ring-2 focus:ring-brand-500/22"\n'
        'className="focus:ring-4 focus:ring-brand-500/22"\n')
    p, _, _ = judge_uniform(w3, c3, 1, [])
    arm("U8 两种环宽 ⇒ 报「不统一」", any("不统一" in x for x in p), f"→ {p}")
    # 🚨 陷阱 1：环色不得被读成环宽（否则造出 40 处虚假「环宽不统一」）
    #    夹具只写环色、**不写环宽** ⇒ 环宽集合必须为空。
    #    （此处 F4 报「30% ≠ 22%」是**对的**，与本臂无关，故不把 `p == []` 写进断言。）
    w4, c4, _, _ = _scan_text('className="focus:ring-brand-500/30"\n')
    arm("U9 只写环色时**环宽集合必须为空**（不得凭空造出 `ring`=3px）",
        w4 == {}, f"→ widths={w4}")
    # 🚨 陷阱 2：`inset` / `offset-*` 是修饰符，不是颜色
    #    （`focus:ring-2` 会正确触发 F3，同样与本臂无关。）
    w5, c5, _, _ = _scan_text(
        'className="focus:ring-2 focus:ring-inset focus:ring-offset-2 '
        'focus:ring-offset-surface focus:ring-brand-500/22"\n')
    arm("U10 `inset`/`offset-*` 不得进环色集合",
        not any(x in c5 for x in ("ring-inset", "ring-offset-2",
                                  "ring-offset-surface")),
        f"→ colors={sorted(c5)}")
    # 对照：无数字的 `ring` 确实是 3px（不许为了避坑把真值也排掉）
    w6, c6, _, _ = _scan_text('className="focus:ring focus:ring-brand-500/22"\n')
    p, _, _ = judge_uniform(w6, c6, 1, [])
    arm("U11 对照：`focus:ring`（无数字）= 3px，且不报红",
        "ring" in w6 and ring_width_px("ring") == 3.0 and p == [],
        f"→ widths={w6} p={p}")
    # 裸环色只报不判（夹具其余项全部合规 ⇒ 唯一变量是那个裸环色）
    w7, c7, _, br7 = _scan_text(
        'className="focus:ring-[3px] focus:ring-brand-500/22 shadow-focus '
        'ring-brand-500/70"\n')
    p, notes, _ = judge_uniform(w7, c7, 1, br7)
    arm("U12 同行裸环色进 notes 而**不进** problems",
        p == [] and any("裸环色" in x for x in notes),
        f"→ p={p} notes={notes} bare={br7}")

    # ---- F6 / F7 / F8 ----
    def st(outline: str, shadow: str, style: str = "none",
           width: str = "0px", color: str = "rgb(0, 0, 0)") -> dict:
        return {"outline": outline, "outline-style": style,
                "outline-width": width, "outline-color": color,
                "box-shadow": shadow}

    BASE = st("", "rgba(16, 24, 40, 0.1) 0px 2px 4px", "none")

    def mk(focus: dict, base: dict | None = None, *, visible: bool = True,
           ancestor: str = "", tag: str = "button") -> dict:
        return {"tag": tag, "cls": "x", "visible": visible,
                "base": BASE if base is None else base, "focus": focus,
                "ancestor": ancestor}

    def page(*nodes: dict) -> dict:
        return {"nodes": list(nodes), "focusable": len(nodes),
                "sampled": len(nodes), "visible_focusable": len(nodes),
                "aligned": True, "anc_ok": True}

    p, _, d, cov = judge_focus(page(mk(st("", SPEC_RING, "none"))))
    arm("V1 出现规范焦点环 ⇒ 绿（对照）", p == [], f"→ {p}")
    arm("V2 覆盖率可观测", cov == 1, f"→ {cov}")
    p, _, _, _ = judge_focus(page(mk(st("", "0 0 0 2px rgba(54, 96, 176, 0.3)", "none"))))
    arm("V3 只有非规范焦点环 ⇒ 报「规范环一次都没出现」",
        any("F6" in x for x in p), f"→ {p}")
    p, _, _, _ = judge_focus(page(mk(BASE)))
    arm("V4 焦点态与未聚焦态**完全一样** ⇒ 红（F7）", any("F7" in x for x in p), f"→ {p}")
    p, _, _, _ = judge_focus(page(mk(st("", "none", "auto", width="1px"))))
    arm("V5 浏览器默认 outline(auto) ⇒ 不算「无指示」",
        not any("F7" in x for x in p), f"→ {p}")
    p, _, d, _ = judge_focus(page(mk(st("", SPEC_RING, "none")), mk(BASE)))
    arm("V6 分布里同时含规范值与基础阴影", len(d) == 2, f"→ {d}")
    p, _, _, _ = judge_focus(page(mk(st("", "none", "solid", width="0px"))))
    arm("V7 `outline-style: solid` 但宽度 0px ⇒ 仍算「无指示」",
        any("F7" in x for x in p), f"→ {p}")
    p, _, _, _ = judge_focus(page(mk(st("", "none", "solid", width="2px",
                                       color="rgba(0, 0, 0, 0)"))))
    arm("V8 `outline` 全透明 ⇒ 仍算「无指示」",
        any("F7" in x for x in p), f"→ {p}")
    p, notes, _, _ = judge_focus(page(mk(BASE, visible=False)))
    arm("V9 自身不可见的可聚焦元素 ⇒ **不判** F7，但留 note",
        not any("F7" in x for x in p) and any("不可见" in x for x in notes),
        f"→ p={p} notes={notes}")
    p, _, d, _ = judge_focus(page(mk(st("", SPEC_RING, "none"), visible=False)))
    arm("V10 规范环只出现在不可见元素上 ⇒ **不算**出现过（F6 红）",
        any("F6" in x for x in p), f"→ {p} d={d}")
    # 🚨 假绿回归：只有**装饰性基础阴影**、焦点态毫无变化的元素必须报红
    p, _, _, _ = judge_focus(page(mk(st("", "rgba(16, 24, 40, 0.1) 0px 2px 4px",
                                       "none"))))
    arm("V11 焦点态只有「基础装饰阴影」（与未聚焦态相同）⇒ 红"
        "（不得因有 box-shadow 就放过）", any("F7" in x for x in p), f"→ {p}")
    # 🚨 假绿回归：**变化了**但变化后的状态不可见（基础阴影被移除、outline 0px）
    p, _, _, _ = judge_focus(page(mk(st("", "none", "solid", width="0px"))))
    arm("V11b 变化了、但焦点态本身**不可见** ⇒ 仍算「无指示」",
        any("F7" in x for x in p), f"→ {p}")
    # 委托模式：自身无变化，但祖先 `focus-within` 有变化 ⇒ 不判
    p, notes, _, _ = judge_focus(page(mk(BASE, ancestor="祖先 <div>.focus-within 有变化")))
    arm("V12 自身无变化但祖先 `focus-within` 有变化 ⇒ 不判，且留 note",
        not any("F7" in x for x in p) and any("委托" in x for x in notes),
        f"→ p={p} notes={notes}")
    arm("V13 `focus_indicator` 能看出 ring 变化",
        "box-shadow" in focus_indicator(mk(st("", SPEC_RING, "none"))),
        f"→ {focus_indicator(mk(st('', SPEC_RING, 'none')))}")
    # 对照：焦点态在基础阴影之外**多出一圈规范环** ⇒ 全绿
    p, _, _, _ = judge_focus(page(mk(st("", "rgba(16, 24, 40, 0.1) 0px 2px 4px, "
                                       + SPEC_RING, "none"))))
    arm("V14 对照：基础阴影 + 规范焦点环 ⇒ 全绿", p == [], f"→ {p}")
    arm("V15 全透明的 box-shadow 不算可见",
        not shadow_visible(st("", "rgba(0, 0, 0, 0) 0px 0px 0px 0px")),
        f"→ {shadow_visible(st('', 'rgba(0, 0, 0, 0) 0px 0px 0px 0px'))}")
    arm("V16 三段式里只要有一段不透明就算可见",
        shadow_visible(st("", "rgba(0, 0, 0, 0) 0px 0px 0px 0px, "
                              "rgba(0, 0, 0, 0) 0px 0px 0px 0px, "
                              "rgba(16, 24, 40, 0.1) 0px 2px 4px")),
        "→ 见实现")
    # 🚨 假绿回归（真实页面实测踩到的那一类）：焦点态只是多了 Tailwind 的
    #    **全透明 ring 占位槽** ⇒ 字符串变了、视觉上什么都没变 ⇒ 必须报红。
    TAILWIND_BASE = st("", "rgba(16, 24, 40, 0.1) 0px 2px 4px", "none")
    TAILWIND_FOC = st("", "rgba(0, 0, 0, 0) 0px 0px 0px 0px, "
                          "rgba(0, 0, 0, 0) 0px 0px 0px 0px, "
                          "rgba(16, 24, 40, 0.1) 0px 2px 4px", "none")
    arm("V17 只多了**全透明** ring 占位槽（字符串变了、视觉没变）⇒ 不算指示",
        focus_indicator(mk(TAILWIND_FOC, TAILWIND_BASE)) == "",
        f"→ {focus_indicator(mk(TAILWIND_FOC, TAILWIND_BASE))!r}")
    p, _, _, _ = judge_focus(page(mk(TAILWIND_FOC, TAILWIND_BASE)))
    arm("V17b 同上 ⇒ F7 报红", any("F7" in x for x in p), f"→ {p}")
    arm("V18 对照：多出的是**不透明** ring ⇒ 算指示",
        "box-shadow" in focus_indicator(mk(st(
            "", "rgba(0, 0, 0, 0) 0px 0px 0px 0px, "
                "rgba(54, 96, 176, 0.3) 0px 0px 0px 2px", "none"), TAILWIND_BASE)),
        "→ 见实现")
    # 🚨 父链取不到时，F7 的结论必须**自带保留**（区分「产品坏了」与「我没测成」）
    p, notes, _, _ = judge_focus({**page(mk(BASE)), "anc_ok": False})
    arm("V19 祖先未能检查 ⇒ F7 结论带保留 + note",
        any("未能检查" in x for x in p) and any("未被祖先检查限定" in x for x in notes),
        f"→ p={p} notes={notes}")

    # ---- 收口 ----
    arm("X1 覆盖率 0 ⇒ exit 2", summary_exit(0, [], []) == 2)
    arm("X2 有缺陷 ⇒ exit 1", summary_exit(5, ["x"], []) == 1)
    arm("X3 环境坏 ⇒ exit 2", summary_exit(5, ["x"], ["env"]) == 2)
    arm("X4 全绿 ⇒ exit 0", summary_exit(5, [], []) == 0)

    print(f"── 自检 {n - len(fails)}/{n} 通过")
    for f in fails:
        print(f"   ✗ {f}")
    if fails:
        return 2
    print("   ✓ 每条判据都证明过会红，且对照臂干净")
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="§4.3「焦点环统一」门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（无浏览器）")
    ap.add_argument("--source-only", action="store_true", help="只跑 F1–F5（不开浏览器）")
    ap.add_argument("--dump", action="store_true", help="诊断模式：打原始数值，不下判断")
    ap.add_argument("--why", default=None, metavar="TEXT", help="取证：含该文本的可聚焦元素")
    ap.add_argument("--path", default=None, help="配合 --why：只测该路径")
    ap.add_argument("--app", default=None, help="只测某一端（web/lawyer/admin/im）")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.why:
        if not args.app:
            print("[env] --why 需要 --app（web/lawyer/admin/im）")
            return 2
        return asyncio.run(why(args.app, args.path, args.why))

    if args.dump:
        return asyncio.run(dump(args.app))

    if args.source_only:
        problems, notes, cov = run_static()
        print()
        for x in notes:
            print(f"   {x}")
        for x in problems:
            print(f"   ✗ {x}")
        print(f"\n── 令牌/源码层：覆盖率 {cov}，缺陷 {len(problems)} 条")
        return summary_exit(cov, problems, [])

    problems, notes, cov_s = run_static()
    rp, rnotes, dist, cov_r, env_break = asyncio.run(run_render(args.app))
    problems += rp
    notes += rnotes
    total = cov_s + cov_r
    print()
    for x in notes:
        print(f"   {x}")
    for x in problems:
        print(f"   ✗ {x}")
    print("\n── 渲染层焦点环取值分布 ──")
    for it in dist:
        mark = "  ← 规范值" if norm_shadow(SPEC_RING) in it["k"] else ""
        print(f"   ×{it['n']:4d}  `{it['k'][:86]}`{mark}")
    for x in env_break:
        print(f"   [env] {x}")
    print(f"\n── 覆盖率 源码层 {cov_s} + 渲染层 {cov_r} = {total}；缺陷 {len(problems)} 条")
    return summary_exit(total, problems, env_break)


if __name__ == "__main__":
    sys.exit(main())
