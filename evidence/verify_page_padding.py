#!/usr/bin/env python
"""`design-spec.md` §4.2「间距（4pt 网格）」里**页边距**那条要求的**静态地板**。

## 为什么需要它：**§4.2 被记为「已覆盖」，但它有三条要求，只有一条有判据**

`verify_spacing_scale.py` 判的是 §4.2 的**刻度与禁令**（S1 任意值间距必须是令牌引用 /
S2 点名禁止的 5·13·18px / S3·S4 只报）。它**不判**同一节里的另外两条：

| §4.2 的要求 | 出处 | 谁判 |
|---|---|---|
| 刻度 4/8/…/80 + 「禁止 5px/13px/18px」 | `:195` `:201` | `verify_spacing_scale.py` ✅ |
| **桌面页面内边距 24px；移动端 16px** | §4.2「桌面页面内边距 24px；移动端 16px」 | 🚨 **谁都没判**（本门禁补） |
| 栅格 12 列 / gutter 24px / 断点 640·1024·1280·1600 | §4.2「栅格：12 列，gutter 24px」 | 🚨 **谁都没判**（见 R3/R4） |

🚨 **这是覆盖率报表的一个新失真**：报表是**小节级**粒度 ⇒
「§4.2 在 CI 内已覆盖」**掩盖了**「§4.2 里三条要求只有一条有判据」。
同族：失真 ③ 是「**提到** vs **判**」（`§` 字面量在 docstring 里），
本项是它的**粗粒度版本** —— 「**小节**被判过」vs「**小节里每条要求**都被判过」。

## 分工表（**不重叠**）

| 要求 | 谁判 | 在哪 |
|---|---|---|
| §4.2 刻度 / 禁令（5·13·18px） | `verify_spacing_scale.py` | GATED |
| §4.2 **页边距 16/24** | **本门禁** | GATED |
| §4.1 侧栏 240 / 内容最大宽 | `verify_appshell.py` | GATED |
| 侧栏渲染宽 ≈ 令牌 | `verify_breakpoints.py` B2b | CDP，**CI 不跑** |

⚠️ 一节多门禁是**本项目既有形态**：§4.3 也是两条（圆角 → `verify_radius_scale.py` /
焦点环 → `verify_focus_ring.py`）。

## 判据

- **A0 守卫**：从 §4.2 **现读**「桌面页面内边距 Npx；移动端 Mpx」。
  读不出 ⇒ **exit 2** —— 绝不在**空期望值**上「全部通过」。
- **A1 令牌值**：`tokens.css` 的 `--page-pad` == 现读的**移动端**值。
- **A2 要求真的被实现**：`AppShell.tsx` 的**内容区**（`padded &&` 之后那个 className）
  必须同时给出**移动端**水平内边距（无断点前缀）与**桌面**水平内边距（`lg:` 前缀），
  且**解析后的 px 值** == 现读值。
  ⚠️ 只判「令牌存在」是**空判** —— 本项目已有先例（`screens.wide` 是**死配置**、
  §4.1 的 A2 明写「令牌存在但没人用 ⇒ **改令牌等于没改**」）。

## 只报不判（R 组）

- **R1** `--page-pad` 的**消费者数**（实测 **0**）⇒ **死令牌**：改它**不会改渲染**。
  只报，把「让它被用」还是「删掉它」交给裁定（两者都是**设计系统改动**）。
- **R2** `tokens.css` **没有桌面档**页边距令牌（规范要 24px，而 `--page-pad` 的注释里
  只写了「桌面 24px」这句话）⇒ 要令牌化就得**新增一个令牌**（属设计系统改动）。
- **R3** ⚠️ 本门禁**只**引 §4.2 作为出处。**第 8.2 节**的断点表**重复**了同一要求
  （`compact` 行写 16px、`medium`/`expanded` 行写 24px），但那一节还有**未判**的内容
  （断点数字自己矛盾 · 12 栅格未实现）⇒ **本门禁不因此声称覆盖第 8.2 节**。
  ⚠️ R2/R3 的文案**刻意不写 `§` 符号**：覆盖率报表把「判据代码体里的 `§x.y` 字面量」
  算作**覆盖**，在**只报**的文案里写节号会造出**假覆盖**（`evidence/README.md` 坑 38）。
- **R4** §4.2 的「栅格 12 列 / gutter 24px」**零判据**；实测全仓 `grid-cols-12` **0 处**
  ⇒ 至少「12 列」这一半是**未实现**（功能缺口，不是覆盖率缺口）。只报。

用法：

    python evidence/verify_page_padding.py              # 0 通过 / 1 产品缺陷 / 2 环境问题
    python evidence/verify_page_padding.py --self-test  # 纯合成夹具自检（不读真实仓库）
    python evidence/verify_page_padding.py --inject     # 真仓库**内存**注入反证（不碰产品文件）
    python evidence/verify_page_padding.py --why 4.2    # 打印出处与归属
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
SHELL_REL = "frontend/packages/ui/src/components/AppShell.tsx"
REQUIRED_FILES = (SPEC_REL, TOKENS_REL, PRESET_REL, SHELL_REL)

# 扫消费者时的两棵根 + 必须剪掉的目录（⚠️ `verify_design_tokens.py` 的 rglob 曾挂死 160s）
SCAN_ROOTS = ("frontend/packages", "frontend/apps")
SCAN_SKIP_DIRS = {".next", ".next_old_v14_keep", "node_modules", "dist", ".turbo", "out"}
SCAN_EXTS = {".ts", ".tsx", ".css", ".js", ".jsx", ".mjs"}


class SpecUnreadable(Exception):
    """规范读不出期望值 ⇒ **环境问题（exit 2）**，不是产品缺陷。"""


# ── 注释剥离（剥注释必须在**判据函数内部**，否则自检测不到真实形态）──────────────
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?<![:\w])//[^\n]*")


def strip_comments(text: str) -> str:
    r"""块注释换成**等量换行**（保行号），行注释用 `(?<![:\w])` 避开 `https://`。"""
    text = BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return LINE_COMMENT_RE.sub("", text)


# ── 源码解析 ────────────────────────────────────────────────────────────────
CSS_VAR_RE = re.compile(r"--([\w-]+)\s*:\s*([^;]+);")


def css_vars(src: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in CSS_VAR_RE.finditer(src)}


def px_int(value: str | None) -> int | None:
    if value is None:
        return None
    m = re.fullmatch(r"(\d+)px", value.strip())
    return int(m.group(1)) if m else None


def first_string_literal_after(src: str, pos: int) -> str | None:
    """取 `pos` 之后**第一个**字符串字面量（不含换行内的引号）。"""
    m = re.search(r'"([^"\n]*)"' + r"|'([^'\n]*)'", src[pos:])
    if m is None:
        return None
    return next(g for g in m.groups() if g is not None)


# ── Tailwind 间距刻度：`px-<n>` 解析成 px ────────────────────────────────────
SPACING_KEY_RE = re.compile(r"(?m)^\s*([\w.-]+)\s*:\s*\"([^\"]+)\"\s*,?\s*$")
# Tailwind 默认：`n` ⇒ `n × 0.25rem` = `n × 4px`（`0.5` 这类半档同理）
REM_RE = re.compile(r"(\d+(?:\.\d+)?)rem")


def spacing_scale(preset_src: str, tokens: dict[str, str]) -> dict[str, int]:
    """preset 的 `spacing` 段里**显式**声明的键 ⇒ px；没有的键走 Tailwind 默认（n×4）。

    ⚠️ 本项目实测 preset 的 `spacing` 段**只有命名键**（`sidebar` / `topbar` / …，
       全是 `var(--…)`），**没有数字键** ⇒ `px-4` / `px-6` 走默认刻度。
       但**不能硬编码** `n × 4`：哪天有人加了 `4: "20px"`，判据必须跟着变。
    """
    m = re.search(r"(?m)^\s*spacing\s*:\s*\{", preset_src)
    if m is None:
        return {}
    i = preset_src.index("{", m.start())
    depth = 0
    end = None
    for j in range(i, len(preset_src)):
        if preset_src[j] == "{":
            depth += 1
        elif preset_src[j] == "}":
            depth -= 1
            if depth == 0:
                end = j
                break
    if end is None:
        return {}
    out: dict[str, int] = {}
    for km in SPACING_KEY_RE.finditer(preset_src[i + 1: end]):
        key, raw = km.group(1), km.group(2).strip()
        v = px_int(raw)
        if v is not None:
            out[key] = v
            continue
        vm = re.fullmatch(r"var\(--([\w-]+)\)", raw)
        if vm is not None:
            tv = px_int(tokens.get(vm.group(1)))
            if tv is not None:
                out[key] = tv
            continue
        rm = REM_RE.fullmatch(raw)
        if rm is not None:
            out[key] = int(round(float(rm.group(1)) * 16))
    return out


def resolve_len(raw: str, scale: dict[str, int], tokens: dict[str, str]) -> int | None:
    """把 `px-` 后面的那个值解析成 px：命名刻度 / 任意值令牌 / 任意值 px。"""
    m = re.fullmatch(r"\[var\(--([\w-]+)\)\]", raw)
    if m is not None:
        return px_int(tokens.get(m.group(1)))
    m = re.fullmatch(r"\[(\d+)px\]", raw)
    if m is not None:
        return int(m.group(1))
    if raw in scale:
        return scale[raw]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", raw)
    if m is not None:
        return int(round(float(m.group(1)) * 4))  # Tailwind 默认刻度 n × 4px
    return None


# ── 内容区的内边距类解析 ────────────────────────────────────────────────────
TOKEN_RE = re.compile(r"^(?:(?P<bp>[a-z]+):)?(?P<prop>p[xytrbl]?)-(?P<val>.+)$")


def horizontal_padding(lit: str, scale: dict[str, int], tokens: dict[str, str]) -> dict[str, list[int]]:
    """从 className 字面量里取**水平**内边距，按断点前缀分桶（`""` = 移动端）。"""
    out: dict[str, list[int]] = {}
    for tok in lit.split():
        m = TOKEN_RE.match(tok)
        if m is None or m.group("prop") not in ("p", "px"):
            continue
        px = resolve_len(m.group("val"), scale, tokens)
        if px is None:
            continue
        out.setdefault(m.group("bp") or "", []).append(px)
    return out


# ── 规范解析 ────────────────────────────────────────────────────────────────
SEC_42_START = r"^###\s*4\.2\s"
SEC_42_END = r"^###\s*4\.3\s"

PAGE_PAD_LINE_RE = re.compile(r"(?m)^.*桌面页面内边距.*$")
DESKTOP_PX_RE = re.compile(r"桌面页面内边距\s*\*{0,2}\s*(\d+)\s*\*{0,2}\s*px")
MOBILE_PX_RE = re.compile(r"移动端\s*\*{0,2}\s*(\d+)\s*\*{0,2}\s*px")


class Expected(NamedTuple):
    desktop_px: int
    mobile_px: int


def read_expected(spec_text: str) -> Expected:
    m = re.search(SEC_42_START, spec_text, re.M)
    if m is None:
        raise SpecUnreadable(f"规范里找不到小节标题 `{SEC_42_START}`")
    tail = spec_text[m.start():]
    e = re.search(SEC_42_END, tail, re.M)
    sec = tail[: e.start()] if e else tail

    line_m = PAGE_PAD_LINE_RE.search(sec)
    if line_m is None:
        raise SpecUnreadable("§4.2 里读不出页边距那一行（含「桌面页面内边距」）")
    line = line_m.group(0)
    dm = DESKTOP_PX_RE.search(line)
    if dm is None:
        raise SpecUnreadable("§4.2 的页边距行里读不出「桌面页面内边距 Npx」")
    mm = MOBILE_PX_RE.search(line)
    if mm is None:
        raise SpecUnreadable("§4.2 的页边距行里读不出「移动端 Mpx」")
    return Expected(int(dm.group(1)), int(mm.group(1)))


# ── 判据 ────────────────────────────────────────────────────────────────────
def count_consumers(needle: str) -> int:
    """`needle` 在 `frontend/` 两棵树里的**非定义处**出现次数。

    ⚠️ 必须剪枝：`verify_design_tokens.py` 的 `rglob` 曾因不剪 `.next` 挂死 160s。
    """
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
                p = pathlib.Path(dirpath) / fn
                if str(p.relative_to(ROOT)).replace("\\", "/") == TOKENS_REL:
                    continue  # 定义处不算消费者
                try:
                    body = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                n += body.count(needle)
    return n


def evaluate(
    spec_text: str,
    tokens_src: str,
    preset_src: str,
    shell_src: str,
    exists=None,
    consumers=None,
) -> tuple[int, list[str], list[str]]:
    """**纯函数**：返回 `(rc, defects, reports)`。

    `exists` / `consumers` 可注入 —— 真仓库里四个文件都在、`--page-pad` 真的零消费者，
    不注入的话这两条就**永远只会走同一个分支**（自检测不到另一条）。
    """
    exists = exists or (lambda rel: (ROOT / rel).exists())
    consumers = consumers if consumers is not None else (lambda s: count_consumers(s))
    tokens_src = strip_comments(tokens_src)
    preset_src = strip_comments(preset_src)
    shell_src = strip_comments(shell_src)

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
        f"规范 §4.2 现读：桌面页面内边距 {exp.desktop_px}px · 移动端 {exp.mobile_px}px"
    )

    tokens = css_vars(tokens_src)
    scale = spacing_scale(preset_src, tokens)
    reports.append(
        "Tailwind 间距刻度：preset 的 `spacing` 段显式声明 "
        f"{len(scale)} 个键（{' / '.join(sorted(scale)) if scale else '（无）'}）；"
        "其余走默认 `n × 4px`"
    )

    # ── A1 令牌值 ──
    got = px_int(tokens.get("page-pad"))
    if got is None:
        defects.append(f"`tokens.css` 里没有可解析的 `--page-pad`（§4.2 要求移动端 {exp.mobile_px}px）")
    elif got != exp.mobile_px:
        defects.append(f"`--page-pad` = **{got}px**，而 §4.2 要求移动端 **{exp.mobile_px}px**")

    # ── A2 要求真的被实现（内容区同时给出移动端与桌面水平内边距）──
    # 🚨 锚点唯一性：`padded &&` 实测只出现 1 次（`padded?: boolean` 与 `padded = true`
    #    都不含 `&&`）。语义同 §4.1 门禁的教训：
    #    **0 处 = 产品缺陷（rc 1）**、**>1 处 = 我没法定位（exit 2）**。
    anchor = "padded &&"
    n_anchor = shell_src.count(anchor)
    if n_anchor != 1:
        return 2, [
            f"【环境】`{anchor}` 出现 {n_anchor} 次（期望 1）⇒ 内容区锚点失效，"
            "A2 会**静默空转**（先修本门禁的锚点，别把它当产品缺陷）"
        ], reports
    lit = first_string_literal_after(shell_src, shell_src.index(anchor) + len(anchor))
    if lit is None:
        return 2, [
            "【环境】`padded &&` 之后取不到字符串字面量 ⇒ 内容区锚点失效，"
            "A2 会**静默空转**（先修本门禁的锚点，别把它当产品缺陷）"
        ], reports

    pads = horizontal_padding(lit, scale, tokens)
    mobile = pads.get("", [])
    desktop = pads.get("lg", [])
    reports.append(
        f"内容区字面量：{lit!r} ⇒ 移动端水平内边距 {mobile or '（无）'} · "
        f"`lg:` 断点 {desktop or '（无）'}"
    )
    # ⚠️ 三条语义分开（`methodology.md` #180）：**没有这个类** = 产品缺陷（rc 1）；
    #    锚点问题 = exit 2（上面已拦）。**不能**用「取不到」把两者糊成一句。
    if not mobile:
        defects.append(
            "内容区**没有移动端水平内边距**（`px-*` / `p-*`，无断点前缀）⇒ "
            f"§4.2 的移动端 {exp.mobile_px}px 页边距没实现"
        )
    elif exp.mobile_px not in mobile:
        defects.append(
            f"内容区移动端水平内边距实测 **{mobile}px**，而 §4.2 要求 **{exp.mobile_px}px**"
        )
    if not desktop:
        defects.append(
            "内容区**没有 `lg:` 断点的水平内边距** ⇒ §4.2 的桌面 "
            f"{exp.desktop_px}px 页边距没实现（移动端值会一路带到桌面）"
        )
    elif exp.desktop_px not in desktop:
        defects.append(
            f"内容区 `lg:` 水平内边距实测 **{desktop}px**，而 §4.2 要求 **{exp.desktop_px}px**"
        )

    # ── R 组：只报不判 ──
    n_cons = consumers("var(--page-pad)")
    reports.append(
        f"R1 `--page-pad` 的消费者数 = **{n_cons}**"
        + (
            " ⇒ 🚨 **死令牌**：改它**不会改渲染**（页边距实际由内容区的 `px-*` 类决定）"
            " —— 让它被用 / 删掉它，两者都是**设计系统改动**，待裁定"
            if n_cons == 0
            else "（已被引用）"
        )
    )
    # 🚨 这里原来写的是 `any(...)` —— 它返回 **bool**，于是下面 `f"--{has_desktop_token}"`
    #    会打出 `--True`。**此前没有桌面令牌 ⇒ 这个分支从未被执行 ⇒ 那句错话一直没人看见**。
    #    2026-09-23 接线 `--page-pad-desktop` 时第一次走到这个分支才暴露（`methodology.md` #197）。
    has_desktop_token = next(
        (k for k in tokens if re.fullmatch(r"page-pad-(desktop|lg|desktop-pad)", k)), None
    )
    reports.append(
        "R2 桌面档页边距令牌："
        + (
            f"在（`--{has_desktop_token}`）"
            if has_desktop_token
            else f"**不在** —— 规范要 {exp.desktop_px}px，而 `tokens.css` 里只有 `--page-pad`"
            "（注释里写着「桌面 24px」**这句话**）⇒ 要令牌化就得**新增一个令牌**"
        )
    )
    reports.append(
        "R3 ⚠️ 本门禁**只**引 §4.2 作为出处。**第 8.2 节**的断点表重复了同一要求"
        "（compact 行写 16px、medium / expanded 行写 24px），"
        "但那一节还有**未判**的内容（断点数字自己矛盾 · 12 栅格未实现）"
        "⇒ **本门禁不因此声称覆盖第 8.2 节**"
    )
    reports.append(
        "R4 §4.2 的「栅格 12 列 / gutter 24px」**零判据**；实测全仓 `grid-cols-12` **0 处**"
        "⇒ 至少「12 列」这一半是**未实现**（功能缺口，不是覆盖率缺口）"
    )

    return (1 if defects else 0), defects, reports


def read_text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


# ── 合成夹具（自检用；**不读真实仓库**）─────────────────────────────────────
SYN_SPEC = """\
## 04 布局 · 间距 · 圆角 · 阴影

### 4.1 AppShell（桌面形态）

左侧 **240px** 墨色导航。

### 4.2 间距（4pt 网格）

刻度：4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80

- 桌面页面内边距 24px；**移动端 16px**
- 卡片内边距：16px（紧凑）/ 20px（常规）/ 24px（宽松）
- 禁止出现 5px / 13px / 18px 等游离值

栅格：12 列，gutter 24px，断点 640 / 1024 / 1280 / 1600。

### 4.3 圆角 / 阴影 / 边框

`--r1` …
"""

SYN_TOKENS = """\
:root {
  --sidebar-w: 240px;
  --page-pad: 16px; /* 移动端页边距（桌面 24px） */
}
"""

SYN_PRESET = """\
export const preset = {
  theme: {
    extend: {
      spacing: {
        sidebar: "var(--sidebar-w)",
        topbar: "var(--topbar-h)",
      },
    },
  },
};
"""

SYN_SHELL = """\
export const AppShell = ({ padded = true }) => (
  <div className={cn("flex-1", padded && "px-4 py-4 lg:px-6 lg:py-6")} />
);
"""


def _with(
    spec: str = SYN_SPEC,
    tokens: str = SYN_TOKENS,
    preset: str = SYN_PRESET,
    shell: str = SYN_SHELL,
    consumers=None,
):
    return evaluate(
        spec,
        tokens,
        preset,
        shell,
        exists=lambda rel: True,
        consumers=consumers if consumers is not None else (lambda s: 0),
    )


def self_test() -> int:
    arms: list[tuple[str, int, list[str]]] = []

    def arm(name: str, result: tuple[int, list[str], list[str]], want_rc: int, must: str = "") -> None:
        rc, defects, _ = result
        ok = rc == want_rc and (must in " ".join(defects) if must else True)
        arms.append((name, 0 if ok else 1, [f"期望 rc={want_rc}，实测 rc={rc}", *defects[:1]]))

    # Q1–Q4 A0 守卫
    arm("Q1 规范缺页边距行 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("- 桌面页面内边距 24px；**移动端 16px**\n", "")), 2, "读不出")
    arm("Q2 规范缺「移动端 Mpx」⇒ exit 2",
        _with(spec=SYN_SPEC.replace("**移动端 16px**", "移动端另行约定")), 2, "移动端")
    arm("Q3 规范缺小节标题 ⇒ exit 2",
        _with(spec=SYN_SPEC.replace("### 4.2 间距（4pt 网格）", "### 42 间距")), 2, "找不到小节标题")
    arm("Q4 必需文件取不到 ⇒ exit 2",
        evaluate(SYN_SPEC, SYN_TOKENS, SYN_PRESET, SYN_SHELL,
                 exists=lambda rel: rel != TOKENS_REL, consumers=lambda s: 0),
        2, "必需文件取不到")

    # Q5 控制组
    arm("Q5 全部对齐 ⇒ 绿（控制组）", _with(), 0)

    # Q6–Q8 A1
    arm("Q6 `--page-pad: 20px` ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("--page-pad: 16px", "--page-pad: 20px")), 1, "--page-pad")
    arm("Q7 `--page-pad` 整个缺失 ⇒ 红",
        _with(tokens=SYN_TOKENS.replace("  --page-pad: 16px; /* 移动端页边距（桌面 24px） */\n", "")),
        1, "没有可解析")
    arm("Q8 令牌值写成 `1rem` ⇒ 红（判据只认 px）",
        _with(tokens=SYN_TOKENS.replace("--page-pad: 16px", "--page-pad: 1rem")), 1, "没有可解析")

    # Q9–Q13 A2（含锚点守卫）
    arm("Q9 内容区移动端改成 `px-2`（=8px）⇒ 红",
        _with(shell=SYN_SHELL.replace("px-4 py-4", "px-2 py-4")), 1, "移动端水平内边距")
    arm("Q10 内容区 `lg:px-6` 改成 `lg:px-4`（=16px）⇒ 红",
        _with(shell=SYN_SHELL.replace("lg:px-6", "lg:px-4")), 1, "lg:")
    arm("Q11 内容区丢掉全部水平内边距（只留 `py-4`）⇒ 红",
        _with(shell=SYN_SHELL.replace('"px-4 py-4 lg:px-6 lg:py-6"', '"py-4 lg:py-6"')), 1, "没有移动端水平内边距")
    arm("Q12 内容区丢掉 `lg:` 那一半 ⇒ 红（移动端值会带到桌面）",
        _with(shell=SYN_SHELL.replace('"px-4 py-4 lg:px-6 lg:py-6"', '"px-4 py-4"')), 1, "lg:")
    arm("Q13 `padded &&` 锚点 0 处 ⇒ exit 2（仪器前提失效）",
        _with(shell=SYN_SHELL.replace("padded &&", "pad &&")), 2, "锚点失效")
    arm("Q13b `padded &&` 锚点 2 处 ⇒ exit 2（我没法定位）",
        _with(shell=SYN_SHELL.replace("padded = true", "padded = true && 1")
              .replace('"flex-1"', 'padded && "x", "flex-1"')), 2, "锚点失效")
    arm("Q13c `padded &&` 之后没有字符串字面量 ⇒ exit 2",
        _with(shell=SYN_SHELL.replace('padded && "px-4 py-4 lg:px-6 lg:py-6"', "padded && undefined")),
        2, "取不到字符串字面量")

    # Q14 注释剥离对照（**关键的假阳性对照**）
    # ⚠️ 光在文件里放个含 `px-2` 的注释**测不出东西**（注释本来就不是字符串字面量）。
    #    要让它有判别力，必须把那段**带引号**的坏值放在**锚点与真字面量之间** ——
    #    不剥注释 ⇒ 「第一个字面量」变成注释里那个 ⇒ **假红**；剥了 ⇒ 绿。
    arm("Q14 锚点后紧邻一段含 `\"px-2\"` 的注释 ⇒ 必须绿（剥注释）",
        _with(shell=SYN_SHELL.replace(
            'padded && "px-4',
            'padded && /* 反面教材 "px-2 py-2 lg:px-2 lg:py-2" */ "px-4')),
        0)

    # Q15 令牌路径（任意值 + 令牌）也认
    arm("Q15 内容区改用 `px-[var(--page-pad)]` + `lg:px-[24px]` ⇒ 绿（不逼死令牌化）",
        _with(shell=SYN_SHELL.replace('"px-4 py-4 lg:px-6 lg:py-6"',
                                      '"px-[var(--page-pad)] py-4 lg:px-[24px] lg:py-6"')),
        0)

    # Q16 刻度不得硬编码：preset 覆盖 `spacing.4` 后，同一份夹具必须红
    arm("Q16 preset 把 `spacing.4` 改成 `20px` ⇒ 同一个 `px-4` **必须**红（证明刻度从 preset 现读）",
        _with(preset=SYN_PRESET.replace('sidebar: "var(--sidebar-w)",',
                                        'sidebar: "var(--sidebar-w)",\n        4: "20px",')),
        1, "移动端水平内边距")

    # Q17 期望值从规范现读（一对臂）
    arm("Q17a 规范改成 12/8，而代码仍是 16/24 ⇒ 红",
        _with(spec=SYN_SPEC.replace("桌面页面内边距 24px；**移动端 16px**",
                                    "桌面页面内边距 12px；**移动端 8px**")),
        1, "12")
    arm("Q17b 规范改成 12/8 **且**代码跟着改 ⇒ 绿（证明值来自规范）",
        _with(spec=SYN_SPEC.replace("桌面页面内边距 24px；**移动端 16px**",
                                    "桌面页面内边距 12px；**移动端 8px**"),
              tokens=SYN_TOKENS.replace("--page-pad: 16px", "--page-pad: 8px"),
              shell=SYN_SHELL.replace('"px-4 py-4 lg:px-6 lg:py-6"', '"px-2 py-2 lg:px-3 lg:py-3"')),
        0)

    # Q18 R 组只报不判：消费者为 0（死令牌）**不得**红
    arm("Q18 `--page-pad` 零消费者（死令牌）⇒ 仍绿（R1 只报不判）",
        _with(consumers=lambda s: 0), 0)
    arm("Q18b 消费者 > 0 ⇒ 也绿（两条分支都不判）",
        _with(consumers=lambda s: 7), 0)

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
    shell = read_text(SHELL_REL)
    # ⚠️ 消费者计数**只走盘一次**（R1 只报不判，注入臂里不需要逐条重算）
    cons = count_consumers("var(--page-pad)")
    consumers = lambda _s: cons  # noqa: E731

    cases: list[tuple[str, str, str, str, str, int]] = [
        ("I0 控制组：一个字节都不改 ⇒ 必须绿", spec, tokens, preset, shell, 0),
        ("I1 `--page-pad: 16px` → 20px ⇒ A1/A2 红（令牌漂移必须被逮住）", spec,
         replace_once(tokens, "--page-pad: 16px;", "--page-pad: 20px;"), preset, shell, 1),
        ("I2 内容区移动端值 → 8px（`p-[var(--page-pad)]` → `p-[8px]`）⇒ A2 红",
         spec, tokens, preset,
         replace_once(shell, "p-[var(--page-pad)]", "p-[8px]"), 1),
        ("I3 内容区桌面值 → 16px（`lg:p-[var(--page-pad-desktop)]` → `lg:p-[16px]`）⇒ A2 红",
         spec, tokens, preset,
         replace_once(shell, "lg:p-[var(--page-pad-desktop)]", "lg:p-[16px]"), 1),
        ("I4 内容区丢掉全部水平内边距 ⇒ A2 红", spec, tokens, preset,
         replace_once(shell, '"p-[var(--page-pad)] lg:p-[var(--page-pad-desktop)]"',
                      '"py-4 lg:py-6"'), 1),
        # ⚠️ 2026-09-23：本条**方向反了**。原先产品侧用字面量、臂注入令牌化形式（证明「不逼死令牌化」）；
        #    接线 `--page-pad` 之后产品侧**本来就是**令牌化形式，于是改为注入**字面量**形式，
        #    继续证明同一件事：**两种写法都必须绿**（判据只认解析后的 px，不认写法）。
        ("I5 内容区改回字面量 `px-4 py-4 lg:px-6 lg:py-6`（**同一个值**）⇒ 必须绿",
         spec, tokens, preset,
         replace_once(shell, '"p-[var(--page-pad)] lg:p-[var(--page-pad-desktop)]"',
                      '"px-4 py-4 lg:px-6 lg:py-6"'), 0),
    ]

    bad = 0
    for name, s, t, p, sh, want in cases:
        try:
            rc, defects, _ = evaluate(s, t, p, sh, consumers=consumers)
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
    "4.2": (
        "§4.2「间距（4pt 网格）」里的**页边距**那条 —— 出处 `deliverables/ui-design/design-spec.md`\n"
        "  的 `### 4.2` 小节，原文「- 桌面页面内边距 24px；**移动端 16px**」。\n"
        "  归属：**本门禁**判页边距（令牌值 + 内容区真的实现）；\n"
        "  同节的**刻度与禁令**由 `verify_spacing_scale.py` 判（S1/S2/S3/S4）—— 不重叠。\n"
        "  ⚠️ 同节的「栅格 12 列 / gutter 24px / 断点 640·1024·1280·1600」**零判据**，\n"
        "     且「12 列」实测未实现（R4）；断点数字在第 8.2 节里**自己矛盾**，待裁定。"
    ),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="design-spec §4.2 页边距静态地板")
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
        shell = read_text(SHELL_REL)
    except OSError as exc:
        print(f"【环境】取不到必需文件：{exc}")
        return 2

    rc, defects, reports = evaluate(spec, tokens, preset, shell)
    for r in reports:
        print(f"  · {r}")
    if defects:
        print()
        for d in defects:
            print(f"  ✗ {d}")
        print(f"\n❌ §4.2 页边距：**{len(defects)} 条欠账**")
        return rc
    print("\n✅ §4.2 页边距：**0 欠账**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
