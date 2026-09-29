#!/usr/bin/env python
"""深色模式门禁：把「深色模式生效」从**静态声明**变成**渲染级判据**。

## 为什么需要它

决策 07 定的是「深色模式 CSS 变量化，零 `!important`」，此前的验收是
「深色模式 **89 条声明**逐项脚本校验通过」——那是**静态**校验（扫 CSS 源码）。

但深色模式是**浏览器解析**出来的：`.dark` 类有没有真的加上、变量有没有真的翻转、
有没有元素在深色下仍是浅色底 —— 静态门禁**一条都覆盖不到**。
⇒ 这正是本项目纪律 2「**源码里写了 ≠ 运行时生效**」的典型场景。

## 判据与出处

| 判据 | 断言 | 出处 |
|---|---|---|
| **D0** | 请求深色后，class 序列里 `dark` 一旦出现就**不得再消失**；且最终必须仍是 `dark`。判据用 **MutationObserver 记录**（确定性内核），**逐帧**记录只用于判「本次是否可见」 | `packages/ui/src/theme/ThemeProvider.tsx:24-28` 注释：「在 `<body>` 顶部注入，**避免主题切换白屏闪烁（首屏前即确定 class）**」 |
| **D1** | 深色下 `--surface-page` / `--surface-card` / `--text-primary` / `--ink-50` / `--ink-100` 的**计算值**必须等于 `tokens.css` **`.dark` 块**声明的值；且浅色下**必须不等于**该值 | `tokens.css` 的 `.dark` 块（**解析得到，不硬编码**） |
| **D1'** | **阴性对照**：`--ink-950` / `--brand-950` 在两档下**必须相同**（两处都注释了「恒定」） | `tokens.css:40/53`（浅）与 `:265/277`（深）注释「恒定」 |
| **D2** | 深色下 `color-scheme` 计算值 == `dark`；浅色下 == `light` | `tokens.css:238` `color-scheme: light;` / `:356` `color-scheme: dark;` |
| **D3** | 深色下**不得存在「浅色孤岛」**：可见、面积 ≥ `MIN_ISLAND_AREA`、**有效背景**相对亮度 > `LIGHT_LUM`，且**其父元素有效背景是深色**（即孤岛的边界元素） | `.dark` 块把所有 `--surface-*` 与 `--ink-50..950` 都翻成深色 ⇒ 深色模式下**不存在合法的浅色大面积表面** |

`LIGHT_LUM = 0.5` 与 `MIN_ISLAND_AREA = 4000px²` 是**工程阈值**（不是规范值）：
前者取「亮/暗」的中间点，后者用来排除图标、徽章这类小面积元素。**它们写在常量里，便于复核。**

### D3 的已知边界（别把它当成「深色模式全对」）

- **排除** `img` / `video` / `canvas` / `svg` 及其子树 —— 图片里有浅色像素是**正常内容**，
  不是缺陷。（代价：图片区域内的真实问题测不到。）
- **不管对比度**：`D4`（正文对比度 ≥ 4.5:1，WCAG 2.1 SC 1.4.3，规范 §8 阶段四把「对比度复核」
  列为待做项）**本稿不实现**，属已知边界 —— **已由 `verify_contrast.py` 接管**
  （判据 T1a/T1'/T2/T3 令牌层 + T4a/T4b 渲染级；它同时覆盖浅色档与深色档）。
- 只判「**大面积的浅色表面**」这一类最刺眼的失败，判不出「深色下某个语义色用错」这种细节。

## 前提锁（前提不成立就不判）

- 页面必须已渲染：有 `aside` / `nav[aria-label="主导航"]` / `header` 之一，且正文 ≥ 40 字符。
- `--surface-page` 等变量必须能读到 —— 读不到说明没进应用页（大概率停在 `/login`）⇒ **不判**。
- **`tokens_sanity()` 先跑**：`load_tokens()` 返回 `(浅, 深)`，一旦解包写反，
  所有比较会**一致地错**——自检也发现不了（它用同一套名字，前后自洽）。
  实测踩过：`DARK, LIGHT = load_tokens()` 写反 ⇒ 全量报出 **131 条假红**而 11 臂自检全绿。
  ⇒ 用**与命名无关的语义不变量**兜底（深色档页面底必须比浅色档暗、正文必须比浅色档亮），
  违反即 exit 2。自检 **Q0a/Q0b** 锁住。
- **D1' 是测量的自检**：恒定变量若在两档间发生变化 ⇒ **测量不可信**，直接 exit 2（不是产品缺陷）。

## 🚨 D0 为什么用两路证据（别只看逐帧）

实测：同一个页面、同一次加载，
**逐帧**只拍到 `['dark']`，而 **MutationObserver** 拍到 `['dark', '', 'dark']` ——
因为「摘除 → 加回」可能落在**同一个帧间隔内**，那一帧根本不存在，逐帧就拍不到。

⇒ 「摘除」是**恒定发生**的，「看得见」才是**竞态**。
**只看逐帧的判据会时红时绿——那不是判据。**
所以：**observer 判有没有摘除**（确定性），**逐帧判这次有没有露出来**（严重性）。
自检 **Q12** 专门锁这条：observer 有摘除、逐帧没有 ⇒ **仍必须报红**。

## 退出码

`0` 通过 / `1` 产品缺陷 / `2` 环境问题（含自检失败、前提不成立、**阴性对照被破坏**）

用法：

    python evidence/verify_dark_mode.py
    python evidence/verify_dark_mode.py --self-test
    python evidence/verify_dark_mode.py --dump
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

TOKENS_CSS = HERE.parents[0] / "frontend" / "packages" / "ui" / "src" / "tokens.css"

# 判据 D1：必须随模式翻转的变量
FLIP_VARS: tuple[str, ...] = ("surface-page", "surface-card", "text-primary", "ink-50", "ink-100")
# 判据 D1'：两档必须**相同**的变量（阴性对照）。两处都注释了「恒定」。
CONST_VARS: tuple[str, ...] = ("ink-950", "brand-950")

LIGHT_LUM = 0.5          # 相对亮度阈值（工程值，非规范值）
MIN_ISLAND_AREA = 4000   # px²，低于此面积不视为「大面积表面」（工程值，非规范值）

APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin",
            "pages": ("/", "/qa", "/documents")},
    "lawyer": {"port": 3001, "user": "lawyer_wang",
               "pages": ("/", "/cases", "/reviews")},
    "admin": {"port": 3002, "user": "admin",
              "pages": ("/", "/audit", "/complaints")},
    "im": {"port": 3003, "user": "client",
           "pages": ("/", "/chat", "/cases")},
}


# ---------------------------------------------------------------------------
# 解析 tokens.css —— **期望值从这里来，不硬编码**
# ---------------------------------------------------------------------------
def _block(css: str, selector: str) -> dict[str, str]:
    """取 `selector { ... }` 这个**顶层块**里的自定义属性声明。"""
    m = re.search(re.escape(selector) + r"\s*\{", css)
    if not m:
        raise KeyError(f"tokens.css 里找不到 `{selector}` 块")
    i = m.end()
    depth = 1
    while i < len(css) and depth:
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
        i += 1
    body = css[m.end(): i - 1]
    return {mm.group(1): mm.group(2).strip()
            for mm in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", body)}


def load_tokens(path: pathlib.Path = TOKENS_CSS) -> tuple[dict[str, str], dict[str, str]]:
    """返回 `(浅色档, 深色档)` —— 即 `(:root, .dark)`。**顺序不能反**。"""
    css = path.read_text(encoding="utf-8")
    return _block(css, ":root"), _block(css, ".dark")


def _lum(v: str) -> float:
    """把 `247 248 249` 这类三元组转成相对亮度（与页面里的算法一致）。"""
    r, g, b = (float(x) / 255 for x in re.split(r"[\s,]+", v.strip())[:3])

    def f(x: float) -> float:
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def tokens_sanity(light: dict[str, str], dark: dict[str, str]) -> list[str]:
    """**解析结果的语义自检**（不是产品判据）。

    为什么需要：判据里到处写 `light[...]` / `dark[...]`，一旦这两个 dict **装反了**，
    所有比较都会一致地错——**自检也发现不了**（它用的是同一套名字，前后自洽）。
    实测踩过：`DARK, LIGHT = load_tokens()` 写反 ⇒ 全量报出 **131 条假红**，
    而 11 臂自检**全绿**。

    ⇒ 用**与命名无关的语义不变量**兜底：深色档的页面底必须比浅色档**暗**、
    正文色必须比浅色档**亮**。违反 ⇒ 测量不可信（exit 2）。
    """
    bad: list[str] = []
    checks = (
        ("surface-page", "浅", "深"),   # 页面底：深色档应更暗
        ("surface-card", "浅", "深"),
        ("ink-50", "浅", "深"),
    )
    for name, a, b in checks:
        if name not in light or name not in dark:
            bad.append(f"`--{name}` 在两档中缺一")
            continue
        if _lum(light[name]) <= _lum(dark[name]):
            bad.append(f"`--{name}`：浅色档亮度 {_lum(light[name]):.3f} 未高于深色档 "
                       f"{_lum(dark[name]):.3f} ⇒ **两档疑似装反了**")
    if "text-primary" in light and "text-primary" in dark:
        if _lum(dark["text-primary"]) <= _lum(light["text-primary"]):
            bad.append(f"`--text-primary`：深色档亮度 {_lum(dark['text-primary']):.3f} "
                       f"未高于浅色档 {_lum(light['text-primary']):.3f} ⇒ **两档疑似装反了**")
    return bad


# ---------------------------------------------------------------------------
# 注入脚本：逐帧记录 `documentElement.className`，并记录 MutationObserver 一路作为兜底。
# 只做**观察**，不下判断。`localStorage` 由 Python 侧在导航前设好，脚本不碰。
# ---------------------------------------------------------------------------
INJECT_JS = r"""
(() => {
  window.__nlawFrames = [];
  window.__nlawClassLog = [];
  const push = (arr, c) => {
    const last = arr[arr.length - 1];
    if (last && last.c === c) return;
    arr.push({ c: c, t: Math.round(performance.now()) });
  };
  let n = 0;
  const tick = () => {
    const de = document.documentElement;
    if (de) push(window.__nlawFrames, de.className);
    if (++n < 240) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  try {
    new MutationObserver(() => {
      const de = document.documentElement;
      if (de) push(window.__nlawClassLog, de.className);
    }).observe(document, { attributes: true, attributeFilter: ['class'], subtree: true });
  } catch (e) {}
})();
"""


# ---------------------------------------------------------------------------
# `--why` 用：抓「谁摘掉了 dark」。**不靠读代码推断根因**——在摘除发生的当下取调用栈。
# ---------------------------------------------------------------------------
WHY_JS = r"""
(() => {
  window.__nlawWhy = [];
  const rec = (op, detail, self) => {
    try {
      window.__nlawWhy.push({
        op: op, detail: detail,
        now: Array.from(self).includes('dark'),
        stack: (new Error().stack || '').split('\n').slice(1, 7).join('  |  '),
      });
    } catch (e) {}
  };
  const t = DOMTokenList.prototype.toggle;
  const r = DOMTokenList.prototype.remove;
  const a = DOMTokenList.prototype.add;
  // ⚠️ 过滤必须看**实参**，不能看调用后的 token 列表：
  //    `add('dark')` 调用时列表里**还没有** dark，用 `includes('dark')` 过滤会把它漏掉
  //    —— 实测踩过：只记到 `toggle(false)`，把「谁加上的 dark」整段丢了。
  DOMTokenList.prototype.toggle = function (tok, force) {
    const hit = tok === 'dark';
    const out = t.apply(this, arguments);
    if (hit) rec('toggle(' + String(force) + ')', tok, this);
    return out;
  };
  DOMTokenList.prototype.remove = function () {
    const hit = Array.from(arguments).includes('dark');
    const out = r.apply(this, arguments);
    if (hit) rec('remove', Array.from(arguments).join(','), this);
    return out;
  };
  DOMTokenList.prototype.add = function () {
    const hit = Array.from(arguments).includes('dark');
    const out = a.apply(this, arguments);
    if (hit) rec('add', Array.from(arguments).join(','), this);
    return out;
  };
  // 顺带记 `localStorage['nlaw-theme']` 的每一次写入 —— 那个 effect 同时会持久化，
  // 若它先用初值 `light` 写一遍，用户已存的偏好会被**瞬时覆盖**。
  const si = Storage.prototype.setItem;
  Storage.prototype.setItem = function (k, v) {
    const out = si.apply(this, arguments);
    try {
      if (k === 'nlaw-theme') {
        window.__nlawWhy.push({op: 'setItem', detail: k + '=' + String(v), now: null,
                               stack: (new Error().stack || '').split('\n').slice(1, 5).join('  |  ')});
      }
    } catch (e) {}
    return out;
  };
})();
"""


# ---------------------------------------------------------------------------
# 测量 JS：只**观察**，判断全在 Python 侧（便于自检直接喂合成数据）。
# ---------------------------------------------------------------------------
MEASURE_JS = r"""(() => {
  const cs = getComputedStyle(document.documentElement);
  const readVar = (n) => cs.getPropertyValue('--' + n).trim();

  const parse = (s) => {
    const m = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:[,\s/]+([\d.]+))?/.exec(s || '');
    if (!m) return null;
    return { r: +m[1], g: +m[2], b: +m[3], a: m[4] === undefined ? 1 : +m[4] };
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b);
  };
  const effBg = (el) => {
    let n = el;
    while (n && n.nodeType === 1) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c.a > 0.5) return c;
      n = n.parentElement;
    }
    return null;
  };
  const visible = (el) => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity < 0.05) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  // 图片/视频/画布/SVG 里的浅色像素是**正常内容**，不是「浅色孤岛」
  const SKIP = new Set(['IMG', 'VIDEO', 'CANVAS', 'SVG', 'PICTURE', 'IFRAME']);

  const islands = [];
  for (const el of document.querySelectorAll('body *')) {
    if (SKIP.has(el.tagName)) continue;
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < __MIN_AREA__) continue;
    const bg = effBg(el);
    if (!bg) continue;
    if (lum(bg) <= __LIGHT_LUM__) continue;
    // 只报「孤岛的边界元素」：自身浅、**父元素有效背景是深色** ⇒ 避免整棵子树重复报
    const pbg = el.parentElement ? effBg(el.parentElement) : null;
    if (pbg && lum(pbg) > __LIGHT_LUM__) continue;
    islands.push({
      tag: el.tagName.toLowerCase(),
      cls: (el.getAttribute('class') || '').slice(0, 90),
      w: Math.round(r.width), h: Math.round(r.height),
      bg: `rgb(${bg.r} ${bg.g} ${bg.b})`, lum: +lum(bg).toFixed(3),
    });
  }

  const shell = document.querySelector('aside, nav[aria-label="主导航"], header');
  return {
    vw: innerWidth,
    colorScheme: cs.colorScheme || '',
    vars: Object.fromEntries(__VARS__.map((n) => [n, readVar(n)])),
    classNow: document.documentElement.className,
    frames: window.__nlawFrames || [],
    classLog: window.__nlawClassLog || [],
    islands: islands,
    hasShell: !!shell,
    contentLen: (document.body.innerText || '').trim().length,
  };
})()"""


def measure_js() -> str:
    names = list(FLIP_VARS) + list(CONST_VARS)
    return (MEASURE_JS
            .replace("__MIN_AREA__", str(MIN_ISLAND_AREA))
            .replace("__LIGHT_LUM__", str(LIGHT_LUM))
            .replace("__VARS__", repr(names).replace("'", '"')))


# ---------------------------------------------------------------------------
# 判据（纯函数，可被自检直接喂数据）
# ---------------------------------------------------------------------------
def norm(v: str) -> str:
    """把 `11 14 18` / `11,14,18` / 多余空白归一，便于比较。"""
    return " ".join(re.split(r"[\s,]+", (v or "").strip()))


def judge(m: dict, want_mode: str, light: dict[str, str], dark: dict[str, str]) -> list[str]:
    """返回问题列表（空 = 通过）。`want_mode` 是本次**请求**的模式。"""
    bad: list[str] = []
    dark_mode = want_mode == "dark"

    # —— D1 / D1'：变量是否按 `.dark` 块翻转 ——
    for name in FLIP_VARS:
        got = norm(m["vars"].get(name, ""))
        want = norm(dark[name] if dark_mode else light[name])
        if not got:
            bad.append(f"[D1] 读不到 `--{name}`（页面可能没进应用）")
            continue
        if got != want:
            bad.append(f"[D1] {want_mode} 下 `--{name}` = `{got}`，"
                       f"tokens.css 声明的是 `{want}`")
    # 阴性对照：恒定变量必须与模式无关
    for name in CONST_VARS:
        got = norm(m["vars"].get(name, ""))
        want = norm(dark[name])
        if got and got != want:
            # 这不判产品，而是判**测量**：见 summary_exit
            bad.append(f"[D1'·对照被破坏] `--{name}` 声明为「恒定」`{want}`，"
                       f"实测 `{got}`（{want_mode}）⇒ 测量不可信")

    # —— D2：color-scheme ——
    want_cs = "dark" if dark_mode else "light"
    if m.get("colorScheme") and want_cs not in m["colorScheme"]:
        bad.append(f"[D2] {want_mode} 下 `color-scheme` = `{m['colorScheme']}`，期望含 `{want_cs}`"
                   f"（tokens.css:238/356）")

    # —— D0：深色 class 不得在加载过程中被摘掉 ——
    if dark_mode:
        # ⚠️ **两路证据，分工不同**（实测踩出来的）：
        #   · `classLog`（MutationObserver）—— **确定性内核**：每一次 class 变更都会记到，
        #     与「有没有被渲染出来」无关。
        #   · `frames`（逐帧）—— **严重性信号**：是否真的有一帧被画成浅色。
        # 为什么必须用 observer 判：实测同一个页面（`im/cases`）逐帧只拍到 `['dark']`，
        # 而 observer 拍到 `['dark','','dark']` ⇒ 「摘除」是**恒定发生**的，
        # 「看得见」才是竞态。**只看逐帧的判据会时红时绿——那不是判据。**
        log = m.get("classLog") or []
        frames = m.get("frames") or []
        to_labels = lambda seq: [  # noqa: E731
            "dark" if "dark" in (f.get("c") or "").split() else "light" for f in seq
        ]
        labels = to_labels(log) or to_labels(frames)
        flabels = to_labels(frames)
        if not labels:
            bad.append("[D0] 前提不成立：没拿到任何帧/变更记录")
        else:
            if labels[0] != "dark" and "dark" in labels:
                bad.append(f"[D0] 首帧不是 dark（序列 {labels[:6]}）——"
                           f"`ThemeScript` 应在首屏前就加上 class")
            if "dark" in labels:
                first = labels.index("dark")
                after = labels[first:]
                if any(x != "dark" for x in after):
                    # 可见性：逐帧里有没有真的拍到浅色
                    seen = any(x != "dark" for x in flabels[flabels.index("dark"):]) if "dark" in flabels else False
                    vis = ("**本次逐帧拍到了浅色帧 ⇒ 可见闪烁**" if seen
                           else "本次逐帧没拍到（摘除与加回落在同一帧内）——"
                                "但这条路径恒定会摘除，**负载高/水合慢时就会露出来**")
                    bad.append(f"[D0] 深色 class **被摘掉又加回**：observer 序列 {labels[:8]}"
                               f" ⇒ 用户可能看到「深→浅→深」闪烁；{vis}"
                               f"（`ThemeProvider.tsx:24-28` 的注释写明要「首屏前即确定 class」）")
            if labels[-1] != "dark":
                bad.append(f"[D0] 结束时 class 不是 dark（`{m.get('classNow')}`）⇒ 请求深色没生效")
        # —— D3：浅色孤岛 ——
        for it in m.get("islands") or []:
            bad.append(f"[D3] 深色下出现浅色孤岛：<{it['tag']}> {it['w']}×{it['h']} "
                       f"bg={it['bg']} 亮度={it['lum']} class=`{it['cls']}`")

    return bad


def summary_exit(total: int, all_bad: list[str], control_broken: list[str]) -> int:
    """收口判定（抽成纯函数，便于自检锁住这两条）。

    🚨 `total == 0` 必须返回 **2**（「我没测成」≠「产品健康」）。
    🚨 阴性对照被破坏 ⇒ **2**：测量本身不可信，此时报红报绿都没有意义。
    """
    if control_broken:
        return 2
    if total == 0:
        return 2
    if all_bad:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 自检：**合成页面**。浏览器臂（Q1–Q7）+ 纯函数臂（Q8/Q9）。
# ---------------------------------------------------------------------------
LIGHT, DARK = load_tokens()   # ⚠️ 顺序：`load_tokens()` 返回 `(:root, .dark)` = `(浅, 深)`

_FILLER = ("本段为合成夹具的填充正文，用于让页面通过「已渲染」前提锁，不含任何产品语义。")


def _root_css(vals: dict[str, str], scheme: str) -> str:
    return (":root{color-scheme:" + scheme + ";"
            + "".join(f"--{k}: {v};" for k, v in vals.items()) + "}")


def _page(vals: dict[str, str], cls: str, body: str, head_script: str = "",
          scheme: str = "dark") -> str:
    """合成夹具。

    ⚠️ **每臂都要把全部前提表达出来**，否则臂测的就不是它声称的那一条：
    `color-scheme` 必须显式声明（`tokens.css:238/356` 就是这么写的），
    不然 D2 会在**每一个**臂上报红，把其它臂的结论淹掉。
    """
    return (
        "<!doctype html><html class='" + cls + "'><head><meta charset='utf-8'>"
        f"<style>{_root_css(vals, scheme)}html,body{{margin:0}}</style>{head_script}"
        f"</head><body>{body}</body></html>"
    )


def _block_of(color: str, w: int = 300, h: int = 200) -> str:
    return (f"<div style='width:{w}px;height:{h}px;background:{color}'>{_FILLER}</div>")


# 逐帧驱动的脚本：加 dark → 摘掉 → 加回（模拟「provider 与 script 打架」）
_FLASH_SCRIPT = """<script>
requestAnimationFrame(function(){
  var de=document.documentElement; de.classList.add('dark');
  requestAnimationFrame(function(){ de.classList.remove('dark');
    requestAnimationFrame(function(){ de.classList.add('dark'); });
  });
});
</script>"""

# 只加一次、不摘（对照组）
_STEADY_SCRIPT = "<script>document.documentElement.classList.add('dark');</script>"

SELFTEST: tuple[tuple[str, str, dict, str, str, str, bool, str], ...] = (
    # key, 描述, 变量表, class, body, 请求模式, 期望判红, 夹具声明的 color-scheme
    ("Q1", "深色档变量齐备 + 深色底 ⇒ D1/D3 **必须干净**（对照组）",
     DARK, "dark", _block_of("#0b0e12"), "dark", False, "dark"),
    ("Q2", "深色档但 `--surface-page` 仍是浅色值 ⇒ D1 **必须报红**（检出能力）",
     {**DARK, "surface-page": LIGHT["surface-page"]}, "dark", _block_of("#0b0e12"), "dark", True, "dark"),
    ("Q3", "深色底上放一块 300×200 的**白块** ⇒ D3 **必须报红**",
     DARK, "dark", _block_of("#0b0e12") + _block_of("#ffffff"), "dark", True, "dark"),
    ("Q4", "深色底上放一块 300×200 的**深块** ⇒ D3 **必须干净**（对照组）",
     DARK, "dark", _block_of("#0b0e12") + _block_of("#14181d"), "dark", False, "dark"),
    ("Q5", "浅色模式下同样的**白块** ⇒ D3 **必须干净**（D3 只在深色档生效）",
     LIGHT, "", _block_of("#ffffff"), "light", False, "light"),
    ("Q6", "深色 class 被**摘掉又加回** ⇒ D0 **必须报红**（检出能力）",
     DARK, "", _block_of("#0b0e12"), "dark", True, "dark"),
    ("Q7", "深色 class 加上后**不再摘** ⇒ D0 **必须干净**（对照组）",
     DARK, "", _block_of("#0b0e12"), "dark", False, "dark"),
)


async def run_self_test() -> int:
    print("自检（合成页面，与产品无关）—— 各臂分开报：")
    bad: list[str] = []

    # —— Q0：解析结果的语义自检（防「两档装反」）——
    for key, desc, args, expect_bad in (
        ("Q0a", "解析顺序正确（`:root`=浅、`.dark`=深）⇒ tokens_sanity **必须干净**",
         (LIGHT, DARK), False),
        ("Q0b", "两档**装反** ⇒ tokens_sanity **必须报出**（这一臂锁的就是那 131 条假红的成因）",
         (DARK, LIGHT), True),
    ):
        probs = tokens_sanity(*args)
        got = bool(probs)
        ok = got == expect_bad
        print(f"  [{key}] {desc}")
        print(f"        期望{'报出' if expect_bad else '干净':4s} 实测{'报出' if got else '干净':4s} "
              f"{'✓' if ok else '✗'}")
        if probs:
            for p in probs[:2]:
                print(f"          · {p}")
        if not ok:
            bad.append(f"[{key}] 期望{'报出' if expect_bad else '干净'} 实测{'报出' if got else '干净'}")

    async with Browser(headless=True, width=1280, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        await b.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=INJECT_JS)
        for key, desc, vals, cls, body, mode, expect_red, scheme in SELFTEST:
            # 夹具：Q6 用「闪一下」的脚本驱动 class；Q7 用「只加一次」的脚本
            head = ""
            if key == "Q6":
                head = _FLASH_SCRIPT
            elif key == "Q7":
                head = _STEADY_SCRIPT
            elif cls == "dark":
                head = _STEADY_SCRIPT
            url = "data:text/html;charset=utf-8," + urllib.parse.quote(
                _page(vals, cls, body, head, scheme))
            await b.goto(url, wait=0.35)
            await b.settle(extra=0.9)
            m = await b.cdp.evaluate(measure_js())
            print(f"  [{key}] {desc}")
            if not m:
                print("        前提不成立：取不到测量结果 ⇒ **夹具坏了**")
                bad.append(f"[{key}] 取不到测量结果")
                continue
            problems = judge(m, mode, LIGHT, DARK)
            got_red = bool(problems)
            ok = got_red == expect_red
            frames = [f.get("c") for f in (m.get("frames") or [])]
            print(f"        class 逐帧={frames[:6]}  期望{'报红' if expect_red else '干净':4s} "
                  f"实测{'报红' if got_red else '干净':4s} {'✓' if ok else '✗'}")
            if problems:
                for p in problems:
                    print(f"          · {p}")
            if not ok:
                bad.append(f"[{key}] 期望{'报红' if expect_red else '干净'} 实测{'报红' if got_red else '干净'}")

    # —— 纯函数臂 ——
    # Q12 锁的是「只看逐帧会漏」这条：observer 拍到摘除、逐帧没拍到 ⇒ **仍必须报红**。
    def _mk(cls_now: str, frames: list[str], log: list[str]) -> dict:
        mk = lambda seq: [{"c": c, "t": i} for i, c in enumerate(seq)]  # noqa: E731
        return {"vars": dict(DARK), "colorScheme": "dark", "classNow": cls_now,
                "frames": mk(frames), "classLog": mk(log), "islands": [],
                "hasShell": True, "contentLen": 200}

    pure = (
        ("Q12", "observer 拍到摘除、**逐帧没拍到** ⇒ D0 **仍必须报红**（只看逐帧会漏）",
         _mk("dark", ["dark"], ["dark", "", "dark"]), True),
        ("Q13", "observer 与逐帧都是 `['dark']` ⇒ D0 **必须干净**（对照组）",
         _mk("dark", ["dark"], ["dark"]), False),
    )
    for key, desc, meas, expect_red in pure:
        problems = judge(meas, "dark", LIGHT, DARK)
        got_red = bool(problems)
        ok = got_red == expect_red
        print(f"  [{key}] {desc}")
        print(f"        期望{'报红' if expect_red else '干净':4s} 实测{'报红' if got_red else '干净':4s} "
              f"{'✓' if ok else '✗'}")
        if problems:
            for p in problems:
                print(f"          · {p}")
        if not ok:
            bad.append(f"[{key}] 期望{'报红' if expect_red else '干净'} 实测{'报红' if got_red else '干净'}")

    for key, desc, args, expect in (
        ("Q8", "收口判定：**阴性对照被破坏 ⇒ exit 2**（测量不可信，不是产品缺陷）",
         (8, [], ["ink-950 变了"]), 2),
        ("Q9", "收口判定：**0 组测量不得算通过**", (0, [], []), 2),
        ("Q10", "收口判定：有缺陷 ⇒ exit 1", (8, ["bad"], []), 1),
        ("Q11", "收口判定：全通过 ⇒ exit 0", (8, [], []), 0),
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
    print("\n自检通过：语义自检（Q0a/Q0b）、检出能力（Q2/Q3/Q6）与对照组干净（Q1/Q4/Q5/Q7）均符合预期。")
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


def premise_problems(m: dict | None) -> list[str]:
    if not m:
        return ["未取到测量结果"]
    if not m.get("hasShell"):
        return ["页面上没有 aside / nav[主导航] / header"]
    if (m.get("contentLen") or 0) < 40:
        return [f"正文仅 {m.get('contentLen')} 字符 ⇒ 疑似未渲染完"]
    if not norm(m["vars"].get("surface-page", "")):
        return ["读不到 `--surface-page` ⇒ 大概率停在 /login"]
    return []


async def run(shot: bool, only: str | None) -> int:
    total = 0
    all_bad: list[str] = []
    control_broken: list[str] = []
    skipped: list[str] = []
    env_fail: list[str] = []
    mjs = measure_js()

    # 🚨 先自检**解析结果**：两档装反会让所有比较一致地错（实测报出 131 条假红）。
    sane = tokens_sanity(LIGHT, DARK)
    if sane:
        print("[env] 解析 `tokens.css` 的结果**语义自检失败** ⇒ 测量不可信（exit 2）：")
        for s in sane:
            print(f"  · {s}")
        return 2

    print(f"期望值来源：`{TOKENS_CSS.relative_to(HERE.parents[0])}` 的 `:root` 与 `.dark` 块")
    print(f"  D1 翻转变量：{', '.join('--' + v for v in FLIP_VARS)}")
    print(f"  D1' 恒定对照：{', '.join('--' + v for v in CONST_VARS)}")
    print(f"  D3 阈值：面积 ≥ {MIN_ISLAND_AREA}px² 且亮度 > {LIGHT_LUM}（工程值）\n")

    async with Browser(headless=True, width=1280, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        await b.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=INJECT_JS)
        for app, cfg in APPS.items():
            if only and app != only:
                continue
            base = f"http://localhost:{cfg['port']}"
            print(f"══ {app}  {base}")
            # Cookie 不区分端口 ⇒ 换端前必须清（localStorage **区分**端口，不用清）
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                print("  [env] 登录失败 ⇒ 本端**全部不判**")
                env_fail.append(f"{app}: 登录失败")
                continue
            for path in cfg["pages"]:
                for mode in ("light", "dark"):
                    await b.cdp.evaluate(f"localStorage.setItem('nlaw-theme', {mode!r})")
                    await b.goto(f"{base}{path}", wait=1.6)
                    await b.settle(extra=1.2)
                    m = await b.cdp.evaluate(mjs)
                    label = f"{path} @{mode}"
                    pre = premise_problems(m)
                    if pre:
                        print(f"  {label:22s} 前提不成立：{pre[0]} ⇒ **不判**")
                        skipped.append(f"{app}{path}@{mode}: {pre[0]}")
                        continue
                    total += 1
                    problems = judge(m, mode, LIGHT, DARK)
                    ctrl = [p for p in problems if "对照被破坏" in p]
                    real = [p for p in problems if "对照被破坏" not in p]
                    if ctrl:
                        control_broken.extend(f"{app}{path}@{mode}: {p}" for p in ctrl)
                    if real:
                        print(f"  {label:22s} ✗")
                        for p in real:
                            print(f"      {p}")
                        all_bad.extend(f"{app}{path}@{mode} {p}" for p in real)
                    else:
                        frames = [f.get("c") for f in (m.get("frames") or [])]
                        src = "逐帧" if frames else "observer"
                        print(f"  {label:22s} ✓   class={m.get('classNow')!r} "
                              f"{src}序列={frames[:4] or [f.get('c') for f in (m.get('classLog') or [])][:4]} "
                              f"孤岛={len(m.get('islands') or [])}")
                    if shot:
                        await b.screenshot(
                            HERE / f"shot_dark_{app}_{path.strip('/') or 'home'}_{mode}.png", full=False)

    print("\n" + "=" * 74)
    print(f"测量 {total} 组；不合格 {len(all_bad)} 组。")
    if skipped:
        print(f"跳过 {len(skipped)} 组（前提不成立，不算通过也不算失败）：")
        for s in skipped:
            print(f"  · {s}")
    if env_fail:
        print(f"环境失败 {len(env_fail)} 项：")
        for s in env_fail:
            print(f"  · {s}")

    code = summary_exit(total, all_bad, control_broken)
    if control_broken:
        print("\n[env] **阴性对照被破坏** ⇒ 测量本身不可信，此时报红报绿都没有意义（exit 2）：")
        for s in control_broken:
            print(f"  · {s}")
        return 2
    if code == 2:
        print("\n[env] **一组都没测到** ⇒ 这是「我没测成」，不是「产品没问题」（exit 2）")
        return 2
    if code == 1:
        print("\n缺陷：")
        for p in all_bad:
            print(f"  ✗ {p}")
        return 1
    if skipped:
        print(f"\n⚠️ 通过，但**有 {len(skipped)} 组被跳过** ⇒ 覆盖率不满。")
    print("深色模式：class 机制、变量翻转、color-scheme、无浅色孤岛 —— 限已测到的组。")
    print("⚠️ 已知边界：**不含对比度判据**（D4，WCAG 1.4.3 —— **已由 `verify_contrast.py` 接管**）"
          "与「语义色用错」类问题。")
    return 0


async def dump(only: str | None) -> int:
    """诊断模式：打印原始测量值，**不下判断**。"""
    mjs = measure_js()
    async with Browser(headless=True, width=1280, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        await b.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=INJECT_JS)
        for app, cfg in APPS.items():
            if only and app != only:
                continue
            base = f"http://localhost:{cfg['port']}"
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                print(f"══ {app}: 登录失败")
                continue
            for path in cfg["pages"]:
                for mode in ("light", "dark"):
                    await b.cdp.evaluate(f"localStorage.setItem('nlaw-theme', {mode!r})")
                    await b.goto(f"{base}{path}", wait=1.6)
                    await b.settle(extra=1.2)
                    m = await b.cdp.evaluate(mjs)
                    if not m:
                        print(f"══ {app}{path}@{mode}: —")
                        continue
                    frames = [f.get("c") for f in (m.get("frames") or [])]
                    log = [f.get("c") for f in (m.get("classLog") or [])]
                    print(f"══ {app}{path} @{mode}  class={m.get('classNow')!r} "
                          f"colorScheme={m.get('colorScheme')!r}")
                    print(f"     逐帧({len(frames)})={frames[:8]}")
                    print(f"     observer({len(log)})={log[:8]}  孤岛={len(m.get('islands') or [])}")
                    print(f"     变量={ {k: v for k, v in m['vars'].items()} }")
                    for it in (m.get("islands") or [])[:5]:
                        print(f"       · <{it['tag']}> {it['w']}×{it['h']} bg={it['bg']} "
                              f"lum={it['lum']} cls=`{it['cls']}`")
    return 0


async def why(app: str) -> int:
    """抓根因：**在摘除发生的当下取调用栈**，不靠读代码推断。"""
    async with Browser(headless=True, width=1280, height=900) as b:
        await b.apply_device(safe_area=None, mobile=False)
        await b.cdp.send("Page.addScriptToEvaluateOnNewDocument", source=WHY_JS)
        cfg = APPS[app]
        base = f"http://localhost:{cfg['port']}"
        if not await login(b, base, cfg["user"]):
            print(f"[env] {app} 登录失败")
            return 2
        for path in cfg["pages"][:1]:
            await b.cdp.evaluate("localStorage.setItem('nlaw-theme', 'dark')")
            await b.goto(f"{base}{path}", wait=1.6)
            await b.settle(extra=1.5)
            ops = await b.cdp.evaluate("window.__nlawWhy || []")
            print(f"══ {app}{path} —— 与 `dark` 相关的 class 操作（按发生顺序）")
            if not ops:
                print("  （无记录）")
            for i, o in enumerate(ops):
                print(f"  [{i}] {o.get('op'):16s} detail={o.get('detail'):6s} "
                      f"调用后含dark={o.get('now')}")
                print(f"      {o.get('stack')}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="深色模式渲染级门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑合成页面自检")
    ap.add_argument("--shot", action="store_true", help="每档出一张视口截图")
    ap.add_argument("--dump", action="store_true", help="诊断模式：打印原始测量值")
    ap.add_argument("--why", action="store_true", help="抓根因：打印摘除 dark 的调用栈")
    ap.add_argument("--app", default=None, help="只测一端（web/lawyer/admin/im）")
    args = ap.parse_args()
    try:
        if args.self_test:
            return asyncio.run(run_self_test())
        if args.why:
            return asyncio.run(why(args.app or "im"))
        if args.dump:
            return asyncio.run(dump(args.app))
        return asyncio.run(run(args.shot, args.app))
    except Exception as e:
        print(f"[env] {type(e).__name__}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
