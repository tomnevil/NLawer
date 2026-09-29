#!/usr/bin/env python3
"""§3.3 中文排印三条硬规则 —— 可重跑门禁（第五个维度）。

## 为什么要有这条

`deliverables/ui-design/design-spec.md` §3.3 的标题是全文**最强的出处措辞**：
「中文排印**三条硬规则**」，前两条都带明确数字（字重 ∈ {400,500,600}、正文行高 ≥ 1.65）。
但 `evidence/` 下 19 个门禁里**一条都没判它**——
`grep -rl 'font-weight|fontWeight|line-height|行高' evidence/*.py` 只命中三个脚本
（`verify_contrast.py` / `verify_mobile_actionbar.py` / `verify_tap_targets.py`），
且都不是判这条规则（`verify_contrast.py` 只是**用**字号去选对比度门槛）。

⇒ 一个带数字、带「硬规则」措辞的规范条款，**零判据**。

## 出处（不自己发明期望值）

| 判据 | 期望 | 出处 |
|---|---|---|
| 字重 | ∈ {400, 500, 600} | §3.3 规则 1（理由：中文 700 在 Windows 上渲染为合成粗体） |
| 正文行高 | ≥ 1.65 | §3.3 规则 2（理由：中文方块字密度高） |
| 「正文」指哪几档 | `body-sm`(13/1.65) / `body`(14/1.7) / `body-lg`(15/1.85) | §3.2「用途」列：`body` 标「**基准**：正文、对话气泡、描述」 |
| 规则 2 **已被实现** | `body{line-height:1.7}`，且注释原文引用规则 | `frontend/apps/*/app/globals.css:36-37` |

`LH_FLOOR = 1.65` **不是我选的门槛**：它同时是 §3.3 的数字**和** §3.2 里 `body-sm` 的声明行高
——即正文阶梯的**地板**。三档正文 1.65 / 1.7 / 1.85 **全部 ≥ 它** ⇒ 门槛自洽，不是我调的参数。

## 为什么必须分「源码层 / 产物层 / 渲染层」

1. **源码层**：字重工具类、preset 声明 —— 纯文本可判。
2. **产物层**：行高覆盖类（`leading-*`）的**实际值只有编译后才确定**。
   `leading-relaxed` → **1.625**，来自 Tailwind 默认值；本项目 preset **没有**覆写 `lineHeight`
   ⇒ 谁也不知道它是 1.625（而不是 1.75），除非去读产物 CSS。
3. **渲染层**：🚨 **`cn()`（clsx + tailwind-merge）会在源码与 DOM 之间删掉类**
   —— 修好之前，`text-body-sm` 与 `text-<颜色>` 被 twMerge 判为**同一组**，后者在后 ⇒ 后者赢
   ⇒ **字号类消失、字号回落到继承值**。
   ⇒ 只看源码会**高估**（以为字号生效了）；只看渲染会**漏掉「为什么」**。
   ✅ **2026-09-24 已在 `cn.ts` 修掉**（`extendTailwindMerge` 登记语义字号档）。
   本判据**现读项目自己的 merge 配置**（`cn.ts` 的 `FONT_SIZE_TOKENS`）—— 若继续用
   库默认配置，则 `cn.ts` 修好之后判据**照红**（永久假红），见 `cn_font_size_tokens`。

## 判据表

| 编号 | 层 | 检查 | 期望 |
|---|---|---|---|
| T1  | 源码 | 字重工具类 | 只用 `font-normal`/`font-medium`/`font-semibold` |
| T1b | 源码 | `tailwind.preset.ts` 的 `fontWeight` 声明 | ∈ {400, 500, 600} |
| T1c | 源码 | 任意值字重（`font-[…]` / 内联 `fontWeight:`） | **0** |
| T2  | 源码×产物 | 正文档字号类 + `leading-*` 覆盖 ⇒ 实际行高比 | **≥ 1.65** |
| T2b | 源码 | `cn()` 吞掉自定义字号类（附带检出，见下） | **0 处** |
| T2c | 源码 | preset 的**每一个**字号档在项目 merge 配置下是否存活（**实跑探针**） | **0 档被吞** |
| T3  | 渲染 | 正文档文本元素实测 `lineHeight / fontSize` | **≥ 1.65** |
| T3b | 渲染 | 运行时类含 `text-<档>` ⇒ 计算字号必须等于该档 px | 一致（**测的不是 T2b**，见下） |
| T4  | 源码 | §3.3 规则 3（中英混排 **0.25em** 间隙） | ① `html .cjk-gap` 的 `margin-inline` **== §3.3 现读值**（**判**）· ② 调用点数（**只报**） |

> **T4 的 2026-09-24 升级（此前是「只报不判」）**：
> 升级前 T4 **只数 `cjk-gap` 的调用点**，一句断言都没有 —— 而「只报不判的报告行
> 永远不会因为错而变红」（见 `evidence/README.md` 坑 58 / `methodology` #184）。
> 覆盖率审计第二遍把它列为**真·零判据**，现在补上：
>
> - **判**：`styles.css` 里 `html .cjk-gap { margin-inline: … }` 的值
>   必须 **== 规范 §3.3 现读的那个 em 值**（与 `verify_page_padding.py` 的 A1 同形：
>   **令牌/钩子值必须等于规范现读值**）⇒ 改规范不改 CSS、或改 CSS 不改规范，**都会红**。
> - **仍然只报**：`cjk-gap` 的**调用点数**。那是「用没用」的**覆盖率**信号 ——
>   哪些中英混排处**该**加间隙需要**语义**判断，静态数不出来（同族：
>   「每屏 ≤ 1 个主色按钮」这类**渲染期概念**，见 `methodology` 里那条「可静态近似要当场实测」）。
>   ⇒ 这两件事**必须分开写**：一个是判据、一个是信号。
>
> ⚠️ **期望值的出处是规范文件本身**（`SPEC_MD`，现读），**不是硬编码的 `0.25em`** ——
> 硬编码会让「规范改了、CSS 没改」**静默通过**（这正是 ⑦ `--page-pad` 那次的教训）。
> ⚠️ 因此新增了**规范锚点读不到 ⇒ exit 2** 的分支：读不到期望值 = **我没测成**，
> **不是**产品缺陷（`methodology` 里「产品坏了 vs 我没测成」必须分开报）。

> **T2b 的出处说明**：它严格讲属 §3.2 字号阶梯（「声明的字号类必须生效」），
> 不属 §3.3。放在这里是因为它**与 T2 共用同一套机制**（源码扫描 + 真实 twMerge），
> 且**同一个 `cn()` 地雷**同时制造两类症状：字号被吞、行高被压。
> 拆成两个脚本会让这套机制被抄两遍（见 `evidence/README.md` 坑 28 的教训）。

> 🚨 **T2b 在 DOM 里不可见**：类被 `cn()` 删掉之后，运行时 `class` 属性里**没有痕迹**
> —— 没有任何办法从渲染结果反推出「这里本该有个 `text-body-sm`」。
> 所以 T2b **只能**在源码层判（源码 + 真实 twMerge），渲染层**不可能**有对应判据。
> 别指望「跑一遍浏览器就发现了」。（初版 T3b 就是这么写错的，见坑 29。）

> 🚨 **T2b 是「T2 变瞎」报警器，不是「类少了」计数器**（2026-09-24 修正）：
> `text-body-sm` 从合并结果里消失有**两种**成因 ——
> (a) **被吞** ⇒ 字号**回落到继承值**，且 **T2 同时瞎了**（`body_here` 为空 ⇒ 不判）；
> (b) **被同组另一个字号类覆盖**（`text-[11px]` / `text-label`）⇒ 字号**没回落**，
>     T2 也照样判 ⇒ **正常写法**。
> 修正前只按「输入有、合并后无」判 ⇒ 把 (b) 也报成缺陷（实测 `DataTable.tsx:628`
> 数字列刻意用 `text-[11px]` 覆盖 `text-body-sm`，**修前修后都在报**，是稳定假红）。
> 现按 `_font_size_tokens` 分流；(b) 走 `notes`（**留痕**，不静默丢）。
>
> 🚨 **T2b 的判据依赖 `cn.ts`，因此判据本身也必须能表达「我读不到」**：
> `cn_font_size_tokens()` 读不到 `FONT_SIZE_TOKENS` ⇒ 进 `env` 通道 ⇒ **exit 2**。
> 否则「`cn.ts` 被改坏 / 改名」会让 T2b 静默变成 0 条（**永久绿**）。

## 退出码

`0` 通过 · `1` 产品缺陷 · `2` 环境问题（读不到产物 CSS / 覆盖率 0 / twMerge 不可用 /
读不到 `cn.ts` 的 merge 配置）

用法：
    python evidence/verify_typography.py                 # 全跑（含浏览器）
    python evidence/verify_typography.py --source-only    # T1/T1b/T1c/T2/T2b/T4（不开浏览器）
    python evidence/verify_typography.py --self-test      # 证明每条判据都会红
    python evidence/verify_typography.py --dump           # 打原始数值，不下判断
    python evidence/verify_typography.py --why 文本        # 取证：打印该元素的类与计算行高
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0] / "backend"))

from cdp import Browser  # noqa: E402

ROOT = HERE.parents[0]
FRONTEND = ROOT / "frontend"
SPEC_MD = ROOT / "deliverables" / "ui-design" / "design-spec.md"
PRESET_TS = FRONTEND / "tailwind.preset.ts"
STYLES_CSS = FRONTEND / "packages" / "ui" / "src" / "styles.css"
# 🚨 `cn()` 的**唯一事实来源**。T2/T2b 判的必须是**这个文件**的行为，
#    不是 tailwind-merge 的库默认行为 —— 否则修好 `cn.ts` 判据照红（永久假红）。
CN_TS = FRONTEND / "packages" / "ui" / "src" / "lib" / "cn.ts"

# §3.3 规则 1
WEIGHT_OK = ("font-normal", "font-medium", "font-semibold")
WEIGHT_BAD = ("font-thin", "font-extralight", "font-light",
              "font-bold", "font-extrabold", "font-black")
WEIGHT_NUM_OK = {400, 500, 600}

# §3.2「用途」列里的正文档（`body` 标「基准：正文、对话气泡、描述」）
BODY_SIZES = ("body-sm", "body", "body-lg")
LH_FLOOR = 1.65            # §3.3 规则 2，同时也是 §3.2 里 `body-sm` 的声明行高（正文阶梯地板）
# 🚨 **2026-09-24 删除 `CUSTOM_SIZES`（原 10 个语义档的硬编码元组）。**
#    理由：字号档的**真源是 `tailwind.preset.ts`**，判据里再留一份手写清单 =
#    坑 63「判据不该有第二份期望值来源」。两份清单之间没有机械校验 ⇒
#    **preset 新增一个档，判据静默不覆盖**（`cn()` 照吞、字号回落、四条判据全绿）。
#    现改由 `parse_preset_sizes()` 直接派生，见 `judge_size_registration`（T2c）。

# T3：正文候选的判据（**按计算字号**，不按类名 —— 类名会被 `cn()` 删掉）
T3_FS_LO, T3_FS_HI = 13.0, 16.0     # 正文三档 13/14/15 + 继承默认 16
T3_MIN_CHARS = 8                     # 少于它 ⇒ 短标签/数字，不当正文
T3_MAX_WEIGHT = 500                  # 标题档字重 600 ⇒ 排除

# 页表照搬 `verify_runtime_health.py`（那是实测过可达的清单，不自己另发明一套）。
# `@first-case` 是占位符，运行时从 `/cases` 的 DOM 读真实 id（见 `resolve_path`）。
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
# 源码扫描：className 取值块（支持多行 / cn() 多参数）
# ---------------------------------------------------------------------------
ATTR_RE = re.compile(r"className\s*=\s*")
LIT_RE = re.compile(r'"([^"\n]*)"' + r"|'([^'\n]*)'")
CN_RE = re.compile(r"\bcn\s*\(")


def attr_blocks(text: str) -> list[tuple[int, list[str], bool]]:
    """产出每个 `className=` 的取值块 → `(行号, 字符串字面量列表, 是否走 cn())`。

    - `className="a b c"`              → `(["a b c"], False)`
    - `className={cn("a", x && "c")}`  → `(["a", "c"], True)`

    为什么按**属性块**而不是按**行**取：`im/chat/page.tsx` 的 `cn()` 里
    字号在第一行、颜色在第二行 —— 按行取会漏掉组合（实测：28 处里 4 处跨行）。

    🚨 **第三个返回值是判据的关键**：`cn()`（clsx + twMerge）**只在该属性真的调用了
    `cn()` 时**才生效。`className="text-caption text-ink-500"` 是**字面量字符串**，
    浏览器拿到的就是这两个类 —— **twMerge 对它没有发言权**。
    实测踩到：把 twMerge 套到**所有** className 上 ⇒ T2b 报 **753 条**假红
    （连 `text-h2`/`text-h3`/`text-caption` 都被判「被吞」），同时 T2 的判对数掉到 **0**
    （`text-body-sm` 在合并结果里消失，判据根本进不去）。见 `evidence/README.md` 坑 29。
    """
    out: list[tuple[int, list[str], bool]] = []
    for m in ATTR_RE.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        i = m.end()
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i >= len(text):
            continue
        ch = text[i]
        if ch in "\"'":
            j = text.find(ch, i + 1)
            if j < 0:
                continue
            out.append((line, [text[i + 1:j]], False))
        elif ch == "{":
            depth, j = 0, i
            while j < len(text):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            body = text[i + 1:j]
            lits = [a or b for a, b in LIT_RE.findall(body)]
            out.append((line, lits, bool(CN_RE.search(body))))
    return out


def source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for pat in ("apps/*/app/**/*.tsx", "apps/*/app/**/*.ts",
                "apps/*/components/**/*.tsx", "packages/*/src/**/*.tsx",
                "packages/*/src/**/*.ts", "packages/*/src/**/*.css"):
        for p in FRONTEND.glob(pat):
            if ".next" in p.parts or "node_modules" in p.parts:
                continue
            files.append(p)
    return sorted(set(files))


def collect_blocks() -> list[dict]:
    """把四端 + 共享包的 `className` 块收集起来（含出处）。"""
    rows: list[dict] = []
    for p in source_files():
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        for line, lits, is_cn in attr_blocks(text):
            rows.append({"where": f"{rel}:{line}", "lits": lits, "is_cn": is_cn})
    return rows


# ---------------------------------------------------------------------------
# 真实 twMerge（`cn()` 的语义）—— 只有实跑才作数
# ---------------------------------------------------------------------------
def _node_bin() -> str | None:
    managed = pathlib.Path.home() / ".workbuddy-ai" / "binaries" / "node" / "versions"
    if managed.is_dir():
        for d in sorted(managed.iterdir(), reverse=True):
            exe = d / "node.exe"
            if exe.exists():
                return str(exe)
    return shutil.which("node")


def cn_font_size_tokens(src: str | None = None) -> list[str] | None:
    """读**项目自己的** merge 配置（`cn.ts`）里登记的字号档。

    🚨 为什么必须读项目、而不是用库默认：
    `twMerge` 只认识 Tailwind 标准档（`text-sm/base/lg`），
    不认识本项目的语义档（`text-body-sm/…`）⇒ 把 `text-body-sm` 与 `text-<颜色>`
    判成**同一组**，同组后者赢 ⇒ **字号类被删**。
    `cn.ts` 用 `extendTailwindMerge` 把语义档登记进 `font-size` 组来修这个问题。
    判据若继续用**库默认**配置，则 `cn.ts` 修好之后**照红** —— 判据测的就不是产品行为。

    读不到 ⇒ `None`（调用方**必须留痕**并走 env 通道，**不得**静默当成「没问题」）。
    `src` 只给自检注入用；不传则读真实文件。
    """
    if src is None:
        try:
            src = CN_TS.read_text(encoding="utf-8")
        except OSError:
            return None
    # 容忍类型标注 / `as const`（`FONT_SIZE_TOKENS: string[] = [` 也要能读）
    m = re.search(r"FONT_SIZE_TOKENS[^=\n]*=\s*\[([^\]]*)\]", src, re.S)
    if not m:
        return None
    toks = re.findall(r"[\"']([A-Za-z0-9_.-]+)[\"']", m.group(1))
    return toks or None


def twmerge_blocks(blocks: list[list[str]],
                   sizes: list[str] | None = None) -> list[str] | None:
    """用 twMerge 合并每组类名（即 `cn()` 的行为）。

    为什么不自己实现：twMerge 把 `text-body-sm` 当**字号**还是**颜色**，
    取决于它的**类名表** —— 只有实跑才作数。

    **两条臂**（缺一条都会得出反的结论）：
    - `sizes=None` ⇒ **库默认配置** = 「`cn.ts` 没修好」时的行为 ⇒ **会吞**语义字号。
      用于自检的**机制臂**（证明判据测得出吞类）。
    - `sizes=[…]` ⇒ **项目配置**（`cn.ts` 的 `extendTailwindMerge` 登记的档）⇒ **不吞**。
      用于 T2/T2b 判据，代表**产品真实行为**。
    """
    node = _node_bin()
    if not node or not (FRONTEND / "node_modules" / "tailwind-merge").exists():
        return None
    js = (
        "const tm=require('tailwind-merge');let s='';"
        "process.stdin.setEncoding('utf8');"
        "process.stdin.on('data',d=>s+=d).on('end',()=>{"
        "const p=JSON.parse(s);"
        "const merge=(p.sizes&&p.sizes.length)"
        "?tm.extendTailwindMerge({extend:{classGroups:{'font-size':[{text:p.sizes}]}}})"
        ":tm.twMerge;"
        "process.stdout.write(JSON.stringify(p.blocks.map(x=>merge.apply(null,x))));});"
    )
    try:
        r = subprocess.run([node, "-e", js],
                           input=json.dumps({"blocks": blocks, "sizes": sizes}),
                           capture_output=True, text=True, encoding="utf-8",
                           cwd=str(FRONTEND), timeout=180)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# preset / 产物 CSS 解析
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


def parse_preset_sizes(text: str | None = None) -> dict[str, float]:
    r"""`tailwind.preset.ts` 的 `fontSize` 阶梯 → `{档名: px}`。

    🚨 档名要**先吃掉引号**再匹配：`"2xl"` / `"3xl"` 以数字开头，
    若写成 `["\']?([A-Za-z][\w-]*)`，正则会从引号**内部**匹配出 `xl`
    ⇒ `"2xl"`(24) 与 `"3xl"`(30) 会**依次覆盖**真正的 `xl`(20)。
    实测踩到：`--dump` 打出 `xl 30px`（应为 20px）。见 `evidence/README.md` 坑 29。
    """
    if text is None:
        text = PRESET_TS.read_text(encoding="utf-8", errors="ignore")
    block = _brace_block(text, "fontSize")
    out: dict[str, float] = {}
    for m in re.finditer(
            r'(?:"([\w-]+)"|\'([\w-]+)\'|([A-Za-z][\w-]*))'
            r'\s*:\s*\[\s*["\'](\d+(?:\.\d+)?)px["\']', block):
        name = m.group(1) or m.group(2) or m.group(3)
        out[name] = float(m.group(4))
    return out


def parse_preset_weights(text: str | None = None) -> list[str]:
    """`tailwind.preset.ts` 里所有 `fontWeight: "NNN"` 的声明值。"""
    if text is None:
        text = PRESET_TS.read_text(encoding="utf-8", errors="ignore")
    return re.findall(r'fontWeight\s*:\s*["\']?(\d{3})["\']?', text)


def css_artifacts(app: str, root: pathlib.Path | None = None) -> list[pathlib.Path]:
    """返回某端 `.next/static/css/` 下的**全部** CSS 产物（dev + build 两种形状）。

    🚨 **坑 73：这里绝不能写死文件名 —— `dev` 与 `build` 的产物路径不同。**
      · dev（`next dev`）    → `.next/static/css/**app/layout.css**`
      · build（`next build`）→ `.next/static/css/**<contenthash>.css**`
    `ci.yml` 的 `frontend` job 跑的是 **`Build (production)`**，
    而 2026-09-24 定稿这次接线时那次「**本机 6/0**」是**对着 dev server 的 `.next`** 量的。
    原实现写死 `css/app/layout.css` ⇒ 生产构建下 `exists()==False` ⇒ `leadings={}`
    ⇒ `judge_line_height` **逐对跳过** ⇒ `judged=0` ⇒ `summary_exit` 判 **2**
    ⇒ 步骤红。**「接线接上了」与「接的线是通的」是两件事。**
    ⚠️ 判据：`--source-only` 在生产 `.next` 上应得 `judged=6 / exit 0`
    （与 dev 那次实测同值）。`root` 参数只给自检用（P8–P11）。
    """
    base = root if root is not None else FRONTEND
    d = base / "apps" / app / ".next" / "static" / "css"
    return sorted(d.rglob("*.css")) if d.is_dir() else []


def parse_leading(css: str) -> dict[str, str]:
    """从产物 CSS 抠出 `leading-*` 类的**实际** `line-height` 值。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"\.((?:leading-)[^\s{,]+)\s*\{([^}]*)\}", css):
        cls = m.group(1).replace("\\", "")
        lm = re.search(r"line-height\s*:\s*([^;}]+)", m.group(2))
        if lm:
            out.setdefault(cls, lm.group(1).strip())
    return out


def css_ratio(value: str, size_px: float | None = None) -> float | None:
    """CSS 的 `line-height` 值 → 行高比（`无单位值` 即比值；`rem/px` 需除以字号）。"""
    v = value.strip().lower()
    if v.endswith("rem"):
        try:
            n = float(v[:-3]) * 16.0
        except ValueError:
            return None
        return round(n / size_px, 4) if size_px else None
    if v.endswith("px"):
        try:
            n = float(v[:-2])
        except ValueError:
            return None
        return round(n / size_px, 4) if size_px else None
    try:
        return float(v)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------
def judge_weight(blocks: list[dict], preset_weights: list[str],
                 raw_texts: dict[str, str]) -> tuple[list[str], list[str]]:
    """T1 / T1b / T1c：字重（§3.3 规则 1）。返回 `(problems, notes)`。"""
    problems: list[str] = []
    notes: list[str] = []
    seen: set[tuple[str, str]] = set()

    for b in blocks:
        toks = " ".join(b["lits"]).split()
        for t in toks:
            if t in WEIGHT_BAD:
                key = (b["where"], t)
                if key not in seen:
                    seen.add(key)
                    problems.append(f"[T1] `{t}` 不在 400/500/600 内 —— {b['where']}")
            if t.startswith("font-["):
                key = (b["where"], t)
                if key not in seen:
                    seen.add(key)
                    problems.append(f"[T1c] 任意值字重 `{t}` —— {b['where']}")

    for v in preset_weights:
        if int(v) not in WEIGHT_NUM_OK:
            problems.append(f"[T1b] preset `fontWeight: \"{v}\"` 不在 400/500/600 内")
    if not preset_weights:
        notes.append("[T1b] preset 里一个 `fontWeight` 声明都没解析到 ⇒ 覆盖率 0")

    for rel, text in raw_texts.items():
        for m in re.finditer(r"fontWeight\s*:\s*[\"']?(\d{3})", text):
            if int(m.group(1)) not in WEIGHT_NUM_OK:
                problems.append(f"[T1c] 内联 `fontWeight: {m.group(1)}` —— {rel}")
    return problems, notes


def merged_tokens(b: dict, merged: list[str] | None, i: int) -> str:
    """该 `className` 属性**实际生效**的类名串。

    🚨 只有**真的调用 `cn()`** 的属性才过 twMerge；字面量字符串原样生效。
    """
    if b.get("is_cn") and merged is not None and i < len(merged):
        return merged[i]
    return " ".join(b["lits"])


def _font_size_tokens(toks: set[str], sizes: dict[str, float]) -> set[str]:
    """`toks` 里**属于字号组**的那些 token。

    🚨 为什么要这个：`text-body-sm` 从合并结果里消失，有**两种**成因 ——
      (a) **被吞**：twMerge 把语义字号当颜色，与 `text-<颜色>` 同组 ⇒ 颜色赢
          ⇒ 字号**回落到继承值**，T2 也同时瞎了（**这才是 T2b 要抓的**）；
      (b) **被合法覆盖**：同块里有**另一个字号类**（`text-[11px]` / `text-label`）在后 ⇒
          它赢 ⇒ 字号**没有**回落，T2 也照样判（**正常写法，不该报**）。
    只按「输入有、合并后无」判 ⇒ 把 (b) 也报成缺陷。实测：`DataTable.tsx:628`
    数字列刻意用 `text-[11px]` 覆盖 `text-body-sm`，**修前修后都在报**，是稳定假红。

    ⚠️ 任意值 `text-[…]` **不能一律当字号**：`text-[rgb(var(--text-muted))]` 是**颜色**。
    实测踩到：一律当字号 ⇒ 把 `CountBadge.tsx:49` 的真红 T2b 误判成「合法冲突」
    ⇒ **假绿**（比假红危险）。所以只认**长得像长度**的任意值。
    """
    out: set[str] = set()
    for t in toks:
        if not t.startswith("text-"):
            continue
        name = t[5:]
        if name in sizes or _is_arb_length(name):
            out.add(t)
    return out


def _is_arb_length(name: str) -> bool:
    """`[11px]` / `[1.5rem]` / `[12]` ⇒ True；`[rgb(var(--x))]` / `[var(--x)]` ⇒ False。"""
    if not (name.startswith("[") and name.endswith("]")):
        return False
    return bool(re.fullmatch(r"[0-9.]+(?:px|rem|em|%)?", name[1:-1].strip().lower()))


def size_registration_probe_blocks(sizes: dict[str, float]) -> list[list[str]]:
    """给 preset 的**每一个**字号档造一组探针类名。

    形状与 M1/M5 的机制臂一致：`text-<档>` + `text-white`（**必然**是颜色类）。
    修好 `cn.ts` 前，两者被判成同一组 ⇒ 颜色赢 ⇒ `text-<档>` 消失。
    """
    return [[f"text-{s}", "text-white"] for s in sorted(sizes)]


def judge_size_registration(sizes: dict[str, float],
                            project_merged: list[str] | None) -> list[str]:
    """T2c：preset 的**每一个**字号档，在**项目 merge 配置**下都必须存活。

    🚨 为什么需要它（**这是 #42 的同一类缺陷，但可被「新增一个档」重新打开**）：
    `T2b` 的检查集合原先写成硬编码的 `CUSTOM_SIZES`（10 个语义档），
    而字号档的**真源是 `tailwind.preset.ts`**（17 个）。两者之间**没有任何机械校验** ⇒
    一旦有人往 preset 加一个语义档（例如 `h5`）并开始用 `text-h5`：
      · `CUSTOM_SIZES` 不含它 ⇒ **T2b 不检查它**；
      · `cn.ts` 的 `FONT_SIZE_TOKENS` 也忘了加 ⇒ `cn()` 照旧把它当颜色吞掉
        ⇒ **字号静默回落继承值**，而**四条判据没有一条会红**。
    这正是坑 63「判据的期望值必须读**产品真正用的那一份**」的同一个坑：
    判据里**不该有第二份手写清单**，应当**从 preset 直接派生**。

    本函数因此**不接收任何硬编码档名**，只吃 preset 解析结果 ⇒ 天然跟随真源。
    退出码语义：返回非空 = **产品缺陷**（preset 声明了、`cn()` 不认识）。

    ⚠️ 今天 17 个档全部存活（10 个语义档由 `cn.ts` 登记，7 个存量档 twMerge 默认就认识）
    ⇒ **0 条**，与棘轮 `0` 相容。
    """
    if project_merged is None:
        return []
    out: list[str] = []
    names = sorted(sizes)
    for i, s in enumerate(names):
        if i >= len(project_merged):
            break
        got = set(project_merged[i].split())
        if f"text-{s}" not in got:
            out.append(
                f"[T2c] preset 字号档 `{s}`（{sizes[s]:g}px）在**项目 merge 配置**下被吞掉"
                f"（`text-{s}` 与颜色类同组 ⇒ 颜色赢）"
                f" ⇒ `cn()` 会删掉它、字号**静默回落继承值**；"
                f"修法：把 `{s}` 加进 `frontend/packages/ui/src/lib/cn.ts` 的 `FONT_SIZE_TOKENS`")
    return out


def judge_line_height(blocks: list[dict], merged: list[str] | None,
                      sizes: dict[str, float], leadings: dict[str, str]
                      ) -> tuple[list[str], list[str], int]:
    """T2 / T2b。返回 `(problems, notes, 已判对数)`。

    - T2：合并后仍带正文档字号类 **且** 带 `leading-*` 覆盖 ⇒ 实际行高比必须 ≥ 1.65。
    - T2b：**只有真的调用 `cn()`** 的属性才可能被 twMerge 吞类；字面量字符串不合并。
      🚨 只在「字号**真的回落继承值**」时报 —— 被同组另一个字号类覆盖的不算
      （见 `_font_size_tokens` 的 (a)/(b) 两成因）。

    🚨 分流是判据的关键（见 `attr_blocks` 的说明）：对非 `cn()` 块套 twMerge 会
    同时造成**假红**（把 `text-caption` 当成被吞）和**假绿**（`text-body-sm` 在合并
    结果里消失 ⇒ T2 覆盖率归零）。
    """
    problems: list[str] = []
    notes: list[str] = []
    judged = 0

    for i, b in enumerate(blocks):
        lits = b["lits"]
        is_cn = bool(b.get("is_cn"))
        m = merged_tokens(b, merged, i)
        mtoks = set(m.split())

        # ---- T2b：自定义字号类被吞（**仅 cn() 属性**）----
        # 🚨 迭代集合**从 preset 派生**，不用硬编码清单 —— 见 `judge_size_registration` 的说明。
        #    只遍历「输入里有」的档（`tok in itoks`）⇒ 多出来的存量档零成本。
        if is_cn:
            itoks: set[str] = set()
            for lit in lits:
                itoks |= set(lit.split())
            for s in sorted(sizes):
                tok = f"text-{s}"
                if tok in itoks and tok not in mtoks:
                    others = _font_size_tokens(mtoks, sizes) - {tok}
                    if others:
                        notes.append(
                            f"[T2b] `{tok}` 被同组字号类 {sorted(others)} 覆盖"
                            f"（**合法冲突**，字号未回落 ⇒ 不判）—— {b['where']}")
                        continue
                    problems.append(
                        f"[T2b] `{tok}` 被 `cn()` 吞掉（输入有、合并后无）—— {b['where']}"
                        f"  ⇒ 字号回落继承值，行高按继承字号算")

        # ---- T2：正文档 + 行高覆盖 ----
        body_here = [s for s in BODY_SIZES if f"text-{s}" in mtoks]
        lead_here = [t for t in sorted(mtoks) if t.startswith("leading-")]
        for s in body_here:
            for lt in lead_here:
                val = leadings.get(lt)
                if val is None:
                    notes.append(f"[T2] `{lt}` 在产物 CSS 里解析不到 ⇒ 跳过（{b['where']}）")
                    continue
                r = css_ratio(val, sizes.get(s))
                if r is None:
                    notes.append(f"[T2] `{lt}` = `{val}` 无法折算行高比 ⇒ 跳过（{b['where']}）")
                    continue
                judged += 1
                if r < LH_FLOOR - 1e-9:
                    problems.append(
                        f"[T2] 正文档 `text-{s}` 被 `{lt}` 压到 **{r:.3f}** < {LH_FLOOR}"
                        f"（`{lt}` = `{val}`）—— {b['where']}")
    return problems, notes, judged


# --- T4 的锚点（2026-09-24 升级）-------------------------------------------
# 规范侧：`design-spec.md` §3.3 规则 3 的原文是
#   `3. **中英混排加 0.25em 间隙** —— 避免「3件」这类粘连。`
# ⚠️ **只锚「中英混排加 <数> em 间隙」这一小段，不锚整行** —— 整行后半句是举例
#    （「3件」），改举例不该让门禁红。
SPEC_CJK_GAP_RE = re.compile(r"中英混排加\s*([0-9]*\.?[0-9]+)\s*em\s*间隙")
# 产品侧：`styles.css` 里的 `html .cjk-gap { … }` 块。**不用行号**（行号会漂）。
CSS_CJK_GAP_BLOCK_RE = re.compile(r"html\s+\.cjk-gap\s*\{([^{}]*)\}", re.S)
CSS_CJK_GAP_MARGIN_RE = re.compile(r"margin-inline\s*:\s*([^;}]+)")


def _norm_em(v: str) -> str | None:
    """把 `0.25em` / `.25em` / `0.250 em` 归一成 `0.25em`。解析不出 ⇒ `None`。

    ⚠️ 与 README 那条「规范文本 vs 令牌文本做字符串相等 ⇒ **稳定假红**」同一个坑：
    必须先**归一化写法**再比。这里只处理**前导零 / 尾随零 / 空白**，
    **不做数值容差** —— `0.25em` 与 `0.250em` 相等（同值），`0.25em` 与 `0.3em` 不等。
    """
    m = re.fullmatch(r"([0-9]*\.?[0-9]+)\s*(em|rem|px)", v.strip().lower())
    if not m:
        return None
    num, unit = m.group(1), m.group(2)
    if num.startswith("."):
        num = "0" + num
    if "." in num:
        num = num.rstrip("0").rstrip(".") or "0"
    return f"{num}{unit}"


def judge_rule3(raw_texts: dict[str, str],
                spec_text: str | None = None,
                ) -> tuple[list[str], list[str], list[str]]:
    """T4：§3.3 规则 3「中英混排加 0.25em 间隙」。

    **判**：`html .cjk-gap` 的 `margin-inline` == **规范现读**的那个 em 值。
    **报**：`cjk-gap` 的**调用点数**（覆盖率信号 —— 「哪些中英混排处该加间隙」需要语义）。

    返回 `(problems, notes, env)`：
      · `problems` ⇒ **产品缺陷**（rc 1）
      · `env`      ⇒ **我没测成**（rc 2）：规范锚点读不到 / 块解析不了
    ⚠️ 两者**必须分开**：把「规范文本改了 ⇒ 我读不到期望值」报成产品缺陷 = **假红**
    （`methodology` 的「产品坏了 vs 我没测成」）。
    """
    problems: list[str] = []
    notes: list[str] = []
    env: list[str] = []

    # ---- ① 期望值：从规范**现读**（不是硬编码 `0.25em`）----
    if spec_text is None:
        try:
            spec_text = SPEC_MD.read_text(encoding="utf-8")
        except OSError as e:                                    # noqa: BLE001
            env.append(f"[T4] 读不到规范 `{SPEC_MD}`：{e} ⇒ 拿不到期望值，本次不判")
            spec_text = None

    expected: str | None = None
    if spec_text is not None:
        hits = SPEC_CJK_GAP_RE.findall(spec_text)
        if len(hits) == 1:
            expected = _norm_em(hits[0] + "em")
        if expected is None:
            env.append(f"[T4] 规范 §3.3 规则 3 的「中英混排加 … em 间隙」锚点命中 "
                       f"{len(hits)} 处（期望 1 处）⇒ **拿不到期望值**，本次不判。"
                       f"⚠️ 这是**规范文本/仪器变了**，不是产品缺陷 —— 别去改 CSS")

    # ---- ② 实际值：`styles.css` 里的 `html .cjk-gap { … }` ----
    css_rel = next((k for k in raw_texts
                    if k.endswith("packages/ui/src/styles.css")), None)
    actual: str | None = None
    if css_rel is None:
        env.append("[T4] 扫描集里没有 `packages/ui/src/styles.css` ⇒ 拿不到实际值，本次不判")
    else:
        blocks = CSS_CJK_GAP_BLOCK_RE.findall(raw_texts[css_rel])
        if len(blocks) == 0:
            # 「锚点命中 0 处」= **东西被删了** ⇒ 产品缺陷（与「解析不了」分开）
            problems.append(
                f"[T4] `html .cjk-gap` 的定义在 `{css_rel}` 里**一处都没有** ⇒ "
                f"§3.3 规则 3 的 CSS 钩子被删了（规范要求 {expected or '该间隙'}）")
        elif len(blocks) > 1:
            env.append(f"[T4] `html .cjk-gap` 块命中 {len(blocks)} 处（期望 1 处）"
                       f"⇒ 解析不了，本次不判")
        else:
            m = CSS_CJK_GAP_MARGIN_RE.search(blocks[0])
            if not m:
                problems.append(
                    f"[T4] `html .cjk-gap` 块存在但**没有 `margin-inline`** ⇒ "
                    f"钩子不生效（规范要求 {expected or '该间隙'}）")
            else:
                actual = _norm_em(m.group(1))

    # ---- ③ 判（期望值与实际值**都**取到才判）----
    if expected is not None and actual is not None:
        if actual != expected:
            problems.append(
                f"[T4] `html .cjk-gap` 的 `margin-inline` = `{actual}`，"
                f"而 §3.3 **现读值** = `{expected}` ⇒ 钩子与规范不同步"
                f"（改规范再改 CSS，或反过来）")
        else:
            notes.append(f"[T4] ✅ `html .cjk-gap` 的 `margin-inline` = `{actual}` "
                         f"== §3.3 现读值 `{expected}`")
    elif actual is not None:
        notes.append(f"[T4] 实际值 `{actual}` 已取到，但**期望值取不到** ⇒ 未判（见上）")

    # ---- ④ 仍然**只报**：调用点数 ----
    callers: list[str] = []
    for rel, text in raw_texts.items():
        if rel.endswith("packages/ui/src/styles.css"):
            continue                      # 这是定义处，不算调用点
        if "cjk-gap" in text:
            callers.append(rel)
    notes.append(f"[T4·只报] `cjk-gap` **调用点 {len(callers)} 处** —— "
                 f"这一项**只报不判**（哪些中英混排处该加间隙需要**语义**判断，静态数不出来）")
    if not callers:
        notes.append("[T4·只报] ⇒ CSS 钩子存在且值正确，但**无任何调用点**："
                     "规范要求了、产品**没落地** ⇒ 这是**功能缺口**（不归本门禁判）")
    else:
        notes.append(f"[T4·只报] 调用点：{', '.join(callers[:6])}")
    return problems, notes, env


# ---------------------------------------------------------------------------
# 渲染级测量（T3）
# ---------------------------------------------------------------------------
RENDER_JS = r"""(() => {
  const LEAD = /(?:^|\s)(leading-[A-Za-z0-9._\[\]-]+)/g;
  const direct = (el) => {
    let s = '';
    for (const n of el.childNodes) if (n.nodeType === 3) s += n.textContent;
    return s.replace(/\s+/g, ' ').trim();
  };
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    return parseFloat(cs.opacity || '1') > 0.05;
  };
  const out = [];
  const SEL = 'p,div,span,li,dd,dt,td,th,blockquote,label,section,article,figcaption';
  for (const el of document.querySelectorAll(SEL)) {
    const t = direct(el);
    if (!t) continue;
    if (!vis(el)) continue;
    const cs = getComputedStyle(el);
    const fs = parseFloat(cs.fontSize);
    const lhRaw = cs.lineHeight;
    const lh = (lhRaw === 'normal') ? null : parseFloat(lhRaw);
    const cls = el.getAttribute('class') || '';
    const lead = [];
    let m; LEAD.lastIndex = 0;
    while ((m = LEAD.exec(cls)) !== null) lead.push(m[1]);
    out.push({
      tag: el.tagName.toLowerCase(),
      cls: cls,
      fs: fs,
      fw: cs.fontWeight,
      lh: lh,
      ratio: (lh && fs) ? +(lh / fs).toFixed(4) : null,
      lead: lead,
      txt: t.slice(0, 48),
    });
  }
  return {
    texts: out,
    vw: innerWidth,
    contentLen: (document.body.innerText || '').length,
  };
})()"""


def judge_render(m: dict | None) -> tuple[list[str], list[dict], list[dict]]:
    """T3：正文候选元素的行高比。返回 `(problems, 正文候选, 只报不判的)`. 

    正文候选的判据**按计算字号 + 计算字重**，**不按类名** —— 类名会被 `cn()` 删掉
    （这正是 T2b 抓到的症状：字号被吞后元素会以 16px 渲染，但内容仍是正文）。
    """
    if not m:
        return ["[T3] 未取到测量结果"], [], []
    problems: list[str] = []
    judged: list[dict] = []
    reports: list[dict] = []
    for it in m.get("texts") or []:
        fs, ratio = it.get("fs"), it.get("ratio")
        if fs is None or ratio is None:
            continue
        try:
            fw = int(float(str(it.get("fw") or 400)))
        except (TypeError, ValueError):
            fw = 400
        if not (T3_FS_LO - 1e-6 <= fs <= T3_FS_HI + 1e-6):
            continue
        if len(it.get("txt") or "") < T3_MIN_CHARS:
            continue
        if fw > T3_MAX_WEIGHT:
            continue                       # 标题档（600）不属「正文」
        if it.get("tag") in ("h1", "h2", "h3", "h4", "h5", "h6"):
            continue
        if ratio < LH_FLOOR - 1e-9:
            judged.append(it)
            problems.append(
                f"[T3] <{it['tag']}> {fs:g}px/{fw} 行高比 **{ratio:.3f}** < {LH_FLOOR}"
                f"  class=`{it['cls']}`  `{it['txt'][:24]}`")
        else:
            reports.append(it)
    return problems, judged, reports


def render_coverage(m: dict | None) -> int:
    if not m:
        return 0
    n = 0
    for it in m.get("texts") or []:
        fs = it.get("fs")
        if fs is None:
            continue
        if T3_FS_LO - 1e-6 <= fs <= T3_FS_HI + 1e-6 and len(it.get("txt") or "") >= T3_MIN_CHARS:
            n += 1
    return n


SIZE_EPS = 0.5      # 计算字号与声明 px 的允许偏差（浏览器可能返回小数）


def judge_render_sizes(m: dict | None, sizes: dict[str, float]) -> tuple[list[str], int]:
    """T3b：**运行时类里真的带着** `text-<档>` 的元素 ⇒ 计算字号必须等于该档声明的 px。

    🚨 **它测的不是 T2b。** 初版我把它写成「T2b 的渲染级独立确认」，那是错的：
    类被 `cn()` 删掉后，运行时 `class` 属性里**没有痕迹**，本判据直接跳过该元素
    ⇒ 实测 T2b 报 62 条、T3b 报 **0** 条，两者**不是矛盾，是判据覆盖不到**
    （267 对覆盖里一条都不落在被吞的元素上）。见坑 29。

    它真正测的是**另一类**失效：类在 DOM 里、CSS 也在产物里，但**没生效**
    （Tailwind 没生成该规则 / 被更高优先级的声明压掉 / 规则顺序被改）。
    这是一条独立且有用的绿。

    返回 `(problems, 覆盖对数)`。
    """
    if not m:
        return [], 0
    problems: list[str] = []
    cov = 0
    for it in m.get("texts") or []:
        toks = set((it.get("cls") or "").split())
        for s, px in sizes.items():
            if f"text-{s}" not in toks:
                continue
            cov += 1
            fs = it.get("fs")
            if fs is None:
                continue
            if abs(fs - px) > SIZE_EPS:
                problems.append(
                    f"[T3b] 类含 `text-{s}`（声明 {px:g}px）但实测字号 **{fs:g}px**"
                    f" —— class=`{it['cls'][:76]}` `{it['txt'][:20]}`")
    return problems, cov


# ---------------------------------------------------------------------------
# 收口
# ---------------------------------------------------------------------------
def summary_exit(total: int, problems: list[str], env_broken: list[str]) -> int:
    """`0` 通过 · `1` 产品缺陷 · `2` 环境问题。

    🚨 覆盖率 0 ⇒ **2**：「我没测成」≠「产品健康」。
    🚨 环境被破坏 ⇒ **2**：测量本身不可信，此时报红报绿都没有意义。
    """
    if env_broken:
        return 2
    if total == 0:
        return 2
    if problems:
        return 1
    return 0


def _load_raw_texts() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in source_files():
        try:
            out[str(p.relative_to(ROOT)).replace("\\", "/")] = p.read_text(
                encoding="utf-8", errors="ignore")
        except OSError:
            continue
    try:
        out["frontend/tailwind.preset.ts"] = PRESET_TS.read_text(encoding="utf-8")
    except OSError:
        pass
    return out


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


# ---------------------------------------------------------------------------
# 各模式
# ---------------------------------------------------------------------------
def run_static(verbose: bool = True) -> tuple[list[str], list[str], int, list[str]]:
    """T1/T1b/T1c/T2/T2b/T2c/T4（不开浏览器）。

    返回 `(problems, notes, 覆盖率, env)`。
    ⚠️ **2026-09-24 新增第 4 个返回值 `env`**：T4 升级后要能表达「**我没测成**」
    （规范锚点读不到）—— 此前这个函数没有 env 通道，只报路径会把「没测成」
    静默吸收成「没问题」。见 `judge_rule3` 的 docstring。
    """
    blocks = collect_blocks()
    raw = _load_raw_texts()
    sizes = parse_preset_sizes()
    leadings: dict[str, str] = {}
    for app in APPS:
        for p in css_artifacts(app):
            for k, v in parse_leading(p.read_text(encoding="utf-8", errors="ignore")).items():
                leadings.setdefault(k, v)
    merge_sizes = cn_font_size_tokens()
    merged = twmerge_blocks([b["lits"] for b in blocks], merge_sizes)
    # T2c 探针：preset 的每一个字号档 × 一个必然的颜色类。**单独一次调用**，
    # 不与 `merged` 混在一起（`merged_tokens` 按下标取，混了会错位）。
    # ⚠️ 判据用 `is not None` 而非真值判断：`cn.ts` 登记为**空列表**也是一种产品状态
    #    （此时应报红，而不是走「探针跑不了」的 env 通道）。
    probe_merged = (twmerge_blocks(size_registration_probe_blocks(sizes), merge_sizes)
                    if merge_sizes is not None else None)

    problems: list[str] = []
    notes: list[str] = []
    env: list[str] = []

    p1, n1 = judge_weight(blocks, parse_preset_weights(), raw)
    problems += p1
    notes += n1

    p2, n2, judged = judge_line_height(blocks, merged, sizes, leadings)
    problems += p2
    notes += n2

    p4, n4, e4 = judge_rule3(raw)
    problems += p4
    notes += n4
    env += e4

    if merged is None:
        notes.append("[env] 真实 twMerge 不可用 ⇒ T2/T2b 用**原始字面量**合并（保守）")
    if merge_sizes is None:
        env.append(
            "[T2b] 读不到 `frontend/packages/ui/src/lib/cn.ts` 的 `FONT_SIZE_TOKENS` "
            "⇒ **拿不到项目的 merge 配置**，吞类判据本次不判（**不是**产品健康）")
    else:
        # ---- T2c：preset 的每一个字号档在项目配置下都必须存活 ----
        # 原先是 `missing = [s for s in CUSTOM_SIZES if s not in merge_sizes]`（硬编码子集，
        # 且**只比清单、不比行为**）。现改为**实跑生存探针**：preset 全 17 档逐档过 merge。
        # 更强（连存量档一起覆盖）且**不可能与 preset 漂移**。
        if probe_merged is None:
            env.append("[T2c] 档登记生存探针跑不了（真实 twMerge 不可用）"
                       "⇒ 本次不判（**不是**产品健康）")
        else:
            problems += judge_size_registration(sizes, probe_merged)
    if not leadings:
        # ⚠️ 刻意留在 `notes` 而**不是** `env`：`summary_exit` 里 `env` **优先于**
        #    `problems` ⇒ 挪过去会把「有真缺陷」从 exit 1 降级成 exit 2（在
        #    `run_ci_probes` 里 = SKIP），反而**掩盖**产品缺陷。退出码本身不变
        #    （`judged==0` ⇒ 2），这里只把**原因**说清楚 —— 见 `README.md` 坑 73。
        have = [a for a in APPS if css_artifacts(a)]
        if have:
            notes.append(
                f"[env] {len(have)} 端的 `.next/static/css/` 里**有** CSS 产物，"
                f"却一个 `leading-*` 都没解析到 ⇒ 判对数 0 ⇒ 退出码 2（**不是**产品健康）。"
                f"⚠️ 先查 dev/build 路径差异（`README.md` 坑 73）")
        else:
            notes.append("[env] 四端都没有 `.next/static/css/` 产物 ⇒ 判对数 0 "
                         "⇒ 退出码 2（**不是**产品健康）")

    if verbose:
        print(f"── 扫描：{len(blocks)} 个 `className` 块，"
              f"preset 字号档 {len(sizes)} 个，产物 `leading-*` {len(leadings)} 个")
        print("── merge 配置来源："
              + (f"**项目**（`cn.ts` 登记 {len(merge_sizes)} 档：{','.join(merge_sizes)}）"
                 if merge_sizes else "**库默认**（`cn.ts` 的 `FONT_SIZE_TOKENS` 读不到）"))
        print(f"── T2 已判对数（正文档 × 行高覆盖）：{judged}")
        print("── 产物 `leading-*` 实测值：")
        for k in sorted(leadings):
            print(f"     {k:22s} = {leadings[k]:10s} → "
                  f"比 13px={css_ratio(leadings[k], 13.0)}  14px={css_ratio(leadings[k], 14.0)}")
        print("── preset 字号档：")
        for k, v in sorted(sizes.items(), key=lambda x: x[1]):
            print(f"     {k:10s} {v:g}px")
    return problems, notes, judged, env


FIRST_CASE_JS = r"""(() => {
  const a = document.querySelector('a[href^="/cases/"]');
  if (a) return { href: a.getAttribute('href') };
  const tr = document.querySelector('tbody tr') || document.querySelector('[role="row"]');
  if (tr) { tr.click(); return { clicked: true }; }
  return {};
})()"""


async def resolve_path(b: Browser, base: str, path: str) -> str:
    """把 `@first-case` 换成真实 id（照搬 `verify_runtime_health.py` 的做法 + 一条回退）。

    为什么不直接调 API 拿 id：SDK 把 access token 存在**模块内存变量**里
    （刻意不落 localStorage），所以 CDP 里发 `fetch` 带不上鉴权。
    **从 DOM 读真实值是唯一不用重建鉴权的办法。**

    ⚠️ 回退是实测补的：**律师端**的案件列表用 `router.push('/cases/'+id)`（行点击），
    DOM 里**没有** `<a href="/cases/…">` ⇒ 只抓链接会永远测不到该端详情页
    （实测报「没有指向案件详情的链接」）。客户端（im）用的是 `<a href>`，能直接抓。
    ⇒ 抓不到链接就**点第一行**，再读 `location.href`。
    """
    if "@first-case" not in path:
        return path
    await b.goto(f"{base}/cases", wait=1.6)
    await b.settle(extra=1.0)
    r = await b.cdp.evaluate(FIRST_CASE_JS) or {}
    href = r.get("href")
    if not href and r.get("clicked"):
        # ⚠️ 这里**不能**用 `settle()`：客户端路由跳转（`router.push`）不产生导航请求，
        # `settle` 会立刻返回、`pathname` 还是 `/cases`（实测踩到）。
        # ⇒ 显式轮询，最多 6 秒。
        for _ in range(12):
            await asyncio.sleep(0.5)
            href = await b.cdp.evaluate("location.pathname")
            if href and href.startswith("/cases/") and href != "/cases":
                break
    if not href or not href.startswith("/cases/") or href == "/cases":
        raise RuntimeError(f"{base}/cases 上取不到案件详情路径（动态路由测不到）")
    return path.replace("@first-case", href.rsplit("/", 1)[-1])


async def run_render(only: str | None, sizes: dict[str, float],
                     verbose: bool = True) -> tuple[list[str], int, int, list[str]]:
    """渲染层 T3（行高）+ T3b（字号类落地）。

    每端只开**一个**浏览器、登录一次，再逐页导航 —— 逐页重开浏览器会把
    27 页的耗时放大到不可用（实测 8 页 × 逐页重开 = 1m29s）。

    返回 `(problems, 行高覆盖, 字号覆盖, 端级环境问题)`。
    """
    problems: list[str] = []
    env_break: list[str] = []
    cov_lh = 0
    cov_size = 0
    for app, cfg in APPS.items():
        if only and app != only:
            continue
        base = f"http://localhost:{cfg['port']}"
        async with Browser(headless=True, width=1280, height=900) as b:
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                env_break.append(f"{app} 登录失败 ⇒ 整端未测")
                print(f"[env] {app} 登录失败 ⇒ 整端跳过")
                continue
            for raw in cfg["pages"]:
                try:
                    path = await resolve_path(b, base, raw)
                except Exception as e:                      # noqa: BLE001
                    # 单页解析失败**不**把整轮判成环境问题（否则一页抖动会掩盖
                    # 已检出的真实缺陷），但必须**显式留痕**，不能静默少测。
                    print(f"[env] {app}{raw} 解析失败：{e}")
                    continue
                await b.goto(f"{base}{path}", wait=1.8)
                await b.settle(extra=1.4)
                m = await b.cdp.evaluate(RENDER_JS)
                cov = render_coverage(m)
                cov_lh += cov
                p, judged, _ = judge_render(m)
                problems += [f"{app}{path} {x}" for x in p]
                ps, cs = judge_render_sizes(m, sizes)
                cov_size += cs
                problems += [f"{app}{path} {x}" for x in ps]
                if verbose:
                    print(f"── {app}{path}  正文候选 {cov} 个，判红 {len(judged)} 个；"
                          f"字号类覆盖 {cs} 对，判红 {len(ps)} 个")
    return problems, cov_lh, cov_size, env_break


async def dump(only: str | None) -> int:
    """诊断模式：打原始数值，**不下判断**。"""
    print("── 源码层 ──")
    blocks = collect_blocks()
    merge_sizes = cn_font_size_tokens()
    merged = twmerge_blocks([b["lits"] for b in blocks], merge_sizes)
    print(f"  className 块 {len(blocks)} 个；twMerge {'可用' if merged is not None else '不可用'}")
    print(f"  merge 配置：{'项目（cn.ts：' + ','.join(merge_sizes) + '）' if merge_sizes else '库默认（cn.ts 读不到）'}")
    sizes = parse_preset_sizes()
    print(f"  preset 字号档：{ {k: v for k, v in sorted(sizes.items(), key=lambda x: x[1])} }")
    print(f"  preset 字重声明：{parse_preset_weights()}")

    leadings: dict[str, str] = {}
    for app in APPS:
        for p in css_artifacts(app):
            for k, v in parse_leading(p.read_text(encoding="utf-8", errors="ignore")).items():
                leadings.setdefault(k, v)
    print("\n── 产物 `leading-*` 实际值 ──")
    for k in sorted(leadings):
        print(f"  {k:22s} = {leadings[k]:12s} 13px→{css_ratio(leadings[k], 13.0)}"
              f"  14px→{css_ratio(leadings[k], 14.0)}")

    print("\n── 正文档 + 行高覆盖的组合（逐条）──")
    for i, b in enumerate(blocks):
        m = merged_tokens(b, merged, i)
        mt = set(m.split())
        body = [s for s in BODY_SIZES if f"text-{s}" in mt]
        leads = sorted(t for t in mt if t.startswith("leading-"))
        if body and leads:
            for s in body:
                for lt in leads:
                    r = css_ratio(leadings.get(lt, ""), sizes.get(s))
                    print(f"  text-{s:9s} + {lt:18s} → {r}   {b['where']}")
    print("\n── `leading-relaxed` 但无正文档（只报）──")
    for i, b in enumerate(blocks):
        m = merged_tokens(b, merged, i)
        mt = set(m.split())
        if "leading-relaxed" in mt and not any(f"text-{s}" in mt for s in BODY_SIZES):
            print(f"  {b['where']}  class=`{m[:90]}`")
    print("\n── T4：§3.3 规则 3（**判**：钩子值 == 规范现读值；**报**：调用点数）──")
    _p4, _n4, _e4 = judge_rule3(_load_raw_texts())
    for line in _n4:
        print(f"  {line}")
    for line in _p4:
        print(f"  ✗ {line}")
    for line in _e4:
        print(f"  [env] {line}")

    if only == "__static__":
        return 0

    print("\n── 渲染层：正文候选（行高比升序，最低 20）──")
    for app, cfg in APPS.items():
        if only and app != only:
            continue
        base = f"http://localhost:{cfg['port']}"
        async with Browser(headless=True, width=1280, height=900) as b:
            try:
                await b.cdp.send("Network.clearBrowserCookies")
            except Exception:
                pass
            if not await login(b, base, cfg["user"]):
                print(f"  [env] {app} 登录失败")
                continue
            for raw in cfg["pages"]:
                try:
                    path = await resolve_path(b, base, raw)
                except Exception as e:                      # noqa: BLE001
                    print(f"  [env] {app}{raw} 解析失败：{e}")
                    continue
                await b.goto(f"{base}{path}", wait=1.8)
                await b.settle(extra=1.4)
                m = await b.cdp.evaluate(RENDER_JS)
                cand = []
                for it in m.get("texts") or []:
                    fs = it.get("fs")
                    if fs is None or it.get("ratio") is None:
                        continue
                    if (T3_FS_LO - 1e-6 <= fs <= T3_FS_HI + 1e-6
                            and len(it.get("txt") or "") >= T3_MIN_CHARS):
                        cand.append(it)
                cand.sort(key=lambda x: x["ratio"])
                print(f"\n  ── {app}{path} 候选 {len(cand)}，最低 {min(20, len(cand))}：")
                for it in cand[:20]:
                    print(f"    {it['ratio']:6.3f}  <{it['tag']}> {it['fs']:g}px/{it['fw']} "
                          f"lh={it['lh']} lead={it['lead']} `{it['txt'][:26]}`")
    return 0


WHY_JS = r"""(function(needle) {
  const direct = (el) => {
    let s = '';
    for (const n of el.childNodes) if (n.nodeType === 3) s += n.textContent;
    return s.replace(/\s+/g, ' ').trim();
  };
  const hits = [];
  for (const el of document.querySelectorAll('p,div,span,li,dd,dt,td,th,blockquote,label')) {
    const t = direct(el);
    if (t && t.includes(needle)) hits.push(el);
  }
  if (!hits.length) return { found: false };
  const el = hits[hits.length - 1];
  const chain = [];
  let node = el;
  while (node && node.nodeType === 1) {
    const cs = getComputedStyle(node);
    chain.push({
      tag: node.tagName.toLowerCase(),
      cls: node.getAttribute('class') || '',
      fs: cs.fontSize, lh: cs.lineHeight, fw: cs.fontWeight,
    });
    node = node.parentElement;
    if (chain.length > 8) break;
  }
  const cs = getComputedStyle(el);
  return {
    found: true,
    txt: direct(el).slice(0, 80),
    cls: el.getAttribute('class') || '',
    fs: cs.fontSize, lh: cs.lineHeight, fw: cs.fontWeight,
    ratio: (cs.lineHeight === 'normal') ? null : +(parseFloat(cs.lineHeight) / parseFloat(cs.fontSize)).toFixed(4),
    chain: chain,
  };
})"""


async def why(app: str, path: str | None, needle: str) -> int:
    """**取证模式**：把含 `needle` 的元素的类 + 计算行高 + 祖先链打出来，**不下判断**。

    为什么需要它：判「行高被压」之前，必须先证明**浏览器真实解析出的**字号/行高是什么、
    `text-body-sm` 到底是**生效了**还是**被 `cn()` 删掉了**。否则会把
    「我的静态推断」当成「运行时事实」（见坑 28）。
    """
    if app not in APPS:
        print(f"[env] 未知端 `{app}`（可选：{'/'.join(APPS)}）")
        return 2
    cfg = APPS[app]
    base = f"http://localhost:{cfg['port']}"
    path = path or cfg["pages"][0]
    async with Browser(headless=True, width=1280, height=900) as b:
        try:
            await b.cdp.send("Network.clearBrowserCookies")
        except Exception:
            pass
        if not await login(b, base, cfg["user"]):
            print("[env] 登录失败")
            return 2
        await b.goto(f"{base}{path}", wait=1.8)
        await b.settle(extra=1.4)
        r = await b.cdp.evaluate(f"({WHY_JS})({json.dumps(needle)})")
    if not r or not r.get("found"):
        print(f"[env] 页面上找不到含 `{needle}` 的元素（路径 {path}）")
        return 2
    print(f"── {app}{path} 命中：`{r['txt']}`")
    print(f"   运行时 class = `{r['cls']}`")
    print(f"   computed   font-size={r['fs']}  line-height={r['lh']}  font-weight={r['fw']}"
          f"  ratio={r['ratio']}")
    print(f"   → 行高比 {'≥' if (r['ratio'] or 0) >= LH_FLOOR else '<'} {LH_FLOOR}")
    print("   祖先链（由内到外）：")
    for i, lv in enumerate(r["chain"]):
        mark = "  ← 命中" if i == 0 else ""
        print(f"     [{i}] <{lv['tag']}> fs={lv['fs']:>7s} lh={lv['lh']:>7s} fw={lv['fw']:>3s}"
              f"  class=`{lv['cls'][:72]}`{mark}")
    return 0


# ---------------------------------------------------------------------------
# 自检：证明每条判据都会红
# ---------------------------------------------------------------------------
FIXTURE_CSS = """
.leading-relaxed{
  line-height: 1.625;
}
.leading-\\[1\\.85\\]{
  line-height: 1.85;
}
.leading-4{
  line-height: 1rem;
}
.text-body-sm{
  font-size: 13px;
  line-height: 1.65;
}
.text-body{
  font-size: 14px;
  line-height: 1.7;
}
.text-caption{
  font-size: 11px;
  line-height: 1.5;
}
"""

FIXTURE_PRESET = """
fontSize: {
  caption: ["11px", { lineHeight: "1.5", fontWeight: "500" }],
  "body-sm": ["13px", { lineHeight: "1.65" }],
  body: ["14px", { lineHeight: "1.7" }],
  h4: ["16px", { lineHeight: "1.5", fontWeight: "600" }],
  xl: ["20px", { lineHeight: "1.5" }],
  "2xl": ["24px", { lineHeight: "1.4" }],
  "3xl": ["30px", { lineHeight: "1.3" }],
},
"""


def self_test() -> int:
    """每条判据都要有一条**会红**的臂 + 一条**对照（应绿）**的臂。

    为什么对照臂不能省：`T2b` 的机制是「twMerge 把自定义字号当颜色」——
    如果 twMerge 哪天改了行为，红的臂可能因为别的原因红、对照臂会静默变红。
    没有对照臂，我分不清「产品有缺陷」和「判据坏了」。

    🚨 **2026-09-24 起 T2b 是「双配置」判据**（见 `twmerge_blocks` 的 docstring）：
    `M1/M2` 走**库默认配置**（机制臂 —— 证明判据测得出吞类），
    `M5–M10` 走**项目配置**（对照臂 + 反证臂 —— 证明修法在判据里被登记，
    且 extend **只放行登记过的档**、不是万能放行）。
    """
    fails: list[str] = []
    skipped: list[str] = []
    n = 0

    def arm(name: str, cond: bool, detail: str = "") -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{name} {detail}")

    # ---- 解析器 ----
    lead = parse_leading(FIXTURE_CSS)
    arm("P1 parse_leading 认得 leading-relaxed",
        lead.get("leading-relaxed") == "1.625", f"→ {lead.get('leading-relaxed')}")
    arm("P2 parse_leading 认得带转义的 leading-[1.85]",
        lead.get("leading-[1.85]") == "1.85", f"→ {lead.get('leading-[1.85]')}")
    arm("P3 parse_leading 认得绝对值的 leading-4",
        lead.get("leading-4") == "1rem", f"→ {lead.get('leading-4')}")
    sizes = parse_preset_sizes(FIXTURE_PRESET)
    arm("P4 parse_preset_sizes 解出 body-sm=13",
        sizes.get("body-sm") == 13.0, f"→ {sizes}")
    arm("P6 带引号的数字开头档名不得被拆开（`2xl`/`3xl`）",
        sizes.get("2xl") == 24.0 and sizes.get("3xl") == 30.0, f"→ {sizes}")
    arm("P7 `2xl`/`3xl` 不得覆盖 `xl`（xl 应为 20）",
        sizes.get("xl") == 20.0, f"→ xl={sizes.get('xl')}")
    arm("P5 parse_preset_weights 解出 500 与 600",
        sorted(parse_preset_weights(FIXTURE_PRESET)) == ["500", "600"],
        f"→ {parse_preset_weights(FIXTURE_PRESET)}")

    # ---- 产物定位（坑 73：`dev` 与 `build` 的 CSS 路径**不同**）----
    # 这两条臂存在的理由：原实现写死 dev 的 `css/app/layout.css`，生产构建下
    # `exists()==False` ⇒ 静默拿不到 CSS ⇒ `judged=0` ⇒ **接线看着接上了、实际是断的**。
    with tempfile.TemporaryDirectory() as _td:
        _t = pathlib.Path(_td)
        _d = _t / "apps" / "web" / ".next" / "static" / "css"
        (_d / "app").mkdir(parents=True)
        (_d / "app" / "layout.css").write_text(FIXTURE_CSS, encoding="utf-8")  # dev 形状
        (_d / "abc123.css").write_text(FIXTURE_CSS, encoding="utf-8")          # build 形状
        got = css_artifacts("web", _t)
        names = sorted(p.name for p in got)
        arm("P8 产物定位认得 **dev** 形状（`css/app/layout.css`）",
            "layout.css" in names, f"→ {names}")
        arm("P9 产物定位认得 **build** 形状（`css/<hash>.css`）",
            "abc123.css" in names, f"→ {names}")
        arm("P10 无 `.next` 时返回空表（不抛错）",
            css_artifacts("nope", _t) == [], f"→ {css_artifacts('nope', _t)}")
        _m: dict[str, str] = {}
        for _p in got:
            for _k, _v in parse_leading(_p.read_text(encoding="utf-8")).items():
                _m.setdefault(_k, _v)
        arm("P11 两种形状的 CSS 都能喂进 parse_leading",
            _m.get("leading-relaxed") == "1.625", f"→ {_m}")

    # ---- 行高折算 ----
    arm("R1 无单位值即比值", css_ratio("1.625", 14.0) == 1.625, f"→ {css_ratio('1.625', 14.0)}")
    arm("R2 rem 折算（1rem/14px = 1.143）",
        css_ratio("1rem", 14.0) == round(16 / 14, 4), f"→ {css_ratio('1rem', 14.0)}")
    arm("R3 px 折算（20px/14px）",
        css_ratio("20px", 14.0) == round(20 / 14, 4), f"→ {css_ratio('20px', 14.0)}")
    arm("R4 折算不出（var()）⇒ None", css_ratio("var(--x)", 14.0) is None,
        f"→ {css_ratio('var(--x)', 14.0)}")

    # ---- T1 字重 ----
    bad_blocks = [{"where": "F:1", "lits": ["text-body font-bold"]}]
    p, _ = judge_weight(bad_blocks, ["500", "600"], {})
    arm("W1 `font-bold` 必须红", any("font-bold" in x for x in p), f"→ {p}")
    ok_blocks = [{"where": "F:1", "lits": ["text-body font-medium font-semibold"]}]
    p, _ = judge_weight(ok_blocks, ["500", "600"], {})
    arm("W2 合规字重必须绿（对照）", p == [], f"→ {p}")
    p, _ = judge_weight([{"where": "F:1", "lits": ["font-[700]"]}], [], {})
    arm("W3 任意值 `font-[700]` 必须红", any("T1c" in x for x in p), f"→ {p}")
    p, _ = judge_weight([], ["700"], {})
    arm("W4 preset `fontWeight: 700` 必须红", any("T1b" in x for x in p), f"→ {p}")
    p, n2 = judge_weight([], [], {})
    arm("W5 preset 字重覆盖率为 0 时必须留痕",
        any("覆盖率 0" in x for x in n2), f"→ {n2}")

    # ---- T2 行高（夹具由 parse_leading 产出，形状与被测一致）----
    def t2(lits: list[str]) -> tuple[list[str], int]:
        blk = [{"where": "F:1", "lits": lits}]
        pr, _, j = judge_line_height(blk, None, sizes, lead)
        return pr, j

    p, j = t2(["text-body-sm leading-relaxed"])
    arm("L1 正文档 + leading-relaxed(1.625) 必须红",
        any("T2]" in x and "1.625" in x for x in p), f"→ {p}")
    arm("L2 上面那条必须真的判到了（覆盖率 > 0）", j == 1, f"→ judged={j}")
    p, _ = t2(["text-body leading-[1.85]"])
    arm("L3 正文档 + leading-[1.85] 必须绿（对照）", p == [], f"→ {p}")
    p, j = t2(["text-caption leading-relaxed"])
    arm("L4 caption 不是正文 ⇒ 不判（对照）", p == [] and j == 0, f"→ {p} judged={j}")
    p, _ = t2(["text-body-sm leading-relaxed text-ink-700"])
    arm("L5 颜色类不影响行高判定", any("T2]" in x for x in p), f"→ {p}")
    p, _ = t2(["text-body-sm leading-4"])
    arm("L6 绝对值 leading-4(16px/13px=1.231) 必须红",
        any("T2]" in x for x in p), f"→ {p}")
    # 边界
    edge = {**lead, "leading-relaxed": "1.65"}
    p, _, _ = judge_line_height([{"where": "F:1", "lits": ["text-body-sm leading-relaxed"]}],
                                None, sizes, edge)
    arm("L7 恰好 1.65 必须绿（边界下沿）", p == [], f"→ {p}")
    edge = {**lead, "leading-relaxed": "1.6499"}
    p, _, _ = judge_line_height([{"where": "F:1", "lits": ["text-body-sm leading-relaxed"]}],
                                None, sizes, edge)
    arm("L8 1.6499 必须红（边界上沿）", any("T2]" in x for x in p), f"→ {p}")

    # ---- T2b cn() 吞字号（实跑真实 twMerge）----
    # 🚨 **两条臂缺一不可**：
    #   · 库默认配置（= `cn.ts` **没**修好）⇒ **必须吞** —— 「判据测得出吞类」的机制臂；
    #   · 项目配置（= 读 `cn.ts` 的 `FONT_SIZE_TOKENS`）⇒ **不得吞** —— 对照臂。
    # 只留前者 ⇒ 修好 `cn.ts` 后判据**永久假红**；只留后者 ⇒ 判据坏了也看不出来。
    groups = [
        ["text-body-sm leading-relaxed", "bg-solid-brand text-white"],   # 吞
        ["text-body-sm leading-relaxed", "bg-brand-600"],                # 对照：不同组
    ]
    blk_a = [{"where": "A:1", "lits": groups[0], "is_cn": True},
             {"where": "B:1", "lits": groups[1], "is_cn": True}]
    mg = twmerge_blocks(groups)                      # 库默认配置
    if mg is None:
        skipped.append("M1/M2 真实 twMerge 不可用 ⇒ 吞类判据未被证明")
    else:
        p, _, _ = judge_line_height(blk_a, mg, sizes, lead)
        arm("M1 `text-body-sm` 被 `text-white` 吞掉 ⇒ 必须红（机制臂·库默认配置）",
            any("T2b" in x and "A:1" in x for x in p), f"→ {p}")
        arm("M2 对照组（只加 bg-*，不同组）⇒ 不得报 T2b",
            not any("T2b" in x and "B:1" in x for x in p), f"→ {p}")

    # ---- M5–M10：**项目配置**臂（`cn.ts` 修好后的行为）----
    fixture_sizes = ["body-sm", "body", "h2"]
    mg_p = twmerge_blocks(groups, fixture_sizes)
    if mg_p is None:
        skipped.append("M5–M10 真实 twMerge 不可用 ⇒ 项目配置臂未被证明")
    else:
        p, _, j = judge_line_height(blk_a, mg_p, sizes, lead)
        arm("M5 **项目配置**下 `text-body-sm` 存活 ⇒ 不得报 T2b（对照臂）",
            not any("T2b" in x for x in p), f"→ {p}")
        arm("M6 **项目配置**下 T2 覆盖率 = 2（字号不再被吞 ⇒ T2 不再瞎）",
            j == 2, f"→ judged={j}")
        # 反证①：`sizes` 传空 ⇒ 退回库默认 ⇒ **必须重新吞**（证明 M5 不是恒绿）
        mg_d = twmerge_blocks(groups, [])
        arm("M7 `sizes=[]` 退回库默认 ⇒ `text-body-sm` 必须重新被吞（M5 非恒绿）",
            mg_d is not None and "text-body-sm" not in mg_d[0], f"→ {mg_d}")
        # 反证②：**未登记**的档仍被吞 ⇒ extend 只放行登记过的档，不是万能放行
        mg_u = twmerge_blocks([["text-h5 text-ink-900"]], fixture_sizes)
        arm("M8 **未登记**的 `text-h5` 仍被吞 ⇒ extend 不是万能放行",
            mg_u is not None and "text-h5" not in mg_u[0], f"→ {mg_u}")
        # 提取器：正向（用**夹具源码**，不读真实 `cn.ts` ——
        # 自检测的是**判据**，不该随产品当前状态变红/绿）
        fixture_cn = 'const FONT_SIZE_TOKENS = ["body-sm", "body", "h2"] as const;'
        got = cn_font_size_tokens(src=fixture_cn)
        arm("M9 提取器能从源码读出登记档",
            got == ["body-sm", "body", "h2"], f"→ {got}")
    # 提取器：反向（读不到必须 None，**不得**静默给空列表）
    arm("M10 提取器读不到标记 ⇒ 必须 None（不得静默给空列表）",
        cn_font_size_tokens(src="export const cn = () => '';") is None,
        f"→ {cn_font_size_tokens(src='export const cn = () => 1;')}")

    # ---- M11/M12：**合法冲突**不得报 T2b ----
    # 被同组**另一个字号类**覆盖 ≠ 回落到继承值（实测 `DataTable.tsx:628` 数字列）。
    conflict = [{"where": "C:1", "is_cn": True,
                 "lits": ["truncate text-body-sm text-ink-800",
                          "num text-[11px] leading-5"]}]
    p, nt, _ = judge_line_height(conflict, twmerge_blocks([conflict[0]["lits"]]), sizes, lead)
    arm("M11 同块另一个字号类覆盖 ⇒ 不得报 T2b（合法冲突）",
        not any("T2b" in x for x in p), f"→ {p}")
    arm("M12 上述「合法冲突」必须**留 note**（不得静默丢弃）",
        any("T2b" in x and "合法冲突" in x for x in nt), f"→ {nt}")
    arm("M13 任意值里只有**像长度**的才算字号（`[rgb(var(--x))]` 是颜色 ⇒ 不得算）",
        _is_arb_length("[11px]") and _is_arb_length("[1.5rem]") and _is_arb_length("[12]")
        and not _is_arb_length("[rgb(var(--text-muted))]")
        and not _is_arb_length("[var(--text-muted)]"), "")

    # ---- M14/M15/M16：T2c「preset 每个字号档在项目配置下必须存活」----
    # 🚨 这三条臂针对的是 **#42 可被重新打开**这件事：判据原先用硬编码清单，
    #    preset 新增一个档后**四条判据没有一条会红**。用合成夹具（不读真实 preset），
    #    这样自检测的是**判据**，不随产品当前状态变红/绿。
    fake_preset = {"body-sm": 13.0, "body": 14.0, "h2": 22.0, "h5": 17.0}   # h5 未登记
    pb = size_registration_probe_blocks(fake_preset)
    pb_p = twmerge_blocks(pb, fixture_sizes)          # 项目配置只登记了 body-sm/body/h2
    if pb_p is None:
        skipped.append("M14–M16 真实 twMerge 不可用 ⇒ T2c 未被证明")
    else:
        t2c = judge_size_registration(fake_preset, pb_p)
        arm("M14 preset 里有、`cn.ts` 未登记的档（`h5`）⇒ T2c 必须红",
            any("h5" in x and "T2c" in x for x in t2c), f"→ {t2c}")
        arm("M15 已登记的档（`body-sm`/`body`/`h2`）⇒ 不得报 T2c（对照臂）",
            not any("body-sm" in x or "h2" in x for x in t2c), f"→ {t2c}")
        # 反证：全部登记 ⇒ T2c 必须一条都不报（证明 M14 不是恒红）
        all_reg = twmerge_blocks(size_registration_probe_blocks(fake_preset),
                                 list(fake_preset))
        arm("M16 全部登记 ⇒ T2c 必须零条（M14 非恒红）",
            all_reg is not None and judge_size_registration(fake_preset, all_reg) == [],
            f"→ {all_reg}")

    # ---- 分流：非 cn() 块**不得**被 twMerge 合并 ----
    # 实测踩到：对字面量 className 套 twMerge ⇒ 753 条假红 + T2 覆盖率归零。
    raw_lits = ["text-body-sm leading-relaxed text-ink-700"]
    plain = [{"where": "P:1", "lits": raw_lits, "is_cn": False}]
    p, _, j = judge_line_height(plain, twmerge_blocks([raw_lits], fixture_sizes), sizes, lead)
    arm("M3 字面量 className 不得报 T2b（twMerge 无发言权）",
        not any("T2b" in x for x in p), f"→ {p}")
    arm("M4 字面量 className 的 T2 必须判到（覆盖率 > 0）", j == 1, f"→ judged={j}")

    # ---- T3 渲染判据 ----
    def mk(fs: float, lh: float | None, fw: str = "400", txt: str = "这是一段足够长的正文内容用于判定",
           tag: str = "p", cls: str = "") -> dict:
        return {"tag": tag, "cls": cls, "fs": fs, "fw": fw, "lh": lh,
                "ratio": (round(lh / fs, 4) if lh else None), "lead": [], "txt": txt}

    p, _, _ = judge_render({"texts": [mk(13.0, 13.0 * 1.625)]})
    arm("N1 13px/1.625 必须红", any("T3]" in x for x in p), f"→ {p}")
    p, _, _ = judge_render({"texts": [mk(14.0, 14.0 * 1.7)]})
    arm("N2 14px/1.7 必须绿（对照）", p == [], f"→ {p}")
    p, _, _ = judge_render({"texts": [mk(16.0, 16.0 * 1.625)]})
    arm("N3 16px/1.625（字号类被吞后的样子）必须红",
        any("T3]" in x for x in p), f"→ {p}")
    p, _, _ = judge_render({"texts": [mk(16.0, 16.0 * 1.5, fw="600", tag="h4")]})
    arm("N4 16px/600 标题档 ⇒ 不判（对照）", p == [], f"→ {p}")
    p, _, _ = judge_render({"texts": [mk(13.0, 13.0 * 1.5, txt="—")]})
    arm("N5 短文本（<8 字）⇒ 不判（对照）", p == [], f"→ {p}")
    p, _, _ = judge_render({"texts": [mk(13.0, 13.0 * 1.65)]})
    arm("N6 恰好 1.65 必须绿（边界）", p == [], f"→ {p}")
    p, _, _ = judge_render({"texts": [mk(12.0, 12.0 * 1.2)]})
    arm("N7 12px（caption 档）⇒ 不判（对照）", p == [], f"→ {p}")
    arm("N8 覆盖率可观测：3 个候选",
        render_coverage({"texts": [mk(13.0, 21.0), mk(14.0, 24.0), mk(16.0, 26.0),
                                   mk(11.0, 16.0)]}) == 3)

    # ---- T3b 字号类落地（渲染级）----
    sz = {"body-sm": 13.0, "label": 12.0}

    def mk2(cls: str, fs: float) -> dict:
        return {"tag": "td", "cls": cls, "fs": fs, "fw": "400", "lh": fs * 1.7,
                "ratio": 1.7, "lead": [], "txt": "示例文本"}

    p, c = judge_render_sizes({"texts": [mk2("px-4 text-body-sm text-ink-700", 16.0)]}, sz)
    arm("S1 类含 `text-body-sm` 但实测 16px ⇒ 必须红",
        any("T3b" in x for x in p), f"→ {p}")
    arm("S2 T3b 覆盖率可观测", c == 1, f"→ {c}")
    p, _ = judge_render_sizes({"texts": [mk2("text-body-sm", 13.0)]}, sz)
    arm("S3 类含 `text-body-sm` 且实测 13px ⇒ 绿（对照）", p == [], f"→ {p}")
    p, _ = judge_render_sizes({"texts": [mk2("text-ink-700", 16.0)]}, sz)
    arm("S4 无字号类 ⇒ 不判（对照）", p == [], f"→ {p}")
    p, _ = judge_render_sizes({"texts": [mk2("text-label", 16.0)]}, sz)
    arm("S5 `text-label`（声明 12px）实测 16px ⇒ 必须红",
        any("T3b" in x for x in p), f"→ {p}")

    # ---- T4 规则 3（2026-09-24 升级：**判**「钩子值 == 规范现读值」）----
    CSS_OK = "frontend/packages/ui/src/styles.css"
    SPEC_OK = "3. **中英混排加 0.25em 间隙** —— 避免「3件」这类粘连。"
    css_def = "html .cjk-gap {\n  margin-inline: 0.25em;\n}\n"

    # ① 「只报」的那一半（沿用原有两臂）
    _p, notes, _e = judge_rule3({"a.tsx": "<div className='cjk-gap' />"}, SPEC_OK)
    arm("C1 有调用点 ⇒ 不报「未落地」", not any("无任何调用点" in x for x in notes),
        f"→ {notes}")
    _p, notes, _e = judge_rule3({"a.tsx": "<div />", CSS_OK: css_def}, SPEC_OK)
    arm("C2 定义处不算调用点；0 调用 ⇒ 报「未落地」",
        any("无任何调用点" in x for x in notes), f"→ {notes}")

    # ② 「判」的那一半：**两侧都要覆盖** ——
    #    门禁里一个一直为真的条件，它的 `if` 侧可能**从写下那天起就没执行过**
    #    （`--page-pad` 那次就是这么栽的：`any()` 返回 bool ⇒ 打出 `--True` 没人看见）。
    p, notes, env = judge_rule3({CSS_OK: css_def}, SPEC_OK)
    arm("C3 钩子 0.25em == 规范现读 0.25em ⇒ **绿**", p == [] and env == [],
        f"→ p={p} env={env}")
    arm("C3b 且**确实判了**（不是静默跳过）",
        any("== §3.3 现读值" in x for x in notes), f"→ {notes}")

    p, notes, env = judge_rule3({CSS_OK: css_def.replace("0.25em", "0.3em")}, SPEC_OK)
    arm("C4 钩子 0.3em ≠ 规范 0.25em ⇒ **必须红**",
        len(p) == 1 and "不同步" in p[0], f"→ {p}")

    p, notes, env = judge_rule3(
        {CSS_OK: "html .cjk-gap {\n  margin-inline: 0.250em;\n}\n"}, SPEC_OK)
    arm("C4b 归一化：`0.250em` == `0.25em` ⇒ **绿**（写法不同、值相同）",
        p == [] and env == [], f"→ p={p} env={env}")

    p, notes, env = judge_rule3({CSS_OK: "html .cjk-gap {\n  margin: 0;\n}\n"}, SPEC_OK)
    arm("C5 块在但没有 `margin-inline` ⇒ **必须红**",
        len(p) == 1 and "margin-inline" in p[0], f"→ {p}")

    p, notes, env = judge_rule3({CSS_OK: "/* 钩子被删了 */"}, SPEC_OK)
    arm("C6 `html .cjk-gap` 定义消失 ⇒ **必须红**（「被删了」≠「解析不了」）",
        len(p) == 1 and "一处都没有" in p[0], f"→ {p} env={env}")

    # ③ env 侧：**我没测成** ≠ 产品缺陷（混了就是假红）
    p, notes, env = judge_rule3({CSS_OK: css_def}, "3. 中英混排加个间隙吧。")
    arm("C7 规范锚点命中 0 处 ⇒ **env（不判）**，且**不得**报产品缺陷",
        p == [] and len(env) == 1 and "拿不到期望值" in env[0], f"→ p={p} env={env}")

    p, notes, env = judge_rule3(
        {CSS_OK: "html .cjk-gap{margin-inline:.25em}\nhtml .cjk-gap{margin-inline:.25em}\n"},
        SPEC_OK)
    arm("C8 钩子块命中 2 处 ⇒ **env（不判）**，不报产品缺陷",
        p == [] and len(env) == 1 and "期望 1 处" in env[0], f"→ p={p} env={env}")

    p, notes, env = judge_rule3({"a.tsx": "x"}, SPEC_OK)
    arm("C9 扫描集里没有 `styles.css` ⇒ **env（不判）**",
        p == [] and len(env) == 1 and "没有" in env[0], f"→ p={p} env={env}")

    # ---- 收口 ----
    arm("X1 覆盖率 0 ⇒ exit 2", summary_exit(0, [], []) == 2)
    arm("X2 有缺陷 ⇒ exit 1", summary_exit(5, ["x"], []) == 1)
    arm("X3 环境坏 ⇒ exit 2（即使有缺陷）", summary_exit(5, ["x"], ["env"]) == 2)
    arm("X4 全绿 ⇒ exit 0", summary_exit(5, [], []) == 0)

    print(f"── 自检 {n - len(fails) - len(skipped)}/{n} 通过"
          f"{'，跳过 ' + str(len(skipped)) if skipped else ''}")
    for s in skipped:
        print(f"   [skip] {s}")
    for f in fails:
        print(f"   ✗ {f}")
    if fails or skipped:
        return 2
    print("   ✓ 每条判据都证明过会红，且对照臂干净")
    return 0


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="§3.3 中文排印三条硬规则门禁")
    ap.add_argument("--self-test", action="store_true", help="只跑自检（无浏览器 / 无网络）")
    ap.add_argument("--source-only", action="store_true", help="只跑源码层与产物层（不开浏览器）")
    ap.add_argument("--dump", action="store_true", help="诊断模式：打原始数值，不下判断")
    ap.add_argument("--why", default=None, metavar="TEXT", help="取证：打印含该文本的元素")
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
        problems, notes, judged, env = run_static()
        print()
        for x in notes:
            print(f"   [note] {x}")
        for x in problems:
            print(f"   ✗ {x}")
        for x in env:
            print(f"   [env] {x}")
        print(f"\n── 源码/产物层：判对数 {judged}，缺陷 {len(problems)} 条")
        return summary_exit(judged, problems, env)

    problems, notes, judged, env = run_static()
    sizes = parse_preset_sizes()
    rp, cov_lh, cov_size, env_break = asyncio.run(run_render(args.app, sizes))
    problems += rp
    total = judged + cov_lh + cov_size
    env_break = list(env) + env_break          # 源码层的 env 也要报（T4 的规范锚点）
    print()
    for x in notes:
        print(f"   [note] {x}")
    for x in problems:
        print(f"   ✗ {x}")
    for x in env_break:
        print(f"   [env] {x}")
    print(f"\n── 判对数 源码层 {judged} + 渲染行高 {cov_lh} + 渲染字号 {cov_size}"
          f" = {total}；缺陷 {len(problems)} 条")
    return summary_exit(total, problems, env_break)


if __name__ == "__main__":
    sys.exit(main())
