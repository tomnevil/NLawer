#!/usr/bin/env python
"""组件接线判据：断言「规范点名的组件」真的被用上了。

## 为什么需要这条判据

组件库导出齐全、组件预览页渲染正常、`tsc --noEmit` 绿、`next build` 绿
—— **静态门禁一条都不会红**，而**产品页接入数可以是 0**。

实测（2026-09-20）：`CameraCapture` / `PullToRefresh` / `InfiniteList`
在**产品页**与**组件库内部**两棵树里都是 **0**，而所有静态门禁全绿。
⇒ 「组件存在」与「组件被用」是两件事。

📌 **2026-09-26（#37 收口）**：三个组件**全部接线完毕** ⇒ `KNOWN_GAPS` 已**清空**。
- `PullToRefresh` → `NotificationCenter` 移动端 BottomSheet（NR-11）+ im `/cases` + im `/chat`
  + **lawyer `/cases`**（2026-09-26 补：给组件加 `scrollTarget` 后接入 ⇒ ①产品 2 → 3）
- `InfiniteList`  → lawyer `/notifications`（原先手动的「加载更多（n/N）」按钮）
- `CameraCapture` → lawyer `/cases/[id]` 证据页签（配 SDK 新增的 `upload()`）

⚠️ **空表不等于这道防线没了** —— `classify_gap()` 对「未接线 + **不在**表里」判 `NEW`（红），
所以任何组件掉线都会被立刻拦住，而不是被这张表兜住（棘轮的「**不许涨**」那一半）。

## 🚨 三棵树 —— 少查一棵会得出**相反**的结论

| 树 | 范围 | 说明 |
|---|---|---|
| ① `product` | `apps/*/app/**`（**排除** `components-preview`） | 产品页真的用上了 |
| ② `preview` | `apps/*/app/**/components-preview/**` | **只证明组件能渲染**，不证明被用 |
| ③ `internal` | `packages/ui/src/**` | 组件库内部互用 |

**实测教训（`evidence/README.md` 坑 22）**：
`grep -rn "<BottomSheet" apps/lawyer` ⇒ **0**，据此会写下
「PRD NR-11 的移动 BottomSheet 未实现」——**假红**。
补查第 ③ 棵树后发现它一直在用（`NotificationCenter.tsx:835`）。
⇒ **`grep` 返回 0 时，第一件事是问「我查的是哪棵树」。**

## 判据

| 判定 | 条件 | 含义 |
|---|---|---|
| `USED` | ① > 0 或 ③ > 0 | 接线正常 |
| `INLINE_EQUIVALENT` | 组件未被用，但**等价实现**已命中 | **不是缺陷**：能力由页面内联承担（见 `INLINE_OK`） |
| `PREVIEW_ONLY` | ① = 0 且 ③ = 0 且 ② > 0，且无等价实现 | **接入缺口** |
| `MISSING` | 三棵树全 0，且无等价实现 | 更严重：组件根本没被任何地方引用 |

## 四条纪律（否则判据会造假红 / 假绿）

1. **组件自己的定义文件必须排除**，否则每个组件都会「自己命中自己」⇒ 恒为 `USED`。（自检 **Q5**）
2. **`preview` 不得计入 `USED`**，否则预览页会让所有组件恒绿 ⇒ **一条只会绿的防线**。（自检 **Q2**）
3. **「组件没被用」≠「能力没做」**——实测 `ProvenanceBlock` 未被任何产品页使用，
   但 §5 的三态视觉由页面**内联**承担（`ai-content` 别名、`border-ai-500/30 bg-ai-500/[0.06]` 等）。
   若不做等价判定，本判据会稳定误报「责任边界三态系统没做」——而三态在产品页有 74 处语义色 + 8 处徽章。
   ⇒ **凡要判「组件未被用」，先确认它承载的能力没有在别处实现。**（自检 **Q7 / Q8**）
4. **P2 与「落点已被取代」的组件不判缺陷**——那是**设计上就该那样**。（见 `OBSERVE`）
5. ⚠️ **`<Name` 是在「原始文本」上匹配的 ⇒ 注释 / docstring 里的用法示例也会被计入。**
   实测（2026-09-26）：`BottomSheet.tsx` 的 JSDoc 里写了一句
   `<PullToRefresh className="flex-1">` 当示例 ⇒ 该组件在**第 ③ 棵树**凭空多了 **1** 次命中
   （当时真实命中也是 1 次 ⇒ 总数 2）。
   ⇒ 后果是**假绿**：一个组件的**真实接入**被删掉后，只要还有人在注释里提一句 `<Name`，
     它**仍然判 `USED`**。
   ⇒ 纪律：**别在 docstring / 注释里写带尖括号的用法示例**（写 `PullToRefresh` + 反引号即可）。
   ⇒ 并且**别把本判据当成接入质量的证明** —— 它本来就只能证伪。

## 退出码

- `0` 通过（无必判缺口）
- `1` 发现接入缺口
- `2` 环境问题（仓库路径不存在、自检失败 ⇒ **工具坏了，不是产品坏了**）

用法：

    python evidence/verify_component_wiring.py
    python evidence/verify_component_wiring.py --self-test
    python evidence/verify_component_wiring.py --verbose
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
FRONTEND = ROOT / "frontend"

TREE_PRODUCT = "product"
TREE_PREVIEW = "preview"
TREE_INTERNAL = "internal"

USED = "USED"
INLINE = "INLINE_EQUIVALENT"
PREVIEW_ONLY = "PREVIEW_ONLY"
MISSING = "MISSING"

# ---------------------------------------------------------------------------
# ① 必判：P0/P1，且要求仍然成立。出处：`design-spec.md` §6.2。
# ---------------------------------------------------------------------------
REQUIRED: dict[str, str] = {
    # —— P0 ——
    "AppLayout": "四端统一骨架（AppShell 的装配层）",
    "AppShell": "桌面 240px 侧栏 + 56px 顶栏 / 移动双形态",
    "TabBar": "移动端底部导航，最多 5 项，含角标",
    "DataTable": "排序 / 筛选 / 列设置 / 三档密度 / **移动端自动转卡片**",
    "CitationPanel": "常驻引用面板（法条原文 / 时效状态）",
    "ProvenanceBadge": "责任边界三态徽章",
    "Timeline": "复核流转 / 证据时间线",
    "BottomSheet": "移动端底部上浮面板，替代 Modal",
    "CollapsePanel": "移动端折叠段落（六段式分析阅读）",
    # —— P1 ——
    "CitationChip": "正文内联引用标记",
    "ProvenanceLegend": "全局三态图例",
    "SegmentedControl": "角色 / 模式 / 时间范围切换",
    "FilterBar": "筛选条件条 + 已选条件回显",
    "Pagination": "页码 + 每页条数 + 总数",
    "PullToRefresh": "下拉刷新",
    "InfiniteList": "上滑加载更多",
    "CameraCapture": "相机取景框 + 文档边缘识别 + 自动裁边",
    "OfflineBanner": "弱网状态条",
    "SyncQueueBadge": "待同步队列徽标（琥珀）",
    "SyncQueueSheet": "待同步队列面板",
    "MobileActionBar": "律师端常驻底部操作条（§8.4 硬要求）",
}

# ---------------------------------------------------------------------------
# ② 必判，但**允许由内联等价实现承担**。
#    结构：名字 → (说明, 等价实现正则, 为什么允许)
#    实测依据写在 `why` 里，必须能指到「产品页的哪一行」。
# ---------------------------------------------------------------------------
INLINE_OK: dict[str, tuple[str, str, str]] = {
    "ProvenanceBlock": (
        "责任边界**内容段**容器（§5 的边框 + 底色）",
        r"provenance-(?:ai|verified|pending)|ai-content|lawyer-confirmed",
        "§5 要求的是「三态视觉」，不是「必须用这个组件」。"
        "实测产品页把该视觉**内联**实现了："
        "`web/qa/page.tsx:498` 用旧别名 `ai-content`（走同一套 `styles.css` 装饰类），"
        "`im/chat/page.tsx:951` 用 `border-ai-500/30 bg-ai-500/[0.06]`，"
        "`:1110` 用 `border-pending-500/30 bg-pending-500/10`；"
        "三态语义色在产品页共 **74** 处、徽章 **8** 处。"
        "⇒ 判定为**一致性 / 重构机会**（页面各写一份），**不是功能缺口**。"
        "⚠️ 但存在**视觉分歧**：§5 规定 AI 态是「紫色**虚线上边框**」，"
        "内联版用的是**四边实线**；只有走 `ai-content` 的那一处符合规范。",
    ),
}

# ---------------------------------------------------------------------------
# ③ 观察：**不判缺陷**，附理由与出处。
# ---------------------------------------------------------------------------
OBSERVE: dict[str, str] = {
    "Drawer": "落点**已被取代**：`§0「07 · 引用溯源交互倒退」` 记的问题是「`CitationDrawer` 弹窗遮挡正文」，"
              "而 §7.3 的解法是「三栏化 + **引用溯源常驻面板**」= `CitationPanel`（已接入）。"
              "⇒ §6.2 仍把 `Drawer` 列为 P1，但它的原始用途已被更好的方案解决。"
              "**建议 §6.2 降级或删除**（规范类改动，待定稿）。",
    "CommandPalette": "P2（§6.2）—— 组件**尚未建**，按优先级允许。",
    "Stepper": "P2（§6.2）—— 组件**尚未建**，按优先级允许。",
    "AppSwitcher": "非 §6.2 点名，属 AppShell 的组成件。",
}

# ---------------------------------------------------------------------------
# ④ 已知缺口（棘轮）：**已登记、不阻断 CI**，但**不许涨**。
#
# 为什么不能直接让它红：门禁必须「第一天就是绿的」（长期红的门禁两天内必被
# 注释掉——第九轮的教训）。这三个是 `design-audit.md` #37 / `admin-gap-analysis.md`
# #20 已登记、等用户拍板的待办，红着只会让整条门禁被关掉。
#
# 为什么不能直接豁免整条探针：那会让这条判据**只剩装饰作用**——将来第 4 个
# 组件掉线也没人知道。
#
# ⇒ 棘轮的**两条相反的不等式**：
#   · 不许涨：`KNOWN_GAPS` **之外**出现未接线 ⇒ **红**（拦住新的掉线）
#   · 不许赖：集合内的组件变成 `USED` ⇒ **红**（提示删除条目，防止豁免集合腐烂）
#     这一条是刻意的：删一行只需 5 秒，但「修好了却永远留在豁免表里」会静默
#     关掉这道防线。它不是「修好反而报错」，是「豁免必须跟着事实走」。
#
# ⚠️ 收录标准：**只收已登记、有任务编号、有负责人的**。禁止为了过门禁往里塞。
# ---------------------------------------------------------------------------
KNOWN_GAPS: dict[str, str] = {
    # ✅ 2026-09-26（#37 收口）：三个移动端组件**全部接线完毕** ⇒ 本表已清空。
    #    · `PullToRefresh` → `NotificationCenter` 移动端 BottomSheet（NR-11）+ im `/cases`
    #      + im `/chat` 会话列表（§5.1 B.1 的三个落点）+ **lawyer `/cases`**
    #      （2026-09-26 补：加 `scrollTarget` 后接入 ⇒ ①产品 2 → 3）。
    #    · `InfiniteList`  → lawyer `/notifications`（§5.1 B.2；原先是手动的「加载更多」按钮）。
    #    · `CameraCapture` → lawyer `/cases/[id]` 证据页签（§5.1 A.1；配 SDK 新增的 `upload()`）。
    # 按棘轮的「**不许赖**」：已接线的组件**必须**从这里删掉，留着会判 `GAP_HEALED` ⇒ 门禁红。
    # ⚠️ 空表**不等于**这道防线没了 —— `classify_gap()` 对「未接线 + 不在表里」判 `NEW`（红），
    #    所以任何组件掉线都会被立刻拦住，而不是被这张表兜住。
}


def tree_of(rel: str) -> str | None:
    """把仓库相对路径归到三棵树之一；不属任何一棵 ⇒ None。"""
    p = rel.replace("\\", "/")
    if "components-preview" in p:
        return TREE_PREVIEW
    if p.startswith("apps/"):
        return TREE_PRODUCT
    if p.startswith("packages/ui/"):
        return TREE_INTERNAL
    return None


def count_hits(name: str, files: list[tuple[str, str]]) -> dict[str, int]:
    """统计 `<Name` 在三棵树里的命中数。

    ⚠️ **排除组件自己的定义文件**：否则每个组件都会「自己命中自己」⇒ 恒为 USED。
    ⚠️ 用**前瞻断言** `(?=[\\s/<>])`：`<Widget` 不得吃掉 `<WidgetRow`。
    🚨 **`<` 必须在前瞻里**（2026-09-26 补）：泛型组件写成 `<InfiniteList<NotificationItem>`
    时，`InfiniteList` 后面跟的是 `<` —— 只写 `[\\s/>]` 会**看不见这种真实接入**
    ⇒ 组件明明接上了却被判 `PREVIEW_ONLY`（**假阴性**，与「注释里的示例被误算」正好相反）。
    自检 **Q13** 钉住这一条。
    """
    tag = re.compile(r"<" + re.escape(name) + r"(?=[\s/<>])")
    out = {TREE_PRODUCT: 0, TREE_PREVIEW: 0, TREE_INTERNAL: 0}
    for rel, text in files:
        stem = pathlib.PurePosixPath(rel.replace("\\", "/")).stem
        if stem == name:
            continue  # 定义文件，不算「被用」
        n = len(tag.findall(text))
        if not n:
            continue
        t = tree_of(rel)
        if t:
            out[t] += n
    return out


def count_equivalent(pattern: str, files: list[tuple[str, str]]) -> int:
    """统计等价实现在三棵树（产品页 + 组件库内部，**不含预览页**）的命中数。"""
    rx = re.compile(pattern)
    n = 0
    for rel, text in files:
        if tree_of(rel) == TREE_PREVIEW:
            continue
        n += len(rx.findall(text))
    return n


def verdict_of(name: str, files: list[tuple[str, str]]) -> tuple[str, dict[str, int], int]:
    counts = count_hits(name, files)
    eq = 0
    if name in INLINE_OK:
        eq = count_equivalent(INLINE_OK[name][1], files)
    if counts[TREE_PRODUCT] or counts[TREE_INTERNAL]:
        return USED, counts, eq
    if eq:
        return INLINE, counts, eq
    if counts[TREE_PREVIEW]:
        return PREVIEW_ONLY, counts, eq
    return MISSING, counts, eq


GAP_OK = "OK"          # 已接线，且不在豁免表里
GAP_NEW = "NEW"        # 未接线，**且不在豁免表里** ⇒ 新掉线，必须红
GAP_KNOWN = "KNOWN"    # 未接线，但已登记在 KNOWN_GAPS ⇒ 不阻断，但要一直打印
GAP_HEALED = "HEALED"  # 已接线，却仍留在豁免表里 ⇒ 必须删条目


def classify_gap(name: str, verdict: str) -> str:
    """把「组件名 + 判定」映射到棘轮的四态。

    单独抽成函数，是为了让**自检能覆盖棘轮本身**——否则「不许涨 / 不许赖」
    这两条只是 main() 里的两个 if，没人证明它们真的会红。
    """
    known = name in KNOWN_GAPS
    if verdict == USED:
        return GAP_HEALED if known else GAP_OK
    return GAP_KNOWN if known else GAP_NEW


def load_files() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    bases = [FRONTEND / "apps", FRONTEND / "packages" / "ui" / "src"]
    skip = ("node_modules", "/.next", "/dist", "\\.next", "\\dist", "\\node_modules")
    for base in bases:
        if not base.exists():
            continue
        for f in sorted(base.rglob("*.tsx")):
            s = f.as_posix()
            if any(x in s for x in skip):
                continue
            try:
                out.append((f.relative_to(FRONTEND).as_posix(), f.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return out


# ---------------------------------------------------------------------------
# 自检：**合成夹具，与产品无关**。各臂**分开报**，不混成一个布尔。
# ---------------------------------------------------------------------------
SELFTEST: tuple[tuple[str, str, list[tuple[str, str]], str], ...] = (
    ("Q1", "产品页命中 ⇒ USED",
     [("apps/web/app/(app)/page.tsx", "const A = () => <Widget />;")], USED),
    ("Q2", "**仅预览页**命中 ⇒ PREVIEW_ONLY（不得算 USED）",
     [("apps/web/app/components-preview/page.tsx", "<Widget />")], PREVIEW_ONLY),
    ("Q3", "**仅组件库内部**互用 ⇒ USED（坑 22：这一棵最容易漏）",
     [("packages/ui/src/components/Shell.tsx", "<Widget />")], USED),
    ("Q4", "三棵树全无 ⇒ MISSING",
     [("apps/web/app/(app)/other.tsx", "const x = 1;")], MISSING),
    ("Q5", "**只在自己定义文件里**出现 ⇒ MISSING（不得自计）",
     [("packages/ui/src/components/Widget.tsx", "<Widget />")], MISSING),
    ("Q6", "前缀更长的名字不得被误算（`<Widget` 不吃 `<WidgetRow`）",
     [("apps/web/app/(app)/page.tsx", "<WidgetRow />")], MISSING),
    ("Q7", "组件未用但**等价实现命中** ⇒ INLINE_EQUIVALENT（不得判缺陷）",
     [("apps/web/app/(app)/page.tsx", "<div className='widget-legacy-deco' />")], INLINE),
    ("Q8", "组件只在预览页、**等价实现也只在预览页** ⇒ 仍判 PREVIEW_ONLY（预览页的等价实现不算数）",
     [("apps/web/app/components-preview/page.tsx", "<Widget /> <div className='widget-legacy-deco' />")],
     PREVIEW_ONLY),
    ("Q13", "**泛型用法** `<Widget<Row> … />` 必须算命中（前瞻里漏了 `<` ⇒ 真实接入被判 PREVIEW_ONLY，假阴性）",
     [("apps/web/app/(app)/page.tsx", "const A = () => <Widget<Row> items={xs} />;")], USED),
)

SELFTEST_INLINE = {"Widget": ("合成夹具", r"widget-legacy-deco", "自检用")}

# 棘轮自检：**单独一组**，因为要按臂替换 `KNOWN_GAPS`。
# 不覆盖这四条的话，「不许涨 / 不许赖」就只是 main() 里两个没人验证过的 if。
SELFTEST_RATCHET: tuple[tuple[str, str, str, bool, str], ...] = (
    ("Q9", "**不许涨**：未接线 + **不在** `KNOWN_GAPS` ⇒ NEW（新掉线，必须红）", MISSING, False, GAP_NEW),
    ("Q10", "**不许赖**：未接线 + **在** `KNOWN_GAPS` ⇒ KNOWN（已登记，不阻断）", MISSING, True, GAP_KNOWN),
    ("Q11", "已接线 + 仍在 `KNOWN_GAPS` ⇒ HEALED（必须删条目）", USED, True, GAP_HEALED),
    ("Q12", "已接线 + 不在豁免表 ⇒ OK（正常态）", USED, False, GAP_OK),
)


def run_ratchet_self_test() -> int:
    print("\n自检·棘轮（合成夹具）—— 四条状态分开报：")
    bad: list[str] = []
    global KNOWN_GAPS
    real = KNOWN_GAPS
    try:
        for key, desc, verdict, known, expect in SELFTEST_RATCHET:
            KNOWN_GAPS = {"Widget": "自检用"} if known else {}
            got = classify_gap("Widget", verdict)
            ok = got == expect
            print(f"  [{key}] {desc}")
            print(f"        期望 {expect:8s} 实测 {got:8s} {'✓' if ok else '✗'}")
            if not ok:
                bad.append(f"[{key}] 期望 {expect} 实测 {got}")
    finally:
        KNOWN_GAPS = real
    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ 工具坏了（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    return 0


def run_self_test() -> int:
    print("自检（合成夹具，与产品无关）—— 各臂分开报：")
    bad: list[str] = []
    global INLINE_OK
    real_inline = INLINE_OK
    INLINE_OK = SELFTEST_INLINE
    try:
        for key, desc, files, expect in SELFTEST:
            got, _, _ = verdict_of("Widget", files)
            ok = got == expect
            print(f"  [{key}] {desc}")
            print(f"        期望 {expect:18s} 实测 {got:18s} {'✓' if ok else '✗'}")
            if not ok:
                bad.append(f"[{key}] 期望 {expect} 实测 {got}")
    finally:
        INLINE_OK = real_inline

    if bad:
        print(f"\n自检失败 {len(bad)} 臂 ⇒ **工具坏了，不是产品坏了**（exit 2）")
        for b in bad:
            print(f"  - {b}")
        return 2
    print("\n自检通过。四条前提都成立：")
    print("  · 检出能力：Q1（产品页）/ Q3（组件库内部）")
    print("  · 对照组干净：Q4（全无）/ Q5（不自计）/ Q6（不吃前缀更长的名字）")
    print("  · 预览页不算被用：Q2 / Q8")
    print("  · 等价实现可免除误报：Q7，且等价实现**只在预览页时不算数**：Q8")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="组件接线判据（规范点名组件是否真的被用）")
    ap.add_argument("--self-test", action="store_true", help="只跑合成夹具自检，不扫仓库")
    ap.add_argument("--verbose", action="store_true", help="打印等价实现的命中数")
    args = ap.parse_args()

    if args.self_test:
        return run_self_test() or run_ratchet_self_test()

    if not FRONTEND.exists():
        print(f"[env] 找不到前端目录：{FRONTEND}", file=sys.stderr)
        return 2

    files = load_files()
    if not files:
        print("[env] 未扫描到任何 .tsx —— 路径或环境有问题，不判产品", file=sys.stderr)
        return 2
    print(f"扫描 {len(files)} 个 .tsx（三棵树：产品页 / 预览页 / 组件库内部）\n")

    print("【必判：P0/P1，未接线即缺陷】")
    print(f"  {'组件':<18} {'①产品':>5} {'②预览':>5} {'③内部':>5}  判定")
    gaps: list[str] = []
    inline_notes: list[str] = []
    known_open: list[str] = []
    healed: list[str] = []
    for name, note in REQUIRED.items():
        v, c, _ = verdict_of(name, files)
        mark = "✓" if v == USED else "✗"
        state = classify_gap(name, v)
        tag = ""
        if state == GAP_HEALED:
            healed.append(name)
        elif state == GAP_KNOWN:
            known_open.append(name)
            tag = "   🚨已知缺口（不阻断）"
        print(f"  {name:<18} {c[TREE_PRODUCT]:>5} {c[TREE_PREVIEW]:>5} {c[TREE_INTERNAL]:>5}  {v} {mark}{tag}")
        if state == GAP_NEW:
            gaps.append(f"{name}：{v}（{note}）—— 三棵树 ①{c[TREE_PRODUCT]} ②{c[TREE_PREVIEW]} ③{c[TREE_INTERNAL]}")

    print("\n【必判但允许内联等价承担】")
    for name, (note, _rx, why) in INLINE_OK.items():
        v, c, eq = verdict_of(name, files)
        mark = "✓" if v in (USED, INLINE) else "✗"
        print(f"  {name:<18} {c[TREE_PRODUCT]:>5} {c[TREE_PREVIEW]:>5} {c[TREE_INTERNAL]:>5}  {v} {mark}  等价实现命中 {eq}")
        if v == INLINE:
            inline_notes.append(f"{name}：组件未被产品页使用，但**等价实现已命中 {eq} 处** ⇒ 能力由页面内联承担，**不判缺陷**")
        elif v not in (USED,):
            gaps.append(f"{name}：{v}（{note}）—— 且无等价实现")
        if args.verbose:
            print(f"      {note}\n      理由：{why}")

    print("\n【观察：不判缺陷】")
    for name, why in OBSERVE.items():
        v, c, _ = verdict_of(name, files)
        print(f"  {name:<18} {c[TREE_PRODUCT]:>5} {c[TREE_PREVIEW]:>5} {c[TREE_INTERNAL]:>5}  {v}")
        if args.verbose:
            print(f"      {why}")

    if inline_notes:
        print("\n【等价实现说明（避免误读成缺口）】")
        for n in inline_notes:
            print(f"  · {n}")

    if known_open:
        print(f"\n【已知缺口 {len(known_open)} 项 —— 已登记，**不阻断 CI**，但每轮都会打印】")
        for n in known_open:
            print(f"  🚨 {n}：{KNOWN_GAPS[n]}")

    if gaps or healed:
        if gaps:
            print(f"\n发现 **{len(gaps)} 个**接入缺口（P0/P1 组件未被使用，且无等价实现，"
                  f"**且不在 `KNOWN_GAPS` 已知清单内** ⇒ 新掉线）：")
            for g in gaps:
                print(f"  ✗ {g}")
            print("\n提示：`PREVIEW_ONLY` = 建好了、预览页能渲染、产品页没用；")
            print("      `MISSING` = 三棵树全无 —— 连组件库内部都没引用。")
        if healed:
            print("\n🚨 以下组件**已接线**，但仍留在 `KNOWN_GAPS` 里 ⇒ "
                  "请删除对应条目（豁免必须跟着事实走，否则这道防线会静默失效）：")
            for n in healed:
                print(f"  · {n}")
        return 1

    print("\n除已知缺口外，全部必判组件均已接线（产品页 / 组件库内部 / 等价实现，三者至少其一）。")
    print("⚠️ 本判据**只能证伪不能证真**：它拦住「组件根本没用」，")
    print("   拦不住「用错了地方」，也拦不住「内联实现与规范有视觉分歧」。")
    print("   别把它当成接入质量的证明。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
