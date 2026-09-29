#!/usr/bin/env python
"""`prefers-reduced-motion` 渲染级门禁 —— 规范 §4.4 的硬要求，此前**没有任何判据**。

用法：
    python evidence/verify_reduced_motion.py --self-test
    python evidence/verify_reduced_motion.py
    python evidence/verify_reduced_motion.py --dump          # 打原始测量值，不下判断
    python evidence/verify_reduced_motion.py --app im

退出码：`0` 通过 / `1` 产品缺陷 / `2` 环境或测量问题。

═══════════════════════════════════════════════════════════════════════════════
为什么需要这个门禁（三条理由，缺一条都不足以立它）
═══════════════════════════════════════════════════════════════════════════════

**① 规范原文是硬要求。** `§4.4「场景 · 时长 · 缓动」`（§4.4 动效）最后一行：
    「必须响应 `prefers-reduced-motion: reduce`。」
    `§9「可访问性：键盘可达、焦点环统一、对比度复核」` 的可访问性清单里也点了它。**「必须」= 有判据义务。**

**② 它是「浏览器解析出来的东西」，静态校验覆盖不到。**
    媒体特性由**浏览器**求值。`grep prefers-reduced-motion` 只能告诉你「声明写了」，
    不能告诉你「运行时动画真的停了」——这与深色模式的 `.dark` 类同族
    （见 `evidence/README.md` 坑 26 与坑 13「构建 exit 0 证明不了产物能用」）。
    实测：全仓 `prefers-reduced-motion` 命中 19 处，其中 **18 处是构建产物副本**，
    源码里**只有 `tokens.css:360` 一处**。

**③ 🚨 更关键：现有探针**结构上不可能**验证它——测量手段覆盖了被测对象。**
    `cdp.py:284 freeze_animations()` 做两件事：
      a) `Emulation.setEmulatedMedia(features=[prefers-reduced-motion: reduce])` —— 把媒体特性**强制**成 reduce；
      b) 注入 `FREEZE_JS`，无条件 `animation:none!important;transition:none!important`。
    ⇒ 凡调用它的探针（视觉基线 / 安全区 / 触摸目标 / 深色门禁…）**测到的是它自己造的假象**。
    它这么做**是对的**（截图要确定性），但代价是：**减弱动效从来没被验证过**，
    而且**不可能**被验证——除非另起一个**不调用它**的门禁。

    ⇒ 本门禁**不调用 `freeze_animations()`**，并把这条做成**前提锁**：
      若页面上出现 `style[data-cdp-freeze]`（`FREEZE_JS` 的指纹）⇒ **exit 2**。
      「我没测成」与「产品坏了」必须分开报。

═══════════════════════════════════════════════════════════════════════════════
判据
═══════════════════════════════════════════════════════════════════════════════

  R2   令牌机制**正面断言**：reduce 档下 `--dur-fast/base/slow/pulse` 与 `--pulse-iter`
       的**运行时值**必须等于 `tokens.css` 的 `@media (prefers-reduced-motion: reduce)`
       块**声明**的值。期望值**从 CSS 解析得到，不硬编码**。
       （机制不生效时，R1 的「归因」就无从谈起——先证明机制是好的。）

  R1a  reduce 档下**不得有可见元素带非零 `transition-duration`**。
       出处：规范 §4.4「必须响应」+ **实现自述**（`tokens.css:358` 注释：
       「令牌归零即可让**全部**动画与过渡停下，无需 `!important`」）。
       过渡是纯装饰动效（颜色/位移），无「承载语义」的辩解空间。

  R1b  reduce 档下不得有可见元素在跑 **Tailwind 内置 `pulse`** 动画（`2s … infinite`）。
       它是**装饰性骨架屏**，且项目内**已有响应式对照实现** `animate-pulse-soft`
       （9 处，走 `var(--dur-pulse)` / `var(--pulse-iter)`）⇒
       **同族元素两种实现、一种响应一种不响应**，属内部不一致，可判定。

  R1d  reduce 档下不得有**项目自定义动画**仍在跑
       （`pulse-soft` / `fade-in` / `slide-in-right` / `sheet-up` / `drawer-in` / `panel-in-*`）。
       这些**本该**被 `--dur-*` 停下 ⇒ 若仍在跑，说明**令牌机制失效**（与 R2 互为印证）。

  R1c  reduce 档下不得有 **Tailwind 内置 `spin`**（`1s linear infinite`，时长硬编码、
       **不走令牌**）仍在跑。**判红**（2026-09-26 起）。
       出处：`design-spec.md` §4.4 补充段 —— **#75 拍板「归零静止」**。
       ⚠️ 它**不是**「不许有加载指示」：指示器必须保留（`<Spinner>` 在 reduce 下渲染
       **静止的弧**），禁的是**内置旋转类**这个实现（未经令牌 ⇒ 归零机制够不着它）。
       与 `verify_motion_tokens.py` 的 **A4** 互为**独立**判据，不是重复：
       A4 是**源码级**（`frontend/**` 文本，**连注释一起扫**），
       R1c 是**运行时级**（真页面的计算值）⇒ 第三方 CSS、未被 A4 扫描范围覆盖的文件，
       只有 R1c 拦得住；反过来，只在注释里出现的类名只有 A4 拦得住。
       （此前是「单列、不计红、留待拍板」——那个「待拍板」就是 #75，已拍板 ⇒ 升为判红。）

前提锁（不成立时**不判**，不算通过也不算失败）：
  P1  页面上不得有 `style[data-cdp-freeze]`（测量被 `freeze_animations()` 污染）⇒ **exit 2**。
  P2  `matchMedia('(prefers-reduced-motion: reduce)')` 必须与**本次请求**的档一致。
  P3  **空真防护**：`no-preference` 档下该页必须测到 ≥1 个「有动画或有过渡」的可见元素；
      否则该页**根本测不出东西** ⇒ 标为「空真，不判」。
      （没有 P3，「reduce 档 0 命中」会被读成通过——那正是本项目最常复发的假绿。）
  P4  全局：至少有一页在 `no-preference` 档命中 > 0；否则 **exit 2**（测量看不见任何东西）。

⚠️ 已知边界（**没测的东西不假装测了**）：
  · 不含「动效是否**令人不适**」的主观判定（WCAG 2.3.3 的「interaction-triggered motion」）。
  · 不含 JS 驱动的动画（`element.animate()` / rAF）——本门禁只看 CSS 计算值。
  · 只看**可见**元素（`getBoundingClientRect()` 为 0 的视为不可见，不判）。
  · 视口只取 390 / 1280 两档。
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
# 演示账号表在 `app.seed.data` 里 —— 账号**不硬编码**，从后端同一份源头读。
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

TOKENS_CSS = HERE.parents[0] / "frontend" / "packages" / "ui" / "src" / "tokens.css"

# R2：reduce 块里必须归零的令牌（期望值从 CSS 解析，这里只列**名字**）
# ⚠️ 新增令牌**必须登记在这里**，否则它的「归零」在**运行时**就无人断言 ——
#    `verify_motion_tokens.py` 的 A1 只做**静态**比对（§4.4 表 ↔ 令牌注释）。
#    2026-09-26（#75）补 `--dur-spin` / `--spin-iter`。
REDUCE_VAR_NAMES: tuple[str, ...] = (
    "--dur-fast", "--dur-base", "--dur-slow", "--dur-pulse", "--pulse-iter",
    "--dur-spin", "--spin-iter",
)

# 项目自定义动画（走 `var(--dur-*)` / `var(--pulse-iter)` / `var(--spin-iter)`）——仍在跑即机制失效（R1d）
PROJECT_ANIMS: frozenset[str] = frozenset({
    "pulse-soft", "fade-in", "slide-in-right", "sheet-up",
    "drawer-in", "panel-in-right", "panel-in-left",
    # 加载指示（#75 收敛后全仓**唯一**的旋转动画）。**必须登记**：漏了它，
    # 令牌失效时只会落到下面那条兜底文案「仍有动画 `spin-soft`」，
    # 读的人看不出「是令牌机制坏了」——判据还在，但归因信息丢了。
    "spin-soft",
})
# Tailwind **内置**动画（时长硬编码，不走令牌）
BUILTIN_DECOR = "pulse"   # 2s infinite —— 骨架屏，判红（R1b）
BUILTIN_SPIN = "spin"     # 1s infinite —— 内置旋转类，判红（R1c，2026-09-26 由「只报不判」升级）

# 两档视口（390 = 规范设计基准；1280 = 桌面）
VIEWPORTS: tuple[tuple[int, int, bool, str], ...] = (
    (390, 844, True, "390"),
    (1280, 900, False, "1280"),
)

APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin", "pages": ("/", "/qa")},
    "lawyer": {"port": 3001, "user": "lawyer_wang", "pages": ("/", "/cases")},
    "admin": {"port": 3002, "user": "admin", "pages": ("/", "/audit")},
    "im": {"port": 3003, "user": "client", "pages": ("/", "/chat")},
}

MIN_CONTENT = 40   # 正文最少字符数（低于此视为未渲染完）
# 加载态采样：「外壳有没有挂上」的最低 DOM 节点数。
# 实测鉴权 spinner 那一屏只有 **38** 个节点（先前定 50 ⇒ 8 组全部误判「外壳都没挂上」——
# **是判据错，不是产品错**）。这里只作粗筛，**真正的有效性控制是 `is_empty_truth` 空真检查**。
MIN_NODES_LOADING = 20
# 加载态采样时**放行**的请求（其余 `/api/*` 一律挂起）。
# 🚨 不能全挂 `*/api/*`：**鉴权引导也是 `/api/`**，全挂会先卡在鉴权 spinner 上，
# 到不了「数据取数期间的骨架屏」（实测就是这么被挡住的）。
LOADING_ALLOW: tuple[str, ...] = ("/auth/",)


# ---------------------------------------------------------------------------
# 解析 tokens.css 的 reduce 块 —— **期望值从这里来，不硬编码**
# ---------------------------------------------------------------------------
def _brace_block(css: str, start: int) -> str:
    """从 `css[start] == '{'` 起，返回配对花括号**内部**的内容。"""
    if css[start] != "{":
        raise ValueError(f"位置 {start} 不是 `{{`")
    depth, i = 0, start
    while i < len(css):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[start + 1:i]
        i += 1
    raise ValueError("花括号不配平")


def load_reduce_tokens(path: pathlib.Path = TOKENS_CSS) -> dict[str, str]:
    """返回 `@media (prefers-reduced-motion: reduce) { :root { … } }` 里声明的变量表。

    空字典 = 解析不到（由 `reduce_tokens_sanity` 报出）。
    """
    css = path.read_text(encoding="utf-8")
    m = re.search(r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)\s*\{", css)
    if not m:
        return {}
    body = _brace_block(css, m.end() - 1)
    inner = re.search(r":root\s*\{", body)
    if not inner:
        return {}
    root = _brace_block(body, inner.end() - 1)
    return {k: v.strip() for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", root)}


def reduce_tokens_sanity(decl: dict[str, str]) -> list[str]:
    """**解析结果的语义自检**（与命名无关）。

    深色门禁踩过同族坑：解包写反 ⇒ 判据**一致地错**，而自检因为用同一套错名字而全绿
    （131 条假红）。这里换一条**不看名字只看语义**的不变量：
    「一个叫『降低动效』的块，**必须**把某些时长归零」，否则它就不是它声称的东西。
    """
    if not decl:
        return [f"在 `{TOKENS_CSS.name}` 里解析不到 `@media (prefers-reduced-motion: reduce)` 块"]
    zeroed = [k for k, v in decl.items()
              if k.startswith("--dur-") and re.fullmatch(r"0(\.0+)?(ms|s)", v)]
    if not zeroed:
        return [f"该块里没有任何 `--dur-*` 归零（实测解析到 {sorted(decl)}）⇒ 与「降低动效」语义不符"]
    return []


# ---------------------------------------------------------------------------
# 测量 JS —— 只看**可见**元素的计算值
# ---------------------------------------------------------------------------
MEASURE_JS = r"""(() => {
  const out = {
    vw: innerWidth,
    rmReduce: matchMedia('(prefers-reduced-motion: reduce)').matches,
    // 🚨 `FREEZE_JS` 的指纹：出现了就说明本次测量被 `freeze_animations()` 污染
    frozen: !!document.querySelector('style[data-cdp-freeze]'),
    nodes: document.querySelectorAll('*').length,
    durVars: {},
    items: [],
    contentLen: document.body ? document.body.innerText.length : 0,
  };
  const rs = getComputedStyle(document.documentElement);
  for (const v of __VARS__) out.durVars[v] = (rs.getPropertyValue(v) || '').trim();

  const seen = new Set();
  for (const el of document.querySelectorAll('*')) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;   // 不可见 ⇒ 不判（P：可见性前提）
    const s = getComputedStyle(el);
    const anim = (s.animationName || 'none');
    const animDur = (s.animationDuration || '0s').split(',')[0].trim();
    const iter = (s.animationIterationCount || '1').split(',')[0].trim();
    const trProp = (s.transitionProperty || 'none');
    const trDur = (s.transitionDuration || '0s');
    const hasAnim = anim !== 'none' && parseFloat(animDur) > 0;
    const hasTr = trProp !== 'none' && trDur.split(',').some(x => parseFloat(x) > 0);
    if (!hasAnim && !hasTr) continue;
    const cls = (el.getAttribute('class') || '').slice(0, 160);
    const key = [el.tagName, cls, anim, animDur, iter, trProp, trDur].join('|');
    if (seen.has(key)) continue;
    seen.add(key);
    out.items.push({
      tag: el.tagName.toLowerCase(), cls: cls,
      anim: anim, animDur: animDur, iter: iter,
      trProp: trProp, trDur: trDur,
      w: Math.round(r.width), h: Math.round(r.height),
      hasAnim: hasAnim, hasTr: hasTr,
    });
  }
  return out;
})()"""


def measure_js() -> str:
    return MEASURE_JS.replace("__VARS__", repr(list(REDUCE_VAR_NAMES)).replace("'", '"'))


# ---------------------------------------------------------------------------
# 判据（纯函数，可被自检直接喂数据）
# ---------------------------------------------------------------------------
def _short(cls: str, n: int = 70) -> str:
    return cls if len(cls) <= n else cls[:n] + "…"


def judge(m: dict, want_media: str, decl: dict[str, str]) -> list[str]:
    """返回判红列表 `problems`。

    ⚠️ **本探针已无「只报不判」通道**（2026-09-26）：原 R1c 是唯一的 `pending` 项，
    #75 拍板「归零静止」后升为判红 ⇒ `pending` 通道整体移除。
    留一个**永远为空**的「只报不判」通道，就是留一个零判据的口子
    （它会让人误以为「这里报出来的东西有人管」）。
    """
    problems: list[str] = []
    if want_media != "reduce":
        return problems

    # —— R2：令牌机制**正面断言**（期望值来自 CSS 声明）——
    for name in REDUCE_VAR_NAMES:
        want = (decl.get(name) or "").strip()
        if not want:
            continue
        got = (m.get("durVars") or {}).get(name, "").strip()
        if not got:
            # 读不到 ⇒ 是**测量**问题（样式表没加载），不是产品问题。
            # 前提锁 `premise_problems` 已经拦下这一档；这里只做防御性跳过。
            continue
        if got != want:
            problems.append(
                f"[R2] reduce 档下 `{name}` 运行时 = `{got}`，`tokens.css` 声明 `{want}` "
                f"⇒ **令牌机制没生效**（先修这里，否则 R1 无法归因）")

    # —— R1：仍在动的可见元素 ——
    for it in m.get("items") or []:
        where = f"<{it['tag']}> {it['w']}×{it['h']} class=`{_short(it['cls'])}`"
        if it.get("hasAnim"):
            nm = (it.get("anim") or "").strip()
            if nm == BUILTIN_SPIN:
                problems.append(
                    f"[R1c] reduce 档下 **Tailwind 内置 `spin`** 仍在跑"
                    f"（`{it['animDur']} ×{it['iter']}`，时长硬编码、**不走令牌**）：{where}。"
                    f"加载指示请改用 `<Spinner>`（其 `animate-spin-soft` 经 "
                    f"`var(--dur-spin)` / `var(--spin-iter)` ⇒ reduce 下**停成静止的弧**）")
            elif nm == BUILTIN_DECOR:
                problems.append(
                    f"[R1b] reduce 档下 **Tailwind 内置 `pulse` 骨架屏**仍在跑"
                    f"（`{it['animDur']} ×{it['iter']}`，时长硬编码、不走令牌）：{where}。"
                    f"项目内已有响应式对照实现 `animate-pulse-soft`")
            elif nm in PROJECT_ANIMS:
                problems.append(
                    f"[R1d] reduce 档下**项目自定义动画** `{nm}` 仍在跑"
                    f"（`{it['animDur']} ×{it['iter']}`）⇒ 它本该被 `--dur-*` 停下 ⇒ 令牌机制失效：{where}")
            else:
                problems.append(
                    f"[R1d] reduce 档下仍有动画 `{nm}`（`{it['animDur']} ×{it['iter']}`）：{where}")
        if it.get("hasTr"):
            problems.append(
                f"[R1a] reduce 档下仍有非零过渡 `{it['trProp']}` / `{it['trDur']}`：{where}。"
                f"`tokens.css` 自述「令牌归零即可让**全部**动画与过渡停下」——这条不成立")
    return problems


def premise_problems(m: dict | None, want_media: str, vw: int,
                     phase: str = "steady") -> list[str]:
    """前提锁。**不成立时不判**（既不算通过也不算失败）。

    `phase="loading"` 时**不放宽**「测量被污染」与「媒体仿真」两条硬前提，
    只把「正文已渲染」换成「外壳已挂载」——加载态本来就没有正文。
    """
    if not m:
        return ["未取到测量结果"]
    if m.get("frozen"):
        return ["页面上出现 `style[data-cdp-freeze]` ⇒ 本次测量被 `freeze_animations()` 污染，"
                "被测对象被测量手段覆盖"]
    if m.get("vw") != vw:
        return [f"视口 {m.get('vw')} ≠ {vw}（媒体仿真/视口未生效）"]
    want_reduce = want_media == "reduce"
    if bool(m.get("rmReduce")) != want_reduce:
        return [f"`matchMedia('(prefers-reduced-motion: reduce)')` = {m.get('rmReduce')}，"
                f"本次请求的是 `{want_media}` ⇒ 媒体仿真没生效"]
    # 🚨 这一条是**实测补上的**：dev 服务在采样途中挂掉时，页面会落到错误页
    # （实测类名是 `blue-button` / `secondary-button`，不是本项目设计系统），
    # 此时 `tokens.css` 根本没加载 ⇒ `--dur-fast` 读到**空串**。
    # 若不当前提，R2 会报「令牌机制没生效」——**那是把我的测量失败说成产品缺陷**。
    # 实测一次跑出 **20 条**这类假红。
    if not ((m.get("durVars") or {}).get("--dur-fast") or "").strip():
        return ["读不到 `--dur-fast` ⇒ `tokens.css` 没加载（页面不是应用，或停在错误页）"]
    if phase == "loading":
        if (m.get("nodes") or 0) < MIN_NODES_LOADING:
            return [f"DOM 仅 {m.get('nodes')} 个节点（< {MIN_NODES_LOADING}）⇒ 外壳都没挂上"]
        return []
    if (m.get("contentLen") or 0) < MIN_CONTENT:
        return [f"正文仅 {m.get('contentLen')} 字符（< {MIN_CONTENT}）⇒ 疑似未渲染完"]
    return []


def is_empty_truth(m: dict) -> bool:
    """P3 空真判定：这一档**根本没有可测对象**（0 个有动画/过渡的可见元素）。"""
    return not (m.get("items") or [])


def summary_exit(total: int, all_bad: list[str], control_broken: list[str]) -> int:
    """收口判定（抽成纯函数，便于自检锁住）。

    🚨 `total == 0` 必须返回 **2**（「我没测成」≠「产品健康」）。
    🚨 前提锁被破坏 ⇒ **2**：测量本身不可信，此时报红报绿都没有意义。
    """
    if control_broken:
        return 2
    if total == 0:
        return 2
    if all_bad:
        return 1
    return 0


# ---------------------------------------------------------------------------
# 自检：合成页面（**不依赖产品**）。浏览器臂 + 纯函数臂分开报。
# ---------------------------------------------------------------------------
REDUCE_DECL = load_reduce_tokens()
# 夹具用的「已归零」令牌表：模拟 `tokens.css` reduce 块生效后的样子
# ⚠️ 必须与 `REDUCE_VAR_NAMES` **同步**：漏一项不会报错，只会让 R2 对那一项**静默跳过**
#    （`got` 为空 ⇒ `continue`）⇒ 又是「零判据且不报错」。2026-09-26 补 spin 两项。
ZEROED = {"--dur-fast": "0ms", "--dur-base": "0ms", "--dur-slow": "0ms",
          "--dur-pulse": "0ms", "--pulse-iter": "1",
          "--dur-spin": "0ms", "--spin-iter": "1"}
LIVE = {"--dur-fast": "120ms", "--dur-base": "180ms", "--dur-slow": "240ms",
        "--dur-pulse": "1.6s", "--pulse-iter": "infinite",
        "--dur-spin": "1s", "--spin-iter": "infinite"}

_FILLER = "合成夹具的填充正文，用于通过「已渲染」前提锁，不含产品语义。"


def _page(body: str, css: str = "", root: dict[str, str] | None = None,
          head_extra: str = "") -> str:
    rv = "".join(f"{k}:{v};" for k, v in (root or {}).items())
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>:root{{{rv}}}html,body{{margin:0}}{css}</style>{head_extra}"
            f"</head><body>{body}</body></html>")


def _box(style: str, text: str = _FILLER) -> str:
    return f"<div style='width:300px;height:120px;{style}'>{text}</div>"


_FREEZE_FINGERPRINT = ("<style data-cdp-freeze='1'>"
                       "*,*::before,*::after{animation:none!important;transition:none!important}"
                       "</style>")

# key, 描述, body, css, root, head_extra, 请求档, 期望报红
SELFTEST: tuple[tuple[str, str, str, str, dict, str, str, bool], ...] = (
    ("Q1", "no-preference 档下 150ms 过渡 ⇒ **必须干净**（对照：这一档本来就该有过渡）",
     _box("transition:color 150ms"), "", LIVE, "", "no-preference", False),
    ("Q2", "reduce 档下 150ms 过渡 ⇒ R1a **必须报红**（检出能力）",
     _box("transition:color 150ms"), "", ZEROED, "", "reduce", True),
    ("Q3", "reduce 档下过渡为 0ms ⇒ **必须干净**（对照）",
     _box("transition:color 0ms"), "", ZEROED, "", "reduce", False),
    ("Q4", "reduce 档下 Tailwind 内置 `pulse` 骨架屏仍在跑 ⇒ R1b **必须报红**",
     _box("animation:pulse 2s cubic-bezier(.4,0,.6,1) infinite"),
     "@keyframes pulse{50%{opacity:.5}}", ZEROED, "", "reduce", True),
    ("Q5", "reduce 档下**内置 `spin`** 仍在跑 ⇒ R1c **必须报红**（#75 拍板后由「只报不判」升级）",
     _box("animation:spin 1s linear infinite"),
     "@keyframes spin{to{transform:rotate(360deg)}}", ZEROED, "", "reduce", True),
    ("Q5b", "reduce 档下 `spin-soft` 已被令牌停下（0ms ×1）⇒ **必须干净**"
            "（阴性对照 · 防自伤：#75 新造的合法动画不得被自己判红）",
     _box("animation:spin-soft 0ms linear 1"),
     "@keyframes spin-soft{to{transform:rotate(360deg)}}", ZEROED, "", "reduce", False),
    ("Q5c", "reduce 档下 `spin-soft` 仍在跑（令牌没生效）⇒ R1d **必须报红**"
            "（证明新动画已登记进 `PROJECT_ANIMS`，归因文案不是兜底那条）",
     _box("animation:spin-soft 1s linear infinite"),
     "@keyframes spin-soft{to{transform:rotate(360deg)}}", ZEROED, "", "reduce", True),
    ("Q6", "reduce 档下 `pulse-soft` 已被令牌停下（0ms ×1）⇒ **必须干净**（对照）",
     _box("animation:pulse-soft 0ms ease-in-out 1"),
     "@keyframes pulse-soft{50%{opacity:.55}}", ZEROED, "", "reduce", False),
    ("Q7", "reduce 档下 `pulse-soft` 仍在跑（令牌没生效）⇒ R1d **必须报红**",
     _box("animation:pulse-soft 1.6s ease-in-out infinite"),
     "@keyframes pulse-soft{50%{opacity:.55}}", ZEROED, "", "reduce", True),
    ("Q8", "reduce 档下 `display:none` 元素带 150ms 过渡 ⇒ **必须干净**（可见性前提）",
     "<div style='display:none;transition:color 150ms'>" + _FILLER + "</div>"
     + _box("background:#eee"),
     "", ZEROED, "", "reduce", False),
    ("Q9", "页面出现 `style[data-cdp-freeze]` ⇒ **前提锁必须报出**（测量被污染）",
     _box("transition:color 150ms"), "", ZEROED, _FREEZE_FINGERPRINT, "reduce", False),
    ("Q10", "reduce 档下运行时 `--dur-fast` 不是声明值 ⇒ R2 **必须报红**（机制失效）",
     _box("background:#eee"), "", {**ZEROED, "--dur-fast": "120ms"}, "", "reduce", True),
    ("Q10b", "reduce 档下运行时 `--dur-spin` 不是声明值 ⇒ R2 **必须报红**"
             "（证明 #75 新令牌**真的进了 R2 覆盖**，不是只写在 `REDUCE_VAR_NAMES` 里好看）",
     _box("background:#eee"), "", {**ZEROED, "--dur-spin": "1s"}, "", "reduce", True),
)


async def run_self_test() -> int:
    print("自检（合成页面，与产品无关）—— 各臂分开报：")
    bad: list[str] = []

    # —— Q0：解析结果的语义自检（防「解析错位」）——
    for key, desc, args, expect_bad in (
        ("Q0a", "真实 `tokens.css` 的 reduce 块 ⇒ `reduce_tokens_sanity` **必须干净**",
         (REDUCE_DECL,), False),
        ("Q0b", "解析结果为空 ⇒ `reduce_tokens_sanity` **必须报出**（锁住「解析失败被当成通过」）",
         ({},), True),
    ):
        probs = reduce_tokens_sanity(*args)
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
        for key, desc, body, css, root, head_extra, media, expect_red in SELFTEST:
            url = "data:text/html;charset=utf-8," + urllib.parse.quote(
                _page(body, css, root, head_extra))
            await b.goto(url, wait=0.25)
            await b.settle(extra=0.5)
            # ⚠️ 必须**在 goto 之后**设媒体特性（本门禁不注入任何冻结脚本）
            await b.cdp.send(
                "Emulation.setEmulatedMedia",
                features=[{"name": "prefers-reduced-motion", "value": media}],
            )
            await asyncio.sleep(0.35)
            m = await b.cdp.evaluate(measure_js())
            print(f"  [{key}] {desc}")
            if not m:
                print("        前提不成立：取不到测量结果 ⇒ **夹具坏了**")
                bad.append(f"[{key}] 取不到测量结果")
                continue
            if key == "Q9":
                pre = premise_problems(m, media, 1280)
                got = bool(pre)
                ok = got is True
                print(f"        前提锁期望**报出** 实测{'报出' if got else '干净'} {'✓' if ok else '✗'}")
                if pre:
                    print(f"          · {pre[0][:100]}")
                if not ok:
                    bad.append("[Q9] 前提锁没报出 `data-cdp-freeze`")
                continue
            problems = judge(m, media, REDUCE_DECL)
            got_red = bool(problems)
            ok = got_red == expect_red
            print(f"        items={len(m.get('items') or [])}  期望{'报红' if expect_red else '干净':4s} "
                  f"实测{'报红' if got_red else '干净':4s} {'✓' if ok else '✗'}")
            for p in problems:
                print(f"          · {p[:130]}")
            if not ok:
                bad.append(f"[{key}] 期望{'报红' if expect_red else '干净'} 实测{'报红' if got_red else '干净'}")

    # —— 纯函数臂：前提锁与空真 ——
    def _mk(**kw) -> dict:
        base = {"vw": 1280, "rmReduce": True, "frozen": False, "durVars": dict(ZEROED),
                "nodes": 300,
                "items": [{"tag": "div", "cls": "x", "anim": "none", "animDur": "0s",
                           "iter": "1", "trProp": "color", "trDur": "150ms",
                           "w": 10, "h": 10, "hasAnim": False, "hasTr": True}],
                "contentLen": 200}
        base.update(kw)
        return base

    pure = (
        ("Q11", "**空真**：`no-preference` 档 0 个可测元素 ⇒ `is_empty_truth` **必须为真**"
                "（不得读成通过）",
         lambda: is_empty_truth(_mk(items=[])), True),
        ("Q12", "有元素 ⇒ `is_empty_truth` **必须为假**（对照组）",
         lambda: is_empty_truth(_mk()), False),
        ("Q13", "媒体档不符 ⇒ 前提锁**必须报出**",
         lambda: bool(premise_problems(_mk(rmReduce=False), "reduce", 1280)), True),
        ("Q14", "视口不符 ⇒ 前提锁**必须报出**",
         lambda: bool(premise_problems(_mk(vw=390), "reduce", 1280)), True),
        ("Q19", "加载态：DOM 节点不足（外壳都没挂上）⇒ 前提锁**必须报出**",
         lambda: bool(premise_problems(_mk(nodes=10, contentLen=0), "reduce", 1280, "loading")), True),
        ("Q20", "加载态：正文为空但外壳已挂载 ⇒ 前提锁**必须干净**"
                "（加载态本来就没有正文，不得因此判红）",
         lambda: bool(premise_problems(_mk(nodes=300, contentLen=0), "reduce", 1280, "loading")), False),
        ("Q21", "加载态：`frozen` 仍然**必须报出**（加载态不放宽污染检查）",
         lambda: bool(premise_problems(_mk(frozen=True, nodes=300, contentLen=0),
                                       "reduce", 1280, "loading")), True),
        ("Q22", "**样式表没加载**（`--dur-fast` 读到空串）⇒ 前提锁**必须报出**"
                "（否则 R2 会把「我没测成」报成「令牌机制失效」——实测一次 20 条假红）",
         lambda: bool(premise_problems(_mk(durVars={}), "reduce", 1280)), True),
        ("Q23", "令牌齐全 ⇒ 前提锁**必须干净**（对照组）",
         lambda: bool(premise_problems(_mk(), "reduce", 1280)), False),
    )
    for key, desc, fn, expect in pure:
        got = bool(fn())
        ok = got == expect
        print(f"  [{key}] {desc}")
        print(f"        期望{expect} 实测{got} {'✓' if ok else '✗'}")
        if not ok:
            bad.append(f"[{key}] 期望 {expect} 实测 {got}")

    for key, desc, args, expect in (
        ("Q15", "收口判定：**前提锁被破坏 ⇒ exit 2**（测量不可信）", (8, [], ["冻结脚本"]), 2),
        ("Q16", "收口判定：**0 组测量不得算通过**", (0, [], []), 2),
        ("Q17", "收口判定：有缺陷 ⇒ exit 1", (8, ["bad"], []), 1),
        ("Q18", "收口判定：全通过 ⇒ exit 0", (8, [], []), 0),
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
    print("\n自检通过：检出能力（Q2/Q4/Q5/Q7/Q10/Q10b）、对照组干净（Q1/Q3/Q6/Q8）、"
          "**阴性对照 · 防自伤**（Q5b：`spin-soft` 归零后不得判红 / Q5c：令牌失效时必须报红）、"
          "前提锁与空真（Q9/Q11–Q14）、收口判定（Q15–Q18）。")
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


async def _set_media(b: Browser, value: str) -> None:
    await b.cdp.send(
        "Emulation.setEmulatedMedia",
        features=[{"name": "prefers-reduced-motion", "value": value}],
    )
    await asyncio.sleep(0.4)


async def _pump_fetch(b: Browser, seconds: float, allow: tuple[str, ...] = LOADING_ALLOW) -> int:
    """在 `seconds` 内，把**匹配 `allow`** 的挂起请求放行，**其余继续挂起**。

    这样鉴权能过（外壳才挂得上），而数据请求一直不回来 ⇒ 应用**无限期**停在
    「数据取数中」的骨架屏状态。比「给网络加延迟」确定，比「抢时间」可靠。
    """
    import time

    deadline = time.time() + seconds
    passed = 0
    while time.time() < deadline:
        for ev in b.cdp.drain_events():
            if ev.get("method") != "Fetch.requestPaused":
                continue
            p = ev.get("params") or {}
            url = ((p.get("request") or {}).get("url")) or ""
            if any(s in url for s in allow):
                try:
                    await b.cdp.send("Fetch.continueRequest", requestId=p["requestId"])
                    passed += 1
                except Exception:  # noqa: BLE001  已被取消/重复
                    pass
        await asyncio.sleep(0.05)
    return passed


async def run(shot: bool, only: str | None) -> int:
    total = 0
    loading_groups = 0
    all_bad: list[str] = []
    control_broken: list[str] = []
    skipped: list[str] = []
    env_fail: list[str] = []
    empty_truth: list[str] = []
    mjs = measure_js()

    # 🚨 先自检**解析结果**：解析失败/错位会让 R2 一致地错。
    sane = reduce_tokens_sanity(REDUCE_DECL)
    if sane:
        print("[env] 解析 `tokens.css` 的 reduce 块**语义自检失败** ⇒ 测量不可信（exit 2）：")
        for s in sane:
            print(f"  · {s}")
        return 2

    print(f"期望值来源：`{TOKENS_CSS.relative_to(HERE.parents[0])}` 的 "
          f"`@media (prefers-reduced-motion: reduce)` 块")
    print("  声明值：" + "，".join(f"`{k}`={v}" for k, v in sorted(REDUCE_DECL.items())))
    print(f"  视口：{'、'.join(v[3] for v in VIEWPORTS)}；判据 R1a/R1b/R1c/R1d **全部判红**"
          f"（R1c 已于 2026-09-26 随 #75 拍板由「只报不判」升为判红）\n")

    for vw, vh, mobile, vtag in VIEWPORTS:
        print(f"════════ 视口 {vtag}（{vw}×{vh}，mobile={mobile}）")
        async with Browser(headless=True, width=vw, height=vh) as b:
            await b.apply_device(safe_area=None, mobile=mobile)
            for app, cfg in APPS.items():
                if only and app != only:
                    continue
                base = f"http://localhost:{cfg['port']}"
                # Cookie 不区分端口 ⇒ 换端前必须清（localStorage **区分**端口，不用清）
                try:
                    await b.cdp.send("Network.clearBrowserCookies")
                except Exception:
                    pass
                if not await login(b, base, cfg["user"]):
                    print(f"  [env] {app} 登录失败 ⇒ 本端**全部不判**")
                    env_fail.append(f"{app}: 登录失败")
                    continue
                for path in cfg["pages"]:
                    label = f"{app}{path} @{vtag}"
                    # 一次加载、两档测量：同一 DOM 只换媒体特性 ⇒ 更快也更可控
                    await b.goto(f"{base}{path}", wait=1.6)
                    await b.settle(extra=1.2)
                    await _set_media(b, "no-preference")
                    m_base = await b.cdp.evaluate(mjs)
                    await _set_media(b, "reduce")
                    m_red = await b.cdp.evaluate(mjs)

                    # —— P1/P2/P4 前提锁（两档都要成立）——
                    pre = premise_problems(m_base, "no-preference", vw) \
                        or premise_problems(m_red, "reduce", vw)
                    if pre:
                        msg = pre[0]
                        if "污染" in msg:
                            print(f"  {label:22s} ✗✗ {msg} ⇒ **exit 2**")
                            control_broken.append(f"{label}: {msg}")
                        else:
                            print(f"  {label:22s} 前提不成立：{msg} ⇒ **不判**")
                            skipped.append(f"{label}: {msg}")
                        continue

                    # —— P3 空真：no-preference 档没有可测对象 ⇒ 这一页测不出东西 ——
                    if is_empty_truth(m_base):
                        print(f"  {label:22s} 空真（no-preference 档 0 个可测元素）⇒ **不判**")
                        empty_truth.append(label)
                        continue

                    total += 1
                    problems = judge(m_red, "reduce", REDUCE_DECL)
                    ctrl = [p for p in problems if p.startswith("[R2]")]
                    real = [p for p in problems if not p.startswith("[R2]")]
                    if real:
                        print(f"  {label:22s} ✗  no-pref={len(m_base['items'])} "
                              f"reduce={len(m_red['items'])}")
                        for p in real:
                            print(f"      {p}")
                        all_bad.extend(f"{label} {p}" for p in real)
                    else:
                        print(f"  {label:22s} ✓  no-pref={len(m_base['items'])} "
                              f"reduce={len(m_red['items'])}"
                              + (f"  ⚠️ R2 未达成 {len(ctrl)} 条" if ctrl else ""))
                    if ctrl:
                        all_bad.extend(f"{label} {p}" for p in ctrl)
                    if shot:
                        await b.screenshot(
                            HERE / f"shot_rm_{app}_{path.strip('/') or 'home'}_{vtag}.png", full=False)

    # ═══════════════════════════════════════════════════════════════════
    # 加载态采样 —— **覆盖稳态看不见的那部分**
    # ═══════════════════════════════════════════════════════════════════
    # 为什么必须补这一段：spinner 与骨架屏**只在取数期间存在**，稳态下 DOM 里根本没有。
    # 实测（2026-09-25 前）：只跑稳态时全量 16 组 `reduce items=0`，看着「全绿」——
    # 而源码里 33 处内置 `spin`（1s，硬编码）与 2 处内置 `pulse`（2s，硬编码）
    # **一处都没被覆盖到**。⇒ 那片绿只覆盖了稳态那一小块，**不能当成「动画都停了」**。
    # 🚨 那段历史正是 **#75** 的起点：33 处内置 `spin` 已全部收敛进 `<Spinner>`
    # （现全仓 0 处），`pulse` 收敛进 `animate-pulse-soft`。⇒ 这一段采样**必须留着**：
    # 它现在测的是**收敛后的实现**（`spin-soft` / `pulse-soft`）到底有没有被令牌停下，
    # 而这恰恰是「源码级判据（A4 / R1c 的静态侧）看不出」的那一半。
    #
    # 手段：`Fetch.enable(patterns=[*/api/*])` 把**接口请求挂起不响应** ⇒ 应用**无限期**
    # 停在加载态（确定性；「抢在数据回来前采样」是竞态，「给网络加延迟」会把 HTML 本身也拖慢
    # ——实测延迟 4s 时 1.2s 处只有 17 个 DOM 节点，外壳根本没挂上）。
    # 每端只采**首页**，以控制耗时。
    #
    # ⚠️ 本段的**有效性控制**是 `no-preference` 档的**空真检查**：
    # 若没抓到加载态（可测元素 = 0）⇒ 该组标为「空真，不判」，**不会被读成通过**。
    print("\n════════ 加载态采样（`Fetch` 挂起 `/api/*`；取数期间的 spinner / 骨架屏）")
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
                    print(f"  [env] {app} 登录失败 ⇒ 加载态不判")
                    continue
                path = cfg["pages"][0]
                label = f"{app}{path} @{vtag}·加载态"
                # ⚠️ 拦截必须在 login **之后**开（否则登录请求会被挂起）
                await _set_media(b, "no-preference")
                try:
                    await b.cdp.send("Fetch.enable",
                                     patterns=[{"urlPattern": "*/api/*", "requestStage": "Request"}])
                except Exception as e:  # noqa: BLE001
                    print(f"  [env] {label:26s} `Fetch.enable` 不可用（{str(e)[:60]}）⇒ 不判")
                    skipped.append(f"{label}: Fetch.enable 不可用")
                    continue
                try:
                    await b.goto(f"{base}{path}", wait=0.2)
                    # 放行鉴权、挂起数据 ⇒ 停在「取数中」的骨架屏
                    await _pump_fetch(b, 3.2)
                    m_base = await b.cdp.evaluate(mjs)
                    await _set_media(b, "reduce")
                    m_red = await b.cdp.evaluate(mjs)
                finally:
                    try:
                        await b.cdp.send("Fetch.disable")
                    except Exception:
                        pass
                    b.cdp.drain_events()

                pre = premise_problems(m_base, "no-preference", vw, "loading") \
                    or premise_problems(m_red, "reduce", vw, "loading")
                if pre:
                    msg = pre[0]
                    if "污染" in msg:
                        print(f"  {label:26s} ✗✗ {msg} ⇒ **exit 2**")
                        control_broken.append(f"{label}: {msg}")
                    else:
                        print(f"  {label:26s} 前提不成立：{msg} ⇒ **不判**")
                        skipped.append(f"{label}: {msg}")
                    continue
                # —— 空真防护：这一档**没抓到加载态**（no-preference 也没有可测元素）——
                if is_empty_truth(m_base):
                    print(f"  {label:26s} 空真（没抓到加载态）⇒ **不判**")
                    empty_truth.append(label)
                    continue

                loading_groups += 1
                total += 1
                problems = judge(m_red, "reduce", REDUCE_DECL)
                ctrl = [p for p in problems if p.startswith("[R2]")]
                real = [p for p in problems if not p.startswith("[R2]")]
                if real:
                    print(f"  {label:26s} ✗  no-pref={len(m_base['items'])} "
                          f"reduce={len(m_red['items'])}")
                    for p in real:
                        print(f"      {p}")
                    all_bad.extend(f"{label} {p}" for p in real)
                else:
                    print(f"  {label:26s} ✓  no-pref={len(m_base['items'])} "
                          f"reduce={len(m_red['items'])}")
                if ctrl:
                    all_bad.extend(f"{label} {p}" for p in ctrl)
                if shot:
                    await b.screenshot(
                        HERE / f"shot_rm_{app}_loading_{vtag}.png", full=False)

    print("\n" + "=" * 74)
    print(f"测量 {total} 组（其中**加载态 {loading_groups} 组**）；不合格 {len(all_bad)} 组。")
    if empty_truth:
        print(f"空真 {len(empty_truth)} 组（no-preference 档无任何动画/过渡 ⇒ 不判，**不算通过**）：")
        for s in empty_truth:
            print(f"  · {s}")
    if skipped:
        print(f"跳过 {len(skipped)} 组（前提不成立）：")
        for s in skipped:
            print(f"  · {s}")
    if env_fail:
        print(f"环境失败 {len(env_fail)} 项：")
        for s in env_fail:
            print(f"  · {s}")

    # 🚨 这里**没有**「只报不判」的收尾块（2026-09-26）：原 R1c 的 `pending` 通道
    # 随 #75 拍板升为判红后整体移除。**别再把它加回来** —— 一个永远为空的
    # 「待拍板」通道会让人以为「这里报出来的东西有人管」。

    code = summary_exit(total, all_bad, control_broken)
    if control_broken:
        print("\n[env] **测量被污染**（`freeze_animations()` 的冻结脚本出现在页面里）⇒ "
              "此时报红报绿都没有意义（exit 2）：")
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
    if empty_truth or skipped:
        print(f"\n⚠️ 通过，但**有 {len(empty_truth)} 组空真 + {len(skipped)} 组跳过** ⇒ 覆盖率不满。")
    print("`prefers-reduced-motion`：令牌机制（R2）、过渡（R1a）、装饰动画（R1b/R1d）—— 限已测到的组。")
    print("⚠️ 已知边界：不含 WCAG 2.3.3 的主观判定、不含 JS 驱动动画、"
          "加载态只在**每端首页**采样、视口只取 390/1280 两档。")
    return 0


async def dump(only: str | None) -> int:
    """诊断模式：打印原始测量值，**不下判断**。"""
    mjs = measure_js()
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
                    print(f"[{vtag}] {app}: 登录失败")
                    continue
                for path in cfg["pages"]:
                    await b.goto(f"{base}{path}", wait=1.6)
                    await b.settle(extra=1.2)
                    await _set_media(b, "no-preference")
                    a = await b.cdp.evaluate(mjs)
                    await _set_media(b, "reduce")
                    r = await b.cdp.evaluate(mjs)
                    print(f"\n── {app}{path} @{vtag}  frozen={r.get('frozen')} "
                          f"rm={r.get('rmReduce')}")
                    print(f"   no-preference items={len(a.get('items') or [])}")
                    for it in (a.get("items") or [])[:8]:
                        print(f"     · <{it['tag']}> {it['w']}×{it['h']} "
                              f"anim={it['anim']}/{it['animDur']}×{it['iter']} "
                              f"tr={it['trProp']}/{it['trDur']} class=`{_short(it['cls'], 60)}`")
                    print(f"   reduce items={len(r.get('items') or [])}  durVars={r.get('durVars')}")
                    for it in (r.get("items") or [])[:12]:
                        print(f"     · <{it['tag']}> {it['w']}×{it['h']} "
                              f"anim={it['anim']}/{it['animDur']}×{it['iter']} "
                              f"tr={it['trProp']}/{it['trDur']} class=`{_short(it['cls'], 60)}`")

    # —— 加载态（`Fetch` 挂起 `/api/*`）——
    print("\n\n════════ 加载态（`Fetch` 挂起 `/api/*`）")
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
                await _set_media(b, "no-preference")
                try:
                    await b.cdp.send("Fetch.enable",
                                     patterns=[{"urlPattern": "*/api/*", "requestStage": "Request"}])
                except Exception as e:  # noqa: BLE001
                    print(f"[{vtag}] {app}: Fetch.enable 失败 {str(e)[:60]}")
                    continue
                try:
                    await b.goto(f"{base}{cfg['pages'][0]}", wait=0.2)
                    for t, delta in ((1.0, 1.0), (2.0, 1.0), (3.5, 1.5), (5.0, 1.5)):
                        await _pump_fetch(b, delta)
                        m = await b.cdp.evaluate(mjs)
                        body = await b.cdp.evaluate(
                            "(document.body?document.body.innerText:'').slice(0,120)")
                        print(f"\n── {app}{cfg['pages'][0]} @{vtag}·加载态 t≈{t}s  "
                              f"nodes={m.get('nodes')} items={len(m.get('items') or [])}  "
                              f"正文前 120 字=`{(body or '').replace(chr(10), ' ')[:120]}`")
                        for it in (m.get("items") or [])[:10]:
                            print(f"     · <{it['tag']}> {it['w']}×{it['h']} "
                                  f"anim={it['anim']}/{it['animDur']}×{it['iter']} "
                                  f"tr={it['trProp']}/{it['trDur']} class=`{_short(it['cls'], 70)}`")
                finally:
                    try:
                        await b.cdp.send("Fetch.disable")
                    except Exception:
                        pass
                    b.cdp.drain_events()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="`prefers-reduced-motion` 渲染级门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（合成页面）")
    ap.add_argument("--dump", action="store_true", help="诊断模式：打原始测量值，不下判断")
    ap.add_argument("--shot", action="store_true", help="顺带截图")
    ap.add_argument("--app", default=None, help="只测某一端（web/lawyer/admin/im）")
    a = ap.parse_args()
    if a.self_test:
        return asyncio.run(run_self_test())
    if a.dump:
        return asyncio.run(dump(a.app))
    return asyncio.run(run(a.shot, a.app))


if __name__ == "__main__":
    raise SystemExit(main())
