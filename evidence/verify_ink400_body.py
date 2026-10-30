#!/usr/bin/env python3
"""§2.1 `--ink-400`「禁用于正文」—— 源码级回归门禁。

## 为什么要有这条

`tokens.css` 与 `design-spec.md` §2.1 都**明文**写：

    --ink-400: … #9AA3AE 占位符/图标/装饰，**禁用于正文**   （对比度 2.5:1）

2026-10-13 的对比度专项（`deliverables/ui-design/contrast-ruling-2026-10-13.md`）全仓清掉了
**65 处**「`ink-400` 当正文用」，但那是**手工逐处**做的 —— 清完之后**没有任何源码判据**
拦「新写的 `ink-400` 正文」。渲染级 T4（`verify_contrast.py`）只测**每端 4 个页面**，
覆盖不到 `apps` 里的其余页面 ⇒ 这条缺口是真的。

⇒ 本判据补一条**源码级地板**：规范说的是「禁用于正文」，而「正文」在源码里是可判的
（带字号令牌的文字）。

## 出处（不自己发明期望值）

| 事实 | 值 | 出处 |
|---|---|---|
| `ink-400` 禁用于正文 | 明文 | `tokens.css`（`--ink-400` 行注释）· `design-spec.md` §2.1「用途」列 |
| 「正文」= 带字号令牌的文字 | 10 档 | `cn.ts` 的 `FONT_SIZE_TOKENS`（**现读**，与 `verify_typography.py` 同源） |

⚠️ **字号档不硬编码**：从 `frontend/packages/ui/src/lib/cn.ts` **现读** `FONT_SIZE_TOKENS`。
硬编码一份会在项目加档时**静默失明**（同 `verify_typography.py` 的理由）。

## 判据表

| 编号 | 检查 | 期望 |
|---|---|---|
| **R1** | 同一字符串字面量里「字号令牌 + **裸** `text-ink-400`」 | **0 处** |
| **R2** | `className="…text-ink-400…"` 的**直接文本**非空且非 `—` | **0 处** |
| **R3** | 同一 `cn(…)` 调用的**不同实参**里分别出现「字号令牌」与「裸 `text-ink-400`」 | **0 处** |

⚠️ **R3 是 2026-10-29 补的**：`cn()` 的**全部实参合并**才是一个 className，而 R1 按**单个字面量**
切分 ⇒ 下面这种写法 R1、R2 **都看不见**（实测：`lawyer/cases/[id]` 时间轴未到达步骤，
11px 文字压白底 **2.55:1**，靠渲染级 T4 才抓到）：

```tsx
className={cn("text-center text-caption", done ? "text-ink-700" : "text-ink-400")}
```

⇒ 「判据按写法切分」的**粒度选错**就会留整类缺口：判据的切分单位必须等于**产物的单位**
（className 的边界是 `cn()` 调用，不是字符串字面量）。

## 规范明文允许、**一律不判**的 `text-ink-400`

- `placeholder:text-ink-400`（占位符）—— 以及**任何**带状态前缀的（`hover:` / `focus:` /
  `dark:` / `disabled:` …）：静态判不出「非默认态」，那是渲染级的事；
- **图标**（`<Search className="text-ink-400" />`，无直接文本）；
- `—` 空值占位符 · `{expr}` 表达式占位 · `aria-hidden` 装饰。

⇒ 2026-10-13 全仓核对：剩余 **40 处** `text-ink-400` **全部**落在这几类里，逐处保留。

## 为什么只做源码层

「`ink-400` 禁用于正文」**与背景无关**（规范说它 2.5:1，任何浅色底都不够）⇒
**源码可判**。渲染级 T4 是**补充**（它测真实背景 + 字号门槛），不是替代。

## 边界（只证伪）

R1 用「同串共现」近似「同一 className」：若两个**无关**的字符串字面量恰好一行，
不会误判（按字面量切分，不按行）。但它**证不了**「没有别的写法把 `ink-400` 用在文字上」
（如变量拼接、动态 className）—— 拦的是**写法**，不是**行为**。
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[0]
UI_SRC = ROOT / "frontend" / "packages" / "ui" / "src"
APPS = ROOT / "frontend" / "apps"
CN_TS = UI_SRC / "lib" / "cn.ts"

# 扫描面：四端 app 源码 + 共享包源码。`node_modules` / 产物目录**必须**排除，
# 否则会把依赖里的 Tailwind 默认样式当成产品代码（同 `verify_design_tokens.py` 的剪枝理由）。
SKIP_DIRS = frozenset({"node_modules", ".next", "dist", "out", ".turbo", "coverage"})

# ── 期望值真源：现读 `cn.ts` 的 `FONT_SIZE_TOKENS` ──────────────────────────────
_FONT_LIST_RE = re.compile(r"FONT_SIZE_TOKENS\s*=\s*\[(.*?)\]", re.S)
_STR_ITEM_RE = re.compile(r'"([^"]+)"')

# ── R1/R2 的判定正则 ──────────────────────────────────────────────────────────
# 「裸」= 前面**不是** `:`（状态前缀）也不是字母/连字符（避免命中 `bg-ink-400` 之类）。
# 后面排除 `\w` 与 `/`（避免命中 `text-ink-400/30` 这类带透明度后缀的写法）。
BARE_INK400 = re.compile(r"(?<![:\w-])text-ink-400(?![\w/])")
# 字符串字面量（三种引号；模板串不跨行、不吞转义）。
STR_LIT = re.compile(r'"([^"\\\n]*)"|\'([^\'\\\n]*)\'|`([^`\\]*)`')
# R2：`text-ink-400` → 属性值结束引号 → `attrs` → `>` → **直接文本**（首字符非空白/非 `<`/`{`/`—`）。
# ⚠️ `attrs` 单独捕获：用来排除**自闭合标签**（`<Icon className="text-ink-400" aria-hidden />`）
#    —— 自闭合 = **无子节点** ⇒ 那个 `text-ink-400` 在**图标**上，紧跟的文字是**兄弟节点**
#    （实测假红：`billing` 的 `<TrendingUp … />`、`knowledge` 的 `<Database … />`）。
BARE_TEXT = re.compile(
    r"""text-ink-400[^"'\n]*["'`](?P<attrs>[^>\n]*)>\s*(?P<txt>[^<{\s—][^<>{}]{0,80})""")
# 「文字」的判据：含**至少一个**字母 / 数字 / 汉字。
# ⚠️ 纯符号（`›` `…` `·` `→`）是**装饰**（规范明文允许 `ink-400` 用于装饰）——
#    不加这条会把 `components-preview` 的 `›` 与 `Pagination` 的 `…` 判成违规（**实测假红**）。
_WORDY = re.compile(r"[0-9A-Za-z\u3400-\u9fff\uf900-\ufaff]")

# ── R3：`cn(...)` **跨实参** ────────────────────────────────────────────────────
# `cn()` 的所有实参**合并**成一个 className（这正是它存在的意义），所以「字号令牌 + 裸
# ink-400」分处两个实参时，R1（按单字面量切分）与 R2（要求 `>` 后跟文字）**双双失明**。
# ⇒ 判据的切分单位必须等于**产物**的单位：className 的边界是 `cn()` 调用。
CN_CALL = re.compile(r"(?<![\w.$])cn\s*\(")


def iter_cn_calls(clean: str):
    """产出 `(调用起于第几行, 实参在 `clean` 里的起始偏移, 实参文本)`。

    ⚠️ **必须带上起始偏移**：实参文本是从 `clean` 切出来的子串，子串内 `m.start()`
    是**相对偏移** ⇒ 直接用它会算出「行号 4」这种假位置（实测踩过：真违规在 :1295，
    报出来是 :4）。行号错的判据等于没判 —— 修的人会去改错地方。
    """
    n = len(clean)
    for m in CN_CALL.finditer(clean):
        i = m.end()          # 紧跟在 `(` 之后
        start = i
        depth = 1
        quote: str | None = None
        while i < n:
            c = clean[i]
            if quote is not None:
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    quote = None
                i += 1
                continue
            if c in "\"'`":
                quote = c
                i += 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    yield clean.count("\n", 0, m.start()) + 1, start, clean[start:i]
                    break
            i += 1



def read_font_tokens() -> list[str]:
    """从 `cn.ts` 现读语义字号档。读不到 ⇒ 返回空（调用方判 exit 2）。"""
    try:
        text = CN_TS.read_text(encoding="utf-8")
    except OSError:
        return []
    m = _FONT_LIST_RE.search(text)
    if not m:
        return []
    return _STR_ITEM_RE.findall(m.group(1))


def strip_comments(src: str) -> str:
    """剥掉 `//` 行注释、`/* */` 块注释、`{/* */}` JSX 注释。

    ⚠️ 用**状态机**而不是正则：正则版会在字符串里的 `//`（如 URL `https://…`）上误切。
    ⚠️ 剥注释是**必须**的 —— 否则注释里的示例会被当成真违规（`README.md` 坑：
    「注释里的示例也算命中」）。
    """
    out: list[str] = []
    i, n = 0, len(src)
    quote: str | None = None
    while i < n:
        c = src[i]
        if quote is not None:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(src[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n:
            nxt = src[i + 1]
            if nxt == "/":
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if nxt == "*":
                j = src.find("*/", i + 2)
                end = n if j < 0 else j + 2
                # ⚠️ 必须**保留注释内部的换行数** —— 否则块注释之后的**行号全部偏移**
                #    （实测：报 `TenantScopeBar.tsx:61`，而真违规在别处）。
                out.append("\n" * src.count("\n", i, end))
                i = end
                continue
        out.append(c)
        i += 1
    return "".join(out)


def scan_source(src: str, fonts: list[str]) -> list[tuple[int, str, str]]:
    """扫一段源码，返回 `[(行号, 规则, 摘要)]`。**纯函数**（自检直接拿它做臂）。"""
    clean = strip_comments(src)
    hits: list[tuple[int, str, str]] = []
    for m in STR_LIT.finditer(clean):
        s = next((g for g in m.groups() if g is not None), "")
        if not BARE_INK400.search(s):
            continue
        font = next((t for t in fonts if f"text-{t}" in s), None)
        if font is None:
            continue
        line = clean.count("\n", 0, m.start()) + 1
        hits.append((line, "R1", f"`text-{font}` + 裸 `text-ink-400` 同串：{s.strip()[:70]}"))
    for m in BARE_TEXT.finditer(clean):
        if m.group("attrs").rstrip().endswith("/"):
            continue  # 自闭合标签 ⇒ 图标，不是文字（见 `BARE_TEXT` 上的注释）
        txt = m.group("txt").strip()
        if not _WORDY.search(txt):
            continue  # 纯符号 ⇒ 装饰，规范允许
        line = clean.count("\n", 0, m.start()) + 1
        hits.append((line, "R2", f"`text-ink-400` 包文字：{txt[:40]}"))
    # R3：`cn()` 跨实参。只报「自己**不带**字号令牌」的那个字面量 ——
    # 自带字号令牌的已被 R1 命中，重复报会让「条数」虚高（棘轮口径靠条数）。
    for call_line, base, args in iter_cn_calls(clean):
        lits = [next(g for g in m.groups() if g is not None) for m in STR_LIT.finditer(args)]
        if not any(f"text-{t}" in s for s in lits for t in fonts):
            continue  # 整个 `cn()` 里没有字号令牌 ⇒ 不是正文（图标/容器），规范允许
        seen: set[int] = set()
        for m in STR_LIT.finditer(args):
            s = next((g for g in m.groups() if g is not None), "")
            if not BARE_INK400.search(s):
                continue
            if any(f"text-{t}" in s for t in fonts):
                continue  # R1 已覆盖
            # ⚠️ 必须加 `base`：`m.start()` 是相对实参子串的偏移（见 `iter_cn_calls` docstring）
            line = clean.count("\n", 0, base + m.start()) + 1
            if line in seen:
                continue
            seen.add(line)
            hits.append((line, "R3", f"`cn()` 跨实参：同调用另有字号令牌，本实参是裸 "
                                      f"`text-ink-400`（调用起于第 {call_line} 行）：{s.strip()[:60]}"))
    return hits


def iter_targets() -> list[pathlib.Path]:
    """枚举扫描面。`os.walk` + `dirnames[:]` 剪枝（不用 `rglob` —— 它会在 `node_modules` 上爬）。"""
    import os

    out: list[pathlib.Path] = []
    for root in (APPS, UI_SRC):
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.endswith((".tsx", ".ts")):
                    out.append(pathlib.Path(dirpath) / fn)
    return sorted(out)


# ── 自检（纯函数：合成 fixture，**不读真实仓库、不启浏览器**）────────────────────
SYN_FONTS = ["caption", "label", "body-sm", "body", "body-lg", "h4", "h3", "h2", "h1", "display"]

_SYN_CASES: tuple[tuple[str, str, int], ...] = (
    # 正例：A 类的**四种形态**（`contrast-ruling-2026-10-13.md` §8.2）
    ("S1 相邻", 'className="text-caption text-ink-400"', 1),
    ("S2 同串不相邻", 'className="text-caption leading-[1.55] text-ink-400"', 1),
    ("S3 其它字号令牌", 'className="mt-1 text-body-sm text-ink-400"', 1),
    ("S4 裸用包文字", '<span className="text-ink-400">待处理</span>', 1),
    # R3（2026-10-29 补）：`cn()` 跨实参 —— R1/R2 **都看不见**的形态
    ("S5 cn跨实参", 'className={cn("text-caption", "text-ink-400")}', 1),
    ("S6 cn跨实参-多行三元",
     '<div className={cn(\n  "text-center text-caption",\n'
     '  done ? "text-ink-700" : "text-ink-400"\n)}>', 1),
    # 阴性对照：规范明文允许的用法
    ("N1 占位符", 'className="placeholder:text-ink-400"', 0),
    ("N2 破折号占位", '<span className="text-ink-400">—</span>', 0),
    ("N3 图标无文本", '<Search className="text-ink-400" />', 0),
    ("N4 行注释示例", '// 例：className="text-caption text-ink-400"\nconst a = 1;', 0),
    ("N5 背景色", 'className="bg-ink-400/15"', 0),
    ("N6 块注释", '/* className="text-body text-ink-400" */\nconst b = 2;', 0),
    ("N7 表达式占位", '<span className="text-ink-400">{v || "—"}</span>', 0),
    ("N8 hover 态", 'className="hover:text-ink-400"', 0),
    ("N9 纯符号装饰", '<span className="text-ink-400">›</span>', 0),
    ("N10 自闭合图标+兄弟文字",
     '<TrendingUp className="h-4 w-4 text-ink-400" aria-hidden />\n  收入预测', 0),
    # R3 的阴性对照：`cn()` 里没有字号令牌 ⇒ 图标/容器，规范允许
    ("N11 cn内图标无字号", '<Search className={cn("h-4 w-4", "text-ink-400")} />', 0),
    ("N12 cn内状态前缀", 'className={cn("text-caption", "hover:text-ink-400")}', 0),
    ("N13 cn内无ink400", 'className={cn("text-caption", "text-ink-700")}', 0),
)


def run_self_test() -> int:
    bad: list[str] = []
    print("── `verify_ink400_body.py --self-test`（纯函数：合成 fixture，不读真实仓库）──")
    for name, src, want in _SYN_CASES:
        got = len(scan_source(src, SYN_FONTS))
        ok = got == want
        print(f"  [{'✓' if ok else '✗'}] {name:14s} 期望 {want} 条  实测 {got} 条")
        if not ok:
            bad.append(name)

    # 注入反证：摘掉 `BARE_INK400` 的判定 ⇒ S1（相邻形态）**必不再命中**。
    # ⚠️ 没有这一臂，「S1 命中」可能只是 R2 顺带命中的，证明不了 R1 真的在跑。
    global BARE_INK400
    saved = BARE_INK400
    BARE_INK400 = re.compile(r"(?!x)x")  # 永不匹配
    try:
        n = len(scan_source(_SYN_CASES[0][1], SYN_FONTS))
    finally:
        BARE_INK400 = saved
    ok = n == 0
    print(f"  [{'✓' if ok else '✗'}] I1 注入反证（摘掉 R1 判定 ⇒ S1 必不命中）  实测 {n} 条")
    if not ok:
        bad.append("I1")

    # 注入反证：摘掉 R3（`cn()` 跨实参）⇒ S5 **必不再命中**。
    # ⚠️ 同 I1 的理由：没有这一臂，「S5 命中」可能只是别的规则顺带命中的
    #    （S5 的字面量 `"text-ink-400"` 单看**不含**字号令牌 ⇒ R1 必然不命中，
    #     但只有真摘掉 R3 才能证明「S5 的 1 条**只**来自 R3」）。
    global iter_cn_calls
    saved_cn = iter_cn_calls
    iter_cn_calls = lambda _clean: iter(())  # noqa: E731  永不产出
    try:
        n = len(scan_source(_SYN_CASES[4][1], SYN_FONTS))
    finally:
        iter_cn_calls = saved_cn
    ok = n == 0
    print(f"  [{'✓' if ok else '✗'}] I2 注入反证（摘掉 R3 ⇒ S5 必不命中）  实测 {n} 条")
    if not ok:
        bad.append("I2")

    # 行号臂：判据报出的位置必须**指得准**。
    # ⚠️ 只数条数、不校行号的臂对「相对偏移」这类缺陷**完全失明**（实测：`cn()` 的实参是
    #    从全文切出的子串，直接用子串内偏移算出 `:4`，而真违规在 `:1295`）。
    got_lines = [ln for ln, _, _ in scan_source(_SYN_CASES[5][1], SYN_FONTS)]
    ok = got_lines == [3]
    print(f"  [{'✓' if ok else '✗'}] L1 行号（S6 的 `text-ink-400` 在第 3 行）  实测 {got_lines}")
    if not ok:
        bad.append("L1")

    pos = sum(1 for _, _, w in _SYN_CASES if w)
    if bad:
        print(f"❌ 自检失败 {len(bad)} 臂：{bad}")
        return 1
    print(f"✅ 自检通过：{len(_SYN_CASES)} 臂 + 2 注入反证 + 1 行号臂"
          f"（{pos} 正例 / {len(_SYN_CASES) - pos} 阴性对照）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="§2.1 `--ink-400`「禁用于正文」源码级门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（合成 fixture）")
    ap.add_argument("--source-only", action="store_true",
                    help="只跑源码层（**本判据默认即源码层**；此开关为登记表兼容）")
    a = ap.parse_args()
    if a.self_test:
        return run_self_test()

    fonts = read_font_tokens()
    if not fonts:
        print(f"[env] 读不到 `{CN_TS.relative_to(ROOT)}` 的 `FONT_SIZE_TOKENS` ⇒ "
              f"字号令牌没有真源，R1 结构上不可能判 ⇒ 「我没测成」（exit 2）")
        return 2

    files = iter_targets()
    if not files:
        print("[env] 扫描面为空（`frontend/apps` 与 `frontend/packages/ui/src` 下没有 .tsx/.ts）"
              " ⇒ 「我没测成」（exit 2）")
        return 2

    all_hits: list[tuple[pathlib.Path, int, str, str]] = []
    for f in files:
        try:
            src = f.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[env] 读不到 {f.relative_to(ROOT)}：{exc} ⇒ exit 2")
            return 2
        for line, rule, msg in scan_source(src, fonts):
            all_hits.append((f, line, rule, msg))

    if all_hits:
        print("❌ `--ink-400` 被用于正文（规范明文禁止）：")
        for f, line, rule, msg in all_hits:
            print(f"  ✗ [{rule}] {f.relative_to(ROOT)}:{line}")
            print(f"        {msg}")

    # ⚠️ **无条件**打印（`declared` 棘轮口径）—— 只在有缺陷时打印会让清零后
    #    「取不到条数」被判红（`verify_font_scale.py` 踩过，坑 50 同形）。
    print(f"── 源码层：缺陷 {len(all_hits)} 条 ──")
    print(f"   扫描面 {len(files)} 个文件 · 字号令牌真源 `cn.ts` 的 {len(fonts)} 档"
          f"（{'/'.join(fonts)}）")
    return 1 if all_hits else 0


if __name__ == "__main__":
    sys.exit(main())
