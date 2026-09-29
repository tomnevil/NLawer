# P0-15 通知系统 · 第二轮：补齐写入点（生产者接线）

**日期**：2026-09-16
**类型**：功能规格书补充 / 交付记录（承第一轮 `prd-notification-readpath-2026-09-16.md`）
**参与成员**：析客（需求分析师，第一轮 PRD）· 方向明（产品舵手，代码勘察与实施编排）

---

## 📌 TL;DR（执行摘要）

- **第一轮修的是「读不到」**（无列表接口、`is_read` 全库无赋值点、铃铛是死按钮）；
  **第二轮修的是「没得读」**——9 种 `NotificationType` 里有 **5 种从来没有写入点**，
  枚举定义了、前端图标配好了、`_TITLE` 映射也写了，但全库没有任何业务代码会发出它。
- 5 种中 **4 种已接线**（`CASE_ACCEPTED` / `REVIEW_DECIDED` / `EVIDENCE_MISSING` / `QUOTA_WARNING`）；
  **第 5 种 `DOCUMENT_CONFIRMED` 故意不接线**，因为「文书确认定稿」这条业务流**根本不存在**
  （`DocumentStatus.CONFIRMED` 全库零引用，文书状态机只实现到 `GENERATED`）。
- 接线过程中发现并修复 **3 处「写错就是静默噪音」的设计陷阱**（收件人、触发次数、触发时序），
  以及 **2 处前端连带缺陷**：`AppLayout` 从未把通知配置透传给 `AppShell`（铃铛在真实应用里仍是死的）、
  路由表有 3 个**不可达分支**且 `ref_type="review"` 无映射（点通知没反应）。
- 新增 31 条生产者用例；E2E 43/43、模块 31/31、ruff 全绿、全量回归通过、前端四端构建通过。
- **零新增依赖**。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 4 种通知按业务节点接线；`DOCUMENT_CONFIRMED` 记为**独立业务缺口**而非通知缺口 |
| 优先级 | P0（第一轮读路径的收口项） |
| 预期影响 | 通知从「9 种里 5 种永不触发」变为「8 种可达、1 种待流程实现」；客户首次能收到「谁接了我的案子」 |
| 资源需求 | 后端 5 文件改动 + 31 条用例；前端 4 文件改动；0 新增依赖 |
| 风险等级 | 低（均为旁路写入，失败不影响主流程） |

---

## 1. 为什么读路径修完还要回头补写入点

第一轮的诊断结论是「只写不读」——写入侧完整、读取侧为零。这个结论**只对了一半**。

第二轮把 9 种 `NotificationType` 逐个反查写入点时发现，写入侧本身也是残缺的：

| 通知类型 | 第一轮前的写入点 | 结论 |
|---|---|---|
| `DISPATCH_CREATED` | `dispatch_service.py` 派单时 | ✅ 已有 |
| `REVIEW_REQUIRED` | `review_service.py` 强制复核命中时 | ✅ 已有 |
| `CASE_ARCHIVED` | `archive_service.py` 归档时 | ✅ 已有 |
| `WORK_ORDER_CREATED` | `billing_service.py` 额度耗尽转工单时 | ✅ 已有 |
| `CASE_ACCEPTED` | **无** | ❌ 本轮接线 |
| `REVIEW_DECIDED` | **无** | ❌ 本轮接线 |
| `EVIDENCE_MISSING` | **无** | ❌ 本轮接线 |
| `QUOTA_WARNING` | **无** | ❌ 本轮接线 |
| `DOCUMENT_CONFIRMED` | **无** | ⛔ 故意不接线（见 §3） |

**为什么这类缺陷值得单开一轮**：它的失效方式是**完全静默**的——不报错、不 500、
日志干净、接口返回 200。只是那个功能「什么都不发生」。任何「接口能跑通」层面的
测试都发现不了它；只有专门针对**生产者**写断言才能锁住。

读路径已经把「黑箱」修好了，但箱子里有一半格子是空的。第二轮补的就是这一半。

---

## 2. 逐生产者接线决策

### 2.1 `CASE_ACCEPTED` —— 通知**客户**，不是律师

**接入点**：`dispatch_service.accept()`，`_on_assigned()` 之后。

**关键决策：收件人是谁。** 直觉写法是「律师接单了 → 通知律师」，但这是错的：
律师**刚刚亲手点了「接单」**，再推一条「你已接单」是纯噪音。

真正需要被知会的是**等待中的客户**——「谁在办我的案子、什么时候开始办」是委托人
最焦虑、也最该被主动告知的信息。在传统模式下这通电话通常由律所行政打，属于典型的
人工成本。通知免费，且不会漏。

**两个守卫**：
- `client_user_id` 为空（线下录入的案件）→ 不写。写进去也永远没人能读到。
- `client_user_id == lawyer_id`（律师自助办案）→ 不写。不给自己发通知。

### 2.2 `REVIEW_DECIDED` —— 只对「需要行动或有实质后果」的结论发

**接入点**：`review_service.decide()` 的三个分支（`REQUEST_REVISION` / `REJECT` / 定稿）。

| 结论 | 是否通知 | 理由 |
|------|---------|------|
| `REVISION_REQUESTED` | ✅ | 要律师去改，不通知就卡住 |
| `REJECTED`（作废） | ✅ | 有实质后果，必须知道 |
| `APPROVED` 且达级别（`CONFIRMED`） | ✅ | 可以定稿归档，是推进信号 |
| **`APPROVED` 但级别不足**（待更高级终审） | ❌ | 律师**无事可做** |

最后一条是**有意为之**，且是本轮最反直觉的决策。L2 通过、还等 L3 终审时，
承办律师确实不知道结果，但他**知道也无法行动**。一条不需要任何行动的通知，
代价不是它自己占的那一行，而是它稀释了「派单待接」「待复核」这些真通知的注意力。

`ref_type="review"` / `ref_id=r.id`：律师要处理的是**这次复核**，不是案件本身。
直接进复核页比「先进案件再找复核入口」少一次跳转。

### 2.3 `EVIDENCE_MISSING` —— 触发点必须放在**解析完成之后**

**接入点**：`job_handlers._evidence_parse_worker()`，`parse()` 返回之后。

**这是本轮最容易写错的一处。** 材料分类（`ev.category`）是**异步**由解析任务写入的。
若把检查放在上传接口里，那一刻 `category` 还是空值，`missing_list()` 会把**刚上传的
这份材料也算成「缺」的**——发出的是错误提醒，而且是最伤信任的那种：
**用户刚做完一个动作，系统立刻说他没做。**

**只提醒 `priority == 1`**：完整清单可能有十几项（三轮追问）。把全部缺失项一次性推给
律师，结果是一条通知里塞 12 个待办，等于没有重点。第一轮关键材料才是真正卡住
立案/庭审的东西，其余留在证据页里按轮次逐步引导。

### 2.4 `QUOTA_WARNING` —— 只在**跨过阈值**那一刻发一次

**接入点**：`billing_service.consume_atomic()`，扣减成功后。

**这是本轮最有价值的修复。** 扣减是**每次调用都发生**的高频动作。若写成
`if remaining / limit < 0.2: notify(...)`，那么用户一旦进入预警区，
**接下来每一次扣减都会再发一条通知**——额度剩 20 次、用户连用 20 次，
就会收到 20 条「额度不足」。

这类通知不但无用，还会**训练用户忽略通知**，连带让「派单待接」「待复核」
这些真通知一起失效。正确语义是**只在状态发生迁移的那一刻发一次**：

```
used_before < 阈值 <= used_after
```

**阈值取 80%**：留出 20% 的反应窗口。律所用户充值/调整套餐通常需要走内部审批，
太晚（如 95%）提醒等于没有提醒，太早（如 50%）则一个账期内大量用户长期处于
「预警中」，预警本身失去区分度。

**阈值必须向上取整**（`ceil(limit × 4/5)`），不能用 `int(limit × 0.8)`：
后者是**向下**取整，在额度较小时会把阈值压到「已经耗尽」的位置，预警永远不触发。
例：额度 3 → `int(2.4) = 2`，但真正危险的是第 3 次。已用边界用例锁死。

**顺序也有讲究**：预警必须在「耗尽转工单」（`WORK_ORDER_CREATED`）**之前**发。
后者是**事后**通知（额度已用光、工单已生成）；预警的价值在于给用户留出反应时间，
两者不是替代关系。

---

## 3. `DOCUMENT_CONFIRMED`：为什么故意不接线

### 3.1 事实

全库检索 `DocumentStatus.CONFIRMED` → **零引用**（`app/` 与 `tests/` 均无）。

进一步量化，文书状态机的实现边界是：

| 状态 | 是否被赋值 | 位置 |
|---|---|---|
| `DRAFT` | ✅ | `document_service.py:90` |
| `COLLECTING` | ✅ | `document_service.py:75, 90` |
| `GENERATED` | ✅ | `document_service.py:105` |
| `IN_REVIEW` | ❌ | — |
| `CONFIRMED` | ❌ | — |
| `EXPORTED` | ❌ | — |
| `VOIDED` | ❌ | — |

即：**文书生成出来后，既不能送审、也不能确认、更不能导出。** 下游半条流程不存在。

旁证：`ReviewTargetType` 五个成员中，只有 `CASE_ANALYSIS` 被引用（3 处），
`DOCUMENT` / `EVIDENCE_LIST` / `COMPLIANCE_REPORT` / `LEGAL_OPINION` 均为 **0 处**。
`document_service.render()` 甚至已经算出了 `doc.required_level`（高风险文书需强制复核），
但**没有任何代码消费它**——算出来就丢在那里。

### 3.2 决策

**不接线。** 给一个不存在的流程补 `notify(...)` 会制造两样东西：
一段**永不触发的死代码**，和一句「9 种通知已全部打通」的**假话**。

因此本轮把它记为**独立的业务功能缺口**（文书确认/导出流程未实现），
而不是通知系统的缺口。这两件事的责任方、工作量、验收标准都不同，
混在一张清单里会让两者都排不上期。

### 3.3 已落两条哨兵用例

- `test_document_confirmed_still_has_no_producer`：扫描 `app/` 下是否出现
  `DocumentStatus.CONFIRMED`。一旦文书确认流实现并接线，此用例**失败**，
  提醒把该类型从缺口清单移出。
- `test_document_flow_stops_before_confirmation`：断言文书状态机的实现边界
  **恰好**停在 `DRAFT/COLLECTING/GENERATED`。下游状态一旦开始被赋值即失败。

> 设计说明：哨兵用例只扫描 `app/`（生产代码）。测试文件自身的说明文字里
> 就含这个字面量，若一并扫描会**自我命中**，哨兵将永远为红。

---

## 4. 前端连带缺陷（接线后才暴露出来的）

后端接线完成后做端到端走查，发现**通知在生产环境里依然到不了用户手上**，
原因全在前端，且都是静默失效。

### 4.1 `AppLayout` 从未把通知配置透传给 `AppShell` → 铃铛仍是死的

第一轮我把 `NotificationCenter` 接进了 `AppShell`，并做成 opt-in
（`notifications?: AppShellNotifications`），理由是「避免未鉴权页面也去轮询」。

**但这个 prop 没有任何调用方传。** 律师端用的是 `AppLayout`（它在 `AppShell`
之上封装了会话恢复/鉴权跳转/面包屑），而 `AppLayout` 没有转发这个 prop。
结果：`AppShell` 里的 `{notifications && <NotificationCenter />}` 恒为假，
**铃铛在真实应用里依然什么都不渲染**——第一轮的修复实际只在 `components-preview` 生效。

**修复**：`AppLayout` 新增 `notifications?: { href, allHref?, pollMs? }`，
内部构造 `AppShellNotifications` 并转发。`href` 由各应用提供。

**为什么不给默认值**：铃铛点开后需要「点某条通知去哪」，而四端路由表不同。
给一个猜测的默认路由会在没有对应页面的应用里产生**死链**（用户点了通知跳到 404）。
**宁可不显示铃铛，也不显示一个会跳错的铃铛。**

### 4.2 路由表有 3 个不可达分支，且 `ref_type="review"` 无映射

修复前的 `hrefFor`（律师端通知页）：

```ts
if (n.ref_type === "case" && n.ref_id != null) return `/cases/${n.ref_id}`;  // ← 第 91 行
if (n.ref_type === "analysis") return "/reviews";                            // 死分支
if (n.type === "CASE_ARCHIVED") return "/archives";                          // 死分支
if (n.type === "DISPATCH_CREATED") return "/dispatches";                     // 死分支
if (n.type === "REVIEW_REQUIRED") return "/reviews";
return null;
```

后端实际的 `ref_type` 取值：

| 类型 | ref_type | ref_id |
|---|---|---|
| `DISPATCH_CREATED` | `"case"` | `case.id` |
| `CASE_ACCEPTED` | `"case"` | `case.id` |
| `EVIDENCE_MISSING` | `"case"` | `case.id` |
| `CASE_ARCHIVED` | `"case"` | `case.id` |
| `REVIEW_REQUIRED` | `target_type.value`（如 `"CASE_ANALYSIS"`） | `target_id` |
| `REVIEW_DECIDED` | `"review"` | `review.id` |

由此产生的三个问题：

1. **`ref_type === "case"` 在第 91 行拦下了 `DISPATCH_CREATED`**，把它送到
   `/cases/{id}`——而案件此时**尚未接单**，详情页没有可操作内容。
   第 94 行的 `/dispatches` 分支因此**永远不可达**。
2. **`ref_type === "analysis"` 与后端不符**：`REVIEW_REQUIRED` 传的是
   `target_type.value`，即大写的 `"CASE_ANALYSIS"`，不是 `"analysis"`。分支不可达。
3. **`REVIEW_DECIDED` 的 `ref_type="review"` 无任何映射** → 返回 `null` →
   **点复核结论通知没反应**。这是最隐蔽的一处：没有报错，只是「点了没用」。

**修复**：抽出 `apps/lawyer/lib/notificationRoutes.ts`，改为
**「按类型优先、按 ref 兜底」**，并把「与后端的对应关系」写成表格式注释
（改动需同步）。类型优先的理由：`type` 回答「用户该去做什么」，`ref_type`
只回答「挂着哪个对象」，前者更具体。

`DISPATCH_CREATED → /dispatches`（可操作处）而非 `/cases/{id}`（无内容）。

**同时消除双份维护**：通知页与顶栏铃铛现在共用这一张表
（`AppLayout` 的 `notifications.href` 与页面的跳转都指向 `notificationHref`），
避免「改一处漏一处」——这类双份路由表正是上面第 3 点的成因。

---

## 5. 验证证据

| 层级 | 命令 / 方式 | 结果 |
|---|---|---|
| 静态检查 | `ruff check .` | ✅ `All checks passed!` |
| 生产者单元测试 | `pytest tests/test_notification_producers.py` | ✅ **31 passed** |
| 读路径单元测试 | `pytest tests/test_notification_api.py` | ✅ 23 passed（第一轮） |
| 全量回归 | `pytest tests/` | ✅ **273 passed, 0 failed**（741.71s；= 第一轮 242 + 本轮新增 31） |
| 端到端 HTTP | `python verify_p0_15_notification.py` | ✅ **43 通过 / 0 失败** |
| 索引生效 | `EXPLAIN QUERY PLAN` | ✅ `SEARCH notifications USING INDEX ix_notifications_tenant_user_id` |
| 前端类型 | `tsc --noEmit`（packages + lawyer） | ✅ 0 error |
| 前端构建 | `next build` × 4 端 | ✅ 全部 `EXIT=0`（web / lawyer / admin / im；lawyer `/notifications` 4.47 kB） |
| 依赖安全 | `pip-audit` / `pnpm audit` | ✅ 均 **exit 0**（`No known vulnerabilities found, 10 ignored` / `No known vulnerabilities found`）；**依赖清单零变更** |

### 生产者用例分布（31 条）

| 组 | 覆盖内容 | 条数 |
|---|---|---|
| A | 额度阈值纯函数（向上取整、边界、单调性） | 4 |
| B | 额度预警跨阈值语义（**含「全周期恰好一条」**） | 6 |
| C | `CASE_ACCEPTED` 收件人选择与守卫 | 4 |
| D | `REVIEW_DECIDED` 三结论 + 自审/无律师/孤儿 + 截断 + **部分通过不通知** | 9 |
| E | `EVIDENCE_MISSING` 过滤 + **触发时序** + 断点续跑不重推 | 6 |
| F | 缺口哨兵 ×2 | 2 |

**三条最强断言**（同时覆盖「该发时发」与「不该发时不发」，任何阈值偏移或重复触发都会失败）：

- `test_quota_warning_fires_exactly_once_across_full_consumption`：
  模拟一个账期 100 次扣减，断言**恰好 1 条**预警且落在第 80 次。
- `test_quota_warning_does_not_refire_inside_warning_zone`：
  预警区内连续 19 次扣减，断言**一条都不发**。
- `test_partial_approval_does_not_notify`：
  走 `decide()` 全流程，断言 L2 通过、待 L3 终审时**不产生通知**，
  且状态保持 `PENDING_CONFIRM`（对照用例 `test_full_approval_does_notify` 断言达级别时必发）。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 | 状态 |
|---|------|--------|--------|------|
| 1 | `CASE_ACCEPTED` 接线（通知客户） | 后端 | 第十二轮 W3 | ✅ 完成 |
| 2 | `REVIEW_DECIDED` 接线（三结论 + 部分通过不通知） | 后端 | 第十二轮 W3 | ✅ 完成 |
| 3 | `EVIDENCE_MISSING` 接线（解析后判定，仅 priority=1） | 后端 | 第十二轮 W3 | ✅ 完成 |
| 4 | `QUOTA_WARNING` 接线（跨阈值一次 + 向上取整） | 后端 | 第十二轮 W3 | ✅ 完成 |
| 5 | 31 条生产者用例 | 后端 | 第十二轮 W3 | ✅ 完成 |
| 6 | `AppLayout` 透传通知配置（铃铛打通） | 前端 | 第十二轮 W3 | ✅ 完成 |
| 7 | 抽 `notificationRoutes.ts`，修 3 处死分支 + `review` 映射 | 前端 | 第十二轮 W3 | ✅ 完成 |
| 8 | 全量回归 + 四端构建 | 全栈 | 第十二轮 W3 | ✅ 完成 |
| 9 | **文书确认/导出流程**（含 `DOCUMENT_CONFIRMED` 接线） | 后端 | **待排期（独立需求）** | ⏳ 待办 |
| 10 | 复核接入文书/证据清单/合规报告（`ReviewTargetType` 4 个成员零引用） | 后端 | 待排期 | ⏳ 待办 |
| 11 | 用量看板 / 计费工单页面（`QUOTA_WARNING`、`WORK_ORDER_CREATED` 目前无落点） | 前端 | 待排期 | ⏳ 待办 |
| 12 | 前端埋点、离线补拉、保留策略、WS 推送（沿用第一轮清单 7~10） | 全栈 | 第十三轮 | ⏳ 待办 |

---

## ⚠️ 待确认 / 假设 / Non-goals

### 假设
- 额度预警阈值 80% 是**产品假设**，未经真实用户数据验证。建议上线后观察
  「预警后 7 日内充值/提额比例」，据此调整分子分母。
- 复核「部分通过不通知」的取舍假设：律师在等待更高级终审期间**无可执行动作**。
  若实际业务中律师需要提前知悉「我这部分过了」，应改为**降级为站内静默状态**
  而非通知（在复核页显示进度条），不占用通知配额。

### 待产品拍板
- **P1**：`QUOTA_WARNING` / `WORK_ORDER_CREATED` 是否需要落地页？目前点击不跳转。
  建议在律师端新增 `/billing`（用量与工单），否则这两类通知只能「看了就过」。
- **P2**：`EVIDENCE_MISSING` 的去重目前依赖前端按 `payload.key_missing` 处理。
  若产品要求「同一案件同一缺失项只提醒一次」，需要后端落状态（当前 `missing_list`
  是实时计算、不落状态）。

### Non-goals（本轮不做）
- 文书确认/导出流程本身（属独立需求，见 §3）
- 用量看板 / 计费工单页面
- 通知偏好设置（用户级开关、免打扰时段）
- 邮件 / 短信 / 企微推送渠道
- WebSocket 实时推送（沿用第一轮决策，30s 轮询足够）
- 通知聚合与分组

---

## 📚 数据来源 & 成员产出索引

- **析客（需求分析师）**：`prd-notification-readpath-2026-09-16.md` —— 第一轮 P0-15 完整 PRD
  （18 条需求池 NR-01~18、5 个用户故事、索引论证、轮询 vs WS 取舍、Mermaid 时序图、Q1~Q9）。
  本轮为其收口项，未新增 PRD 章节。
- **方向明（产品舵手）**：代码级勘察（9 种类型逐个反查写入点的事实基准）、
  4 个生产者的接线决策与实施、2 处前端连带缺陷定位与修复、31 条用例、多层验证、本交付记录。
- **瑞思 / 竞析 / 数析**：**本轮未做独立一手调研**。§2 的阈值与信噪比取舍均为
  **工程推理 + 代码事实**，已如实标注，**未伪造调研数据或指标**。

### 代码事实来源（改动点）
- 后端：`app/services/billing_service.py`（`_quota_warn_threshold` / `_maybe_warn_quota`）、
  `dispatch_service.py`（`_notify_accepted`）、`review_service.py`（`_notify_decided`）、
  `job_handlers.py`（`_notify_missing_key_evidence`）、`notification_service.py`（写入侧守卫）
- 前端：`packages/ui/src/components/AppLayout.tsx`（透传）、
  `apps/lawyer/lib/notificationRoutes.ts`（新建，路由唯一事实来源）、
  `apps/lawyer/app/(app)/layout.tsx`、`apps/lawyer/app/(app)/notifications/page.tsx`
- 用例：`backend/tests/test_notification_producers.py`（31 条）

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。
