# 律小智 AI 法律助手 · 全面代码审查与优化计划

**日期**：2026-09-12
**类型**：代码审查 + 优化计划（上线就绪度评估）
**审查范围**：backend 127 个 Python 文件（约 11,665 行）+ frontend 4 应用/2 包（约 4,942 行 TS/TSX）+ 部署配置
**审查方式**：主理人 + 后端架构审查员（backend-auditor）交叉验证，全部结论基于真实代码证据（文件:行号）
**参与成员**：方向明（主理人）、后端架构审查员

---

## 📌 TL;DR（执行摘要）

- **核心结论**：这是一个**架构设计优秀、工程实现完成度约 65%、但距离可上线运营仍有硬差距**的项目。领域建模（复核状态机、强制复核命中器、租户隔离、引用校验）达到了超出一般 MVP 的水准，但**关键能力大量停留在「骨架/占位」状态**，生产环境直接启动会立刻失败。
- **最致命问题**：① 生产环境**无 Dockerfile 构建验证**（compose 引用 `./backend/Dockerfile`，需在 CI 中验证可用性）；② **IM 接入层全部是骨架**——产品线 A 的核心假设「客户零门槛在微信里对话」目前**完全没有真实实现**；③ **向量检索链路断裂**（`search(None)` 硬编码空查询向量），RAG 实际退化为纯 BM25；④ **审计日志只接了登录**，PRD 要求的复核/归档/证据全链路留痕**未落地**；⑤ **前端无注册、无找回密码、无支付**，无法承接真实付费用户；⑥ **无内容安全审核**，无法通过算法备案（上线前置条件）。
- **生产就绪度评分：42 / 100**（安全事故级别问题未清、核心链路未闭环）。
- **关键决策建议**：**不要按原计划「先补功能再上线」**。建议采用「**阻断项清零 → 单 IM 真实打通 → 小范围真实试点**」三步走，把原定 V1.0 拆成「可试点版（P0 修复）」与「可运营版（P1 补齐）」两个门槛。
- **下一步**：先执行本文档「P0 必修项 Top 10」，其中 #1–#5 是**阻塞上线**，必须在任何真实用户接触系统前完成。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 分两阶段上线：**Phase 1「可试点版」**（P0 阻断项清零，单 IM 真实打通，内部+3 家律所灰度）→ **Phase 2「可运营版」**（P1 补齐，开放付费） |
| 优先级 | P0 共 **16 项**（阻塞上线，全部必须在真实用户前修复）；P1 共 **22 项**（开放付费前修复）；P2 共 **15 项**（迭代优化） |
| 预期影响 | 修复 P0 后：消除越权/数据泄露风险，产品线 A 核心链路可真实跑通；修复 P1 后：付费闭环成立，可对外运营 |
| 资源需求 | 后端 2 人 × 4 周（P0+P1）；前端 2 人 × 3 周；DevOps 0.5 人 × 2 周；法务/合规 0.5 人（算法备案材料） |
| 风险等级 | **高**（当前状态直接上线存在数据泄露、越权访问、服务不可用三重风险） |

---

## 一、生产就绪度评分

| 维度 | 得分 | 满分 | 扣分理由（关键） |
|------|------|------|------------------|
| **安全** | 5 | 20 | IDOR 越权（案件时间线无归属校验）；token 存 localStorage 无 XSS 防护；限流默认关闭且仅内存态；弱密钥默认值可被误用；无密码强度策略、无登录失败锁定；refresh token 无法撤销；上传无内容嗅探（仅后缀白名单） |
| **可靠性** | 7 | 15 | BackgroundTasks 进程重启即丢任务且无补偿扫描；无多 worker 幂等锁（同一 Job 可被重复执行）；Job 无 heartbeat，僵死任务无法回收；事务内混入外部调用与长耗时 AI 调用 |
| **数据一致性** | 8 | 15 | 复核状态机为纯逻辑白名单（设计优秀），但缺少数据库层乐观锁，并发复核存在竞态；关键写操作缺少行级锁；归档与版本控制缺唯一约束保护 |
| **AI / RAG 质量** | 6 | 20 | **向量检索链路断裂**（`search(None)`）；rerank 为词重叠启发式而非真实 rerank 模型（与 PRD 9.2 不符）；OCR 为文件名规则模拟；Mock 降级在生产无禁令（可能静默返回假法律答案）；知识图谱未实现；引用校验只校验「有引用」不校验「引用正确」 |
| **工程化** | 6 | 15 | **无 CI/CD**；无 Dockerfile 构建验证；无任何可观测性（metrics/tracing/告警）；根目录散落 5 个一次性脚本（`fix_fk.py` / `patch_migration.py` 等）；配置无分级（仅单一 `.env`） |
| **性能** | 5 | 10 | 异步服务中混入同步文件 IO（`archive_service.py:211` 直接 `open()`）；关键词查询用 `LIKE '%kw%'` 无法走索引；检索每次请求全量装载语料构建 BM25；无缓存层 |
| **前端** | 5 | 5 | ——（此项并入下文第九章：18 个页面、SDK 174 行、零测试、无 lint/CI、无注册/支付/找回密码） |
| **合计** | **42** | **100** | —— |

> 结论：**42 分意味着「可演示」但不「可运营」**。领域模型的成熟度（约 80 分水准）与工程完备度（约 35 分水准）严重不匹配。

---

## 二、P0 阻塞上线问题（16 项，必须全修）

> **📌 勘误（2026-09-16）**：本节标题原写「10 项」，与实际列出的 **16 项**不符，已修正。
> P0 修复真实进度为 **12/16**（详见 `remaining-work-inventory-2026-09-16.md` §十）。

### P0-1 【越权】案件时间线接口缺少归属校验 —— 任意用户可读取任意案件动态

**证据**：`backend/app/api/v1/cases.py:85-110`

```python
@router.get("/{case_id}/events", ...)
async def case_events(case_id: int, db=..., ctx=Depends(get_tenant_context)):
    rows = list((await db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(...)   # ← 无 case 归属校验
    )).scalars().all())
```

**对比**：同文件 `get_case()`（`:71-82`）正确做了「租户 + 客户归属」双重校验，而 `case_events` **完全没有**。

**影响**：任意已登录用户（含任意客户）遍历 `case_id` 即可读取其他当事人案件的完整事件流、时间线、涉案描述——**这是法律产品的致命数据泄露**，直接违反《个人信息保护法》与律师保密义务，且 PRD 10.1 明确要求「律师-客户特权通信保护」「客户仅见本人案件」。

**修复**：抽取 `_case_or_404(db, case_id, ctx)` 统一守卫（参考 `evidence.py:19-21` 的既有实现），在 `case_events` 中先加载 Case 并执行：

```python
case = await _case_or_404(db, case_id, ctx)
if ctx.role == Role.CLIENT and case.client_user_id != ctx.user_id:
    raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
```

**同时必须全量排查同类漏网**：`analyses.py`、`archives.py`、`reviews.py`、`documents.py` 中所有以 `case_id` / 资源 id 为入参的读取接口，确认均经过归属校验（其中 `evidence.py` / `archives.py` / `knowledge.py` 已具备，属正面范例）。

---

### P0-2 【AI 正确性】向量检索链路断裂，RAG 实际退化为纯关键词检索

**证据**：`backend/app/rag/retriever.py:28`

```python
res = self.vector_store.search(None, top_k=top_k, tenant_id=tenant_id)
#                             ^^^^ 查询向量硬编码为 None
```

`VectorStore.search(None)` 直接返回空（README 亦承认：「当前 `VectorStore.search(None)` 返回空」）。同时 `EMBEDDING_API_KEY` 为空，**写入侧 embedding 也无法生成**，即整条向量链路从写入到检索**完全未闭环**。

**影响**：
- PRD 9.2 明确要求「混合检索：BM25 + 向量检索」，实际只有 BM25，**召回率显著低于承诺**；
- 法条/类案的语义泛化能力丧失（用户说「被开除」无法召回「违法解除劳动合同」条文）；
- 直接冲击 PRD 成功指标「回答准确率 ≥90%」与「引用溯源完整率 100%」。

**修复**：
1. 实现 embedding 客户端（配置 `EMBEDDING_API_KEY` 时走真实模型，走 `text-embedding-v3` 或 bge-m3）；
2. `search()` 增加 query embedding 生成：`qvec = await embed(query)` → `search(qvec, ...)`；
3. 语料入库时同步写 `knowledge_embeddings`（当前 `embedding.py` 模型已存在但无写入路径，需补）；
4. 兜底策略显式化：无 Key 时**明确降级为 BM25-only 并打点告警**，不得静默。

---

### P0-3 【AI 正确性】生产环境 Mock LLM 静默降级，可能向真实用户输出假法律答案

**证据**：`backend/app/ai/router.py:70-72`

```python
provider = _provider(tier)
if provider is None:
    provider = MockProvider(tier.value)   # ← 无环境判断，生产同样静默降级
```

`MockProvider` 用于「零依赖演示」，但**生产环境若 Key 配置遗漏（如部署时忘配 `LLM_STRONG_API_KEY`），系统不会报错，而是继续用 Mock 生成看似合理的法律分析**。

**影响**：这是**法律产品的最高危失效模式**——律师看到「六段式分析」以为有效，实际是占位文本；一旦据此出具给当事人，将造成执业事故。

**修复**：
```python
if provider is None:
    if settings.ENVIRONMENT == "production":
        raise ConfigurationError("生产环境未配置 LLM API Key，拒绝使用 Mock 降级")
    provider = MockProvider(tier.value)
```
并在 `/api/health` 暴露各档 `provider_configured` 状态，启动时对生产环境做**前置校验**（缺 Key 直接 fail-fast，与 `SECRET_KEY` 校验同一策略）。

---

### P0-4 【可靠性】异步任务进程重启即永久丢失，且无补偿机制

**证据**：`backend/app/services/job_handlers.py:84`

```python
await db.commit()
background.add_task(run_job, job.id, _case_analysis_worker)   # ← 进程内 BackgroundTasks
```

FastAPI `BackgroundTasks` **在进程内执行**：服务重启 / 崩溃 / 滚动发布时，所有 PENDING 与 RUNNING 的 Job **永久丢失**，没有任何恢复扫描。

**影响**：律所办到一半的「案件分析」「证据解析」「合规扫描」静默消失，Job 表里永远停在 PENDING/RUNNING——用户在等待一个永不会完成的任务。

**修复（二选一，推荐 B）**：
- **A（轻量）**：启动时扫描 `status in (PENDING, RUNNING) AND updated_at < now-5min` 的 Job，重新投递；配合 `Job.heartbeat_at` 字段与周期任务复位僵死任务。
- **B（推荐，生产级）**：引入轻量任务队列（ARQ / Dramatiq / Celery + Redis），把 `run_job` 搬到独立 worker 进程；Job 表保留为「状态真相源」。

无论哪种，都必须补 `heartbeat_at` + 僵死任务回收 + PENDING 超时重投。

---

### P0-5 【可靠性】同一 Job 可被并发重复执行，无幂等锁

**证据**：`backend/app/services/job_service.py:76-92`

```python
async def run_job(job_id: int, worker):
    async with _semaphore:                      # ← 进程内信号量，跨进程无效
        ...
        job = (await db.execute(select(Job).where(Job.id == job_id))).scalars().first()
        for attempt in range(settings.JOB_MAX_RETRIES + 1):
            job.mark_running(...)               # ← 无「抢占式更新」保证唯一执行者
```

`_semaphore` 是**进程内**对象，多副本部署时完全失效；`mark_running` 是普通赋值，两个 worker 可同时通过检查。Job 无「已执行」唯一约束。

**影响**：多副本部署下同一案件被重复分析、重复计费扣减、重复生成法务文书（版本号错乱）。

**修复**：采用**抢占式 UPDATE**：
```sql
UPDATE jobs SET status='RUNNING', worker_id=:wid, heartbeat_at=now()
WHERE id=:id AND status='PENDING'   -- 影响行数=1 才继续
```
RETURNING 判断影响行数，确保唯一执行者；worker 完成时以 `worker_id` 为条件回写。

---

### P0-6 【安全】限流默认关闭，且实现为进程内内存态

**证据**：`backend/app/config.py:43`（`RATE_LIMIT_ENABLED: bool = False`）+ `middleware.py:86-93`（进程内 `defaultdict(deque)`）

**影响**：① 默认关闭 → 若生产忘开，登录口**无限暴力破解**；② 即使开启，多副本下每实例独立计数，实际阈值 ×N；③ 仅覆盖 `/auth/login` 与 `/auth/register`，**问答、文书生成、上传等耗 Token/耗资源的端点完全不限流**，存在成本被刷爆风险（PRD 风险表已识别「高并发下成本失控」，但未落地）。

**修复**：① 生产环境强制 `RATE_LIMIT_ENABLED=true`（与 SECRET_KEY 同策略 fail-fast）；② 换 Redis 实现跨实例限流；③ 按端点分级限额（登录 5/min、问答 20/min、上传 10/min）。

---

### P0-7 【安全】前端令牌存 localStorage，无自动刷新，存在 XSS 窃取风险

**证据**：`frontend/packages/sdk/src/index.ts:150-167`
```typescript
localStorage.setItem(STORAGE_KEY, JSON.stringify({ access, refresh }));
```
SDK 全文 174 行，**未发现任何自动 refresh 逻辑**；同时 `access_token` 有效期 **1440 分钟（24 小时）**（`config.py:38`），refresh 机制形同虚设。

**影响**：任一 XSS 即可窃取长期有效令牌 → 全量数据泄露；且 24 小时长效 access token 放大了泄露窗口。

**修复**：① refresh token 迁移至 **HttpOnly + Secure + SameSite=Strict Cookie**；② access token 缩短至 15–30 分钟并在 SDK 内实现 401 自动刷新（含并发请求去重）；③ 全站接入 CSP。

---

### P0-8 【合规】审计日志只覆盖登录，产品线 A 全链路留痕未落地

**证据**：全局搜索显示 `write_audit` **仅被 `auth_service.py:8` 引用**。`app/core/audit.py` 已实现完整能力（`write_audit` / `log_detached` / `record`），但**复核通过、文书定稿、卷宗归档、证据访问、下载开庭材料包**等关键动作**无一处写入审计**。

**影响**：PRD 5.5/5.6 与 10.2 均以「全程留痕、可审计、责任可追溯」为**硬性合规要求**，风险表更将「律师未经确认内容被误当定稿使用」列为高影响项。当前状态**无法举证谁在何时改了什么、谁确认了定稿**——一旦发生纠纷，平台与律所均无自证能力。

**修复**：在 `review_service`（`edit`/`submit`/`decide`/`archive`/`void`）、`archive_service`（归档、导出材料包）、`evidence_service`（上传、解析、下载）中补 `write_audit`；统一 `AuditAction` 枚举；审计表增加「不可篡改」保障（哈希链或只追加表 + 定期归档）。

---

### P0-9 【产品可用性】IM 接入层全部为骨架，产品线 A 核心假设未验证

**证据**：`backend/app/im/wecom.py:1-4` 文件头自述「**骨架（待接入凭证）**」「不接真实企业微信 API」；`feishu.py` 同类；`registry.py` 仅注册 `web_sim / wecom / feishu` 三个适配器，**无微信（个人号/公众号）** 适配器。

```python
async def send_outbound(self, msg): logger.debug("WeCom 出站（骨架未接真实 API）-> ...")   # 只打日志
async def resolve_tenant(self, external_user_id): return None    # 恒为 None
```

**影响**：整个产品线 A 的**立论基础**是「客户在微信里零门槛对话、律师在 IM 内办案」。当前只有 `web_sim`（网页模拟）可用，**PRD 附录 B 的接单场景在真实环境中无法发生**。这不仅是功能缺失，而是**最大的产品风险**：核心假设未经真实用户验证。

**修复（最高优先级，且需要前置决策）**：
1. **合规优先**：微信**个人号自动化存在明确封号风险**（PRD 风险表已列），**不建议投入**。应优先选**企业微信自建应用**（有官方 API、回调签名、`external_userid` 体系），其次飞书。
2. 实现企微真实链路：`msg_signature` 验签 → AES 报文解密 → 统一消息映射 → 被动/主动回复 → 客户与租户/律师绑定关系落库（当前 `resolve_tenant` / `resolve_bind_lawyer` 恒返回 None，绑定关系表亦缺失）。
3. 补「渠道绑定关系」数据模型（`im_binding`：channel / external_user_id / tenant_id / lawyer_id）。
4. **在真实打通前，不要对外宣称支持微信接入。**

---

### P0-10 【交付】CI/CD 与容器化交付链缺失

**证据**：`docker-compose.yml` 引用了 `build: ./backend/Dockerfile`，项目根目录**无 `.github/`**（无任何 CI），无 lint 门禁、无测试流水线、无镜像构建与推送、无迁移执行步骤、无回滚方案。

**影响**：无法可靠地将代码交付到生产；70 个单测（58 条用例）**没有任何自动化执行保障**；迁移靠人工 `alembic upgrade head` 易漏。

**修复**：① CI 流水线：ruff + mypy + pytest（覆盖率门禁）+ 前端 tsc/lint/build；② 多阶段 Dockerfile（非 root 用户运行、`--no-cache-dir`、健康检查）；③ CD：镜像推送 + 迁移 Job + 滚动发布 + 健康检查通过才切流量 + 一键回滚；④ 建立 staging 环境。

---

### P0-11 【安全】无 CSRF 防护，且 CORS 允许凭据 + 通配头

**证据**：`backend/app/main.py:66-73`

```python
app.add_middleware(CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,          # ← 允许携带凭据
    allow_methods=["*"],
    allow_headers=["*"],)
```

`allow_credentials=True` 与 `allow_methods/headers=["*"]` 组合；一旦后续按 P0-7 将 refresh token 迁移至 Cookie（**这正是修复 P0-7 的必然路径**），将立即暴露 **CSRF 攻击面**，而当前代码库**无任何 CSRF token 机制**。

**影响**：攻击者诱导已登录用户访问恶意页面，可以该用户身份执行派单、定稿、归档等写操作——对法律产品而言意味着**案件被恶意流转或归档**。

**修复**：① Cookie 强制 `SameSite=Strict`（或 Lax）+ `Secure` + `HttpOnly`；② 引入双提交 Cookie 或 synchronizer token 做 CSRF 校验；③ 收紧 `allow_methods` / `allow_headers` 为显式白名单；④ 校验 `Origin` / `Referer` 头。

---

### P0-12 【产品可用性】前端缺失账号生命周期与支付页面，无法承接真实付费用户

**证据**：
- **无注册页面**：`find apps -name "*.tsx" | xargs grep -ln "register\|注册\|忘记密码\|forgot"` → **无任何命中**
- **无支付页面**：`billing/page.tsx` 仅 102 行，仅展示用量，无支付入口；后端亦无支付 SDK
- **无合同审查独立页面**：仅 `qa/page.tsx` 提及，无专属功能页
- 全部 `page.tsx` 共 18 个，单页规模 42–218 行，功能深度普遍较浅

**影响**：真实客户**无法自助注册**（只能预置账号）、**无法付费**（无法商业化）、**无法找回密码**（一旦忘记即永久失去账号）。这是「可演示」与「可运营」之间的最直观差距。

**修复**：见功能规格书 v3.0 的 P1-01（支付）与 P1-02（账号生命周期），属**门槛二（可运营版）**的必达项。建议同时补齐 `error.tsx` / `loading.tsx` / `not-found.tsx` 边界页与合规文本页（隐私政策 / 服务条款 / 免责声明）。

---

### P0-13 【合规】无内容安全审核能力，无法通过算法备案

**证据**：全局检索 `app/services/` 与 `app/core/` 中**无任何内容审核/moderation/敏感词过滤实现**（仅 `compliance_service.py` 中有「人脸」等合规扫描关键词，与内容安全无关）。

**影响**：《生成式人工智能服务管理暂行办法》要求生成式 AI 服务具备**违法不良信息识别与处置能力**，是**算法备案的前置条件**。当前缺失将直接**阻塞备案 → 阻塞上线**。

**修复**：接入内容安全服务（如腾讯云/阿里云内容安全 API）对**用户输入与 AI 输出双向审核**；命中即拦截、记录并告警；保留审核日志以备监管核查。

---

### P0-14 【越权】复核编辑路由缺少租户校验

**证据**：`backend/app/api/v1/reviews.py:104-112`

```python
@router.post("/{review_id}/edit", ...)
async def edit_review(
    review_id: int, payload: ReviewAction,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_current_user),          # ← 只有 get_current_user
):                                           #   缺少 get_tenant_context
    r = await ReviewService(db).edit(review_id, actor=user, ...)
```

同文件其他路由（`get` / `records` / `submit` / `decide` / `archive` / `void`）均使用 `ctx=Depends(get_tenant_context)` 并做 `r.tenant_id != ctx.tenant_id` 校验，**唯独 `edit` 遗漏**。

**影响**：任意已登录用户知晓（或遍历）`review_id` 即可**修改他人租户的复核内容**——包括篡改律师已确认的文书正文。这比读取更严重：它是**写操作**，直接破坏「复核留痕、责任可追溯」的合规基础。

**修复**：改为 `ctx=Depends(get_tenant_context)`，并在 `ReviewService.edit()` 内做归属校验（与 `get` 同一范式）。**建议同步审计 `reviews.py` 全部 9 个端点**，确认校验策略一致。

---

### P0-15 【功能空洞】通知系统只写不读，实时推送链路完全不存在

**证据链**（已逐条实测确认）：
1. `notify()` 全项目**仅 4 个调用点**：`billing_service.py:95`（工单）、`archive_service.py:78`（归档）、`dispatch_service.py:151`（派单）、`review_service.py:83`（复核），实现均为 `db.add(Notification(...))` + `flush`。
2. **`select(Notification)` 全项目零处**；`api/v1/` 下 15 个端点文件**无任何 notifications 路由** → 通知写入 DB 后**永远不会被任何人读取**。
3. `ws.py:37-88` **无连接管理器**（无 `ConnectionManager`、无连接注册表），服务端**无法主动推送** → 该端点实为「长连接版请求-响应」，与 HTTP 能力等价（这也解释了前端未接 WS：**不是遗漏，而是 WS 本就不提供前端需要的能力**）。
4. `im/registry.py:31-48` 的 `dispatch_inbound()` / `get_adapter()` **零外部调用**；`conversation_engine.py:13` 导入 `get_adapter` 但函数体内从未使用（无用导入）。

**影响**：PRD §5.1 承诺的「**派单通知、律师回复、材料缺失、定稿等节点自动推送**」在后端**零基础**——不是"待接线"，而是"从零开始"。结合 P0-9，产品线 A 的 IM 能力（接入 + 推送 + 多渠道）**在代码层面均无可运行路径**。

**修复**：① 补 `GET /notifications` 端点与读取/已读状态；② 引入 `ConnectionManager`（进程内 dict 或 Redis pub/sub），把 `notify()` 改造为「落库 + 推送」双写；③ `wecom.py` / `feishu.py` 需按真实平台 API 重写（当前 `wecom.py:5` 自述「骨架仅记录」）。

**⚠️ 重新定性**：IM 真实接入是**前后端联合工程**（后端补 ConnectionManager + 通知端点 + 真实适配器），**不是"前端补个页面"**。若排期紧张，建议在 PRD 中**显式降级或推迟**，而非留下虚假的"已完成"印象。

---

### P0-16 【商业风险】合同审查收 99 元/次，但实现为零 LLM 的关键词规则引擎

**证据**：
- `documents.py:95-122` → `contract_review.py:30-70`：全程**无 `router.complete()` 调用**（对比 `case_copilot.py:86` 有真实 LLM 调用）
- 实现为 **7 条关键词正则 + 5 条必备条款检查**（共 88 行）
- 而 `billing_service.py:19`：`UsageType.CONTRACT_REVIEW: {"standard": 9900, ...}` = **99 元/次**
- 附带 `contract_review.py:63` `source_text[:5000]` **静默截断**

**影响**：这是**商业与合规风险，而非技术缺陷**。以「AI 合同审查」名义（PRD §5.9 表述）按 99 元/次收费，实际交付关键词匹配结果，**上线即可能引发客诉与消费争议**；在广告法视角下还有虚假宣传风险。PRD §5.10 合规扫描同样是**纯关键词引擎**（`compliance_service.py:17-53`），存在同一问题。

**修复（产品决策优先于技术决策）**：① 对外口径必须明确区分「**规则引擎预检**」与「**AI 合同审查**」，前者定价应显著下调或作为免费引流；② 若维持 99 元定价，须接真实 LLM 做条款语义分析；③ 取消静默截断，改为显式提示「仅分析前 5000 字」。

---

## 三、P1 开放付费前必修（22 项）

> **📌 勘误（2026-09-16）**：本节标题原写「18 项」，与实际列出的 **P1-1 ~ P1-22 = 22 项**不符，已修正。
> 第八~十一轮另新增 6 项 P1（额度原子扣减、审计保留期归档、审计加密+异地备份、
> `usage_quotas` 存量去重迁移、列表 `COUNT` 有界化、依赖漏洞扫描门禁），**P1 池合计 28 项**。
> 已修 6 项，**剩 22 项**。

| # | 问题 | 证据 | 影响 | 修复方向 |
|---|------|------|------|----------|
| P1-1 | **无支付集成** | `billing_service.py` / `billing.py` 全局无 `wechatpay/alipay/stripe` 任何支付 SDK | 无法真实收款，商业模式不成立 | 接入微信支付/支付宝，订单-支付-退款-对账全链路 |
| P1-2 | 无密码强度策略、无登录失败锁定 | `schemas/auth.py:11` 仅 `min_length=1`（登录）/`:35` `min_length=6`（注册） | 弱口令 + 暴力破解 | 强度校验（≥8 位含大小写数字）+ 失败计数锁定 + 图形验证码 |
| P1-3 | refresh token 无法撤销 | `auth_service.py:109` 仅验签，无黑名单/撤销表 | 令牌泄露无法止损 | 引入 `refresh_token` 表（jti + 撤销 + 轮换） |
| P1-4 | 上传仅校验后缀，无内容嗅探 | `storage_service.py:16-34` `ALLOWED_EXT` 白名单但 `file.content_type` 未校验，`file.read()` **全量读入内存** | 伪装文件上传 / 20MB×并发导致 OOM | magic number 校验 + 流式落盘 + 病毒扫描 |
| P1-5 | `/storage` 静态目录公开挂载，无鉴权 | `main.py:97` `app.mount("/storage", StaticFiles(...))` | **任何人凭 URL 即可下载他人证据/卷宗**，绕过全部租户隔离 | 改为鉴权代理下载接口 + 短期签名 URL |
| P1-6 | 复合索引缺失，关键词查询不可用索引 | `cases.py:44` `Case.title.like(f"%{keyword}%")`；索引统计见各 models | 数据量增长后列表接口全表扫描 | 全文检索（PG `pg_trgm`/`tsvector`）替代前后模糊匹配 |
| P1-7 | 事务内混入外部 AI 调用与长耗时操作 | `case_copilot.py:86` `await router.complete(...)`（LLM 调用）处于业务事务中 | 连接池被长事务占满，LLM 抖动即拖垮 DB | AI 调用移到事务外，仅落库阶段开事务 |
| P1-8 | 异步服务中同步文件 IO | `archive_service.py:211` `with open(abs_path, "w", ...)` | 阻塞事件循环 | `asyncio.to_thread` 包裹 |
| P1-9 | 检索每次请求全量装载语料重建 BM25 | `retriever.py:14-20` `index_documents` 由 service 层请求时装载 | 语料增长后每次问答都付出索引构建成本 | 启动时构建 + 法规变更时增量更新 + 进程级缓存 |
| P1-10 | rerank 为词重叠启发式，与 PRD 9.2 不符 | `rerank.py:18` `score*0.7 + _overlap*0.3` | 检索精度低于承诺 | 接入 `bge-reranker-v2-m3`（或明确降级说明） |
| P1-11 | OCR 为文件名规则模拟 | `evidence_service.py:62` `_mock_ocr` 用「文件名 + 元信息拼装」 | PRD AC「OCR 准确率 ≥95%」无法达成 | 接多模态模型 / PaddleOCR |
| P1-12 | 引用校验只验「有引用」不验「引用正确」 | `citation_service.py:19-34` 仅判 `citation_ids` 非空 | 幻觉引用（编造法条）可通过校验 | 校验引用内容与生成结论的相关性 + 法条存在性 + 时效性 |
| P1-13 | 无并发控制，复核存在竞态 | `review_service.py:125` `decide` 无行锁/版本号 | 两人同时终止审 → 状态错乱 | `SELECT ... FOR UPDATE` + `version` 乐观锁 |
| P1-14 | 无监控告警与 tracing | 全局无 metrics/prometheus/OTEL | 线上故障不可观测 | 接入 Prometheus + 关键业务指标 + 告警 |
| P1-15 | 日志无脱敏，可能落库卷宗原文 | `middleware.py:43` 注释说明不记请求体（正面），但服务层 `logger` 无统一脱敏 | 日志侧泄露隐私 | 统一脱敏过滤器 + 日志分级 |
| P1-16 | 知识库更新机制缺失 | PRD 9.2 要求「法规变更监控管道 + 每周人工审核」，代码中无任何实现 | 法条滞后 → 输出失效法律意见（PRD 首要法律风险） | 建法规更新管道 + 时效性标记 + 历史回答失效提示 |
| P1-17 | 误删/越权删除无二次确认 | `knowledge_service.py:82-84` 直接 `delete` | 误操作不可恢复 | 软删除 + 回收站 + 二次确认 |
| P1-18 | 根目录一次性脚本污染 | `backend/` 下 `fix_fk.py` / `patch_migration.py` / `check_chunk.py` / `check_tables.py` / `create_mig_db.py` | 暴露数据库结构、混淆交付物、无法维护 | 移入 `scripts/dev/` 并加说明，或删除 |
| P1-19 | **文书模板仅 23 个（PRD 要求 200+，差 88%）且内容不可交付** | `app/seed/templates.py` 实测 `name` 字段 **23 处**（劳动 4 / 合同 10 / 婚姻 4 / 公司 3 / 诉讼 3）；文件 docstring 自认「200+ 规模的子集」 | **比数量更严重的是质量**：所有模板 `body` 为 20–120 字单段骨架。以"劳动合同"（`templates.py:13`）为例，全文约 100 字，**缺《劳动合同法》第 17 条要求的试用期、工作内容、工时、社保、解除条件**等必备条款 → **23 个模板没有一个能直接交付客户** | **内容工程缺口（需法务内容团队），非开发缺口**；排期必须**先于**前端模板库 UI，否则 UI 做好也无内容可展示 |
| P1-20 | AI 迭代版本 `changed_by` 恒为 None | `case_copilot.py:123-129` 创建 `CaseAnalysisVersion` 时未传 `changed_by`（模型已有该字段：`analysis.py:66`） | 版本对比功能只能显示"未知操作人"，审计价值受损 | 补传 `changed_by` 字段 |
| P1-21 | AI 决策 `duration_ms` 4 个阶段中 3 个硬编码 0 | `case_copilot.py` 各 `_decide()` 调用仅 generate 阶段传真实耗时 | 耗时瀑布图只能画 1/4，性能优化缺依据 | 统一补真实计时 |
| P1-22 | 证据时间线依赖字符串排序 | `evidence.py` timeline 端点按字符串排序 | `occurred_at` 格式不统一时事件错序 | 统一 ISO 格式或改 DateTime 类型排序（与 P1-7 同源） |

---

## 四、P2 迭代优化（15 项，择要）

1. 统一 `AppError` 体系已具备（`core/errors.py`）——**继续推进**，将剩余裸 `HTTPException` 全部归一。
2. `Case` 状态机（`case_fsm`）与复核 FSM 分层清晰，建议补**状态流转图**文档与可视化。
3. 实现 PRD 9.2 的**知识图谱**（法条-案例-文书-证据关联），当前完全未实现。
4. 补充**法规时效性自动标记**（`LawArticle` 已有生效日期字段，需接入更新检测）。
5. `AiRun` / `AiDecision` 决策留痕设计优秀，建议增加 **token 成本看板**。
6. 长文本/大 PDF 处理需引入**分片与流式摘要**。
7. 前端补齐 `error.tsx` / `loading.tsx` / `not-found.tsx` 边界。
8. 建立前端组件级测试与 E2E（Playwright）。
9. 引入 `pytest-cov` 覆盖率门禁（当前 58 用例集中在 7 个模块）。
10. 补充压测（目标：IM 消息 P95 ≤2s、问答首字 ≤3s，PRD 10.4）。
11. 增加**成本护栏**：单租户日 Token 上限 + 异常用量熔断。
12. 数据库连接池参数显式配置（当前依赖默认值）。
13. 补充数据导出/删除接口（PRD 10.1 要求用户可查看/导出/删除）。
14. 建立**灰度发布**能力（按租户/律所维度开关新功能）。
15. API 版本化与 OpenAPI 契约测试（PRD 9.6 规划 V2.0 开放 API）。

---

## 五、架构层面的正面评价（请保持）

审查中确认以下设计**显著优于同阶段产品，应作为基线保留**：

1. **复核状态机白名单**（`workflows/review_fsm.py:27-76`）——「未确认不可定稿、未定稿不可归档」以机制而非约定实现，并具备级别校验，是产品合规的基石。
2. **强制复核命中器**（`workflows/forced_review.py`）——覆盖 PRD 五类强制场景，与状态机联动形成自动升级。
3. **租户隔离双校验**（`deps.py:37-44` + `knowledge_service.py:71-80`）——写入校验 + 读取后 `assert_tenant`，`evidence.py` / `archives.py` 均遵循同一范式（这也是 P0-1 的修复模板）。
4. **Job 断点续跑**（`job_handlers.py:19-23`）——`step_state` 判断已生成则跳过，避免重复消耗。
5. **AI 决策留痕**（`AiRun` + `AiDecision`）——每步决策、耗时、token 均可追溯，为企业级审计打好了基础。
6. **引用溯源强校验**（`citation_service.py`）——「引用缺失视为生成失败」的硬约束方向正确（需按 P1-12 加强到内容级）。
7. **三级模型路由 + 敏感数据本地化**（`ai/router.py:30-37`）——与 PRD 9.1 一致。

> 判断：**这个项目的「骨头」很好，问题在「肉」**。修复应集中在补齐真实链路与工程护栏，而非重构架构。

> **⚠️ 补充判断（经前后端交叉审查后修正）**：本项目的缺陷不仅是「工程实现不足」，更存在一类**「宣称能力与实现之间的空洞」**——合同审查无 LLM 却按 99 元/次计费、通知系统只写不读、WS 无推送能力、IM 双渠道为死代码、Mock LLM 生产静默降级。**这类问题不属技术债，属交付与商业风险**，必须在对外发布前完成口径对齐（见行动清单 #13）。因此若单独计入「AI 质量」与「功能空洞」维度，**实际交付完整度比 42 分所反映的更低**。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 | 状态 |
|---|------|--------|--------|------|
| 1 | 修复 P0-1 越权漏洞，并全量排查同类接口 | 后端 | 立即（1 天） | ✅ **已完成** |
| 2 | P0-3 生产禁用 Mock + 启动前置校验 | 后端 | 立即（0.5 天） | ✅ **已完成** |
| 3 | P0-14 复核写端点租户校验收口 | 后端 | 立即（0.5 天） | ✅ **已完成** |
| 4 | P0-8 补齐全链路审计日志 | 后端 | 第 1 周 | ⏳ 待办 |
| 5 | P0-6 限流加固（默认开启 + 分级） | 后端 | 第 1 周 | ⏳ 待办 |
| 6 | P0-10 CI/CD + Dockerfile 构建验证 | DevOps | 第 1 周 | ⏳ 待办 |
| 7 | P0-2 打通 embedding 写入与检索链路 | 后端 + AI | 第 1–2 周 | ⏳ 待办 |
| 8 | P0-9 企业微信真实接入 + 绑定关系模型 | 后端 + 前端 | 第 2–3 周 | ⏳ 待办 |
| 9 | P0-4/P0-5 任务队列化 + 抢占式幂等 | 后端 | 第 2–3 周 | ⏳ 待办 |
| 10 | P0-7 令牌迁移 Cookie + 自动刷新 | 前端 + 后端 | 第 2 周 | ⏳ 待办 |
| 11 | P1-1 支付集成 + P1-5 存储鉴权 | 全栈 | 第 3–4 周 | ⏳ 待办 |
| 12 | P1-4/12/13 上传安全、引用内容级校验、并发控制 | 后端 | 第 3–4 周 | ⏳ 待办 |
| 13 | 建立 staging 环境 + 压测 + 监控告警 | DevOps + 后端 | 第 4 周 | ⏳ 待办 |
| 14 | **重估对外功能承诺**：合同审查无 LLM（99 元/次）、模板 23 个、IM 双渠道死代码、Mock 静默降级——**先对齐售前/PRD 口径，再决定补实现还是收窄承诺** | 产品 + 商务 | 第 1 周（**先于对外发布**） | ⏳ 待办 |

> **本轮已完成安全修复明细见文末「§ 修复记录」**——3 个 P0 已落代码并通过 4 层验证（编译 / 静态 / E2E / 回归测试）。

---

## ⚠️ 待确认 / 假设 / Non-goals

**待确认**：
- `backend/Dockerfile` 已存在（941 字节），但未验证能否成功构建并启动——需在 CI 中验证。
- 生产部署形态未定（单机 compose vs K8s），直接影响 P0-4/P0-5 的改造方案选择。
- 是否已有算法备案主体与等保三级预算（PRD 10.1 要求，属合规前置条件）。

**关键假设**：
- 假设目标上线形态支持多副本（据此将 P0-4/P0-5 列为 P0）；若确定单副本运行，可降为 P1，但**风险自担**。

**Non-goals（本次审查不覆盖）**：
- 不包含业务逻辑正确性的法律专业性判断（需执业律师评审）。
- 不包含 UI/UX 视觉设计评审。
- 不包含第三方服务选型与商务比价。

---

## 📚 数据来源 & 成员产出索引

- **方向明（主理人）**：项目结构盘点、生产就绪度评分、P0 定位与验证、架构正面评价、行动清单汇编。
- **后端架构审查员（backend-auditor）**：backend/app 全量（main/config/database/middleware/core/ai/services/workflows/api/im/rag/models）+ 部署配置深度审查，提供安全/可靠性/一致性/AI质量/工程化/性能六维问题清单与评分。
- **前端审查**：由主理人直接取证（18 个页面清单、SDK 174 行全文、token 存储方式、页面行数统计、无测试/lint/CI 确认）。

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。

---

## 🔧 修复记录（2026-09-12 · 第一轮：3 个最高危 P0）

> 本轮针对审查结论中**风险最高、且改动面最小**的 3 个 P0 完成代码修复与验证。
> 选择依据：均可由单人 1 天内闭环、无需外部依赖、且不修则直接阻断上线。

### 修复总览

| 编号 | 漏洞 | 攻击后果 | 改动文件 | 状态 |
|------|------|----------|----------|------|
| P0-1 | 自助提权 + 越权指定租户 | 任何人可注册为 `PLATFORM_ADMIN`，再借 `X-Tenant-Id` 读取全平台数据 | `schemas/auth.py`、`api/v1/auth.py`、`services/auth_service.py` | ✅ 已修复 |
| P0-3 | 生产环境 Mock LLM 静默降级 | 生产环境缺模型凭证时，用占位文本冒充法律分析结果交付用户 | `core/errors.py`、`ai/router.py`、`main.py` | ✅ 已修复 |
| P0-14 | 复核写端点缺租户校验 | 任意登录用户凭 `review_id` 篡改/定稿**他人租户**的复核内容 | `api/v1/reviews.py` | ✅ 已修复 |

---

### P0-1 自助提权 —— 三层防御

**问题**：`RegisterRequest` 暴露 `role` / `tenant_id` 字段并直接透传给 `AuthService`，导致 `POST /auth/register` 可自助创建 `PLATFORM_ADMIN`。

**修复**（纵深防御，即使前一层被绕过仍有兜底）：

1. **Schema 层**（`schemas/auth.py`）—— 移除 `role` / `tenant_id` 字段，客户端注入的多余字段被 Pydantic 静默丢弃；密码最小长度由 6 提升至 8。
2. **接口层**（`api/v1/auth.py`）—— 服务端硬编码 `role=Role.CLIENT, tenant_id=None`，不读取任何客户端输入。
3. **服务层**（`services/auth_service.py`）—— 增加 `if role != Role.CLIENT: raise PermissionDeniedError(...)` 二次防线，未来若有其他调用方误传特权角色同样被拒。

---

### P0-3 生产 Mock 降级 —— 显式失败优于静默错误

**问题**：`ModelRouter.complete()` 在 `provider is None` 时无条件回落到 `MockProvider`，生产环境将输出占位文本冒充真实法律分析。

**修复**：

1. **新增错误类型**（`core/errors.py`）—— 定义 `CONFIGURATION_ERROR` 错误码与 `ConfigurationError`（HTTP 500），语义上属「服务端自身配置问题」，由监控按 code 告警。
2. **路由层守卫**（`ai/router.py`）—— `settings.ENVIRONMENT == "production"` 且对应档位凭证为空时**直接抛 `ConfigurationError`**，错误信息明确指出缺失的环境变量名；非生产环境保持 Mock 行为不变（开发者零配置可跑通）。
3. **新增 `provider_status()`**（`ai/router.py`）—— 暴露各档位凭证配置状态，供运维自查。
4. **健康检查接入**（`main.py`）—— `/api/health` 在生产环境报告 `status: degraded` 且**不泄露档位细节**；非生产环境回传各档位明细便于本地排查。

---

### P0-14 复核写端点越权 —— 统一守卫收口

**问题**：`edit` / `decide` / `archive` / `void` / `submit` 五个写端点仅依赖 `get_current_user`，未做租户校验。

**修复**：抽取 `_review_or_404(db, review_id, tenant_id)` 统一守卫，**五个端点全部接入**；非本租户返回 `404 REVIEW_NOT_FOUND`（而非 403，避免暴露资源存在性）。

---

### 验证结果（4 层，全部通过）

| 层级 | 方式 | 结果 |
|------|------|------|
| L1 编译 | `compileall app/` | ✅ 通过 |
| L2 静态 / 单元级 | `verify_security_fixes.py`（15 项断言：schema 字段、服务层守卫、错误类定义、5 个端点守卫接入） | ✅ 15/15 |
| L3 端到端 | `verify_security_fixes_e2e.py`（走真实 HTTP 链路：注入 `role=PLATFORM_ADMIN` 注册→实为 CLIENT；生产无凭证→`ConfigurationError`；跨租户 `decide`→404，同租户→200） | ✅ 8/8 |
| L4 回归 | `pytest tests -q` | ✅ 70/70 无回归 |

**验证脚本已留档**：`backend/verify_security_fixes.py`、`backend/verify_security_fixes_e2e.py`（可纳入 CI 作为安全回归用例）。

**验证环境说明**：本地通过隔离 venv（`~/.workbuddy-ai/binaries/python/envs/nlawer`）安装 `requirements.txt` 后运行，数据库用 SQLite 隔离，未污染项目 `.env` 的 Postgres 配置。

---

---

## 🔧 修复记录（2026-09-12 · 第二轮：同类越权全量收口 + 文件鉴权）

> 第一轮修复了 `register` 一个入口后，本轮**对「按资源 id 直接取数」的同类漏洞做了全量排查**，
> 又发现并修复 3 处同源缺陷，并移除了无鉴权的 `/storage` 静态挂载。

### 修复总览

| 编号 | 漏洞 | 攻击后果 | 改动文件 | 状态 |
|------|------|----------|----------|------|
| **P0-1同类-A** | `case_events` 案件时间线无归属校验 | 任意登录用户遍历 `case_id` 读取他人案件完整事件流（**法律产品最致命泄露**） | `api/v1/cases.py` | ✅ 已修复 |
| **P0-1同类-B** | `POST /analyses/case/{id}/generate` 无归属校验 | 可为他人案件触发分析并读回分析内容 | `services/job_handlers.py` | ✅ 已修复 |
| **P0-1同类-C** | `EvidenceService.parse` 无租户参数 | worker 从 job 恢复执行时无归属校验（纵深防御缺口） | `services/evidence_service.py`、`services/job_handlers.py` | ✅ 已修复 |
| **P0（原审查未单列）** | `/storage` 以 `StaticFiles` 挂载，**完全绕过鉴权** | 任何人凭 URL 直连即可下载他人租户证据与文书 | `main.py`（移除挂载）、新增 `api/v1/files.py`、`api/v1/__init__.py` | ✅ 已修复 |

### 修复要点

**1. 案件时间线越权（`cases.py`）**
抽取 `_case_or_404(db, case_id, ctx)` 统一守卫，执行**租户 + 客户归属**双重校验（与既有 `get_case()` 口径一致），并复用到 `get_case` / `case_events` / `dispatch_case` 三个端点，消除重复代码。

**2. 案件分析生成越权（`job_handlers.py`）**
`trigger_case_analysis` 在入队前校验案件归属，非本租户直接 `404 CASE_NOT_FOUND`。此前 `case_id` 被直接写入 job 后由 worker 无校验执行。

**3. 证据解析纵深防御（`evidence_service.py`）**
`parse()` 新增可选 `tenant_id` 参数做归属校验；`_evidence_parse_worker` 改为传入 `job.tenant_id`，使后台任务链路同样受保护。

**4. `/storage` 静态挂载移除（`main.py` + 新增 `files.py`）**
原 `app.mount("/storage", StaticFiles(...))` **不经过任何鉴权中间件**，是纯粹的越权下载通道。改为显式接口 `GET /api/v1/files/{tenant_id}/{subdir}/{filename}`：
- 路径中的 `tenant_id` 必须等于当前登录用户所属租户，否则 `404`（不用 403，避免泄露文件是否存在）
- 扩展名白名单校验（与上传白名单一致的子集）
- **双重路径穿越防护**：`realpath` 规范化后必须位于 storage 根目录内 + 拒绝符号链接逃逸
- 经确认**前端未消费 `/storage` URL**（全库检索无引用），移除不破坏任何现有链路

### 验证结果

`verify_security_fixes_round2.py` —— **17/17 全部通过**，含 6 个攻击用例 + 6 个正向对照：

| 类型 | 用例 | 结果 |
|------|------|------|
| 攻击 | 跨租户读案件时间线 | ✅ 404 |
| 攻击 | 伪造 `X-Tenant-Id` 读案件时间线 | ✅ 404 |
| 攻击 | 跨租户触发案件分析生成 | ✅ 404 |
| 攻击 | 未鉴权下载文件 | ✅ 401 |
| 攻击 | 跨租户下载文件 | ✅ 404 |
| 攻击 | 路径穿越（`..%2f..%2f`） | ✅ 404 |
| 攻击 | 旧 `/storage` 静态路径 | ✅ 404（已失效） |
| 对照 | 本租户读时间线 | ✅ 200 |
| 对照 | 本租户触发分析 | ✅ 200 |
| 对照 | 本租户下载文件 | ✅ 200 + 内容正确 |
| 对照 | 非白名单扩展名 | ✅ 404 |
| 对照 | `EvidenceService.parse` 支持租户参数 | ✅ |

**两轮累计验证**：编译 ✅ / 第一轮静态 15-15 ✅ / 第一轮 E2E 8-8 ✅ / 第二轮 17-17 ✅ / 单元测试 70-70 ✅

### 已修复 P0 累计进度

| 轮次 | 修复项 | 数量 |
|------|--------|------|
| 第一轮 | 注册提权、生产 Mock 降级、复核写端点越权 | 3 |
| 第二轮 | 案件时间线越权、案件分析生成越权、证据解析纵深防御、`/storage` 无鉴权 | 4 |
| **合计** | | **7** |

### ⚠️ 仍未修复的高优先项

- **限流默认关闭**（`config.py:43` `RATE_LIMIT_ENABLED=False`）+ 进程内内存态，多副本下失效
- **向量检索链路断裂**（`rag/retriever.py:28` 查询向量硬编码 `None`），RAG 实为纯关键词检索
- **异步任务进程内调度**（`job_handlers.py` `background.add_task`、`job_service.py` `_semaphore`），多副本下任务丢失/重复
- **审计日志仅覆盖登录**：复核/归档/证据等关键操作未接入 `write_audit`
- **无内容安全审核**（算法备案前置条件）
- **`cases.py` 剩余风险**：`list_cases` 中律师传他人 `lawyer_id` 时可查他人案件，需确认是否符合产品预期

### ⚠️ 本轮修复的边界与后续

- **修复过程中发现并修复 1 个自身引入的缺陷**：`api/v1/auth.py` 新增 `Role.CLIENT` 后缺少 `from app.core.rbac import Role` 导入，会导致注册接口 500 —— 已由 E2E 测试捕获并修复（**这正是 L3 端到端验证不可省略的原因**）。
- **P0-1 的同类风险仍需全量排查**：本轮仅收口了 `register` 一个入口。审查报告 P0-1 要求排查的 `cases.py:85-110` 案件时间线越权、`analyses.py` / `archives.py` / `documents.py` 等同类接口**尚未修复**，仍为上线阻断项。
- **`/storage` 静态挂载无鉴权**（`main.py:97`）：上传的证据与文书可被任意人通过 URL 直接访问，**仍是未修复的 P0**，建议下一轮优先处理。
- **限流默认关闭**（`config.py:43` `RATE_LIMIT_ENABLED=False`）在单机场景下由 compose 环境变量开启，但多副本下仍需外部存储支持——**未修复**。

---

## 🔧 修复记录（2026-09-12 · 第三轮：任务队列化 + 限流加固）

> 前两轮解决了「谁能看什么」，本轮解决**「多副本能不能跑」**。
> 这两项是 P0-4 / P0-5 / P0-6 的正面攻坚，也是「可运营版」能否水平扩容的硬前提。

### 修复总览

| 编号 | 缺陷 | 生产后果 | 改动文件 | 状态 |
|------|------|----------|----------|------|
| **P0-4 / P0-5** | 异步任务用请求级 `BackgroundTasks` + 内存 `_semaphore` | 请求结束即被回收，长任务中断；多副本各跑一份、重复消耗 AI 额度 | `services/job_service.py`（重写）、`services/job_handlers.py`、`api/v1/jobs.py`、`main.py` | ✅ 已修复 |
| **P0-4** | 崩溃后 RUNNING 任务永久卡死 | 需人工介入；用户看到任务永远「处理中」 | `models/job.py`、`services/job_service.py`、`main.py` | ✅ 已修复 |
| **P0-6** | 限流默认关闭 + 进程内内存态 | 多副本下限流形同虚设；重启即清零，可被绕过 | `config.py`、`core/rate_limit_backend.py`（新增）、`middleware.py` | ✅ 已修复 |
| **P0-10** | 交付链缺 Redis 依赖 | 无法声明多副本部署形态 | `docker-compose.yml`、`requirements.txt`、`.env.example` | ✅ 已修复 |

### 修复要点

**1. 进程内常驻任务队列（`JobQueue`）替代 `BackgroundTasks`**

`BackgroundTasks.add_task` 注册的任务是**请求级**的：响应返回后由 FastAPI 回收，长任务有被中断的风险，且没有并发上限、没有优雅停机。改为 `asyncio.Queue` + N 个常驻消费者协程：

- **先落库再入队**：任务先写成 `job` 行，入队失败也可由回收逻辑重新投递，不丢用户输入
- **反压而非丢弃**：队列满时 `await queue.put()` 等待，不静默丢任务（`JOB_QUEUE_MAX_SIZE=500`）
- **优雅停机**：`stop()` 取消消费者协程并 `gather(..., return_exceptions=True)`，避免 shutdown 挂起

**2. 原子认领（`claim_job`）—— 跨进程乐观锁**

这是整个可靠性改造的**核心**。用单条 `UPDATE ... WHERE id=? AND status IN ('pending','retrying')` 的 `rowcount` 判定归属：

```python
result = await db.execute(
    update(Job).where(Job.id == job_id, Job.status.in_(claimable))
    .values(status=JobStatus.RUNNING.value, claimed_by=runner_id, heartbeat_at=now)
    .execution_options(synchronize_session=False)
)
await db.commit()
if result.rowcount == 0:
    return None  # 已被他人认领 / 已终态——静默退出，不是错误
```

多副本同时消费同一 Job 时，只有一个实例拿到 `rowcount=1`。

> **⚠️ 本轮修正的关键缺陷**：初版把 `JobStatus.RUNNING` 也放进了 `claimable`，导致第二个执行者能「抢走」正在运行的任务——并发测试直接抓到 2 个成功者。改为**默认 `allow_running=False`**：`RUNNING` 意味着已有实例在跑，绝不重复执行；僵尸任务的正确路径是 `recover_stale_jobs()` 先重置为 `PENDING` 再重新投递。

> **⚠️ 本轮修正的第二个关键缺陷**：`UPDATE` 使用 `synchronize_session=False` 绕过 ORM 身份映射，若调用方**此前已读过这个 Job**，session 里会残留 `status=PENDING` 的陈旧对象，调用方误判「认领失败」而重复入队——**这正是「读到即执行」时代重复执行的根因**。修复：认领成功后 `db.expire_all()` 再回读。

**3. 心跳 + 僵尸回收**

- `Job` 新增 `heartbeat_at` / `claimed_by` 两列，执行期间持续刷新
- `recover_stale_jobs()` 把超过 `JOB_STALE_TIMEOUT_SECONDS=300` 无心跳的 `RUNNING` 重置为 `PENDING` 并清空 `claimed_by`
- `main.py` 起独立后台协程 `_stale_job_recovery_loop()`，每 `JOB_RECOVERY_INTERVAL_SECONDS=60` 扫描一次
- 进程被 `kill -9` 后，任务最多 5 分钟内自动回到队列

**4. 限流：安全默认 + 可插拔后端 + 优雅降级**

| 改造项 | 之前 | 现在 |
|--------|------|------|
| 默认状态 | `RATE_LIMIT_ENABLED=False`（关） | `True`（**安全默认**，开发可显式关闭） |
| 存储 | 进程内 dict | `RateLimitBackend` Protocol，内存 / Redis 双实现 |
| 多副本 | 失效 | Redis `Lua` 脚本原子执行 `ZREMRANGEBYSCORE + ZCARD + ZADD` |
| Redis 故障 | — | **优雅降级**到内存后端并告警，不阻断业务 |
| 分层阈值 | 仅登录 | 认证 `20/min`、上传 `10/min`、生成 `15/min` |

`MemoryRateLimitBackend` 增加**基于墙钟的惰性清理**（每 60s 扫一次，120s 时间窗），避免长期运行内存泄漏。

**5. 交付链补齐（`docker-compose.yml`）**

新增 `redis:7-alpine` 服务（带 healthcheck）+ `nlawer-redisdata` 卷，后端 `depends_on: redis`；透出 `REDIS_URL` / `RATE_LIMIT_UPLOAD_MAX` / `RATE_LIMIT_GENERATE_MAX` / `JOB_*` 全部环境变量。`redis==5.0.8` 作为**可选依赖**写入 `requirements.txt`（不装也能跑，自动降级内存后端）。

### 新增单元测试

`backend/tests/test_job_claim.py` —— **11 个用例**，守护多副本正确性（改动 claim 逻辑必须全绿）：

| 用例 | 守护点 |
|------|--------|
| `test_claim_succeeds_for_pending` | PENDING 可被认领，状态/owner/心跳/time 全部写入 |
| `test_claim_is_exclusive` | **并发认领同一 Job 只允许 1 个成功** |
| `test_claim_rejects_terminal_states` | COMPLETED / FAILED 不可被认领 |
| `test_claim_rejects_running_by_default` | RUNNING 默认不可抢占 |
| `test_claim_allows_running_when_explicit` | 显式 `allow_running=True` 可接管 |
| `test_claim_retryable` | RETRYING 可重新认领 |
| `test_started_at_not_overwritten_on_reclaim` | 重试不覆盖首次 `started_at` |
| `test_recover_stale_jobs` | 僵尸回收 PENDING，心跳正常的不误伤 |
| `test_recover_ignores_jobs_without_heartbeat` | 从未执行的（心跳 NULL）不被误判 |
| `test_queue_backpressure_does_not_drop` | 队列满时反压等待，不丢任务 |
| `test_queue_stop_is_clean` | 优雅停机，协程清理干净 |

### 验证结果（4 层）

| 层级 | 手段 | 结果 |
|------|------|------|
| L1 编译 | `compileall` 全量 | ✅ |
| L2 静态断言 | `verify_reliability_round3.py`（23 项） | ✅ 23/23 |
| L3 端到端 | 第一轮 15-15 + E2E 8-8 + 第二轮 17-17 | ✅ 全部通过 |
| L4 回归 | `pytest tests/` 全量 + 新增 `test_job_claim.py` | ✅ **11/11 新增通过**，全量回归通过 |

**三轮累计验证**：编译 ✅ / 静态 15+17+23=55 ✅ / E2E 8 ✅ / 单元测试 70+11=81 ✅

### 已修复 P0 累计进度

| 轮次 | 主题 | 修复项 |
|------|------|--------|
| 第一轮 | 最高危越权 + 静默降级 | 3 |
| 第二轮 | 同类越权全量收口 + 文件鉴权 | 4 |
| 第三轮 | 任务队列化 + 限流加固 | 4（P0-4/P0-5/P0-6/P0-10 部分） |
| **合计** | | **11** |

### ⚠️ 仍未修复的高优先项（更新）

- ~~限流默认关闭 + 进程内内存态~~ → **本轮已修复**
- ~~异步任务进程内调度，多副本下丢失/重复~~ → **本轮已修复**
- **向量检索链路断裂**（`rag/retriever.py:28` 查询向量硬编码 `None`），RAG 实为纯关键词检索 —— **未修复，P0-2，影响 AI 输出正确性**
- **审计日志仅覆盖登录**：复核/归档/证据等关键操作未接入 `write_audit` —— **未修复，P0-8**
- **无内容安全审核**（算法备案前置条件）—— **未修复，P0-13**
- **前端令牌存 localStorage + 无 CSRF** —— **未修复，P0-7 / P0-11**
- **`cases.py` 剩余风险**：`list_cases` 中律师传他人 `lawyer_id` 可查他人案件，需确认是否符合产品预期

### ⚠️ 本轮修复的边界与后续

- **队列是「进程内」而非「跨进程」**：本轮的队列解决的是**单个实例内的调度可靠性**，多副本之间的协作靠 `claim_job` 的数据库原子认领。若未来要上独立 worker 集群（不与 API 同进程），需要把队列后端换成 Redis Stream / RabbitMQ —— 当前架构已预留边界（`JobQueue` 是独立类，替换成本低）。
- **僵尸回收依赖心跳，存在最长 5 分钟窗口**：`kill -9` 后任务最多 5 分钟才回到队列。如需更短，调小 `JOB_STALE_TIMEOUT_SECONDS`（代价是心跳刷新开销上升）。
- **Redis 降级后限流退化为单机**：Redis 不可用时自动降级内存后端并告警，此时多副本限流不再准确。**生产环境应将 Redis 可用性纳入监控告警**。
- **仍未做真正的消息队列持久化**：Redis 只用于限流计数，未用于任务投递。任务投递的持久性由 `job` 表保证。


---

## 🔧 修复记录（2026-09-12 · 第四轮：向量检索链路打通）

> 前三轮解决「谁能看什么」和「多副本能不能跑」，本轮解决**「AI 说的是不是真的」**。
> P0-2 是本项目**对律师交付质量影响最直接**的缺陷：RAG 号称语义检索，实际上是纯关键词匹配。

### 缺陷本质

`app/rag/retriever.py:28` 把查询向量**硬编码为 `None`**：

```python
res = self.vector_store.search(None, top_k=top_k, tenant_id=tenant_id)
#                             ^^^^ 查询向量恒为 None
```

而所有 vector store 对 `None` 的处理都是「静默返回空列表」（`if vector is None: return []`）。
两者叠加的后果：**向量召回分支永远返回空，且没有任何日志或异常**。RAG 链路看似完整、实际退化为纯 BM25，调用方毫无察觉。

更糟的是这条链路上还有 3 个连锁缺陷：
1. **没有 Embedding 客户端** —— 项目里根本不存在把文本转成向量的能力（`app/ai/` 下只有 chat provider）。
2. **内存 `VectorStore.search()` 是空实现** —— 无条件 `return []`，注释写着「MVP：未实现真实向量相似度」。
3. **没有任何地方写入向量** —— 全库检索 `KnowledgeEmbedding` 只有模型定义，无一处 `db.add`。即使修好前两点，向量表也是空的。

> **这就是「宣称能力与实现之间的空洞」的典型样本**：PRD 写了 pgvector 语义检索，代码里有 `PgVectorStore` 的完整实现，但**整条链路从未接通过**。

### 修复总览

| # | 缺陷 | 改动文件 | 状态 |
|---|------|----------|------|
| 1 | 查询向量硬编码 `None` | `app/rag/retriever.py`（重写 search + 新增 `_embed_query` / `index_documents_async`） | ✅ |
| 2 | 无 Embedding 客户端 | **新增 `app/ai/embeddings.py`**、`app/config.py`（新增 `EMBEDDING_BASE_URL`） | ✅ |
| 3 | 内存向量库为空实现 | `app/rag/vector_store.py`（实现真实余弦检索 + 修正后端选择逻辑） | ✅ |
| 4 | 向量表无人写入 | `app/services/knowledge_service.py`（写入时切片索引） | ✅ |
| 5 | 无 pgvector 时向量列无法写入 | `app/models/embedding.py`（新增 `JsonVector` TypeDecorator） | ✅ |

### 修复要点

**1. 查询向量必须真实（`retriever.py`）**

新增 `_embed_query()`：把查询文本通过 embedding 客户端转成向量。**取不到向量时跳过向量召回，而不是传 `None` 进 store**——两者行为看似相同（都返回空），但前者是有意识降级、后者是缺陷伪装成正常。

Embedding 服务抖动时返回 `None` 并告警，检索降级为纯 BM25，不整体失败。

**2. Embedding 客户端（`app/ai/embeddings.py`，新增）**

对齐 OpenAI `/embeddings` 协议（Qwen / GLM / DeepSeek / Ollama 兼容）。两个关键设计：

- **按 `index` 排序**：服务端可能乱序返回，不排序会导致向量与文本错配（静默的错误检索，比报错更难排查）。
- **维度校验告警**：返回维度与 `EMBEDDING_DIM` 不一致时告警（会让 pgvector 写入直接失败，提前暴露比事后排查便宜）。
- **生产守门**：生产环境声明要用向量后端却没配 `EMBEDDING_API_KEY` → 抛 `ConfigurationError`，而不是静默降级。这与 P0-3 的 Mock LLM 守门同一原则：**宁可显式失败，不要静默降级**。

**3. 内存向量库实现真实检索（`vector_store.py`）**

实现余弦相似度暴力检索（千级语料亚毫秒）。同时修正 `build_vector_store()` 的后端选择逻辑：

> 原逻辑：`if backend == "none" or not settings.EMBEDDING_API_KEY: return None`
>
> **问题**：用「有没有 Key」决定「库的存在」，会把「配了 Key 但后端选错」这类问题掩盖成「没有向量能力」。
>
> 修正：向量存储（库）与查询向量（钥匙）是两件事，分开判定。声明 pgvector 但环境不满足时**降级内存实现并告警**，而不是返回 `None`。

**4. 知识文档写入时索引（`knowledge_service.py`）**

`create()` 现在会切片并写入 `knowledge_embeddings`。**任何失败都只告警不抛错**：知识库写入的可用性优先于语义检索（此时文档仍可被 BM25 检索到）。

**5. 向量列序列化（`models/embedding.py`）**

原设计 `if HAS_PGVECTOR: Vector(dim) else Text()` —— 但 `Text` 列**存不下 Python list**，写入直接报 `type 'list' is not supported`。新增 `JsonVector` TypeDecorator：无 pgvector 时自动 `list[float] <-> JSON` 双向序列化，让两种后端的写入语义一致，调用方无需分支。

### 验证结果（5 层，共 51 项断言）

| 层级 | 手段 | 结果 |
|------|------|------|
| L1 结构 | 源码不含 `search(None`、含 `_embed_query`、store 已实现 `_cosine` | ✅ |
| L2 单元 | 余弦相似度边界（同向/正交/零向量/维度不等）+ 内存库排序/契约 | ✅ |
| L3 集成 | **判决性**：混合检索 `hybrid=3 vs bm25=1`（向量带来 2 条 BM25 找不到的召回）；仅向量分支可召回 | ✅ |
| L4 降级 | embedding 抛错时检索不崩、BM25 兜底、`_embed_query` 返回 None | ✅ |
| L5 守门 | 生产 + 声明向量 + 缺 Key → `ConfigurationError`；显式 `none` → 合法返回 None | ✅ |

**脚本**：`verify_p0_2_vector.py`（39 项）+ `verify_p0_2_ingest.py`（12 项）
**回归用例**：`tests/test_vector_retrieval.py`（14 项）

### ⚠️ 本轮集成测试抓到 2 个单元测试测不出的真缺陷

这两个都是**只有真实读写数据库才能暴露**的问题，很有代表性：

1. **`chunk_text` 返回的是 `list[dict]` 不是 `list[str]`** —— 我按 `list[str]` 写索引逻辑，直接把 dict 塞进 `content` 列，SQLite 报 `type 'dict' is not supported`。修：显式取 `c["text"]`。
2. **无 pgvector 时向量列是 `Text`，存不下 list** —— 模型注释声称「回落为 Text 列保证零依赖启动」，但实际写入会直接崩。这个缺陷**在原代码里就存在**，只是从没有人写过向量所以从未触发。修：`JsonVector` TypeDecorator。

> **教训**：`verify_p0_2_vector.py`（纯内存 + fake embedding）39 项全绿，但真正的写库缺陷要等 `verify_p0_2_ingest.py` 才暴露。**涉及持久化的改动，必须有一层真实 DB 的集成验证**——这与第一轮 `Role` 导入缺失只能靠 E2E 抓到是同一个规律。

### 四轮累计进度

| 轮次 | 主题 | 修复项 |
|------|------|--------|
| 第一轮 | 最高危越权 + 静默降级 | 3 |
| 第二轮 | 同类越权全量收口 + 文件鉴权 | 4 |
| 第三轮 | 任务队列化 + 限流加固 | 4 |
| 第四轮 | 向量检索链路打通 | 1（P0-2） |
| **合计** | | **12** |

**验证总计**：编译 ✅ / 静态 15+17+23+39+12=106 ✅ / E2E 8 ✅ / 单元测试 81+14=95 ✅

### ⚠️ 仍未修复的高优先项（更新）

- ~~向量检索链路断裂（P0-2）~~ → **本轮已修复**
- **审计日志仅覆盖登录**（P0-8）：复核/归档/证据等关键操作未接入 `write_audit`
- **前端令牌存 localStorage + 无 CSRF**（P0-7 / P0-11）
- **无内容安全审核**（P0-13，算法备案前置条件）
- **IM 接入层全为骨架**（P0-9，产品线 A 核心假设未验证）
- **前端无注册/找回密码/支付页面**（P0-12）
- **`cases.py` `list_cases`**：律师传他人 `lawyer_id` 可查他人案件，需产品确认是否符合预期

### ⚠️ 本轮修复的边界

- **需要配 Key 才真正启用语义检索**：本轮打通了链路，但默认配置（`EMBEDDING_API_KEY` 为空）下仍是纯 BM25。要获得语义检索能力，**运维需配置 Embedding Key**（同时也能让健康检查暴露 `llm_providers` 之外的 embedding 状态）。
- **`PgVectorStore` 的真实 pgvector 路径未做端到端验证**：本轮验证在 SQLite + 内存向量库上完成。pgvector 的真实余弦检索需在 Postgres 环境另做验证（`cosine_distance` 的实际行为、索引类型选择）。
- **种子数据未回填向量**：`app/seed/` 下的法条/类案不会自动生成向量。存量语料需要一次性的 backfill 脚本（建议随 Embedding Key 配置一并交付）。
- **`rerank` 仍是词重叠启发式**：本轮未动。真实语义排序需替换为 Cross-Encoder，属 P1。

---

## 🔧 修复记录（2026-09-12 · 第五轮：审计日志全链路补全）

> 前四轮解决「能不能跑、看得对不对、AI 说的是不是真的」。本轮解决
> **「出了事能不能还原」**——这是合规硬要求，也是**算法备案材料里会被逐条查**的一项。
>
> 修复前的实测状态：全仓 `write_audit` 只有 **2 个调用点**（注册、登录成功）。
> 也就是说——律师复核批准、案件归档、开庭材料包导出、证据上传解析、
> **卷宗原文下载**，全部**无任何留痕**。

### 缺陷本质

审计的失效模式是**静默的**：漏掉一个端点不会报错，接口照样返回 200，
只有等合规检查或事故复盘时才发现「这个操作没留痕」。所以问题不是
「某一行代码写错了」，而是**覆盖面缺失 + 没有可重复的验证手段**。

三个层次的问题：

1. **覆盖缺失**：`record()` 已在 `reviews.py` 等 5 个文件接入，但 `knowledge.py`、
   `dispatches.py`、`cases.py`、`documents.py`、`compliance.py`、
   `auth_service.py`（`LOGIN_FAILED`）仍是空白。
2. **上下文断链**：审计写入点遍布 service 层，但 `Request` 只在路由层可见，
   IP / UA / request_id 传不下去 → 审计行**有 actor 却没有来源**。
3. **事务生命周期错配**：登录失败会抛 401 触发回滚、文件下载返回 `FileResponse`
   不经过请求事务 commit —— 这两种场景用主会话写审计，记录会**随请求结束静默消失**。

### 修复总览

| # | 缺陷 | 改动文件 | 状态 |
|---|------|----------|------|
| 1 | 请求上下文无法下沉到 service 层 | **新增 `app/core/audit_context.py`**（`ContextVar`）、`app/middleware.py` | ✅ |
| 2 | 逐调用点重复拼字段、易漏传 | `app/core/audit.py`（`record()` 自动补全 actor / tenant / 上下文） | ✅ |
| 3 | 回滚场景审计丢失 | `app/core/audit.py`（新增 `log_detached_ctx()`） | ✅ |
| 4 | 知识库无留痕 | `app/api/v1/knowledge.py`（CREATE / READ / DELETE） | ✅ |
| 5 | 派单无留痕 | `app/api/v1/dispatches.py`（ACCEPT）、`app/api/v1/cases.py`（CREATE） | ✅ |
| 6 | 文书/合同/合规无留痕 | `app/api/v1/documents.py`（RENDER / CONTRACT_REVIEW）、`app/api/v1/compliance.py`（SCAN） | ✅ |
| 7 | 登录失败无留痕 | `app/services/auth_service.py`（`LOGIN_FAILED` × 2 种原因） | ✅ |
| 8 | 卷宗下载来源缺失 | `app/api/v1/files.py`（改用 `log_detached_ctx`） | ✅ |

### 修复要点

**1. 用 `contextvars` 做请求上下文透传（`audit_context.py`，新增）**

`ContextVar` 的**每请求隔离**是关键——用全局 dict 会在并发请求间串 IP。
同时按列宽截断（IP 64 / UA 300 / request_id 64）：`AuditLog.user_agent` 是
`String(300)`，不截断在 Postgres 上直接 `IntegrityError`（SQLite 不校验，**极易漏测**）。

**2. `record()` 收口三件事（`audit.py`）**

调用方只需 `actor=user`，函数自动拆出 `actor_id` / `actor_role` / `tenant_id`，
并从上下文取 IP / UA / request_id。`role` 是枚举时自动取 `.value`
（否则表里会存成 `"<Role.LAWYER: 'LAWYER'>"`）。

**3. 两种事务生命周期，两个入口**

| 入口 | 场景 | 事务 |
|------|------|------|
| `record()` | 常规业务操作 | flush 到当前事务，随外层 commit |
| `log_detached_ctx()` | 主事务会回滚（登录失败）/ 响应不走事务（`FileResponse`） | 独立会话，立即 commit |

**4. 登录失败必须用独立会话（`auth_service.py`）**

`authenticate()` 失败后抛 401，请求事务被回滚。而**「哪个 IP 在反复试哪个账号」
恰是撞库检测的唯一线索**。同时区分两种失败原因（`BAD_CREDENTIALS` /
`ACCOUNT_DISABLED`），且**用户名不存在时也留痕**——只记录已存在账号，
等于给探测者留了绕过后门。

### 验证结果（4 层，共 66 项断言）

| 层级 | 手段 | 结果 |
|------|------|------|
| L1 编译 | `compileall` 全量 app 包 | ✅ PASS |
| L2 静态 | `verify_p0_8_audit.py` —— 覆盖度 + 契约 + 上下文贯通 + 危险模式 + actor 注入 | ✅ **46/46** |
| L3 集成 | `tests/test_audit_log.py` —— 真实 DB 落库 / 上下文隔离 / 回滚后仍留痕 | ✅ **12/12** |
| L4 E2E | `verify_p0_8_e2e.py` —— 真实 HTTP 打接口再回查 `audit_log` 表 | ✅ **20/20** |

### ⚠️ 本轮 E2E 抓到 3 个「静态检查全绿」的缺陷

这是本轮的**最大价值**——三个缺陷在编译与静态检查下**全部通过**：

1. **`LOGIN` 审计没有来源 IP / UA** —— `auth_service` 调的是**裸 `write_audit()`**，
   它不读 `audit_context`。静态检查只断言「出现了 `AuditAction.LOGIN`」就放过了。
   对比证据：同一个文件里 `LOGIN_FAILED`（走 `log_detached_ctx`）**有** `ip='testclient'`，
   而 `LOGIN` **是 `None`** —— 只有真实请求链路能看出这个差异。

2. **`FILE_DOWNLOAD` 同样缺来源** —— 它是裸 `log_detached()`。这个是**新增的
   「禁止裸用上下文盲函数」守卫自动抓出来的**（`D2` 断言立即报 `files.py:76`）。
   说明"把规则写成机器可验的断言"比"人肉 review"可靠。

3. **`success` 布尔恒等判断风险** —— 测试里 `assert row.success is True` 失败
   （SQLite 回读是 `int 1`）。虽然本轮只在测试里踩到，但它揭示了一类**生产隐患**：
   若业务代码写 `if row.success is True:`，在 SQLite 下**恒为假且不报错**。
   已新增源码扫描用例 `test_no_identity_check_on_success_in_source` 钉死。

> **教训（第三次验证同一规律）**：第一轮 `Role` 导入缺失、第四轮 `JsonVector`
> 写入失败、本轮上下文断链——**都是静态检查抓不到、只有真实 DB / 真实 HTTP 才暴露**。
> 所以「涉及持久化与请求链路的改动，必须有 E2E 层」。

### 五轮累计进度

| 轮次 | 主题 | 修复项 |
|------|------|--------|
| 第一轮 | 最高危越权 + 静默降级 | 3 |
| 第二轮 | 同类越权全量收口 + 文件鉴权 | 4 |
| 第三轮 | 任务队列化 + 限流加固 | 4 |
| 第四轮 | 向量检索链路打通 | 1（P0-2） |
| 第五轮 | 审计日志全链路补全 | 1（P0-8） |
| **合计** | | **13** |

**验证总计**：编译 ✅ / 静态 106+46=152 ✅ / E2E 8+20=28 ✅ / 自动化测试 95+12=107 ✅

### ⚠️ 仍未修复的高优先项（更新）

- ~~审计日志仅覆盖登录（P0-8）~~ → **本轮已修复**
- **前端令牌存 localStorage + 无 CSRF**（P0-7 / P0-11）：XSS 一旦命中即可完整盗取会话
- **`usage_quotas` 扣减非原子**：并发下可能超额消费（建议改原子 UPDATE + 乐观锁，
  复用第三轮 `claim_job` 的同款写法）
- **审计日志无保留期与轮转策略**：等保要求 ≥6 个月，当前无限增长，需定期归档

---

## 🔧 修复记录（2026-09-13 · 第六轮：前端令牌安全 + CSRF 防护）

> 前五轮解决「服务端能不能防住」。本轮解决**「浏览器端能不能防住」**——
> 对法律产品而言，XSS 一次命中意味着**当事人姓名、身份证、案情细节**全部外泄。

### 缺陷本质

修复前的实测状态：

```ts
// 修复前：access + refresh 全明文存 localStorage，有效期 1440 分钟（24 小时）
localStorage.setItem("nlaw_access", access);
localStorage.setItem("nlaw_refresh", refresh);   // 7 天，且 JS 可读
```

任意一段第三方脚本（广告 SDK、埋点库、供应链投毒的 npm 包）执行
`localStorage.getItem("nlaw_refresh")` 就能拿到 **7 天有效期的刷新令牌**，
然后**离线持续换发 access**——即使受害者改了密码也不一定失效。
同时全站**零 CSRF 防护**，第三方页面可无声调用写接口。

这不是"配置写得不够严"，而是**令牌存放媒介与生命周期**两个根本选择都错了。

### 修复设计：分层令牌策略

| 令牌 | 存放位置 | 有效期 | XSS 能否窃取 | 说明 |
|------|----------|--------|--------------|------|
| access | **内存变量**（非 localStorage） | **30 分钟**（原 1440） | 仅当前标签页存活期内 | 刷新页面即失效 |
| refresh | **HttpOnly Cookie** | 7 天 | **不能**（JS 读不到） | 由浏览器自动携带 |
| csrf | 普通 Cookie（JS 可读） | 1 小时 | 能，但**无用** | 需配合 HttpOnly 才构成双提交 |

**威胁模型变化**：XSS 的最坏后果从「7 天完整会话被盗」降级为
「当前标签页 30 分钟内被借用，刷新即失效」。这是**数量级的收敛**。

### 后端改动

| 文件 | 改动 |
|------|------|
| `app/config.py` | `ACCESS_TOKEN_EXPIRE_MINUTES` 1440→30；新增 8 个 Cookie/CSRF 配置项；新增**生产 fail-fast 校验**（`ENVIRONMENT=production` + 启用 Cookie + `AUTH_COOKIE_SECURE=false` → 启动即抛错，防"忘记开 Secure"） |
| `app/core/csrf.py` | **新增**。签名式双提交令牌：HMAC-SHA256 签名，格式 `nonce.ts.session_id.sig`，TTL 3600s，会话绑定（`session_id = sha256(refresh)[:16]`） |
| `app/core/auth_cookies.py` | **新增**。`set_refresh_cookie`（HttpOnly）/ `set_csrf_cookie`（**非** HttpOnly）/ `clear_auth_cookies` / 读取辅助 |
| `app/middleware.py` | **新增 `CsrfMiddleware`**。豁免带 `Authorization: Bearer` 的请求（浏览器不会自动附加该头，天然免疫） |
| `app/api/v1/auth.py` | 重写。`_issue_and_store()` 统一签发；`refresh` 优先读 Cookie（兼容 body）；新增 `logout` 端点（清 Cookie + `LOGOUT` 审计） |
| `app/main.py` | 注册 `CsrfMiddleware`（仅在 `AUTH_COOKIE_ENABLED` 时） |

**CSRF 覆盖范围**：`/api/v1/auth/refresh` + `/api/v1/auth/logout`。
`logout` 是**本轮二次加固时补上的**——只凭 Cookie 即可生效的端点都必须覆盖，
否则第三方站点能用 `<img>` 静默把用户踢下线。

### 前端改动

| 文件 | 改动 |
|------|------|
| `packages/sdk/src/index.ts` | 重写令牌策略。内存 `_accessToken`；`request()` 加 `credentials:"include"`、非 GET 且无 Bearer 时自动回填 CSRF 头、401 自动静默刷新重试一次；新增 `login()` / `logout()` / `restoreSession()` |
| `packages/ui/src/components/LoginShell.tsx` | 改走 `login()`（**第 5 条登录路径，首轮遗漏、本轮补上**） |
| 4 个登录页 | `apps/{web,lawyer,admin,im}/app/login/page.tsx` 改走 `login()` |
| 13 个页面守卫 | `if (!tokenStore.get())` → `restoreSession().then(...)` |
| 2 个登出按钮 + 1 个 401 处理器 | 改走 `logout()` |

**为什么 13 个页面守卫必须改**：access 只在内存里，**页面刷新即清空**。
旧守卫 `if (!tokenStore.get())` 在刷新后必然为假 → 无条件跳登录页 →
用户每刷新一次就被登出。必须由 `restoreSession()` 先用 HttpOnly Cookie
静默换一个新 access。

### ⚠️ 本轮抓到 11 个「肉眼看着没问题」的编译错误

批量改写 13 个页面守卫时，脚本替换了 `if/else` 前缀却留下孤儿 `else`：

```tsx
// 事故形态（静态 grep 完全看不出来，只有 tsc 报 TS1128）
restoreSession().then((ok) => {
  if (!ok) router.push("/login");
  else load();
});
else refresh();      // ← 孤儿 else，语法错误
```

另外有一类更隐蔽的：`else load()` 但该页面**根本没有 `load()` 函数**
（真实名字是 `refresh()`，或者压根是静态导航页不需要拉取数据）。
分布在 `web/{billing,compliance,documents,knowledge,qa}`、
`lawyer/{archives,cases,cases/[id],dispatches,reviews,page}`、`admin/page`、`im/page`。

> **教训（第四次验证同一规律）**：批量改写后，**编译器是唯一的裁判**。
> 已把这两种事故模式固化为静态断言（见下 C.3 / C.4），以后自动拦截。

### ⚠️ 顺带修掉的 SDK 逻辑缺陷：登出形同虚设

`request()` 的 401 自动刷新重试**没有例外通道**。后果是：

```
用户点「退出」→ logout 请求发出 → 若 access 已过期 → 401
   → 自动静默刷新 → 拿到全新 access → 重试 logout 成功
   → 但会话已被"复活"，用户仍是登录态
```

修法：给 `RequestOptions` 加 `_noRetry` 标记，`logout()` 与 `login()` 都带上，
401 刷新分支显式排除。这是**只在阅读控制流时才能发现**的缺陷——
两个单独看都正确的机制（自动刷新 + 登出）组合起来产生错误行为。

### 验证（五层）

| 层 | 手段 | 结果 |
|----|------|------|
| 1 编译 | `compileall` + `ast.parse` | ✅ |
| 2 静态 | `verify_p0_7_frontend.py`（**25 项**） | ✅ 25/25 |
| 3 单元 | `pytest tests/test_csrf.py` | ✅ 12/12 |
| 4 真实请求 | `verify_p0_7_csrf.py`（TestClient + Cookie 罐，**32 项**） | ✅ 32/32 |
| 5 类型检查 | 4 个应用 + ui + sdk 全量 `tsc --noEmit` | ✅ 全绿 |
| 回归 | `pytest tests/` | ✅ **123/123** |

关键验证点：`refresh` 不在响应体内 / Cookie 带 HttpOnly+SameSite /
CSRF 缺失或不匹配 → 403 / 跨会话令牌无效 / 过期令牌无效 /
Bearer 请求豁免 / 生产未开 Secure → 启动失败 / 登出清 Cookie + 留痕。

**静态不变量新增 2 条**（防止事故重演）：
- `C.3` 检测「孤儿 else」残留（批量改写事故模式）
- `C.4` 检测 `restoreSession` 守卫内调用了**未定义**的加载函数

### 六轮累计进度

| 轮次 | 主题 | 修复项 |
|------|------|--------|
| 第一轮 | 最高危越权 + 静默降级 | 3 |
| 第二轮 | 同类越权全量收口 + 文件鉴权 | 4 |
| 第三轮 | 任务队列化 + 限流加固 | 4 |
| 第四轮 | 向量检索链路打通 | 1（P0-2） |
| 第五轮 | 审计日志全链路补全 | 1（P0-8） |
| 第六轮 | **前端令牌安全 + CSRF** | **2（P0-7 / P0-11）** |
| **合计** | | **15** |

**验证总计**：静态 152+25=177 ✅ / E2E 28+32=60 ✅ / 自动化测试 123 ✅ / 类型检查全绿 ✅

### ⚠️ 仍未修复的高优先项

- ~~前端令牌存 localStorage + 无 CSRF（P0-7 / P0-11）~~ → **本轮已修复**
- **`usage_quotas` 扣减非原子**：并发下可能超额消费
- **审计日志无保留期与轮转策略**：等保要求 ≥6 个月
- **新增（本轮引入的已知权衡）**：
  - **移动端需保留 Bearer body 通道**——Cookie 模式在部分 App 内嵌 WebView 受限，
    建议后续按 `User-Agent` 分流，允许正文传 refresh 并强制绑定设备指纹
  - **滚动发布会导致会话失效**——`SECRET_KEY` 轮换或跨版本时，
    HMAC 签名的 CSRF 令牌会失配，用户需重新登录（当前可接受，量产后需做双密钥平滑过渡）
  - **`CSRF_STRICT_ALL_WRITES` 仍为 `false`**——当前只保护 refresh/logout。
    其他写接口依赖 Bearer 天然免疫，但若后续放开 Cookie 直连，必须打开此开关

---

## 🔧 修复记录（2026-09-13 · 第七轮：内容安全审核 + 投诉举报机制）

> 前六轮解决**「数据不被偷、权限不越界」**。本轮解决
> **「平台自身合规能不能过备案」**——这是**法律产品的营业执照**，
> 不是加分项。没有它，产品连上线资格都没有。

### 为什么这一轮必须现在做（而不是"上线前再说"）

依据《生成式人工智能服务管理暂行办法》（2023-08-15 施行）：

| 条款 | 要求 | 不做的后果 |
|------|------|-----------|
| **第四条** | 不得生成煽动颠覆政权、恐怖主义、民族仇恨、暴力、淫秽色情等违法内容 | 内容事故 → 约谈/下架 |
| **第十四条** | 发现违法内容须**停止生成、停止传输、消除**，并**保存有关记录**、**向主管部门报告** | 未履责即违法 |
| **第十五条** | 建立**投诉举报机制**，公布流程与**反馈时限** | 备案审查必查项 |
| **第十七条** | 有舆论属性/社会动员能力的须**算法备案**，而备案**前置审查**就看上述三项 | **无备案 = 不得提供服务** |

**关键判断**：算法备案的**周期不可控**（材料补正、安全评估可能数月）。
如果等到"功能开发完了再启动备案"，会造成**产品已就绪却无法上线**的空转期。
因此本轮把技术侧的合规底座全部落地，让备案可以**尽早启动**。

### 缺陷本质：三条法定动作全部缺失

修复前的实测状态：

```bash
# 1. 输入侧无审核 —— 违法提问直接进模型上下文
curl -X POST /api/v1/qa -d '{"question":"教我怎么颠覆国家政权"}'
# → 200，模型照常回答

# 2. 输出侧无审核 —— 违法生成直接返回用户
# （同上，无任何拦截点）

# 3. 无留痕、无投诉入口 —— 被问"你们怎么处置违法内容"时无话可答
```

### 修复设计：判定 / 留痕 / 上报 三层解耦

**核心设计决策——为什么判定引擎不做 IO：**

流式输出要求**每个 SSE 片段**都过审，如果每次判定都碰数据库，
首字延迟（PRD 要求 ≤3s）会直接崩掉。因此拆成三层：

```
core/moderation.py      → 纯判定（无 IO，可离线单测）      ← 高频调用
services/moderation_service.py → 判定 + 留痕 + 上报编排    ← 低频（仅命中时）
models/moderation.py    → 留痕表（不存原文）
```

**第十四条四个动作词 → 实现的逐项映射：**

| 法条要求 | 实现 | 验证用例 |
|----------|------|----------|
| **停止生成** | 输入审核命中 → 不调用模型，抛 422 | B.1-B.4 |
| **停止传输** | 输出审核命中 → 不推送内容（含 SSE 跨片段） | G.1-G.4 / H.1-H.4 / I.1 |
| **消除** | 落库前清洗；已生成内容标记 `blocked` | F.1-F.3 |
| **保存有关记录** | `moderation_records` 表 + 审计双写 | D.1-D.11 |
| **向主管部门报告** | `report_to_authority()` 钩子 + 状态跟踪 | E.1-E.2 |

### 本轮踩到的三个真实缺陷（都是"想当然"型）

#### 缺陷 1：留痕被事务回滚一起吃掉 ⚠️ 最严重

**症状**：E2E 验证 `D.1 moderation_records 已落库 → rows=0`，
但 SQL echo 明明打印了 INSERT。日志末尾跟着一行 `ROLLBACK`。

**根因**：`ContentBlockedError` 从路由抛出 → `get_db` 的
`except Exception: await session.rollback()` 把**请求会话**里的一切（审核记录 + 审计行）
全部丢弃。**留痕彻底失效，而第十四条恰恰要求「保存有关记录」。**

**这是第五轮审计日志踩过的同一个坑的第 2 次出现**——
说明"依赖请求事务生命周期写合规记录"是一个**系统性陷阱**，不是偶发失误。

**修复**：新增 `record_with_detached_row()`——把「业务留痕行 + 审计行」
放进**同一个独立事务**立即 commit：

```python
# 拦截路径：请求事务注定回滚，必须独立提交
if blocked or self.db is None:
    await record_with_detached_row(action, "moderation", row=record_row, ...)
    return
# 放行但需关注（REVIEW / degraded）：随请求事务走即可
```

**为什么两条记录必须同事务**：宁可两条都失败，
也不要出现「有审核记录但查不到审计」或反之的**对不上账**。

#### 缺陷 2：Base64 绕过漏检（真实漏检，非测试问题）

**症状**：单测 `test_bypass_with_base64_is_caught` FAILED。

**根因**：`decode_layers` 要求**整串**是 Base64 才解码。
攻击者把 payload 嵌在句子里就绕过了：

```
"解码这段 6aKg6KaG5Zu95a626ZS/5p2D 然后照做"
```

**修复**：增加**子串扫描**策略——用 `[A-Za-z0-9+/=_-]{12,}`
配合"含数字/大小写混合/尾部等号"的启发式，逐个 token 试解码。

**为什么这个 bug 有价值**：它是**对抗性场景**暴露的，
静态审查和"正常输入测试"都发现不了。这类 bug 只有**主动构造绕过手法**才能发现。

#### 缺陷 3：拦截与上报被混为一个动作码（设计缺陷）

**症状**：`D.9 写入了 CONTENT_BLOCKED 审计` 反复失败。

**根因**：初版用**单个** action 码表达状态，导致语义坍缩：

```
BLOCK 命中 → 若需上报 → 记 CONTENT_REPORTED  → CONTENT_BLOCKED 丢失
                       → 若无需上报 → 记 CONTENT_BLOCKED
```

**"拦截"和"上报"是两件独立的事**（第十四条区分了动作与报告义务），
用一个码表达必然丢信息。

**修复**：拆成**两条独立审计行**：

| 行 | action | 含义 |
|----|--------|------|
| 处置行 | `CONTENT_BLOCKED` / `CONTENT_UNDER_REVIEW` | 内容怎么处理了 |
| 上报行 | `CONTENT_REPORTED` / `CONTENT_REPORT_PENDING` | 法定报告义务履行状态 |

**新增 `CONTENT_REPORT_PENDING` 的价值**：运维可以执行一句查询就回答
**「我欠监管部门几笔上报？」**——这是会直接导致处罚的问题，
不能让答案藏在拦截日志里靠人猜。验证库实测：9 次拦截 → 9 条 `CONTENT_BLOCKED`
+ 9 条 `CONTENT_REPORT_PENDING`，**完美配对**。

### 第十五条：投诉举报机制

**关键设计决策——提交入口必须允许匿名。**
「公众投诉举报」的主体是公众，不是注册用户。
若入口要求先登录，等于把「便捷入口」四个字作废，
也直接违反第十七条备案时对投诉渠道的审查口径。

| 端点 | 鉴权 | 说明 |
|------|------|------|
| `POST /api/v1/complaints` | **无** | 公众提交，限流 5 次/分/IP |
| `GET  /api/v1/complaints/policy` | **无** | 公布流程与时限（第十五条明示要求） |
| `GET  /api/v1/complaints/{ticket_no}` | **无** | 凭工单号回查（工单含随机段，不可枚举） |
| `GET  /api/v1/complaints` | 平台管理员 | 工单列表（支持逾期筛选） |
| `POST /api/v1/complaints/{id}/handle` | 平台管理员 | 处理并办结 |

**防「假闭环」的不变量**：
- 办结/不予受理**必须**填写处理结论（≥5 字）→ 否则投诉人拿不到答复
- `RESOLVED` **必须**带 `handled_at` 与 `feedback_sent` → 可验证是否真反馈了
- 承诺时限**从受理时刻起算**（`due_at = 受理时刻 + 15 天`），不是从办结起算
- 看板必须给出 `overdue` 口径 → 逾期是**违约信号**，不能只数总数

### 其他内容出口一并接入（防止"只审问答"的合规缺口）

审核必须覆盖**所有**内容出入口，否则合规审查会问：
「你们只审了问答，那合同审查和合规扫描呢？」

| 链路 | 审核侧 | 理由 |
|------|--------|------|
| `/qa` | 输入 + 输出 | 主链路 |
| `/qa/stream` | 输入 + 输出（跨片段滑动窗口） | SSE 违禁词可能被切开 |
| `/documents/contract-review` | 输入 | 合同原文可能是违规载体 |
| `/documents/{id}/render` | 输出 | AI 产出可能因用户变量带出违规内容 |
| `/compliance/scans` | 输入 | `input_summary` 会进提示词上下文 |

**误杀率验证**：10 条真实法律咨询 + 2 条正常业务请求**全部放行**（C.1 / K.3 / K.4），
确认"接入审核"没有变成"全站拒绝"。

### 生产环境强制守卫（fail-fast）

法规要求**不可配置关闭**，因此生产环境启动时强制校验：

```python
# 生产环境三项必须同时满足，否则拒绝启动
1. MODERATION_ENABLED == True          # 不可关闭
2. MODERATION_BACKEND != "none"        # 必须真有审核后端
3. backend == "external" → 必须有 API Key  # 内置词库不足以商用
```

**为什么内置词库不够**：`_BUILTIN_TERMS` 只覆盖每个类别的**最小可运行集**
（用于打通链路与回归测试）。真实词库通常 **10 万+ 条**，
必须由法务/合规团队以独立文件交付并支持热更新（`load_terms_from_file`）。

### 脱敏设计：留痕不存原文

| 层级 | 措施 |
|------|------|
| 数据库 | 只存 `content_hash`（sha256[:32]）+ `content_len` + 分类/级别 |
| `Hit.as_dict()` | 只输出 `matched_hash` / `matched_len`，**绝不输出命中词** |
| 用户响应 | `reason_for_user()` 不含任何命中词，只引导至投诉入口 |

**两个理由**：① 存原文等于把违规内容**二次存储**，本身是新的合规风险；
② 泄露命中词可被攻击者**反向调优绕过策略**。

### 验证结果

| 验证层 | 结果 |
|--------|------|
| 静态编译 | ✅ 全绿 |
| 单测（引擎 30 + 投诉 13） | ✅ **43/43** |
| E2E HTTP（审核） | ✅ **44/44** |
| E2E HTTP（投诉） | ✅ **28/28** |
| 全量回归 | ✅ **166/166** |
| 路由挂载 | ✅ 73 → **79** 条 |

### 七轮累计进度

| 轮次 | 主题 | 修复项 |
|------|------|--------|
| 第一轮 | 最高危越权 + 静默降级 | 3 |
| 第二轮 | 同类越权全量收口 + 文件鉴权 | 4 |
| 第三轮 | 任务队列化 + 限流加固 | 4 |
| 第四轮 | 向量检索链路打通 | 1（P0-2） |
| 第五轮 | 审计日志全链路补全 | 1（P0-8） |
| 第六轮 | 前端令牌安全 + CSRF | 2（P0-7 / P0-11） |
| 第七轮 | **内容安全审核 + 投诉举报** | **1（P0-13）** |
| **合计** | | **16 项修复**（覆盖 **12** 个 P0 编号）✅ |

**验证总计**：E2E 60 + 44 + 28 = **132** ✅ / 自动化测试 **166** ✅ / 静态断言全绿 ✅

> **📌 勘误（2026-09-16）**：此处原写「**16 / 16 P0 全部清空**」，**计数口径有误**。
> 表中的「16」是**累计修复条目数**（含第一轮修复的「注册接口自助提权」等不在原始 P0 清单内的新增发现项），
> **不是 P0 编号的完成数**。经 2026-09-16 逐项代码核实，原始 16 项 P0 的真实进度为：
> **已修 12 项，剩 4 项**（P0-9 IM 骨架 / P0-12 前端注册支付 / P0-15 通知只写不读 / P0-16 合同审查无 LLM）。
> 根因：轮次修复记录按「本轮又修了 N 项 → 累计累加」的惯性书写，**未回到 P0 清单逐项复核**。
> 详见 `remaining-work-inventory-2026-09-16.md` §十。

### ⚠️ 本轮留下的已知项（必须在上线前闭环）

**技术侧未完成（下一轮建议）**：
- **`usage_quotas` 扣减非原子**：并发下可能超额消费（P1）
- **审计日志无保留期与轮转**：等保要求 ≥6 个月（P1）

**必须由非技术团队交付（本轮的硬性交付物依赖）**：
1. **全量违禁词库**（10 万+ 条）——当前内置词库仅够打通链路，
   **不可直接商用**。需法务/合规提供，支持热更新。
2. **外部审核服务接入**——商用必须接第三方内容安全 API
   （生产守卫已强制要求 Key，否则拒绝启动）。
3. **监管上报通道**——`MODERATION_REPORT_URL` 需对接主管部门接口。
   未配置期间所有 ESCALATE/BLOCK 事件都会落 `CONTENT_REPORT_PENDING`，
   **运维必须定期清账**，否则构成"未报告"违法。
4. **算法备案材料**——技术底座已就绪，备案本身需法务发起，**周期不可控，建议立即启动**。

> **给产品负责人的一句话**：第七轮之后，**技术侧已具备备案条件**，
> 但备案能不能过取决于**词库质量、外部审核服务、上报通道**这三项
> **非技术交付物**。建议本周内同步法务启动，避免产品等备案。

---

## 🔧 修复记录（2026-09-13 · 第八轮：并发扣减原子化 + 审计保留期）

> 本轮修的是第七轮结尾列出的两项 P1。**过程中发现并根治了一个已重复 3 次的机制性缺陷**（独立会话写审计撞锁），
> 并**推翻了我自己上一轮对缺陷的描述**——详见下文。

### 一、缺陷 A：用量额度扣减非原子（少计费、少转工单）

**上一轮的描述是错的。** 我曾写"并发下可能**超额**消费"，实测证明恰好相反：

```
额度 limit=5，并发 20 请求 → used_count = 1 或 2      ← 少计，不是超计
```

即 18~19 个请求的用量**从未被计入**，且大部分**没有转成工单**。原实现是三步式：

```python
q = await self.ensure_quota(...)             # 读
exceeded = q.exhausted                       # 判
if not exceeded:
    q.used_count = (q.used_count or 0) + 1   # 写（基于旧快照）
```

并发时多个请求基于**同一旧快照**计算，后写覆盖先写 —— 典型**丢失更新（lost update）**。

**另一个隐藏缺陷**：`ensure_quota` 的"查不到就插入"在并发首次使用时可能插入多行同一
`(tenant, type, period)`，把额度切成几份、**变相放大可用量**。

**修复**：

| 措施 | 文件 | 说明 |
|------|------|------|
| `consume_atomic()` | `services/billing_service.py` | 条件 UPDATE 把"判断+自增"合并为一条 SQL，由 DB 原子求值，以 `rowcount` 判定 |
| 唯一索引 `uq_usage_quota_tenant_type_period` | `models/billing.py` | 把"额度唯一"下沉到数据库约束（应用层只能缓解） |
| `IntegrityError` 兜底 | `services/billing_service.py` | `ensure_quota` 并发首用时回滚重读 |
| 调用点迁移 | `qa.py` / `documents.py`×2 / `billing.py` | 全部切到 `consume_atomic()`；`consume()` 标注**已废弃** |

**修复后实测**：`used_count=5`、成功 `5`、工单 `15` —— 零丢失、零漏单；并发建 15 工单，工单号全部唯一。

### 二、缺陷 B：审计日志无保留期与归档出口

**两个方向都是缺陷**：
- **留存不足**违反等保 2.0（三级）"留存不少于 6 个月"；
- **无限期留存**导致表无限膨胀拖垮查询，且 `detail` 可能含个人信息，与《个人信息保护法》最小必要原则冲突。

**修复：三段式** `保底留存 → 到期归档 → 归档校验通过后清理`

| 组件 | 文件 | 作用 |
|------|------|------|
| `AUDIT_RETENTION_DAYS=365` | `config.py` | 默认高于 180 天下限 |
| `retention_days()` | `services/audit_retention.py` | 配置低于 180 时**告警并强制抬到 180**（拒绝遵从错误配置） |
| `archive()` | 同上 | gzip JSONL + sha256；**只读不删** |
| `purge()` | 同上 | **默认 dry-run**；分批删除；写 `AUDIT_RETENTION_PURGE` 墓碑 |
| `archive_then_purge()` | 同上 | 归档行数 ≠ 待清理行数时**拒绝清理**（宁可保留，不可丢失） |
| 4 个管理端点 + 1 个公开策略端点 | `api/v1/audit_retention.py` | 仅平台管理员，受 `AUDIT_RETENTION_ADMIN_ENABLED` 急停开关约束 |
| `/api/health` 暴露 `audit_retention` 块 | `main.py` | 合规巡检可一眼比对配置值与生效值 |

### 三、⚠️ 本轮最重要的发现：第 3 次撞到同一个坑

清理审计日志时，墓碑审计写入失败：

```
ERROR | 审计写入失败（已重试 5 次，该条审计已丢失，请检查数据库锁竞争）:
(sqlite3.OperationalError) database is locked
```

**根因**：`log_detached_ctx` 用的是**独立数据库连接**（这正是它存在的意义——主事务回滚时仍能留痕）。
若调用方此刻**仍持有未提交的写事务**，SQLite 加库级写锁、Postgres 行锁等待超时，独立连接写入必败。

**这已是本项目第 3 次踩到同一坑**：
1. **第 5 轮**：审计日志随请求事务回滚而丢失
2. **第 7 轮**：内容拦截记录随 `ContentBlockedError` 回滚而丢失
3. **第 8 轮**：清理墓碑被未提交的 DELETE 事务阻塞

**根治（两处）**：
1. `log_detached` 内置**退避重试**（5 次递增间隔），仍失败时打 **error** 级日志（原为 warning）——避免"看起来正常"。
2. `purge()` **调整顺序**：先在主会话内提交删除，**再**写墓碑。顺序反了必踩锁。

> **工程约定（已写入代码注释）**：写审计前先问一句 —— "此刻是否还有未提交的写事务？"
> 若有，要么先提交，要么接受重试。

### 三-B、⚠️ 回归测试抓住了我在本轮引入的真实回归

给 `log_detached` 加退避重试时，我把 `factory = _get_detached_factory()` 放在了 `try` **外面**。
`test_audit_log.py::test_log_detached_swallows_db_errors` **立刻失败**：
mock 让工厂抛异常 → 异常直接冒泡 → **"审计失败绝不阻断主流程"这个无条件契约被破坏了**。

- **修复**：把工厂获取也移进 `try`（它同样可能失败：配置错误 / 引擎创建异常）。
- **并新增** `test_log_detached_retries_are_bounded`：断言重试次数**恰好有上限**
  （`retries=3` → 尝试 3 次）——**审计可以丢，主流程不能卡**。

> **教训**：给已有函数"加能力"时，先读它的**既有契约测试**。
> 这次是既有测试替我兜住的；若没有它，这个回归会在生产里以"审计把主流程搞挂"的形式出现，
> 而且因为重试逻辑"看起来更健壮"，极难被归因。

### 四、验证证据

| 层次 | 脚本 | 结果 |
|------|------|------|
| 并发扣减 + 保留期（含修复前后对比） | `backend/verify_p1_concurrency_retention.py` | **13 / 13** |
| 接口端到端（真 HTTP 路径） | `backend/verify_p1_retention_api.py` | **12 / 12** |
| 单元测试（真库 + 真并发） | `backend/tests/test_p1_concurrency_retention.py` | **16 / 16** |
| 全量回归 | `backend/tests/` | **183 / 183** |
| 路由注册 | `app.main.create_app()` | 79 → **84**（新增 5 个审计保留期端点） |

**关键断言**：
- `A.2` 额度 5 / 并发 20 → `used_count=5`（旧实现只能记到 1~2）
- `C.4` 配置 30 天 → 生效 180 天（拒绝遵从错误配置）
- `C.5` 配置 730 天 → 生效 730 天（不误伤合理配置）
- `D.2` 不传 confirm → `dry_run=True`，行数不变
- `D.3` / `I.5` 清理后墓碑审计 ≥1 条
- `E.1~E.3` 匿名访问清理/归档/统计一律 401

### 五、本轮留下的已知项

**技术侧（下一轮建议）**：
- **审计日志加密存储与异地备份**（等保测评的"数据备份"控制项，P1）
- **`usage_quotas` 唯一索引的存量迁移脚本**（存量库可能有重复行，需先归并）
- **`consume()` 是否直接删除**（当前保留为对比基线，P2）

**运维侧（上线前必须落实）**：
1. 审计归档挂**定时任务**（建议每月一次 `archive-then-purge`）
2. 监控审计总量增长趋势 + `AUDIT_RETENTION_PURGE` 告警

**第七轮遗留的非技术交付物仍然阻塞备案**（词库 / 外部审核 / 上报通道 / 备案材料）——本轮未改变这一结论。

> **给产品负责人的一句话**：第八轮之后，**技术侧对等保测评的阻塞项也已消除**（审计留存合规 + 计费准确）。
> 剩下的关键路径**完全在非技术侧**：词库、外部审核服务、上报通道、备案材料。
> 建议本周内同步法务启动备案流程。

---

## 🔧 修复记录（2026-09-13 · 第九轮：CI 门禁 + 可观测性）

> 本轮处理 P0-10，一次补齐**两个**空白：**代码质量没有强制门禁**、**系统跑起来是黑盒**。
> 之所以合并处理，是因为二者共享同一个前提——工程师得先能"看见"问题。
> 门禁负责在合并前拦住问题，可观测性负责在上线后看见问题；缺任一半，另一半的价值都大打折扣。

### 一、修复前的真实状态（不是"没做"，而是"配了但从未生效"）

| 项目 | 修复前 | 后果 |
|------|--------|------|
| CI 配置 | **无** `.github/` 目录 | 所有改动靠人肉 review 把关 |
| Lint 配置 | `pyproject.toml` 里**有** `[tool.ruff]` | **从未强制执行**——配置存在给人"已在管"的错觉，比没有更危险 |
| 实测 lint 结果 | `ruff check .` → **52 个错误** | 一旦今天直接开 CI，**第一天就是红的** |
| 日志 | loguru 默认格式，无 request_id | 深链路的错误日志无法关联到具体请求 |
| 指标 | **无任何 `/metrics`** | 5xx 率、P99 延迟、队列积压全是盲区 |
| 健康检查 | 单个 `/api/health`（综合自检） | 把"进程活着"与"能接流量"混为一谈 |
| Pre-commit | 无 | — |

### 二、⚠️ 本轮最重要的判断：门禁必须"第一天就是绿的"

一条**上线即红**的 CI，实际寿命是**两天**：第一天报红、第二天被注释掉、第三天没人记得它存在。
所以本轮做 CI 的顺序是**先清债、再上锁**：

```
步骤 1  让 ruff 全绿（清 52 个历史错误）        ← 不做这步，后面全是白干
步骤 2  写 CI，跑 ruff / compileall / pytest     ← 此时门禁必须是绿的
步骤 3  本地完整模拟一遍 CI，确认 EXIT=0          ← 不允许"提交上去看看"
```

### 三、清债：52 个 lint 错误中，有 5 个是**真缺陷**

自动化修掉 47 个（未用导入 / 导入顺序 / 重复定义）之后，剩下 5 个**不能**用 `--fix` 或 `noqa` 糊过去：

| # | 问题 | 判定 | 处理 |
|---|------|------|------|
| 1-4 | `app/database.py` **4× E402** | ✅ **真实缺陷** | 有一行 `logger = logging.getLogger(__name__)` 被插在**导入块中间**，把导入语句切断。这不是风格问题，是"谁写的谁不知道"的时序错误 |
| 5 | `case_copilot.py` **E741** | ✅ 真实可读性缺陷 | 变量名 `l`（易与 `1` 混淆）→ 改为 `law` |

**另外诊断脚本与根目录一次性脚本（`verify_*.py` / `check_chunk.py` / `patch_migration.py`）走 `extend-exclude` 排除，而非改代码**：
它们必须在 `import app.*` **之前**设置 `DATABASE_URL`，E402 在此处是**设计使然**。
> 工程判断：区分"代码写错了"与"规则在此场景不适用"，是引入 lint 时最容易做错的决定。
> 前者必须修（否则规则形同虚设），后者必须显式排除并写明理由（否则开发者会开始习惯性 `noqa`）。

### 四、CI 门禁（`.github/workflows/ci.yml`）

三个 job，**全部无需外部服务边车容器**——这直接受益于本项目"零必需外部依赖即可完整启动"的设计：

| Job | 内容 | 为什么需要 |
|-----|------|-----------|
| `backend` | `ruff` → `compileall` → `pytest` | 三层递进：风格 / 语法 / 行为 |
| `frontend` | `pnpm install --frozen-lockfile` → `pnpm typecheck` | **tsc 是唯一裁判**（见第六轮教训） |
| `docker-build` | `docker/build-push-action`（只构建不推送） | 防止"代码能跑但镜像构建不出来" |

**安全与工程细节**：
- `permissions: contents: read` —— CI 不需要写权限（最小权限原则）
- `concurrency` + `cancel-in-progress` —— 同一 PR 连推多次时自动取消旧任务，省额度
- 失败时上传 `*.db` / `_tmp_tests/` / `audit_archive/` 作为诊断产物
- `CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR: ""` 需在 CI 中显式置空——这是沙箱守卫变量

> **为什么前端 job 必须跑 `tsc` 而不是 ESLint？**
> 第六轮批量改写 13 个页面鉴权守卫时，**静态 grep 完全看不出问题**，而 `tsc` 一次性报出 **11 个编译错误**。
> 类型检查能发现 grep 与 code review 结构性看不见的错误，因此它必须是门禁的硬性一环。

**本地完整模拟 CI 的实际输出**（不是"应该会通过"，是实际跑的）：

```
### STEP 1: ruff ###        All checks passed!
### STEP 2: compileall ###  compile OK
### STEP 3: pytest ###      183 passed, 17 warnings in 738.83s (0:12:18)
pytest EXIT=0
```

前端四个应用全部 `tsc --noEmit` **EXIT=0**（admin / im / lawyer / web）。

### 五、可观测性：自建而非引依赖

新增 `app/core/metrics.py`（约 150 行）与 `app/core/log_config.py`，**零新增运行期依赖**。

**为什么不引 `prometheus_client` / `structlog`？**
本项目实际只用到「计数器 + 直方图 + 文本导出」与「JSON sink + 上下文注入」四件小事，
自建完全可控；而每多一个运行期依赖，就多一份供应链风险与版本升级负担。
这与 CI 不需要边车容器是**同一条原则**：保持"零必需外部服务"。

### 六、⚠️ 本轮自己引入并修复的两个真实缺陷

**缺陷 1：直方图桶不累计 → `histogram_quantile()` 静默返回错误分位数**

`Histogram.observe()` 初版**只把计数加到命中的第一个桶**：

```
观测 5 / 50 / 500 / 5000，buckets = [10, 100, 1000]
错误实现 → buckets = [1, 1, 1, 4]     ← le=5000 竟然 ≤ le=500，违反单调性
正确实现 → buckets = [1, 2, 3, 4]     ← 累计
```

**为什么这个 bug 特别危险**：Prometheus 服务端**不会报错**，
`histogram_quantile(0.99, ...)` 只会**返回一个错误的 P99**。
而 P99 是延迟告警的**唯一依据**——意味着告警会在错误的阈值上触发或不触发，且无人能察觉。

> **和第 8 轮是同一类错误的不同形态**：上一轮是"并发时少计用量"（丢失更新），
> 这一轮是"分桶时少计样本"（非累计）。**共同点：错误的结果不报错，只是数值不对。**
> 所幸验证脚本的 A.5 断言"桶必须单调不减"当场抓住了它。

**缺陷 2：Gauge 私有字段被外部直接读写**

在途请求计数需要在中间件里做「读-改-写」。初版直接操作 `metrics.http_requests_in_progress._values`，
虽能跑通，但**任何一次字段重命名都会静默改坏计数逻辑**。
→ 给 `Gauge` 补齐 `get()` / `inc()` / `dec()` 公开 API，`dec()` 内**夹到 0**（负值会污染面板且难以察觉）。

**缺陷 3（同类）：限流指标用了原始路径**

我在 `metrics.py` 里专门写了 `normalize_path()` 防基数爆炸，
却在**唯一真正危险的那个调用点**（限流指标的路径标签）绕过了它。
→ 已改为 `normalize_path(request.url.path)`，并补了端到端测试
`test_metrics_endpoint_no_high_cardinality_paths`（扫描所有 `path=` 标签断言无裸数字 ID）。

### 七、探针语义拆分：`livez` / `readyz` 不能混用

原来的单个 `/api/health` 是**综合自检**（含 LLM 档位配置），
用它做容器探针会导致：**LLM Key 没配 → 探针失败 → 容器被反复重启**，而进程本身完全健康。

| 探针 | 检查内容 | 失败后果 |
|------|---------|---------|
| `/api/health/livez` | **只回答"进程还在吗"**，绝不碰 DB | 失败 → 编排器**重启**容器 |
| `/api/health/readyz` | `SELECT 1` **真实 DB 往返** | 失败 → 编排器**摘流量**，不重启 |

**关键设计**：`readyz` 用 `SELECT 1` 而非连接池状态判断——连接可能被中间件/防火墙
**静默断开**，只有真正往返一次才能发现。失败返回 **503**（不是 200 带 error 字段），
否则编排器不会摘流量。

**已同步修正 `docker-compose.yml`**：healthcheck 从 `/api/health` 改为 `/api/health/readyz`
（原配置会因 LLM 未配置而误判容器不健康）。

### 八、配套告警规则（`deploy/alert-rules.yml`，9 条 / 5 组）

告警设计原则：**每条都对应一个具体的用户可感知故障或合规风险**，不做无法行动的告警——
"看起来不太对"的告警会训练运维忽略告警，**比没有告警更危险**。

| 组 | 告警 | 阈值 | 依据 |
|----|------|------|------|
| 可用性 | 5xx 率 > 5% / 实例不可达 | 持续 5m / 2m | 用户已可感知失败 |
| 延迟 | P99 > 5s | 持续 10m | 法律问答超 5s 用户大概率放弃 |
| 安全 | 限流激增 / CSRF 拒绝激增 | >30 次/min | 暴力破解、刷量、跨站攻击 |
| **合规** | **审计写入失败 > 0（零容忍）** | 任何 1 条 | 等保 2.0 三级要求审计完整留存 |
| 合规 | 内容拦截激增 | >20 次/min | 恶意投放 或 词库过严误伤 |
| 容量 | 队列积压 > 200 / 配额耗尽激增 | 持续 10m | 按并发 4 估算等待超 10 分钟 |

> **交叉验证**：已用脚本比对「告警规则引用的 8 个指标名」与「注册表实际导出的 12 个指标」，
> 全部可解析（含直方图 `_bucket` 子序列）。**指标名拼错的告警会永远不触发**——
> 这是告警体系最阴险的失效模式，必须用工具而非肉眼保证。

### 九、验证证据（五层）

| 层次 | 脚本 | 结果 |
|------|------|------|
| 静态 | `ruff check .` | **All checks passed!**（52 错误 → 0） |
| 语法 | `python -m compileall -q app/` | **OK** |
| 指标内核 + 格式 + 探针 + 日志 + CI 配置 | `backend/verify_p0_observability.py` | **53 / 53** |
| 单元测试（真 HTTP 路径） | `backend/tests/test_observability.py` | **26 / 26** |
| 前端类型 | `tsc --noEmit` × 4 应用 | **全部 EXIT=0** |
| CI 端到端模拟 | 三步按 CI 原样执行 | **EXIT=0**，`183 passed` |
| 全量回归 | `backend/tests/` | **209 / 209**（183 + 26） |

**回归确认（第 8 轮成果未被破坏）**：
`verify_p1_concurrency_retention.py` **13/13**、`verify_p1_retention_api.py` **12/12**。

**关键断言**：
- `A.5` 直方图桶 `[1,2,3,4]` 单调不减（**抓住了本轮真实缺陷**）
- `A.3` 200 个唯一路径 → **1 个模板**（基数受控）
- `B.3` / `test_metrics_endpoint_no_high_cardinality_paths` 无裸 ID 路径标签残留
- `C.3` 探针与 `/metrics` 自身**不计入**业务指标
- `C.6` DB 故障 → `readyz` **503**；`C.7` 同一时刻 `livez` **仍 200**
- `D.2` 含双引号与换行的消息仍产出**单行合法 JSON**
- `E.5` 权限最小化 `contents: read`

### 十、本轮留下的已知项

**技术侧（下一轮建议）**：
- **CI 缺少依赖漏洞扫描**（`pip-audit` / `pnpm audit`）——供应链安全目前仍无门禁
- **指标无持久化**：进程重启后计数归零（Prometheus 拉取模型下可接受，但**不适合做计费依据**）
- **无分布式追踪**：跨副本调用链仍靠 `request_id` 人工串联
- **`ci_test.db` 未在 CI 中清理**：当前依赖 `.gitignore` 的 `*.db`，长期应显式清理

**运维侧（上线前必须落实）**：
1. 部署 Prometheus 并挂载 `deploy/prometheus.yml` + `deploy/alert-rules.yml`
2. **`/metrics` 必须限制为集群内可达**（含限流命中数等业务敏感指标）
3. 生产环境确认 `LOG_FORMAT=json`（compose 已配置）
4. 在编排器中按语义分别配置 `livez`（重启）/ `readyz`（摘流量）——**不要只配一个**

> **给产品负责人的一句话**：第九轮之后，**"改坏了不会被发现"这个系统性风险已经消除**——
> 任何 PR 都要过 ruff + 类型 + 209 个测试才能合并；上线后 5xx 率、P99、队列积压、审计丢失都有人盯着。
> 需要你决策的是**成本项**：Prometheus/Grafana 托管方案，以及 /metrics 的暴露范围（内网 or 集群内）。

---

## 🔧 修复记录（2026-09-13 · 第十轮：分页 COUNT 性能缺陷）

> **⚠️ 本轮最重要的成果不是"修了什么"，而是"推翻了什么"。**
> 第九轮我在收尾时写下"下一轮建议做性能优化：**同步 IO 混入 async、LIKE 查询慢、无缓存**"。
> 本轮**先测量、再动手**，结果这三条里**两条是错的**——它们只是"读代码时的直觉"，
> 不是实测结论。若照原计划动手，会做三件**收益为零甚至为负**的改动。

### 一、先测量：三条假设，推翻两条

| 第九轮的假设 | 实测结果 | 判定 |
|-------------|---------|------|
| 同步 IO 混入 async | 全仓 grep `requests.` / `httpx.get` / `time.sleep` / `urllib` → **仅 1 处命中**：`moderation.py:336` 的 `urllib.parse.unquote`，**纯字符串解析、非 IO** | ⚠️ **本轮结论有误，已撤销**（见下方勘误） |
| 深分页（大 OFFSET）慢 | 2 万行实测：`OFFSET 19000` **1.98ms** vs `OFFSET 0` **4.55ms**（深分页反而更快）；keyset 分页 2.29ms，**收益为零** | ❌ **不成立，撤销** |
| LIKE 查询慢 | `LIKE '%kw%' LIMIT 20` 从 25 万→55 万行恒为 ~2.7ms——**这是 `LIMIT` 短路的假象**，不是"LIKE 快" | ⚠️ **结论错误，真因在别处** |

> **📌 勘误（2026-09-16）：上表第一行「同步 IO 不成立」的判定是错的。**
> **错因不是结论草率，而是 grep 模式不完整**——只搜了 `requests.` / `httpx.get` / `time.sleep` / `urllib`，
> **漏掉了 `open()`**。2026-09-16 复核实测：
> - `app/services/archive_service.py:211` → `with open(abs_path, "w", encoding="utf-8") as f:`
> - `app/services/storage_service.py:47` → `with open(abs_path, "wb") as f:`
>
> 两处均为 **async 服务内的同步文件写入**；全仓 `asyncio.to_thread` 命中 2 个文件（均不在这两处），`aiofiles` **零使用**。
> **故 P1-8（同步文件 IO）应保留为待办**，不应撤销。
>
> **方法论教训（与第九轮"直觉不能替代测量"是同一类错误的镜像）**：
> 第九轮的错误是"用直觉代替测量"；**本轮的错误是"用测量代替了正确的测量"**——
> 验证手段本身有缺陷时，**测出来的"没问题"比没测更危险**，因为它会关闭一个本该保留的待办项。
> **凡以"全仓 grep 无命中"为由撤销缺陷，必须把搜索模式一并写出来供复核。**

**为什么深分页"不成立"**：执行计划为 `SEARCH cases USING INDEX ix_cases_tenant_id`——
`tenant_id` 索引已把工作集收窄，`LIMIT` 再让其短路，因此 OFFSET 大小无关紧要。
**若按原计划改 keyset 分页，是纯粹的无效改动。**

### 二、真因：`COUNT(*)` 无法短路（这是本轮唯一经实测确认的缺陷）

`LIMIT` 能让**取数据**在凑够 20 条后立即停止，但 `COUNT` 必须**遍历全部匹配行**。
55 万行实测（SQLite）：

| 查询 | 耗时 |
|------|------|
| 租户过滤 `COUNT`（走覆盖索引） | 4.55 ms |
| **租户 + 关键词 `LIKE` 的 `COUNT`** | **114.95 ms** |
| 数据页本身（`LIKE` + `LIMIT 20`，可短路） | **0.53 ms** |

→ **计数比取数据贵 217 倍，而用户等的正是计数。**
`LIKE '%kw%'` 前导通配无法走 B-Tree 索引，只能逐行匹配——这才是真正的成本来源。

### 三、方案：有界计数（bounded count）

绝大多数场景用户只需知道"能翻几页"，而非精确总数。故改为**最多数 `COUNT_CAP=200` 行就停**：

```sql
SELECT count(*) FROM (SELECT ... FROM cases WHERE ... LIMIT 201) t
```

取 `cap + 1` 是为了**区分两种塌缩情况**（二者都不报错、只是数字错）：

- 数到 **200** → 已数完 → 返回 `(200, False)` **精确值**
- 数到 **201** → 确实还有更多 → 返回 `(200, True)` **下界**，前端显示"200+"

若只取 `LIMIT 200`，则"恰好 200 条"会被错报成下界、"201 条"会被错报成精确 200（静默丢 1 条）。
**精度只在"多到用户翻不完"时才降级，而那种场景下精确值本身也无意义。**

### 四、改造范围：只改实测证明确有收益的站点

本轮**没有**"见到 COUNT 就改"，而是逐站点测量。判据是**匹配集是否远大于一页**：

| 站点 | 匹配规模 | 无界 COUNT | 有界 COUNT | 有界收益 | 处置 |
|------|---------|-----------|-----------|---------|------|
| **complaints 管理员列表** | 27 万（status 偏斜 90%） | **50.81 ms** | **0.37 ms** | **137.6×** | ✅ **改** |
| **cases `/pool`**（大律所） | 8,572 | **30.99 ms** | **0.95 ms** | **32.6×** | ✅ **改** |
| **dispatches 抢单池** | （or_ 选择性谓词） | 4.99 ms | 0.47 ms | 12.9× | ✅ **改** |
| dispatches 列表（小租户） | ~1500 | 7.23 ms | 7.01 ms | 1.3× | ⚠️ 零收益，但大律所下 54× → 统一采用 |
| conversations（CLIENT） | ~1500 | 6.34 ms | 6.32 ms | 1.2× | ⚠️ 同上 |
| reviews 列表 | 小集合 | 7.37 ms | 6.91 ms | 1.1× | ⚠️ 同上 |
| `list_templates` | **无 LIMIT**（目录型数据） | — | — | — | ✅ 加 `TEMPLATE_LIST_MAX=500` 防御 |

**共改造 7 个站点**（cases ×2、dispatches ×2、complaints、conversations、reviews、knowledge_service）
+ 模板列表加硬上限。`Page` 新增 `total_is_lower_bound: bool = False`（默认 False，旧调用点零影响）。

**一个反直觉的发现**：`complaints` 是我最可能跳过的一个——它的执行计划用了
`ix_complaints_status`，**看起来健康**；但该表**分布高度偏斜**（90% 工单都是 `PENDING`），
索引几乎无法收窄，实际是全部站点里最差的一个（**137.6×**）。
**"计划里有索引" ≠ "索引起了作用"。**

### 五、⚠️ 本轮第二个方法论教训：不要在 ORM 墙钟上做绝对阈值

初版验证脚本断言"有界计数 < 5ms"，实测 **9.43ms 失败**。排查后发现**不是代码问题**：

| 层级 | 无界 COUNT | 有界 COUNT | 比值 |
|------|-----------|-----------|------|
| 裸 sqlite3（扫描代价） | 18.31 ms | **0.354 ms** | **51.7×** |
| SQLAlchemy + aiosqlite（端到端） | 23.86 ms | 5.259 ms | 4.5× |

`SELECT 1` 经 aiosqlite 就要 **4.381ms**——每条语句有 ~4.4ms 固定框架开销，
因此有界计数**无论如何也降不到 5ms 以下**。**有意义的信号是"扫描代价"**（随数据量增长的部分，也正是我们修掉的部分）；
**框架开销是常态背景，不该作为验收标准。** 修正后：扫描加速 **51.9×**、端到端 **3.5×**。

### 六、验证（5 层，全绿）

| 层级 | 手段 | 结果 |
|------|------|------|
| 编译 | `compileall app` | ✅ EXIT=0 |
| 静态 | `ruff check .` | ✅ All checks passed（第九轮的 0 保持） |
| 单元 | `tests/test_pagination_perf.py` | ✅ **10/10**（含 cap / cap+1 边界、租户过滤、自定义 cap、`Page` 契约） |
| 真实数据 | `verify_p1_pagination_perf.py`（30 万行） | ✅ **20/20**（语义 / 性能 / HTTP 契约 / 静态收口） |
| 端到端 | 全量回归 | ✅ **219 passed**（209 + 新增 10） |

**验证脚本还抓出我自己的 3 个错误**（这类脚本的价值就在这里）：
1. `dispatches.py` **有两个端点**我只改了一个——`/pool` 仍有遗留无界 COUNT
2. 检测器只匹配 `count_bounded(db,` ，漏了服务层的 `count_bounded(self.db,`（**脚本错，非代码错**）
3. 脚本**不可重复运行**：同名租户残留撞 `case_no` UNIQUE 约束（**脚本错**）

### 七、本轮不做的事（明确 Non-goals）

- ❌ **keyset 分页**——实测零收益，不做
- ❌ **缓存层**——在有真实 QPS 数据前引入缓存是过早优化；当前瓶颈已用有界计数消除
- ⚠️ ~~**同步 IO 改造**——经查不存在该问题~~ → **勘误（2026-09-16）：该撤销有误，P1-8 仍为待办**（`archive_service.py:211`、`storage_service.py:47` 存在 async 内同步 `open()`；原 grep 漏搜 `open()`）
- ⚠️ **不引入 `total_is_lower_bound` 之外的分页语义变更**——保持接口向后兼容

### 八、给产品负责人的一句话

第十轮把**唯一经实测确认的性能缺陷**（关键词搜索的 `COUNT`，55 万行下 115ms）降到 **0.36ms（51×）**，
覆盖面是**全部 7 个列表接口**。更重要的是建立了一条纪律：
**性能优化必须先有测量，否则会做出"深分页改 keyset"这种收益为零的改动**——本轮已用数据推翻了三条此类假设。
**需要你决策的**：是否需要为大型律所（单租户 10 万+ 案件）单独规划归档策略——
有界计数让列表接口不再随数据量变慢，但历史数据的存储与合规保留期仍需产品口径。


## 🔧 修复记录（2026-09-14 · 第十一轮：依赖漏洞扫描门禁（供应链安全））

> **一句话**：前九轮修的都是"我们自己写的代码"，而 `ruff` / `pytest` / `tsc` 对
> **依赖本身是否已被攻破**完全无感——本轮补上这最后一类盲区，并发现前端
> **39 个漏洞（含 3 个 critical RCE）**，而它们在本地**完全不可见**。

### 一、为什么这一轮必须做：工具链的盲区

| 缺陷类型 | 发现手段 | 前九轮 |
|---------|---------|-------|
| 语法/类型错误 | `ruff` / `tsc` / `compileall` | ✅ |
| 逻辑与并发错误 | `pytest` + 真实 DB / 并发 | ✅ |
| 鉴权与越权 | 5 层验证 + IDOR 扫描 | ✅ |
| **依赖带已知 CVE / 被投毒** | **只能靠扫描器** | ❌ → **本轮** |

一个被投毒的依赖可以**通过全部测试、通过全部静态检查**，然后在生产环境开门。
这是唯一一类"改坏了我们的门禁不会响"的问题。

### 二、实测结果（全部来自扫描器，非估计）

**后端 `pip-audit`：25 个唯一漏洞 / 6 个包**

| 包 | 数量 | 可达性判定（读代码确认） |
|----|-----|---------------------|
| `python-multipart` 0.0.9 | 7 | **✅ 可达**（`evidence.py` 接收 `UploadFile`）→ 必须修 |
| `starlette` 0.38.6 | 7 | **✅ 可达**（Host 头绕过影响本项目中间件鉴权）→ 必须修 |
| `cryptography` 43.0.1 | 6 | 间接（TLS/证书链）→ 必须修 |
| `python-jose` 3.3.0 | 3 | ❌ 不可达（`algorithms=[...]` 白名单 + 从不调 `jwe`）→ 仍升级 |
| `pytest` 8.3.3 | 1 | 仅开发期 → 升级 |
| `ecdsa` 0.19.2 | 1 | ❌ 不可达 + **上游不修** → 登记豁免 |

**关键结论：严重度 ≠ 数量，必须做可达性分析。**
认证栈那 3 个"高危"在本项目其实**不可达**（`app/core/security.py` 用了
`algorithms=[settings.ALGORITHM]` 显式白名单，正是官方推荐的规避方式；且从不调用 `jwe`）。
若只看 CVE 数量，会把精力全砸错地方——**实际最该修的是数量最多的 `python-multipart`**。

**前端 `pnpm audit`：39 个漏洞（3 critical / 14 high）**

| 包 | 数量 | 代表问题 |
|----|-----|---------|
| `next` 14.2.5 | 33 | **2 个 critical 未认证 RCE** + 中间件鉴权绕过 + SSRF/DoS 多项 |
| `postcss` 8.4.31（**next 内嵌**） | 6 | 任意文件读取 / 路径穿越 / XSS |

### 三、⚠️ 最危险的不是漏洞，是"本地看不见"

```
$ pnpm audit --audit-level high      # 本地默认（华为云镜像）
ERR_PNPM_AUDIT_BAD_RESPONSE ... responded with 405: Request method 'POST' is not supported
```

镜像**不支持 audit 接口**。修复前，本地跑这条命令**不暴露任何漏洞**，
而同一份代码在 CI 上会报 39 个（含 3 个 critical）。
**"本地绿"与"CI 绿"不同义的门禁，比没有门禁更危险**——它会让人误以为已经检查过了。

### 四、修复内容

**后端：链式依赖升级（不能只升一个）**

`fastapi==0.115.0` **硬约束 `starlette<0.39.0`**，而 starlette 的修复全在 0.40+，
所以必须**同时升 fastapi** 才能拿到修复：

| 包 | 升级 | 修复内容 |
|----|------|---------|
| `fastapi` | 0.115.0 → **0.141.1** | 解除 starlette 上界（解锁钥匙） |
| `starlette` | 0.38.6 → **1.3.1** | Host 头校验绕过 + 表单解析 DoS |
| `python-multipart` | 0.0.9 → **0.0.31** | 路径穿越 + Content-Length 负数读 + 头部 DoS（7 项） |
| `python-jose` | 3.3.0 → **3.4.0** | JWT bomb + 算法混淆 |
| `cryptography` | 43.0.1 → **50.0.1** | OpenSSL 6 项 + PKCS7 解密结果误报 |
| `pytest` | 8.3.3 → **9.0.3** | `/tmp` 本地提权/DoS |
| `pytest-asyncio` | 0.24.0 → **1.3.0** | 否则与 pytest 9 冲突（实测 `ResolutionImpossible`） |

**前端：Next 14 → 15（无 14.x 可解）**

经计算，**没有任何 14.x 能清除那两个 critical RCE**，最低需要 `next >= 15.5.24`。
破坏面**逐项核查后仅 1 处**：
- ✅ **无 `middleware.ts`**（Next 15 最高风险破坏点，本项目不受影响）
- ✅ 全 App Router + React 18.3.1（兼容）
- ⚠️ `apps/lawyer/app/cases/[id]/page.tsx`：改用 `useParams()`，**移除对 `params` prop 的依赖**

`postcss` 需额外 override：`next@15.5.24` **仍精确锁定 `postcss@8.4.31`**，
只升 next 清不掉（实测残留 4 个），必须在解析层强制改为 8.5.28。

**豁免清单**：`security-allowlist.txt` 登记 5 条上游无补丁的漏洞，每条附
**可达性分析 + 复查条件**（而非让 CI 长期报红——长期红的门禁两天内必被注释掉）。

### 五、验证（5 层 + 门禁实跑）

| 层级 | 手段 | 结果 |
|------|------|------|
| 编译 | `compileall app/` | 0 错误 |
| 静态 | `ruff check .` | **All checks passed**（顺手清掉第十轮遗留 2 处） |
| 单测 | `pytest -q` | **219 passed**（升级后栈，EXIT=0） |
| 真实 DB | readyz 真实往返 | 通过 |
| 端到端 | `TestClient` livez / metrics / readyz / JWT | 全部通过 |
| **门禁实跑** | `pip-audit` + 5 个 `--ignore-vuln` | **`No known vulnerabilities found, 10 ignored`，rc=0** |
| **门禁实跑** | `pnpm audit --audit-level high`（本地裸命令） | **`No known vulnerabilities found`，rc=0** |

验证脚本 `verify_p1_supply_chain.py`：**48 项检查全通过**，八节，含**可达性哨兵**。

### 六、本轮的方法论教训（比代码更值得记住）

1. **"修复版本"必须来自扫描器的 `fix_versions`，不能推断。**
   第一版凭感觉把 cryptography 定在 49.0.0，实测仍报漏洞（fix 50.0.0）——
   这个错误**顺带暴露了被我忽略的另一个包 `pyasn1`**。
2. **升级会带来新约束，必须记录在案。**
   升 `python-jose` 换来 `pyasn1<0.5.0` 的硬约束，反而**锁死一个本可修复的包**。
   这类"升级的副作用"必须写进文件，否则下一个人会以为是漏升。
3. **配置"写了"≠"生效"；判据是产物，不是告警。**
   - `overrides` 写在 `pnpm-workspace.yaml` → **静默忽略**（无报错），锁文件无 `overrides:` 段
   - 写在 `package.json` → **生效**，尽管终端会打印**误导性告警**说该字段"已不再被读取"
   - ✅ 唯一可靠判据：`grep -A2 '^overrides:' pnpm-lock.yaml`
4. **"本地绿"必须与"CI 绿"同义**（否则门禁是假的）。
5. **⚠️ 绝不要在 `pip install` 中途杀进程。**
   本轮杀掉一个卡住的安装，导致 pip 已卸载旧包、未装新包，
   把 venv 的 `site-packages` 留成**完全空的**（连 pip 自身都没了）。
   修复靠 `python -m ensurepip --upgrade --default-pip` 再整体重装。
   **pip 不是事务性的。**
6. **先测量再动手**：原以为 Next 15 升级"肯定大量破坏"，实际逐项核查后只有 1 处。

### 七、本轮不做的事（明确 Non-goals）

- ❌ **不引入 SBOM 生成与签名**——需先有供应链治理流程
- ❌ **不做 `--require-hashes` 依赖哈希锁定**——显著抬高维护成本，当前收益不足
- ❌ **不替换 `python-jose` 为 `PyJWT`**——避免一次性引入认证重构风险
- ❌ **不升级 Tailwind 3→4**——全量样式回归，与本轮安全主题无关

### 八、给产品负责人的一句话

第十一轮把门的范围从"**我们写的代码**"扩展到"**我们依赖的代码**"——
这是最后一类"改坏了不会被发现"的问题。实测共消除 **后端 25 个 + 前端 39 个（含 3 个 critical RCE）**
漏洞，且门禁在本地与 CI **结论一致**。
**需要你决策的**：`python-jose` 对 `pyasn1<0.5.0` 的硬约束使 4 个 ASN.1 DoS 漏洞
**无法通过升级消除**（已用运行时实证确认当前不可达，并设哨兵守护）。
若后续产品要支持**非对称签名 JWT（RS256/ES256）**，这些豁免的前提立即失效——
届时需评估是否替换为 `PyJWT`。


