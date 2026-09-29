#!/usr/bin/env python
"""§6.1「按钮」+ §8.3「触控目标与拇指热区」审计 —— 源码层 + 渲染级，此前**没有任何判据**。

> 本节号出自 `deliverables/ui-design/design-spec.md`。**必须点名文档**：
> 另两份移动端规范也有 `§6.1`（TabBar 接入方式）与 `§8.3`，
> 不点名会让覆盖率矩阵判为「归因歧义」而把本门禁的引用**整个丢掉**。

用法：
    python evidence/verify_buttons.py --self-test
    python evidence/verify_buttons.py --source-only
    python evidence/verify_buttons.py [--app web] [--dump] [--why web /]
    python evidence/verify_buttons.py --app web --path /knowledge

退出码：`0` 通过 / `1` 产品缺陷 / `2` 环境或测量问题。

═══════════════════════════════════════════════════════════════════════════════
为什么需要这个门禁（第七轮覆盖率审计的产物）
═══════════════════════════════════════════════════════════════════════════════

§6.1 只有两句话，但**两句都带硬约束**：

> 5 变体（`pri` / `vfy` / `sec` / `gho` / `dgr`）× 3 尺寸（桌面 28 / 34 / 40px，**移动端统一 48px**）。
> 主色按钮**全局唯一**——每屏最多一个 `pri`，用于该屏最关键动作。

**这两条此前一条判据都没有。** 实测：`evidence/*.py` 里 grep
`主色按钮|全局唯一|每屏最多` ⇒ **0 命中**；grep `pri\b` ⇒ 只命中
`--text-primary`（对比度门禁里毫不相关的令牌名）。

而「48px」那一半看似已被覆盖，实际**被三个门禁互相推给了对方**：

| 门禁 | 它自己的声明 |
|---|---|
| `verify_tap_targets.py` | 「只扫**图标按钮**（有 `svg` 且无可见文字）。**带文字的按钮不在本判据范围**」 |
| `verify_mobile_safe_area.py` | 只查**底栏**那一批 |
| `verify_im_safe_area.py` | 只查 **TabBar** 的触控目标 |
| `verify_mobile_actionbar.py` | 只查**底部操作条** |

⇒ 四个门禁覆盖的都是「底栏 / TabBar / 操作条 / 图标按钮」，
**普通正文区里带文字的按钮，一个门禁都没测**。
这正是 `evidence/README.md` 里那条教训的又一形态：
**「已覆盖」是四个门禁各自以为别人管了。**

═══════════════════════════════════════════════════════════════════════════════
判据的出处（逐条可指）
═══════════════════════════════════════════════════════════════════════════════

| 判据 | 断言 | 出处 |
|---|---|---|
| **B1** | `--solid-brand` == `--brand-600` == `39 76 147`；主色按钮底色只有一个来源 | `tokens.css:158`（`--solid-brand: 39 76 147; /* #274C93 白字对比 8.3:1 */`）+ §2.2 表（`--brand-600` 用途列「**主按钮**、激活导航、链接」） |
| **B2** | Button 三尺寸 == 28 / 34 / 40px；且三档都带 `min-h-tap` + `sm:min-h-0` | §6.1 第 1 句；`--tap: 48px`（`tokens.css:228`「触控目标最小边长」） |
| **B3** | variant 集合：规范声明 **5**，实现 **7** | §6.1 第 1 句 vs `Button.tsx:9`（「`outline` 与 `accent` 为兼容保留」） |
| **B4** | 交互元素上**不得**硬编码 `h-7/h-8/h-9`（= 28/32/36px ⇒ 移动端不足 48） | §6.1「**移动端统一 48px**」 |
| **B5** | 每屏 `primary` 按钮数 **≤ 1** | §6.1 第 2 句 |
| **B6** | 按钮触控目标 ≥ **48×48**（移动端档） | §8.3「本项目标准 **48 × 48px**」+ `--tap: 48px`；**可点整行**的列表标题按钮另见 §8.3「**列表行高 ≥ 48px**」 |

**为什么 B1 需要单列**：`--solid-brand` 与 `--brand-600` 是**两个令牌、同一个值**。
只要有人只改其中一个，主色按钮的底色就会分裂成两种蓝，
而**任何一个「底色 == 某个令牌」的判据都还能通过** —— 与焦点环那次
「定义了没人用」同族：**同一个语义有两个来源，本身就是缺陷源**。

**为什么 B5 是「集合大小」类判据**：与焦点环的「统一」一样，
`len(set) == 1` 是**关于集合大小**的断言 —— **单看一处永远看不出「不唯一」**。

═══════════════════════════════════════════════════════════════════════════════
必须区分的合法兄弟（否则就是假红）
═══════════════════════════════════════════════════════════════════════════════

| 看起来像 | 实际是 | 判据怎么处理 |
|---|---|---|
| 分页「当前页」也是 `bg-brand-600` 实底 | **激活态指示**，不是 CTA；`Pagination.tsx:117` 明确写了 `aria-current="page"` | B5 **排除** `aria-current="page"` 的元素（用 DOM 语义标记，不用类名启发式） |
| `<a href>` / `.text-link` 的文字链接只有 18–24px 高 | **内联文字链接**，WCAG 2.5.8 有明确的 inline 例外（尺寸被行高约束） | B6 **豁免**：只判 `<button>` / `[role=button]`，且类名含 `text-link` 的排除 |
| 图标按钮盒子 32×32 | 热区由 `.tap-ghost` 撑到 48 | 本门禁**不管**（那是 `verify_tap_targets.py` 的范围，它已用 `elementFromPoint` 实测热区） |

> ⚠️ 上一条与 `verify_tap_targets.py` 是**互补、不重叠**的关系：
> 那个门禁管**图标按钮**（量热区，因为盒子可能只有 20px），
> 本门禁管**带文字的按钮**（量热区，因为 `.tap-ghost` 同样可能撑开）。
> 两者都量热区，但**元素集合互斥**。

═══════════════════════════════════════════════════════════════════════════════
判据设计上踩过的坑（写在这里，免得下一个人重踩）
═══════════════════════════════════════════════════════════════════════════════

1. **JS 只测量、Python 做判定。**
   第一版把判定写在 JS 里，结果是自检只能靠真跑浏览器（秒级变分钟级，且要登录）。
   拆成「JS 返回原始测量值 → Python 纯函数判定」后，**全部判据都能用夹具自检**。
   （与 `verify_focus_ring.py` 同构。）

2. **`variant` 的默认值是 `primary`**（`Button.tsx:52`）。
   ⇒ 「数 primary 按钮」**不能只数 `variant="primary"`**：
   不写 `variant` 的 `<Button>` 渲染出来就是主色按钮。
   实测 `apps/web/.../contract-review/page.tsx` 有 **3 个裸 `<Button>`**。

3. **`variant={expr}` 是条件变体，静态数不出来。**
   实测 `variant={showForm ? "outline" : "primary"}` 这类写法有 3 处。
   ⇒ 源码层的计数**只作为线索**（`--dump` 报出），**判定以渲染层为准**。

4. **主色按钮的文字是 `rgb(20,24,29)`（ink-900），不是白色。**
   实测：`<Button variant="primary">` 渲染出来 `color=rgb(20, 24, 29)`，
   `text-white` **不在 class 列表里** —— 被 `cn()` 的 `tailwind-merge` 吞了
   （`sizeStyles` 里的 `text-body-sm` / `text-label` / `text-body` 是**字号**工具类，
   但 `text-` 前缀被判成**文字颜色**，与 `variantStyles` 的 `text-white` 冲突，
   `sizeStyles` 在后 ⇒ 保留 `text-body-sm`、丢掉 `text-white`）。
   后果是主色按钮文字对比度 **2.16:1**（hover 时 `brand-700` 更低到 **1.67:1**）。
   ⇒ **本门禁不重复判它**：`verify_contrast.py` 的 T4 已经报出（实测 4 处 `2.16:1`）。
   **已登记为 #42**。这里记下来是因为**B5 的判据必须不依赖文字颜色** ——
   若按「底色 brand-600 且文字白」判 primary，会**一个都测不到**（假绿 15 处同族陷阱）。

5. **`scrollIntoView()` 之后必须等一帧再读 `getBoundingClientRect()`。**
   第一版 `SCAN_JS` 是**同步 IIFE** ⇒ 读到的矩形是**滚动前**的，
   与 `elementFromPoint` 的布局不匹配 ⇒ 实测 `box 98×56 hot 0×0`。
   若据此判红，就是把「我没测成」说成产品缺陷。
   ⇒ 改成 `async` IIFE + `await requestAnimationFrame`（`cdp.evaluate` 已带 `awaitPromise`）。

6. **中心点戳不到自己时，要报出「中心点站着谁」。**
   实测 im 的 TabBar 第一项（`工作台`，98×56）中心点站着 **`nextjs-portal`** ——
   **Next.js 开发模式的浮动标识**盖住了左下角。
   这是**环境产物**，不是产品缺陷。有了 `centerEl` 这一栏，
   「量不到」与「点不到」一眼可分（否则只能靠猜）。

7. **条件渲染的元素，渲染层看不到，源码层看得到。**
   实测 `im/chat` 的 `案件`（`h-8`，`xl:hidden`）与 `发送`（`h-9`，需 `activeConv`）
   在 390 宽的演示数据下**根本没渲染** ⇒ 渲染层扫不到它们。
   这正是**源码层判据不可省**的理由：渲染层的「覆盖率」受数据与视口影响。
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
FE = ROOT / "frontend"
BUTTON_TSX = FE / "packages" / "ui" / "src" / "components" / "Button.tsx"
TOKENS_CSS = FE / "packages" / "ui" / "src" / "tokens.css"

# ── 期望值（全部来自出处，不硬编码） ─────────────────────────────────
SPEC_VARIANTS = 5                      # §6.1「5 变体（pri/vfy/sec/gho/dgr）」
SPEC_SIZES = {"sm": 28.0, "md": 34.0, "lg": 40.0}   # §6.1「桌面 28 / 34 / 40px」
SPEC_TAP = 48.0                        # §8.3「本项目标准 48 × 48px」
# 热区用整数像素逐格扫描量出，粒度 ±1px ⇒ 门槛正下方这一段不判（见 judge_targets）
CRITICAL_BAND = 1.0
SOLID_BRAND = (39.0, 76.0, 147.0)      # #274C93 == --brand-600 == --solid-brand
SPEC_PRIMARY_PER_SCREEN = 1            # §6.1「每屏最多一个 pri」

# 硬编码高度 ⇒ 移动端不足 48px（h-7=28 / h-8=32 / h-9=36 / h-10=40）
BAD_HEIGHTS = {"h-7": 28.0, "h-8": 32.0, "h-9": 36.0, "h-10": 40.0}
# `h-12` = 48px 合规；`h-11` = 44px —— 只够 WCAG 2.5.5，不够本项目标准，故不列入
H_12 = 48.0

VIEWPORTS: tuple[tuple[int, int, bool, str], ...] = ((390, 844, True, "390"),)
APPS: dict[str, dict] = {
    "web": {"port": 3000, "user": "ent_admin",
            "pages": ("/", "/qa", "/documents", "/contract-review", "/compliance",
                      "/knowledge", "/billing")},
    "lawyer": {"port": 3001, "user": "lawyer_wang",
               "pages": ("/", "/dispatches", "/cases", "/reviews", "/archives", "/notifications")},
    "admin": {"port": 3002, "user": "admin",
              "pages": ("/", "/reviews", "/cases", "/dispatches", "/compliance",
                        "/billing", "/complaints", "/audit")},
    "im": {"port": 3003, "user": "client",
           # `@first-case` 是占位符，运行时从 /cases 的 DOM 读真实 id。
           "pages": ("/", "/chat", "/cases", "/cases/@first-case", "/me")},
}

MIN_CONTENT = 30


# ===========================================================================
# 源码解析（纯函数 —— 可夹具自检）
# ===========================================================================
def _tag_end(src: str, i: int) -> int:
    """从 `<Name` 的下标 `i` 出发，返回该**开标签**的 `>` 下标（找不到返回 -1）。

    ⚠️ **必须跟踪 `{}` 深度与引号**：JSX 属性里 `onClick={() => f()}` 的 `>` 不是标签结束。
    第一版用 `src.find(">")` ⇒ `variant={showForm ? "outline" : "primary"}` 这类
    表达式里的 `>` 会把标签**截断在半路**，后面的 `h-9` 就扫不到。
    """
    depth = 0
    q: str | None = None
    j = i
    while j < len(src):
        c = src[j]
        if q:
            if c == q:
                q = None
            elif c == "\\":
                j += 1
        elif c in "\"'`":
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            return j
        j += 1
    return -1


def iter_tags(src: str, name: str) -> list[tuple[str, int]]:
    """返回源码里所有 `<name ...>` 开标签的 `(标签文本, 结束下标)`。

    ⚠️ **必须把结束下标一起返回**：可见文字在**标签之后**（`<button ...>刷新</button>`），
    只看标签文本取不到文字 ⇒ 会把所有按钮都判成「图标按钮」而**一条都不报**。
    实测第一版就是这样：`h_bad == []`，自检臂 B4-a/B4-e 同时报红才发现。
    """
    out: list[tuple[str, int]] = []
    for m in re.finditer(r"<" + re.escape(name) + r"(?=[\s/>])", src):
        end = _tag_end(src, m.start())
        if end > 0:
            out.append((src[m.start():end + 1], end))
    return out


def top_level_keys(body: str) -> list[str]:
    """对象字面量的**顶层**键。

    ⚠️ 不能用 `re.findall(r"(\\w+)\\s*:")`：值的字符串里含 `hover:bg-brand-700`
    这种伪键，会被一并抓成 `hover` 变体 ⇒ B3 误报「多出一个变体」。
    实测就是这个坑（自检臂 P7 / B3-a 同时报红）。
    """
    keys: list[str] = []
    depth = 0
    q: str | None = None
    i = 0
    while i < len(body):
        c = body[i]
        if q:
            if c == q:
                q = None
        elif c in "\"'`":
            q = c
        elif c in "{[(":
            depth += 1
        elif c in "}])":
            depth -= 1
        elif depth == 0:
            m = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:", body[i:])
            if m:
                keys.append(m.group(1))
                i += m.end()
                continue
        i += 1
    return keys


def class_tokens(tag: str) -> list[str]:
    """把开标签里所有字符串字面量拼起来当类名候选（`className="a b"` / `cn("a","b")` 都能覆盖）。"""
    toks: list[str] = []
    for m in re.finditer(r'["\'`]([^"\'`]*)["\'`]', tag):
        toks.extend(m.group(1).split())
    return toks


def parse_button_sizes(src: str) -> dict[str, set[str]]:
    """从 `Button.tsx` 解析 `sizeStyles`：size → 类名集合。"""
    m = re.search(r"const\s+sizeStyles\s*(?::[^=]*)?=\s*\{", src)
    if not m:
        return {}
    body = _object_body(src, m.end() - 1)
    out: dict[str, set[str]] = {}
    for sm in re.finditer(r"([\w$]+)\s*:\s*[\"'`]([^\"'`]*)[\"'`]", body):
        out[sm.group(1)] = set(sm.group(2).split())
    return out


def parse_variants(src: str) -> set[str]:
    """从 `Button.tsx` 解析 `variantStyles` 的**顶层键**集合。"""
    m = re.search(r"const\s+variantStyles\s*(?::[^=]*)?=\s*\{", src)
    if not m:
        return set()
    body = _object_body(src, m.end() - 1)
    return set(top_level_keys(body))


def _object_body(src: str, i: int) -> str:
    """`i` 指向 `{`，返回配平的对象体（不含最外层花括号）。"""
    depth = 0
    q: str | None = None
    j = i
    while j < len(src):
        c = src[j]
        if q:
            if c == q:
                q = None
        elif c in "\"'`":
            q = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j]
        j += 1
    return ""


def parse_tokens(src: str) -> dict[str, str]:
    """解析 `:root` 里的令牌。`--solid-brand` 等主色来源就在这里。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"--([\w-]+)\s*:\s*([^;]+);", src):
        out.setdefault(m.group(1), m.group(2).strip())
    return out


def norm_rgb(value: str) -> tuple[float, float, float] | None:
    """把 `39 76 147` / `#274C93` / `rgb(39, 76, 147)` 归一成三元组。"""
    v = value.strip()
    m = re.match(r"^#([0-9a-fA-F]{6})$", v)
    if m:
        h = m.group(1)
        return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))
    m = re.match(r"^(\d+)\s+(\d+)\s+(\d+)$", v)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    m = re.match(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", v)
    if m:
        return (float(m.group(1)), float(m.group(2)), float(m.group(3)))
    return None


def height_px(token: str) -> float | None:
    """`h-7` → 28.0 · `h-[34px]` → 34.0 · `h-10` → 40.0（Tailwind 间距阶梯 4px/格）。"""
    m = re.match(r"^h-\[(\d+(?:\.\d+)?)px\]$", token)
    if m:
        return float(m.group(1))
    m = re.match(r"^h-(\d+(?:\.\d+)?)$", token)
    if m:
        return float(m.group(1)) * 4.0
    return None


def collect_tsx() -> list[tuple[str, str]]:
    """收集所有业务 `.tsx`（排除 `node_modules` / `.next`），返回 `(显示路径, 源码)`。

    ⚠️ **返回内存里的源码而不是 `Path`**：自检要用夹具字符串，
    若签名收 `Path` 就得把夹具写进临时目录，再 `relative_to(FE)` 就会炸
    （实测：`ValueError: ... is not in the subpath of ...`）。
    收 `(路径, 源码)` 后自检**完全不碰文件系统**。
    """
    out: list[tuple[str, str]] = []
    for app in ("apps", "packages"):
        for p in sorted((FE / app).rglob("*.tsx")):
            if "node_modules" in p.parts or ".next" in p.parts:
                continue
            out.append((p.relative_to(FE).as_posix(),
                        p.read_text(encoding="utf-8", errors="replace")))
    return out


def scan_hardcoded_heights(files: list[tuple[str, str]]) -> list[dict]:
    """B4：源码里在**原生** `<button>` / `<a>` 上硬编码 `h-7/h-8/h-9/h-10` 的位置。

    只扫**原生**元素 —— 用 `<Button size="...">` 的走组件，尺寸由 B2 保证。
    """
    out: list[dict] = []
    for rel, src in files:
        for name in ("button", "a"):
            for tag, end in iter_tags(src, name):
                toks = class_tokens(tag)
                bad = [t for t in toks if t in BAD_HEIGHTS]
                if not bad:
                    continue
                # 纯图标按钮（无文字）交给 `verify_tap_targets.py`，本判据只管带文字的
                label = _jsx_label(src, end)
                # ⚠️ `.text-link` 是项目的**内联文字链接**样式 ⇒ 与 B6 用**同一条豁免**。
                #    两处豁免不一致会出现「B4 说它 32px 违规、B6 说它豁免」的自相矛盾
                #    （实测：自检臂 B4-a 期望 1 条却得到 2 条，就是这条不一致暴露出来的）。
                inline = "text-link" in toks
                # ⚠️ **`min-h-tap` 是项目自己的补偿机制** ⇒ `h-9 min-h-tap` 在移动端
                #    实际高度是 **48px**（min-height 胜过 height），**不是缺陷**。
                #    实测踩到：`lawyer/notifications` 的两处「刷新 / 全部已读」写成
                #    `h-9 min-h-tap … sm:min-h-0`，源码里还有注释「`min-h-tap` 保证移动端
                #    48px 触控目标（规范 08 节），桌面回到 36px」——
                #    **正确实现被我的判据测成了缺陷**（假红）。
                #    渲染层的交叉印证：该页的「刷新」盒子在 390 宽下量到 48px，不在 <48 名单里。
                compensated = "min-h-tap" in toks
                out.append({
                    "file": rel,
                    "tag": name,
                    "heights": sorted(set(bad)),
                    "px": sorted({BAD_HEIGHTS[t] for t in bad}),
                    "label": label,
                    "icon_only": not label,
                    "exempt": inline,
                    "compensated": compensated,
                    "exempt_reason": ".text-link 内联文字链接（WCAG 2.5.8 inline 例外）" if inline else (
                        "带 `min-h-tap` ⇒ 移动端由 min-height 撑到 48px，非缺陷" if compensated else ""),
                    "cls": " ".join(toks)[:110],
                })
    return out


def _jsx_label(src: str, tag_end: int) -> str:
    """取元素**子节点**里的可见文字（用于区分「图标按钮」与「文字按钮」）。

    ⚠️ 第一版是「取标签后到下一个 `<` 之前的字符」，实测**产生假红**：
    `{showPassword ? (<Eye />) : (<EyeOff />)}` 的 `(` 出现在第一个 `<` 之前
    ⇒ 文字被读成 `'{showPassword ? ('` ⇒ **纯图标按钮被当成文字按钮**报红 4 处
    （`LoginShell` 的密码可见性切换、`im/chat` 的主题切换…）。
    ⇒ 正确做法是**真的走一遍子节点**：
      · 嵌套标签**整段跳过**（含它的属性 —— 否则 `className="h-4 w-4"` 会被当文字）；
      · `{}` 表达式里**只认字符串字面量**（`{cond ? "收起" : "新增"}` 能取到 `收起`）；
      · 顶层裸文本直接取。
    """
    i = tag_end + 1
    depth = 0                      # `{}` 深度
    buf: list[str] = []
    while i < len(src):
        if src.startswith("</", i):
            break                  # 闭合标签 ⇒ 子节点结束
        c = src[i]
        if c == "<":
            e = _tag_end(src, i)   # 跳过整个嵌套开标签（含属性与自闭合 `/`）
            if e < 0:
                break
            i = e + 1
            continue
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            if depth < 0:
                break
            i += 1
            continue
        if depth > 0:
            if c in "\"'`":
                j = src.find(c, i + 1)
                if j < 0:
                    break
                # ⚠️ **只认「值位置」的字符串**：`{theme === "dark" ? <Sun/> : <Moon/>}`
                #    里的 `"dark"` 是**比较运算的操作数**，不是渲染文字。
                #    第一版收了它 ⇒ 图标按钮被读成「文字 dark」⇒ 假红 2 处
                #    （`im/chat` 与 `AppLayout` 的主题切换按钮）。
                before = src[max(0, i - 6):i].rstrip()
                after = src[j + 1:j + 6].lstrip()
                if before.endswith(("==", "!=")) or after.startswith(("==", "!=")):
                    i = j + 1
                    continue
                buf.append(src[i + 1:j])
                i = j + 1
                continue
            i += 1
            continue
        buf.append(c)
        i += 1
    return re.sub(r"\s+", " ", "".join(buf)).strip()


# ===========================================================================
# 判据（纯函数）
# ===========================================================================
def judge_tokens(tokens: dict[str, str]) -> tuple[list[str], list[str]]:
    """B1：主色按钮底色只有一个来源。返回 (缺陷, 提示)。"""
    bad: list[str] = []
    note: list[str] = []
    b600 = norm_rgb(tokens.get("brand-600", ""))
    solid = norm_rgb(tokens.get("solid-brand", ""))
    if b600 is None:
        bad.append("`tokens.css` 里找不到 `--brand-600`")
    if solid is None:
        bad.append("`tokens.css` 里找不到 `--solid-brand`")
    if b600 and solid:
        if b600 != solid:
            bad.append(
                f"`--brand-600` {b600} ≠ `--solid-brand` {solid} ⇒ "
                "主色按钮底色有两个来源，改一个就会分裂成两种蓝")
        elif b600 != SOLID_BRAND:
            note.append(f"主色底色 == {b600}，规范 §2.2 写的是 #274C93 {SOLID_BRAND}")
    return bad, note


def judge_sizes(sizes: dict[str, set[str]]) -> list[str]:
    """B2：三尺寸高度 == 28/34/40 且都带 `min-h-tap`（移动端 48）。"""
    bad: list[str] = []
    if set(sizes) != set(SPEC_SIZES):
        bad.append(f"尺寸档集合 {sorted(sizes)} ≠ 规范 §6.1 的 {sorted(SPEC_SIZES)}")
    for name, want in SPEC_SIZES.items():
        toks = sizes.get(name)
        if toks is None:
            continue
        got = next((height_px(t) for t in toks if height_px(t) is not None), None)
        if got != want:
            bad.append(f"`{name}` 高度 {got}px ≠ 规范 §6.1 的 {want}px")
        if "min-h-tap" not in toks:
            bad.append(f"`{name}` 缺 `min-h-tap` ⇒ 移动端到不了 48px（§6.1「移动端统一 48px」）")
        if "sm:min-h-0" not in toks:
            bad.append(f"`{name}` 缺 `sm:min-h-0` ⇒ 桌面端会被 48px 撑高，与 28/34/40 冲突")
    return bad


def judge_variants(variants: set[str]) -> tuple[list[str], list[str]]:
    """B3：规范声明 5 变体；实现是超集只**报出**，不判缺陷（组件注释已声明为兼容保留）。"""
    bad: list[str] = []
    note: list[str] = []
    spec = {"primary", "verify", "secondary", "ghost", "danger"}
    missing = spec - variants
    extra = variants - spec
    if missing:
        bad.append(f"规范 §6.1 的 5 个变体里缺 {sorted(missing)}")
    if extra:
        note.append(
            f"实现多出 {len(extra)} 个变体 {sorted(extra)}（规范声明 5 个；"
            "`Button.tsx` 自述 `outline`/`accent` 为兼容保留）")
    if len(variants) != SPEC_VARIANTS:
        note.append(f"变体集合大小 {len(variants)} ≠ 规范 §6.1 声明的 {SPEC_VARIANTS}")
    return bad, note


def judge_hardcoded(items: list[dict]) -> list[str]:
    """B4：带文字的原生按钮不得硬编码 <48px 的高度。

    豁免三类（前两类与 B6 保持一致）：
    - 纯图标按钮 ⇒ 归 `verify_tap_targets.py`（它量热区，`tap-ghost` 撑开的测得到）
    - `.text-link` 内联文字链接 ⇒ WCAG 2.5.8 的 inline 例外
    - **带 `min-h-tap`** ⇒ 移动端由 `min-height` 撑到 48px，`h-9` 只在桌面生效 ⇒ 非缺陷
    """
    bad: list[str] = []
    for it in items:
        if it["icon_only"] or it.get("exempt") or it.get("compensated"):
            continue
        bad.append(
            f"{it['file']} 的 `<{it['tag']}>`（文字 {it['label']!r}）硬编码 "
            f"{'/'.join(it['heights'])} = {'/'.join(str(int(p)) for p in it['px'])}px "
            "⇒ 移动端不足 48px（§6.1），且绕过了 `Button` 组件")
    return bad


def judge_primary(rows: list[dict]) -> list[str]:
    """B5：每屏 primary 按钮 ≤ 1（排除 `aria-current="page"` 的激活态指示）。"""
    bad: list[str] = []
    ctas = [r for r in rows if not r.get("ariaCurrent")]
    if len(ctas) > SPEC_PRIMARY_PER_SCREEN:
        names = "、".join(f"{r['tag']}「{r['label'] or '(无文字)'}」" for r in ctas)
        bad.append(
            f"同屏 {len(ctas)} 个主色按钮（规范 §6.1「每屏最多一个 `pri`」）：{names}")
    return bad


def judge_targets(rows: list[dict]) -> tuple[list[str], list[str]]:
    """B6：按钮触控目标 ≥48×48（排除内联文字链接、以及热区够大的）。

    返回 `(缺陷, 临界)`。**临界不判**：热区是用整数像素逐格扫描量出来的，粒度 ±1px
    （见 `verify_tap_targets.py` 的注释：盒子跨 `[10.5, 58.5]` ⇒ 整数点 11..58 全命中
    ⇒ 长度 48）。所以 `47px` 的盒子可能量成 `48` ⇒ 门槛正下方的一小段不可靠，
    既不判绿也不判红，单独报出（与 `verify_contrast.py` 的「区间内不判」同一条纪律）。
    """
    bad: list[str] = []
    near: list[str] = []
    for r in rows:
        if r.get("exempt"):
            continue
        w, h = r["hotW"], r["hotH"]
        if w >= SPEC_TAP and h >= SPEC_TAP:
            continue
        # ⚠️ 别把「差值」当「实际值」打出去：第一版写成 `[(n, SPEC_TAP - v)]` 后
        #    消息变成「高 12.0px」（实际是 36px 的缺口 12）⇒ 自检臂 B6-b/B6-c 当场报红。
        short = [(n, v) for n, v in (("宽", w), ("高", h)) if v < SPEC_TAP]
        why = "、".join(f"{n} {v}px" for n, v in short)
        msg = (f"`<{r['tag']}>`「{r['label'] or '(无文字)'}」触控目标 {why} < 48px"
               f"（盒子 {r['boxW']}×{r['boxH']}）")
        if all(SPEC_TAP - v <= CRITICAL_BAND for _, v in short):
            near.append(msg + "  ← 落在 ±1px 粒度带内，**不判**")
        else:
            bad.append(msg)
    return bad, near


# ===========================================================================
# 渲染级测量（JS 只测量，判定在 Python）
# ===========================================================================
SCAN_JS = r"""(async () => {
  const SOLID = 'rgb(39, 76, 147)';      // #274C93 == --brand-600 == --solid-brand
  const TAP = 48;
  const frame = () => new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)));
  const cs = (el) => getComputedStyle(el);
  const txt = (el) => (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 26);
  const cls = (el) => (el.className || '').toString();
  const vis = (el) => {
    const c = cs(el);
    if (c.display === 'none' || c.visibility === 'hidden' || c.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width >= 1 && r.height >= 1;
  };

  const SEL = 'button,a[href],[role=button],[role=link],input[type=submit],summary';
  const all = [...document.querySelectorAll(SEL)].filter(vis);

  // ---- B5：primary 签名 = 底色 == #274C93（**不要求白字**，见文件头「坑 4」）----
  // 在**滚动之前**先数完，避免滚动改变可见集合。
  const primaries = all.filter(el => cs(el).backgroundColor === SOLID).map(el => ({
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute('role') || '',
    href: el.getAttribute('href') || '',
    ariaCurrent: el.getAttribute('aria-current') || '',
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    label: txt(el),
    h: Math.round(el.getBoundingClientRect().height),
    w: Math.round(el.getBoundingClientRect().width),
    cls: cls(el).slice(0, 100),
  }));

  // ---- B6：触控目标（`elementFromPoint` 实测，不量盒子）----
  // 豁免：`<a href>` 纯链接、`.text-link` 内联文字链接（WCAG 2.5.8 inline 例外）
  const targets = [];
  for (const el of all) {
    const c = cls(el);
    const isPureLink = el.tagName === 'A' && !el.hasAttribute('role');
    const isInlineLink = /(^|\s)text-link(\s|$)/.test(c);
    const label = txt(el);
    if (isPureLink || isInlineLink) {
      targets.push({ tag: el.tagName.toLowerCase(), label, exempt: true,
                     reason: isPureLink ? 'a[href] 纯链接' : '.text-link 内联链接',
                     boxW: 0, boxH: 0, hotW: 0, hotH: 0 });
      continue;
    }
    // ⚠️ `scrollIntoView` 之后**必须等一帧**再读 `getBoundingClientRect()`。
    //    第一版是同步 IIFE，读到的矩形是**滚动前**的，与 `elementFromPoint` 的
    //    布局不匹配 ⇒ 实测 `box 98×56 hot 0×0`（TabBar 激活项「量不到」），
    //    若据此判红就是把「我没测成」说成产品缺陷。
    el.scrollIntoView({ block: 'center', inline: 'center' });
    await frame();
    const r = el.getBoundingClientRect();
    const cx = Math.round(r.left + r.width / 2), cy = Math.round(r.top + r.height / 2);
    const hits = (x, y) => { const t = document.elementFromPoint(x, y); return !!(t && (t === el || el.contains(t))); };
    // 中心点戳到的**到底是谁** —— 用于区分「产品坏了」与「我没测成」
    const atCenter = () => {
      const t = document.elementFromPoint(cx, cy);
      if (!t) return 'null（视口外）';
      return t.tagName.toLowerCase() + '.' + (t.className || '').toString().split(' ').slice(0, 2).join('.');
    };
    const extent = (horiz) => {
      const lo = (horiz ? Math.floor(r.left) : Math.floor(r.top)) - 40;
      const hi = (horiz ? Math.ceil(r.right) : Math.ceil(r.bottom)) + 40;
      const at = (v) => (horiz ? hits(v, cy) : hits(cx, v));
      const a = horiz ? cx : cy;
      if (!at(a)) return { n: 0, trunc: false, center: false };
      let s = a, e = a;
      while (s - 1 >= lo && at(s - 1)) s--;
      while (e + 1 <= hi && at(e + 1)) e++;
      return { n: e - s + 1, trunc: (s <= lo || e >= hi), center: true };
    };
    const hx = extent(true), vy = extent(false);
    targets.push({
      tag: el.tagName.toLowerCase(),
      label,
      exempt: false,
      boxW: Math.round(r.width), boxH: Math.round(r.height),
      hotW: hx.n, hotH: vy.n,
      // 中心点都戳不到自己 ⇒ **量不到**（不是「点不到」）⇒ 既不判绿也不判红
      unmeasured: !hx.center || !vy.center,
      centerEl: (!hx.center || !vy.center) ? atCenter() : '',
      truncated: hx.trunc || vy.trunc,
      cls: c.slice(0, 90),
    });
  }
  return { vw: innerWidth, vh: innerHeight, nInter: all.length, primaries, targets };
})()"""


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


async def resolve_path(b: Browser, base: str, path: str) -> str:
    """把路径里的占位符换成真实值（目前只有 `@first-case`）。"""
    if "@first-case" not in path:
        return path
    await b.goto(f"{base}/cases", wait=1.5)
    await b.settle(extra=2.0)
    cid = await b.cdp.evaluate(
        "(()=>{const a=document.querySelector('a[href*=\"/cases/\"]');"
        "if(!a)return null;const m=a.getAttribute('href').match(/\\/cases\\/([^/?#]+)/);return m?m[1]:null})()")
    return path.replace("@first-case", str(cid)) if cid else path


async def probe_page(b: Browser) -> dict | None:
    for _ in range(3):
        r = await b.cdp.evaluate(SCAN_JS)
        if r:
            return r
        await asyncio.sleep(0.8)
    return None


async def run_render(only: str | None, path_filter: str | None = None,
                     verbose: bool = True) -> int:
    targets = {only: APPS[only]} if only else APPS
    # (位置, 判据, 消息) —— 汇总时按「消息」聚类，因为同一条根因会在很多页重复出现
    bad: list[tuple[str, str, str]] = []
    near: list[tuple[str, str]] = []
    env: list[str] = []
    scanned = 0

    if verbose:
        print("── §6.1 按钮规范 + §8.3 按钮触控目标（390×844）──\n")

    async with Browser(headless=True, width=390, height=844, device_scale_factor=2) as b:
        await b.apply_device(mobile=True)
        for app, spec in targets.items():
            base = f"http://localhost:{spec['port']}"
            if not await login(b, base, spec["user"]):
                print(f"[{app}] ✗ 登录失败（环境问题）")
                env.append(app)
                continue
            for path in spec["pages"]:
                if path_filter and path != path_filter:
                    continue
                real = await resolve_path(b, base, path)
                await b.goto(f"{base}{real}", wait=1.5)
                await b.settle(extra=2.5)
                r = await probe_page(b)
                if not r:
                    print(f"[{app}] {path}  ✗ 扫描返回空（环境问题）")
                    env.append(f"{app}{path}")
                    continue
                scanned += 1

                pbad = judge_primary(r["primaries"])
                # 贴边截断 / 中心点戳不到自己 ⇒ 「量不到」不是「点不到」，
                # 既不判绿也不判红（与 README 坑 14 同一条纪律）
                skipped = [t for t in r["targets"]
                           if t.get("truncated") or t.get("unmeasured")]
                measurable = [t for t in r["targets"]
                              if not t.get("truncated") and not t.get("unmeasured")]
                tbad, tnear = judge_targets(measurable)

                mark = "✗" if (pbad or tbad) else "✓"
                print(f"[{app}] {path:<18} 可交互 {r['nInter']:>3}  "
                      f"主色按钮 {len(r['primaries'])}  触控目标 {len(measurable)}"
                      + (f"（{len(skipped)} 个量不到，未判定）" if skipped else "")
                      + f"   {mark}")
                for d in pbad:
                    print(f"      [B5] {d}")
                for d in tbad:
                    print(f"      [B6] {d}")
                for d in tnear:
                    print(f"      [B6·临界] {d}")
                bad += [(f"[{app}] {path}", "B5", d) for d in pbad]
                bad += [(f"[{app}] {path}", "B6", d) for d in tbad]
                near += [(f"[{app}] {path}", d) for d in tnear]
            print()

    print("── 汇总 ──")
    print(f"  扫描 {scanned} 个页面")
    if env:
        print(f"  ⚠ 未能测量：{', '.join(env)}（环境问题）")
    if near:
        print(f"  · 临界未判定 {len(near)} 条（落在 ±1px 粒度带内）")
    if bad:
        # 按「判据 + 消息」聚类 —— 同一条根因（如 AppLayout 的头像按钮）会在 20+ 页重复，
        # 逐页罗列会淹掉真正该改的那一处。这里聚成「一条根因 = 一行」。
        groups: dict[tuple[str, str], list[str]] = {}
        for loc, crit, msg in bad:
            groups.setdefault((crit, msg), []).append(loc)
        print(f"  ✗ 渲染层缺陷 {len(bad)} 条，聚为 {len(groups)} 条根因：")
        for (crit, msg), locs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            print(f"      [{crit}] ×{len(locs):<3} {msg}")
            print(f"              出现于：{'、'.join(locs[:6])}"
                  + (f" 等 {len(locs)} 处" if len(locs) > 6 else ""))
    if env:
        print("\nEXIT=2  有页面未能测量，不当作通过")
        return 2
    if bad:
        print(f"\nEXIT=1  产品缺陷 {len(bad)} 条")
        return 1
    print("  ✓ 每屏主色按钮 ≤1；按钮触控目标 ≥48×48（规范 §6.1 / §8.3）")
    print("\nEXIT=0  通过")
    return 0


# ===========================================================================
# 源码层
# ===========================================================================
def source_scan(verbose: bool = True) -> tuple[int, list[str]]:
    button_src = BUTTON_TSX.read_text(encoding="utf-8")
    tokens = parse_tokens(TOKENS_CSS.read_text(encoding="utf-8"))

    t_bad, t_note = judge_tokens(tokens)
    s_bad = judge_sizes(parse_button_sizes(button_src))
    v_bad, v_note = judge_variants(parse_variants(button_src))

    files = collect_tsx()
    hard = scan_hardcoded_heights(files)
    h_bad = judge_hardcoded(hard)

    if verbose:
        print("── 源码层 ──")
        print(f"  [B1] `--brand-600` == `--solid-brand` == {norm_rgb(tokens.get('solid-brand', ''))}  "
              f"{'✓' if not t_bad else '✗'}")
        for n in t_note:
            print(f"       ⚠ {n}")
        print(f"  [B2] Button 尺寸 {({k: next((height_px(t) for t in v if height_px(t) is not None), None) for k, v in parse_button_sizes(button_src).items()})}  "
              f"{'✓' if not s_bad else '✗'}")
        print(f"  [B3] 变体 {sorted(parse_variants(button_src))}  "
              f"（规范声明 {SPEC_VARIANTS} 个）")
        for n in v_note:
            print(f"       ⚠ {n}")
        print(f"  [B4] 扫描 {len(files)} 个 .tsx，硬编码 <48px 高度的原生按钮 {len(hard)} 处"
              f"（其中带文字的 {sum(1 for i in hard if not i['icon_only'])} 处，"
              f"判红 {len(h_bad)} 处）")
        for it in hard:
            if it["icon_only"]:
                kind = "图标按钮（归 verify_tap_targets.py）"
            elif it.get("exempt"):
                kind = f"带文字但豁免（{it['exempt_reason']}）"
            elif it.get("compensated"):
                kind = f"带文字但**有补偿**（{it['exempt_reason']}）"
            else:
                kind = "**带文字 ⇒ 判红**"
            print(f"       · {it['file']}  <{it['tag']}> {kind} "
                  f"{'/'.join(it['heights'])}  {it['label']!r}")
        for d in t_bad + s_bad + v_bad + h_bad:
            print(f"      ✗ {d}")
        print()

    defects = t_bad + s_bad + v_bad + h_bad
    return len(defects), defects


# ===========================================================================
# --dump / --why
# ===========================================================================
async def dump(only: str | None) -> int:
    targets = {only: APPS[only]} if only else APPS
    async with Browser(headless=True, width=390, height=844, device_scale_factor=2) as b:
        await b.apply_device(mobile=True)
        for app, spec in targets.items():
            base = f"http://localhost:{spec['port']}"
            if not await login(b, base, spec["user"]):
                print(f"[{app}] ✗ 登录失败")
                continue
            for path in spec["pages"]:
                real = await resolve_path(b, base, path)
                await b.goto(f"{base}{real}", wait=1.5)
                await b.settle(extra=2.5)
                r = await probe_page(b)
                if not r:
                    continue
                print(f"\n[{app}] {path}  可交互 {r['nInter']}")
                for p in r["primaries"]:
                    ac = f" aria-current={p['ariaCurrent']!r}" if p["ariaCurrent"] else ""
                    print(f"    P  {p['tag']} {p['label']!r} {p['w']}×{p['h']}{ac}")
                    print(f"       {p['cls']}")
                small = [t for t in r["targets"]
                         if not t.get("exempt") and not t.get("truncated")
                         and not t.get("unmeasured")
                         and (t["hotW"] < SPEC_TAP or t["hotH"] < SPEC_TAP)]
                print(f"    <48 的按钮 {len(small)} 个（已排除内联链接、贴边截断、量不到）")
                for t in small:
                    print(f"       box {t['boxW']:>3}×{t['boxH']:<3} hot {t['hotW']:>3}×{t['hotH']:<3} "
                          f"{t['tag']} {t['label']!r}  {t['cls'][:56]}")
    return 0


async def why(app: str, path: str | None) -> int:
    spec = APPS[app]
    base = f"http://localhost:{spec['port']}"
    async with Browser(headless=True, width=390, height=844, device_scale_factor=2) as b:
        await b.apply_device(mobile=True)
        if not await login(b, base, spec["user"]):
            print("✗ 登录失败")
            return 2
        for p in ([path] if path else spec["pages"]):
            real = await resolve_path(b, base, p)
            await b.goto(f"{base}{real}", wait=1.5)
            await b.settle(extra=2.5)
            r = await probe_page(b)
            if not r:
                print(f"[{app}] {p} 扫描空")
                continue
            print(f"\n── {app}{p} ──")
            print("  全部 primary 签名（底色 == #274C93）：")
            for x in r["primaries"]:
                print(f"    {x['tag']:<7} {x['w']:>4}×{x['h']:<3} aria-current={x['ariaCurrent']!r:<8} "
                      f"{x['label']!r}")
                print(f"            {x['cls']}")
            print("  触控目标（实测热区，含豁免标记）：")
            for t in r["targets"]:
                extra = ""
                if t.get("exempt"):
                    tag, extra = "豁免", f"  ({t['reason']})"
                elif t.get("unmeasured"):
                    tag = "量不到"
                    extra = f"  中心点站着 {t.get('centerEl')!r}（不是它自己 ⇒ 量不到，不是点不到）"
                elif t.get("truncated"):
                    tag = "截断"
                else:
                    tag = " 判 "
                print(f"    [{tag}] box {t['boxW']:>4}×{t['boxH']:<4} hot {t['hotW']:>4}×{t['hotH']:<4} "
                      f"{t['tag']:<7} {t['label']!r}{extra}")
    return 0


# ===========================================================================
# 自检
# ===========================================================================
FIXTURE_BUTTON_OK = '''
const variantStyles: Record<string, string> = {
  primary: "bg-brand-600 text-white shadow-s1 hover:bg-brand-700 border border-transparent",
  verify: "bg-verified-600 text-white shadow-s1 hover:bg-verified-700 border border-transparent",
  secondary: "bg-surface-subtle text-ink-800 border border-line hover:bg-surface-hover",
  ghost: "bg-transparent text-ink-600 border border-transparent hover:bg-surface-hover",
  danger: "bg-danger-500 text-white shadow-s1 hover:bg-danger-600 border border-transparent",
};
const sizeStyles: Record<string, string> = {
  sm: "h-7 min-h-tap sm:min-h-0 px-3 text-label rounded-r2 gap-1.5",
  md: "h-[34px] min-h-tap sm:min-h-0 px-4 text-body-sm rounded-r2 gap-2",
  lg: "h-10 min-h-tap sm:min-h-0 px-5 text-body rounded-r2 gap-2",
};
'''
FIXTURE_BUTTON_BADSIZE = FIXTURE_BUTTON_OK.replace('sm: "h-7', 'sm: "h-9')
FIXTURE_BUTTON_NOTAP = FIXTURE_BUTTON_OK.replace("h-7 min-h-tap", "h-7")
FIXTURE_BUTTON_NOSM0 = FIXTURE_BUTTON_OK.replace("h-10 min-h-tap sm:min-h-0", "h-10 min-h-tap")
FIXTURE_BUTTON_MISSING = FIXTURE_BUTTON_OK.replace(
    '  ghost: "bg-transparent text-ink-600 border border-transparent hover:bg-surface-hover",\n', "")
FIXTURE_BUTTON_EXTRA = FIXTURE_BUTTON_OK.replace(
    "};", '  outline: "bg-surface text-ink-700 border border-line-strong",\n'
           '  accent: "bg-gold-500 text-white shadow-s1",\n};', 1)

FIXTURE_TOKENS_OK = """
:root {
  --brand-600: 39 76 147; /* #274C93 主按钮、激活导航 */
  --solid-brand: 39 76 147; /* #274C93 白字对比 8.3:1 */
  --tap: 48px;
}
"""
FIXTURE_TOKENS_SPLIT = FIXTURE_TOKENS_OK.replace("--solid-brand: 39 76 147", "--solid-brand: 41 78 150")

FIXTURE_TSX = '''
export function A() {
  return (
    <div>
      <button className="flex h-9 items-center gap-1.5 rounded-r2 border border-line px-3">刷新</button>
      <button className="flex h-9 min-h-tap items-center gap-1.5 rounded-r2 border border-line px-3 sm:min-h-0">全部已读</button>
      <button className="flex h-8 w-8 items-center justify-center rounded-r2 tap-ghost"><Refresh /></button>
      <button className="flex h-12 items-center gap-2 rounded-r2 px-1.5">王</button>
      <Button variant="primary" onClick={() => go()}>接单</Button>
      <Button variant={showForm ? "outline" : "primary"}>新增文档</Button>
      <a className="inline-flex h-8 items-center text-link" href="/all">全部</a>
      <button className="h-8 w-8">{theme === "dark" ? <Sun /> : <Moon />}</button>
    </div>
  );
}
'''


def self_test() -> int:
    """每条判据都要有**会红**的臂 + **对照（应绿）**的臂。

    ⚠️ 与 `verify_focus_ring.py` 同一条纪律：**对照臂不是装饰**。
    一条只会绿的判据不算判据；一条「什么输入都判红」的判据同样不算 ——
    后者会把自己的判据错误说成产品缺陷。
    """
    fails: list[str] = []
    n = 0

    def arm(name: str, cond: bool, detail: str = "") -> None:
        nonlocal n
        n += 1
        if not cond:
            fails.append(f"{name} {detail}")

    # ---- 解析器 ----
    sizes = parse_button_sizes(FIXTURE_BUTTON_OK)
    arm("P1 parse_button_sizes 解出三档", set(sizes) == {"sm", "md", "lg"}, f"→ {sorted(sizes)}")
    arm("P2 `h-7` → 28px", height_px("h-7") == 28.0, f"→ {height_px('h-7')}")
    arm("P3 `h-[34px]` → 34px", height_px("h-[34px]") == 34.0, f"→ {height_px('h-[34px]')}")
    arm("P4 `h-10` → 40px", height_px("h-10") == 40.0, f"→ {height_px('h-10')}")
    arm("P5 `h-12` → 48px（合规档，必须能算出来）",
        height_px("h-12") == H_12, f"→ {height_px('h-12')}")
    arm("P6 `min-h-tap` 不是高度令牌，返回 None",
        height_px("min-h-tap") is None, f"→ {height_px('min-h-tap')}")
    arm("P7 parse_variants 解出 5 个键",
        parse_variants(FIXTURE_BUTTON_OK) == {"primary", "verify", "secondary", "ghost", "danger"},
        f"→ {sorted(parse_variants(FIXTURE_BUTTON_OK))}")
    arm("P8 parse_tokens 取到 `--solid-brand`",
        parse_tokens(FIXTURE_TOKENS_OK).get("solid-brand") == "39 76 147",
        f"→ {parse_tokens(FIXTURE_TOKENS_OK).get('solid-brand')}")

    # ---- 标签截断（坑 1）----
    tricky = '<Button variant={showForm ? "outline" : "primary"} className="h-9">新增</Button>'
    tags = iter_tags(tricky, "Button")
    arm("P9 `_tag_end` 不被表达式里的 `>` 骗（坑 1）",
        len(tags) == 1 and 'className="h-9"' in tags[0][0], f"→ {tags}")
    arm("P10 嵌套 `=>` 箭头函数也能配平",
        len(iter_tags('<button onClick={() => setShowForm((v) => !v)} className="h-9">x</button>',
                      "button")) == 1)
    _t11 = '<button className="h-9">刷新</button>'
    arm("P11 可见文字在**标签之后**，`_jsx_label` 要能取到",
        _jsx_label(_t11, iter_tags(_t11, "button")[0][1]) == "刷新",
        f"→ {_jsx_label(_t11, iter_tags(_t11, 'button')[0][1])!r}")
    _t12 = '<button className="h-8 w-8"><Refresh /></button>'
    arm("P13 纯图标按钮（子节点是元素）⇒ 空文字 ⇒ 归 verify_tap_targets.py",
        _jsx_label(_t12, iter_tags(_t12, "button")[0][1]) == "",
        f"→ {_jsx_label(_t12, iter_tags(_t12, 'button')[0][1])!r}")
    # 下面三臂是「假红」的回归锁：第一版把条件子节点的 `(` 当成文字
    _t14 = ('<button className="h-8 w-8">{showPassword ? (\n  <Eye className="h-4 w-4" />\n) : (\n'
            '  <EyeOff className="h-4 w-4" />\n)}</button>')
    arm("P14 条件子节点里只有图标 ⇒ 空文字（**假红回归锁**）",
        _jsx_label(_t14, iter_tags(_t14, "button")[0][1]) == "",
        f"→ {_jsx_label(_t14, iter_tags(_t14, 'button')[0][1])!r}")
    _t15 = ('<button className="h-9">{creating ? (\n  <Loader />\n) : (\n'
            '  "开始新咨询"\n)}</button>')
    arm("P15 条件子节点里有字符串字面量 ⇒ 取到它",
        _jsx_label(_t15, iter_tags(_t15, "button")[0][1]) == "开始新咨询",
        f"→ {_jsx_label(_t15, iter_tags(_t15, 'button')[0][1])!r}")
    _t16 = '<button className="h-9"><RefreshCw className="h-4 w-4" />刷新</button>'
    arm("P16 嵌套标签的 `className` 不得被当成文字",
        _jsx_label(_t16, iter_tags(_t16, "button")[0][1]) == "刷新",
        f"→ {_jsx_label(_t16, iter_tags(_t16, 'button')[0][1])!r}")
    _t17 = ('<button className="h-8 w-8">{theme === "dark" ? (\n  <Sun className="h-4 w-4" />\n) : (\n'
            '  <Moon className="h-4 w-4" />\n)}</button>')
    arm("P17 比较运算的操作数 `\"dark\"` 不算文字（**假红回归锁**）",
        _jsx_label(_t17, iter_tags(_t17, "button")[0][1]) == "",
        f"→ {_jsx_label(_t17, iter_tags(_t17, 'button')[0][1])!r}")
    _t18 = '<button className="h-9">{theme === "dark" ? "浅色" : "深色"}</button>'
    arm("P18 三元的两个分支都是**值**，都要取到（渲染时只出现一个）",
        _jsx_label(_t18, iter_tags(_t18, "button")[0][1]) == "浅色深色",
        f"→ {_jsx_label(_t18, iter_tags(_t18, 'button')[0][1])!r}")
    arm("P12 顶层键扫描：值里的 `hover:` 不算键",
        top_level_keys('primary: "hover:bg-brand-700", ghost: "bg-transparent"')
        == ["primary", "ghost"],
        f"→ {top_level_keys('primary: \"hover:bg-brand-700\", ghost: \"bg-transparent\"')}")

    # ---- 颜色归一 ----
    arm("N1 `#274C93` → (39,76,147)", norm_rgb("#274C93") == SOLID_BRAND, f"→ {norm_rgb('#274C93')}")
    arm("N2 `39 76 147` → (39,76,147)", norm_rgb("39 76 147") == SOLID_BRAND)
    arm("N3 `rgb(39, 76, 147)` → (39,76,147)", norm_rgb("rgb(39, 76, 147)") == SOLID_BRAND)
    arm("N4 非法值返回 None", norm_rgb("var(--x)") is None)

    # ---- B1 ----
    b, _ = judge_tokens(parse_tokens(FIXTURE_TOKENS_OK))
    arm("B1-a 两令牌同值 ⇒ 绿", b == [], f"→ {b}")
    b2, _ = judge_tokens(parse_tokens(FIXTURE_TOKENS_SPLIT))
    arm("B1-b 两令牌分裂 ⇒ 必须报红", len(b2) == 1, f"→ {b2}")
    b3, _ = judge_tokens({})
    arm("B1-c 令牌缺失 ⇒ 必须报红（不是静默通过）", len(b3) == 2, f"→ {b3}")

    # ---- B2 ----
    arm("B2-a 28/34/40 + min-h-tap + sm:min-h-0 ⇒ 绿",
        judge_sizes(parse_button_sizes(FIXTURE_BUTTON_OK)) == [],
        f"→ {judge_sizes(parse_button_sizes(FIXTURE_BUTTON_OK))}")
    s2 = judge_sizes(parse_button_sizes(FIXTURE_BUTTON_BADSIZE))
    arm("B2-b `sm` 写成 `h-9`(36px) ⇒ 必须报红", len(s2) >= 1, f"→ {s2}")
    s3 = judge_sizes(parse_button_sizes(FIXTURE_BUTTON_NOTAP))
    arm("B2-c 缺 `min-h-tap` ⇒ 必须报红（移动端到不了 48）", len(s3) >= 1, f"→ {s3}")
    s4 = judge_sizes(parse_button_sizes(FIXTURE_BUTTON_NOSM0))
    arm("B2-d 缺 `sm:min-h-0` ⇒ 必须报红（桌面被撑高）", len(s4) >= 1, f"→ {s4}")

    # ---- B3 ----
    v, vn = judge_variants(parse_variants(FIXTURE_BUTTON_OK))
    arm("B3-a 恰好 5 个 ⇒ 绿且无提示", v == [] and vn == [], f"→ {v} {vn}")
    v2, vn2 = judge_variants(parse_variants(FIXTURE_BUTTON_EXTRA))
    arm("B3-b 多出 outline/accent ⇒ **只报不判**（组件已声明为兼容保留）",
        v2 == [] and len(vn2) == 2, f"→ {v2} {vn2}")
    v3, _ = judge_variants(parse_variants(FIXTURE_BUTTON_MISSING))
    arm("B3-c 缺 `ghost` ⇒ 必须报红", len(v3) == 1, f"→ {v3}")

    # ---- B4 ----
    items = scan_hardcoded_heights([("__fixture__/fixture.tsx", FIXTURE_TSX)])
    h_bad = judge_hardcoded(items)
    arm("B4-a 只报带文字的 `h-9` 刷新按钮", len(h_bad) == 1, f"→ {h_bad}")
    arm("B4-b 图标按钮（h-8 + tap-ghost，无文字）不报 —— 归 verify_tap_targets.py",
        any(i["icon_only"] and "h-8" in i["heights"] for i in items), f"→ {items}")
    arm("B4-c `h-12`(48px) 合规，不得命中",
        not any("h-12" in i["heights"] for i in items), f"→ {[i['heights'] for i in items]}")
    arm("B4-d `<Button size=\"md\">` 走组件，不得命中",
        not any(i["tag"] == "Button" for i in items))
    arm("B4-e `<a class=\"text-link\">` 与 B6 用**同一条豁免**，不在 B4 判红",
        any(i["exempt"] and i["tag"] == "a" for i in items) and len(h_bad) == 1,
        f"→ exempt={[(i['tag'], i['exempt']) for i in items]} bad={len(h_bad)}")
    # 「正确实现被测成缺陷」的回归锁：`h-9 min-h-tap` 在移动端实际是 48px
    comp = [i for i in items if i.get("compensated")]
    arm("B4-f `h-9 min-h-tap`（移动端由 min-height 撑到 48）⇒ **必须绿**",
        len(comp) == 1 and comp[0]["label"] == "全部已读",
        f"→ {[(i['label'], i['compensated']) for i in items]}")
    arm("B4-g 主题切换（比较操作数 `\"dark\"`）是图标按钮 ⇒ 绿",
        any(i["icon_only"] and "h-8" in i["heights"] for i in items), f"→ {items}")

    # ---- B5 ----
    arm("B5-a 1 个 ⇒ 绿",
        judge_primary([{"tag": "button", "label": "发起咨询", "ariaCurrent": ""}]) == [])
    p2 = judge_primary([{"tag": "button", "label": "发起咨询", "ariaCurrent": ""},
                        {"tag": "button", "label": "发起首次咨询", "ariaCurrent": ""}])
    arm("B5-b 2 个 ⇒ 必须报红", len(p2) == 1, f"→ {p2}")
    arm("B5-c 3 个（列表每行一个）⇒ 必须报红",
        len(judge_primary([{"tag": "button", "label": "接单", "ariaCurrent": ""}] * 3)) == 1)
    arm("B5-d **分页当前页 `aria-current=page` 是合法兄弟** ⇒ 绿",
        judge_primary([{"tag": "button", "label": "发起咨询", "ariaCurrent": ""},
                       {"tag": "button", "label": "1", "ariaCurrent": "page"}]) == [],
        "→ 这是「区分合法兄弟」的关键对照臂")
    arm("B5-e 只有 0 个 ⇒ 绿",
        judge_primary([]) == [])

    # ---- B6 ----
    _ok = [{"tag": "button", "label": "发送", "hotW": 48, "hotH": 48,
            "boxW": 48, "boxH": 48, "exempt": False}]
    arm("B6-a 48×48 ⇒ 绿", judge_targets(_ok) == ([], []), f"→ {judge_targets(_ok)}")
    t2, n2 = judge_targets([{"tag": "button", "label": "发送", "hotW": 82, "hotH": 36,
                             "boxW": 82, "boxH": 36, "exempt": False}])
    arm("B6-b 高 36px ⇒ 必须报红", len(t2) == 1 and "高 36px" in t2[0], f"→ {t2} {n2}")
    t3, _ = judge_targets([{"tag": "button", "label": "王", "hotW": 40, "hotH": 49,
                            "boxW": 40, "boxH": 48, "exempt": False}])
    arm("B6-c 宽 40px ⇒ 必须报红（宽也要 48）", len(t3) == 1 and "宽 40px" in t3[0], f"→ {t3}")
    arm("B6-d 内联 `text-link`（18px）豁免 ⇒ 绿",
        judge_targets([{"tag": "button", "label": "全部", "hotW": 42, "hotH": 19,
                        "boxW": 42, "boxH": 18, "exempt": True}]) == ([], []))
    arm("B6-e 图标按钮盒子 32×32 但热区 48×49 ⇒ 绿（本判据量热区，不量盒子）",
        judge_targets([{"tag": "button", "label": "", "hotW": 48, "hotH": 49,
                        "boxW": 32, "boxH": 32, "exempt": False}]) == ([], []))
    # 「临界不判」的回归锁：热区是整数像素扫描量出来的，±1px 粒度
    tb, tn = judge_targets([{"tag": "button", "label": "临界", "hotW": 47, "hotH": 48,
                             "boxW": 47, "boxH": 48, "exempt": False}])
    arm("B6-f 47px（门槛下方 1px，落在 ±1px 粒度带内）⇒ **只报不判**",
        tb == [] and len(tn) == 1, f"→ bad={tb} near={tn}")
    tb2, tn2 = judge_targets([{"tag": "button", "label": "真缺陷", "hotW": 46, "hotH": 48,
                               "boxW": 46, "boxH": 48, "exempt": False}])
    arm("B6-g 46px（出带）⇒ 必须报红", len(tb2) == 1 and tn2 == [], f"→ {tb2} {tn2}")
    tb3, tn3 = judge_targets([{"tag": "button", "label": "一维临界", "hotW": 47, "hotH": 36,
                               "boxW": 47, "boxH": 36, "exempt": False}])
    arm("B6-h 一维临界、另一维明显不足 ⇒ 必须报红（不能整条放过）",
        len(tb3) == 1 and tn3 == [], f"→ {tb3} {tn3}")

    print(f"── 自检 {n - len(fails)}/{n} 通过 ──")
    for f in fails:
        print(f"  ✗ {f}")
    if fails:
        print("\nEXIT=1  自检失败")
        return 1
    print("\nEXIT=0  自检通过")
    return 0


def _fixture_path(src: str) -> pathlib.Path:
    """已废弃：自检改用内存夹具（见 `collect_tsx` 的注释）。保留仅为兼容旧调用。"""
    raise RuntimeError("自检不应再落临时文件；请直接把 (路径, 源码) 传给 scan_hardcoded_heights")


# ===========================================================================
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--source-only", action="store_true")
    ap.add_argument("--app", choices=sorted(APPS))
    ap.add_argument("--path", help="只测该端的这一条路径（配合 --app）")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--why", nargs=2, metavar=("APP", "PATH"), help="取证：打原始测量值，不下判断")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if args.why:
        return asyncio.run(why(args.why[0], args.why[1]))
    if args.dump:
        return asyncio.run(dump(args.app))
    if args.source_only:
        n, defects = source_scan()
        print(f"── 源码层：缺陷 {n} 条 ──")
        for d in defects:
            print(f"  ✗ {d}")
        if n:
            print(f"\nEXIT=1  源码层 {n} 条")
            return 1
        print("\nEXIT=0  通过")
        return 0

    n_src, src_defects = source_scan()
    print(f"── 源码层：缺陷 {n_src} 条 ──\n")

    rc = asyncio.run(run_render(args.app, args.path))

    if src_defects:
        print(f"\n── 源码层缺陷明细（{len(src_defects)} 条）──")
        for d in src_defects:
            print(f"  · {d}")
    if rc == 0 and src_defects:
        print(f"\nEXIT=1  源码层 {len(src_defects)} 条（渲染层已通过）")
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
