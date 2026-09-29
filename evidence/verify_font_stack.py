#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""§3.1「三套字体栈」门禁 —— **声明 / 加载 / 实际渲染** 三层。

## 为什么这个维度之前没有门禁

第十二轮覆盖率审计逐节枚举 `design-spec.md` 的 §-引用时发现：
29 个 `evidence/verify_*.py` 里

    $ grep -ln 'font-family\\|fontFamily\\|font-serif\\|font-mono\\|--font-' evidence/verify_*.py
    （0 命中）

而 `verify_typography.py` 的 docstring **自己就写明**它管的是 `font-weight`（T1/T1b/T1c）、
`line-height`（T3）、`font-size` —— **没有字体族**。

⇒ §3.1 是覆盖率矩阵上**唯一「整节零判据」的排印小节**。

## 出处

| 判据 | 内容 | 出处 |
|---|---|---|
| **F1** | 三套 `--font-*` 必须**存在**、**包含**规范表里的族名且**相对顺序一致**、并以通用族收尾 | `§3.1「三套字体栈」` |
| **F2** | 栈里**非系统预装**的族必须有加载证据（`@font-face` / `next/font` / 字体文件）⇒ **只报不判** | §3.1「Inter 负责西文与数字」「Inter 负责西文与数字」 |
| **F3a** | **实测**：衬线探针与无衬线探针的**实际渲染族必须不同** | §3.1「衬线体仅用于法条原文、引用摘录、文书正文」「这是最重要的差异化手段」 |
| **F3b** | 衬线探针实际渲染族 ∈ 规范衬线栈族名 ⇒ **只报不判** | §3.1「衬线 · 法条与文书」（不在即回退通用 `serif`，属环境） |
| **F4** | `font-serif` / `.legal-text` 使用点普查，按 §3.1「衬线体仅用于法条原文、引用摘录、文书正文」「**仅**用于法条原文、引用摘录、文书正文」逐条列出 ⇒ **只报不判** | §3.1「衬线体仅用于法条原文、引用摘录、文书正文」 |

## 🚨 判据设计的坑（本门禁诞生时实测到的）

1. 🚨 **`document.fonts.check()` 对本场景零判别力，且失败方式是「假绿」。**
   第一版探针用它测「13 个族哪些可用」，返回 **13/13 全 True** —— 包括
   `PingFang SC` / `Songti SC` / `SFMono-Regular` / `Menlo` 这些**只存在于 macOS** 的族。
   **对照组**：`document.fonts.check('16px "ZZZ_NoSuchFont_9f3a"')`（**我编的名字**）**也返回 True**。
   ⇒ 该 API 会把**任何**未注册为 `@font-face` 的族名报成「可用」。
   ⇒ 唯一可信的仪器是 **`CSS.getPlatformFontsForNode`**（DevTools「Rendered Fonts」的底层 API），
     它给的是**浏览器实际选中的字体族 + 字形数 + 是否 webfont**。
   ⇒ 教训：**「名字听起来正好的 API」不等于「有判别力」**；判据上线前必须拿一个
     **自证的阴性对照**打一发（证据：`evidence/font_stack_control.txt`）。
2. **「声明了」≠「加载了」≠「用上了」是三个不同的问题**，必须分三层测：
   源码声明（F1）→ 加载证据（F2）→ 实际渲染（F3）。
   实测本项目：`--font-sans` 首位是 `Inter`，但全仓 **0 个** `@font-face` / `next/font` /
   `<link>` / 字体文件，浏览器**实际渲染成 `Microsoft YaHei`**
   ⇒ §3.1「Inter 负责西文与数字」「Inter 负责西文与数字」**未实现**。
3. **守卫要盯「结论所依赖的前提」**：F3 的前提是「探针真的渲染出来了」。
   判据是 `glyphCount > 0`；为 0 ⇒ 样本没采到 ⇒ `exit 2`（**不是通过、也不是缺陷**）。
4. **衬线的判据不能用「白名单」**：白名单是我编的，不是规范的。
   改用**运行时自校准** —— 同页注入 `var(--font-serif)` 与 `var(--font-sans)` 两个探针，
   断言两者**渲染族不同**。把衬线探针换成无衬线栈即可证伪（自检臂 ②）。
5. **F4 的「注释关键词」是弱信号**，只用来**排序人工复核**，**不是判据**
   —— 「关键词命中 ≠ 有判据」这条纪律在**判据自己的实现**里同样成立。

用法：
    python evidence/verify_font_stack.py --self-test          # 纯函数自测（40 臂），不起浏览器
    python evidence/verify_font_stack.py                      # **默认：只扫源码层**（秒级，CI 跑这条）
    python evidence/verify_font_stack.py --render             # 加跑渲染层 F3（需 dev server + 浏览器）
    python evidence/verify_font_stack.py --render-self-test   # 渲染层故障注入自检（6 项）
    python evidence/verify_font_stack.py --why                # 打出处
    python evidence/verify_font_stack.py --dump               # 打印原始探针数据

退出码：0 = 通过；1 = 产品缺陷；2 = 环境问题
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parents[0]
FE = ROOT / "frontend"
SPEC = ROOT / "deliverables" / "ui-design" / "design-spec.md"
TOKENS = FE / "packages" / "ui" / "src" / "tokens.css"

#: CSS 通用族 + 系统关键字：由浏览器解析到系统默认，**不需要**加载
GENERIC = {
    "sans-serif", "serif", "monospace", "cursive", "fantasy", "fangsong",
    "system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded",
    "math", "emoji",
}

#: 操作系统预装族。⚠️ **这是工程判断，不是规范原文** —— 用途只是把
#: 「必须自己加载的族」和「可以假定系统有的族」分开。判断依据：这些族随
#: macOS / Windows / 常见 Linux 发行版分发。**已登记待拍板**（规范没说加载策略）。
OS_FONT = {
    # macOS
    "PingFang SC", "Hiragino Sans GB", "Songti SC", "Heiti SC", "STHeiti",
    "SFMono-Regular", "Menlo", "-apple-system",
    # Windows
    "Microsoft YaHei", "SimSun", "NSimSun", "Consolas", "Segoe UI", "Arial",
    # 跨平台 / Linux 发行版常见
    "Georgia", "Times New Roman", "Noto Serif SC", "Noto Sans SC",
    "Source Han Serif SC", "Liberation Mono", "Liberation Serif", "DejaVu Serif",
}

#: 规范 §3.1 表格三行的行首关键词 → 令牌名
SPEC_ROWS = (("无衬线", "sans"), ("衬线", "serif"), ("等宽", "mono"))

FONT_GLOBS = ("*.woff", "*.woff2", "*.ttf", "*.otf")

#: 预览 / 演示页标记（不计产品缺陷，沿用其它门禁的口径）
PREVIEW_MARKER = "components-preview"

#: **定义文件**：这些是「工具类 / 令牌 / Tailwind 映射」的**定义处**，不是**使用处**。
#: 🚨 必须按**文件**排除，不能只按行排除：`styles.css:46` 是规则头 `html .legal-text {`，
#: 它不含 `font-family` 也不含 `font-serif` 字样，只按行过滤会把它当成一处「使用点」——
#: 这正是本门禁第一版自测抓到的假阳性。
DEF_FILES = {
    "packages/ui/src/tokens.css",
    "packages/ui/src/styles.css",
    "tailwind.preset.ts",           # `serif: ["var(--font-serif)"]` 是映射，不是使用
}

#: 🚨 **生成物 / 历史构建目录** —— 必须整份排除。
#: 踩过的坑：第一版只跳过 `.next` 与 `node_modules`，于是 `find_load_evidence()` 在
#: `_prev_build/…/main-app.js`（**Next.js 自己的产物**）里找到了 `@font-face` 与 `next/font`，
#: 报「字体加载证据 30 条」⇒ **F2 判定「项目有加载字体」**，把真正的发现
#: （源码里 0 个加载机制）**静默压掉**，门禁还退 0。
#: ⇒ 教训：**扫「有没有 X」时把生成物算进样本，等于用别人的 X 证明自己有 X。**
IGNORED_PARTS = {"node_modules", "dist", "out", "coverage", "_prev_build"}


def _is_generated(p: pathlib.Path) -> bool:
    """生成物 / 历史构建目录（`.next`、`.next_old_v14_keep2`、`.next_corrupt_devmix`、`_prev*` …）。"""
    for part in p.parts:
        if part in IGNORED_PARTS:
            return True
        if part.startswith(".next") or part.startswith("_prev") or part.startswith("_tmp"):
            return True
    return False


def _walk_source_files():
    """遍历 `frontend` 下的文件，**在遍历时剪枝**。

    🚨 **不能用 `Path.rglob`**：它会把 `node_modules` / `.next` 整棵走完**再**过滤，
    实测本门禁因此从 ~2s 涨到 **67.7s**（CI 里 `verify_design_tokens.py` 栽在同一个坑，
    见 `run_ci_probes.py` 里 `verify_design_tokens.py` 那条棘轮记录的说明
    「修 `rglob` 剪枝前要 **160s** ⇒ 曾被当挂死」）。
    这里用 `os.walk` + 就地改写 `dirnames` 来剪枝。
    """
    for dirpath, dirnames, filenames in os.walk(FE):
        dirnames[:] = [d for d in dirnames if not _is_generated(pathlib.Path(d))]
        for fn in filenames:
            yield pathlib.Path(dirpath) / fn

#: F4 的弱信号关键词（仅用于给人工复核排序，**不作判据**）。
#: ⚠️ 不含裸 `legal` —— 它是类名 `legal-text` 的子串，会让**每一处**都命中（自造的假阳性）。
SANCTION_HINTS = ("法条", "原文", "引用", "摘录", "文书", "isLegal",
                  "basis", "citation", "判决", "合同")


# ─────────────────────────── 解析 ───────────────────────────

def split_stack(s: str) -> list[str]:
    """拆字体栈：按逗号切，去引号 / 空白。支持跨行（先压平换行）。"""
    out: list[str] = []
    for part in s.replace("\n", " ").split(","):
        p = part.strip().strip('"').strip("'").strip()
        if p:
            out.append(p)
    return out


def parse_spec_stacks(src: str) -> dict[str, list[str]]:
    """从 `design-spec.md` §3.1 的表格解析三套栈（**不硬编码**，改规范即生效）。"""
    lines = src.splitlines()
    start = None
    for n, line in enumerate(lines):
        if line.strip().startswith("### 3.1"):
            start = n
            break
    if start is None:
        return {}
    out: dict[str, list[str]] = {}
    for line in lines[start + 1:]:
        if line.strip().startswith("###"):
            break
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = re.search(r"`([^`]+)`", cells[1])
        if not m:
            continue
        for label, key in SPEC_ROWS:
            if label in cells[0]:
                out[key] = split_stack(m.group(1))
    return out


def parse_token_stacks(css: str) -> dict[str, list[str]]:
    """从 `tokens.css` 读 `--font-sans/serif/mono` 的**完整**值（支持跨行）。"""
    out: dict[str, list[str]] = {}
    for key in ("sans", "serif", "mono"):
        m = re.search(r"--font-" + key + r"\s*:\s*(.*?);", css, re.S)
        if m:
            out[key] = split_stack(m.group(1))
    return out


# ─────────────────────────── 纯函数判据 ───────────────────────────

def is_subsequence(need: list[str], have: list[str]) -> bool:
    """`need` 是否为 `have` 的**子序列**（允许实现加族，不允许改序 / 丢族）。"""
    j = 0
    for x in need:
        while j < len(have) and have[j] != x:
            j += 1
        if j >= len(have):
            return False
        j += 1
    return True


def judge_stack(name: str, spec_fams: list[str],
                impl_fams: list[str]) -> list[tuple[str, str]]:
    """F1：返回 `[(级别, 说明)]`，级别 ∈ {`red`, `note`}。"""
    out: list[tuple[str, str]] = []
    if not impl_fams:
        return [("red", f"`--font-{name}` 未声明（§3.1 要求三套栈齐全）")]
    missing = [f for f in spec_fams if f not in impl_fams]
    if missing:
        out.append(("red",
                    f"`--font-{name}` 缺少规范点名的族 {missing}"
                    f"（规范：{spec_fams}；实现：{impl_fams}）"))
    elif not is_subsequence(spec_fams, impl_fams):
        out.append(("red",
                    f"`--font-{name}` 族名**顺序**与规范不一致"
                    f"（规范：{spec_fams}；实现：{impl_fams}）"))
    if impl_fams[-1] not in GENERIC:
        out.append(("red",
                    f"`--font-{name}` 未以**通用族**收尾（末位 `{impl_fams[-1]}`）"
                    f"⇒ 字体缺失时没有兜底"))
    return out


def judge_load(name: str, impl_fams: list[str],
               evidence: list[str]) -> list[tuple[str, str]]:
    """F2：栈里「非系统预装」的族是否有加载证据。**只报不判**（规范未定加载策略）。"""
    need = [f for f in impl_fams if f not in GENERIC and f not in OS_FONT]
    if not need or evidence:
        return []
    who = need[0]
    extra = f"（另有 {', '.join(need[1:])}）" if len(need) > 1 else ""
    return [("note",
             f"`--font-{name}` 里的 {', '.join(need)} **非系统预装**，"
             f"但全仓找不到加载证据（0 个 `@font-face` / `next/font` / 字体文件）"
             f"⇒ §3.1:148「{who} 负责西文与数字」**未实现**{extra}")]


def find_load_evidence() -> list[str]:
    """全仓找字体加载证据：`@font-face` / `next/font` / 字体文件。

    ⚠️ **只认源码**：生成物整份排除（`_is_generated`），否则会在 Next.js 自己的
    bundle 里找到 `@font-face` 并得出「项目有加载字体」的**假结论**（见 `IGNORED_PARTS`）。
    """
    hits: list[str] = []
    for p in _walk_source_files():
        if p.suffix.lower() in (".woff", ".woff2", ".ttf", ".otf", ".eot"):
            hits.append(f"字体文件 {p.relative_to(FE).as_posix()}")
            continue
        if p.suffix.lower() not in (".css", ".ts", ".tsx", ".js", ".jsx", ".mjs"):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "@font-face" in t:
            hits.append(f"@font-face @ {p.relative_to(FE).as_posix()}")
        if "next/font" in t:
            hits.append(f"next/font @ {p.relative_to(FE).as_posix()}")
    return hits


def collect_sources() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for p in sorted(_walk_source_files()):
        if p.suffix.lower() not in (".tsx", ".ts", ".css"):
            continue
        try:
            out.append((p.relative_to(FE).as_posix(), p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    return out


def scan_serif_sites(files: list[tuple[str, str]]) -> list[dict]:
    """F4：找所有**使用**衬线的地方。

    排除两类「不是使用点」的东西：
      1. **定义文件**（`DEF_FILES`）—— 工具类 / 令牌的定义处；
      2. 定义行（`--font-serif:` 声明、`font-family: var(--font-serif)` 赋值）。
    """
    out: list[dict] = []
    for rel, text in files:
        if rel in DEF_FILES:
            continue
        lines = text.splitlines()
        for n, line in enumerate(lines, 1):
            is_class = "font-serif" in line
            is_legal = "legal-text" in line
            if not (is_class or is_legal):
                continue
            # 定义行：令牌声明 / 工具类本体
            if "--font-serif:" in line or "font-family: var(--font-serif)" in line:
                continue
            if is_class and re.search(r"font-serif\s*:", line):
                continue
            ctx = " ".join(x.strip() for x in lines[max(0, n - 3):n])
            out.append({
                "where": f"{rel}:{n}",
                "kind": "legal-text" if is_legal else "font-serif",
                "preview": PREVIEW_MARKER in rel,
                "hint": any(k in ctx or k in line for k in SANCTION_HINTS),
                "src": line.strip()[:110],
            })
    return out


# ─────────────────────────── 渲染层 ───────────────────────────

PROBE_JS = r"""(() => {
  document.querySelectorAll('[data-font-probe]').forEach(e => e.remove());
  const mk = (id, fam) => {
    const d = document.createElement('div');
    d.id = id;
    d.setAttribute('data-font-probe', '1');
    d.style.fontFamily = fam;
    d.style.fontSize = '16px';
    d.style.position = 'fixed';
    d.style.left = '-9999px';
    d.style.top = '0';
    d.textContent = '法条原文引用摘录 ABC123';
    document.body.appendChild(d);
  };
  mk('__fs_sans',  'var(--font-sans)');
  mk('__fs_serif', 'var(--font-serif)');
  mk('__fs_mono',  'var(--font-mono)');
  return document.querySelectorAll('[data-font-probe]').length;
})()"""

#: 🚨 故障注入：把**衬线探针也换成无衬线栈** ⇒ F3a **必须**转红。
#: 这是「差异化手段失效」的**唯一可造形态**，也是 F3a 检出能力的证明。
INJECT_SERIF_EQ_SANS_JS = r"""(() => {
  document.querySelectorAll('[data-font-probe]').forEach(e => e.remove());
  const mk = (id, fam) => {
    const d = document.createElement('div');
    d.id = id;
    d.setAttribute('data-font-probe', '1');
    d.style.fontFamily = fam;
    d.style.fontSize = '16px';
    d.style.position = 'fixed';
    d.style.left = '-9999px';
    d.style.top = '0';
    d.textContent = '法条原文引用摘录 ABC123';
    document.body.appendChild(d);
  };
  mk('__fs_sans',  'var(--font-sans)');
  mk('__fs_serif', 'var(--font-sans)');   /* ← 注入：衬线栈被吃掉 */
  mk('__fs_mono',  'var(--font-mono)');
  return document.querySelectorAll('[data-font-probe]').length;
})()"""

#: 守卫故障注入：删掉全部探针 ⇒ 字形采不到 ⇒ **必须**判「不可信」（exit 2）
REMOVE_PROBES_JS = """(() => {
  document.querySelectorAll('[data-font-probe]').forEach(e => e.remove());
  return document.querySelectorAll('[data-font-probe]').length;
})()"""

PROBES = (("#__fs_sans", "sans"), ("#__fs_serif", "serif"), ("#__fs_mono", "mono"))

#: **对照组**：拿一个**我编的**字体名去问 `document.fonts.check()`。
#: 它返回 `True` ⇒ 该 API 对本地字体可用性**零判别力**（见 docstring 坑 1）。
#: 本门禁因此改用 `CSS.getPlatformFontsForNode`。
#: ⚠️ 这是**仪器选择所依赖的前提** —— 若某天它转成 `False`，说明该 API 变可信，
#: 应当**重新评估仪器选择**，而不是继续沿用结论。故自检里为它留了一臂。
CONTROL_JS = """(() => {
  try { return document.fonts.check('16px "ZZZ_NoSuchFont_9f3a"'); }
  catch (e) { return 'ERR:' + e.message; }
})()"""


async def platform_fonts(cdp, selector: str) -> list[dict]:
    """`CSS.getPlatformFontsForNode` —— 浏览器**实际渲染**用的字体。"""
    doc = await cdp.send("DOM.getDocument", depth=1)
    r = await cdp.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=selector)
    nid = r.get("nodeId")
    if not nid:
        return []
    res = await cdp.send("CSS.getPlatformFontsForNode", nodeId=nid)
    return res.get("fonts", [])


def fams_of(fonts: list[dict]) -> set[str]:
    return {f.get("familyName") for f in fonts if f.get("familyName")}


def glyphs_of(fonts: list[dict]) -> int:
    return sum(int(f.get("glyphCount") or 0) for f in fonts)


def describe(fonts: list[dict]) -> str:
    if not fonts:
        return "（无）"
    return " · ".join(
        f"{f.get('familyName')}(glyphs={f.get('glyphCount')},"
        f"custom={f.get('isCustomFont')})" for f in fonts)


async def _probe_all(b, inject_js: str) -> dict[str, list[dict]]:
    await b.cdp.evaluate(inject_js)
    await b.settle(extra=0.6)
    out: dict[str, list[dict]] = {}
    for sel, label in PROBES:
        out[label] = await platform_fonts(b.cdp, sel)
    return out


async def run_render(dump: bool = False) -> tuple[list[str], int, int]:
    """返回 `(输出行, 缺陷数, 环境问题数)`。"""
    from cdp import Browser
    out: list[str] = []
    red = env = 0
    try:
        async with Browser(headless=True, width=1440, height=900,
                           device_scale_factor=1) as b:
            await b.cdp.send("CSS.enable")
            await b.goto("http://localhost:3000/", wait=1.5)
            await b.settle(extra=1.5)
            n = await b.cdp.evaluate(PROBE_JS)
            probes = await _probe_all(b, PROBE_JS)
            ctrl = await b.cdp.evaluate(CONTROL_JS)
            fonts_status = await b.cdp.evaluate(
                "document.fonts ? document.fonts.status : 'n/a'")
            n_faces = await b.cdp.evaluate(
                "(() => { let n=0; if(document.fonts) document.fonts.forEach(()=>n++); return n; })()")
    except Exception as e:                                            # noqa: BLE001
        out.append(f"[ENV] 渲染层异常：{type(e).__name__}: {e}")
        return out, 0, 1

    out.append(f"document.fonts.status={fonts_status} · 注册的 FontFace {n_faces} 个"
               f"（⚠️ 本轮实测：`__nextjs-*` 系 Next dev 注入，非产品字体）")
    out.append(f"注入探针 {n} 个")
    out.append("── 仪器选择：对照组（**证明为什么不用 `document.fonts.check`**）──")
    out.append(f"  document.fonts.check('16px \"ZZZ_NoSuchFont_9f3a\"') = {ctrl}"
               + ("  ⇒ 连**我编的**字体名都报 True ⇒ **该 API 零判别力**，"
                  "故改用 CSS.getPlatformFontsForNode"
                  if ctrl is True else
                  "  ⇒ 该 API 现在**可信**了 ⇒ **应重新评估仪器选择**"))
    out.append("── 每探针实际渲染（CSS.getPlatformFontsForNode）──")
    for _, label in PROBES:
        fs = probes.get(label) or []
        out.append(f"  var(--font-{label}): {describe(fs)}")

    # ── 守卫：结论依赖的前提是「探针真的渲染出来了」 ──
    empty = [label for _, label in PROBES if glyphs_of(probes.get(label) or []) == 0]
    if empty:
        out.append(f"  [ENV] {len(empty)} 个探针**一个字形都没渲染**（{', '.join(empty)}）"
                   f"⇒ **本轮渲染结论不可信**，判为环境问题而非通过")
        env += 1
    else:
        s_fams = fams_of(probes.get("sans") or [])
        r_fams = fams_of(probes.get("serif") or [])
        if not s_fams or not r_fams:
            out.append("  [ENV] 探针族名采不到 ⇒ 不可信")
            env += 1
        elif s_fams == r_fams:
            out.append(f"  ✗ [F3a] 衬线探针与无衬线探针**渲染族完全相同**"
                       f"（{sorted(s_fams)}）⇒ §3.1:149「最重要的差异化手段」失效")
            red += 1
        else:
            out.append(f"  ✓ [F3a] 衬线 ≠ 无衬线：{sorted(r_fams)} vs {sorted(s_fams)}")
    if dump:
        out.append(json.dumps(probes, ensure_ascii=False, indent=2))
    return out, red, env


async def render_self_test() -> int:
    """渲染层故障注入自检：证明 F3a 有检出能力、守卫能发现「采不到」。"""
    from cdp import Browser
    try:
        async with Browser(headless=True, width=1440, height=900,
                           device_scale_factor=1) as b:
            await b.cdp.send("CSS.enable")
            await b.goto("http://localhost:3000/", wait=1.5)
            await b.settle(extra=1.5)

            n1 = await b.cdp.evaluate(PROBE_JS)
            ok = await _probe_all(b, PROBE_JS)

            bad = await _probe_all(b, INJECT_SERIF_EQ_SANS_JS)

            n_removed = await b.cdp.evaluate(REMOVE_PROBES_JS)
            gone = await platform_fonts(b.cdp, "#__fs_serif")

            ctrl = await b.cdp.evaluate(CONTROL_JS)
    except Exception as e:                                            # noqa: BLE001
        print(f"[ENV] 渲染自检异常：{type(e).__name__}: {e}")
        return 2

    def differ(p):
        return fams_of(p.get("sans") or []) != fams_of(p.get("serif") or [])

    checks = [
        ("⓪ 探针注入数 == 3（否则后续断言会**空转**）", n1, 3),
        ("① 探针自身完整性：三个探针字形数都 > 0（否则「渲染族」是空的）",
         all(glyphs_of(ok.get(label) or []) > 0 for _, label in PROBES), True),
        ("② 正常态：衬线 ≠ 无衬线 ⇒ F3a 通过", differ(ok), True),
        ("③ 🚨检出能力：把衬线探针换成无衬线栈 ⇒ F3a **必须**转红",
         differ(bad), False),
        ("④ 守卫：删掉探针后 `CSS.getPlatformFontsForNode` 取不到字体 ⇒ 判「采不到」",
         (n_removed, len(gone)), (0, 0)),
        ("⑤ **仪器选择的前提**：`check('不存在族')` 仍返回 True ⇒ `document.fonts.check`"
         "依然零判别力（若转 False ⇒ 该 API 变可信，**应重新评估仪器选择**）",
         ctrl, True),
    ]
    n_bad = 0
    print("渲染层自检（故障注入）")
    for name, got, want in checks:
        okk = got == want
        n_bad += 0 if okk else 1
        print(f"  {'✓' if okk else '✗'} {name}")
    print(f"渲染自检 {'通过' if not n_bad else f'失败 {n_bad} 项'}"
          f"（6 项：1 注入数 + 1 探针完整性 + 1 正常态 + 1 检出能力 + 1 守卫"
          f" + 1 仪器前提）")
    return 0 if not n_bad else 1


# ─────────────────────────── 自测（纯函数） ───────────────────────────

SPEC_SNIPPET = """
### 3.1 三套字体栈

| 用途 | 字体栈 |
|---|---|
| UI 无衬线 | `Inter, "PingFang SC", "Microsoft YaHei", system-ui` |
| 衬线 · 法条与文书 | `"Noto Serif SC", "Source Han Serif SC", "Songti SC", Georgia, serif` |
| 等宽 · 数字与编号 | `ui-monospace, SFMono-Regular, Menlo, Consolas` |

Inter 负责西文与数字，中文回退系统字（避免加载 3–8 MB 中文字体包）。
衬线体仅用于法条原文、引用摘录、文书正文——这是最重要的差异化手段。

### 3.2 字号阶梯
"""

TOKENS_SNIPPET = """
:root {
  --font-sans: Inter, "PingFang SC", "Microsoft YaHei", "Hiragino Sans GB", system-ui,
    -apple-system, "Segoe UI", sans-serif;
  --font-serif: "Noto Serif SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
}
"""


def self_test() -> int:
    arms: list[tuple[str, object, object]] = []

    def arm(name: str, got, want) -> None:
        arms.append((name, got, want))

    # ── split_stack ──
    arm("split_stack 去引号", split_stack('a, "b c", d'), ["a", "b c", "d"])
    arm("split_stack 压平跨行",
        split_stack('a,\n  b, c'), ["a", "b", "c"])

    # ── parse_spec_stacks ──
    spec = parse_spec_stacks(SPEC_SNIPPET)
    arm("规范解析出三套栈", sorted(spec), ["mono", "sans", "serif"])
    arm("规范 sans 首位是 Inter", spec["sans"][0], "Inter")
    arm("规范 serif 含 Georgia", "Georgia" in spec["serif"], True)
    arm("规范 mono 首位 ui-monospace", spec["mono"][0], "ui-monospace")
    arm("解析不越界到 §3.2", parse_spec_stacks("### 3.2 字号阶梯\n| a | `x` |").get("sans"), None)

    # ── parse_token_stacks ──
    tk = parse_token_stacks(TOKENS_SNIPPET)
    arm("tokens 解析出三套", sorted(tk), ["mono", "sans", "serif"])
    arm("tokens sans 跨行完整（末位 sans-serif）", tk["sans"][-1], "sans-serif")
    arm("tokens sans 含 Segoe UI", "Segoe UI" in tk["sans"], True)

    # ── is_subsequence ──
    arm("子序列：允许实现加族", is_subsequence(["a", "c"], ["a", "b", "c"]), True)
    arm("子序列：改序即假", is_subsequence(["c", "a"], ["a", "b", "c"]), False)
    arm("子序列：丢族即假", is_subsequence(["a", "z"], ["a", "b", "c"]), False)

    # ── F1 judge_stack ──
    arm("F1 合规实现 ⇒ 无 red",
        [lvl for lvl, _ in judge_stack("sans", spec["sans"], tk["sans"])], [])
    arm("F1 缺族 ⇒ red",
        any(lvl == "red" and "缺少" in m
            for lvl, m in judge_stack("sans", spec["sans"],
                                      ["Inter", "system-ui", "sans-serif"])), True)
    arm("F1 顺序错 ⇒ red",
        any(lvl == "red" and "顺序" in m
            for lvl, m in judge_stack("sans", ["a", "b", "c"], ["b", "a", "c", "sans-serif"])), True)
    arm("F1 无通用族收尾 ⇒ red",
        any(lvl == "red" and "通用族" in m
            for lvl, m in judge_stack("sans", ["a"], ["a"])), True)
    arm("F1 未声明 ⇒ red",
        judge_stack("sans", ["a"], [])[0][0], "red")

    # ── F2 judge_load ──
    arm("F2 Inter 非系统预装且无证据 ⇒ note",
        any(lvl == "note" and "未实现" in m
            for lvl, m in judge_load("sans", tk["sans"], [])), True)
    arm("F2 有加载证据 ⇒ 无 note", judge_load("sans", tk["sans"], ["@font-face @ x"]), [])
    arm("F2 全是系统预装族 ⇒ 无 note",
        judge_load("serif", ["SimSun", "Georgia", "serif"], []), [])
    arm("F2 mono 全系统预装 ⇒ 无 note",
        judge_load("mono", tk["mono"], []), [])

    # ── F4 scan_serif_sites ──
    # fixture 按**真实文件**的形态写：styles.css 的第 46 行是规则头 `html .legal-text {`，
    # 它不含 `font-family` / `font-serif` 字样 —— 只按行过滤会漏，必须按文件排除。
    fake = [
        ("packages/ui/src/tokens.css",
         ':root {\n  --font-serif: "Noto Serif SC", Georgia, serif;\n}'),
        ("packages/ui/src/styles.css",
         "html .legal-text {\n  font-family: var(--font-serif);\n}"),
        ("apps/web/app/(app)/qa/page.tsx",
         "// 法律依据段整段用衬线体，是「原文」质感\n<p className=\"font-serif text-body\">"),
        ("apps/web/app/components-preview/page.tsx",
         "<p className=\"legal-text\">demo</p>"),
    ]
    sites = scan_serif_sites(fake)
    arm("F4 定义文件不算使用点（tokens.css / styles.css 整份排除）",
        [s["where"] for s in sites if "styles.css" in s["where"] or "tokens.css" in s["where"]], [])
    arm("F4 🚨 规则头 `html .legal-text {` 也不得被当成使用点（只按行过滤会漏）",
        any("legal-text {" in s["src"] for s in sites), False)
    arm("F4 产品使用点被抓到",
        any(s["where"].endswith("qa/page.tsx:2") for s in sites), True)
    arm("F4 有出处注释 ⇒ hint=True",
        all(s["hint"] for s in sites if "qa/page.tsx" in s["where"]), True)
    arm("F4 预览页被标记", any(s["preview"] for s in sites), True)
    arm("F4 预览页那条 hint=False（无出处注释）",
        [s["hint"] for s in sites if s["preview"]], [False])

    # ── fams_of / glyphs_of ──
    fs = [{"familyName": "A", "glyphCount": 3}, {"familyName": "B", "glyphCount": 4}]
    arm("fams_of", sorted(fams_of(fs)), ["A", "B"])
    arm("glyphs_of", glyphs_of(fs), 7)
    arm("glyphs_of 空 ⇒ 0", glyphs_of([]), 0)

    # ── 🚨 生成物排除（F2 假绿的根因）──
    P = pathlib.Path
    arm("生成物：_prev_build 被排除",
        _is_generated(P("_prev_build/web-dev-next-120726/static/chunks/main-app.js")), True)
    arm("生成物：.next 被排除",
        _is_generated(P("apps/web/.next/static/css/x.css")), True)
    arm("生成物：.next_old_v14_keep2 被排除",
        _is_generated(P("apps/im/.next_old_v14_keep2/static/css/x.css")), True)
    arm("生成物：.next_corrupt_devmix 被排除",
        _is_generated(P("apps/admin/.next_corrupt_devmix/static/css/x.css")), True)
    arm("生成物：.next_eprerm_bak_115619 被排除",
        _is_generated(P("apps/web/.next_eprerm_bak_115619/x")), True)
    arm("生成物：node_modules 被排除",
        _is_generated(P("node_modules/next/dist/x.js")), True)
    arm("源码：apps/web/app/layout.tsx **不得**被排除",
        _is_generated(P("apps/web/app/layout.tsx")), False)
    arm("源码：packages/ui/src/tokens.css **不得**被排除",
        _is_generated(P("packages/ui/src/tokens.css")), False)
    arm("源码：tailwind.preset.ts **不得**被排除",
        _is_generated(P("tailwind.preset.ts")), False)

    n_bad = 0
    print(f"§3.1 字体栈门禁 —— 纯函数自测（{len(arms)} 臂）")
    for name, got, want in arms:
        ok = got == want
        n_bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  got={got!r} want={want!r}"))
    print(f"自测 {'通过' if not n_bad else f'失败 {n_bad} 项'}（{len(arms)} 臂）")
    return 0 if not n_bad else 1


# ─────────────────────────── main ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="§3.1 三套字体栈门禁")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--render-self-test", action="store_true")
    ap.add_argument("--render", action="store_true",
                    help="加跑渲染层 F3（需浏览器 + dev server；默认只跑源码层）")
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--why", action="store_true")
    a = ap.parse_args()

    try:
        spec_src = SPEC.read_text(encoding="utf-8", errors="replace")
        tokens_src = TOKENS.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"[ENV] 读不到规范 / 令牌文件：{e}")
        return 2

    spec = parse_spec_stacks(spec_src)
    impl = parse_token_stacks(tokens_src)
    if len(spec) != 3 or not impl:
        print(f"[ENV] 解析失败：规范 {sorted(spec)} · 令牌 {sorted(impl)}（选择器失效？）")
        return 2

    if a.why:
        print(f"出处：{SPEC.relative_to(ROOT).as_posix()} §3.1")
        for k in ("sans", "serif", "mono"):
            print(f"  规范 --font-{k}：{spec[k]}")
            print(f"  实现 --font-{k}：{impl.get(k) or '（未声明）'}")
        print("  :148 「Inter 负责西文与数字，中文回退系统字（避免加载 3–8 MB 中文字体包）」")
        print("  :149 「衬线体**仅**用于法条原文、引用摘录、文书正文——这是最重要的差异化手段」")
        print("  F3a 的仪器：CSS.getPlatformFontsForNode（**不是** document.fonts.check —— 见 docstring 坑 1）")
        return 0

    if a.self_test:
        return self_test()
    if a.render_self_test:
        return asyncio.run(render_self_test())

    print("§3.1「三套字体栈」门禁")
    reds: list[tuple[str, str]] = []
    notes: list[tuple[str, str]] = []

    if not a.render_only:
        print("\n── 源码层 F1/F2 ──")
        evidence = find_load_evidence()
        print(f"  字体加载证据：{len(evidence)} 条"
              + (f" —— {evidence[:3]}" if evidence else "（**全仓 0 个 @font-face / next/font / 字体文件**）"))
        for k in ("sans", "serif", "mono"):
            for lvl, msg in judge_stack(k, spec[k], impl.get(k) or []):
                (reds if lvl == "red" else notes).append((f"F1/{k}", msg))
            for lvl, msg in judge_load(k, impl.get(k) or [], evidence):
                notes.append((f"F2/{k}", msg))
        for crit, msg in reds:
            print(f"  ✗ [{crit}] {msg}")
        for crit, msg in notes:
            print(f"  · [{crit}] {msg}")
        if not reds:
            print("  ✓ F1 三套栈的族名 / 顺序 / 通用族收尾均合规")

        files = collect_sources()
        sites = scan_serif_sites(files)
        prod = [s for s in sites if not s["preview"]]
        prev = [s for s in sites if s["preview"]]
        nohint = [s for s in prod if not s["hint"]]
        print(f"\n── 源码层 F4（**只报不判**）：衬线使用点 {len(sites)} 处"
              f"（产品 {len(prod)} · 预览页 {len(prev)}）──")
        print("  按 §3.1:149「**仅**用于法条原文、引用摘录、文书正文」逐条列出。")
        print("  ⚠️ 「无出处注释」是**弱信号**（关键词命中 ≠ 有判据），只用来排序人工复核。")
        for s in prod:
            flag = "有出处注释" if s["hint"] else "**无出处注释**"
            print(f"   [{s['kind']}] {s['where']}  — {flag}")
            print(f"       {s['src']}")
        if nohint:
            print(f"  → 其中 **{len(nohint)} 处没有出处注释**，建议优先人工裁定：")
            for s in nohint:
                print(f"      {s['where']}  ({s['kind']})")

    env = 0
    if a.render or a.render_only:
        print("\n── 渲染层 F3（实测实际渲染字体）──")
        lines, r, env = asyncio.run(run_render(dump=a.dump))
        for line in lines:
            print(f"  {line}")
        reds += [("F3a", line.strip()) for line in lines if line.strip().startswith("✗")]

    if env:
        print(f"\n[ENV] {env} 项环境问题 ⇒ 退出码 2（**不是通过、也不是缺陷**）")
        return 2
    if reds:
        print(f"\n✗ 缺陷 {len(reds)} 条")
        return 1
    print(f"\n✓ 通过（另有 {len(notes)} 条只报不判，见上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
