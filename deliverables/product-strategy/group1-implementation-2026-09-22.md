# 组一 🔴 实现进展（2026-09-22）

**日期**：2026-09-22
**类型**：实现记录（组一 12 项待裁定，产品已「整体采用推荐默认」）
**参与成员**：析客（裁定选项来源）· 路径（排期）· 主理人方向明（汇编与实现）
**前置**：组二 🟢 已收口（路由覆盖棘轮 11→0）；决策包见 `decisions-pending-group1-2026-09-21.md`。

---

## 📌 TL;DR（执行摘要）

- 产品已拍板：**整体采用推荐默认**。本轮把 12 项里**能落地的 8 项**做完，其中 5 项**逐条故障注入自证**。
- 已完成：**Q-P**（`billing:write`）· **Q-AA**（净负营收拒收）· **Q-V**（复核 422）· **Q-U**（会话归属防伪造）· **Q-R**（证据权限码门控）· **Q-S**（卷宗下载按案件归属）· **Q-M**（admin 手动调额度端点）· **Q-H**（失败任务可见入口）。
- 每项都走「改代码 → 更新/新建判据 → （关键防御）注入故障证明判据识别缺陷 → 还原 → 复跑转绿」。
- **仅剩 Q-G**（向量读侧接 Retriever，Postgres-only）—— 本机只有 SQLite，**无法验证**，故不动。
- Q-E / Q-W / Q-F 按推荐默认**只文档化不改码**（单独立项 / 接受现状 / 棘轮已卡住）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 决策包 12 项的「推荐」列（产品已整体采纳） |
| 优先级 | Q-P / Q-R / Q-AA / Q-S 影响正确性与合规，已先做 |
| 预期影响 | 权限矩阵与错误语义收敛一致；消除「只读岗能扣费」「客户伪造归属」「证据端点无门控」「同租户可下他人卷宗」「净负营收污染财报」「失败任务无人可见」 |
| 资源需求 | 已耗 1 轮（8 项 + 5 次注入 + 全回归）；剩 Q-G 需 Postgres 环境 |
| 风险等级 | 中（改的是共享的 `rbac.py` / `errors.py` / `files.py`，故每轮跑全回归兜底） |

---

## 1. 已完成（8 项）

### 1.1 Q-P —— `billing:write` 权限码落地 ✅
- **改动**：`rbac.py` 给 `FIRM_ADMIN` / `ENTERPRISE_ADMIN` 增 `billing:write`；`billing.py` `consume` 门控由 `billing:read` 升为 `billing:write`。
- **判据**：`B6` / `M3` 由「登记现状（无 billing:write）」翻转为「向前看」（`billing:write` 存在、**仅**授予两个管理员角色、consume 必须挂 `billing:write`、禁止残留 `billing:read`）。
- **验证**：计费两文件 **15 passed**。

### 1.2 Q-AA —— 收入预测拒收净零/净负 ✅
- **改动**：`BillingService.project_revenue` 合计 `total <= 0` ⇒ `BadRequestError(400, VALIDATION_ERROR)`。单项可为负（退款/冲销），只判**净合计**。
- **判据**：`M7` 翻转为「净负 ⇒ 4xx 且不返回 total；单点为负但净合计为正 ⇒ 200」。
- **连带翻转**：`test_billing_service.py::test_project_revenue_all_zero`（原断言全零 ⇒ `total==0`）改为 `test_project_revenue_rejects_all_zero`（断言拒收）。
- **故障注入**：恒假化 `if total <= 0:` ⇒ 负数重新被接受 ⇒ **M7 转红** ✅。

### 1.3 Q-V —— 复核参数错误改 422 ✅
- **改动**：`errors.py` 增 `REVIEW_INVALID_PARAM` + `UnprocessableEntityError`（422）；`reviews.py` 两处错配（`ensure` 非法参数原报 404 `REVIEW_NOT_FOUND`；`decide` 未知结论原报 400 `REVIEW_ALREADY_DECIDED`）统一改 422。
- **判据**：R4 / R11 升级为断言 **422** + `REVIEW_INVALID_PARAM`。
- **故障注入**：两处 `UnprocessableEntityError` 全局改回 `NotFoundError` ⇒ **R4 + R11 双双转红** ✅。

### 1.4 Q-U —— 会话归属防客户伪造 ✅
- **改动**：`conversations.py` 仅**所内人员**（非 `CLIENT`）可指定 `client_user_id` / `bind_lawyer_id`；客户传了按「自己 / 不绑定」处理。
- **判据**：`C11` 翻转为「客户伪造被忽略 + 所内人员指定生效（反向量）」。
- **故障注入**：恒假化客户分支 ⇒ 伪造被采信 ⇒ **C11 转红** ✅。

### 1.5 Q-R —— 证据端点权限码门控 ✅
- **改动**：`evidence.py` router 级统一挂 `evidence:read`；写端点叠加 `evidence:create`（上传）/ `evidence:update`（重解析）。归属隔离仍由 `_case_or_404` / `_evidence_or_404` 兜底。
- **RBAC 配套**：`CLIENT` 原有的是 `evidence:*:own`，**没有**无后缀码 ⇒ 直接挂门会把客户自己的材料也 403。故给 `CLIENT` 补 `evidence:read/create/update`。
- **口径说明**：决策包字面写 `evidence:write`，实现改用**已存在**的 `create` / `update`，避免为同一语义新增第三个码并牵动整个矩阵。
- **判据**：新建 `tests/test_evidence_rbac_gate.py`（R-gate-A AST / B 无权限 403 / C 有权限过门）。
- **踩坑**：`test_evidence_authz.py::_client_for` **只**覆盖 `get_tenant_context`，而 `require_permissions` 读 `get_current_user.role` ⇒ role 为 `None` ⇒ 全 403。已补 `get_current_user` 覆盖。
- **验证**：证据四文件 **49 passed**。
- **故障注入**：摘掉 router 级 `dependencies` ⇒ **R-gate-B 转红** ✅。

### 1.6 Q-S —— 卷宗下载按案件归属授权 ✅
- **改动**：`files.py` 下载端点对 `CLIENT` 增加**归属校验**：用 `Evidence.file_path` 反查所属案件，再比 `case.client_user_id`。
- **新建反查链路**：`save_upload` 返回的 `rel_path` = `{tenant_id}/{subdir}/{filename}`，与下载 URL **同形** ⇒ 可精确反查。此前确实没有这条链路（`_evidence_or_404` 只按 `evidence_id` 取数）。
- **失败即拒（fail-closed）**：查不到 `Evidence` 记录的无主文件也一律 404（当前 `save_upload` 只有证据上传一处调用，无主只可能是孤儿文件）。
- **判据**：`test_file_download_endpoint.py::D9`（同租户客户乙下载客户甲案件卷宗 ⇒ 404）。
- **连带修正**：`D3`（扩展名白名单）改用**律师**身份 —— 白名单与归属是**正交**的两道门，D3 现写的临时文件没有 Evidence 记录，用客户身份会被 fail-closed 挡掉，测的就不是白名单了。
- **验证**：下载文件 **21 passed**。
- **故障注入**：恒假化归属分支 ⇒ 客户乙能下载 ⇒ **D9 转红** ✅。

### 1.7 Q-M —— admin 手动调额度端点 ✅
- **改动**：新增 `POST /billing/quota/adjust`，沿用 Q-P 的 `billing:write` 口径；`BillingService.set_quota_limit`（语义是**设置新上限**的绝对值，不是增量；拒负）；新增 `AuditAction.QUOTA_ADJUST` 审计动作，留痕含**调整前后**值。
- **判据**：`MQ1`（管理员调整 ⇒ 200 + 落库 + 审计留痕 + 不动他租户）· `MQ2`（负上限 ⇒ 4xx）· `MQ3`（无 `billing:write` ⇒ 403）。
- **验证**：计费层 **12 passed**。

### 1.8 Q-H —— 失败任务可见入口 ✅
- **改动**：新增 `GET /jobs`（分页 + `?status=` 过滤，按 `ctx.tenant_id` 隔离）。
- **为什么需要**：此前只有 `GET /{job_id}` 与 `POST /{job_id}/retry` —— 要先**知道 id** 才看得见、才重试得了；而僵尸任务（重试 3 次后判 FAILED）恰恰是「没人知道它在」的那一类 ⇒ 重试入口形同虚设。
- **判据**：`JH1`（能看到本租户失败任务、看不到他租户）· `JH2`（`?status=FAILED` 只返回失败任务，反向量：仍能拿到本租户失败任务）。
- **验证**：任务文件 **13 passed**。

### 1.9 收口验证（末次全量回归）

| 项目 | 结果 |
|------|------|
| 全量回归 | **750 passed / 0 failed**（1305.50s）—— **新基线**（744 → 750，+6：D9 / JH1 / JH2 / MQ1–MQ3） |
| `ruff` | 全绿 |
| `evidence/run_ci_probes.py` | **通过 14 / 失败 0** |
| 注入残留 | `grep -rn INJECTED` **无命中**；临时注入器已删 |

中途两次失败**都是真信号，不是环境问题**：

1. **743 passed / 1 failed** —— Q-AA 的 `<= 0` 撞上 `test_project_revenue_all_zero` 的旧期望。这是**口径变了**（净零也算拒收），已翻转为 `test_project_revenue_rejects_all_zero`，并回问产品（见 §待确认）。
2. **749 passed / 1 failed** —— `QuotaAdjust.usage_type: str` 被 `test_enum_input_validation.py` 的类级防线判「脏值绕过写入校验」。改为 `UsageType` 后 22 条定向复跑全绿（Pydantic 直接 422，端点无需手工转换）。

---

## 2. 未完成（1 项，卡环境）

| 编号 | 事项 | 为什么不做 | 需要什么 |
|------|------|-----------|---------|
| **Q-G** | 向量读侧接 `Retriever` | 推荐口径是**只接 Postgres**（顺带消掉内存后端无租户隔离盲区）。但本机回归环境只有 SQLite ⇒ **接了也验证不了**，而召回链路改错是静默的（召不回/串租户都不报错） | 一个 Postgres + pgvector 环境；接完需补「跨租户召回隔离」判据 |

---

## 3. 按推荐默认**只文档化不改码**（3 项）

- **Q-E**（`allow_methods=["*"]` / `allow_headers=["*"]`）：推荐**单独立项收紧，不阻塞上线**。本轮不动 `main.py`。
- **Q-W**（手动 retry 把 `retry_count` 归零）：推荐**接受现状但必须写进文档**——「手动重试 = 人工确认过，值得给新预算」。`J8` 已把「归零」钉住，口径一改判据立刻红。
- **Q-F**（21 个未写 `audit_logs` 的写端点）：推荐**先定合规口径**（哪些必须补、其余登记豁免）。棘轮 `BASELINE_UNAUDITED_MAX=21` 已卡住不许涨，旧账待产品定口径后再补。
  - 本轮 Q-M 的新端点**已按合规要求留痕**（`QUOTA_ADJUST`），不新增旧账。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 |
|---|------|--------|--------|
| 1 | 组一 8 项已实现（Q-P / Q-AA / Q-V / Q-U / Q-R / Q-S / Q-M / Q-H） | 后端 | 本轮 ✅ |
| 2 | 全量回归 + ruff + `run_ci_probes` 门禁校验 | 后端 | 本轮 |
| 3 | **Q-G**：准备 Postgres + pgvector 环境后再接 Retriever | 后端 / 运维 | 待环境 |
| 4 | Q-F：产品定「哪些写端点必须补审计」的合规口径 | 产品 | 待定 |
| 5 | Q-E：单独立项收紧 CORS 通配 | 产品/后端 | 待定 |
| 6 | 复核 Q-AA 的边界：全零预测是否也被拒（当前 `<= 0`，会拒全零） | 产品 | 待定 |

---

## ⚠️ 待确认 / 假设 / Non-goals

- **待确认（Q-AA 边界）**：「`<= 0`」把**全零预测**（新租户的空预测）也拒了。若认为应保留，把判等放宽为 `< 0` 即可 —— `M7` 与 `test_project_revenue_rejects_all_zero` 会同时提示。
- **假设（Q-R 口径）**：用已有的 `evidence:create`/`evidence:update` 替代字面的 `evidence:write`，语义等价且不动矩阵；若坚持要 `evidence:write` 码名可再改名（判据会红逼人确认）。
- **假设（Q-S fail-closed）**：对客户查不到归属就拒。当前 `save_upload` 只有证据上传一处调用，故只影响孤儿文件；若将来新增别的 `subdir` 上传，需同步给那些文件建归属记录。
- **Non-goals**：本轮不动前端；组三 🔵（Q1 / Q2 / Q-K①+Q-L / Q-J）仍卡外部资源，不在范围内。

---

## 📚 数据来源 & 成员产出索引

- 析客（需求分析师）：12 项裁定选项与后端动作，源自 `decisions-2026-09-20.md` §3 与 `decisions-pending-group1-2026-09-21.md`。
- 瑞思（用户研究员）：未新增调研；沿用既有归属/权限风险判断。
- 竞析（竞品分析师）：未新增扫描；权限矩阵口径对齐 billing 既有范式。
- 数析（数据分析师）：未新增指标；沿用审计棘轮与路由覆盖棘轮。
- 路径（路线图规划师）：组一排期 1–2 人日，本轮完成 8 项，剩 Q-G 等环境。
- 主理人（方向明）：实现 + 判据 + 故障注入自证 + 本记录汇编。

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。
