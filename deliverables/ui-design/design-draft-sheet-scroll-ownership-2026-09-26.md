# 设计草案 · §9 第 10 项：`BottomSheet` 的「滚动权交给调用方」开关

> **状态：✅ 已定稿（2026-09-26 · 用户选 A）并已落地。**
> 契约已并入 `mobile-feature-integration-spec.md` **§5「✅ 定稿」**；
> `packages/ui` 的 `BottomSheet` / `NotificationCenter` 已改，`PullToRefresh` 已接线（NR-11）。
> ⇒ 本文件转为**定稿记录**（记「怎么定的 + 两轮实测」），**「要求」身份由 §5.1 承担**。
> ⏳ 仍缺：通知中心 BottomSheet 的**真机渲染**验证（须走 `browser-all` 重路径）。
>
> **归属**：任务 **#37**（`CameraCapture` / `PullToRefresh` / `InfiniteList` 接入）的**硬前置**。
> **上游裁定**：§9 第 10 项 = **①**（给 `BottomSheet` 加开关）—— 已获「全部按你的建议」授权。
>
> ⚠️ 本文件**刻意不用编号标题**（用「一、二、…」），以免被覆盖率报表的
> 「未分类文档」守卫（`report_spec_coverage.py` 判据 Q14）判红。**定稿后要求并入的
> `mobile-feature-integration-spec.md` 已在 `SPEC_FILES` 内**（`report_spec_coverage.py:178`），
> 届时由它承担「要求」身份。

---

## 一、这份草案要定什么

PRD **NR-11** 点名要求通知中心移动 BottomSheet「列表可滚动、**支持下拉刷新**」。
但两个组件**各自带滚动容器**：

| 组件 | 滚动容器 | 位置 |
|---|---|---|
| `BottomSheet` | **硬编码** `scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4` | `packages/ui/src/components/mobile/BottomSheet.tsx:170` |
| `PullToRefresh` | 自带 `ref={scrollRef}` + `overflow-y-auto overscroll-contain` | `packages/ui/src/components/mobile/PullToRefresh.tsx:124-135` |

而 `PullToRefresh` 的手势守卫是 `if (!el || el.scrollTop > 0) return`（`:56` / `:63`）——
它**假设自己那个容器就是真正在滚的那个**。若真正滚动的是外层，内层 `scrollTop` 恒为 0 ⇒
**守卫失效** ⇒ 列表滚到中段后上滑，仍会被判成「在顶部」而触发刷新。

⇒ 所以「给 NR-11 接一行 `<PullToRefresh>`」是**错的**。需要先把「谁持有滚动权」**显式化**。

---

## 二、实测（两轮合成探针，与产品无关）

### 第一轮探针 —— 证明「朴素嵌套不可用」（2026-09-20）

`evidence/probe_pulltorefresh_in_sheet.py`（**已登记** `run_ci_probes.py` 的 `NON_GATE_SCRIPTS`）：

| 臂 | 接法 | 外层 `scrollTop` | 内层 `clientH`/`scrollH` | 内层 `scrollTop` | 守卫 |
|---|---|---|---|---|---|
| **A** | 朴素嵌套（`PullToRefresh` 根不加高度） | **1271**（外层在滚） | 1600 / 1600 ⇒ **滚不动** | **0（恒）** | ❌ **失效** |
| **B** | 根加 `height:100%` | 0 | 329 / 1600 | 1271 | ✅ |
| **C** | 对照：只有内层滚动 | —（无外层） | 361 / 1600 | 1239 | ✅ |

**结论**：A 不可用；B 能跑但**依赖外层 `py-4` + `flex-1` 恰好如此** ——「碰巧对」，不是「由构造对」。

### 第二轮探针 —— 定夺「内容区槽位」的形态（2026-09-26，本次）

同一个探针思路，改问「`content` 模式的槽位该写成什么」：

| 臂 | 槽位写法 | 子元素 | 槽位高 | 调用方高 | 内层盒高 | 内层 `clientH`/`scrollH` | 内层滚到底 `scrollTop` | 判定 |
|---|---|---|---|---|---|---|---|---|
| **P** | `grid` + `grid-template-rows: repeat(1, minmax(0,1fr))` | 单子元素（**无** `min-h-0`） | 361 | 361 | 361 | 361 / 1600 | 1239 | ✅ 成立 |
| **Q** | `flex` 列 | 单子元素（**无** `min-h-0`） | 361 | **1600** | 1600 | **1600 / 1600** | **0** | ❌ **不成立** |
| **R** | `flex` 列 | 单子元素（**有** `min-h-0`） | 361 | 361 | 361 | 361 / 1600 | 1239 | ✅ 成立 |
| **S** | `flex` 列 | **三个**子元素（表头 / PTR / 页脚） | 361 | — | 表头 **34** · 内层 **293** · 页脚 **34** | 293 / 1600 | 1307 | ✅ 成立 |
| **F** | `grid` + `grid-rows-1` | **三个**子元素 | 361 | — | **第 1 个被压成 0** | 340 / 1600 | 1260 | ⚠️ **表头被压扁** |

**三条决定性事实**：

1. **`grid` 槽位只支持单子元素** —— F 臂里表头被压成 **0 高**（隐形）。
   而 `NotificationCenter` 的 `body` 天然是**三段**（表头 / 滚动列表 / 页脚，见 §四）⇒ **`grid` 不可用**。
2. **`flex` 槽位支持 N 个子元素**（S 臂：表头 34px、页脚 34px 都钉住，内层独占 293px 滚动）⇒ **选它**。
3. **但 `flex` 槽位对「单子元素」有一个真陷阱** —— Q 臂：调用方若把内容包一层
   `flex flex-col` 却**忘了** `min-h-0`，调用方会被撑到 **1600px**、内层**滚不动**（`scrollTop` 恒 0）。
   ⚠️ 这**正是「碰巧对」的反面**：不报错、不白屏，只是手势静默失效 —— 与坑 71 同族。

⇒ **契约必须把这两条写出来**（见 §三）。

---

## 三、提案：新增 `scrollOwner` prop

### API

```ts
export interface BottomSheetProps {
  // …既有 prop 不变…
  /**
   * 内容区的**滚动权**归谁。
   * - `"sheet"`（**默认**）：`BottomSheet` 自带滚动容器（**现状，逐字不变**）
   * - `"content"`：把滚动权交给调用方 —— 内容区变成**不滚动**的
   *   `flex min-h-0 flex-1 flex-col` 槽位，且**不带内边距**，由调用方自行管理。
   *   适用于内嵌 `PullToRefresh` / 虚拟列表等**自带滚动容器**的组件。
   */
  scrollOwner?: "sheet" | "content";
}
```

### DOM 契约

| 模式 | 内容区渲染 |
|---|---|
| `"sheet"` | `<div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>`（**现状**） |
| `"content"` | `<div className="flex min-h-0 flex-1 flex-col">{children}</div>` |

`"content"` 模式下调用方的**两条义务**（由 §二 的 Q/S 臂实测得出）：

1. 自己滚动的那一层要 `flex-1`（`PullToRefresh` 根加 `className="flex-1"`）；
2. 若在槽位与滚动层之间**再包一层** `flex` 容器，那一层**必须带 `min-h-0`**（否则 Q 臂的失败形态）。

### 两个设计选择的理由

**为什么 `"content"` 模式不带内边距？**
`"sheet"` 模式的 `px-4 py-4` 是给「裸内容」用的。而调用方既然接管了滚动，
它**已经知道自己的行/表头有没有内边距** —— 再叠一层就是**双边距**。
⚠️ 实测现状**已经**有这个双边距：`BottomSheet` 槽位 `px-4` + `NotificationRow` 自带 `px-4`
⇒ 移动端通知行的左右内边距是 **32px**，而桌面下拉面板是 **16px**（`NotificationCenter.tsx:827` 的面板不带 `px`）。
⇒ 本项**顺带修掉这个不一致**，属本次改动的**附带收益**（不是额外范围）。

**为什么槽位用 `flex` 而不是 `grid`？**
见 §二 事实 1：`body` 是**三段**结构，`grid-rows-1` 会把第一段压成 0 高。`flex` 列与
桌面下拉面板（`NotificationCenter.tsx:827` 的 `flex max-h-… flex-col overflow-hidden`）**同构**，
两端可共用同一套子元素。

---

## 四、NR-11 的接线形态（定稿后才施工）

`NotificationCenter.tsx` 的 `body`（`:719-792`）目前是**一段三块**，被桌面与移动**共用**：

```
body = 表头（:721 px-4 py-2.5 / 通知 + 全部已读）
      + 自带滚动容器（:742 scroll-thin min-h-0 flex-1 overflow-y-auto）
      + 页脚（:777 onViewAll 时）
```

⇒ 拆成 `header` / `listBody` / `footer` 三块，两种装配：

```tsx
// 桌面：锚定下拉面板（:823-831）—— 结构不变
<>
  {header}
  <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">{listBody}</div>
  {footer}
</>

// 移动：BottomSheet 把滚动权交给 PullToRefresh（NR-11 点名的那一条）
<BottomSheet isOpen={open} onClose={…} heightRatio={0.7} scrollOwner="content">
  {header}                                          {/* flex:0 0 auto */}
  <PullToRefresh onRefresh={refresh} className="flex-1">{listBody}</PullToRefresh>
  {footer}                                          {/* flex:0 0 auto */}
</BottomSheet>
```

`refresh` = `fetchUnread()`（`:335`）+ `loadList()`（`:639`），两者都已是 `async` `useCallback`。

⚠️ **既有行为的两处变化**（都是**修**，需一并认下）：
1. 移动端表头 / 页脚**从「跟着滚走」变为「钉住」**（与桌面一致）—— 现状是三层内容一起被
   槽位滚动，表头会滚出视野；
2. 移动端左右内边距 **32px → 16px**（见 §三「两个设计选择的理由」）。

---

## 五、验证计划（判据）

1. **接线判据的棘轮会强制我删条目**：`evidence/verify_component_wiring.py:159-166` 的
   `KNOWN_GAPS` 里已登记 `PullToRefresh` / `InfiniteList` / `CameraCapture`；
   一旦接线成功，`classify_gap()`（`:233`）判 `GAP_HEALED` ⇒ **门禁判红**，必须删条目。
   ⇒ 这是设计好的行为（「豁免必须跟着事实走」），不是意外。
2. **真机渲染**：在移动视口下打开通知中心，量「内层 `scrollTop` 能不能 > 0」（守卫成立）
   与「槽位是否不再滚」。复用既有 `evidence/cdp.py` + Chromium 151 本机链路。
3. **新探针登记**：本轮第二轮探针现放在 `%TEMP%`（不入 `evidence/`）。定稿后若保留，
   须移入 `evidence/probe_sheet_scroll_contract.py` 并**登记进 `run_ci_probes.py` 的
   `NON_GATE_SCRIPTS`**（与 `probe_pulltorefresh_in_sheet.py` 同档：`**一次性**：…决策取证`），
   否则「新探针未登记 ⇒ 阶段 1 短路」。

---

## 六、待你定稿的一项

**`scrollOwner` 这个 prop 的命名与取值是否认可？**

| 选项 | 说明 |
|---|---|
| **A（本草案推荐）** | `scrollOwner?: "sheet" \| "content"` —— 把「谁持有滚动权」**显式命名**，与 §9 第 10 项的原话一致 |
| B | `scrollable?: boolean`（默认 `true`）—— 更短，但没说清**滚动权给了谁** |
| C | 不做开关，改为让 `PullToRefresh` 接受外部滚动元素（§9 第 10 项的 ②）—— 已裁定不取 |

选 **A** 即视为定稿，我随即：① 契约并入 `mobile-feature-integration-spec.md` §5.1；
② 改 `packages/ui/src/components/mobile/BottomSheet.tsx`；③ 接 NR-11 并删 `KNOWN_GAPS` 条目。

---

## 七、证据

- 第一轮探针：`evidence/probe_pulltorefresh_in_sheet.py`（已登记），2026-09-20。
- 第二轮探针输出：`evidence/sheet_scroll_contract_probe_2026-09-26.txt`（本次存证，含逐臂原始输出）。
- 磁盘核验（2026-09-26）：`BottomSheet.tsx:170` · `PullToRefresh.tsx:56,63,98,124-135` ·
  `NotificationCenter.tsx:719-792,823-838` · `NotificationRow` 的 `px-4` · Tailwind **3.4.17**
  （`frontend/package.json:16`，preset 未覆写 `gridTemplateRows`）。
- §9 原文：`deliverables/ui-design/mobile-feature-integration-spec.md:1152-1156`；
  §5.1 上游分析：同文件 `:251-286`。
