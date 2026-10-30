# 裁定：P2 待拍板项收口状态（2026-10-17）

> 范围：I2/I3、ProvenanceBlock Q1–Q3、im/chat 的 InfiniteList。
> 流程：design-audit.md §16(12) 8 步裁定流程。标注「已拍板/已落地」「仍需拍板」「⚠️文档 desync」。
> 一句话：**I2/I3 的合规核心（A1·404）已闭合；ProvenanceBlock Q1/Q3 实际已落地只是记录没跟上；im/chat InfiniteList 是功能工单不是接线。**

---

## 1. I2 / I3（跨客户读路由收口 + 显式共享）

### 1.1 事实（来源：roadmap-update-pending-rulings-2026-09-22.md）

- **A1 · 6 条跨客户读路由是否收口（客户乙读客户甲 ⇒ 404 非 403）**：**已由 2026-10-16 的 404 裁定确认**（deliverables/product-strategy/ruling-cross-tenant-404-2026-10-16.md）—— 404 是有意枚举防护、正确，**合规核心决策落地**。
- **I2 · 「显式共享」上线**（案件页一键分享 + 带有效期链接 + `CASE_SHARE_SCOPE` 开关）：**未开工**，依赖设计稿（I1）。
- **I3 · 收口 + 灰度**（6 条路由补归属校验 + 🟡4 条强制审计留痕 + 灰度）：**未开工**，且 **I3 硬阻塞于 I2**（「先上共享、后收紧」，否则同事突然看不到 = 效率回退，瑞思点名）。

### 1.2 裁定建议

- **A1 已闭合**——无需再议；门禁层面 404 行为已被授权层覆盖，无缺口。
- **I2/I3 作为工程迭代，仍须 A2–A6 产品拍板后才开工**，尤其：
  - A2「先共享后收紧」硬前置（A1 不能单独开工）；
  - A5 批准 `CASE_OWNER_SCOPE` 回滚开关（试点期 404 突增的唯一退路）。
- 遵从「未拍板 I2/I3 不许开工」——当前未拍板（除 A1 已决）。

---

## 2. ProvenanceBlock Q1 / Q2 / Q3 —— ⚠️ 记录 desync（重点）

### 2.1 事实核查（关键）

`block3-item9-provenance-wiring-2026-10-10.md`（2026-10-10，授权「全部按建议推进」）**正文**已写明：

| 问 | 拍板 | 落地情况 |
|---|---|---|
| **Q1** C 类「中性卡片 + AI 页眉」接不接 | **C1 接**（卡片本体也染 AI 态） | ✅ 已落地：web/qa 简单回复卡 + 四段式结构化卡（#5/#6）接 `state="ai"` + `as="article"` |
| **Q3** 给组件加 `as` 可选参数 | **A1 加**（默认 `"div"`，不传 = 逐字不变） | ✅ 已落地：`ProvenanceBlock` 加 `as`（`"div"\|"article"\|"section"`），4 处本就是 `<article>` 的调用点显式传 `as="article"` |
| **Q2** B 类状态提示条要不要形状规范 | **B1 不立规范** | ✅ 既定站位：提示条四边实线是**有意**的，不是漏改 |

### 2.2 ⚠️ desync（必须修）

- 同一文档「**第七节 待拍板**」表格（lines 136–142）**仍把 Q1/Q2/Q3 列为待拍板**；
- 且 MEMORY.md、ui-optimization-stage-summary-2026-10-15.md 的 P2 清单也仍写「ProvenanceBlock Q1/Q2/Q3（Q3=共享包加 as）待拍板」。

⇒ **Q1（C1）与 Q3（A1）实际上已拍板且已实现**，属「记录没跟上实现」，不是真待拍板。

### 2.3 裁定建议

- 立即把三处记录（本文件尾表、MEMORY.md、stage-summary）改为「✅ Q1=C1、Q3=A1 已落地（2026-10-10）」；Q2=B1 记一笔「有意」即可。
- **唯一真残留**：ProvenanceBlock 三态视觉（顶部虚线 / 左侧 3px 实线 / 浅紫底 + 深色令牌切换）**只到源码层 + 类型层**，**渲染层未跑**（block3-item9 §6 明示）—— 真机视觉需 `browser-all`（≈22–25min）合并验证。建议挂在「渲染层统一验证」批次，不作独立阻塞项。

---

## 3. im /chat 的 InfiniteList

### 3.1 事实核查

- 组件已建（`packages/ui/src/components/mobile/InfiniteList.tsx`）且**已接入 lawyer `/notifications`**（lawyer/app/(app)/notifications/page.tsx:304，替换原手动「加载更多（n/N）」按钮）。
- `verify_component_wiring.py` 记录：`InfiniteList → lawyer /notifications`。
- 唯一未接的是 **im `/chat` 会话列表**：mobile-feature-integration-spec.md（line 380/386）明确——「im /chat 会话列表的 InfiniteList 需要先有『分页』，而本页没有」；列表按 进行中/已结束 **分组**，`InfiniteList` 是**扁平**模型。**接它 = 建功能**（补会话列表分页后端 + 处理分组），**不是接线**。

### 3.2 裁定建议

- lawyer/notifications 已接 ⇒ 门禁里 InfiniteList 的「PREVIEW_ONLY / 0 接入」状态应**更新**为「已接 1 处」（避免把已落地写成缺口）。
- im/chat 会话列表：**不归类为「接线」**，应作为**独立功能工单**（分页 + 分组适配）单列，待拍板「是否本期 + 分页范围」。
- 门禁层面：**不要**把 InfiniteList 移出 `KNOWN_GAPS` 并硬红，直到 im/chat 接入范围明确（否则假绿/假红）。

---

## 4. 更新后的待拍板清单

| 项 | 状态 | 需用户拍板 |
|---|---|---|
| I2 显式共享 | 未开工，依赖设计稿 | A2–A6 拍板 + 排期 |
| I3 收口+灰度 | 未开工，硬阻塞 I2 | 同上；**A1(404) 已闭合** |
| ProvenanceBlock Q1/Q3 | ✅ 已落地（**记录 desync**，仅须更新记录） | 无（更新三处记录） |
| ProvenanceBlock Q2 | ✅ B1 既定站位（有意四边实线） | 无 |
| ProvenanceBlock 渲染验证 | ⏳ 待 `browser-all` 合并跑 | 挂渲染批次 |
| im/chat InfiniteList | ⏳ 功能工单（非接线） | 是否本期 + 分页范围 |
| cjk-gap 覆盖率 | 只报不判 | A/B/C 路径 + 是否实测（见 ruling-cjk-gap-2026-10-17.md） |

---

> 本裁定梳理现状与 desync，**不改动任何产品代码**；其中 I2/I3、im/chat 功能范围、cjk-gap 路径仍需产品负责人审定。
