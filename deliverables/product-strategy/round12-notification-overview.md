# 第十二轮 · P0-15 通知系统读路径 · 交付记录

**日期**：2026-09-16
**类型**：功能规格书 / 实施记录（工作流 1）
**参与成员**：析客（需求分析师，PRD）· 方向明（产品舵手，编排与实施）
**配套文档**：`prd-notification-readpath-2026-09-16.md`（本轮的完整需求规格）

---

## 📌 TL;DR（执行摘要）

- **修的是什么**：通知系统「**只写不读**」——写入侧自项目初期完备（9 种类型 / 5 个业务写入点），但**读取侧为零**：无 API、无 Schema、`is_read` 与 `read_at` 全库无赋值点、前端铃铛是**没有 onClick 的死按钮且红点硬编码常亮**。通知写进库即成黑洞。
- **做了什么**：补齐**读路径全链**——后端 6 个端点（列表 / 未读数 / 详情 / 单条已读 / 批量已读 / 全部已读）+ 复合索引 + Alembic 迁移；前端通知中心（桌面下拉面板 / 移动 BottomSheet 双形态）+ 铃铛角标打通 + 律师端 `/notifications` 页。
- **顺手堵了 3 个缺陷**：① 批量 UPDATE 用 `synchronize_session=False` 导致会话内读到陈旧 `is_read`（静默错误）；② 计费侧 `user_id or 0` 产生**无人可读的孤儿通知**；③ 铃铛常亮红点让用户学会忽略通知。
- **验证**：ruff 全绿 · 模块单测 **23/23** · 全量后端回归 **242 passed / 0 failed** · 端到端 HTTP **43/43** · 四端构建 exit 0 · 前后端安全门禁 exit 0。
- **下一步**：P0-9（IM 真实打通）的前置已就绪，可开第十三轮。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| **推荐方案** | 6 个只读/幂等端点 + `(tenant_id, user_id, is_read, type)` 与 `(tenant_id, user_id, id)` 双复合索引 + 前端双形态通知中心；实时性**本轮用轮询**（30s + 焦点触发 + 指数退避），WS 延后至 P1 |
| **优先级** | **P0**（门槛一上线前置，且为 P0-9 前置） |
| **预期影响** | 责任链触达由「**不可用**」→「**可用**」；未读积压变为可观测；企微闭环清障 |
| **资源需求** | 后端 1 人日 / 前端 1 人日（实际：本环境单轮完成）；**零外部依赖、零新增依赖** |
| **风险等级** | **低**——技术方案成熟；头号风险「用户级越权」已用 404 语义 + 双层归属约束 + 端到端用例封死 |

---

## 1. 缺口定位（事实基准）

| 环节 | 修复前 | 修复后 |
|------|--------|--------|
| 写入 | ✅ `notify()` 在派单 / 归档 / 计费×2 / 复核共 5 处写入 | ✅ 不变，**新增「无接收人则拒写」守卫** |
| 列表 API | ❌ 不存在 | ✅ `GET /api/v1/notifications`（分页 + `is_read` + `type` 筛选） |
| 未读数 API | ❌ 不存在 | ✅ `GET /api/v1/notifications/unread-count`（总数 + 分组 + `latest_id`） |
| 详情 API | ❌ 不存在 | ✅ `GET /api/v1/notifications/{id}` |
| 标记已读 | ❌ `is_read` / `read_at` **全库无赋值点** | ✅ 单条 / 批量 / 全部，**均幂等** |
| 索引 | ⚠️ 仅 `tenant_id`、`user_id` 单列索引 | ✅ 两个复合索引，`EXPLAIN` 实测命中 |
| 前端铃铛 | ❌ 无 `onClick` 的死按钮 + **硬编码常亮红点** | ✅ 真实未读数驱动，0 未读时**不渲染红点** |
| 前端列表 | ❌ 不存在 | ✅ 桌面下拉面板 / 移动 BottomSheet + 律师端独立页 |

---

## 2. 安全设计（本轮头号验收项）

通知是**用户级**资源，不是租户级资源。仅按 `tenant_id` 过滤会让同一律所内律师甲读到律师乙的通知（含案件标题、客户信息、复核结论）。采取三重防护：

1. **归属约束单一来源**：所有读侧查询经 `notification_service._owned(user_id, tenant_id)` 构造，**不提供只按 tenant 过滤的读接口**——从 API 形状上杜绝误用。
2. **越权一律 404，不返回 403**：通知 `id` 为全局自增，403 会告诉攻击者「该 id 存在，只是你没权限」，可用于枚举系统通知总量与他人活跃度。**对无权访问的资源一律表现得「如同不存在」**（OWASP IDOR 防护惯例）。代价是 404 会掩盖真实权限问题——以服务端日志区分「不存在」与「存在但越权」来缓解。
3. **`user_id` 只取自 JWT**：由 `get_tenant_context` 派生，**不接受任何请求参数传入**。端到端用例 E.1 专门验证「用查询参数指定 `user_id` 无效」。

---

## 3. 三个顺带修掉的缺陷

### 3.1 批量 UPDATE 的静默陈旧读（`synchronize_session`）

批量标记已读走 Core 层 `update()`，ORM 身份映射不会自动感知行变化。初版写的是 `synchronize_session=False`，导致**同一会话内「先批量标记、再读列表」会拿到陈旧的 `is_read=0`**——不报错、不抛异常，只在特定调用顺序下出现。

> **发现方式**：单元测试 `test_filter_by_is_read` 断言失败。最初怀疑是测试写错，做了最小复现（直接执行 UPDATE 后检查会话对象属性）才确认是生产代码问题。
> **修复**：`synchronize_session="fetch"`（代价是一次有界 SELECT ≤200 行），并补 `test_mark_read_batch_is_visible_in_same_session` 回归用例锁死。

### 3.2 无人可读的孤儿通知（PRD Q8）

计费侧调用点写的是 `user_id=user_id or 0`——当调用方没有具体用户上下文时，`None` 被兜底成哨兵值 `0`，而 `0` **不是任何真实用户的 id**。在读路径一律按 `user_id == 当前登录用户` 过滤的前提下，这类通知**对任何人都不可见**：不是「稍后可见」，而是永久黑洞——既占存储，又让「未读积压」统计失真。

**修复**：写入侧加守卫，`user_id` 为空或 0 时**拒写并告警**；计费侧两处调用点去掉 `or 0`。真正需要「租户级公告」时应新增独立广播模型，而不是复用用户级通知塞哨兵 id。

### 3.3 让用户学会忽略的红点

原铃铛红点是硬编码常亮 `<span>`。**常亮的告警等于没有告警**——用户很快学会无视它。现在角标由真实未读数驱动，`0` 时完全不渲染。

---

## 4. 关键设计取舍

| 取舍 | 选择 | 理由 |
|------|------|------|
| 实时性 | **轮询**（30s + 焦点触发） | 通知响应窗口是**分钟级**（派单/复核），不需秒级；轮询天然兼容多实例（无状态）、弱网健壮，成本约为长连接的 5%。未读数端点设计为廉价聚合（走覆盖索引），未来接 WS 时**读侧契约不变**，仅替换拉取触发源 |
| 计数方式 | `count_bounded`（CAP=200） | 沿用第十轮结论：`COUNT` 无法短路，是真正的性能瓶颈；超限时前端显示「200+」 |
| 排序 | `id DESC` 而非 `created_at DESC` | 归档会同时通知客户与承办律师，两条 `created_at` 可能同秒，排序不稳定会导致**翻页重复/漏项** |
| 越权语义 | 404 | 见 §2 |
| 批量上限 | 200（非 PRD 建议的 500） | 与 `COUNT_CAP` 同口径，避免构造无界 `IN (...)` |
| 通知中心开关 | **显式传入才渲染铃铛** | 骨架层默认发起网络轮询会让未登录页产生无意义 401，且「页面为什么在发请求」难追查 |
| 路由映射位置 | 各端自定义（`onNavigate` 回调） | 四端路由表不同；组件库不假设 URL 结构 |
| 审计留痕 | 读操作**不留痕** | 每次点铃铛都落审计会让 `audit_logs` 被噪音淹没，反而降低写操作的可见性；通知行本身（含 `read_at`）即记录 |

---

## 5. 验证证据（多层，全部实跑）

| 层 | 命令 / 方式 | 结果 |
|----|------------|------|
| 语法 | `ast.parse` 全量新增文件 | 通过 |
| 静态 | `ruff check app/ tests/ verify_p0_15_notification.py alembic/` | **All checks passed** |
| 契约 | `app.openapi()` 断言 6 个端点 | 6/6 齐全 |
| 单元 | `pytest tests/test_notification_api.py` | **23 passed** |
| 回归 | `pytest tests/`（全量） | **242 passed / 0 failed**（10m36s） |
| 端到端 | `verify_p0_15_notification.py`（TestClient 真实 HTTP + Cookie 罐） | **43 通过 / 0 失败** |
| 索引 | `EXPLAIN QUERY PLAN` | `SEARCH notifications USING INDEX ix_notifications_tenant_user_id`（无全表扫描） |
| 前端类型 | `tsc -p tsconfig.packages.json --noEmit` | exit 0 |
| 前端构建 | 四端 `next build` | web / lawyer / admin / im **全部 exit 0**（lawyer 端新增 `/notifications` 4.07 kB） |
| 门禁·后端 | `pip-audit -r requirements.txt` + allowlist | `No known vulnerabilities found, 10 ignored`，exit 0 |
| 门禁·前端 | `pnpm audit --audit-level high` | `No known vulnerabilities found`，exit 0 |

> 端到端脚本覆盖：未认证 401 · 列表归属 · 未读数与分组 · **同租户跨用户 404** · **跨租户 404** · 越权未改动他人数据 · `user_id` 不可被请求参数覆盖 · 单条已读幂等（`read_at` 不被刷新）· 批量跳过他人 id · 全部已读不波及其他用户 · `is_read` 序列化为真布尔 · 未知 `type` → 400 · 索引落地与命中。

---

## 6. 交付物清单

**后端（新增 5 / 修改 4）**

| 文件 | 说明 |
|------|------|
| `app/schemas/notification.py` | 新增：`NotificationOut` / `UnreadCountOut` / `ReadBatchIn` / `ReadResultOut` |
| `app/api/v1/notifications.py` | 新增：6 个端点 |
| `app/services/notification_service.py` | 修改：新增读侧 6 个函数 + 无接收人守卫 + `synchronize_session` 修复 |
| `app/models/notification.py` | 修改：两个复合索引 |
| `alembic/versions/c4a91f7e2b83_notification_read_indexes.py` | 新增：索引迁移（可回滚） |
| `app/api/v1/__init__.py` | 修改：注册路由 |
| `app/core/errors.py` | 修改：新增 `NOTIFICATION_NOT_FOUND` |
| `app/services/billing_service.py` | 修改：去掉 `user_id or 0` 哨兵 |
| `tests/test_notification_api.py` | 新增：23 个用例 |
| `verify_p0_15_notification.py` | 新增：43 项端到端断言 |

**前端（新增 2 / 修改 3）**

| 文件 | 说明 |
|------|------|
| `packages/ui/src/components/NotificationCenter.tsx` | 新增：双形态通知中心（自取未读数、轮询 + 退避 + 可见性暂停、乐观更新） |
| `apps/lawyer/app/notifications/page.tsx` | 新增：律师端通知页（筛选 / 分页 / 跳转） |
| `packages/ui/src/components/AppShell.tsx` | 修改：**删除死按钮与硬编码红点**，接入通知中心 |
| `packages/ui/src/index.ts` | 修改：导出通知中心与工具函数 |
| `apps/web/app/components-preview/page.tsx` | 修改：预览页启用通知中心 |

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 | 状态 |
|---|------|--------|--------|------|
| 1 | 后端 Schema + 6 端点 + 路由注册 | 后端 | 第十二轮 W1 | ✅ 完成 |
| 2 | 复合索引 + Alembic 迁移 + `EXPLAIN` 验证 | 后端 | 第十二轮 W1 | ✅ 完成 |
| 3 | 越权语义（404）+ 单元测试 | 后端 | 第十二轮 W1 | ✅ 完成 |
| 4 | 前端通知中心双形态 + 角标打通 + 跳转 | 前端 | 第十二轮 W1–W2 | ✅ 完成 |
| 5 | 轮询 + 焦点触发 + 退避 | 前端 | 第十二轮 W2 | ✅ 完成 |
| 6 | 端到端验证 + 全量回归 + 四端构建 + 门禁 | 全栈 | 第十二轮 W2 | ✅ 完成 |
| 7 | 前端埋点事件接入（PRD §5.3） | 前端 | 第十三轮 | ⏳ 待办 |
| 8 | 离线补拉 `since_id` + 离线缓存（NR-15） | 全栈 | 第十三轮 | ⏳ 待办 |
| 9 | 保留策略 + 审计留痕（NR-16/17） | 后端 | 第十三轮 | ⏳ 待办 |
| 10 | WS 推送（P1，与 P0-9 同期） | 全栈 | 第十三~十四轮 | ⏳ 待办 |
| 11 | 补齐 5 种「尚无写入点」的通知类型 | 后端 | 待排期 | ⏳ 待办 |
| 12 | 设计：桌面面板 / 移动 BottomSheet 视觉稿 | 设计 | 第十三轮 | ⏳ 待办 |

---

## ⚠️ 待确认 / 假设 / Non-goals

### 假设
- 通知**不需要秒级时效**（30s 轮询足够），故本轮不做 WS。
- 用户级隔离以 `user_id == 当前登录用户` 为**唯一权威判据**，不可仅靠 `tenant_id`。
- `PaginationParams` / `Page.build` / `count_bounded`（CAP=200）契约不变。

### 待产品/安全拍板（沿用 PRD §10）
- **Q2**：已读通知保留期（建议 90 天，未读永不清理）
- **Q3**：离线缓存是否落盘（建议仅缓存非敏感元数据、登出即清）
- **Q5**：多端已读一致性 SLA（建议最终一致，不承诺强一致）
- **Q6**：未读数是否按 `type` 分组展示（**后端已提供 `by_type`，前端本轮仅用总数**——若产品决定要分组 Tab，无需后端改动）

### 已在本轮解决的 PRD 待确认项
- **Q1（404 vs 403）**：采用 **404**，已实现并有用例覆盖。
- **Q8（孤儿通知）**：已修复（写入侧守卫 + 计费调用点）。
- **Q9（未产生类型的 UI 兜底）**：前端 `TYPE_META` 已对未知类型做通用渲染兜底，不会出现空白条目。

### Non-goals（本轮不做）
邮件 / 短信 / 微信 / 企微推送渠道 · 通知偏好设置中心 · 分组聚合 · 多端已读强一致 · WebSocket 实时推送 · 通知删除 / 撤回 / 标记未读 · 富媒体通知 · 全文搜索 · 移动 App 推送（APNs/FCM）· @提及与私信。

---

## 📚 数据来源 & 成员产出索引

- **析客（需求分析师）**：`prd-notification-readpath-2026-09-16.md` —— P0-15 完整 PRD（18 条需求池 NR-01~18、5 个用户故事、索引必要性论证、轮询 vs WS 取舍、Mermaid 时序图、Q1~Q9 待确认项）。
- **方向明（产品舵手）**：代码级勘察（缺口定位的事实基准）、编排、实施与多层验证；本交付记录。
- **瑞思（用户研究员）/ 竞析（竞品分析师）/ 数析（数据分析师）**：**本轮未做独立一手调研**，PRD §3/§4/§5 已如实标注为「推导 / 经验性对比 / 可观测性设计」，**未伪造调研数据**。
- **代码事实来源**：`backend/app/models/notification.py`、`backend/app/services/notification_service.py`、`backend/app/models/enums.py:229`、`backend/app/core/pagination.py`、`backend/app/core/deps.py`、`frontend/packages/ui/src/components/AppShell.tsx`（修复前 522–530 行）。

---

## 附：未纳入本轮的发现（留给后续）

1. **`NotificationType` 9 种枚举中 5 种尚无写入点**（`CASE_ACCEPTED` / `EVIDENCE_MISSING` / `REVIEW_DECIDED` / `DOCUMENT_CONFIRMED` / `QUOTA_WARNING`）——读路径 UI 已做兜底，但对应业务节点的触达能力实际缺失，需另行排期。
   > **已被第二轮（`round12b-notification-producers-overview-2026-09-16.md`）收口**：
   > 其中 4 种已接线并有用例覆盖；`DOCUMENT_CONFIRMED` 经查证属**独立业务缺口**
   > （文书确认/导出流程未实现，`DocumentStatus.CONFIRMED` 全库零引用），故意不接线。
2. **`AppShell` 尚未被业务页面使用**（仅 `components-preview`）——通知中心因此目前只在预览页可见，四端页面接 Shell 属阶段三工作。
3. **无自动化视觉回归**——本环境不支持浏览器自动化（Windows），通知中心双形态的视觉与手势建议在 macOS/Linux CI 建 Playwright 截图基线。

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。
