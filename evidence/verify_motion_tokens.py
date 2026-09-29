#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""§4.4「动效」门禁 —— 时长/缓动**令牌表**。

## 为什么要有这一条

§4.4（`§4.4「动效」`）给了一张**四行表 + 一条硬要求**：

| 场景 | 时长 | 缓动 |
|---|---|---|
| hover / 颜色变化 | 120ms | ease-out |
| 展开 / 折叠 | 180ms | `cubic-bezier(.2,.8,.2,1)` |
| 抽屉 / 模态 / 底部 Sheet | 240ms | `cubic-bezier(.2,.8,.2,1)` |
| 流式打字光标 | 1.2s 循环 | ease-in-out |

必须响应 `prefers-reduced-motion: reduce`。

**「必须响应 reduced-motion」那一半已有强判据**（`verify_reduced_motion.py`，渲染级、CDP 驱动）。
但**表本身零判据** —— 实测：那个门禁的 `LIVE = {"--dur-fast": "120ms", …}`（`:357`）
是**夹具里硬编码的期望值**，**没有任何门禁断言 `tokens.css` 的 `--dur-*` / `--ease-*` == §4.4 表**。
（⚠️ 那三个数字**恰好是对的**，但「恰好对」不是判据 —— 规范改了它不会红。）

本节与 §4.2 间距 / §4.3 圆角**结构同构**（**令牌阶梯表**），那两节各有门禁 ⇒ 本节按同一形状补齐。

## 分工（不重叠）

- **reduced-motion 行为** ⇒ `verify_reduced_motion.py`（本节**不判**渲染、不启浏览器）。
- **动效时长/缓动令牌的值** ⇒ **本节**。
- **§4.3 的过渡时长/缓动**（圆角/阴影/边框那节的表）⇒ `verify_radius_scale.py`。

## 判据

- **A0** 守卫：§4.4 必须解析出 ≥4 行表 + 「必须响应 `prefers-reduced-motion`」那句；
  `tokens.css` 必须解析出 ≥3 个 `--dur-*`。**期望值一律从 §4.4 现读，不硬编码。**
- **A1** 🎯 **按「场景名 ↔ 令牌注释」精确配对**，逐条判时长相等。
  ⚠️ **不按顺序配对** —— 顺序是假设；而令牌注释里**逐字抄了规范场景名**
  （`--dur-fast: 120ms; /* hover / 颜色变化 */`），这是**可验证的对应关系**。
- **A2** 缓动：§4.4 里以 `ease-*` 关键字给出的，`preset` 的 `transitionTimingFunction`
  必须有**同名后缀**的键（`ease-out` ↔ `out`）；以 `cubic-bezier(...)` 字面量给出的，
  必须有某个令牌**归一化后相等**。
- **A3** **机制**（不是值）：`.flow-typing::after` 的 `animation` 必须**引用 `var(--dur-pulse)`**，
  不得硬编码时长 —— 否则「响应 reduced-motion」的机制根本不成立
  （`verify_reduced_motion.py` 的 R2 依赖这条链）。
- **A4** 🚨 **不得绕开令牌链另起炉灶**：源码里不许出现 Tailwind **内置**动画工具类
  （`animate-pulse` / `animate-bounce` / `animate-ping` / **`animate-spin`**）——
  它们**写死了时长/缓动、不读 `--dur-*`** ⇒ reduce 归零机制对它们**完全无效**。
  骨架屏一律用 `animate-pulse-soft`；**加载指示一律用 `<Spinner>`**（其 `animate-spin-soft`）。
  ⚠️ 右边界 `(?![\w-])` 必需，否则 `animate-pulse-soft` / `animate-spin-soft` 会被自己误伤。
  🚨 **这一行本身踩过坑**（2026-09-25）：docstring **也是字符串** —— 非 raw 的 docstring 里写 `\w`
  会触发 `SyntaxWarning: invalid escape sequence`，即「写**关于转义**的文档」时，**文档自己中了同一个坑**
  （README 坑 71 同族）。⇒ **本 docstring 已加 `r` 前缀**，所以上面那个 `\w` 是字面量、安全。
  ⚠️ 顺带：想在这段文字里写出「三个引号」本身，会**提前闭合 docstring** ⇒ 只能这样描述。
  （A3 判的是「那条链**接上了**」；A4 判的是「**不许有第二条链**」—— 一正一反。）
  📌 2026-09-25 补：修 #41 时实测有 2 处（im 骨架屏 + `NotificationCenter`），
  而当时**没有任何判据拦它** ⇒ 这条是把「零判据」补上。
  📌 2026-09-25（#75）**再补 `spin`**：`spin` 此前被刻意排除（见 R7），拍板后纳入。
  ⚠️ 本条**连注释一起扫**（不做注释剥离）⇒ 源码里想写出被禁类名做说明，**必须拆开写**
  （例：`animate-` + `spin`）。这与上面那条坑**同族**：**判据的文本扫描会把「描述规则的文字」也当规则对象**。

## 只报不判（R 组）

- **R1** 🚨 **流式打字光标的时长与规范不符**：规范 **1.2s**，实现 `--dur-pulse` = **1.6s**。
  ⚠️ **不当缺陷报，因为它是「合并令牌」**：`--dur-pulse` 的注释写着
  「骨架屏**与**打字光标的循环周期」⇒ 它是**共用**令牌（消费者见 R2），
  改它会连带改骨架屏 ⇒ **需要拍板**（改规范 / 拆令牌 / 改实现）。
- **R2** `--dur-pulse` 的消费者清单（说明 R1 为什么不能直接改）。
- **R3** 规范对**骨架屏周期**没有任何出处 —— 建议 §4.4 补一行。
- **R4** `ease-out` 工具类被 preset **重定义**为 `cubic-bezier(0,0,0.2,1)`，
  **不等于** CSS 关键字 `ease-out` 的标准值 `cubic-bezier(0,0,0.58,1)`。
  项目**有意为之**（`preset` 的「动效」段），**不是缺陷**，但读者容易被名字误导。
- **R5** 裸 ms/s 的 `animation`/`transition` 时长（不走令牌）。
- **R6** 未令牌化的 **CSS 原生缓动关键字**（规范没要求，**不判**）。
- **R7** 📌 **存档项**（判据已于 2026-09-25 · #75 **并入 A4**）：加载指示的旋转类的**正面计数**。
  它曾是「只报不判」—— 因为「**加载指示**算不算『必须归零的动效』」是设计问题、需拍板。
  拍板结果：**算**（与 `animate-pulse` 同类，「要么都归零、要么都不归零」），
  但加载指示**换形态不换指示器**（旋转 → 静止的弧；停掉指示器会让用户以为卡死）。
  收敛前实测 **33 处**全是真·加载指示（8 处 `packages/ui` 原语 / 25 处页面手搓；
  共享 `<Spinner>` 只有 4 处真实引用 ⇒ **原语被绕过**）；现已 **0 处**。
  ⇒ 裁定材料：`deliverables/ui-design/motion-spin-audit.md`。
  ⚠️ **保留 R7 而不直接删**：A4 只能说「有就红」，说不出「收敛到 0 这件事有没有被回退」。

## 退出码

`0` 通过（允许出现**已登记**的欠账）· `1` 新的违反，或棘轮豁免过期 · `2` 环境问题（工具坏了）。
"""

import argparse
import os
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

SPEC_REL = "deliverables/ui-design/design-spec.md"
TOKENS_REL = "frontend/packages/ui/src/tokens.css"
STYLES_REL = "frontend/packages/ui/src/styles.css"
# ⚠️ 树外必需文件：`tailwind.preset.ts` 在 `frontend/` **根下**，
# 不在 `apps/` 或 `packages/ui/src/` 任何一棵被遍历的树里。
# 漏掉它不是报错，而是 `easing` 变**空字典** ⇒ A2 **空判据恒绿（假绿）**。
# ⚠️ `SPEC_REL` 是 `.md`、同样不在那两棵树里，而 R3 要读它
# ⇒ 也必须补进来，否则 R3 会**拿空串下结论**。
# （实测：这个守卫**当场咬合过一次**，把「工具问题」与「产品缺陷」分开了。）
EXTRA_RELS: tuple[str, ...] = ("frontend/tailwind.preset.ts", SPEC_REL)

REQUIRED_FILES = (SPEC_REL, TOKENS_REL, STYLES_REL) + EXTRA_RELS

SKIP_DIRS = {"node_modules", ".next", ".next_old_v14_keep", "_prev_build",
             "dist", "build", ".turbo", "__pycache__", ".git"}

# ---------------------------------------------------------------------------
# 棘轮：已登记、不阻断 CI，但**不许涨**、**修好必须删**。
# 当前为空 —— A1/A2/A3 实测**全部通过**。
# ---------------------------------------------------------------------------
KNOWN_GAPS: dict[str, str] = {}

# `--dur-fast: 120ms; /* hover / 颜色变化 */`
DUR_RE = re.compile(
    r"--(?P<name>dur-[a-z0-9-]+)\s*:\s*(?P<val>[0-9.]+m?s)\s*;"
    r"(?:\s*/\*\s*(?P<note>[^*]*?)\s*\*/)?"
)
# `--ease-soft: cubic-bezier(0.2, 0.8, 0.2, 1);`
EASE_RE = re.compile(r"--(?P<name>ease-[a-z0-9-]+)\s*:\s*(?P<val>[^;]+);")
# §4.4 表行：| 场景 | 时长 | 缓动 |
ROW_RE = re.compile(
    r"^\|\s*(?P<sc>[^|]+?)\s*\|\s*(?P<dur>[0-9.]+m?s)(?:\s*循环)?\s*\|\s*(?P<ease>[^|]+?)\s*\|\s*$",
    re.M,
)
# `cubic-bezier(.2,.8,.2,1)`
CB_RE = re.compile(r"cubic-bezier\(\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*\)")
BARE_DUR_RE = re.compile(r"(?:transition|animation)[^;{}]*?[0-9.]+m?s")

# ---------------------------------------------------------------------------
# A4 用：Tailwind **内置**动画工具类（时长/缓动**写死在 Tailwind 里**）
# ---------------------------------------------------------------------------
# 🚨 为什么内置类是**缺陷**：`tokens.css` 的 reduce 归零靠的是
#   `--dur-pulse: 0ms; --pulse-iter: 1`，而内置类**根本不读这两个变量**
#   ⇒ 在 `prefers-reduced-motion: reduce` 下照跑。
#   骨架屏必须用本项目的 `animate-pulse-soft`（经 `var(--dur-pulse)`）。
# ⚠️ 右边界 `(?![\w-])` 是**必需的**：否则 `animate-pulse-soft` 会被自己误伤
#   （与坑 46 第三形态「按名字守的守卫」同族：**守卫自己得先认得出名字的边界**）。
BUILTIN_ANIM_RE = re.compile(r"\banimate-(?:pulse|bounce|ping|spin)(?![\w-])")
# 📌 2026-09-25（#75）：`spin` **已纳入 A4**。此前它被**刻意排除**，因为
#   「**加载指示**算不算『必须归零的动效』」是**设计问题、需拍板**（原 R7）。
#   拍板结果：**算** —— 但**形态可以换**（旋转 → 静止的弧），指示器本身必须保留
#   （停掉指示器会让用户以为界面卡死）。
#   纳入前先做了两件事，所以判据上线当天是**绿的**：
#     ① **收敛**：33 处手搓 → 0 处（25 处页面 + `packages/ui` 内部 7 处 ⇒ 一律引用 `<Spinner>`）；
#     ② **令牌化**：新增 `animate-spin-soft`（经 `--dur-spin` / `--spin-iter`）。
# ⚠️ 这条判据**连注释一起扫**（`iter_sources()` 不做注释剥离）⇒ 源码里想把被禁类名
#   写出来做说明，**必须拆开写**（例：`animate-` + `spin`，见 `Spinner.tsx` 的注释）。
#   与坑 71「写**关于转义**的文档，文档自己中了转义的坑」同型。
# ⚠️ 右边界 `(?![\w-])` 对 `spin` 同样必需：否则 `animate-spin-soft` 会被自己误伤。
# 保留 `SPIN_RE` 仅供 R7 存档统计（判据本身已由 A4 承担）。
SPIN_RE = re.compile(r"\banimate-spin(?![\w-])")


def iter_sources() -> "list[tuple[str, str]]":
    """剪枝遍历 `apps/` + `packages/`（+ 树外必需文件）。"""
    roots = [ROOT / "frontend" / "apps", ROOT / "frontend" / "packages"]
    out: list[tuple[str, str]] = []
    for r in roots:
        if not r.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                if not fn.endswith((".ts", ".tsx", ".css")):
                    continue
                p = pathlib.Path(dirpath) / fn
                try:
                    out.append((p.relative_to(ROOT).as_posix(),
                                p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    continue
    for rel in EXTRA_RELS:
        p = ROOT / rel
        if p.exists():
            out.append((rel, p.read_text(encoding="utf-8", errors="replace")))
    out.sort(key=lambda kv: kv[0])
    return out


def to_seconds(raw: str) -> float | None:
    """`120ms` -> 0.12；`1.2s` -> 1.2。解析不出返回 None（**不是「合规」**）。"""
    m = re.fullmatch(r"([0-9.]+)(ms|s)", raw.strip())
    if not m:
        return None
    v = float(m.group(1))
    return v / 1000.0 if m.group(2) == "ms" else v


def norm_cb(raw: str) -> str | None:
    """`cubic-bezier(.2,.8,.2,1)` -> `cubic-bezier(0.2,0.8,0.2,1)`（补前导 0）。"""
    m = CB_RE.search(raw)
    if not m:
        return None
    return "cubic-bezier(" + ",".join(f"{float(x):g}" for x in m.groups()) + ")"


def load_section(text: str, heading: str, stop: str) -> str | None:
    m = re.search(r"^###\s+" + re.escape(heading) + r"\s*$", text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    e = re.search(r"^##\s+" + re.escape(stop) + r"\s*$", rest, re.M)
    return rest[: e.start()] if e else rest


class Report:
    def __init__(self) -> None:
        self.ok: list[str] = []
        self.fail: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def add_ok(self, m: str) -> None:
        self.ok.append(m)

    def add_fail(self, k: str, m: str) -> None:
        self.fail.append((k, m))

    def add_note(self, m: str) -> None:
        self.notes.append(m)


# ===========================================================================
# 判据
# ===========================================================================
def judge_a0(rows, tokens, preset, rep: Report) -> None:
    """守卫：规范表 / 令牌表 / preset 都必须解析出来；**阈值不许自己编**。"""
    if len(rows) < 4:
        rep.add_fail("A0:表行数",
                     f"§4.4 解析出 {len(rows)} 行（期望 ≥4）⇒ 规范表结构变了，**判不了**")
    durs = {n: v for n, v, _ in tokens}
    if len(durs) < 3:
        rep.add_fail("A0:令牌数",
                     f"`tokens.css` 只解析出 {len(durs)} 个 `--dur-*`（期望 ≥3）⇒ 判不了")
    if not preset:
        rep.add_fail("A0:preset", "`tailwind.preset.ts` 里解析不到 `transitionTimingFunction` ⇒ 判不了")
    if not rep.fail:
        rep.add_ok(f"A0 守卫：§4.4 {len(rows)} 行表 + {len(durs)} 个 `--dur-*` + preset 缓动表均已解析")


def judge_a1(rows, tokens, rep: Report) -> None:
    """🎯 按「场景名 ↔ 令牌注释」**精确配对**，逐条判时长相等。"""
    by_note = {}
    for name, val, note in tokens:
        if note:
            by_note.setdefault(note.strip(), (name, val))
    paired = 0
    for sc, dur, _ease in rows:
        hit = by_note.get(sc.strip())
        if hit is None:
            continue
        paired += 1
        name, tval = hit
        want, got = to_seconds(dur), to_seconds(tval)
        if want is None or got is None:
            rep.add_fail("A1:解析失败", f"`{sc}` 的时长解析不出（规范 `{dur}` / 令牌 `{tval}`）⇒ 判不了")
        elif abs(want - got) > 1e-6:
            rep.add_fail("A1:时长不符",
                         f"§4.4「{sc}」= **{dur}**，而 `--{name}` = **{tval}**")
    if paired == 0:
        rep.add_fail("A1:零配对",
                     "§4.4 的场景名**一个都没能在令牌注释里精确配到** ⇒ 配对机制失效，**本轮结论不可信**")
    elif not any(k.startswith("A1:") for k, _ in rep.fail):
        rep.add_ok(f"A1 时长逐条相等：{paired} 行按「场景名 ↔ 令牌注释」精确配对，全部一致")


def judge_a2(rows, preset, ease_tokens, rep: Report) -> None:
    """缓动。

    🚨 **两类缓动要分开判，否则是假红**（实测踩过）：
    - **项目令牌化的**（`preset` 的 `transitionTimingFunction` 里**有同名后缀键**）：
      必须指向 `var(--ease-<后缀>)` 且该令牌存在 ⇒ **硬判**。
    - **走 CSS 原生关键字的**（`preset` 里**没有**该键，如 `ease-in-out`）：
      CSS 原生能力，**规范没要求令牌化** ⇒ **只报不判**
      （第一版对所有 `ease-*` 都要求 preset 键 ⇒ 对 `ease-in-out` **假红**）。
    """
    keys = set(preset.get("keys", ()))
    items = dict(preset.get("items", ()))
    token_cb = {norm_cb(v): n for n, v in ease_tokens if norm_cb(v)}
    untokenized: list[str] = []
    for sc, _dur, ease in rows:
        e = ease.strip().strip("`")
        m = re.fullmatch(r"ease-(out|in|in-out|linear)", e)
        if m:
            suffix = m.group(1)
            if suffix not in keys:
                untokenized.append(f"{sc}→`{e}`")
                continue
            raw = items.get(suffix, "")
            tok = re.fullmatch(r"var\(--(ease-[a-z0-9-]+)\)", raw.strip())
            if tok is None:
                rep.add_fail("A2:键未指向令牌",
                             f"§4.4「{sc}」的缓动 `{e}` 在 preset 里有键 `{suffix}`，"
                             f"但它的值是 `{raw}` —— **不是 `var(--ease-…)`**")
            elif tok.group(1) not in {n for n, _ in ease_tokens}:
                rep.add_fail("A2:令牌不存在",
                             f"preset 的 `{suffix}` 指向 `var(--{tok.group(1)})`，"
                             f"但 `tokens.css` 里没有这个令牌")
            continue
        c = norm_cb(e)
        if c is None:
            rep.add_fail("A2:缓动无法解析",
                         f"§4.4「{sc}」的缓动 `{e}` 既不是 `ease-*` 也不是 `cubic-bezier(...)` ⇒ 判不了")
        elif c not in token_cb:
            rep.add_fail("A2:缓动无等价令牌",
                         f"§4.4「{sc}」要求 `{c}`，`tokens.css` 的 `--ease-*` 里找不到等价令牌"
                         f"（实测：{sorted(token_cb) or '空'}）")
    if untokenized:
        rep.add_note(
            f"R6 下列缓动走 **CSS 原生关键字**、未令牌化（规范没要求，**不判**）："
            f"{untokenized} —— 若将来要支持「品牌化缓动」需补令牌。"
        )
    if not any(k.startswith("A2:") for k, _ in rep.fail):
        rep.add_ok("A2 缓动：令牌化关键字已指向 `var(--ease-*)`，`cubic-bezier` 字面量均有等价令牌")


def judge_a3(files: dict[str, str], rep: Report) -> None:
    """机制：`.flow-typing::after` 必须把时长接在 `var(--dur-pulse)` 上。"""
    src = files.get(STYLES_REL)
    if src is None:
        rep.add_fail("A3:文件缺失", f"`{STYLES_REL}` 不在扫描集 ⇒ 判不了")
        return
    m = re.search(r"\.flow-typing::after\s*\{(?P<body>[^}]*)\}", src, re.S)
    if m is None:
        rep.add_fail("A3:结构", "`styles.css` 里找不到 `.flow-typing::after` 规则 ⇒ 结构变了")
        return
    body = m.group("body")
    if "var(--dur-pulse)" not in body:
        rep.add_fail("A3:未接令牌",
                     "`.flow-typing::after` 的 animation **没有引用 `var(--dur-pulse)`** "
                     "⇒ reduced-motion 的归零机制对它失效（`verify_reduced_motion.py` 的 R2 依赖这条链）")
    else:
        rep.add_ok("A3 机制：`.flow-typing::after` 的时长接在 `var(--dur-pulse)` 上（可被 reduce 归零）")


def judge_a4(files: dict[str, str], rep: Report) -> None:
    """源码里**不得出现 Tailwind 内置动画工具类**
    （`animate-pulse` / `animate-bounce` / `animate-ping` / `animate-spin`）。

    🚨 为什么这是**判据**、而不是「只报」：内置类的时长/缓动**写死在 Tailwind 里**，
    不经过 preset 的 `--dur-*` ⇒ `@media (prefers-reduced-motion: reduce)` 里那句
    `--dur-pulse: 0ms; --pulse-iter: 1` **对它无效** ⇒ 动效在 reduce 下照跑。
    （A3 只判了 `.flow-typing::after` **这一条链**；本条补的是**反向**：
      不许绕开令牌链另起炉灶。）

    实测（2026-09-25 · 修 #41）：修前有 2 处 —— `apps/im/app/(app)/page.tsx:175`
    （im 骨架屏）与 `packages/ui/src/components/NotificationCenter.tsx:746`，
    且**当时没有任何判据拦它**（`verify_motion_tokens.py` 只把消费者列进 R2 备注；
    `verify_reduced_motion.py` 是 CDP 渲染级、且按 `--app` 单端跑）。

    📌 2026-09-25（#75）补 `spin`：加载指示与骨架屏**在「功能型」维度上同类** ——
    只豁免其中一个，另一套论证会立刻回来把 A4 拆掉 ⇒ **要么都归零、要么都不归零**。
    拍板为「都归零」，但加载指示**换形态不换指示器**（旋转 → 静止的弧）。
    """
    hits: list[str] = []
    for rel, src in sorted(files.items()):
        for i, ln in enumerate(src.splitlines(), 1):
            if BUILTIN_ANIM_RE.search(ln):
                hits.append(f"{rel}:{i}")
    if hits:
        rep.add_fail(
            "A4:内置动画类",
            f"源码里出现 Tailwind **内置**动画工具类 {hits[:6]}"
            f"（共 {len(hits)} 处）⇒ 它们**不读 `--dur-*`**，reduced-motion 下不会归零；"
            "骨架屏请改用 `animate-pulse-soft`（经 `var(--dur-pulse)`）、"
            "加载指示请改用 `<Spinner>`（其 `animate-spin-soft` 经 `var(--dur-spin)`）",
        )
    else:
        rep.add_ok(
            "A4 机制：源码无 Tailwind 内置动画工具类"
            "（骨架屏一律经 `animate-pulse-soft`、加载指示一律经 `Spinner` 的 `animate-spin-soft`）"
        )


def collect_notes(rows, tokens, files, rep: Report) -> None:
    """R 组：只报不判。"""
    # R1 光标时长 vs 规范
    note_pulse = next(((n, v) for n, v, _ in tokens if n == "dur-pulse"), None)
    cursor = [r for r in rows if "打字" in r[0] or "光标" in r[0]]
    if note_pulse and cursor:
        want = to_seconds(cursor[0][1])
        got = to_seconds(note_pulse[1])
        if want is not None and got is not None and abs(want - got) > 1e-6:
            rep.add_note(
                f"R1 🚨 **流式打字光标的时长与规范不符**：§4.4 写 **{cursor[0][1]}**，"
                f"实现 `--{note_pulse[0]}` = **{note_pulse[1]}**（慢 {abs(got/want-1)*100:.0f}%）。"
                f"⚠️ **不当缺陷报 —— 它是「合并令牌」**：注释写着「骨架屏**与**打字光标的循环周期」，"
                f"改它会**连带改骨架屏** ⇒ 需拍板（改规范 / 拆令牌 / 改实现）。"
            )
    # R2 --dur-pulse 的消费者（**直接 + 间接**都要列，否则会低估影响面）
    direct: list[str] = []
    indirect: list[str] = []
    for rel, src in sorted(files.items()):
        if rel == TOKENS_REL:
            continue
        if "dur-pulse" in src:
            direct.append(rel)
        elif "animate-pulse-soft" in src or "pulse-soft " in src:
            # 经 preset 的 `animate-pulse-soft` 工具类间接消费 `var(--dur-pulse)`
            indirect.append(rel)
    if direct or indirect:
        rep.add_note(
            f"R2 `--dur-pulse` 的消费者：**直接** {len(direct)} 个文件 {direct[:4]}；"
            f"**经 `animate-pulse-soft` 工具类间接** {len(indirect)} 个文件 {indirect[:6]} "
            f"⇒ **这就是 R1 不能直接改值的理由**：它同时驱动骨架屏与打字光标。"
        )
    # R3 骨架屏周期在规范里没有出处
    spec_has_skeleton = False
    spec = files.get(SPEC_REL, "")
    m = re.search(r"^###\s+4\.4\s*$", spec, re.M)
    if m:
        seg = spec[m.end():m.end() + 2000]
        spec_has_skeleton = "骨架" in seg
    if not spec_has_skeleton:
        rep.add_note(
            "R3 §4.4 的表**没有「骨架屏」这一行**，但实现里 `--dur-pulse` 的注释声明它同时服务骨架屏 "
            "⇒ **骨架屏周期在规范里没有出处**，建议 §4.4 补一行（或拆出独立令牌）。"
        )
    # R4 ease-out 被重定义
    preset_src = files.get(EXTRA_RELS[0], "")
    if re.search(r"out:\s*\"var\(--ease-out\)\"", preset_src):
        rep.add_note(
            "R4 preset 把 `ease-out` **重定义**为 `var(--ease-out)` = `cubic-bezier(0,0,0.2,1)`，"
            "**不等于** CSS 关键字 `ease-out` 的标准值 `cubic-bezier(0,0,0.58,1)`。"
            "项目**有意为之**（`preset` 的「动效」段，且实测 0 处代码用内置 `ease-in-out` 工具类）"
            "⇒ **不是缺陷**，但读者容易被名字误导。"
        )
    # R5 裸时长
    bare: list[str] = []
    for rel, src in sorted(files.items()):
        if not rel.endswith(".css"):
            continue
        for i, ln in enumerate(src.splitlines(), 1):
            if ln.lstrip().startswith(("*", "//", "/*")):
                continue
            if BARE_DUR_RE.search(ln) and "var(--dur" not in ln:
                bare.append(f"{rel}:{i}")
    if bare:
        rep.add_note(f"R5 裸 ms/s 的 animation/transition 时长（不走令牌）：{bare[:5]}")
    # R7 —— **判据已并入 A4**（2026-09-25 · #75），此处仅存档收敛状态。
    # 保留它的理由：A4 只说「有就红」，说不出「已经收敛到什么程度」；
    # 而 R7 是**正面计数**，能一眼看出 33 → 0 这件事有没有被回退。
    spins: list[str] = []
    for rel, src in sorted(files.items()):
        for i, ln in enumerate(src.splitlines(), 1):
            if SPIN_RE.search(ln):
                spins.append(f"{rel}:{i}")
    if spins:
        rep.add_note(
            f"R7（存档）加载指示的旋转类仍有 **{len(spins)} 处**：{spins[:3]} ⇒ "
            f"应改用 `<Spinner>`（其 `animate-spin-soft` 经 `var(--dur-spin)`，reduce 下归零）。"
            f"⚠️ 这几处**同时**会被 A4 判红 —— `spin` 已于 #75 纳入 A4。"
        )
    else:
        rep.add_note(
            "R7（存档；判据已并入 A4）加载指示的旋转类 **0 处** ✅ —— "
            "2026-09-25（#75）把全仓 **33 处**手搓收敛为 0"
            "（25 处页面 + `packages/ui` 内部 7 处 ⇒ 一律引用 `<Spinner>`），"
            "并给原语建了 `animate-spin-soft`（经 `--dur-spin` / `--spin-iter`）"
            "⇒ `prefers-reduced-motion: reduce` 下**停成静止的弧**（换形态、不换指示器）。"
            "裁定材料：`deliverables/ui-design/motion-spin-audit.md`。"
        )


def split_ratchet(fail, known):
    known_hits = [(k, m) for k, m in fail if k in known]
    new_hits = [(k, m) for k, m in fail if k not in known]
    expired = [k for k in known if not any(k == fk for fk, _ in fail)]
    return known_hits, new_hits, expired


# ===========================================================================
# 自检（纯函数：不读真实仓库、不启浏览器 ⇒ 可进 SELFTESTABLE）
# ===========================================================================
SYN_SPEC = """
### 4.4 动效

| 场景 | 时长 | 缓动 |
|---|---|---|
| hover / 颜色变化 | 120ms | ease-out |
| 展开 / 折叠 | 180ms | `cubic-bezier(.2,.8,.2,1)` |
| 抽屉 / 模态 / 底部 Sheet | 240ms | `cubic-bezier(.2,.8,.2,1)` |
| 流式打字光标 | 1.2s 循环 | ease-in-out |

必须响应 `prefers-reduced-motion: reduce`。
"""

SYN_TOKENS = """
:root {
  --dur-fast: 120ms; /* hover / 颜色变化 */
  --dur-base: 180ms; /* 展开 / 折叠 */
  --dur-slow: 240ms; /* 抽屉 / 模态 / 底部 Sheet */
  --dur-pulse: 1.6s; /* 骨架屏与打字光标的循环周期 */
  --ease-out: cubic-bezier(0, 0, 0.2, 1);
  --ease-soft: cubic-bezier(0.2, 0.8, 0.2, 1);
}
"""

SYN_PRESET = """
theme: { extend: { transitionTimingFunction: { out: "var(--ease-out)", soft: "var(--ease-soft)" } } }
"""

SYN_STYLES = """
.flow-typing::after {
  content: "▋";
  animation: pulse-soft var(--dur-pulse) ease-in-out var(--pulse-iter);
}
"""


def parse_rows(section: str):
    return [(m.group("sc"), m.group("dur"), m.group("ease")) for m in ROW_RE.finditer(section)]


def parse_tokens(text: str):
    return [(m.group("name"), m.group("val"), m.group("note")) for m in DUR_RE.finditer(text)]


def parse_ease_tokens(text: str):
    """`--ease-*` 令牌（**必须与 `--dur-*` 分开解析** —— 第一版漏了这一步，
    导致 `token_cb` 恒空 ⇒ A2 对合法代码**假红**）。"""
    return [(m.group("name"), m.group("val").strip()) for m in EASE_RE.finditer(text)]


def parse_preset(text: str) -> dict:
    m = re.search(r"transitionTimingFunction\s*:\s*\{(?P<body>[^}]*)\}", text, re.S)
    if not m:
        return {}
    body = m.group("body")
    items = re.findall(r"([A-Za-z0-9_-]+)\s*:\s*\"([^\"]+)\"", body)
    return {"keys": [k for k, _ in items], "items": items}


def _syn_srcs(**over):
    files = {SPEC_REL: SYN_SPEC, TOKENS_REL: SYN_TOKENS,
             STYLES_REL: SYN_STYLES, EXTRA_RELS[0]: SYN_PRESET}
    files.update(over)
    return files


def _run(srcs=None):
    files = srcs if srcs is not None else _syn_srcs()
    section = load_section(files[SPEC_REL], "4.4 动效", "05 责任边界三态系统")
    rep = Report()
    if section is None:
        rep.add_fail("A0:小节", "解析不到 §4.4")
        return rep
    rows = parse_rows(section)
    tokens = parse_tokens(files[TOKENS_REL])
    ease_tokens = parse_ease_tokens(files[TOKENS_REL])
    preset = parse_preset(files[EXTRA_RELS[0]])
    judge_a0(rows, tokens, preset, rep)
    if rep.fail:
        return rep
    judge_a1(rows, tokens, rep)
    judge_a2(rows, preset, ease_tokens, rep)
    judge_a3(files, rep)
    judge_a4(files, rep)
    collect_notes(rows, tokens, files, rep)
    return rep


def self_test() -> int:
    print("=== §4.4 动效令牌门禁 · 自检（纯函数，不读真实仓库） ===")
    arms: list[tuple[str, bool, str]] = []

    sec = load_section(SYN_SPEC, "4.4 动效", "05 责任边界三态系统")
    rows = parse_rows(sec or "")
    arms.append(("Q0 解析出 4 行表", len(rows) == 4, f"{len(rows)} 行"))
    toks = parse_tokens(SYN_TOKENS)
    arms.append(("Q0 解析出 4 个 --dur-*", len(toks) == 4, f"{[n for n, _, _ in toks]}"))
    etoks = parse_ease_tokens(SYN_TOKENS)
    arms.append(("Q0 解析出 2 个 --ease-*", len(etoks) == 2, f"{[n for n, _ in etoks]}"))
    pre = parse_preset(SYN_PRESET)
    arms.append(("Q0 解析出 preset 缓动键", pre.get("keys") == ["out", "soft"], f"{pre.get('keys')}"))

    r = _run()
    arms.append(("Q1 干净对照必须无 fail", not r.fail, f"{[k for k, _ in r.fail]}"))
    arms.append(("Q2 R 组必须报出光标时长不符",
                 any("流式打字光标" in n and "1.2s" in n and "1.6s" in n for n in r.notes),
                 f"notes={len(r.notes)}"))

    # A1 时长漂移
    r = _run(_syn_srcs(**{TOKENS_REL: SYN_TOKENS.replace("--dur-base: 180ms", "--dur-base: 200ms")}))
    arms.append(("Q3 时长漂移必须红", any(k.startswith("A1:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A1 配对失效（把注释全删）
    r = _run(_syn_srcs(**{TOKENS_REL: re.sub(r"/\*[^*]*\*/", "", SYN_TOKENS)}))
    arms.append(("Q4 零配对必须红", any(k == "A1:零配对" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A2 键**存在但没指向令牌** ⇒ 必须红（这是真正该拦的形态）
    r = _run(_syn_srcs(**{EXTRA_RELS[0]: SYN_PRESET.replace(
        'out: "var(--ease-out)"', 'out: "cubic-bezier(0,0,0.58,1)"')}))
    arms.append(("Q5 键未指向令牌必须红", any(k == "A2:键未指向令牌" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A2 键指向**不存在的令牌** ⇒ 必须红
    r = _run(_syn_srcs(**{EXTRA_RELS[0]: SYN_PRESET.replace(
        'out: "var(--ease-out)"', 'out: "var(--ease-nonexistent)"')}))
    arms.append(("Q5b 令牌不存在必须红", any(k == "A2:令牌不存在" for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A2 **键缺失** ⇒ 走 CSS 原生关键字 ⇒ **不得红**（只报）—— 第一版判据过宽，这是它的回归臂
    r = _run(_syn_srcs(**{EXTRA_RELS[0]: SYN_PRESET.replace('out: "var(--ease-out)",', "")}))
    arms.append(("Q5c 键缺失不得红（走原生关键字）",
                 not any(k.startswith("A2") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A2 cubic-bezier 等价性：把令牌改成不同曲线
    r = _run(_syn_srcs(**{TOKENS_REL: SYN_TOKENS.replace(
        "cubic-bezier(0.2, 0.8, 0.2, 1)", "cubic-bezier(0.9, 0.9, 0.9, 0.9)")}))
    arms.append(("Q6 曲线不等价必须红", any(k.startswith("A2:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A2 归一化：前导 0 与空格差异**不得**红
    r = _run(_syn_srcs(**{TOKENS_REL: SYN_TOKENS.replace(
        "cubic-bezier(0.2, 0.8, 0.2, 1)", "cubic-bezier(.2,.8,.2,1)")}))
    arms.append(("Q7 归一化后相等不得红", not any(k.startswith("A2") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A3 机制
    r = _run(_syn_srcs(**{STYLES_REL: SYN_STYLES.replace("var(--dur-pulse)", "1.6s")}))
    arms.append(("Q8 光标未接令牌必须红", any(k.startswith("A3:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))
    r = _run(_syn_srcs(**{STYLES_REL: "/* 没有这条规则 */"}))
    arms.append(("Q9 结构缺失必须红", any(k.startswith("A3:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A4 内置动画类：骨架屏必须走 `animate-pulse-soft`
    r = _run(_syn_srcs(**{"frontend/apps/im/app/x.tsx":
                          'const a = <div className="h-3 animate-pulse" />;\n'}))
    arms.append(("Q18 裸 animate-pulse 必须红", any(k.startswith("A4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))
    # 阴性对照：**没有它，Q18 只能证明「会红」，证明不了「该绿时绿」** ——
    # `animate-pulse-soft` 含 `animate-pulse` 前缀，右边界写错就会把合法代码判红。
    r = _run(_syn_srcs(**{"frontend/apps/im/app/x.tsx":
                          'const a = <div className="h-3 animate-pulse-soft" />;\n'}))
    arms.append(("Q19 animate-pulse-soft 不得红（阴性对照）",
                 not any(k.startswith("A4") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))
    # 📌 2026-09-25（#75）：`spin` 纳入 A4 后，同形补一对 —— 阳性 + 阴性。
    # ⚠️ 阴性对照在这里**比 pulse 那次更要紧**：`animate-spin-soft` 是本次**新造**的
    #    合法类名，若右边界写错，A4 会把**我们自己的修复**判成缺陷（自伤）。
    r = _run(_syn_srcs(**{"frontend/apps/im/app/x.tsx":
                          'const a = <span className="animate-spin" />;\n'}))
    arms.append(("Q18b 裸 animate-spin 必须红（#75 新纳入）",
                 any(k.startswith("A4:") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))
    r = _run(_syn_srcs(**{"frontend/packages/ui/src/components/Spinner.tsx":
                          'const a = <svg className="animate-spin-soft" />;\n'}))
    arms.append(("Q19b animate-spin-soft 不得红（阴性对照 · 防自伤）",
                 not any(k.startswith("A4") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # A0 守卫
    r = _run(_syn_srcs(**{SPEC_REL: SYN_SPEC.replace(
        "| 展开 / 折叠 | 180ms | `cubic-bezier(.2,.8,.2,1)` |\n", "")
        .replace("| 抽屉 / 模态 / 底部 Sheet | 240ms | `cubic-bezier(.2,.8,.2,1)` |\n", "")}))
    arms.append(("Q10 规范表行数不足必须红", any(k.startswith("A0") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))
    r = _run(_syn_srcs(**{TOKENS_REL: ":root { --dur-fast: 120ms; }"}))
    arms.append(("Q11 令牌数不足必须红", any(k.startswith("A0") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))
    r = _run(_syn_srcs(**{EXTRA_RELS[0]: "/* 没有 transitionTimingFunction */"}))
    arms.append(("Q12 preset 缺缓动表必须红", any(k.startswith("A0") for k, _ in r.fail),
                 f"{[k for k, _ in r.fail]}"))

    # R 组不得进 fail
    r = _run()
    arms.append(("Q13 R 组只报不进 fail", not r.fail and len(r.notes) >= 3,
                 f"fail={len(r.fail)} notes={len(r.notes)}"))
    # 未令牌化的 CSS 原生关键字（`ease-in-out`）**不得**判红，但必须报出来
    arms.append(("Q17 未令牌化缓动只报不判",
                 not any(k.startswith("A2") for k, _ in r.fail)
                 and any("未令牌化" in n for n in r.notes),
                 f"fail={[k for k, _ in r.fail]}"))

    # 棘轮三分支
    kh, nh, ex = split_ratchet([("A1:x", "m")], {"A1:x": "登记"})
    arms.append(("Q14 棘轮内不阻断", len(kh) == 1 and not nh and not ex, f"{len(kh)}/{len(nh)}/{len(ex)}"))
    kh, nh, ex = split_ratchet([("A1:x", "m")], {})
    arms.append(("Q15 棘轮外阻断", len(nh) == 1 and not kh, f"{len(kh)}/{len(nh)}/{len(ex)}"))
    kh, nh, ex = split_ratchet([], {"A1:x": "登记"})
    arms.append(("Q16 豁免过期阻断", ex == ["A1:x"] and not kh and not nh, f"{ex}"))

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
    ap = argparse.ArgumentParser(description="§4.4 动效时长/缓动令牌门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（纯函数）")
    ap.add_argument("--why", action="store_true", help="打印期望值出处")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    spec_p = ROOT / SPEC_REL
    if not spec_p.exists():
        print(f"[ENV] 缺规范文件：{SPEC_REL} ⇒ **本轮结论不可信**（exit 2 = 环境问题）")
        return 2
    section = load_section(spec_p.read_text(encoding="utf-8"), "4.4 动效", "05 责任边界三态系统")
    if section is None:
        print("[ENV] `design-spec.md` 里找不到 `### 4.4 动效` ⇒ 规范结构变了")
        return 2
    rows = parse_rows(section)
    print(f"[ENV] 规范：{SPEC_REL}")
    print(f"[ENV] §4.4 解析出 {len(rows)} 行：{[r[0] for r in rows]}")

    files = dict(iter_sources())
    absent = [k for k in REQUIRED_FILES if k not in files]
    if absent:
        print("[ENV] 下列必需文件未进入扫描集（键约定不一致？）：")
        for k in absent:
            print(f"      {k}")
        print(f"[ENV] 扫描集样例键：{sorted(files)[:3]}")
        print("[ENV] ⇒ **本轮结论不可信**（exit 2 = 工具问题，不是产品缺陷）")
        return 2

    tokens = parse_tokens(files[TOKENS_REL])
    ease_tokens = parse_ease_tokens(files[TOKENS_REL])
    preset = parse_preset(files[EXTRA_RELS[0]])

    if args.why:
        print("\n[W] 期望值出处（一律从 §4.4 正文解析，不硬编码）")
        for sc, dur, ease in rows:
            print(f"      {sc:<24} 时长={dur:<8} 缓动={ease}")
        print("      配对方式：**场景名 ↔ 令牌注释**（不按顺序）")

    print(f"[ENV] 扫描 {len(files)} 个源码文件")

    rep = Report()
    judge_a0(rows, tokens, preset, rep)
    if not rep.fail:
        judge_a1(rows, tokens, rep)
        judge_a2(rows, preset, ease_tokens, rep)
        judge_a3(files, rep)
        judge_a4(files, rep)
    collect_notes(rows, tokens, files, rep)

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
        print("\n  ── ✗ 豁免已过期（判据已不再命中，必须删掉登记）──")
        for key in expired:
            print(f"  ✗ [{key}]")

    print("\n=== 只报不判（需拍板 / 机制说明，**不是判据**） ===")
    for n in rep.notes:
        print(f"  · {n}")

    if new_hits or expired:
        print(f"\n[结论] exit 1 —— 新违反 {len(new_hits)} 条 · 过期豁免 {len(expired)} 条")
        return 1
    print(f"\n[结论] exit 0 —— 已登记欠账 {len(known_hits)} 条（棘轮内），无新违反")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
