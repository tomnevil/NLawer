# 后端配合清单（2026-09-21）

> **用途**：把「需要后端团队跟进的工作」从决策台账里摘出来，按**能不能立刻开工**分组，
> 让产品把裁定做完、后端把活领走，两边不互相等。
> **读者**：产品（做裁定）+ 后端（领活）。
> **来源**：`deliverables/product-strategy/decisions-2026-09-20.md`（决策台账，唯一权威）。
> 本清单**不新增决策项**，只做分组与出处核对；每一条都能在台账里找到对应编号。

---

## 怎么读这份清单

| 组 | 含义 | 谁先动 |
|---|---|---|
| **一 🔴** | 需要产品先裁定，**后端等裁定**（现在动手就是猜） | 产品 |
| **二 🟢** | **后端可直接开工**，不需任何裁定 | 后端 |
| **三 🔵** | 卡**外部资源**，后端也做不了 | 产品去拿资源 |
| **四 ⚪** | 已完成，**仅供知晓**（避免重复报缺陷） | 无 |

⚠️ **一句话纪律**：组一里的事项，后端**不要**先写代码再等追认 —— 台账里每条都写着
「不定的代价」，先动手会把**产品取舍**变成**既成事实**。

---

## 总览

| 组 | 条数 | 事项 |
|---|---|---|
| 一 🔴 等裁定 | **11** | Q-P · Q-R · Q-S · Q-U · Q-V · Q-W · Q-E · Q-G · Q-H · Q-F · Q-M |
| 二 🟢 可开工 | **0** ✅ 全部完成（见文末「组二收口记录」） | 11 条零覆盖路由补判据 · `billing/*` 三条端点层覆盖 · Q-D 留档脚本作废 |
| 三 🔵 卡资源 | **4** | Q1 · Q2 · Q-K①/Q-L · Q-J |
| 四 ⚪ 已完成 | **8** | 派单守卫 · 证据守卫 · `files.py` 身份口径 · XFF 信任 · Q-O/Q-O2/Q-T 收口 · **2.1 路由覆盖清零** · **2.2 billing 端点层** · **2.3 Q-D 作废** |

---

## 一 🔴 需要产品先裁定（后端等裁定）

### 1.1 Q-P —— `POST /billing/consume`（**写**）由 `billing:read`（**读**）放行

- **现状（已复核）**：`backend/app/core/rbac.py:33` 的 `ROLE_PERMISSIONS` 里
  **只有 `billing:read`**（`:72`、`:135` 两处），**根本没有 `billing:write` 这个权限码**；
  而 `backend/app/api/v1/billing.py:54` 的 `/consume` 端点，门控写在 `:57`：
  `dependencies=[Depends(require_permissions("billing:read"))]`
  ⇒ **扣减用量**这个写操作只能挂在读权限上。
- **要你定**：三选一
  - ① 新增 `billing:write`，只给 `FIRM_ADMIN` / `ENTERPRISE_ADMIN`（**建议**）
  - ② 拆出 `billing:consume` 单独授权
  - ③ 明确接受现状，并写进权限矩阵说明
- **后端拿到裁定后的动作**：改 `rbac.py` 角色矩阵 + 改 `billing.py:57` 门控；
  `tests/test_billing_endpoint_scope.py::B6` 是**登记型**判据，口径一改它会红，强制回头确认。
- **不定的代价**：持 `billing:read` 的角色（含未来的**只读财务岗**）**都能扣减用量**。

### 1.2 Q-R —— 证据端点**完全没有权限码门控**

- **现状（已复核）**：`backend/app/api/v1/evidence.py:18`
  `router = APIRouter(prefix="/evidence", tags=["证据材料"])` —— **既无 `dependencies=`
  也无 `require_permissions`**，全文件 `grep` 零命中；6 个路由
  （`:72` / `:115` / `:132` / `:142` / `:152` / `:162`）全部裸挂。
  对比 `billing.py` 的 4 个路由**全挂门** ⇒ **两模块口径不一致**。
  目前靠 `_case_or_404`（`evidence.py:21`）的**归属校验**兜底。
- **要你定**：三选一
  - ① 引入 `evidence:read` / `evidence:write` 并接入 `ROLE_PERMISSIONS`（**建议**，与 billing 对齐）
  - ② 明确接受「证据按案件归属授权，不按权限码」并写进权限矩阵说明
- **后端拿到裁定后的动作**：若 ①，`evidence.py:18` 加 `dependencies=` + `rbac.py` 加权限码。
- **不定的代价**：归属校验只能防「**别人的案件**」，防不住「**本租户内谁都能上传/重解析**」；
  将来加角色（实习律师、外部专家）时无法细粒度收口。

### 1.3 Q-S —— 卷宗下载**要不要按案件归属授权**

- **现状（已复核）**：`backend/app/api/v1/files.py:58`
  `@router.get("/{tenant_id}/{subdir}/{filename}")`，守卫只有 `:84` 的
  `if ctx.tenant_id != tenant_id`（**租户级**）；全文件 `grep client_user_id` ⇒ **零命中**。
  ⇒ 同租户客户甲只要知道（或猜到）文件名，就能下载客户乙的卷宗。
- **⚠️ 重要前提（今日已变）**：该文件**今天（2026-09-21）刚修过**身份口径 ——
  `:73` 的注释写明「**必须用 `ctx.tenant_id` 而不是 `user.tenant_id`**」，
  由 `tests/test_file_download_endpoint.py::D4` 钉住。**但这次修的是「平台管理员看不看得见」，
  不是「客户甲能不能拿客户乙的」** —— **Q-S 仍然开着**，别把 D4 当成已收口。
- **要你定**：二选一
  - ① **改**：复用 `_evidence_or_404` 思路，下载也校验 `case.client_user_id`
    （**建议**，与 §3.19 口径一致）
  - ② 明确接受「卷宗按租户授权」并写进权限矩阵
- **后端拿到裁定后的动作**：若 ①，需从 `subdir`/文件名反查所属案件（当前**没有**这条链路，
  是本次要新建的），再比 `case.client_user_id`。
- **不定的代价**：与 §3.19 口径**分叉** —— 重解析已按案件归属卡住，**下载却能绕过同一道边界**
  ⇒ **修了一半**。

### 1.4 Q-U —— 会话创建时 `client_user_id` / `bind_lawyer_id` **可被客户伪造**

- **现状（已复核）**：`backend/app/api/v1/conversations.py:35`
  `client_user_id=payload.client_user_id or ctx.user_id`、`:36` `bind_lawyer_id=payload.bind_lawyer_id`
  ⇒ 请求体里的**归属被采信**。实测（C11）：客户甲可建一条归属为客户丁的会话，
  但**自己读不到**（`:52` 的可见性过滤 + `:121` 的端用户规则）⇒ **无数据泄露**，
  只有**归属污染**（丁的收件箱多一条自己没发起的会话）。
- **要你定**：二选一
  - ① 限制为「**只有所内人员**（LAWYER / 助理 / 管理员）才能指定 `client_user_id`」（**建议**）
  - ② 明确接受现状（所内代建会话确实需要这个能力）
- **后端拿到裁定后的动作**：若 ①，在 `conversations.py:35` 前按 `ctx.role` 判一次。
- **不定的代价**：「谁发起的」这个事实在事后**不可追溯**；当前靠访问判据兜底，
  一旦将来放宽所内可见性策略，**污染立刻变成泄露**。

### 1.5 Q-V —— 复核两处**错误语义错配**

- **现状（已复核）**：
  - ① `backend/app/api/v1/reviews.py:81` —— `target_type` 非法时抛
    `NotFoundError("参数不合法", code=ErrorCode.REVIEW_NOT_FOUND)`（**404**，
    文案「参数不合法」但码是「复核任务不存在」）；
  - ② `reviews.py:192` —— `decision` 未知时抛
    `BadRequestError(f"未知复核结论：{payload.decision}", code=ErrorCode.REVIEW_ALREADY_DECIDED)`
    （**400** 但码是「已出过结论」）。
- **要你定**：二选一
  - ① 改成 422 + 新增 `REVIEW_INVALID_PARAM` / `VALIDATION_ERROR`（**建议**）
  - ② 明确接受现状并写进接口文档
- **后端拿到裁定后的动作**：改两处 `raise` + 可能加错误码常量。
- **不定的代价**：调用方无法区分「我传错了」与「东西不存在」，排障时被误导。
  ⚠️ 本轮判据**只固化不变量**（「拒绝且不落库 / 状态不变」），**未断言具体状态码**，
  就是为了避免把错误语义焊死 —— 所以这条改起来不会有判据阻力。

### 1.6 Q-W —— 手动 `POST /jobs/{id}/retry` 把 `retry_count` **归零**（🟡 P2）

- **现状（已复核）**：`backend/app/api/v1/jobs.py:29` 是 `@router.post("/{job_id}/retry")`，
  `:49` `job.retry_count = 0`。而 `JOB_MAX_RETRIES` 只约束**自动重试**与僵尸回收
  ⇒ 一个必然失败的任务可被**无限手动重试**，每次都重新消耗 LLM 额度。
- **要你定**：二选一
  - ① **接受现状**：「手动重试 = 人工确认过，值得给新预算」，但**必须写进文档**（**建议**）
  - ② 改成累加：预算耗尽后只能改需求重跑
- **后端拿到裁定后的动作**：若 ②，改 `jobs.py:49` 为累加（`+= 1` 或不重置）。
- **不定的代价**：不写下来就会被当成 bug。`J8` 已把「归零」这个行为**钉住**
  ⇒ 口径一改判据立刻红，**不会有静默漂移**。

### 1.7 Q-E —— `allow_methods=["*"]` / `allow_headers=["*"]`

- **现状（已复核）**：`backend/app/main.py:209` `allow_methods=["*"]`、
  `:210` `allow_headers=["*"]`（origin 本轮已收，见 `:207`）。
- **要你定**：是否单独立项收紧（台账建议：**单独立项，不阻塞上线**）。
- **不定的代价**：预检形同虚设（**不造成跨域读取**，故非安全阻塞项）。

### 1.8 Q-G —— 向量读侧接不接

- **现状**：`KnowledgeEmbedding` 表在涨，`Retriever` 在产品代码引用数 **0** ⇒ **只写不读**。
- **要你定**：接不接；若接，是否**只接 Postgres**。
- **台账建议**：**接，但只接 Postgres**（顺带消掉内存后端无租户隔离的盲区）。
- **不定的代价**：继续往库里堆没人读的向量；SQLite 部署若启用召回则**无租户隔离**。

### 1.9 Q-H —— 僵尸任务 3 次后判 FAILED，**有无人工重试入口**

- **现状**：已把「永不放弃」改成「上限即失败」。
- **要你定**：确认**有无人工重试入口**（目前只能重新入队）。
- **后端动作**：若确认需要，补一个可见的重试入口（与 Q-W 的 `jobs/{id}/retry` 是**不同**的问题：
  Q-W 是「归零算不算绕过上限」，Q-H 是「失败任务**有没有人看得见**」）。
- **不定的代价**：失败任务静默堆积、无人可见。

### 1.10 Q-F —— 21 个未写 `audit_logs` 的写端点

- **现状（已复核）**：`backend/tests/test_audit_coverage.py:13-14` —— 42 个写端点中
  **21 个未留痕**；`:20` 明写「『这 21 个未留痕端点里哪些**必须**补』是
  **产品/合规裁定**，不是工程能单方面决定的」；`:26` A3 **不许涨**；
  `:66` `BASELINE_UNAUDITED_MAX = 21`。
- **要你定**：**合规口径** —— 哪些**必须**补，其余**登记豁免**。
- **后端拿到裁定后的动作**：按裁定补 `record(...)`；`:32` 注释写明
  「若将来合规裁定某类端点必须补留痕，应把 A3 的基线数字**调小**（收紧棘轮）」。
- **不定的代价**：新账已被棘轮卡住（不许涨），但**旧账越积越久**。

### 1.11 Q-M —— 线下充值期间，额度怎么改

- **现状**：API 层 **0 处** `quota` / `recharge`；工单端点只处理投诉/建议，**不承载充值**
  ⇒ 目前只能**直连 DB 人工改**。
- **要你定**：① 人工改库的**操作入口与授权人**；② 是否需要一个 admin 端点「手动加额度」；
  ③ 工单与额度变更是否需要关联留痕（**与 Q-F 的审计留痕范围耦合**）。
- **后端动作**：若 ② 为是，则新建 admin 端点（涉及 Q-P 的权限码口径）。

---

## 二 🟢 后端可直接开工（不需任何裁定）

### 2.1 补 **11 条**零端点层覆盖路由的判据

- **实测（刚跑，非引述）**：`backend/.venv/Scripts/python.exe evidence/verify_route_coverage.py`
  ```
  # 路由总数 79，端点层零覆盖 11（棘轮基线 16）
  ## A 类：服务层有测试、端点层零请求（零件测过、装配没测）（11）
  ## B 类：服务层同样零引用（0）
  ✅ 未突破棘轮（11 ≤ 16）
  ```
- **11 条清单**：

  | 方法 | 路径 |
  |---|---|
  | GET | `/api/v1/auth/me` |
  | POST | `/api/v1/qa/stream` |
  | GET,POST | `/api/v1/knowledge/docs` |
  | DELETE,GET | `/api/v1/knowledge/docs/{doc_id}` |
  | GET | `/api/v1/billing/dashboard` |
  | POST | `/api/v1/billing/consume` |
  | POST | `/api/v1/billing/project-revenue` |
  | GET | `/api/v1/complaints/policy` |
  | GET | `/api/v1/complaints/stats` |
  | POST | `/api/v1/complaints/{complaint_id}/handle` |
  | GET | `/api/v1/complaints/{ticket_no}` |

- **为什么可以直接开工**：台账 Q-T 已裁定「按建议 ① 推进」，且**已证明有效** ——
  本轮清 43 条**查出 6 个真缺陷**（含 1 个 P0 级跨租户写），命中率约 **14%**。
  剩这 11 条据此外推**大概率还有 1–2 个**。
- **⚠️ 方法要按台账改**：Q-T 的结论是「**不要再按路由逐条试**」，
  改用**模式横扫**（搜「服务层方法签名里没有 `tenant_id` 但端点却按 `id` 取资源」）——
  但注意 **§3.30 的反向结论**：模式横扫是**排序工具**，不是**定罪工具**
  （报出的 20 条候选里 `JobService.get` 与 `ArchiveService.versions` **都是无辜的**）。
- **棘轮**：`evidence/verify_route_coverage.py:70` `BASELINE_ZERO = 16`，实测 11
  ⇒ **只降不涨**，补判据后应把基线**继续下调**。

### 2.2 `billing/*` 三条路由的**端点层**覆盖（Q-O 的收口口径需要澄清）

- **⚠️ 这是一处**口径**要澄清的地方，不是「测试没写」**：
  - 台账把 **Q-O 记为「已收口」**，但那是按 **§3.17** 的口径（**服务层 + 数据边界**）算的：
    `backend/tests/test_billing_service.py` 覆盖的是**服务层**，且把计费服务 monkeypatch 掉了。
  - 而**端点层（HTTP）**实测仍然零覆盖：`verify_route_coverage.py` 把
    `billing/dashboard`、`billing/consume`、`billing/project-revenue` 三条列在 **A 类**。
  - **实测证据**：`backend/tests/test_billing_endpoint_scope.py` 全文里
    对计费端点的 HTTP 请求**只打 `/api/v1/billing/work-orders`**
    （`:294`、`:313`、`:330` 三处），**从未**请求 `dashboard` / `consume` / `project-revenue`。
- **⇒ 结论**：这两条**不矛盾**，是**两把尺子**。Q-O 收的是「服务层有数据边界判据」，
  端点层这三条**仍然开着**，应并入 §2.1 一起清。
- **建议**：在台账里给 Q-O 补一句「收口口径 = 服务层；端点层另计」，避免以后误读成全覆盖。

### 2.3 Q-D —— 两个留档验证脚本**就地作废**

> ⚠️ **这两个脚本在 `backend/` 下，不在 `evidence/` 下** —— 别去找错目录。

- **现状（两个文件，实测）**：
  - `backend/verify_security_fixes.py`（**15 项**，静态）
  - `backend/verify_security_fixes_e2e.py`（**6 项**，e2e）
  - 两者在 `tests/`、`conftest.py`、`pyproject.toml`、`.github/` 中**引用数 = 0**
    ⇒ CI（`.github/workflows/ci.yml:78` 跑 `pytest tests/ -q`）**一条都不会执行**。
- **两种坏法，性质不同**：
  - `verify_security_fixes_e2e.py` 硬编码 `DATABASE_URL=./storage/verify_e2e.db`（第 16 行），
    库文件**跨运行保留** ⇒ 注册用例从第二次运行起**永久 409 红**。
    实测该库里 `hacker_p0_1` 的 `created_at = 2026-09-13 01:39`
    ⇒ **这条已经红了 8 天，无人发现** —— 正因为它不在门禁里。这就是台账里的「**红 1/6**」。
  - `verify_security_fixes.py` 的 `P0-14b` / `P0-14c` 用**子串命中**判据
    （`"_review_or_404(" in src`）。**双向注入实测**：把守卫的租户比对删掉（注入 A）
    ⇒ **15/15 全绿**；把端点里的守卫调用注释掉（注入 B）⇒ **15/15 全绿**
    ⇒ **它是假判据，而且是全绿的那种假**。
- **⚠️ 别把整份留档判成「零判据」**：`e2e` 脚本的 `P0-14` 项**是真判据**
  （注入 A 下正确转红）。准确表述是「**有判据，但不在门禁内；同一份留档里的另一半是假的**」。
- **台账建议已明确**：**就地作废**（留档的价值是「当时的现场」，不是「持续有效的门禁」）。
- **后端动作**：给两个脚本加作废抬头（或移入归档目录），
  **不要**让它们继续出现在「可以手动跑一下看看」的路径上。
- **不定的代价**：继续被人手动跑一次看到 **5/6 绿** ⇒ **比没有更危险**。
  （本清单**不**把它们列为「待修」—— 它们是**待作废**，修判据的收益不如作废。）

---

## 三 🔵 卡**外部资源**（后端也做不了，得产品去拿）

| # | 事项 | 卡在哪 | 需要你提供 |
|---|---|---|---|
| 3.1 | **Q1** 合同审查在生产环境不可用 | `backend/app/config.py:117` `CONTRACT_REVIEW_SENSITIVE: bool = True` ⇒ 生产未配 LOCAL provider 时抛 `ConfigurationError` | ① 一个 **LOCAL provider 地址**（如内网 Ollama），**或** ② 「生产关闭合同审查入口」的**书面确认**。⚠️ 台账明写**不要**默默改 `False` —— 那等于**产品承诺变了** |
| 3.2 | **Q2** 真实 LLM API Key 未配置 | `/api/health` 的 `llm_providers`（cheap/strong/local）**全 false** ⇒ 问答与审查走**降级路径** | 用哪家、走 cheap/strong **哪一档**、配额与预算上限。**性质是操作前置，不是代码缺陷** |
| 3.3 | **Q-K ① + Q-L** 邮箱验证与邮件找回 | 三层皆缺：**字段层** `RegisterRequest`（`schemas/auth.py:33-46`）无 `email` ⇒ `users.email` **恒为 NULL**；**通道层** `config.py` 对 smtp/mail 命中 **0**、全库 `smtplib`/`send_mail` 命中 **0**；**端点层** `auth.py` 里 **0 个** reset/forgot/change-password | ① **SMTP 凭据**或事务邮件 API；② 重置令牌 TTL（建议 ≤ 30 分钟）；③ 邮件模板与发件人；④ 老用户（email 为 NULL）**怎么补**邮箱；⑤ 该端点是否单独限流（防账号枚举）。⚠️ **拿到凭据也不够** —— 得先补 `email` 字段与验证流程 |
| 3.4 | **Q-J** 仓库状态 + 前端测试运行器 | `git rev-parse --is-inside-work-tree` ⇒ **`fatal: not a git repository`**；`ci.yml` 存在但**从未真正跑过**；前端测试文件实测 **0 个** | ① **git 仓库的实际位置 / 是否初始化**；② vitest 是否单独立项。⇒ 目前所有 CI 改动**只有本地预演**这一步证据 |

---

## 四 ⚪ 已完成（**仅供知晓**，避免被重复报成缺陷）

| # | 事项 | 状态 | 出处 |
|---|---|---|---|
| 4.1 | 派单 `accept`/`grab` **全程无租户校验**（租户 A 律师可改租户 B 案件 `lawyer_id` 并推进到 ACCEPTED） | ✅ **已修**：按既有范式补 `_dispatch_or_404` | `dispatches.py:19`，`tests/test_dispatch_endpoint_layer.py::P4–P6` 坐实 |
| 4.2 | `POST /evidence/{id}/parse` **无客户归属校验**（同租户客户乙遍历 `evidence_id` 可读/覆盖他人 `ocr_text`） | ✅ **已修**：补 `_evidence_or_404` | `evidence.py:45` |
| 4.3 | 卷宗下载用 `user.tenant_id` 判等 ⇒ 平台管理员**列表看得见、下载 404** | ✅ **已修（2026-09-21）**：改用 `ctx.tenant_id` | `files.py:73`，`tests/test_file_download_endpoint.py::D4`。⚠️ **与 Q-S 是两件事**，Q-S 仍开着 |
| 4.4 | 限流可被 `X-Forwarded-For` **低成本绕过**（每换一个伪造头就是新桶） | ✅ **已修**：新增 `TRUSTED_PROXIES`（默认只信本机反向代理），`_client_ip` 改为从右往左取第一个不可信跳；判据 **R7–R12** + 整机复验 **T8**，**双向故障注入**已证明 | `config.py:90`、`middleware.py`；`tests/test_rate_limit.py`、`tests/test_auth_register.py` |
| 4.5 | Q-O（计费数据边界）· Q-O2（证据上传端点）· Q-T（路由覆盖推进到第六批） | ✅ **已收口**（口径见 §2.2 的澄清） | `decisions-2026-09-20.md` §3 |

> ⚠️ **4.1 / 4.2 的教训值得后端看一眼**：本轮清的 20 条里**唯一的缺陷**是派单，
> 而派单在「风险排序」里**并不靠前** ⇒ **风险排序本身会漏**。
> 两者的形状相同：**服务层 `_get()` 无租户过滤 + 服务层不收 `tenant_id` + 端点不校验**。

---

## 附录 A：全部出处（**已逐条 grep 复核**）

| 编号 | 出处 | 复核方式 |
|---|---|---|
| Q-P | `backend/app/core/rbac.py:33`（`ROLE_PERMISSIONS`）、`:72`/`:135`（仅 `billing:read`）；`backend/app/api/v1/billing.py:54`（`/consume`）、`:57`（门控） | `grep -n "ROLE_PERMISSIONS\|billing:"` · `grep -n "consume\|require_permissions"` |
| Q-R | `backend/app/api/v1/evidence.py:18`（`APIRouter` 无 `dependencies=`）；6 路由 `:72/:115/:132/:142/:152/:162` | `grep -n "APIRouter\|dependencies\|require_permissions"` · `grep -c "@router\."` = **6** |
| Q-S | `backend/app/api/v1/files.py:58`（路由）、`:84`（仅租户级）、`:73`（今日身份口径修正） | `grep -n "client_user_id"` ⇒ **零命中** · 通读 `:55-120` |
| Q-U | `backend/app/api/v1/conversations.py:35`、`:36`、`:52`、`:121` | `grep -n "client_user_id\|bind_lawyer_id"` |
| Q-V | `backend/app/api/v1/reviews.py:81`、`:192` | `grep -n "REVIEW_NOT_FOUND\|REVIEW_ALREADY_DECIDED"` |
| Q-W | `backend/app/api/v1/jobs.py:29`（路由）、`:49`（`job.retry_count = 0`） | `grep -n "retry_count\|retry"` |
| Q-E | `backend/app/main.py:207`（origin）、`:209`、`:210` | `grep -n "allow_methods\|allow_headers\|allow_origins"` |
| Q-F | `backend/tests/test_audit_coverage.py:13-14`、`:20`、`:26`、`:66`（`BASELINE_UNAUDITED_MAX = 21`） | `grep -n "21\|未留痕\|裁定"` |
| Q1 | `backend/app/config.py:117` `CONTRACT_REVIEW_SENSITIVE: bool = True` | `grep -n "CONTRACT_REVIEW_SENSITIVE"` |
| Q-L | `backend/app/schemas/auth.py:33-46`（`RegisterRequest` 只有 `username`/`password`/`full_name`，**无 `email`**） | `sed -n '33,46p'` 通读 · `grep -c "email"` ⇒ **0** |
| Q-J | `git rev-parse --is-inside-work-tree` ⇒ `fatal: not a git repository`；前端测试文件 **0** | 实跑 |
| Q-D | `backend/verify_security_fixes.py`（15 项）+ `backend/verify_security_fixes_e2e.py`（6 项，第 16 行硬编码 DB）；`.github/workflows/ci.yml:78` 只跑 `pytest tests/ -q` | `find . -name "verify_security*"` ⇒ 命中 3 个（**均在 `backend/`**）；`ls evidence/` ⇒ **无** |
| 零覆盖 11 条 | `evidence/verify_route_coverage.py:70` `BASELINE_ZERO = 16` | **实跑探针**，输出 79 路由 / 11 零覆盖 / B 类 0 |
| `billing` 端点层 | `backend/tests/test_billing_endpoint_scope.py:294/:313/:330` 仅打 `work-orders` | `grep -n 'client.get\|/billing/'` |
| 归属守卫范式 | `analyses.py:23` · `cases.py:19` · `dispatches.py:19` · `documents.py:34` · `evidence.py:21` · `evidence.py:45` · `reviews.py:18` | `grep -rn "^async def _.*or_404"` ⇒ **7 个** |

---

## 附录 B：本次复核做了什么 / **刻意没写什么**

**做了什么**

1. 以 `decisions-2026-09-20.md`（161 行）为**唯一权威来源**通读，**不凭记忆转述**。
2. **逐条 grep 复核**上表全部代码引用，取**真实行号**。
3. **实跑** `verify_route_coverage.py` 取零覆盖清单（非引述台账数字）。
4. 顺手**更正两处我自己的旧笔记**：
   - 此前记「`grep -r CONTRACT_REVIEW_SENSITIVE backend/app/` 查不到」是**检索路径写错**，
     实际在 `backend/app/config.py:117`；
   - 路由覆盖缺口是 **11，不是 16** —— 16 是**棘轮基线**，11 是**实测值**（只降不涨，所以 11 ≤ 16）。
5. **附录 A 里没有一行是「引台账、未复核」** —— 最后一条 `Q-L` 也已实测
   （`grep -c "email" schemas/auth.py` ⇒ **0**）。

**刻意没写什么**

- **不发明后端工作**：每一条都能追到台账编号或一次 grep。追不到的**直接丢掉**，不补白。
- **Q-N（注册前端零入口）没有列进来** —— 它是**前端**事项（SDK 无 `register` 方法、
  `apps/web` 无注册页），后端端点 `auth.py:67` 已经存在，**后端无需动**。
- **§4 的 6 项设计/前端事项没有列进来** —— 按项目约定「设计类改动先出规范、定稿后才动工」，
  它们归 UI 侧，不占用后端排期。

---

## 组二 🟢 收口记录（2026-09-21）

> 三条全部完成。技术证据见 `round15b-fixed-but-unguarded-sweep-2026-09-20.md` §3.32 / §3.33；
> Q-AA 见本台账 §3，Q-D 见上方 ✅。

### 2.1 ✅ 11 条零覆盖路由判据已全部补齐（棘轮 11 → 4 → 0）

- **实测（2026-09-21）**：`evidence/verify_route_coverage.py` 现输出
  `路由总数 79，端点层零覆盖 0`（棘轮基线 11 → 4 → 0，B 类全程 0）。
- 新增端点层判据（均「先红后修」+ 故障注入自证）：
  - §3.32 `billing/*` 3 条 + `complaints/*` 4 条 →
    `test_billing_endpoint_layer.py`（9，M1–M8b）+ `test_complaint_endpoint_layer.py`（11，T1–T10）= **20 条**，全绿。
  - §3.33 `auth/me` + `knowledge/docs`(+`{doc_id}`) + `qa/stream` →
    `test_auth_endpoint_layer.py`（3，A1–A3）+ `test_knowledge_endpoint_layer.py`（5，KD1–KD5）
    + `test_qa_stream_endpoint_layer.py`（4，QS1–QS4）= **12 条**，全绿。
- **查出的真实缺陷（§3.32）**：`POST /billing/consume` 对不存在的 member 抛
  `ErrorCode.NOT_FOUND`，但 `ErrorCode` **根本没有 `NOT_FOUND` 成员** ⇒ 构造异常时 500。
  已修为 `400 + VALIDATION_ERROR`（`app/api/v1/billing.py:76`，与 Q-V 口径一致）。
- **登记 Q-AA（§3.32）**：`billing/project-revenue` 接受负数营收（`total_cents=-1` 被照收）。见本台账 §3。
- 故障注入：§3.32 **10 臂** + §3.33 **8 臂** = 18 臂，全部「干净绿 → 注入红 → 还原绿」；
  累计注入 **144 → 162** 次。
- 全量回归 `pytest tests/ -q`：**741 passed / 0 failed**（709 → 729 → 741）。
  `ruff` 全绿；`grep -rn INJECTED app/ tests/` 无残留；CI 门禁 `run_ci_probes.py` **通过 12 / 失败 0**。

### 2.2 ✅ `billing/*` 三条端点层覆盖已完成

- 即 §3.32 的 M1–M8b（dashboard / consume / project-revenue）。`dashboard` 只显本租户
  （`tenant_id` 写死会冒出他租户行）、`consume` 只扣调用方租户、`project_revenue` 纯计算不写库
  （标记周期探针 `2099-01`）、`consume` 门控登记型——均已判据化。
- **口径已澄清**：Q-O「已收口」= 服务层；本批补的是端点层，两把尺子，不矛盾（见 §2.2 原文）。

### 2.3 ✅ Q-D 留档脚本已就地作废

- `backend/verify_security_fixes.py` / `verify_security_fixes_e2e.py` / `verify_security_fixes_round2.py`
  三件全部移入 `backend/archive/retired-security-verifiers/` 并附 `README.md`
  （说明作废原因 + 旧断言与 `tests/` 判据的对应关系）。
- 已 `grep` 确认无 CI / conftest / 源码引用；不再出现在「可手动跑」的路径上。
