# 设计文档：对话式咨询 Agent + 律师复核闭环

> 状态：P0 + Phase 1 + Phase 2 已实现（2026-09-30）：解耦 / 草稿入复核 / 确认回流闭环打通；Phase 3（打磨）待排期。
> 背景：客户在智能问答 / IM 中常被「四段式结构化卡片」劝退、AI 味重；同时无人机确认的法律意见存在合规风险。
> 目标：**客户侧自然聊天，四段式转为律师侧草稿，律师确认后把带签章的咨询报告回流给客户**。

---

## 1. 目标架构与数据流

```
┌─────────┐  自然语言消息   ┌──────────────┐   信息齐了 → 生成四段式草稿(内部)
│  客户    │ ─────────────► │  Conversation │   ──────────────────────────────┐
│ (Web/IM) │ ◄───────────── │   Engine(BOT) │                                 │
└─────────┘   自然对话回复   └──────────────┘                                 ▼
                  │  (草稿不推给客户)                              ┌────────────────────┐
                  │                                               │ ConsultReport(草稿) │
                  │                                               └─────────┬──────────┘
                  │                                                         │ 创建 Review
                  │                                                         ▼
                  │                                          ┌──────────────────────────┐
                  │                                          │ Review(L2/L3) 律师复核台  │
                  │                                          │ 编辑 / 确认 / 驳回         │
                  │                                          └───────────┬──────────────┘
                  │                                 APPROVE → 定稿带律师署名/时间戳
                  │                                                      │
                  │                                                      ▼
                  │                                          ┌──────────────────────────┐
                  └──────────────── 回流「律师已确认」的咨询报告 ─┤ ConsultReport(已确认)      │
                                                             └──────────────────────────┘
```

**核心原则**：四段式从「客户可见的回复」降级为「律师侧草稿 / 内部工作产物」，客户全程只在聊天流里看到自然对话与最终定稿报告。

---

## 2. 现有可复用资产（避免重复造轮子）

经代码核查，闭环所需的大部分基础设施已存在：

| 能力 | 现有实现 | 说明 |
|---|---|---|
| 多轮会话 Agent + 状态机 | `ConversationEngine`（`backend/app/services/conversation_engine.py:33`） | 状态 `BOT→WAITING_HUMAN→HUMAN→CLOSED`；维护 `conv.context`（意图/争议类型/已收集事实/待补材料）；含派单分支 |
| 意图/争议类型识别 | `intent_service`（`recognize_intent` / `summarize_dispute_type`） | 已被 engine 调用 |
| 律师复核工作流 | `Review` + `ReviewRecord`（`backend/app/models/review.py:15`） | 含 `L2/L3` 级别、`assignee_id`、`decision`、`comment`、完整留痕 |
| 复核服务/强制复核 | `review_service.py`、`forced_review.py` | 已有「AI 输出→律师修改→确认」流程 |
| 律师指派/派单 | `DispatchService`（`dispatch_service.py:31`）+ `LawyerProfile.can_l3_review` | 支持指定/自动派单，优先 `can_l3_review` 终审 |
| 消息/会话落库 | `Conversation` / `Message`（`backend/app/models/conversation.py`） | `Message.card_payload`、`citation_ids` 已支持结构化卡片与溯源 |
| 法律检索/分词 | `qa_service._retrieve` / `_tokens`（bigram 中文分词） | 已修复，可按相关度注入法条/类案 |
| 前端「非结构化」渲染 | `qa/page.tsx` 的 `structured:false` 分支 | 寒暄已隐藏四段式骨架（首期前端基础已具备） |

**结论**：主要工作量在「编排 + 少量模型/接口增量」，而非从零搭建。

---

## 3. 关键设计决策

1. **四段式 = 内部草稿**：`结论/法律依据/行动建议/风险提示` 仅作为 `ConsultReport` 的草稿态字段，不进入客户消息体。
2. **何时生成草稿**：满足以下任一即触发
   - 会话上下文收集齐「必填事实」（见 §4 事实模型），且 `intent` 属咨询类；
   - 用户主动点「生成咨询报告 / 请律师确认」。
3. **信息是否充足**：复用 `intent_service` + `conv.context` 中的必填字段做校验；不足时 Bot 继续自然追问（不生成草稿）。
4. **律师指派**：草稿创建 `Review` 后，复用 `DispatchService` 派单（指定律师优先；否则自动派给 `can_l3_review` 律师终审）。
5. **引用溯源随草稿走**：`citations` 随 `ConsultReport` 进入 `Review`，律师可在复核台增删。
6. **合规约束**：对应 PRD 5.5 强制复核语义——`Review` 未 `APPROVE` 前，报告不得定稿、不得推回客户。
7. **入口统一**：`/qa` 页与 IM 共用同一套「解耦后的对话能力」，避免两套行为。

---

## 4. 会话事实模型（用于「信息是否充足」判定）

`conv.context` 已存 `last_intent` / `dispute_type`，建议扩展为：

```json
{
  "dispute_type": "劳动合同解除",
  "facts": {
    "parties": "公司与员工",
    "timeline": "2025-03 入职 / 2026-09 被通知解除",
    "amount": 80000,
    "has_contract": true,
    "has_evidence": ["聊天记录", "工资流水"]
  },
  "missing": ["解除通知形式", "是否书面通知"],
  "ready": false
}
```

`ready=true` 即触发草稿生成；`missing` 非空时 Bot 用自然语气追问。

---

## 5. 数据模型变更

### 5.1 `ReviewTargetType` 枚举新增
```python
class ReviewTargetType(str, Enum):
    CASE_ANALYSIS = "case_analysis"
    DOCUMENT = "document"
    EVIDENCE_LIST = "evidence_list"
    COMPLIANCE_REPORT = "compliance_report"
    CONSULT_REPORT = "consult_report"   # 新增：咨询报告草稿
```

### 5.2 新增 `ConsultReport` 表（替代「把四段式塞进 Message.card_payload」）
```python
class ConsultReport(Base, TenantMixin, TimestampMixin):
    conversation_id: int          # 关联会话
    case_id: Optional[int]        # 可关联案件
    draft_sections: dict          # 四段式草稿 {conclusion, legal_basis, advice, risk}
    citations: list               # 引用溯源
    status: str                   # draft | approved | rejected
    lawyer_id: Optional[int]      # 指派律师
    signed_by: Optional[int]      # 最终确认律师
    signed_at: Optional[str]      # 署名时间戳（合规留痕）
    final_report: Optional[dict]  # 律师确认后的定稿（可与原草稿不同）
    review_id: Optional[int]      # 关联 Review
```
> 说明：也可用 `Message.card_payload` 承载草稿，但独立表更利于复核留痕、统计与合规审计，建议独立建表。

### 5.3 `Conversation` 增量
- `report_id: Optional[int]`（关联最新 `ConsultReport`）。

---

## 6. 分阶段任务清单

### Phase 0 — 解耦：客户自然语言（**已实现并验证**）
目标：四段式从客户可见回复中剥离，客户只看到自然对话；四段式在后台作为草稿落库。

- [ ] **T0.1** 拆分 `conversation_engine._consult_answer`（`backend/app/services/conversation_engine.py:120`）
  - Bot 给客户的 `reply` = 自然对话（追问/引导），不再返回四段式卡片；
  - 四段式仅在「信息齐备」时作为 `ConsultReport(草稿)` 落库，不进 `Message`。
- [ ] **T0.2** 会话事实收集与 `ready` 判定
  - 扩展 `conv.context` 为 §4 事实模型；
  - 增加自然追问逻辑（基于 `missing` 字段，口语化、不机械）。
- [ ] **T0.3** `/qa` 页与 IM 共用解耦后的能力
  - `/qa` 调用 `ConversationEngine`（或抽取共享的「自然对话」服务），保证两入口行为一致；
  - 沿用现有 `structured:false` 前端分支，客户侧永不渲染四段式骨架。
- [ ] **T0.4** `ConsultReport` 草稿落库
  - 新增模型 + migration；`ready=true` 时写 `draft_sections` / `citations`，`status=draft`。
- [ ] **T0.5** 前端：客户侧确认不展示四段式（已基本具备，回归验证）
- [ ] **验收标准**：输入「你好 / 我有个法律问题想咨询一下」→ 自然引导、0 四段式、0 硬塞引用；输入真实法律问题 → 自然追问收集事实，后台生成草稿（客户不可见）。

### Phase 1 — 草稿入复核（**T1.1–T1.3 已实现；T1.4 IM 占位已实现；/qa 为无状态问答故不展示占位**）
- [ ] **T1.1** 新增 `ReviewTargetType.CONSULT_REPORT` 枚举。
- [ ] **T1.2** 草稿生成后自动创建 `Review`（级别 L2，指定律师时按 `can_l3_review` 升 L3），经 `DispatchService` 派单。
- [ ] **T1.3** 律师复核台展示四段式草稿 + 引用，支持编辑/驳回/确认（复用 `review_service`）。
- [ ] **T1.4** 客户侧显示「已为您整理咨询要点，律师确认中…」占位，非四段式。

### Phase 2 — 确认回流（**T2.1–T2.3 已实现；合规：未 APPROVE 不定稿、不推回客户**）
- [ ] **T2.1** 律师 `APPROVE` → 定稿 `ConsultReport`（`signed_by` / `signed_at` / `final_report`）。
- [ ] **T2.2** 定稿推回客户会话消息，带「律师已确认」标识 + 署名/时间戳。
- [ ] **T2.3** 前端：客户侧「律师已确认」咨询报告卡片（四段式此时可展示，但带律师背书）。

### Phase 3 — 体验打磨（待评审后排期）
- [ ] 多轮追问 UX、转人工无缝衔接、满意度评价、超时/律师离线兜底。

---

## 7. 风险与开放问题

1. **模型网关延迟**：QA 模型偶发 >60s 超时（CME 网关）。Phase 0 即应加「短超时 + 优雅降级提示」，避免客户长时间转圈。
2. **`ready` 阈值**：事实充足判定过松会生成垃圾草稿、过严会一直追问。需结合真实语料调参。
3. **独立表 vs `card_payload`**：建议独立 `ConsultReport` 表（§5.2），便于审计；需评审确认。
4. **律师产能**：草稿自动派单后若律师长时间不确认，需超时兜底（转派/告知客户）。
5. **入口一致性**：`/qa` 与 IM 当前是两套实现，Phase 0 需决定统一为共用 `ConversationEngine`。

---

## 8. 待评审确认项

- [ ] 入口：Phase 0 是只改 IM 的 `conversation_engine`，还是同时统一 `/qa` 页？（影响 T0.3 范围）
- [ ] `ConsultReport` 是否独立建表，还是复用 `Message.card_payload`？
- [ ] Phase 0 是否顺带加「模型短超时 + 降级提示」？
- [ ] 律师终审级别默认 L2 还是强制 L3？
