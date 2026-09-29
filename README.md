# 律小智 AI 法律助手（NLawer）

依据《律小智AI法律助手 PRD v2.0》从零构建的法律科技平台，**双产品线并行**交付 MVP 全链路闭环，零外部依赖（Mock LLM 下即可完整演示）。

- **产品线 A · 律所智能协作平台（LawyerOS）**：IM 接待 → 智能派单 → AI 辅助办案（六段式）→ 证据材料整理 → L1/L2/L3 复核 → 备案归档与开庭支持。
- **产品线 B · 个人/企业法务助手（Legal Copilot）**：四段式智能问答 → 文书自动化/合同审查 → 四维合规扫描 → 企业知识库 → 用量计费。

## 技术栈

- 后端：Python 3.12 + FastAPI + SQLAlchemy 2.0(async) + Pydantic v2 + SQLite(aiosqlite，可切 Postgres)
- 前端：Next.js 14 App Router + React 18 + TypeScript + Tailwind 3.4（四应用 + `packages/ui` + `packages/sdk` pnpm workspace）
- 异步任务：BackgroundTasks + Job 表「先落库再执行」+ 并发槽 + 退避重试
- AI 路由：三档模型（cheap/strong/local），无 Key 自动降级 Mock；SSE `data:{json}\n\n` 逐包流式

## 快速开始

### 1. 后端（:8000）
```bash
cd backend
pip install -r requirements.txt
python seed_demo.py --reset      # 灌入演示租户/账号/法规/类案/模板/材料清单
python smoke_test.py             # 端到端冒烟（可选）
uvicorn app.main:app --reload --port 8000
```

### 2. 前端（四应用，分别启动）
```bash
cd frontend
pnpm install
pnpm --filter app-web    dev    # 法务助手    http://localhost:3000
pnpm --filter app-lawyer dev    # 律师工作台  http://localhost:3001
pnpm --filter app-admin  dev    # 管理后台    http://localhost:3002
pnpm --filter app-im     dev    # 模拟IM客户端 http://localhost:3003
```
前端通过 `NEXT_PUBLIC_API_BASE`（默认 `http://localhost:8000`）直连后端。

## Postgres + pgvector（已在本机跑通）

默认 SQLite 零依赖即可运行；Postgres 模式同样已验证通过（含 pgvector 真实向量检索）。

1. 安装依赖并启动数据库：

```bash
cd backend
pip install -r requirements.txt -r requirements-postgres.txt
cd .. && docker compose up -d db      # pgvector/pgvector:pg16，账号 nlawer/nlawer，宿主端口 5433
```

2. 配置 `backend/.env`：

```bash
# 宿主端口需与 docker-compose.yml 映射一致（默认 5433，避免与本机已在跑的 5432 冲突）
DATABASE_URL=postgresql+asyncpg://nlawer:nlawer@localhost:5433/nlawer
DATABASE_URL_SYNC=postgresql+psycopg2://nlawer:nlawer@localhost:5433/nlawer
VECTOR_BACKEND=auto        # auto 会在 Postgres+pgvector 下自动启用；也可设 pgvector/memory/none
EMBEDDING_API_KEY=         # 留空则只走 BM25，不启用向量召回
```

3. 初始化（建表时会自动 `CREATE EXTENSION IF NOT EXISTS vector`）：

```bash
cd backend
python seed_demo.py --reset
uvicorn app.main:app --port 8000
```

说明：
- 未配置 `EMBEDDING_API_KEY` 时向量检索自动关闭，由 BM25 兜底，不影响主流程。
- SQLite 下 `VECTOR_BACKEND=pgvector` 不生效（自动降级），保证开发态不被破坏。
- 向量召回需要 embedding 服务提供查询向量；当前 `VectorStore.search(None)` 返回空。

### 已验证结果（本机 `docker compose up -d db` 后）

| 验证项 | 结果 |
| --- | --- |
| pgvector 扩展 | `pgvector=0.8.6`（建表时自动 `CREATE EXTENSION IF NOT EXISTS vector`） |
| `knowledge_embeddings.embedding` 列 | `vector(1536)` |
| 种子数据 | 法条 12 / 类案 6 / 模板 23 / 账号 9 / 材料清单 22 |
| 端到端冒烟 `smoke_test.py` | 6/6 通过（Postgres 与 SQLite 双方言均通过） |
| 向量检索 | 余弦排序正确（doc-A 0.9939 > doc-B 0.0），租户隔离生效 |
| `pytest tests` | **219 passed** |

> **方言差异（本次修复）**：SQLite 把布尔存成整数，故 `Boolean == 1` 能跑；Postgres 要求
> `column.is_(True)`。已修正 `dispatch_service` 中的忙闲过滤，其余 `enabled/urgent` 等列本身是
> `Integer` 类型，保持原写法即可。

### 切回 SQLite（零依赖）

将 `backend/.env` 改为：

```bash
DATABASE_URL=sqlite+aiosqlite:///./storage/nlawer.db
DATABASE_URL_SYNC=sqlite:///./storage/nlawer.db
```

SQLite 下 `VECTOR_BACKEND=pgvector` 会自动降级为不启用，开发态不受影响。

## 演示账号（登录页内置一键填充）

| 角色 | 用户名 | 密码 | 用途 |
| --- | --- | --- | --- |
| 客户 | client | Client@12345 | 模拟 IM 咨询 / 委托 |
| 执业律师(L2) | lawyer_li | Lawyer@12345 | 接单、办案、L2 复核 |
| 高级合伙人(L3) | lawyer_wang | Lawyer@12345 | L3 终审、定稿归档 |
| 律所管理员 | firm_admin | Firm@12345 | 律所运营 |
| 企业管理员 | ent_admin | Ent@12345 | 企业知识库 |
| 企业员工 | ent_user | Ent@12345 | 法务助手 |
| 平台管理员 | admin | Admin@12345 | 管理后台 |

## 测试

```bash
cd backend
python -m pytest tests -q     # 单元测试：复核状态机 / 强制复核 / 引用校验 / 派单规则 / Job 模型
                              #          + 越权 / 并发扣减 / 审计保留期 / 内容审核 / 可观测性 / 分页语义
python smoke_test.py          # 端到端冒烟：接待→派单→接单→L3复核定稿→归档+材料包→四段式问答→合规扫描
```

单元测试覆盖 PRD 硬约束：非法复核流转拒绝、未确认不可定稿/不可归档、复核级别不足拒绝、
五类强制复核命中、引用缺失判生成失败、派单规则优先级短路、Job 断点续跑与状态推进。

## CI 与代码质量门禁

`.github/workflows/ci.yml` 提供**四个** job，**全部无需外部服务边车容器**（延续"零依赖即可启动"的设计）：

| Job | 内容 |
| --- | --- |
| `backend` | `ruff check .` → `compileall` → `pytest tests/`，失败时上传 DB 与临时目录作为诊断产物 |
| `frontend` | `pnpm install --frozen-lockfile` → `pnpm typecheck`（`tsc` 是唯一裁判） |
| `security-audit` | **`pip-audit` + `pnpm audit` 依赖漏洞扫描**（豁免项集中在 `security-allowlist.txt`） |
| `docker-build` | `docker/build-push-action`（仅构建不推送，防"代码能跑但镜像构建不出来"） |

本地手动执行同一套门禁：

```bash
cd backend
python -m ruff check .              # 风格 + 未用导入 + 导入顺序
python -m compileall -q app/        # 语法
python -m pytest tests/ -q          # 行为

cd ../frontend
pnpm typecheck                      # 4 个应用 + 2 个包的类型检查
```

> `verify_*.py` 与根目录一次性脚本在 `pyproject.toml` 中走 `extend-exclude` 排除——
> 它们必须在 `import app.*` **之前**设置 `DATABASE_URL`，E402 在此处是设计使然。

## 可观测性

### 指标（`/metrics`，Prometheus 文本格式）

零新增运行期依赖，自建 `app/core/metrics.py`（约 150 行）。导出 12 项指标：

| 类别 | 指标 |
| --- | --- |
| HTTP | `nlaw_http_requests_total`（方法/路径/状态码）、`nlaw_http_request_duration_milliseconds`（直方图）、`nlaw_http_requests_in_progress` |
| 安全 | `nlaw_rate_limit_hits_total`、`nlaw_csrf_rejections_total`、`nlaw_unhandled_errors_total` |
| 内容安全 | `nlaw_moderation_blocks_total`、`nlaw_moderation_report_pending` |
| 业务/队列 | `nlaw_quota_exhausted_total`、`nlaw_job_queue_pending`、`nlaw_job_queue_completed_total` |
| 合规 | `nlaw_audit_write_failures_total` |

**路径自动归一化**：`/api/v1/cases/{uuid}` 会折叠为 `/api/v1/cases/{id}`，避免指标基数随业务量线性膨胀。
**探针与 `/metrics` 自身不计入指标**（否则抓取频率变化会造成指标抖动）。

```bash
curl http://localhost:8000/metrics
```

配套 Prometheus 抓取配置与告警规则见 `deploy/prometheus.yml`、`deploy/alert-rules.yml`（9 条 / 5 组）。

## 列表接口性能：`COUNT` 有界化

列表接口的 `total` 采用**有界计数**（`app/core/pagination.py` 的 `count_bounded`），最多数 200 行即停。

**为什么**：`LIMIT` 能让**取数据**凑够一页就停，但 `COUNT(* )` **无法短路**——必须遍历全部匹配行。
55 万行实测（SQLite，关键词 `LIKE` 场景）：

| 查询 | 耗时 |
| --- | --- |
| `COUNT(*)`（无法短路） | **114.95 ms** |
| 数据页（`LIMIT 20`，可短路） | **0.53 ms** |

→ 计数比取数据贵 **217 倍**，而用户等的正是计数。有界计数把扫描代价降到 **0.354ms（51×）**。

```sql
SELECT count(*) FROM (SELECT ... WHERE ... LIMIT 201) t
```

取 `cap + 1` 用于区分「恰好 200 条（精确）」与「超过 200 条（下界）」：

| 数到 | 返回 | 含义 |
| --- | --- | --- |
| 200 | `(200, False)` | 精确值 |
| 201 | `(200, True)` | 下界，前端展示「200+」 |

`Page.total_is_lower_bound` 字段传递该标记（默认 `false`，向后兼容）。
**精度只在"多到用户翻不完"时才降级**；小结果集**零损失**。

> **注**：本项目**不使用** keyset 分页——实测 `OFFSET 19000` 反而快于 `OFFSET 0`
> （`tenant_id` 索引 + `LIMIT` 已足够短路），keyset **收益为零**。详见 `pagination.py` 模块注释。

## 供应链安全：依赖漏洞扫描门禁

`ruff` / `tsc` / `pytest` 检查的是**我们写的代码**，它们**不会**告诉你
**你依赖的代码是否已被攻破**——一个被投毒的依赖能让全部测试与静态检查通过，然后在生产开门。
CI 的 `security-audit` job 就是为这一类问题设的。

### 本地执行（与 CI 同一条命令）

```bash
cd backend
pip-audit -r requirements.txt        # 后端（豁免项见 security-allowlist.txt）
cd ../frontend && pnpm audit --audit-level high   # 前端
```

### 豁免机制：门禁必须「第一天就是绿的」

长期报红的门禁活不过两天（第九轮的教训），因此上游**无补丁**的漏洞集中登记在
`backend/security-allowlist.txt`，**每条附可达性分析 + 复查条件**，而不是硬编码 `--ignore-vuln`。

当前豁免 5 条，理由都是「**不可达 + 无升级路径**」，且经**运行时实证**而非读代码推断：

| 豁免 | 理由 |
| --- | --- |
| `PYSEC-2026-1325`（ecdsa） | 项目从不 `import ecdsa`；仅 HS256；上游明确声明该类侧信道**不修** |
| `pyasn1` 4 条 | `python-jose 3.4.0` 硬约束 `pyasn1<0.5.0`，修复版 0.6.3/0.6.4 **装不上**；且 HS256 路径**从不载入 pyasn1** |

> **可达性哨兵**：`verify_p1_supply_chain.py` 会在
> ① 项目引入非对称算法（`RS256`/`ES256`…）、或 ② `pyasn1` 修复版变得可安装时**主动报失败**，
> 防止豁免静默腐烂。

### ⚠️ 前端：本地必须走官方 registry

开发机默认 registry（华为云镜像）**不支持 audit 接口**（返回 405）。
这会让本地 `pnpm audit` **一个漏洞都不报**，而 CI 上却报出 39 个（含 3 个 critical RCE）。
**"本地绿"与"CI 绿"不同义的门禁比没有门禁更危险**——它会让人误以为已经检查过了。
因此 `frontend/.npmrc` 把 registry 指向官方源，保证**本地与 CI 结论一致**。

### 验证

```bash
cd backend
python verify_p1_supply_chain.py     # 48 项：门禁产物 / 升级状态 / 扫描结果 / 应用可启动 / 可达性哨兵 / 前端
```


### 健康检查（探针语义拆分）

| 端点 | 语义 | 检查内容 | 失败后果 |
| --- | --- | --- | --- |
| `/api/health/livez` | **进程是否存活** | 不碰任何外部依赖 | 编排器**重启**容器 |
| `/api/health/readyz` | **能否接流量** | 真实 `SELECT 1` DB 往返 | 编排器**摘流量**（返回 503） |
| `/api/health` | 综合自检 | 含 LLM 档位、审核、审计保留期等配置自查 | 供人排查，**不宜做容器探针** |

> ⚠️ **不要把 `/api/health` 用作容器探针**：它会因 LLM Key 未配置等原因报 `degraded`，
> 那些与"容器能否服务请求"无关，会导致进程健康却反复重启。

### 结构化日志

```bash
LOG_FORMAT=json   # 生产：每条日志一行合法 JSON，采集器可直读
LOG_FORMAT=text   # 默认：人眼可读（本地开发）
```

- `request_id` 通过 `contextvars` 穿透到 service 层深处，便于按请求串联日志
- 异常栈在 JSON 模式下**重建为纯文本**（loguru 的彩色多行栈会破坏"一行一条 JSON"的约定）
- 密码 / 令牌 / API Key 等字段自动脱敏

## 前端体验

- **深色模式**：四应用统一支持，右下角悬浮按钮切换，状态持久化到 `localStorage`，首屏无闪烁。
  基于 Tailwind `darkMode: 'class'`，共享组件已内置 `dark:` 变体，并在各应用 `globals.css`
  通过 `.dark` 作用域统一映射页面级 `slate/white` 表面与文本（深色画布 `#0B1120`、卡片 `#1E293B`）。
- **加载与空态**：`@nlaw/ui` 提供 `Skeleton` 骨架屏与 `EmptyState` 空态组件，已接入派单池、
  案件列表、驾驶舱 KPI 等数据加载场景。

## 关键设计要点

- **复核硬约束**：`ReviewFSM` 合法转移白名单 + `assert_transition`，非法流转（未确认定稿、未定稿归档）一律拒绝；L2→L3 升级流转全程留痕。
- **强制复核命中器**：法律意见书 / 标的额≥50万或涉人身婚姻家庭刑事 / 高风险文书 / 对外合规报告 / 客户要求正式意见 → 自动升级要求级别。
- **引用溯源强校验**：AI 落库前校验结论性内容必须带有效引用，缺失视为生成失败。
- **租户隔离**：业务表继承 `TenantMixin`，查询层统一过滤 + 读取前后双重校验（知识库/卷宗），满足「企业私有知识隔离率 100%」。
- **引用时效标记**：法条展示生效日期与「被新法替代」提示，避免引用失效条款。
- **用量计费**：实时扣减看板，超套餐额度自动转工单（标准价/加急价）；收入预测采用分项系数法。
- **原子额度扣减**：条件 `UPDATE ... WHERE used_count < limit_count` + `rowcount` 判定，
  由数据库保证原子性（不是"先读后写"，避免并发下丢失更新导致少计费）。
- **审计保留期（等保 2.0 三级）**：`保底留存 ≥180 天 → 到期归档（gzip JSONL + sha256）→ 归档校验通过后清理`；
  配置低于下限时告警并强制抬到 180；`purge()` 默认 dry-run 且写墓碑审计。
- **内容安全审核**：《生成式人工智能服务管理暂行办法》第十四条要求——输入侧"停止生成"、
  输出侧"停止传输"（SSE 跨片段滑动窗口）、法定留痕、上报义务状态跟踪。

## 目录

```
backend/app
  api/v1/      auth conversations dispatches cases analyses evidence reviews archives
               qa documents compliance knowledge billing jobs ws
  services/    conversation_engine intent_service grading_service dispatch_service
               case_copilot evidence_service review_service archive_service
               qa_service document_service contract_review compliance_service
               knowledge_service billing_service citation_service job_service notification_service
  workflows/   review_fsm forced_review dispatch_rules case_fsm
  models/      (tenant/user/case/dispatch/analysis/evidence/review/archive/...)
backend/tests/ 复核状态机 / 强制复核 / 引用校验 / 派单规则 / Job 模型
               越权 / 并发扣减 / 审计保留期 / 内容审核 / 可观测性
frontend/apps  web(3000) lawyer(3001) admin(3002) im(3003)
frontend/packages  ui  sdk  types
deploy/         prometheus.yml  alert-rules.yml
.github/workflows/  ci.yml
```
