# 第十三轮交付记录：通知实时推送（P0-15 收口项）

**日期**：2026-09-16
**类型**：功能规格书落地记录（PRD → 实施 → 验证）
**参与成员**：析客（需求分析师 / `requirement-analyst-2`，PRD 撰写与修订）、主理人方向明（编排 + 代码级核实 + 实施 + 验证）
**上游交付物**：`prd-notification-realtime-push-2026-09-16.md`（析客，17 条需求池 / 477 行）
**本轮范围**：该 PRD 的 **M1 + M2 + M3 + M4**（M5 为 P1，M6 属 P0-9）

---

## 📌 TL;DR（执行摘要）

- **核心目标**：把通知从「只写不读、只能轮询」打通到「**事务提交后秒级实时推送**」，并**顺手收口一处真实的跨客户越权**。
- **为什么是这一项**：4 个剩余 P0 中，P0-9 卡企微服务商资质、P0-16 需产品先对齐、P0-12 无支付 SDK——**实时推送是唯一未被外部依赖阻塞的 P0**，且它是 P0-9 的前置（企微闭环的「消息触达」环节依赖推送链路）。
- **关键决策**：**推送锚定在 `COMMIT` 之后而非 `flush` 之后**。`flush` 不提交，回滚会撤销通知行——若此时已推送，客户端会收到「您有新派单」却**点进一个不存在的案件**（「幽灵通知」）。改用 SQLAlchemy `after_commit` 投递 / `after_rollback` 丢弃。
- **安全收益**：`ws.py` 原判据 `tenant_id != ? AND client_user_id != ?` 等价于「同租户**或**是客户」→ 放行同租户任意用户；叠加「公开自助注册把所有 CLIENT 放进默认租户」，**任意注册客户可读任意其他客户的会话与消息**。四处不一致判据已收敛为单一纯函数。
- **下一步**：M5（Redis pub/sub 跨副本）与 P0-9（企微闭环）并行推进；Q1~Q10 需产品/安全/运维拍板（Q4、Q10 为本轮遗留开放项）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 进程内 `ConnectionManager` + `session.info` 排队 + `after_commit` 投递；WS 推送为主、轮询兜底 |
| 优先级 | P0（已完成 13/13） |
| 预期影响 | 通知触达从「≤30s 轮询」提升到「**实测 272ms**」；关闭一处**真实的跨客户会话/证据越权** |
| 资源需求 | 后端 3 个新模块 + 1 个端点重写 + 7 个指标；前端 1 个新模块 + 1 个组件改造；**零新增依赖** |
| 风险等级 | **中**——多副本部署下推送失效（进程内语义）；已三处声明 + 轮询兜底，跨副本修复（NP-13）排 P1 |

---

## 1. 交付范围与完成度

需求池共 **17 条**（P0 13 / P1 2 / P2 2）。本轮**全部 13 条 P0 已交付**。

| 编号 | 需求 | 优先级 | 状态 | 落地位置 |
|------|------|--------|------|---------|
| NP-01 | `ConnectionManager` 核心结构（`user_id → set[ws]`） | P0 | ✅ | `app/services/connection_manager.py` |
| NP-02 | 单用户连接上限 5 + 超限 4429 | P0 | ✅ | 同上（`DEFAULT_MAX_PER_USER`） |
| NP-03 | 断连清理 + 并发安全（**锁内取快照、锁外发送**） | P0 | ✅ | 同上（`_prune` / `asyncio.Lock`） |
| NP-04 | 进程内单实例语义 + 多副本限制**三处声明** | P0 | ✅ | 代码 docstring + PRD Non-goals + `docs/ops/notification-realtime.md` |
| NP-05 | `notify()` **提交后**双写 | P0 | ✅ | `app/services/notification_push.py` |
| NP-06 | 推送消息体契约（与 `NotificationOut` 一致） | P0 | ✅ | `notification_service._push_payload()` |
| NP-07 | 鉴权 WS 端点 `/ws/notifications` | P0 | ✅ | `app/api/v1/ws.py` |
| NP-08 | 心跳 30s / 空闲 90s → `close(1001)` | P0 | ✅ | 同上 |
| NP-09 | 会话归属判据单一来源 + `ws.py:63` 收口 | P0 | ✅ | `app/services/conversation_access.py` |
| NP-10 | `NotificationCenter` 接 WS、关闭轮询 | P0 | ✅（代码） | `packages/ui/src/components/NotificationCenter.tsx` |
| NP-11 | 重连退避 + `since_id` 补拉 + 降级回轮询 | P0 | ✅（代码） | 同上 + `packages/ui/src/lib/notificationRealtime.ts` |
| NP-12 | 指标 + 结构化日志 + 告警口径 | P0 | ✅ | `app/core/metrics.py` + 运维文档 |
| NP-17 | `GET /notifications` 新增 `since_id` 增量补拉 | P0 | ✅ | `notification_service.py` / `api/v1/notifications.py` |
| NP-13 | Redis pub/sub 跨副本推送 | P1 | ⏳ 未做 | 排 M5（第十三~十四轮） |
| NP-14 | 多端已读一致性收敛 | P1 | ⏳ 未做 | 排 M5 |
| NP-15 | 推送风暴保护 | P2 | ⏳ 未做 | 入停车场 |
| NP-16 | 用户级免打扰 / 类型开关 | P2 | ⏳ 未做 | 入停车场 |

> **`since_id` 的定位说明**：它是**第一轮 PRD 的 NR-15（离线补拉）**，当时列入待办但**从未实现**（`grep -rn "since_id" app/` 零命中）。PRD 初稿曾把它当作既成事实，主理人核实后回退给析客修正；析客独立复核后接受，将其**从 NR-15 拆出并提前到 P0**——因为实时推送一旦引入，断线补拉就从「离线优化」升级为「**推送链路正确性的必要组成**」（推送是 best-effort + 必然断线，没有补拉就会丢通知）。

---

## 2. 四个关键设计判断（均非显然，且都有反直觉成分）

### 判断一：推送必须在 `COMMIT` 之后，而不是 `flush` 之后

**PRD 初稿写的是「`flush` 后推送」，并附注「保证推的一定已落库」——这句是自相矛盾的。**

`flush` 只把 INSERT 发进**事务**，并未提交。调用方随后 `rollback`（校验失败 / 并发冲突 / 下游异常）时，该行**会被撤销**。若在 `flush` 后就推送，客户端会收到「您有新派单待接」，点进去**案件不存在**。

这类「**幽灵通知**」的特征是：**不报错、不留日志、只在特定失败路径出现，直接摧毁用户对通知的信任**——与项目历史上反复出现的「错误结果不报错」是同一类静默错误。

**落地**：`session.info` 排队（登记，不发送）→ `after_commit` 真正发送 → `after_rollback` 丢弃队列。
**钉死**：`test_rollback_does_not_push`（回滚零推送）+ `test_rollback_leaves_no_row`（行确实不在）——两条互补，缺一不可（前者只证明「没推」，不证明「该推的没推成」）。

### 判断二：推送结果指标必须**四态**，`send_to_user` 必须返回 `PushOutcome` 而非 `int`

「用户不在线」与「在线但每个连接发送都失败」——两者**送达数都是 0**，但一个是**常态**、一个是**事故**。

若只返回送达数（或只记 `delivered / no_connection` 两态），事故会被常态的量淹没，**告警永远不会触发**。这是本项目反复踩的同一类坑：**错误状态与正常状态在指标上不可区分**。

**落地**：`PushOutcome(delivered, attempted)` NamedTuple，附 `failed` / `had_connections` 派生属性；`notification_push_total{result}` 四态 `delivered / no_connection / failed / rolled_back`。

其中 **`rolled_back` 是「幽灵通知」防御的活体证据**——每 +1 就代表一条**本会推给不存在行的通知**被拦下。运维文档为此设了二级告警（`rolled_back` 突增）。

### 判断三：判据只收紧**无争议**的部分

`ws.py:63` 原判据是 `tenant_id != ? AND client_user_id != ?`，即「同租户 **或** 是客户」→ 放行同租户任意用户。REST 的详情 / 发消息两处同样只查 `tenant_id`——表现为「**列表藏起来、详情页直接给**」。

**无争议的越权**（端用户跨用户、跨租户）已关闭；但**律所内部可见性（同所律师之间能否互看会话）本轮刻意不动**——会话含客户咨询内容，受《律师法》保密义务约束，但**静默改变律所的协作模式，风险大于收益**。该问题升级为 PRD **Q4**，待产品 + 安全拍板。

> 这是本轮最重要的**范围纪律**：安全修复不等于「顺手把判据改严」。改动越界会把一个安全修复变成一次未经确认的产品变更。

### 判断四：进程内推送 + 三处同步声明，不引入 Redis

`ConnectionManager` 是**进程内单例**。多副本部署下，推送**只在持有该连接的副本内生效**（副本 B 调用 `notify()` 时推送 0 条）。

该限制**必须在三处显式声明**，否则运维会把它当成 bug 排查：
1. `connection_manager.py` docstring
2. PRD §7 Non-goals
3. **`docs/ops/notification-realtime.md`**（本轮新增，NP-04 的第三处声明点）

降级保障：前端轮询兜底，**最坏 ≤30s 也能到达**（通知不丢，只是慢）。跨副本编排见 NP-13（P1），复用既有 `REDIS_URL` 可选配置与 `rate_limit_backend.py` 的优雅降级范式。

---

## 3. 安全修复（真实越权，非理论）

**缺陷链**：
1. `ws.py` 的会话判据 = 「同租户 **或** 是客户本人」→ 放行同租户任意用户；
2. 自助注册（`auth.py`）把**所有** `Role.CLIENT` 固定到默认租户；
3. 1 + 2 ⇒ **任意注册客户可读任意其他客户的会话与消息**；
4. REST 详情 / 发消息两处独立只查 `tenant_id` ⇒ 列表页隐藏、详情页直接给出；
5. **`evidence.py` 里有一份同样的判据拷贝** ⇒ 同租户客户 B 可读客户 A 的**证据文件名、缺失证据清单、时间线**——而证据文件名本身就是高敏信息（「离婚协议书」「劳动仲裁申请书」「欠条」）。

**收敛方式**：四处不一致判据 → **一个纯函数** `can_access_conversation()`（`app/services/conversation_access.py`），WS 与 REST 三处共用。`case_owner_id` 由调用方预解析，使 8 类身份可在**无 DB** 条件下全部断言。

**不可枚举**：拒绝时 WS 关闭码 **4404**、REST 维持 **404 `CONVERSATION_NOT_FOUND`**，与「会话不存在」**完全一致**（每条通知都有 `ref_id` 指向业务数据，**存在性本身就是敏感信息**）。

---

## 4. 自己引入、又自己发现的三个缺陷

记录在案，因为它们比外部缺陷更有教育意义。

| # | 缺陷 | 性质 | 发现方式 | 修复 |
|---|------|------|---------|------|
| 1 | **`ENTERPRISE_ADMIN` 被误伤** | **功能回归**（非安全收紧） | 写「8 类身份穷尽用例」时 | `STAFF_ROLES` 补入 `ENTERPRISE_ADMIN` + 钉死用例 `test_enterprise_admin_is_not_collateral_damage` |
| 2 | `_auth_user` 里 `int(sub)` 裸转换 | 攻击者可让服务端报错 | 审阅畸形令牌路径 | 包 `try/except (TypeError, ValueError)` → `invalid_token` |
| 3 | `/metrics` 的 `render()` 手写收集器元组 | 静默失效 | 审阅新增指标导出路径 | 改为遍历实例属性（与 `reset()` 一致）+ 回归测试 |

**缺陷 1 的教训（最重要）**：`STAFF_ROLES` 初版只列了 `LAWYER / ASSISTANT / FIRM_ADMIN`，企业管理员落到「默认拒绝」分支。但**收敛前它被 tenant 检查放行**——所以这是**功能回归**，不是安全收紧。`ENTERPRISE_ADMIN` 是**企业侧租户级管理员**（产品线 B，类比律所侧的 `FIRM_ADMIN`），持有 `knowledge:*` / `contract:review` / `compliance:*`，明确属于本租户。

> **收敛判据时最容易犯的错，不是放太宽，而是误伤合法角色。** 只有「穷尽式身份矩阵」能抓到它——单测「越权被拒」全绿也发现不了。

**缺陷 3 的教训**：`render()` 与 `reset()` 是同一件事的两种写法，新增指标忘记加进 `render()` 的元组 = **永不导出**，不报错、无日志，只表现为「监控面板没数据」——排查会先怀疑采集侧。已加 `test_every_registered_collector_appears_in_render` 钉死。

---

## 5. 验证证据（全部实跑，非推断）

### 5.1 静态与单元

| 项目 | 命令 | 结果 |
|------|------|------|
| 静态检查 | `ruff check .` | ✅ `All checks passed!`（exit 0） |
| 推送单测 | `pytest tests/test_notification_push.py` | ✅ **25 通过** |
| 归属判据单测 | `pytest tests/test_conversation_access.py` | ✅ **21 通过** |
| `since_id` 单测 | `pytest tests/test_notification_since_id.py` | ✅ **12 通过** |
| 可观测性单测 | `pytest tests/test_observability.py` | ✅ **29 通过**（+3） |
| 证据越权补强 | `pytest tests/test_evidence_authz.py` | ✅ **13 通过**（+13，本轮新增） |
| **全量回归** | `pytest tests/ -q` | ✅ **347 通过 / 0 失败**（688.39s ≈ 11m28s，exit 0） |

**用例数对账（精确）**：273（第十二轮末）+ 18（推送新文件）+ 13（`evidence_authz` 补强）+ 7（推送指标）+ 21（归属判据）+ 12（`since_id`）+ 3（可观测性）= **347** ✓

### 5.2 端到端（真实 uvicorn + 真实 WebSocket over TCP）

`backend/verify_p0_15_push.py` —— **41/41 通过**。**不使用 `TestClient`**（`TestClient` 在另一个线程的 portal loop 里跑应用，`after_commit` 派发的任务无法投递到它），而是**起真服务、走真 TCP**。

| 阶段 | 断言 | 实测 |
|------|------|------|
| A 协议与鉴权 | 缺 token / 伪造 token → **1008**；首帧为 `connected`；快照与 REST **同一时刻**取值一致 | ✅ `ws=2 rest@connect=2` |
| B 真实业务链路 | 真 HTTP `POST /dispatches/{id}/accept` → 客户收 `CASE_ACCEPTED`、律师收 `DISPATCH_CREATED`、**同租户第三方零推送** | ✅ **时延 272ms** |
| B' 契约 | 帧可反序列化为 `NotificationOut` | ✅ |
| C 幽灵通知防御 | 回滚 → **零推送**，且行确实不存在 | ✅ |
| D 连接上限 | 第 6 个连接 → **4429** + 显式 `error` 帧，既有 5 个不受影响 | ✅ |
| E 保活 | `ping`/`pong` 维持连接；静默 → 空闲 **1001** | ✅ |
| F 指标 | 7 个指标全部暴露；**四态守恒**（四态之和 = 推送总数） | ✅ |
| G 索引 | 复合索引仍在 | ✅ |

读路径回归：`backend/verify_p0_15_notification.py` —— **52/52 通过**（原 43 + 新增 9 条 `since_id` 断言）。

### 5.3 前端与门禁

| 项目 | 结果 |
|------|------|
| `pnpm typecheck`（packages + admin/im/lawyer/web） | ✅ exit 0 |
| 四端 `next build` | ✅ web / lawyer / admin / im 全部 exit 0 |
| `pip-audit -r requirements.txt` | ✅ exit 0（`No known vulnerabilities found, 10 ignored`） |
| `pnpm audit --audit-level high` | ✅ exit 0（`No known vulnerabilities found`） |
| **新增依赖** | ✅ **零**（`packages/ui/package.json` 依赖项未变） |

### 5.4 ⚠️ 证据边界（必须如实声明）

**M3（前端）的验证强度低于 M1/M2/M4，不得夸大：**

- 本仓库**没有前端测试运行器**（`frontend/node_modules/.bin` 无 vitest / jest；`package.json` scripts 只有 `dev/build/start/lint/typecheck`）。因此 PRD 中 M3 的出口标准「WS 断开自动回退轮询」「重连补拉不重不漏」**没有可执行的自动化证据**。
- 前端结论仅由 **typecheck exit 0 + 四端构建 exit 0 + 代码审阅**支撑。**应用未实际运行、未做真机/浏览器验证。**
- 补拉「不重不漏」的**服务端半边**已有强证据（`test_notification_since_id.py` 12 条，含 25 条分页遍历「恰好一次」与并发写入下的稳定性）；**客户端半边**（合并去重、角标对账）**无自动化验证**。
- 同理，运维文档中的告警规则**已写好但未部署验证**（无 Prometheus 实例）。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 | 状态 |
|---|------|--------|--------|------|
| 1 | 后端：`ConnectionManager`（结构 / 上限 / 清理 / 快照锁粒度） | 后端 | 第十三轮 W1 | ✅ 完成 |
| 2 | 后端：`notify()` 提交后双写 + 推送契约 | 后端 | 第十三轮 W1 | ✅ 完成 |
| 3 | 后端：`/ws/notifications` + 心跳超时 | 后端 | 第十三轮 W1 | ✅ 完成 |
| 4 | 后端：会话归属判据单一来源 + `ws.py` 收口 | 后端 | 第十三轮 W1 | ✅ 完成 |
| 5 | 后端：`since_id` 增量补拉 | 后端 | 第十三轮 W1 | ✅ 完成 |
| 6 | 后端：7 个指标 + 结构化日志 | 后端 | 第十三轮 W2 | ✅ 完成 |
| 7 | 前端：`NotificationCenter` 接 WS、关闭轮询 | 前端 | 第十三轮 W2 | ✅ 完成（代码） |
| 8 | 前端：重连 + 补拉 + 降级回轮询 | 前端 | 第十三轮 W2 | ✅ 完成（代码） |
| 9 | 测试：连接管理器单测 + WS 端到端（41 条） | 后端 | 第十三轮 W2 | ✅ 完成 |
| 10 | 验证：ruff + 全量回归 + 四端构建 + 门禁 | 全栈 | 第十三轮 W2 | ✅ 完成 |
| 11 | 产品 / 安全 / 运维：确认 Q1~Q10 口径 | 产品 + 安全 + 运维 | 第十三轮启动前 | ⚠️ **未完成**（Q1/Q2/Q3/Q4 阻塞项仍开放） |
| 12 | 运维：新增 `docs/ops/notification-realtime.md` | 运维 + 后端 | 第十三轮 W2 | ✅ 完成（229 行） |
| 13 | 后端：Redis pub/sub 跨副本推送（P1 / NP-13） | 后端 | 第十三~十四轮 | ⏳ 待做（M5） |
| 14 | 预发：真实并发压测（校验 §6.1 连接数估算） | 运维 + 后端 | 第十三轮后 | ⏳ 待做 |
| 15 | **新增**：为 M3 补前端自动化测试能力（引入 vitest + 为 `notificationRealtime.ts` 写纯函数测试） | 前端 | 第十四轮 | ⏳ **本轮暴露的缺口** |
| 16 | **新增**：Q10 口径确认后决定 `since_id` + 分页同时传入是「静默覆盖」还是「显式 400」 | 产品 + 前端 | 联调前 | ⏳ 待做 |

---

## ⚠️ 待确认 / 假设 / Non-goals

### 本轮遗留的开放项（真正需要拍板）

| 编号 | 问题 | 当前实现选择 | 影响 |
|------|------|-------------|------|
| **Q4** | 同所律师之间是否可见彼此会话？ | **保持既有行为（可见）**，未收紧 | 安全 vs 律所协作模式的权衡，需产品 + 安全确认 |
| **Q10** | `since_id` 与 `page/page_size` 同时传入时的语义 | **静默覆盖：以 `since_id` 为准，忽略 `offset`** | 前端当前只传 `since_id` + `page_size`，不触发歧义；但契约未固化 |
| **Q1/Q2/Q3** | 部署形态（单副本？）/ 连接上限值 / 超限策略 | 按 PRD 建议取 5 / 4429 | Q1 若为多副本，NP-13 必须提前 |

### 本轮明确记录的设计选择（非缺陷，是取舍）

1. **前端 `realtime` prop 默认为 `true`，且未从 `AppShell` / `AppLayout` 透传。** 收益是四端零改动即获得实时推送；代价是**同一用户开四个应用会占 4 个连接**（上限 5），第 5 个起被 4429 拒绝并**永久降级轮询**。当前判定为可接受（通知在四端语义相同，降级后仍 ≤30s 送达）。若未来需要按端开关，透传一行即可。
2. **重连退避上限的实际值是 36000ms 而非 30000ms。** 实现为「先截断到 30s，再叠加 ±20% 抖动」；抖动是防**重连惊群**的必要项，把上限做在抖动之前会使上限失效。PRD 措辞「上限 30s」应理解为「基线上限」，最终值可达 36s。
3. **`mergeNotifications` 对 `is_read` 取单调 OR。** 否则一个「在 `POST /read` 提交前算出的」补拉响应会让该条**闪回未读**、角标跳动——本项目把角标可信度视为硬要求。
4. **后台隐藏期间的推送缓存在 ref 而非丢弃。** 隐藏时轮询被抑制，丢弃即永久丢失；且「隐藏 < 5min」不触发补拉，没有第二次机会。
5. **`token` 为 `null` 时安排重试而非直接连接。** 令牌为内存态（刷新后需 `restoreSession()`），此时连接必然收到 `1008`，而 `1008` 是终止态——会把该标签页**永久打进轮询降级**。
6. **`connected` / 补拉不受可见性抑制**（低频生命周期事件，不是推送流）。

### 关键假设（未经验证，标注推导）

- 假设门槛一「可试点版」为**单副本部署**——这是 Q1 的核心前提。
- 假设推送时效目标为秒级（P95 ≤ 1s）；实测单次业务链路 272ms（**单机单连接样本，非压测结论**）。
- 假设 9 种通知类型的语义与收件人不变（第二轮已冻结）。

### Non-goals（本轮明确不做）

- **跨副本推送编排**（Redis pub/sub）——排 P1 / M5；本轮**不引入 Redis 依赖**。
- **可靠投递保证**——推送是 best-effort；**落库才是可靠源**，轮询兜底。
- **多端强一致**——只承诺最终一致（NP-14 未做）。
- **离线推送 / IM 消息推送 / 免打扰 / 聚合富媒体**。
- **不改通知业务语义**——9 种类型与收件人不变。
- **读侧仅做向后兼容增强**（新增 `since_id`，未传时行为与第一轮完全一致）——此处已修正第一轮「读侧零改动」的措辞。

---

## 📚 数据来源 & 成员产出索引

### 本轮成员产出

- **析客（需求分析师 / `requirement-analyst-2`）**：`prd-notification-realtime-push-2026-09-16.md`（477 行 / 17 条需求池 NP-01~17 / 8 类身份判据表 / 2 张时序图 + 1 张判据流程图 / Q1~Q10 / R1~R9）。
  - **经主理人回退后自行复核并修订的 3 处**：① `since_id` 从「既成事实」修正为「本轮需新增」（新增 F17 事实基准 + NP-17 + §4.6）；② `notify()` 推送时机从 `flush` 后改为 `COMMIT` 后（NP-05 重写 + 新增风险 R9 + §6 延迟口径改为「提交→推送」）；③ §7 Non-goals 措辞修正（不再声称「读侧零改动」）。
  - 另接受主理人两项建议：NP-03 的**锁粒度**（锁内取快照、锁外发送）写入验收标准；新增 `docs/ops/notification-realtime.md` 作为 M4 交付物（行动清单第 12 项）。
- **主理人方向明**：代码级事实核实（PRD §3 基准）、后端实施、E2E 验证脚本、运维文档、本交付记录。
- **用户研究 / 竞品分析 / 数据分析**：**本轮未做独立一手调研**。所有数值均为**工程实测或量级估算**，已如实标注来源与边界，**未伪造调研或压测数据**。

### 本轮代码产出清单

| 类型 | 文件 | 说明 |
|------|------|------|
| 新增 | `backend/app/services/connection_manager.py` | 连接注册表 + `PushOutcome` |
| 新增 | `backend/app/services/notification_push.py` | 提交桥（`session.info` + `after_commit` / `after_rollback`） |
| 新增 | `backend/app/services/conversation_access.py` | 会话归属判据单一来源 |
| 重写 | `backend/app/api/v1/ws.py` | `/ws/notifications` + 关闭码 + 帧契约 |
| 改动 | `backend/app/services/notification_service.py` | `notify()` 登记推送；`since_id` 查询 |
| 改动 | `backend/app/api/v1/notifications.py` | `since_id` 查询参数（NP-17） |
| 改动 | `backend/app/core/metrics.py` | 7 个指标 + `render()` 自维护 |
| 改动 | `backend/app/main.py` | 启动装载推送桥；停机 `close_all` + 指标归零 |
| 改动 | `backend/app/api/v1/conversations.py` | 收敛至单一判据 |
| 改动 | `backend/app/api/v1/evidence.py` | 收敛至单一判据 |
| 新增测试 | `backend/tests/test_notification_push.py`（25） | 含 `PushOutcome` 区分与四态守恒 |
| 新增测试 | `backend/tests/test_conversation_access.py`（21） | 8 类身份矩阵 + 不可枚举 |
| 新增测试 | `backend/tests/test_notification_since_id.py`（12） | keyset 补拉不重不漏 |
| 补强测试 | `backend/tests/test_evidence_authz.py`（+13） | 证据端点同类越权 |
| 补强测试 | `backend/tests/test_observability.py`（+3） | 指标导出完整性 |
| 新增脚本 | `backend/verify_p0_15_push.py`（41 断言） | 真 uvicorn + 真 TCP WebSocket |
| 改动脚本 | `backend/verify_p0_15_notification.py`（52 断言） | 新增 9 条 `since_id` |
| 新增前端 | `frontend/packages/ui/src/lib/notificationRealtime.ts`（163 行） | 纯逻辑：URL 构造 / 退避 / 终态判定 / 合并去重 |
| 改动前端 | `frontend/packages/ui/src/components/NotificationCenter.tsx`（541→816 行） | WS 接入 + 降级 + 补拉 |
| 新增文档 | `docs/ops/notification-realtime.md`（229 行） | 运维手册（NP-04 第三处声明） |

### 关键实测数据

| 指标 | 值 | 来源 |
|------|-----|------|
| 业务动作 → 客户端收到推送 | **272ms** | `verify_p0_15_push.py` 阶段 B（单机单连接样本） |
| 单用户连接上限 | 5 | 代码常量 `DEFAULT_MAX_PER_USER` |
| 心跳 / 空闲超时 | 30s / 90s | 代码常量（**非环境变量**，运维文档已警示） |
| 全量回归用例数 | **347 通过 / 0 失败** | `pytest tests/ -q`，688.39s |
| 全量回归耗时 | ≈11 分 28 秒 | 同上（同一提交重复运行耗时在 11m28s–14m09s 区间波动） |

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。
> **M3（前端）验证强度低于其余里程碑**，证据边界见 §5.4——请在依赖「WS 断开自动降级」这一行为前先做人工验证。
