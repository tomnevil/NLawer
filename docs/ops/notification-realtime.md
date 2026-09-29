# 通知实时推送 · 运维手册

**适用版本**：第十三轮（P0-15 收口项）
**对应 PRD**：`deliverables/product-strategy/prd-notification-realtime-push-2026-09-16.md`（NP-04、NP-12）
**代码位置**：`backend/app/api/v1/ws.py`、`backend/app/services/connection_manager.py`、`backend/app/services/notification_push.py`

---

## 1. 这套东西是干什么的

通知在写入数据库后，通过 WebSocket **实时推**给在线的用户，替代「只靠前端轮询」。
轮询**没有删除**——它降级为兜底通道（见 §5）。

```
业务事务（派单/接单/复核/计费…）
   └─ notify()  写 notifications 行 + flush
        └─ 登记到 session.info（不发送）
   └─ COMMIT ─────────────► after_commit 事件
                              └─ 取队列 → 投递协程 → ConnectionManager
                                   └─ 遍历该 user_id 的连接 → send_json
   └─ ROLLBACK ───────────► after_rollback 事件
                              └─ 丢弃队列（该通知从未落库，推出去就是幽灵通知）
```

**唯一触发点是事务提交**，不是 `flush`。这是刻意的，见 §6 故障模式 F1。

---

## 2. ⚠️ 多副本部署的推送限制（最重要的一条）

**`ConnectionManager` 是进程内单例。连接挂在哪个副本，推送就只能由哪个副本发出。**

| 场景 | 结果 |
|------|------|
| 单副本（当前门槛一：3 家律所灰度） | 推送 100% 送达在线用户 |
| 2 副本，用户连接在副本 A，通知由副本 A 写入 | 推送送达 ✅ |
| 2 副本，用户连接在副本 A，通知由副本 B 写入 | **副本 B 推送 0 条**，副本 A 不感知 ❌ |

**丢失的只是「实时性」，不是「通知本身」**：
通知行已落库，用户在 ≤30s 的轮询周期内、或重连时通过 `since_id` 增量补拉，仍能看到。

### 运维动作

- **不要把 `--workers` 调到 >1**，除非已确认下列之一：
  - 只有单个副本接收写流量（读写分离 / 主副本路由），或
  - 已按 NP-13（P1）引入 Redis pub/sub 跨进程广播。
- **判断当前是否命中该限制**：比较 `nlaw_notification_push_total{result="no_connection"}`
  的增速与「实际在线用户数」。若在线用户很多但 `no_connection` 占比异常高，
  说明通知被写到了没有连接的副本上。
- **该限制的声明在三处同步维护**：`connection_manager.py` 模块 docstring、
  PRD 的 Non-goals、本文件。**改动其中一处必须同步另两处。**

---

## 3. 连接参数与上限

| 参数 | 值 | 位置 | 说明 |
|------|-----|------|------|
| 单用户连接上限 | **5** | `connection_manager.DEFAULT_MAX_PER_USER` | 覆盖「手机 + 电脑 + 重连过渡期」；超出**拒绝**而非静默堆积 |
| 心跳间隔 | **30s** | `ws.HEARTBEAT_INTERVAL` | NAT / 代理常在 60s 左右回收空闲连接，留一倍余量 |
| 空闲超时 | **90s** | `ws.IDLE_TIMEOUT` | 3 倍心跳；单次心跳丢失（移动网络抖动）不应直接断连 |
| 优雅停机关闭码 | **1001** | `manager.close_all(code=1001)` | 客户端据此**主动重连**，而非当作错误反复重试 |

> ⚠️ **以上是代码常量，不是环境变量**。调整需改代码并重新部署。
> 若运维需要热调，需另开工单改造（当前无此能力，不要尝试用 env 覆盖）。

### WebSocket 关闭码速查

| 码 | 含义 | 客户端应有的行为 |
|----|------|------------------|
| `1008` | 鉴权失败（缺 token / token 无效 / 用户不存在） | **不要重试**，先重新登录 |
| `1001` | 服务端下线，或连接空闲超时被回收 | **主动重连**（带退避） |
| `4429` | 该用户连接数已达上限（5） | **不要重试**，提示用户关闭其他页面 |
| `4404` | 会话不存在或无权访问（**仅** `/ws/conversations/{id}`） | 不要重试 |

### 帧类型

| 方向 | `type` | 说明 |
|------|--------|------|
| 服务端 → 客户端 | `connected` | 建连后第一帧，携带 `unread_total` / `latest_id` 快照 |
| 服务端 → 客户端 | `notification` | 通知推送，`data` 与 REST `NotificationOut` **同形** |
| 服务端 → 客户端 | `ping` | 心跳（每 30s，仅在有连接空闲时发） |
| 服务端 → 客户端 | `error` | 建连失败原因（随后即关闭） |
| 客户端 → 服务端 | `pong` | 心跳应答（**唯一**需要客户端做的事） |

> 该通道**只推不收**。客户端发任何其他内容都不解析、不报错——接收侧的唯一职责是保活。

---

## 4. 监控与告警

指标经 `/metrics` 以 Prometheus 文本格式暴露。

| 指标 | 类型 | 标签 | 告警建议 |
|------|------|------|----------|
| `nlaw_ws_connections_active` | Gauge | `endpoint` | 接近 `实例数 × 预期在线用户` 上限时关注 fd / 内存 |
| `nlaw_ws_connections_total` | Counter | `endpoint` | 突增 → 客户端重连风暴 |
| `nlaw_ws_connection_rejected_total` | Counter | `reason` | `over_limit` 持续 >0 → 前端重连有 bug（未关旧连接） |
| `nlaw_ws_auth_failed_total` | Counter | `reason` | `missing_token` 突增 → 前端漏带 token；`invalid_token` 突增 → 令牌过期/攻击 |
| `nlaw_ws_heartbeat_timeout_total` | Counter | — | 持续增长 → 移动网络质量差，属正常；突增 → 网络故障 |
| `nlaw_notification_push_total` | Counter | `result` | **见下方首要告警** |
| `nlaw_notification_push_latency_milliseconds` | Histogram | — | `histogram_quantile(0.99, ...) > 2000ms` 告警 |

### 🔔 首要告警：推送失败率

```promql
# 5 分钟内推送失败率 > 5% 即告警
sum(rate(nlaw_notification_push_total{result="failed"}[5m]))
  /
sum(rate(nlaw_notification_push_total[5m])) > 0.05
```

**为什么 `failed` 必须与 `no_connection` 分开**：两者都表现为「用户没收到」，
但 `no_connection`（用户不在线）是**常态、量大**，`failed`（有连接但发送失败）
是**事故**。合并成一个值会让事故被常态淹没，告警永不触发。

### 次要告警：幽灵通知拦截率

```promql
# rolled_back 突增说明业务事务回滚率异常（不是推送本身的问题）
sum(rate(nlaw_notification_push_total{result="rolled_back"}[5m])) > 1
```

`rolled_back` 是**幽灵通知防线的活体证据**：每 +1 代表「一条本会被推出去、
但用户点开会发现不存在的通知」被拦下了。它本身是**好事**（防线在工作），
但突增意味着上游业务回滚率异常，应去查业务侧。

### 推送实时性

```promql
histogram_quantile(0.99, sum(rate(nlaw_notification_push_latency_milliseconds_bucket[5m])) by (le))
```

时延口径 = **通知落库（flush）→ 推送完成**。因为 `flush` 与 `commit` 相邻，
该值即「落库到送达」的端到端时延。实测同进程内为**亚毫秒到数十毫秒**量级。

---

## 5. 降级行为（推送失败时会发生什么）

**推送是旁路。任何推送失败都不会影响业务流程。**

| 故障 | 系统行为 | 用户感知 |
|------|----------|----------|
| 用户不在线 | 推送 0 条，`result="no_connection"` | 无。下次轮询 / 重连补拉时看到 |
| 连接已死（发送抛异常） | 该连接**就地摘除**，不抛给业务 | 无。前端重连后补拉 |
| WebSocket 服务不可用 | 前端轮询兜底（`pollMs=30000`） | **最坏 30s 内**看到通知 |
| 进程重启 | `close_all(1001)` 通知客户端重连 | 短暂重连后恢复 |
| 多副本写到了无连接的副本 | 推送 0 条（见 §2） | ≤30s 轮询周期内看到 |

**关键保障**：`notify()` 先落库、再推送。所以**通知永不丢失**，
最坏情况是「晚 ≤30s 看到」，而不是「看不到」。

---

## 6. 故障模式排查表

### F1｜用户点开通知，提示「案件不存在」

**这是幽灵通知，属于严重缺陷，应立即上报。**

- 含义：推送发生在事务提交**之前**，通知行随事务回滚了，但推送已经到达。
- 排查：检查是否有人在 `notify()` 里改成「`flush` 后直接发送」，
  或新增了绕过 `notification_push.queue_push()` 的推送路径。
- 防线位置：`notification_push._push_after_commit`（唯一触发点）
  + `_discard_after_rollback`（丢弃）。
- 佐证指标：`nlaw_notification_push_total{result="rolled_back"}` 应有计数；
  若该值恒为 0 而业务有回滚，说明**防线没接上**（钩子未注册）。

### F2｜前端收不到推送，但 `delivered` 在涨

- 检查前端是否**连上了正确的端点**：`/api/v1/ws/notifications`（不是 `/ws/conversations/{id}`）。
- 检查前端是否在处理 `type == "notification"` 的帧。
- 检查是否**同一用户已有 5 个连接**（`4429`），新连接被拒后前端未提示。

### F3｜`no_connection` 占比异常高，但用户声称在线

- 见 §2：很可能命中多副本限制。
- 或：用户连接挂在另一个副本 / 已被空闲超时回收（`ws_heartbeat_timeout_total` 会涨）。

### F4｜连接数持续增长不回落

- 查 `nlaw_ws_connections_active`。
- 若增长但 `ws_heartbeat_timeout_total` 不涨，说明**空闲回收没生效**——
  检查是否有代码绕过了 `/ws/notifications` 直接注册到 `ConnectionManager`，
  导致 `finally: unregister` 未被挂上。
- 单用户连接数异常高（>5 不可能，被上限拦住）→ 查是否有大量不同用户。

### F5｜服务端日志报「无运行中的事件循环，跳过 N 条实时推送」

- 发生在同步上下文（Alembic 迁移、同步脚本）调用 `notify()`。
- **不影响数据**：通知已落库，推送被跳过。这些条数计入 `result="failed"`。
- 生产请求路径不应出现此日志；若出现，说明有同步 Session 在跑 `notify()`。

---

## 7. 关键日志

| 日志 | 级别 | 含义 |
|------|------|------|
| `通知实时推送已启用（单用户连接上限 N…）` | INFO | 启动时确认推送桥已装载 |
| `通知连接建立：user_id=… 该用户连接数=… 在线用户=…` | INFO | 建连（含连接数，便于发现泄漏） |
| `通知连接空闲超时回收：user_id=… 静默 Ns` | INFO | 「假活」连接被回收 |
| `通知连接被拒（超单用户上限 N）：user_id=…` | WARNING | 达到 4429 的拒绝 |
| `推送失败：user_id=… 在线连接 N 个全部发送失败` | WARNING | **事故信号**，对应 `failed` |
| `通知实时推送失败（已忽略）：user_id=… err=…` | WARNING | 旁路失败，已消化 |
| `事务回滚，丢弃 N 条实时推送` | DEBUG | 幽灵通知防线生效 |

**越权拒绝**在 `/ws/conversations/{id}` 记为
`会话访问被拒：user_id=… role=… conversation_id=…`，
且**响应体一律为「会话不存在」**（404 / 4404）——
服务端日志能区分「不存在」与「存在但越权」，客户端不能（防枚举）。

---

## 8. 变更与回滚

- **关闭实时推送**（不改代码）：前端停止建连即可，轮询继续工作。
  后端推送会退化为 `no_connection`，无副作用。
- **回滚本轮**：`/ws/notifications` 端点删除后前端建连失败 → 自动走轮询。
  `notification_push` 的事件钩子随模块导入注册，未被导入则完全不生效。
- **不要**在保留 `ConnectionManager` 的同时移除 `after_commit` 钩子——
  那样 `session.info` 里的待推送队列会一直累积到会话结束（内存缓慢增长）。
  若要禁用推送，应同时不调用 `queue_push`。

---

> 本手册与代码同步维护。**改动连接上限、心跳参数、多副本策略时，
> 必须同步更新本文件、`connection_manager.py` 的模块 docstring，以及 PRD 的 Non-goals。**
