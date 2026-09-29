# 第十五轮（后半）：「已修复 ≠ 有判据」专项扫荡

> 日期：2026-09-20 ｜ 团队：ProductStrategyTeam
> 姊妹篇：`round15-p0-decomposition-and-register-regression-2026-09-20.md`（前半：P0-12 分解 + 注册越权）

---

## 1. TL;DR

前半轮在补注册越权判据时撞见一个模式：**P0-1 的修复代码里自己写着「已修复」，
但全量回归里没有任何一条用例会因为它变红**。本轮把这个模式当成**一类问题**
而不是一个孤例去扫，结果在 16 项 P0 里又撞出 **3 个同族缺口 + 1 个真缺陷**：

| 编号 | 扫荡结论 | 本轮动作 |
|------|----------|----------|
| P0-6 限流 | **上传档限流从未生效**（40 个 POST 路由中命中 **0 个**） | 修 `middleware.py`（后缀→前缀匹配）+ 18 条判据 |
| P0-14 复核越权 | 有行为级判据，但**不在门禁内**；同目录静态脚本的判据是**假绿** | 补 5 条 pytest 判据（进 CI） |
| P0-3 LLM 生产守卫 | **向量侧有判据、LLM 生成侧零判据** | 补 5 条 pytest 判据 |
| P0-11 CORS | **CORS 半边零判据**；且通配+凭据会回显任意 origin（Starlette 已证实） | 加生产启动守卫 + 4 条判据 |

**四个缺口全部做了故障注入**：把历史缺陷重新塞回去，确认判据转红，再还原确认转绿。
没有这一步，「补了 N 条用例」只是一个数字。

---

## 2. 核心结论卡片

### 2.1 真正的结论不是「补了 32 条用例」

是这一条：**「已修复」是一个关于代码的状态，不是一个关于门禁的状态。**
两者之间没有推导关系。本轮 4 个缺口里，3 个的修复代码**写得完全正确**——
问题只在于没有任何判据能发现它被删掉。

### 2.2 🚨 最硬的一条：留档脚本 ≠ 门禁

`backend/verify_security_fixes.py`（15 项）与 `verify_security_fixes_e2e.py`（6 项）
在 2026-09-12 修复记录里被写作「**验证脚本已留档**」。实测它们今天的下场：

- **两者在 `tests/`、`conftest.py`、`pyproject.toml`、`.github/` 中引用数 = 0**
  ⇒ CI（`.github/workflows/ci.yml:78` 跑 `pytest tests/ -q`）**一条都不会执行**。
- `verify_security_fixes_e2e.py` 硬编码 `DATABASE_URL=./storage/verify_e2e.db`（第 16 行），
  库文件跨运行保留 ⇒ **注册用例从第二次运行起永久 409 红**。
  实测该库里 `hacker_p0_1` 的 `created_at = 2026-09-13 01:39` ⇒
  **这条已经红了 8 天，无人发现**——正因为它不在门禁里。
- 静态脚本的 `P0-14b` / `P0-14c` 用**子串命中**判据（`"_review_or_404(" in src`）。
  实测：把守卫的租户比对删掉（注入 A）→ **15/15 全绿**；
  把端点里的守卫调用注释掉（注入 B）→ **15/15 全绿**。

> 反过来说：`e2e` 脚本的 P0-14 项是**真判据**（注入 A 下正确转红）。
> 所以 P0-14 的准确表述不是「零判据」，而是「**有判据，但不在门禁内；
> 同一份留档里的另一半判据是假的**」。

---

## 3. 四个缺口逐个说

### 3.1 P0-6：上传档限流从未生效（**真缺陷，不是判据缺失**）

`app/middleware.py` 原实现用**后缀**匹配：

```python
UPLOAD_PATH_SUFFIXES = ("/evidence/cases",)
...
if any(path.endswith(s) for s in UPLOAD_PATH_SUFFIXES):
```

而真实路由是 `POST /api/v1/evidence/cases/{case_id}`——**运行时路径以案件 ID 结尾**，
`endswith("/evidence/cases")` 恒为假。

不是靠读代码断定的：从 `create_app()` 枚举全部 POST 路由实测档位分布，
得到 `upload10` 命中 **0/40**。

**修法**：新增 `UPLOAD_PATH_PREFIXES = ("/api/v1/evidence/cases/",)`，`_resolve_limit`
改用 `startswith`。保留旧常量名以免破坏外部引用，但注释标明其语义有误。

⚠️ 中途差点写反判据：第一次枚举路由只取顶层 `app.routes`，拿到的是**不含 `/api/v1`
的相对路径**，照它写判据会得到「auth 档也是死的」这种**假红**。
⇒ **枚举类判据必须先验证枚举结果本身是对的。**

### 3.2 P0-14：复核越权（5 条判据，含 HTTP 攻击者路径）

`_review_or_404()` 写得很对，但 `tests/` 对 `/api/v1/reviews` 与 `_review_or_404`
**零命中**。补 `tests/test_review_tenant_guard.py`：

- G1 跨租户 ⇒ `NotFoundError`
- G2 同租户 ⇒ 正常返回（**干净对照**）
- G3 跨租户与「不存在」**错误码+消息一致**（返回 403 会泄露编号存在性）
- G4 HTTP 层以租户 B 身份 `POST /reviews/{id}/edit` ⇒ 404
- G5 **枚举路由**：凡带 `{review_id}` 的写端点都必须调用守卫

🚨 **G5 第一版是假绿**，而且是被故障注入抓出来的：把调用整行注释掉成
`# await _review_or_404(...)` 后，`"_review_or_404(db" in body` **仍然命中注释里的函数名**。
改成 `ast` 找真实 `Call` 节点后才转红。

> 同一处还有第二个改进：G5 最初写死 `["edit_review", ...]` 四个函数名。
> 硬编码清单只能防住**已经出过事的那一个**端点，将来新加一个漏守卫的写端点照样全绿。
> 改成按路由枚举后，判据盯的是**性质**（带 ID 的写操作）而不是**名单**。

### 3.3 P0-3：LLM 生成侧生产守卫零判据

`ai/router.py:98` 的生产守卫，`tests/` 里 `MockProvider` **零命中**。
容易看错的两处：

- `test_vector_retrieval.py::test_production_without_embedding_key_fails_fast`
  只覆盖**向量侧**（`embeddings.build_embedding_client`），与 LLM 生成侧无关；
- `test_contract_review_llm.py:680` 里出现了 `ConfigurationError`，但那是
  **注入一个假的、会抛错的 router** 去测下游「不计费 / 不泄露」——
  它**假设守卫已经抛错**，并不验证守卫会抛错。差一跳，那一跳正是 P0-3。

补 `tests/test_llm_production_guard.py`（5 条）：非生产落 Mock（干净对照）、
生产三档全覆盖抛 `ConfigurationError` **且消息点名缺失的环境变量**、
生产配好 Key 时不抛错（防过度收紧）。

两次注入分别验证：删掉守卫分支 ⇒ 3 条转红；把 `_ENV_KEY_NAME[LOCAL]`
写成 STRONG 的变量名 ⇒ **只有 LOCAL 参数转红**（证明参数化不是摆设）。

### 3.4 P0-11：CORS 半边零判据 + 一个静默的全开放开关

`grep -rli cors tests/` 改动前 **0 命中**（CSRF 半边有 `test_csrf.py`）。

风险不在代码而在配置：`main.py` **硬编码** `allow_credentials=True`
（Cookie 承载 refresh token 需要它），唯一开关是 `CORS_ORIGINS`。
而 Starlette 在「通配 + 凭据」下走 `starlette/middleware/cors.py:167`：

```python
if self.allow_all_origins and self.allow_credentials:
    self.allow_explicit_origin(headers, origin)   # 原样回显请求方 origin
```

**无条件回显**（不需要请求带 cookie）⇒ 任何站点可读带登录态的跨域响应。
把 `CORS_ORIGINS=*` 写进生产是极常见的「先让它跑起来」操作，改动前会**静默**生效。

**修法**：按 P0-3 的同款原则（显式失败优于静默错误），新增
`main.py::_assert_cors_origins_safe()`——生产环境含 `*` 直接拒绝启动。
补 `tests/test_cors_credentials_guard.py`（4 条），其中 C4 是**第三方行为钉子**：
把 Starlette 的回显行为本身钉住，将来升级依赖若改变语义会先红。
（它不是在背书该行为，docstring 里写明了。）

### 3.4b P0-13：匹配器有 30 条判据，「拦不拦得下来」0 条

`tests/test_moderation.py` 有 **30 条**用例（归一化、分隔符/零宽/全角/base64/URL 编码
绕过、严重度取最大、不泄露命中词、流式窗口……）——但它们**全部停在匹配器这一层**：

- `grep -rln "ModerationService" tests/` ⇒ **0 命中**
- `grep -rn "ContentBlockedError" tests/` ⇒ **0 命中**
- 而 `ModerationService(db).check(...)` 被 **6 处端点**调用
  （`qa.py:52,67`、`documents.py:92,147`、`compliance.py:37`）

**实测决定性证据**：把 `check()` 改成一律返回 PASS（**拦截完全失效**）后重跑——
`test_moderation.py` 的 **30 条全部保持绿**，一条都没红。
对算法备案而言这两层同等重要：第十四条要求「**保存有关记录**」，
而「识别出来了但服务没拦」既不会留痕、也不会拦。

补 `tests/test_moderation_enforcement.py`（**7 条**）：

| 判据 | 内容 |
|------|------|
| M1 | 服务层 `check()` 对硬违规返回 `blocked=True` |
| M2 | **干净对照**：正常法律咨询不得误杀（否则「一律拦」也能让 M1 绿） |
| M3 | 拦截落到 `moderation_records`（独立会话提交，外层回滚也丢不掉） |
| M4 | 拦截写 `audit_logs`（`CONTENT_BLOCKED`） |
| M5 | **HTTP 攻击者路径**：`POST /api/v1/qa` 提交硬违规 ⇒ 422 + `CONTENT_BLOCKED` |
| M6 | 设计约束：`MODERATION_ENABLED=False` 一律放行（反向对照） |
| M7 | 拦截让 `moderation_blocks_total` 递增（**备案巡检依据**） |

注入验证：拦截完全失效 ⇒ **5 红 / 2 绿**（M1/M3/M4/M5/M7，M2/M6 保持绿）；
仅破坏指标 ⇒ **M7 单独红**（M7 就是为此而加的：指标不影响功能，只影响"看不看得见"，
是最容易被顺手删掉的东西）。

#### 这一段里我自己踩的两个坑（都靠实测兜住）

1. **判据方向反了**：拦截留痕走 `audit._get_detached_factory()`，它按
   `settings.DATABASE_URL` 建**自己的 engine**，与测试自建库不是同一个
   ⇒ 修夹具前，「留痕其实成功」时判据红、「留痕被破坏」时判据**绿**。
2. **假红**：M7 第一版按代码注释写了标签 `side="input"`，而指标实际定义的是
   `stage`（`metrics.py`）⇒ 永远读不到 ⇒ **恒红**。
   它"在注入下红了"纯属巧合，不是检出能力。⇒ 修正后才取得上面的数字。

### 3.5 反向体检：#16 / #5 判据是真的，#8 才是缺口

🧭 这一节是刻意加的。**如果扫荡只报缺口、不报「这几项其实是好的」，
那它就不是体检而是猎巫**——而且会让「已修复 ⇒ 存疑」变成新的默认假设。
所以我对上一节没深挖的三项也做了同样的故障注入：

| P0 | 注入方式 | 结果 | 结论 |
|------|----------|------|------|
| **#16 合同审查** | `documents.py` 计费门控改回 `billable = True`（原始缺陷：无条件收 99 元） | **5 条转红**（四条「不计费」全中 + 降级审计），32 条保持绿 | ✅ **判据真实有效**，无需补 |
| **#5 Job 幂等** | `job_service.claim_job` 去掉 `Job.status.in_(claimable)`（任何状态都能抢占） | **3 条转红**（独占性 / 终态拒绝 / RUNNING 默认拒绝），14 条保持绿 | ✅ **判据真实有效**，无需补 |
| **#8 审计覆盖** | ①删除 `void_review` 的 `record(...)` ②新增一个无留痕写端点 | ①A1/A3/A4 转红 ②A3/A4 转红 | ⚠️ **此前零判据** |

### 3.6 P0-8：留痕**覆盖**没有判据（棘轮式收口）

`tests/test_audit_log.py` 测的是 `record()` 这个 **helper 本身**（落库字段、截断、
上下文隔离、异常吞掉）——它证明「helper 好用」，**证明不了「写端点真的调用了它」**。

AST 实测（`app/api/v1/**`）：写端点 **42** 个，端点层调用留痕的 **21** 个，
其余 **21** 个未调用。其中 `auth.*` 由 `auth_service` 内部 `record()` 代劳
（`auth_service.py:84,132`）、`complaints.*` 由投诉行本身承载（含 `ticket_no`/IP/UA/时限）
——**这两类不是缺口**；剩下的（会话、问答、通知已读、文书起草、计费、任务重试等）
确实没有任何 `audit_logs` 留痕。

**「这 21 个里哪些必须补」是产品/合规裁定，不是工程能单方面决定的**
（会话消息、问答是高频写，全量入审计表的成本与价值需要权衡）。
所以新建的 `tests/test_audit_coverage.py` **不做绝对覆盖断言**，只钉两件能证明的事：

- **不许退**：已留痕的 21 个端点任何一个被删掉 `record(...)` ⇒ 红（含自检：基线名字
  必须仍是真实端点，否则改名会让判据静默失去保护）
- **不许涨（棘轮）**：无留痕写端点数量**不得超过 21** ⇒ 新加写端点必须补留痕或显式说明
- **按性质**：`/reviews` 下所有带 ID 的写端点必须留痕（防将来新加的复核端点漏留痕）

⇒ 旧账慢慢算（走 Q-F），**新账不允许再欠**。

🚨 顺带发现本判据的一个已知盲区，写在这里以免被误读为全覆盖：
若留痕调用被写成 `if False: await record(...)`，**AST 仍能看到 Call 节点 ⇒ 判据不红**。
（与 §3.2 的「注释里提到守卫」是同一族：结构判据只认「有没有这个调用」，
不认「这个调用能不能执行到」。彻底解决要靠行为级断言，代价高，本轮未做。）

---

### 3.7 P0-2：向量检索——判据只钉住了「一半」，且整条链路**没有调用方**

这是本轮**第二硬**的一条（仅次于 §2.2 的留档脚本）。它有两层，第二层比第一层严重。

#### 第一层：那层「保护」删掉之后，17 条用例一条都不红

`app/rag/retriever.py::search()` 的修复长这样：

```python
query_vector = await self._embed_query(query)
if query_vector is not None:              # ← 这层保护
    res = self.vector_store.search(query_vector, top_k=top_k, tenant_id=tenant_id)
```

三次故障注入的实测结果：

| 注入 | 做法 | 结果 |
|------|------|------|
| A（原缺陷） | `query_vector = None` | ✅ `test_vector_recall_adds_beyond_bm25` 红，**1 红 / 16 绿** |
| B（更隐蔽的退化） | 返回**零向量**（余弦恒 0，看似"跑过了"） | ✅ 同一条红，**1 红 / 16 绿** |
| C（**删掉保护**） | `if query_vector is not None:` → `if True:` | 🚨 **17 全绿** |

⇒ **「向量必须真的参与召回」有判据，「拿不到向量就跳过」没有判据。**
原因是 `VectorStore.search(None)` 本来就返回 `[]`，删掉保护在行为上**不可观测**——
它是防御性注释，不是门禁。这与 §3.4b 是同一个病：**测试测到了结果，没测到机制。**

#### 第二层：`Retriever` 在产品代码里零实例化（**向量只写不读**）

```
写侧：knowledge_service._index_chunks → KnowledgeEmbedding 表（带 tenant_id）   ✅ 接了
读侧：PgVectorStore.search → 只有 build_vector_store() 造 → 只有 Retriever.__init__ 调
      Retriever 在 app/ 下（排除 app/rag/**）真实引用数 = 0                    🚨 断了
```

`grep -rn "from app.rag" app/` 的命中**只有 retriever.py 自己和测试**。
实际在跑的检索是另一套：`qa_service._retrieve`（SQL 全表拉 + Python `_score_law` 打分）
与 `_tenant_knowledge`（`_overlap` 关键词重叠）——**没有 BM25、没有向量、没有 rerank**。

⇒ 结论必须写准：**P0-2 修的那行代码位于一条没有调用方的链路上。**
「修好了」在这条链路上是真的，「语义检索已恢复」是假的。
这是 `CameraCapture` / `PullToRefresh`（前端三个 0 接入组件）在**后端**的同族复发。

#### 附带发现：内存向量后端**不做租户隔离**

`VectorStore.add/search` 都用 `**_` 吞掉 `tenant_id`；而 `vector_backend=auto`
在非 Postgres 下解析为 `memory`（`app/config.py:192`）⇒
**SQLite 部署一旦启用向量召回，语义检索就是跨租户的**。
目前因为读侧没接入所以是休眠缺陷，但**一旦有人按 P0-2 的本意把读侧接上、又跑在 SQLite 上，它当场变成真事故**。

新增 5 条判据 `tests/test_retrieval_wiring.py`（R1–R5），**逐条做了故障注入归属**。

### 3.8 #4 任务恢复：接线正常，但**重试上限在回收路径上完全失效**（真缺陷，已修）

先说好的一面：`_stale_job_recovery_loop` 确实挂在 lifespan 上（60s 巡检 / 300s 超时），
既有 2 条判据也真实（含「心跳正常的任务不得被误回收」这条反向对照）。

但把预算账算一遍就出问题了：

```
recover_stale_jobs:  status RUNNING → PENDING，claimed_by=None，
                     retry_count 不动          ← 🚨
run_job:             for attempt in range(MAX+1)  ← 每次从 0 重来
                     job.retry_count = attempt + 1  ← 🚨 覆盖写回，还会把回收累计的抹低
```

⇒ 一个**必然失败**的任务（毒药载荷 / 每次都崩在同一步）会
`RUNNING →(心跳超时)→ PENDING → RUNNING → …` **无限重启**，永不进 `FAILED`。
`app/models/job.py:5` 写着「新增 `retry_count` + `max_retries` 上限与指数退避
（原实现 retry 无上限无退避）」——**这个上限在僵尸回收这条路上形同虚设**。

**修复**（`app/services/job_service.py`）：

1. `recover_stale_jobs`：回收消耗一次预算（`retry_count + 1`）；
   已耗尽的僵尸直接标记 `FAILED` 并写 `error_message`，**让它对上层可见而不是静默循环**。
2. `run_job`：`retry_count = attempt + 1` → `(retry_count or 0) + 1`（累加而非覆盖）——
   不改这里，注入场景「崩溃→回收→再崩溃」仍会因为覆盖写回而把预算抹回去。

> ⚠️ 这次改动**改变了生产行为**：过去「永不放弃」现在最多重启 `JOB_MAX_RETRIES`(=3) 次后判失败。
> 这是有意的（无限重启必然更糟），但属于**需要产品确认的取舍**，已列 Q-G。

新增 3 条判据（J1 耗尽不复活 / J2 回收消耗预算 / J3 巡检必须挂载），3 次注入逐条归属。

### 3.9 #15 通知：**反向体检通过**（本项无需补判据）

按纪律，扫荡不能只挑有问题的查。对第十二~十三轮收口的「幽灵通知防线」做了一次注入：
把 `_discard_after_rollback` 的 `_drain(session)` 改成空数组（**回滚不再丢弃待推送队列**）。

结果：**4 红 / 21 绿**，且红的不止是直接那条：

- `test_rollback_does_not_push`（回滚后队列必须清空）
- `test_push_metric_records_rolled_back`
- `test_push_latency_is_observed_for_every_outcome`
- `test_push_metrics_are_conserved`（**四态之和 ≠ 落库总数**）

⇒ 这条防线是**分层**守住的：队列、指标、延迟、守恒四条一起红。
**本项判定：判据真实有效，不补。**

---

### 3.10 P0-7 令牌存储：后端只测了「一个字典」，前端**连测试运行器都没有**

#### 后端：唯一那条断言测的是 `_base_kwargs()`，不是真实响应

`tests/test_csrf.py::test_cookie_config_is_sane` 断言
`auth_cookies._base_kwargs()["httponly"] is True`。这是**唯一**一条，且它测的是
**一个 Python 字典**。两种失效它都抓不到：

| 注入 | 既有判据 | 新增 K1 |
|------|----------|---------|
| `httponly: True` → `False` | ✅ 红（3 个参数） | ✅ 红 |
| **`_issue_and_store()` 绕过 `set_refresh_cookie()`**（Cookie 根本不下发） | 🚨 **全绿** | ✅ **红** |

⇒ 与 P0-8「`test_audit_log.py` 只测 `record()` helper、测不到端点是否调用它」
**同一个病**：**测了组件，没测接线**。故 K1 走真实 HTTP 响应头。

补 6 条：`tests/test_auth_cookie_transport.py`
（K1 响应头 HttpOnly / K2 响应体不回传 refresh / K3 csrf **不得** HttpOnly（反向对照，
防过度加固把双提交打死）/ K4 生产 `SECURE=False` 拒绝启动 / K5 签出的令牌
`exp-iat ≤ 30min` / K6 部署配置 `.env` 同样不得放大）。

#### ⚠️ 这一段我自己踩的坑（第 51 条的同族反向）

K5 第一版断言 `settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60`。
故障注入把**类默认值**改成 1440，跑出来 **绿**。
差点写成「判据未命中」——实际是 **`backend/.env:24` 把它覆盖回 30**
⇒ **注入根本没生效**。

> ⇒ 纪律补一条：**注入之后也要确认「脏态确实是脏的」**，不能只看判据结果。
> 「干净态是绿的」与「脏态确实是脏的」是故障注入的两个前提，缺一不可。
> 修法：K5 改成**直接解签令牌**断言 `exp - iat`（绕开配置来源优先级），
> 再加 K6 单独钉 `.env`——否则「只改 `.env`」仍能绕过 K5。

#### 前端：0 个测试文件、0 个测试运行器

`find frontend -name "*.test.*"` ⇒ **空**；根 `package.json` 无 `test` 脚本；
无 vitest / jest 依赖。⇒ **整个前端面的唯一静态门禁是 `tsc --noEmit` + `next build`**，
而这两者证明不了任何运行期行为（本项目已反复验证：`NEXT_PUBLIC_*` 缺失、
`viewport-fit=cover` 缺失、`CameraCapture` 0 接入，三者都是 `tsc` + `build` 全绿）。

按项目 `evidence/` 探针规范补 `evidence/verify_token_storage.py`（F1–F6），
含 `--self-test`（**6 臂注入全部转红 + 干净样本 0 缺陷**）。

⚠️ 自检第一次跑出 **F4 仍绿**：SDK 里有 **3 处** `fetch`，而 F4 写的是
「全文至少有一处 `credentials: "include"`」⇒ 删掉任意一处**照样绿**。
改成「**每一处** `fetch` 都必须带」后才转红。
**这条值得单独记住：「存在性判据」在多实例场景下等于没有判据。**

---

### 3.11 Q-I 落地：把 `evidence/*.py` 接进 CI（**本轮最后一个缺口**）

§2.2 批判的是「留档脚本 ≠ 门禁」。本轮补的 F1–F6 如果只躺在 `evidence/` 里，
那**本轮自己就是 §2.2 的反例**。所以扫荡收口后按 Q-I 的建议把它接进了 `ci.yml`。

#### 先盘家底：25 个探针现在的真实状态

⚠️ 第一版审计**用错了解释器**（裸 Python 没有 `websockets`/`sqlalchemy`/`pydantic`），
于是 `ModuleNotFoundError` 被当成 `exit=1`（产品缺陷）报了出来 —— 凭空造出一批假红。
换 `backend/.venv/Scripts/python.exe` 全量重跑后才拿到可信分布：

| 退出码 | 个数 | 说明 |
|---|---|---|
| `0` | 8 | api_base_baked / breakpoints / component_wiring(修棘轮后) / enum_domain_drift / migration_enum_defaults / mobile_actionbar / render_e2e / token_storage |
| `1` | 3 | component_wiring（3 个真未接线组件）、conversation_channel_500（**过期口径**，见下）、dark_mode |
| `2` | 4 | im_safe_area / im_tabs / mobile_safe_area / tap_targets（缺 dev server） |
| `124` | 10 | 超时——**其中 design_tokens 是误判**：实测它要 **2m24s**（`rglob` 不剪枝，会走进 `node_modules`/`.next`），最终退 `2`，不是挂死 |

> **纪律**：退出码只是信号，得看红的原因。这一轮光靠退出码下结论会得出完全相反的结论。

#### 三个新发现（都是「门禁要接进来才暴露」的）

**① `verify_conversation_channel_500.py` 在缺陷修好的那天变红了。**
它断言「事实1：写入侧**接受**非法 channel」期望**触发**——那是缺陷成立时的形态。
枚举域收口落地后 `ConversationCreate` 已经**拒绝**非法值 ⇒ 事实1 变成未触发 ⇒ 探针红，
而且结论段会继续打印「缺陷成立」。**一个把「修好了」报成「没修好」的门禁，比没有门禁更糟**
——它会诱使人去"修"一个已经修好的东西。
⇒ 已把期望翻转为「不触发」，并重写结论为**当前**事实：schema 层已挡（已修）/ 模型层与 raw SQL
仍可落脏值（**残留风险**，事实2·4 仍触发，缺 DB 层约束）。
故障注入证明翻转后仍有检出能力：把 `channel` 退回 `str` ⇒ `EXIT=1`；还原 ⇒ `EXIT=0`。

**② `verify_token_storage.py` 的 F4 有个静默失效的窗口。**
F4 要求「**每一处** `fetch` 都要带 `credentials: "include"`」，实现是取 `fetch(` 后 500 字符
找 `include`。自检把第一处改成 `"omit"` 后 **F4 仍然绿** —— 500 字符一路跨到了**第二处**
`fetch`，把它的 `include` 借来用了。⇒ 调用密集时，前一处缺凭据会被后一处掩盖，
这条判据**静默失效**。已改为窗口在**下一处 `fetch(`** 处截断；改完自检 6/6 转红，
真实 SDK 实测仍 `EXIT=0`（说明真实现象尚未发生，但判据以前也拦不住）。

**③ 自检读了真实文件 ⇒ 把产品缺陷误诊成工具缺陷。**
`self_test()` 原先 `good = _read(SDK)`（真实 SDK 当"干净样本"）。于是在真实 SDK 里注入一行
`localStorage.setItem("access_token", ...)` 后，**自检自己挂了**（exit 2），
运行器据此报「工具坏了，先修探针」——而实际是**产品坏了**。
⇒ 已改为**纯合成夹具** `CLEAN_SDK`（字面量）。自检必须与产品**完全解耦**，
否则「自检失败 = 工具坏了」这个语义不成立。

#### 棘轮：`verify_component_wiring.py` 的三个已知缺口

直接让它红 ⇒ 门禁第一天就是红的 ⇒ 两天内必被注释掉（第九轮教训）。
直接豁免整条探针 ⇒ 判据只剩装饰作用，第 4 个组件掉线也没人知道。

⇒ 加 `KNOWN_GAPS`（PullToRefresh / InfiniteList / CameraCapture，均登记 #20 / 任务 #37），
两条**相反的不等式**：

| 方向 | 判据 | 注入实测 |
|---|---|---|
| **不许涨** | 未接线**且不在**豁免表 ⇒ 红 | 把 `InfiniteList` 移出 `KNOWN_GAPS` ⇒ `EXIT=1`（报 `✗ InfiniteList：PREVIEW_ONLY`） |
| **不许赖** | 已接线**却仍在**豁免表 ⇒ 红（提示删条目） | 把已接线的 `TabBar` 塞进去 ⇒ `EXIT=1` |

自检补 4 臂（Q9–Q12）覆盖棘轮本身，共 **12/12 通过**。

#### 落地形态：`evidence/run_ci_probes.py` + `ci.yml` 的 `evidence` job

| 档 | 判据 | 个数 |
|---|---|---|
| `GATED` | 只读源码 / 自带临时 SQLite，**不需要浏览器、dev server、生产产物** | **5** |
| `NOT_GATED` | 需 CDP / 运行中前后端 / `.next` 产物（**逐条写了「要接进来还差什么」**） | 20 |

运行器本身三条判据，且**每条都被合成故障触发过**（`--self-test`，4/4）：

- **判据 0**：`verify_*.py` 必须**全部**被分类 ⇒ 新建 `verify_zzz_tmp_probe.py` 实测 `EXIT=1`。
  这条是刻意的：「新建了探针」和「探针进了门禁」在本项目反复被当成一回事。
- **判据 1**：探针自检与实测**分开跑**，自检挂了 = 工具坏了（不混进产品结论）。
- **判据 2/3**：`exit 2` ⇒ 跳过（不是通过）；**全部跳过 ⇒ 失败**（一条永远跳过的门禁 = 没有门禁）。

端到端注入（在真实 SDK 里写 `localStorage.setItem("access_token", ...)`）：
自检 `0`（工具没坏）→ 实测 `1`（产品缺陷）→ 运行器 `1`，诊断文案为「门禁失败（产品缺陷）」⇒ 还原后 `0`。

⏱ 门禁耗时 **52s**（5 个探针），无新增依赖（复用 backend 的 requirements）。

#### ⚠️ 一个必须报告的观察

**`.github/workflows/ci.yml` 存在，但当前目录不是 git 仓库**（`git rev-parse` 报
`fatal: not a git repository`）。⇒ 这条流水线**从未真正跑过**，本轮所有 CI 改动都只是
**本地按 CI 环境变量预演**（已用 backend job 同一套 env 跑通）。
这与本轮主题同族——**「写了」≠「在跑」**。

---

### 3.12 Q-A 派生核查：「公开自助注册」的滥用防线到底有没有

用户裁定 **Q-A 公开自助注册 / Q-B 找回密码走邮件** 之后，"有没有防线"从可选项变成
**必须回答的问题**。实地核查 5 条事实，并把它们固化成探针
`evidence/verify_register_abuse_defense.py`（已进 `GATED`）。

| # | 断言 | 结论 | 证据 |
|---|---|---|---|
| `RATE_LIMIT_TIER` | 注册端点在限流档位内 | ✅ | `middleware.py:26-30` 含 `/api/v1/auth/register`；20 次/60s/按 IP；`test_rate_limit.py:104,185` 有档位映射与 429 断言 |
| `XFF_TRUST` | 分桶键不得无条件采信 `X-Forwarded-For` | ✅ **本轮已修** | 旧：`_client_ip` 无条件返回 XFF 首段，`config.py` 无 `TRUSTED_PROXY`，测试**零覆盖** XFF |
| `NO_TOKEN_ON_REGISTER` | 注册不得直接签发令牌 | ✅ | `auth.py:82` 只 `return ok(await _brief(...))` |
| `NO_PRIVILEGE_FIELD` | 注册不得接受 `role`/`tenant_id` | ✅ | `RegisterRequest` 仅 username/password/full_name |
| `EMAIL_CAPTURED` | 注册必须采集邮箱 | ❌ **登记** | `RegisterRequest` 无 email ⇒ `users.email` 恒 NULL |

#### 更正一处我自己上一轮写错的事实

上一版台账写"**P0-6 已修的上传档限流不覆盖注册**"——**是错的**。
注册**确实在** `AUTH_RATE_LIMITED_PATHS` 里，而且有端到端 429 测试。
真正的洞不在"有没有限流"，而在**限流的分桶键可被伪造**：换一个 `X-Forwarded-For`
就是新桶。这一条如果不实地看代码，靠"我记得限流没覆盖注册"就会推导出完全错误的建议。
（⇒ `methodology.md` 第 64 条）

#### 顺带翻出两条比"缺凭据"更前置的事实

1. **Q-B「邮件找回」目前物理上无法落地**——不是没有 SMTP，是**注册根本不收邮箱**，
   `users.email` 恒为 NULL。即使今天就拿到 SMTP 凭据，也**没有收件地址**。
2. **Q-A「公开自助注册」在前端零入口**——`apps/web` 只有 `login/`、全目录中文"注册"
   0 命中、SDK 无 `register` 方法。后端端点只能被手工 curl 调用 ⇒ **已裁定、未交付**。
   ⇒ 新增 **Q-N**。

#### 红灯为什么用棘轮而不是"先记在文档里"

放进 `KNOWN_GAPS` 后门禁**首日仍是绿的**（实测 `EXIT=0`），但棘轮咬两个方向，
且**两个方向都已故障注入证明**：

| 注入 | 预期 | 实测 |
|---|---|---|
| 把 `/api/v1/auth/register` 移出限流清单（**不许涨**） | 新缺口 | ✅ `EXIT=1`，报「未登记，棘轮不许涨」 |
| 给 `RegisterRequest` 加 email 但**保留豁免**（**不许赖**） | 已收口未删豁免 | ✅ `EXIT=1`，报「请从 `KNOWN_GAPS` 删掉」 |

#### `XFF_TRUST` 已在本轮收口（**棘轮第一次在真实仓库里真的咬合**）

这条不需要任何外部资源，所以直接修了：

- `config.py` 新增 `TRUSTED_PROXIES`（默认 `"127.0.0.1,::1"` = 只信任本机反向代理）
  + `trusted_proxies_list` 属性；
- `middleware.py::_client_ip` 改为：**仅当直接对端可信**才采信 XFF，且采信时
  **从右往左**取第一个不可信的跳——nginx 用 `$proxy_add_x_forwarded_for` 是**追加**，
  客户端伪造的值留在**左边**，"取首段"恰好取到攻击者写的那个值；
- `_is_trusted` 支持 CIDR，且**解析失败一律判为不可信**（对端可能是主机名 / Unix socket /
  `TestClient` 的 `"testclient"`；在这里抛异常会让整站每个请求 500）。

判据 **R7–R12** 加进 `tests/test_rate_limit.py`，**双向**故障注入：

| 注入方向 | 转红的用例 | 含义 |
|---|---|---|
| 退回旧实现（无条件采信 XFF 首段） | R7 / R8 / R10 / R11 | 防"太松" |
| 改成一律无视 XFF（**修复过头**） | R8 / R9 / R10 / R12 | 防"太严" |
| 正确实现 | 无（24 passed） | —— |

⚠️ **R12 是关键对照组**：直接 `return peer` 也能让 R7/R11 通过，但会把整站塞进同一个桶；
没有 R12，"一律无视 XFF"会被误当成正确实现。

⚠️ **但 R7–R12 测的都是「零件」，不是「整机」**：它们跑在**最小 Starlette 应用**上，
那里只装了 `RateLimitMiddleware` 一个中间件；真实 `create_app()` 还叠着
ErrorHandler / RequestContext / CSRF / CORS，且 `main.py` 是**按 settings 现取现装**的。
⇒ 补 **T8**（`tests/test_auth_register.py`，走 `create_app()` 完整栈）：
从不可信对端连发 3 次注册、每次换一个伪造 XFF，第 3 次必须 429。
注入旧实现后 T8 与 R7/R8/R10/R11 一起转红，失败原因 `assert 200 == 429`
（**换了头就开新桶**）⇒ 不是别的原因误红。

修复落地后，探针立刻报 `XFF_TRUST: HEALED` ⇒ **退出码 1**，逼着我把 `KNOWN_GAPS` 里的
豁免删掉。**这正是棘轮设计的目的**——它让"欠账"会被自动催收，而不是躺在文档里。
删豁免后自检臂 S3 也改挂到剩下的 `EMAIL_CAPTURED` 上，否则「不许赖」这一半就没人证明了。

⚠️ **部署提醒**：网关若不在本机（另一容器/主机），必须把网关地址加进 `TRUSTED_PROXIES`，
否则整站共用一个桶——限流**变严**而非变松，是安全方向的失效，但会影响可用性。

#### 🚨 但应用层的修复**能被部署配置架空**——必须再补两层

**同一天发现的第二个洞**：uvicorn 自带 `ProxyHeadersMiddleware`
（`uvicorn/middleware/proxy_headers.py:32-40,68`）。当 `forwarded_allow_ips` 含 `*` 时，
它取 **`x_forwarded_for_hosts[0]`**——**左起第一个，正是客户端可以随便写的那个**——
然后**直接改写 `scope["client"]`**。

关键在于**时序**：这一步发生在我们的中间件**之前**，于是 `_client_ip` 拿到的"直接对端"
已经是攻击者伪造的值，`TRUSTED_PROXIES` 判断根本不成立 ⇒ **每换一个头 = 换一个桶**，
修复被完全绕过。

该开关来自 `--forwarded-allow-ips` 或环境变量 `FORWARDED_ALLOW_IPS`
（`uvicorn/config.py:333`，默认 `127.0.0.1`），而 `backend/Dockerfile:30` 起 uvicorn 时
**没有指定它** ⇒ 今天没踩到，但**在网关 / CDN / ALB 后面设成 `*` 是极常见的"先让它跑起来"操作**，
且失效是**静默**的（限流分桶 + 审计来源 IP 一起失去意义）。

补两层（**静态 + 运行时，缺一不可**——探针够不到运行时环境，运行时守卫够不到别人改的部署文件）：

| 层 | 实现 | 注入验证 |
|---|---|---|
| 运行时 | `main.py::_assert_forwarded_allow_ips_safe()`：生产环境拒绝启动（与 P0-11 的 CORS 守卫同款原则：显式失败优于静默错误）；非生产只告警 | `tests/test_forwarded_allow_ips_guard.py` F1–F5 |
| 静态 | 探针事实6 `UVICORN_NOT_ALWAYS_TRUST`：扫描 Dockerfile / compose / sh / env / toml | 在 `backend/Dockerfile` 加 `ENV FORWARDED_ALLOW_IPS=*` ⇒ `EXIT=1` 报新缺口；还原 ⇒ `0` |

⚠️ 途中修掉一个我自己写的**顺序依赖**：`app/main.py:346` 有**模块级** `app = create_app()`，
守卫在 **import 阶段**就跑。F1 原本先改环境、再 import ⇒ 异常抛在 `with pytest.raises` **之外**。
（`test_cors_credentials_guard.py::C2` 有同款依赖，只是恰好因为同文件 C1 先跑过才绿。）
⇒ 新测试文件改成**模块级** `import app.main`，脱离执行顺序。

---

### 3.13 顺手清查①：`X-Tenant-Id` 租户切换——**整条边界零判据，且有一个静默缺陷**

多租户隔离是这个产品最核心的边界之一，但清查发现它**没有一条判据**：

| 证据 | 实测 |
|------|------|
| `X-Tenant-Id` 在 `tests/` 下命中数 | **1**，且是 `test_auth_register.py:8` 的 **docstring** |
| 直接测 `get_tenant_context` 的用例 | **0** |
| 4 个相关测试文件（`test_evidence_authz.py:169` / `test_moderation_enforcement.py:267` / `test_review_tenant_guard.py:219` / `test_contract_review_llm.py:624`） | 全部用 `dependency_overrides[get_tenant_context]` **把真实逻辑替换掉了** |

⇒ 既没直接测，也没间接跑过。把 `deps.py:86` 的 `user.role == Role.PLATFORM_ADMIN and`
改成无条件采信，**全量回归会全绿**。

**补的判据**：`tests/test_tenant_context_guard.py`（X1–X5，9 个参数化用例）

| 编号 | 性质 |
|------|------|
| X1 | 非管理员（CLIENT / LAWYER / FIRM_ADMIN / ENTERPRISE_ADMIN）带 `X-Tenant-Id` ⇒ **不得切换** |
| X2 | 平台管理员**可以**切换（反向量：防"把功能焊死"） |
| X3 | 管理员不带头 ⇒ 用自身租户 |
| X4 | **纯空白头部**不得切成 `"   "` |
| X5 | HTTP 层：走真实 `create_app()` + DI，非管理员带头无效 |

**顺带发现并修掉一个静默缺陷（X4）**：原实现是 `if ... and header_tenant:`，
而 **`"   "` 是 truthy** ⇒ 空白头部会被当成合法租户 ID 采信，
`tenant_id` 变成三个空格 ⇒ **全表查不到数据，且不报错**（静默错误，
比抛异常难查得多）。已改成 `(... or "").strip() or None`。

> 这条红是**测试先红、然后确认是真缺陷**——不是"测试写错了改测试期望"。
> 与 `methodology.md` 第 3 条一致：红的时候先问「是判据错还是产品错」。

### 3.14 顺手清查②：路由级权限门控 `require_permissions`——**从未被执行过**

与 3.13 同族，但更严重一点，因为它是**唯一**的路由级权限门控：

| 证据 | 实测 |
|------|------|
| `require_permissions` 在 `tests/` 下命中数 | **0** |
| 实际使用点 | 仅 `app/api/v1/billing.py` 的 **4 处路由级** `dependencies=[...]` |
| `tests/` 请求过 `/api/v1/billing*` 吗 | **没有**（只有 `test_billing_service.py` 测服务层，还把计费服务 monkeypatch 掉了） |
| `dependency_overrides` 覆盖过它吗 | **没有**（覆盖的键只有 `get_db` / `get_current_user` / `get_tenant_context`） |

⇒ 函数体在全量回归里**一次都没跑过**。

**补的判据**：`tests/test_require_permissions_guard.py`（P1–P9 / R1–R3 / W1，21 条）

| 编号 | 性质 |
|------|------|
| P1/P2 | `mode="all"`：齐全放行 / 缺一条 403（**参数化 4 个角色**，防"只挡住某一个角色"） |
| P3/P4 | `mode="any"`：命中一条放行 / 全不命中 403 |
| P5 | `PLATFORM_ADMIN` 的 `*` 通配仍放行（反向量：防过度加固把超管挡在外面） |
| P6/P7 | **HTTP 层**：真实 `create_app()` + 路由级 `dependencies`，无权限 403 / 有权限 200（成对，防"把门焊死"） |
| P8 | 空权限码清单 ⇒ **工厂阶段** ValueError |
| P9 | `mode` 写错 ⇒ 工厂阶段 ValueError |
| R1–R3 | `require_roles`：允许放行 / 不允许 403 / 空清单报错 |
| W1 | **登记型**：`deps.require_roles` 与 `rbac.require_roles` 同名不同义 |

**顺带修掉两个静默失败（P8/P9）**——这两个都不是"缺测试"，是**产品代码本身的坑**：

| 坑 | 后果 | 处理 |
|----|------|------|
| `require_permissions()` 空清单 | `all([]) is True` ⇒ **端点被公开**，一声不响 | 工厂阶段 `ValueError` |
| `mode="anyy"` 拼错 | `all(...) if mode == "all" else any(...)` ⇒ **静默退化成 any**，语义从"全都要"变"有一个就行" | 工厂阶段 `ValueError` |

两者都属于「写错一行，安全边界凭空消失」⇒ 按本轮一贯原则（P0-11 CORS 守卫、
Q-K 的 `FORWARDED_ALLOW_IPS` 守卫同款）：**启动时就炸，而不是等线上被扫**。

**已知隐患（W1，登记不修）**：`deps.require_roles` 返回 `TenantContext`、
`rbac.require_roles` 返回 `User`，两者都在用（`audit_retention.py` / `complaints.py`
用前者，`knowledge.py` 用后者）。已核查 `knowledge.py::create_doc` 自己声明了
`ctx=Depends(get_tenant_context)`，**租户上下文没有丢** ⇒ 目前只是可读性隐患，
不是漏洞。登记 W1 是为了将来谁合并/删除其中一个时强制回头确认返回值语义。

### 3.15 顺手清查③：上传落盘——**唯一的写盘路径，目录穿越无人管**

把同一把尺子从 `deps.py` 扩到整个 `app/`：扫「名字像守卫、但 `tests/` 零引用」的函数，
命中 12 个。逐个看过之后，安全价值最高的是 `storage_service.py`——它是产品**唯一**
把用户上传写到磁盘的地方。

| 证据 | 实测 |
|------|------|
| `save_upload` / `_safe_name` 在 `tests/` 下命中数 | **0 / 0** |
| `test_evidence_authz.py` 覆盖上传端点吗 | **没有**——只测了**读**（`test_client_cannot_read_*` / `test_cross_tenant_denied` / `test_lawyer_can_read_*`），`POST /cases/{case_id}` 从未被请求过 |

**且它真的有洞**。原实现：

```python
rel_dir = os.path.join(tenant_id, subdir)          # ← tenant_id 直接参与拼路径
abs_dir = os.path.join(settings.LOCAL_STORAGE_PATH, rel_dir)
```

而 `tenant_id` 对平台管理员来说就是 `X-Tenant-Id` 头部的**任意字符串**
（`deps.py::get_tenant_context` 本轮只补了 `strip()`，不校验是否为合法路径片段）。
实测解析结果：

| `tenant_id` | 规范化后 | 在存储根内？ |
|---|---|---|
| `../victim-tenant` | `...\backend\victim-tenant\evidence` | ❌ |
| `..\..\windows` | `...\NLawer\windows\evidence` | ❌ |
| `a/../../b` | `...\backend\b\evidence` | ❌ |
| `/etc` | `C:\etc\evidence`（**跨盘符**） | ❌ |

⇒ **静默**目录穿越：不报错、文件照写，只是写进了别人的目录。

**修法**：新增 `_resolve_under_root()`，落盘前 `abspath` + `commonpath` 验证仍在根内，
不同盘符（`commonpath` 抛 `ValueError`）一律按逃逸处理。

> 注：文件名侧原本就是安全的（`_safe_name` 直接丢弃原名、只留扩展名），
> **没人管的是 `tenant_id`** —— 它参与拼路径，却来自请求头。

**补的判据**：`tests/test_storage_path_containment.py`（S1–S6 / A1–A3，26 条）

| 编号 | 性质 |
|------|------|
| S1 | `_safe_name`：9 种恶意文件名 ⇒ 输出必须匹配**形状白名单** `[0-9a-f]{32}(\.[a-z0-9]+)?` |
| S2 | `_safe_name`：空 / None ⇒ 仍安全 |
| S3 | `_resolve_under_root`：正常片段**能**解析（反向：防焊死） |
| S4 | `_resolve_under_root`：5 种逃逸片段 ⇒ 拒绝 |
| S5 | `save_upload`：正常租户 ⇒ 真落盘且在根内（反向） |
| S6 | `save_upload`：恶意 tenant_id ⇒ 拒绝，**且不在存储根里留下任何东西** |
| A1/A2 | `assert_tenant`：不匹配拒绝 / 匹配放行 |
| A3 | **登记型**：空 / None 目标**绕过**校验（已知语义） |

S1 刻意用**形状白名单**而不是「不得包含 `..`」的黑名单：后者漏一个变形就绿，
前者任何漏网字符都会红。

⚠️ **扫描方法本身有假阳性**：那个 12 个函数清单里，`_assert_forwarded_allow_ips_safe`
其实上轮已经补了 F1–F5，只是测试里没写函数名。**名字统计只能用来找候选，
能不能下「零判据」的结论必须靠注入验证。**

### 3.16 顺手清查④：合规上报链路（第十四条）——**零判据 + 一个语义错配**

《生成式人工智能服务管理暂行办法》第十四条：发现违法内容应当处置**并向主管部门报告**。
产品里对应 `report_to_authority()` + `_persist()` 里的上报状态判定。清查结果：

| 关键词 | 在 `tests/` 下的命中数 |
|--------|----------------------|
| `report_to_authority` | **0** |
| `MODERATION_REPORT_ENABLED` / `MODERATION_REPORT_URL` | **0 / 0** |
| `report_status` / `ReportStatus` | **0 / 0** |

🚨 **注入实证**：把 `needs_report` 置成恒 `False`（上报义务整段消失），
跑既有的 `test_moderation.py` + `test_moderation_enforcement.py`
⇒ **37 passed / 0 failed**。P0-13 那 7 条判据覆盖的是**拦截执行链**，
对「有没有上报」**完全无感**。

**顺带发现并修掉一个语义错配**（不是缺测试，是产品代码的坑）：

`ReportStatus` 定义了四个值（`NOT_REQUIRED` / `PENDING` / `REPORTED` / **`FAILED`**），
但服务层**从来不用 `FAILED`** —— 通道**配好了**却调用失败时，状态也停在 `PENDING`。
而 `PENDING` 的语义是「**需上报但通道未配置** → 人工兜底」：

| 实际情况 | 修复前落的状态 | 运维照此去做 | 真正该做的 |
|---|---|---|---|
| 通道压根没配 | `PENDING` | 去配通道 | ✅ 一致 |
| 通道已配、调用失败 | `PENDING` | 去配一个**本来就配好了**的通道 | ❌ 重发 + 排查接口 |

⇒ statutory 上报被延误，且**不报错**（静默错分）。
已改成：通道已配但失败 ⇒ `FAILED`；`report_note` 也拆成两句，不再把两种失败混成一句话。

**补的判据**：`tests/test_moderation_report.py`（R1–R8，9 条）

| 编号 | 性质 |
|------|------|
| R1/R2 | BLOCK / ESCALATE ⇒ **绝不能**是 `NOT_REQUIRED`（＝上报义务被静默丢弃） |
| R3 | **反向量**：PASS / REVIEW ⇒ `NOT_REQUIRED`（防人工补报队列被海量 REVIEW 淹没） |
| R4 | 通道未配置 ⇒ 返回 `False` **且打 warning**（docstring 承诺「绝不静默丢弃」） |
| R5 | 已配通道 + 失败 ⇒ `FAILED`，**不是** `PENDING` |
| R6 | 已配通道 + 成功 ⇒ `REPORTED`，备注清空（反向） |
| R7 | **假上报**：失败时绝不能标成 `REPORTED`（比不上报更危险） |
| R8 | 上报失败**不得**把业务请求打挂（`except Exception` 吞异常是刻意契约） |

> R5 与 R7 分开成条，是因为它们抓的不是一件事：R5 抓「错分成 PENDING」，
> R7 抓「直接假装成功」——后者会让备案材料显示「全部已上报」。

### 3.17 Q-O 收口：计费端点的**数据边界**（「门里的东西」）

§3.14 补的是**门**（`require_permissions` 的 21 条判据），当时明确写了「没补门里的东西」。
本轮把它收掉。

| 事实 | 实测 |
|------|------|
| `/api/v1/billing*` 在 `tests/` 下的请求次数 | **0** |
| `tests/` 里唯一带 `billing` 的文件 | `test_billing_service.py`——测**服务层**，且把计费服务 monkeypatch 掉 |
| 4 个路由中带租户数据的 | `dashboard` / `work-orders` / `consume`（`project-revenue` 是纯计算） |

**补的判据**：`tests/test_billing_endpoint_scope.py`（B1–B6，6 条）

| 编号 | 性质 |
|------|------|
| B1 | **AST 结构**：传给 `BillingService` 的 `tenant_id` 必须来自 `ctx.tenant_id` |
| B2 | **AST 结构**：`billing` 下**每个**路由都挂在 `require_permissions` 上（**枚举路由**而非写死名单） |
| B3 | HTTP **数据边界**：租户 B 的管理员读工单 ⇒ 只看得到 B 的，看不到 A 的 |
| B4 | HTTP：无 `billing:read` 的角色 ⇒ 403（**产品自己的路由**，不是测试专用路由） |
| B5 | **反向量**：有权限的角色能看到**自己**租户的工单（防功能被焊死） |
| B6 | **登记型**：`POST /consume`（**写**）由 `billing:read`（**读**）放行 ⇒ 派生 **Q-P** |

**判据自身被自己的自检救了一次**：B1 第一版只扫 `tenant_id=` 关键字实参，
实测只命中 **1** 处——因为 `dashboard(ctx.tenant_id, period)` 和
`list_work_orders(ctx.tenant_id, status=status)` 都是**位置参数**。
若不写那条 `assert checked >= 3` 的自检，B1 会**静默退化成空集并报告通过**。
改成用 `inspect.signature` 把形参名映射到实参位置后才覆盖到全部 3 处。

> HTTP 层**刻意不覆盖 `get_tenant_context`**（与 `test_review_tenant_guard.py` 的做法相反）——
> 覆盖掉它就测不到「租户上下文到底从哪来」，而那正是这次要验的东西。

**顺带发现（登记为 Q-P，未擅自改）**：`ROLE_PERMISSIONS` 里**没有 `billing:write`**，
所以 `POST /billing/consume`（**扣减用量**）只能挂在 `billing:read` 上。
是否新增权限码并重排角色矩阵会影响 FIRM_ADMIN / ENTERPRISE_ADMIN 的实际能力，
属**产品决策** ⇒ 已登记，B6 负责在将来有人改动时强制回头确认。

---

### 3.18 顺手清查⑤：证据**写**端点（「门里的东西」另一半）

§3.17 收口计费时留了一句「证据上传端点仍是 0 覆盖」，本轮把它做掉。

| 事实 | 实测 |
|------|------|
| `tests/` 下对 `POST /evidence/cases/{cid}` 的请求次数 | **0** |
| `tests/` 下对 `POST /evidence/{id}/parse` 的请求次数 | **0** |
| `save_upload` 的覆盖 | 有（`test_storage_path_containment.py`），但只测**服务层** |

`test_evidence_authz.py` 的 `CASE_SCOPED_ENDPOINTS` 里**全是 GET**——
读端点锁死了，写端点一个没测。而 `save_upload(tenant_id)` 收到哪个租户，
完全取决于**端点传了什么**：服务层测得再全，端点传错照样串号，
且在单租户测试环境里**照样 200**。

**补的判据**：`tests/test_evidence_upload_scope.py`（U1–U8 / V1–V5，13 条）

| 编号 | 性质 |
|------|------|
| U1 | **AST**：`save_upload` 的 `tenant_id` 必须来自 `ctx.tenant_id` |
| U2 | **AST 顺序**：`_case_or_404` 必须排在 `save_upload` **之前** |
| U3 | **AST**：`Evidence(...)` 落库的 `tenant_id` 必须来自 `ctx.tenant_id` |
| U4 | **运行时**：后台解析任务收到的租户 == `ctx.tenant_id` |
| U5 | HTTP：跨租户上传 ⇒ 404 **且磁盘上一个文件都没多出来** |
| U6 | HTTP：同租户其他客户上传 ⇒ 404，同样不留文件 |
| U7 | **反向量**：本客户上传 ⇒ 200 且文件落在 `<root>/<tenant>/evidence/` 下 |
| U8 | **反向量**：本租户律师上传 ⇒ 200（防归属修复把律师挡在外面） |

> **U2 为什么单独成条**：「先落盘再鉴权」的表现是**干净的 404**，
> 但文件已经写进受害者租户的目录，且 `record()` 在 `save_upload` 之后，
> **连审计都没有**——完全静默的写入。功能测试永远看不出来。

---

### 3.19 由 U1–U8 带出的真实缺陷：`POST /evidence/{id}/parse` **没有客户归属校验**

`_case_or_404` 的客户归属分支早在 `test_evidence_authz.py` 就被钉住了，
但 `reparse` 是按 `evidence_id` 取数的，**没走那个函数**，于是：

```python
ev = await db.get(Evidence, evidence_id)
if ev is None or ev.tenant_id != ctx.tenant_id:      # 只判租户
    raise NotFoundError(...)
ev = await EvidenceService(db).parse(evidence_id)     # 还没传 tenant_id
```

| 后果 | 说明 |
|------|------|
| 同租户客户乙遍历 `evidence_id` | 可读到客户甲材料的 `EvidenceOut` **全字段**，含 `ocr_text` 与 `legality_risks` |
| 且是**写**操作 | `parse` 会**覆盖**既有解析结果（`ocr_text` / `category` / `extracted`） |
| 服务层的纵深防御没用上 | `EvidenceService.parse(evidence_id, tenant_id=None)` **支持**归属校验，端点没传 |

⇒ **守卫按参数名分类，缺口也会按参数名分布**：修了「按 `case_id` 取数」的端点，
按 `evidence_id` 取数的兄弟端点原样保留了同一类泄露，而且因为能写，更严重。
（已上升为 `methodology.md` 第 85 条）

**修复**（`app/api/v1/evidence.py`）：新增 `_evidence_or_404(db, ev, ctx)`，
与 `_case_or_404` 同口径；`case_id` 为空时退到 `uploaded_by` 兜底；
同时把 `tenant_id=ctx.tenant_id` 传给 `EvidenceService.parse`。

**补的判据**：V1–V5

| 编号 | 性质 |
|------|------|
| V1 | HTTP：客户乙重解析客户甲的证据 ⇒ 404（**本轮的红的起点**） |
| V2 | HTTP：不存在的证据 ⇒ 404（防退化成 200/500） |
| V3 | **反向量**：客户甲重解析自己的 ⇒ 200 |
| V4 | **反向量**：本租户律师重解析 ⇒ 200 |
| V5 | **AST**：`EvidenceService.parse` 必须带 `tenant_id`（纵深防御不得绕过） |

---

### 3.20 判据自己的两个洞（本轮各被抓到一次）

1. **U4 第一版是废的**：`jobtenant` 注入臂跑出 **13 条全绿**。
   根因不在产品代码，而在**夹具**——所有测试用户 `user.tenant_id` 恒等于
   `ctx.tenant_id`，两个恒等的量之间**任何判据都测不出差异**。
   修法：加一个 **平台管理员**（自身租户 `platform`，靠 `X-Tenant-Id` 切到目标租户），
   这是唯一能让两者不等的身份。修完 `jobtenant` 臂 ⇒ 1 红、`rowtenant` 臂 ⇒ 2 红。
2. **§3.16 的 R4 留了个没用到的 `caplog` 参数**：平时因为 logging 插件在而"通过"，
   本轮用 `-p no:logging` 跑 ⇒ `fixture 'caplog' not found` ⇒ **error**。
   项目用 loguru，告警是靠 `logger.add(sink)` 抓的，caplog 全程没参与。
   已删除该参数（9 passed）。

⇒ 两条都进了 `methodology.md`（第 83 / 84 / 86 条）。

---

### 3.21 CSRF：**零件测了 10 条，整机一条没有**

`tests/test_csrf.py` 看上去覆盖充分（生成 / 校验 / 过期 / 伪造签名 / Cookie kwargs），
但逐条看下来全是**令牌原语**。整机层面的实测：

| 事实 | 实测 |
|------|------|
| `tests/` 下对 `/api/v1/auth/refresh` 的请求 | **0** |
| `tests/` 下对 `/api/v1/auth/logout` 的请求 | **0** |
| `tests/` 下出现 `X-CSRF-Token` | 只在 `test_auth_cookie_transport.py` 的**注释**里 |
| `CsrfMiddleware` / `_needs_check` 被测试引用 | **0** |

⇒ `CSRF_PROTECTED_PREFIXES` 保护的正好就是那两个端点，而它们从未被请求过——
**中间件挂没挂上、拦不拦得住，此前无人验证**。与 §3.14（权限门控从未被执行）
是同一类问题：**测了算法，没测装配**（`methodology.md` 89）。

**补的判据**：`tests/test_csrf_middleware_dispatch.py`（C1–C7，7 条）

| 编号 | 性质 |
|------|------|
| C1 | **整机**：无令牌 POST 受保护端点 ⇒ 403 且 `error.code == "CSRF_TOKEN_INVALID"`（只断言 403 不够：401/422 也是 40x） |
| C2 | **反向量**：合法双提交 ⇒ **不是** CSRF 拒绝（防中间件被焊死） |
| C3 | cookie 与 header 值不一致 ⇒ 403（双提交的意义） |
| C4 | `_needs_check`：Bearer **豁免** + 同路径无 Bearer 必须查（**成对**） |
| C5 | `strict_all_writes=True` ⇒ 保护前缀外的写也查；GET 永远不查 |
| C6 | `enabled=False` ⇒ 一律不查（**登记现状**：关掉就是完全不查，没有第二道防线） |
| C7 | **登记型**：默认 `CSRF_PROTECTED_PREFIXES` 必须覆盖 refresh / logout |

> C1/C2/C3 用**真实 `create_app()`**，但**关掉限流**再打：
> `/api/v1/auth/refresh` 正在认证档限流名单里（`test_rate_limit.py:96`），
> 不隔离会因为「撞桶」假红，把限流的行为误算到 CSRF 头上。

**零判据反证（第 4 次）**：把 `_needs_check` 改成恒 `False`（**中间件完全不查**），
跑既有的 3 个 CSRF 相关文件（`test_csrf.py` / `test_auth_cookie_transport.py` /
`test_auth_register.py`）⇒ **29 passed / 0 failed**。

---

### 3.22 其余候选的裁定（12 个扫完）

| 候选 | 裁定 | 依据 |
|------|------|------|
| `_check_builtin` / `_check_external` | **假阳性**，不补 | `test_moderation.py` 通过 `ContentModerator(backend="builtin")` 已覆盖，还有 `test_external_backend_without_key_falls_back_to_builtin`（`methodology.md` 73：名字扫描只能找候选） |
| `_needs_check` | 真零判据 ⇒ §3.21 | — |
| `permissions_for` | 补 3 条（R1–R3） | 未知角色兜底空集、每个 `Role` 必须有条目 |
| `health_check` | 补 3 条（H1–H3） | 被请求过 3 处但**响应内容零断言**；生产脱敏分支从未执行 |
| `_validate_moderation_backend` | 补 3 条（M1–M3） | 启动期校验完全没测；失败模式是「运行时才炸」 |

**补的判据**：`tests/test_role_health_config_guards.py`（R1–R3 / H1–H3 / M1–M3，9 条）

- **R2 用枚举而不是写死清单**：遍历 `Role` 断言每个成员在 `ROLE_PERMISSIONS` 里有条目。
  新加角色忘了配权限**不报错**，只会让该角色一无所能（表现为「登录后每个按钮都 403」）。
- **H1/H3 成对**：生产 `llm_providers` 必须是 `None`（不暴露模型编排）；
  非生产必须有明细（本地排查「为何走了 Mock」就靠它）。
- **H2**：生产 + LLM 未配齐 ⇒ `status == "degraded"`。报成 `healthy` 的后果是
  **监控永远绿**，用户侧才是 500。
- **M1/M2 分开**：M1 判**取值域**，M2 判**跨字段依赖**（`external` 必须配 Key）。

### 3.23 本轮注入脚本自己踩的两个坑（都会伪装成「判据通过」）

1. **锚点不唯一**（`methodology.md` 87）：`main.py` 里有 **2 处**
   `if settings.ENVIRONMENT == "production":`（另一处是 `_assert_cors_origins_safe`），
   `replace(..., 1)` 打到了 CORS 守卫 ⇒ **注入根本没生效**，跑出「9 passed」。
   ⇒ 锚点要带相邻注释使其唯一；每臂执行后 `grep -n INJECTED` 确认行号。
2. **多文件注入没逐臂还原**（`methodology.md` 88）：注入脚本按原文件重写**单个**文件，
   只改 A 的臂不会清掉上一臂留在 B 里的注入 ⇒ 6 臂里 4 臂带着残留红。
   ⇒ 循环体必须是「注入 → 跑 → 还原」。

### 3.24 把尺子抬高一层：全量「路由 × 端点层请求次数」扫描

前 23 节反复撞到同一个形状——**服务层测得很扎实，端点本身一次都没被请求过**
（权限门控 §3.14、落盘 §3.15、证据写端点 §3.18、CSRF 整机 §3.21）。
逐个函数去猜只能碰运气，所以改成可枚举的尺子：枚举全部路由，逐条数它在
`tests/` 下被请求过几次。

**枚举源的坑（先踩再记）**：starlette **1.3.1** 把 `include_router` 包成惰性
`_IncludedRouter`，`app.routes` 里 v1 的路由**根本不出现**（实测只有 10 条：
5 个 `APIRoute` + 4 个 `Route` + 1 个 `_IncludedRouter`）。
唯一可靠的枚举源是 `app.openapi()`——它会强制展开，得到 **79 条**。

**匹配方法的坑（更要紧）**：tests 里大量路径是 f-string 拼的，例如
`f"/api/v1/evidence/cases/{cid}/missing"`，源码里的字面量只有
`"/api/v1/evidence/cases/"` 和 `"/missing"` 两段。拿整条路由去匹配必然落空
⇒ **把已覆盖误报成零覆盖**。朴素版实测报 **67/79**，虚高。

改成**按静态片段双向匹配取并集**后：

| 版本 | 零覆盖数 | 说明 |
|---|---|---|
| 朴素子串 | 67 | f-string 路径全部漏判 |
| 静态片段双向 | 56 | 修掉漏判 |
| 补完 files 端点判据后 | **55** | 本轮实做 |

**阴性结果必须反向核验**（否则「扫出 0」可能只是脚本坏了）：
`auth/me`、`reviews/ensure`、`notifications/*` 在整个 `tests/` 下**一次都没出现**，
而 `test_notification_api.py`（26 KB）里 `client.get()` 的命中数是 **0**——
它测的是服务层。所以「零覆盖」是真的，不是漏判。

**分档**（按「服务层有没有被提到」粗分，只用于定优先级，不作为结论）：

| 档 | 条数 | 含义 |
|---|---:|---|
| A：服务层有测试、端点层零请求 | 48 | **零件测过、装配没测**（`methodology.md` 89 的同一形状，系统性地复现了 48 次） |
| B：服务层同样零引用 | 7 | 整套没有任何测试 |

扫描器已固化为 `evidence/scan_route_coverage.py`，带**棘轮基线 55**
（零覆盖路由数只能降不能涨），并登记进 `run_ci_probes.py` 的 `GATED`。
环境缺失时退 `2`（由 runner 判为「跳过」），避免把环境噪声报成产品缺陷。

### 3.25 扫描带出的最高风险一条：卷宗下载端点（三个真实缺陷）

`GET /api/v1/files/{tenant_id}/{subdir}/{filename}` 是全站**唯一**把磁盘文件直接
吐给客户端的接口，也是早期 `StaticFiles` 挂载（完全绕过鉴权）整改的产物——
整改有没有真的守住，此前**零判据**。新增 `tests/test_file_download_endpoint.py`
（D1–D8，20 条），先红后修，坐实三个缺陷：

| # | 缺陷 | 证据 | 修复 |
|---|---|---|---|
| ① | 用 `user.tenant_id` 而不是 `ctx.tenant_id` 鉴权 | 平台管理员 `X-Tenant-Id` 切到租户 A ⇒ **404**（D4）；而同一份材料在列表接口（用 ctx）**看得见** ⇒ 同一功能被两套身份切成两半 | 改用 `ctx: TenantContext = Depends(get_tenant_context)` |
| ② | 只校验「在存储根内」，不校验「在租户目录内」 | `/files/{自己的租户}/%2e%2e/secret-at-root.pdf` ⇒ **200 + 文件内容原样返回**（探针实测） | `_resolve_abs_path` 增加 `tenant_root` 约束 |
| ③ | 审计留痕抛错 ⇒ 下载 **500** | 与本函数 docstring「不能因审计存储故障导致业务不可用」不符（`log_detached` 只吞**写库**失败，`log_detached_ctx` 取上下文时抛的没人兜） | 端点层 `try/except` + warning 日志 |

**① 为什么不是「见仁见智」**：`app/api/v1/*.py` 共 **14 个模块**引用
`ctx.tenant_id`，`files.py` 是**唯一一个引用数为 0** 的模块；
`deps.py::get_tenant_context` 明文写着「平台管理员可通过 `X-Tenant-Id` 切换到目标
租户进行运营管理」；本模块 docstring 写的也是「当前**请求所属**租户 ID」。
三处一致的约定，不是本轮自己拍的期望值。

**② 的一个陷阱（会伪装成「被拦住了」）**：URL 里写**字面量 `..`** 时，
httpx 会把点段规范化掉，请求根本进不到端点，返回 FastAPI 的
`{"detail":"Not Found"}`——判据会**假红**。必须写 `%2e%2e`，并且同时断言
`error.code` 非空（路由未命中的响应没有 `error.code`）。
⇒ `methodology.md` 92。

**③ 的边界**：`log_detached` 内部确实有重试 + 吞异常（`audit.py:158-167`），
所以「写库失败」本来就不阻断；缺口在 `log_detached_ctx` 的上下文补全段。
钉住它是为了防止将来有人在端点里加一句 `raise` 把它改坏。

**判据有效性**（7 臂注入，**每臂注入→跑→还原**）：

| 臂 | 注入 | 结果 |
|---|---|---|
| `ctxcheck` | 判等改回 `user.tenant_id` | ✅ D4 红 |
| `tenantdir` | 去掉 `tenant_root=` 约束 | ✅ D5 红 |
| `audguard` | `except` 改成 `raise` | ✅ D7b 红 |
| `extweld` | 白名单一律拒绝（**焊死**） | ✅ D1 红（反向量生效） |
| `rootguard` | 去掉存储根校验 | ✅ D6 红 |
| `tenant403` | 租户不符改回 403（泄露存在性） | ✅ D2 红 |
| `noaudit` | 跳过留痕 | ✅ D7 红 |

七臂**无一全绿**，还原后 `grep INJECTED` 干净。

### 3.26 第二批（Q-T ①）：`notifications/*` 6 条——**这次没有缺陷，但有一个结构发现**

按 Q-T 建议的次序清第二批。通知是**用户级**资源，服务层有 4 个测试文件约 100 KB，
但 6 条路由在 `tests/` 下从未被请求过——与 §3.18 / §3.25 完全同形状。

**这一轮刻意反过来做**：不预设「这里有缺陷」，先写判据跑一遍看结果。
新增 `tests/test_notification_endpoint_layer.py`（N1–N9，12 条）：

| 编号 | 判据 |
|---|---|
| N1 | 6 条路由**首次真实 HTTP 请求**：全员 200/404 而非 500（装配层冒烟） |
| N2/N3 | 列表只返回自己的 / 跨租户看不到 |
| N4 | 未读数只算自己的（2 条，不是 6 条） |
| N5 | 他人通知 ⇒ **404 `NOTIFICATION_NOT_FOUND`**，不是 403（不泄露存在性） |
| N6 | `read-one` **幂等**：`read_at` 不刷新、`unread_total` 不漂移 |
| N7 | `read-all`：自己清干净，**同租户律师乙一条都没动**（反向量） |
| N7c | 批量混入他人 id ⇒ 静默跳过且不改对方（`updated == 1`） |
| N8 | 未知 `type` ⇒ **400**（不做「静默返回空列表」） |
| N9 | AST：端点不接受 `user_id` / `tenant_id` 请求参数，一律取自 ctx |

**结果：12 条全绿，本轮没有查出缺陷。** 这也是一条信息——
「端点层真的通」从推断变成了实测（此前这 6 个端点**一次都没被执行过**，
任何 500 都不会有人发现）。

**但绿灯必须先被证伪**（`methodology.md`：绿 ≠ 有判据）。7 臂注入：

| 臂 | 注入 | 结果 |
|---|---|---|
| `ownednouser` | `_owned` 丢掉 `user_id` | ✅ **7 条红**（列表/未读/详情/已读全串） |
| `batchnouser` | `mark_read_batch` 丢掉归属条件 | ✅ 3 条红 |
| `detailcode` | 详情 404 的 code 改成 `RESOURCE_NOT_FOUND` | ✅ 2 条红 |
| `notidem` | `read_at` 每次都刷新 | ✅ N6 红 |
| `silenttype` | 未知类型静默返回 None | ✅ N8 红 |
| `acceptuser` | 给列表端点加 `user_id` 查询参数 | ✅ N9 红（AST 生效） |
| `ownednotenant` | `_owned` 丢掉 `tenant_id` | 🟢 **12 条全绿** |

**最后那一臂的绿，是本轮真正的发现**：

- 丢掉 `user_id` ⇒ 7 条红；丢掉 `tenant_id` ⇒ **一条都没红**。
- 原因：通知按 `(tenant_id, user_id)` 双重过滤，而 `user_id` 是**全局唯一**的
  自增主键；只要 `user_id` 还在过滤，去掉 `tenant_id` **就不可能**产生跨租户读取。
- ⇒ 这两个守卫在代码里是**对称的一行**（`_owned` 里一个 `&` 连接），
  但**权重完全不同**：`user_id` 是唯一关口，`tenant_id` 是纵深防御。
  把它们当成同权重的两个人，会得出「两个都重要、都要测」的错误结论——
  实际上**丢了前者立刻越权，丢了后者今天看不出任何差别**。

⚠️ 这不意味着 `tenant_id` 可以删：一旦将来引入「同一 user_id 跨租户复用」
（如平台账号体系改造），这道冗余就是唯一剩下的防线。结论是**权重不同、都要留**，
而不是「一个没用」。

**判据自己踩的坑**：N7 的「一键已读」会清空客户甲的数据 ⇒ 排在后面的 N7c
拿到 `updated=0`，看起来像「批量接口不工作」。已加 autouse 复位夹具
（每个用例前把所有通知复位成未读）。⇒ 模块级 `seeded` 夹具省建库开销的代价，
就是必须显式处理用例间状态污染。

**棘轮**：零覆盖路由 **55 → 49**，`verify_route_coverage.py` 的基线同步下调。

**由此新开的决策 Q-S（🟠 P1，待拍板）**：下载端点只做**租户级**校验、
不做**案件归属**校验——同租户内客户甲只要知道（或猜到）文件名，就能下载客户乙的
卷宗。这与 §3.19 修掉的 `reparse` 缺口是**同一个形状**（守卫按参数名分类，
缺口也按参数名分布）。本轮**未改**，因为「下载是否要按案件归属授权」属于访问
模型决策，需要产品拍板后再动。

---

### 3.27 第三批（Q-T ②/③）：`conversations/*` 3 条 + `reviews/*` 7 条

按 Q-T 建议的次序清第三批，一次清两个模块（共 10 条路由）。

#### 3.27.1 会话：17 条判据 + 7 臂注入（**一个判据漏洞、一个归属发现**）

新增 `tests/test_conversation_endpoint_layer.py`（C1–C11，17 条）：

| 编号 | 判据 |
|---|---|
| C1/C1b | 4 条路由**首次真实 HTTP 请求**：全员非 500 |
| C2 | 创建：请求体里的 `tenant_id` **被忽略**（伪造租户落不到 B） |
| C3a/b/c | 列表三向：客户只见自己 / 律师见本租户 / 跨租户为空 |
| C4 | 跨租户 ⇒ 404，与不存在的 id **完全同形** |
| **C4b** | 跨租户**用所内身份再判一次**（见下） |
| C5 | 同租户他人 ⇒ 404 |
| C6 | 往他人会话发消息 ⇒ 404，**且一条 Message 都没落库**（反向量） |
| C7 | 律师发言 `sender=LAWYER` 且 `reply == ""`（AI 不抢答） |
| C8/C9 | 非法 `channel` / `msg_type` ⇒ 422（schema 是唯一关口） |
| C10a/b/c | AST：不吃请求体 `tenant_id`；详情与发消息共用同一守卫；列表双过滤在位 |
| C11 | 客户可伪造 `client_user_id` 归属（**发现 Q-U**） |

**本轮最重要的自查：`tenantflooroff` 臂第一次跑出来是 16 全绿。**

顺着查下去发现是**我自己的判据有洞**，不是产品没问题：

- C4 用的是**客户**身份跨租户访问。而客户本来就命中 `can_access_conversation`
  的第 4 条「端用户到此为止」——**租户底线（第 2 条）对客户是冗余的**。
- ⇒ 把租户底线整段删掉，C4 依然全绿。**租户底线这条防线此前没有判据。**
- 补了 **C4b**：用**所内身份**（律师，命中 `STAFF_ROLES`、不命中端用户规则）
  再判一次跨租户。补完之后该臂才转红。

⇒ 一条防线若有**两条规则**同时挡在路径上，只用「两条都会命中」的身份去测，
删掉其中任何一条都测不出来。**必须找一个只命中其中一条的身份补测。**

**C11 是发现而不是缺陷**：`create_conversation` 写的是
`client_user_id=payload.client_user_id or ctx.user_id`，请求体里的
`client_user_id` **被采信** ⇒ 客户甲可以建一条归属为客户丁的会话。

- **无数据泄露**：伪造者自己读不到（判据 1/2 都不满足 + 端用户规则）⇒
  C11 明确断言「伪造者 GET 自己造的会话 ⇒ 404」；
- **有归属污染**：丁的收件箱里会多一条自己没发起过的会话；`bind_lawyer_id` 同理。

⇒ 登记为 **Q-U（🟡 P2）**：是否应限制只有所内人员才能指定 `client_user_id`。
本轮**不改产品代码**。

7 臂注入（**全部转红**）：

| 臂 | 注入 | 结果 |
|---|---|---|
| `listnorole` | 列表丢掉 `Role.CLIENT` 过滤 | ✅ 2 红 |
| `tenantflooroff` | 归属判据拆掉租户底线 | ✅ 1 红（**C4b**，补判据前是 0 红） |
| `tenantonly` | 归属判据退回「仅校验 tenant_id」 | ✅ 3 红（C5/C6/C11） |
| `createtenant` | 创建端点开始吃 `payload.tenant_id` | ✅ 2 红 |
| `senderhard` | 发言一律记成 CLIENT | ✅ 1 红 |
| `schemachannel` | `channel` 退化成 `str` | ✅ 1 红 |
| `strictowner` | 详情不传 `ctx.user_id` | ✅ 2 红（功能全丢方向） |

#### 3.27.2 复核：15 条判据 + 9 臂注入（**又一处非对称权重**）

新增 `tests/test_review_endpoint_layer.py`（R1–R12，15 条）：

| 编号 | 判据 |
|---|---|
| R1 | 7 条零覆盖路由**首次真实 HTTP 请求**：全员非 500 |
| R2 | 列表双向租户隔离 |
| R3 | `ensure` **幂等**且按租户隔离（同 target 跨租户不复用） |
| R4 | 非法 `target_type` ⇒ 4xx **且不落库** |
| R5/R5b | 跨租户 `decide` / `submit` ⇒ 404 且**对方状态未变** |
| R6 | 链路走端点：`ensure → submit → decide → archive` 逐状态断言 |
| R7 | 留痕按动作落库、`actor_id` 正确 |
| R8 | DRAFT 直接 archive ⇒ 4xx `REVIEW_TRANSITION_DENIED` |
| R9 | 跨租户读留痕 ⇒ 404 |
| R10a/b/c | AST：4 个写端点都过 `_review_or_404`（带命中数下限自检）；`ensure` 用 `ctx.tenant_id` |
| R11 | 未知结论 ⇒ 4xx（现状 code 语义错配，见 Q-V） |
| **R12** | 归档后 edit ⇒ 4xx（**只压白名单那一层**，见下） |

**第二次撞到「冗余层」**：`fsmarchiveopen` 臂（拆掉「未确认不可归档」前置条件）
跑出来 **15 全绿**。查 `review_fsm.assert_transition` 发现它有两层独立防线——
白名单 + `if nxt == ARCHIVED and cur != CONFIRMED` 前置条件。

- 白名单里能到 `ARCHIVED` 的**只有** `CONFIRMED`；能到 `CONFIRMED` 的**只有**
  `PENDING_CONFIRM`，而前置条件要求的正是这两个 ⇒ **前置条件与白名单完全冗余**，
  不存在「只有前置条件在挡」的流转。
- 反过来，`ARCHIVED → LAWYER_EDITING` 是**只有白名单在挡**的流转
  （前置条件只管 CONFIRMED/ARCHIVED 两种**目标**状态）⇒ 补 **R12** 钉住白名单。
- 再用 `fsmnoop`（`assert_transition` 整体变 no-op）验证 R8 **确有检测能力**
  ⇒ 2 红。**所以 R8 不是空转，只是它压的那一层不是唯一关口。**

⇒ 与 §3.26 的 `tenant_id`、§3.27.1 的租户底线是**同一个结构**：
代码里对称的两行，实际权重可能完全不对称。本轮已是第三次。

9 臂注入（**8 红 1 绿**）：

| 臂 | 注入 | 结果 |
|---|---|---|
| `ensurenotenant` | `ensure` 查重丢掉 `tenant_id` | ✅ 1 红（跨租户串号） |
| `guardgone` | `_review_or_404` 丢掉租户判等 | ✅ 2 红 |
| `recordsnotenant` | 留痕丢掉内联租户校验 | ✅ 2 红 |
| `listnotenant` | 列表丢掉租户过滤 | ✅ 1 红 |
| `ensureillegalok` | `ensure` 不再校验 `target_type` | ✅ 1 红 |
| `submitnoguard` | `submit` 绕过守卫 | ✅ 2 红（**含行为判据**，非仅 AST） |
| `fsmalwaysok` | 白名单全放行 | ✅ 1 红（R12） |
| `fsmnoop` | `assert_transition` 整体失效 | ✅ 2 红（R8 + R12） |
| `fsmarchiveopen` | 只拆归档前置条件 | 🟢 **15 全绿**（发现：与白名单完全冗余） |

#### 3.27.3 本轮工程侧事故：**注入器自己会造出假红**

三次踩到同一类问题，单列出来避免重犯：

1. **还原机制不可靠**：原先用「复制 `.bak` + `shutil.move` 回来」。实测 `.bak`
   会丢 ⇒ 产品代码**永久停在注入态**，后续每一臂量到的都是**假红**
   （`senderhard` 的残留甚至让 `enduseropen` 被误判为「有效」）。
   改为**内存里存原始字节 + `write_bytes` 还原**。
2. **SIGTERM 不执行 `finally`**：前台 bash 超时把注入器杀掉 ⇒ 同样留下残留。
   改为注册 `SIGTERM`/`SIGINT` 处理器先还原再退出，并**每臂前后扫描全仓残留**。
3. **注入臂无效 ≠ 判据有效**：必须区分「锚点没命中（注入器写坏了）」与
   「注入生效但判据测不到（判据有洞）」。前者要**报错**，后者要**补判据**。

**棘轮**：零覆盖路由 **49 → 39**（会话 3 + 复核 7），基线同步下调。

**由此新开的决策**：**Q-U**（🟡 P2，会话归属可伪造）、
**Q-V**（🟡 P2，`ensure` 非法参数返回 404「复核任务不存在」、
`decide` 未知结论的 code 是 `REVIEW_ALREADY_DECIDED`——两处**语义错配**，
会误导调用方与排障；本轮只固化「拒绝且不落库 / 状态不变」的不变量，
未断言具体状态码，以免把错误语义焊死、挡住后续修正）。

### 3.28 第四批（Q-T ④/⑤）：`dispatches/*` 4 条 + `analyses/*` 6 条

#### 3.28.1 派单：**查出 1 个 P0 级跨租户写（已修）**

新增 `tests/test_dispatch_endpoint_layer.py`（P1–P9，9 条）。**先红后修**，
判据一次就坐实了缺陷——且比预想严重。

**缺陷本体**：`accept_dispatch` / `grab_dispatch` 直接调
`DispatchService(db).accept(dispatch_id, ctx.user_id)`，而

- `DispatchService._get()` 是 `db.get(Dispatch, dispatch_id)`，**不带租户过滤**；
- `DispatchService` 整个服务**不接收 `tenant_id`**（全模块 `tenant_id` 只出现在
  `_candidates`，那是**建单时**选候选律师用的）。

⇒ 租户 A 的律师凭 `dispatch_id` 就能接下**租户 B** 的派单。

**后果不是「读到了别人的数据」，而是责任链被改写**。`accept()` 会同时：

1. `disp.lawyer_id = lawyer_id`、`disp.status = ACCEPTED`；
2. `case.lawyer_id = lawyer_id`、`case.status = ACCEPTED`；
3. 触发 `_on_assigned` / `_notify_accepted`——**用租户 A 律师的名字给租户 B 的客户发通知**。

**最隐蔽的是顺序**：端点在 `db.commit()` **之后**才调 `trigger_case_analysis`
（它内部有租户校验）⇒ **越权写已经落库，接口才返回 404**。
只盯状态码会以为「被挡住了」（`methodology.md` 76「先落盘后鉴权」的又一个实例）。
P6 的反向量把它钉死了：

```
('PENDING', None, 'DISPATCHED', None) → ('ACCEPTED', 1, 'ACCEPTED', 1)
```

**修复**（`app/api/v1/dispatches.py`）：按代码库既有范式
（`analyses._load_or_404` / `reviews._review_or_404` / `cases._case_or_404`）
新增 `_dispatch_or_404(db, dispatch_id, tenant_id)`，`accept` / `grab` **两个端点**都过它。
修完 3 红转绿。

| 编号 | 判据 |
|---|---|
| P1 | 4 条路由**首次真实 HTTP 请求**：全员非 500 |
| P2/P3 | 列表 / 抢单池的租户隔离（双向；池只含本租户 PENDING 且未指定/POOL） |
| **P4/P5** | 跨租户 `accept` / `grab` ⇒ **404 `DISPATCH_NOT_FOUND`**（不是 403：不泄露存在性） |
| **P6** | 反向量：跨租户接单**不得改动**对方的 dispatch 与 case |
| P7 | 同租户接单正常工作（防把功能焊死） |
| P8 | 指定派单 ⇒ 非指定律师 400 `DISPATCH_RULE_CONFLICT` |
| P9 | AST：两个接单端点都把 `ctx.tenant_id` 交给守卫（带命中数下限自检） |

7 臂注入**全部转红**：`guardgone` 3 红 / `noguardaccept` 2 红 / `noguardgrab` 2 红
（**只修 accept 漏掉 grab 会被抓到**）/ `listnotenant` 1 红 / `poolnotenant` 1 红 /
`designatedoff` 1 红 / `caseownerlost` 1 红。

#### 3.28.2 分析：**无缺陷**（守卫在位，但有一处「守卫在别的文件」）

新增 `tests/test_analysis_endpoint_layer.py`（A1–A10，11 条）⇒ **全绿**。

值得记的一条：`POST /analyses/case/{id}/generate` 的租户判等**不在 `analyses.py`**——
端点只把 `ctx.tenant_id` 透传给 `trigger_case_analysis`，真正的检查在
`job_handlers.py:141`。与 `methodology.md` 93 同形：**同一边界的第二份实现在别的文件**。
⇒ 判据必须钉**行为**，只扫 `analyses.py` 会得出「这个端点没有守卫」的错误结论。

⚠️ **刻意不做同租户 `iterate` 的正向判据**：`CaseCopilot.generate` 走 LLM，
后端未配 LLM（`MEMORY.md` 硬约束 5）⇒ 正向结果依赖降级路径、不稳定。
跨租户那条守卫在 Copilot **之前**，不依赖 LLM，故保留。

7 臂注入**全部转红**：`loadguardgone` 4 红 / `casetenantoff` 2 红 /
`generateopen` 1 红（**打的是 `job_handlers.py`**）/ `editnoguard` 2 红 /
`versionsnoguard` 2 红 / `decisionsnoguard` 2 红 / `changedbylost` 1 红。

#### 3.28.3 注入器新增一条硬约束：**锚点必须在文件里唯一**

`caseownerlost` 臂第一次跑出来 **exit=0（全绿）**。查下去是**注入器自己的问题**：
锚点 `case.lawyer_id = lawyer_id` 在 `dispatch_service.py` 里有**两处**——
第 84 行是**建单**路径、第 129 行才是**接单**路径，`replace(..., 1)` 打到了
建单路径，而本判据文件根本不走那条路 ⇒ **空转的臂**：

- 它既不红、也不报错，最容易被误记成「判据没测到」甚至「产品没问题」；
- 判别法：给锚点**加上下文**（带上前后各一行），并在注入器里断言 `src.count(old) == 1`，
  `> 1` 直接报错退出（已加进 `_inject_*.py`）。

⇒ 补充 `methodology.md` 96：注入臂无效的**三种**处置——
锚点没命中（报错）/ 锚点不唯一（报错）/ 注入生效但判据测不到（补判据）。

**棘轮**：零覆盖路由 **39 → 29**（派单 4 + 分析 6）。

### 3.29 第五批（Q-T ⑥/⑦）：`documents/*` 5 条 + `compliance/*` 2 条

#### 3.29.1 文书：**又查出 2 个 P0（已修）**，且是**两层都零覆盖**的模块

`grep -rl "documents\|compliance" tests/` 是**空的** —— 文书与合规是
**服务层 + 端点层双双零覆盖**的模块（比派单更彻底：派单至少还有
`test_dispatch_rules.py`）。这两个模块此前**一次都没被执行过**。

新增 `tests/test_document_endpoint_layer.py`（W1–W8，9 条），**先红后修**，
一次坐实两个缺陷：

**缺陷一：`POST /documents/{doc_id}/collect` 跨租户可写**
`DocumentService._get()` 只按主键取、服务层**不收 `tenant_id`** ⇒
租户 A 能把变量**写进**租户 B 的文书。实测响应 200：

```json
{"id":2,"title":"B 的文书","variables":{"party_a":"我塞进去的"}, ...}
```

**缺陷二：`POST /documents/{doc_id}/render` 跨租户可读 + 记错账**
同一个缺口，但后果更重：`DocumentOut.content` 是**渲染后的完整正文**，
而 `render_document` 的 `BillingService.consume` 用的是 `ctx.tenant_id` ⇒
**读别人的东西、记自己租户的账**。实测响应 200 且带回了正文，
W5b 的反向量把记账也钉死了（`UsageRecord` 计数 2 → 3）。

**修复**（`app/api/v1/documents.py`）：新增 `_document_or_404(db, doc_id, tenant_id)`，
`collect` / `render` 都过；顺带把 `get_document` 里**内联的重复判据**也收敛到同一处
（此前是三份判据各写一份、其中两份缺失—— 与 `conversation_access.py` 收敛前同形）。

| 编号 | 判据 |
|---|---|
| W1 | 6 条路由**首次真实 HTTP 请求**：全员非 500 |
| W2 | 模板库 = 本租户 + platform，**不含其他租户** |
| W3 | `start` 落在本租户 ⇒ 租户 B 读不到（间接断言：直接比字段证明不了「别人读不到」） |
| **W4** | 跨租户 `collect` ⇒ 404 且**不改对方 variables** |
| **W5/W5b** | 跨租户 `render` ⇒ 404、**正文不外泄**、**不记调用方租户的账** |
| W6 | 同租户 `collect` → `render` 正常出正文（防焊死） |
| W7 | `GET /{doc_id}` 跨租户 ⇒ 404（这条本来就有，守住不退化） |
| W8 | AST：两个端点都把 `ctx.tenant_id` 交给守卫（带命中数下限自检） |

6 臂注入**全部转红**：`docguardgone` 5 红 / `collectnoguard` 2 红 /
`rendernoguard` 2 红 / `tplnotenant` 1 红 / `starttenantswap` 2 红 / `renderlost` 1 红。

#### 3.29.2 合规：**无缺陷**

新增 `tests/test_compliance_endpoint_layer.py`（K1–K6，8 条）⇒ **全绿**。
4 臂注入**全部转红**：`cmplistnotenant` 2 红 / `cmpscannotenant` 3 红 /
`cmpcreatetenantswap` 1 红 / `cmptitleoptional` 1 红。

#### 3.29.3 本轮最有价值的信号：**「服务层不收 `tenant_id`」会按模块复现**

派单（§3.28.1）与文书（§3.29.1）是**两个独立模块、同一个缺口形状**：

| | 派单 | 文书 |
|---|---|---|
| 服务层 `_get()` | `db.get(Dispatch, id)` 无租户过滤 | `db.get(Document, id)` 无租户过滤 |
| 服务层收 `tenant_id` 吗 | ❌ | ❌ |
| 端点层校验 | ❌ | 仅详情有（另两条没有） |
| 后果 | 改写他人**责任链** | 改写他人**文书** + **读正文** + **记错账** |

⇒ 这不再是「某一处漏了」，而是**一个可复现的模式**。
下一批清理应当**先按这个模式做一次横扫**（搜「服务层方法签名里没有 `tenant_id`
但端点传了 `id`」），而不是继续按路由逐条试。
已登记为待办动作（见 §6.2）。

**棘轮**：零覆盖路由 **29 → 22**，且 **B 类（服务层也零引用）归零** ——
剩余的 22 条全是「零件测过、装配没测」。

### 3.30 第六批（Q-T ⑧/⑨）：`jobs/*` 2 条 + `archives/*` 4 条

> 这一批是**按模式挑的**，不是按路由挑的：§3.29.3 建的模式横扫探针
> `verify_service_tenant_param.py` 报出 20 条「服务层不收 `tenant_id`」候选，
> 其中 `JobService.get` 与 `ArchiveService.versions` 都在列，
> 而它们对应的路由恰好也在剩余 22 条里 ⇒ 优先清这两组。

#### 3.30.1 异步任务：**无产品缺陷**（端点层守卫齐全）

新增 `tests/test_job_endpoint_layer.py`（J1–J10，11 条）⇒ **全绿**。

`JobService.get()` 与 §3.28 的 `DispatchService._get()` **同一个形状**
（`select(Job).where(Job.id == job_id)`，不带租户过滤），
但 `jobs.py` 的两个端点**都**写了 `job.tenant_id != ctx.tenant_id` ⇒ 拦得住。

| 编号 | 判据 |
|---|---|
| J1 | 2 条路由**首次真实 HTTP 请求**：全员非 500 |
| J2 | 同租户 `GET` 正常返回，字段与库一致 |
| J3 / J3b | 跨租户 `GET` 双向 ⇒ 404 `JOB_NOT_FOUND`（`error.code` 非空 ⇒ 端点发的） |
| J4 | 不存在的 id ⇒ 与跨租户**同码**（不许用 HTTP 语义泄露「这 id 属于别家」） |
| J5 | 非整数 id ⇒ 422（**判据自己的护栏**：证明请求真的进了端点） |
| J6 | `retry` 非 FAILED ⇒ 400 `JOB_NOT_RETRYABLE` + **库里状态不变** |
| **J7** | **跨租户 `retry` ⇒ 404，且对方 job 的 status/error/retry_count 一个字都不动** |
| J8 | 同租户 `retry` ⇒ 状态回 pending、错误清空、预算归零、**且真的重新入队** |
| J9 | 未注册 handler 的类型 ⇒ 400，**且状态不许先改再失败** |
| J10 | AST：两条端点都过归属守卫（带命中下限自检） |

**关于 J8 的一处工程取舍**：`retry` 的最后一跳 `await job_queue.enqueue(...)`
在 lifespan 里已经 `start()` 过，真跑起来 worker 会去**调 LLM** ⇒ 测试不确定。
所以 J8 用记录器替掉 `app.api.v1.jobs.job_queue`：既不让真活跑起来，
又钉住「确实重新投递了」这个行为（不替换的话这条判据根本测不到）。

5 臂注入**全部转红**：`jobnofen` 3 红 / `retryopen` 2 红 / `retryanystat` 1 红 /
`noenqueue` 1 红 / `unregorder` 1 红。

**顺带钉住的语义**（非缺陷，登记备查）：`retry` 会把 `retry_count` **归零**，
即「手动重试 = 换一批新预算」，`JOB_MAX_RETRIES` 只约束**自动重试**。
这是有意设计还是漏算，见 §6.2 **Q-W**。

#### 3.30.2 归档与材料包：**无产品缺陷**（但两处防线只有一个文件里有）

新增 `tests/test_archive_endpoint_layer.py`（V1–V10，11 条）⇒ **全绿**。

| 编号 | 判据 |
|---|---|
| V1 | 5 条路由**首次真实 HTTP 请求**：全员非 500 |
| **V2** | **跨租户归档 ⇒ 404 + 对方案件状态不变 + 没多出 Archive**（双反向量） |
| V3 | 未定稿不可归档 ⇒ 400 `ARCHIVE_NOT_CONFIRMED` + 状态不变 |
| V4 | 同租户归档 ⇒ 200 且案件进 `ARCHIVED`（**正向判据 = 反向判据的防伪标记**） |
| V5 | `GET /cases/{case_id}` 跨租户 ⇒ 404，卷宗内容不外泄 |
| **V6** | **`/versions` 跨租户 ⇒ 404**（`ArchiveService.versions` **不收** `tenant_id`，端点是唯一防线） |
| **V7** | **跨租户导出材料包 ⇒ 404，且没多出材料包、对方案件状态不变** |
| V8 | `GET /hearing-packs/{pack_id}` 跨租户 ⇒ 404 |
| V9 | 归档与导出**各留一条审计**（压 `record(...)` 那两行，与业务结果无关） |
| V10 | AST：5 条端点都引用 `ctx.tenant_id` |

6 臂注入**全部转红**：`arcgetopen` 2 红 / `arcveropen` 2 红 / `arcpackopen` 3 红 /
`arctenantb` 5 红 / `arcnoaudit` 1 红 / `arcnoconfirm` 2 红。

⚠️ **`arcnoconfirm` 是这一批最值钱的一条臂**：它改的是
`app/services/archive_service.py`，而判据 V3 读的是 **HTTP 行为**。
⇒ 证明 V3 **不是**「扫某个文件里有没有那行」，而是真的压住了行为
（`methodology.md` 93「守卫在别的文件」的又一条实证）。

#### 3.30.3 ☠️ 本轮工程事故：**三条臂曾经「空转」，而且都伪装成「通过」**

| # | 现象 | 真因 | 处置 |
|---|---|---|---|
| 1 | `retryanystat` / `unregorder` 报「锚点没找到」 | **仓库是 CRLF**（实测 `jobs.py` 54/54 全 CRLF），多行锚点写 `\n` ⇒ `count()` 恒 0 | 匹配前归一成 LF、写回还原 CRLF（`_load()` / `_dump()`） |
| 2 | `arctenantb` 注入后 **SyntaxError** | 把 `# INJECTED` 注释放到了**未闭合的括号里** | 注释挪到闭合括号之后；并新增**注入后 `ast.parse` 自检**——语法坏了就报「这个红是假的」，不跑测试 |
| 3 | `arcnoconfirm` 报「找不到函数」 | `_region` 写死 `^(async )?def`，匹配不到**类里的缩进方法** | 改成按**缩进层级**切区间 |

⇒ 共同点：**三条都发生在注入器里，都不会让测试变红，只会让测试「没变」**。
如果注入器不做「锚点必须唯一 + 注入后必须能 import + 还原后 grep 无残骸」
这三条自检，这三条臂会被记成「判据通过」——比假红更危险。
已写入 `methodology.md` **98/99**。

#### 3.30.4 这一批的反向结论：**「只防新增」的棘轮设计是对的**

`JobService.get` 与 `ArchiveService.versions` **都在**模式横扫的 20 条候选里，
但清完发现**两端点都已在端点层收口** ⇒ 无缺陷。

⇒ 如果当初把 `verify_service_tenant_param.py` 写成「存量判红」，
第一天就会有 **20 条假红**淹没有效信号（§3.30 实测：20 条里至少 2 条是无辜的）。
**「只报不判 + 只防新增」**这个折中，在它第一次被真实数据时验证成立。

**棘轮**：零覆盖路由 **22 → 16**（`jobs/*` 2 + `archives/*` 4；两批均无产品缺陷）。

### 3.31 第七批（Q-T ⑩）：`audit/retention/*` 5 条 —— **查出一个合规级缺陷（已修）**

> 模式横扫**没有**点名这一批（20 条候选全在已清模块里）⇒ 换排序依据：
> 别的模块最坏是「读/写别人的东西」，这一批的 `purge` 是**不可逆删除**，
> 删的还是**审计日志本身**。等保 2.0 三级要求留存 ≥ 6 个月，
> 而删除接口是唯一能违反它的入口。

#### 3.31.1 🚨 缺陷：`purge` 的 `days` 参数**绕过了等保下限**

`retention_days()` 对**配置值**做了下限保护（配 30 天会抬到 180 天，
还有日志告警），而 `cutoff_at(days)` 是：

```python
return _utcnow() - timedelta(days=days if days is not None else retention_days())
```

⇒ **请求里传 1 天，就按 1 天删**。`PurgeRequest.days` / `ArchiveRequest.days`
都是裸 `Optional[int]`，没有任何约束。

实测（注入前）：

```
POST /api/v1/audit/retention/purge  {"days": 1, "confirm": true}
⇒ 200，deleted=5   ← 等保要求留存 180 天的审计日志，被一条请求清掉
```

`days=0` 更极端：cutoff 就是「此刻」⇒ **等价清空整张审计表**。

**为什么此前没人发现**：`tests/test_p1_concurrency_retention.py` 有 8 条服务层测试，
但**全部用合规值 `days=180` 调用**。它们证明了「配置错了会被抬到 180 天」，
**没人问过「请求参数错了会怎样」** —— 又一次「已修复 ≠ 有判据」：
下限保护是**真的存在**，但只存在于配置那一侧。

**修复**（`app/api/v1/audit_retention.py`）：两个请求 schema 的 `days` 加上
`ge=SECURITY_BASELINE_DAYS` ⇒ 低于下限的请求在**边界层**被 422 拒掉。
（服务层保持宽松：dry-run 预览与内部调用仍可用小值，安全边界放在 HTTP 入口。）

#### 3.31.2 判据（L1–L10，13 条）

| 编号 | 判据 |
|---|---|
| L1 | 5 条路由**首次真实 HTTP 请求**：全员非 500 |
| L2 | 非平台管理员 ⇒ 403，4 条管理端点**逐条**验（门控是按路由挂的，漏一条少一条） |
| L3 | `/policy` 公开可读，且 `baseline_days == 180` |
| **L4** | **🚨 `purge` 传 `days=1` ⇒ 被拒 + 一行都不删**（等保下限） |
| **L5 / L5b** | **`days=0` / 负数 / `archive` 的 `days=1` 同样被拒** |
| L6 | 默认 dry-run ⇒ 只统计不删除（反向量） |
| L7 | `confirm=true` + 合规 days ⇒ 真的删除，且**墓碑审计被调用** |
| L8 / L8b | `archive-then-purge`：dry-run 不删；确认后归档校验通过才清理 |
| **L8c** | **行数不一致 ⇒ 拒绝清理**（这条是被注入臂逼出来的，见 §3.31.3） |
| L9 | `AUDIT_RETENTION_ADMIN_ENABLED=false` ⇒ 4 条管理端点**全部**被拒 |
| L10 | AST：4 条管理端点都同时有角色门控与急停开关 |

6 臂注入**全部转红**：`retnobaseline` 3 红 / `retnorole` 2 红 / `retnoswitch` 2 红 /
`retconfirm` 3 红 / `retnotomb` 1 红 / `retarcnopurge` 1 红。

#### 3.31.3 ☠️ `retarcnopurge` 首版**全绿** —— 又一个「防线与数据冗余」

把 `if arc["rows"] != pending:` 改成 `if False:` 之后，L8b **依然全绿**：
数据一致时，有没有这道校验**结果完全一样**。
⇒ 补 **L8c**：替掉 `AuditRetentionService.archive` 让它报一个**假的行数**
（等价「归档产物被截断」），其余走真实代码路径 ⇒ 校验才真正被压到。

这是 `methodology.md` 95 的**第四次**出现：
**代码里对称的两行守卫，权重可能完全不同；必须为每条防线找「只命中它」的探针。**

#### 3.31.4 两个顺带发现（未改，登记）

- **`/policy` 的 `days` 查询参数是死的**：签名里写了
  `days: int = Query(0, description="传入自定义值可预览生效结果")`，
  但函数体里调的是 `retention_days()`（**不接参数**）⇒ 传什么都返回配置值。
  文档承诺了一个不存在的功能。已登记 **Q-X**。
- **墓碑审计的「落库」这一跳没有端点层判据**：`purge` 用 `log_detached_ctx`
  走**独立连接**写墓碑，绑的是 `settings.DATABASE_URL`（不是测试库）
  ⇒ L7 只能钉到「被调用」，钉不到「真的写进去了」。已登记 **Q-Y**。

**棘轮**：零覆盖路由 **16 → 11**。

## 4. 本轮实做清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `backend/tests/test_rate_limit.py` | 新增 18 条 | 档位映射 / 429 码 / `Retry-After` / GET 不限 / 上传档命中 ID 路径 |
| `backend/tests/test_review_tenant_guard.py` | 新增 5 条 | G1–G5，含 HTTP 攻击者路径与 AST 结构判据 |
| `backend/tests/test_llm_production_guard.py` | 新增 5 条 | 三档参数化 + 干净对照 + 不误伤 |
| `backend/tests/test_cors_credentials_guard.py` | 新增 4 条 | 生产守卫 + 白名单行为 + 第三方钉子 |
| `backend/tests/test_audit_coverage.py` | 新增 4 条 | P0-8 棘轮：不许退 + 不许涨 + 复核链路按性质 |
| `backend/tests/test_moderation_enforcement.py` | 新增 7 条 | P0-13 拦截**执行链**：服务层判定 + 留痕 + HTTP + 指标 |
| `backend/app/middleware.py` | **改产品代码** | 上传档 `endswith` → `startswith`（真缺陷） |
| `backend/app/main.py` | **改产品代码** | 新增 `_assert_cors_origins_safe()` 生产守卫 |
| `backend/tests/test_retrieval_wiring.py` | 新增 5 条 | P0-2 接线 + 租户：写侧 AST / 读侧棘轮 / SQL 租户过滤 / 内存后端盲区 / tenant 透传 |
| `backend/tests/test_job_claim.py` | **+3 条** | #4 回收预算：耗尽不复活 / 消耗预算 / 巡检必须挂载（AST） |
| `backend/app/services/job_service.py` | **改产品代码** | 回收消耗 `retry_count`、耗尽判 FAILED；`run_job` 改累加（真缺陷） |
| `backend/tests/test_auth_cookie_transport.py` | 新增 6 条 | P0-7 后端面：真实响应头 HttpOnly / body 不回传 refresh / csrf 非 HttpOnly / 生产守卫 / 有效期（行为级 + `.env`） |
| `evidence/verify_token_storage.py` | 新增（含自检 6 臂） | P0-7 前端面：SDK 不得持久化令牌 / 每处 fetch 带 credentials / 401 自动刷新且 logout `_noRetry` / 四端不得绕过 |
| `evidence/run_ci_probes.py` | **新增** | Q-I 落地：CI 运行器（分档 / 退出码映射 / 自检 4 臂 / 判据 0 清单一致性） |
| `evidence/verify_component_wiring.py` | 改判据 | 棘轮 `KNOWN_GAPS`：已知缺口不阻断、**不许涨**、**不许赖**；自检 +4 臂（Q9–Q12，共 12） |
| `evidence/verify_conversation_channel_500.py` | 改判据 | 事实1 期望**翻转**（schema 层已拒绝 = 修复生效）+ 结论重写；原口径会把"修好了"报成"没修好" |
| `evidence/verify_token_storage.py` | 改判据 | F4 窗口在**下一处 `fetch(`** 截断（原 500 字符会跨调用掩盖缺凭据）；自检改纯合成夹具 |
| `.github/workflows/ci.yml` | **新增 `evidence` job** | 自检 → 打印分档 → 跑门禁；**6 个探针**、79s、零新增依赖 |
| `evidence/verify_register_abuse_defense.py` | 新增（含自检 3 臂） | Q-A 派生：公开注册的 5 条滥用防线事实 + `KNOWN_GAPS` 棘轮（§3.12） |
| `backend/app/config.py` | **改产品代码** | 新增 `TRUSTED_PROXIES`（默认 `127.0.0.1,::1`）+ `trusted_proxies_list` |
| `backend/app/middleware.py` | **改产品代码** | `_client_ip` 加信任判断 + 右起第一个不可信跳；新增 `_is_trusted`（CIDR / 解析失败即不可信） |
| `backend/tests/test_rate_limit.py` | **+6 条**（R7–R12） | XFF 信任：不可信对端 / 右起跳 / 全可信回退 / CIDR 与非法条目 / 端到端换头不逃桶 / 反向对照 |
| `backend/tests/test_auth_register.py` | **+1 条**（T8） | **整机**复验：走 `create_app()` 完整中间件栈，换伪造 XFF 仍须被同一个桶拦住 |
| `backend/app/main.py` | **改产品代码** | 新增 `_assert_forwarded_allow_ips_safe()`：生产禁止 `FORWARDED_ALLOW_IPS=*`（否则 uvicorn 先改写 `client`，应用层修复被架空） |
| `backend/tests/test_forwarded_allow_ips_guard.py` | **新增 5 条**（F1–F5） | 生产拒绝 `*` / 非生产允许（对照）/ 精确名单不误伤 / 未设不干预 / **反向对照**：生产能正常启动 |
| `evidence/verify_register_abuse_defense.py` | +1 条事实 | `UVICORN_NOT_ALWAYS_TRUST`：静态扫部署文件；扫描不到文件 ⇒ **按缺陷处理**（空集必须显式判） |
| `deliverables/product-strategy/decisions-2026-09-20.md` | **新增** | 决策台账：Q-A/B/C/I 已裁定 + Q-K/L/M/N 待定 + 需用户提供的外部资源 |
| `backend/tests/test_tenant_context_guard.py` | **新增 9 条**（X1–X5） | §3.13：`X-Tenant-Id` 租户切换边界；X4 抓出**纯空白头部**静默缺陷 |
| `backend/app/core/deps.py` | **改产品代码** | §3.13：头部 `strip()` 后判空（`"   "` 是 truthy ⇒ 租户被切成三个空格） |
| `backend/tests/test_require_permissions_guard.py` | **新增 21 条**（P1–P9/R1–R3/W1） | §3.14：路由级权限门控；P6/P7 是 HTTP 层成对判据 |
| `backend/app/core/deps.py` | **改产品代码** | §3.14：`require_permissions` / `require_roles` 加**工厂阶段**参数校验（空清单会公开端点、`mode` 拼错会静默降级） |
| `backend/tests/test_storage_path_containment.py` | **新增 26 条**（S1–S6/A1–A3） | §3.15：唯一落盘路径的目录穿越防线 + `assert_tenant` 语义 |
| `backend/app/services/storage_service.py` | **改产品代码** | §3.15：新增 `_resolve_under_root()`，落盘前验证仍在存储根内（**真缺陷**：`tenant_id` 未净化即可穿越） |
| `backend/tests/test_moderation_report.py` | **新增 9 条**（R1–R8） | §3.16：合规上报（第十四条）链路判据 |
| `backend/app/services/moderation_service.py` | **改产品代码** | §3.16：上报失败落 `FAILED` 而非 `PENDING`（**语义错配**）；`report_note` 拆成两种失败各一句话 |
| `backend/tests/test_billing_endpoint_scope.py` | **新增 6 条**（B1–B6） | §3.17 / Q-O 收口：计费端点**数据边界**（AST 结构 + HTTP 跨租户 + 反向） |
| `backend/tests/test_evidence_upload_scope.py` | **新增 13 条**（U1–U8 / V1–V5） | §3.18 / §3.19：证据**写**端点（AST 结构 + 顺序 + HTTP 数据边界 + 不留文件 + 反向） |
| `backend/app/api/v1/evidence.py` | **改产品代码** | §3.19：新增 `_evidence_or_404()`（`reparse` 缺客户归属校验，**真缺陷**）；`EvidenceService.parse` 补传 `tenant_id` |
| `backend/tests/test_moderation_report.py` | 修 1 条 | 删掉签名里**没用到**的 `caplog`（`-p no:logging` 下会 error，平时是死重量） |
| `backend/tests/test_csrf_middleware_dispatch.py` | **新增 7 条**（C1–C7） | §3.21：CSRF **中间件整机**（此前只有 10 条令牌原语测试，整机 0 条） |
| `backend/tests/test_role_health_config_guards.py` | **新增 9 条**（R1–R3 / H1–H3 / M1–M3） | §3.22：`permissions_for` 最小权限 / `/api/health` 生产脱敏 / 审核后端启动校验 |
| `backend/tests/test_file_download_endpoint.py` | **新增 20 条**（D1–D8） | §3.25：卷宗下载端点（租户身份 / 租户目录边界 / 白名单双向 / 留痕不阻断 / AST） |
| `backend/app/api/v1/files.py` | **改产品代码（3 处）** | §3.25：`user.tenant_id` ⇒ `ctx.tenant_id`；`_resolve_abs_path` 补 `tenant_root`；留痕外包 `try/except` |
| `evidence/scan_route_coverage.py` | **新增探针** | §3.24：路由 × 端点层覆盖扫描，**棘轮基线 55**，已进 `run_ci_probes.py::GATED` |
| `evidence/run_ci_probes.py` | 加 1 行分档 | 把新探针登记进 `GATED`（否则 runner 会因为「新探针未分类」直接失败） |
| `backend/tests/test_notification_endpoint_layer.py` | **新增 12 条**（N1–N9） | §3.26：`notifications/*` 6 条路由的端点层判据（**本轮结果为「无缺陷」**） |
| `backend/tests/test_conversation_endpoint_layer.py` | **新增 17 条**（C1–C11） | §3.27.1：`conversations/*` 3 条路由端点层判据；C4b 补上了「租户底线此前无判据」的洞；C11 是归属伪造发现（Q-U） |
| `backend/tests/test_review_endpoint_layer.py` | **新增 15 条**（R1–R12） | §3.27.2：`reviews/*` 7 条路由端点层判据；R12 钉住状态机白名单（此前压的是冗余的那一层） |
| `backend/tests/test_dispatch_endpoint_layer.py` | **新增 9 条**（P1–P9） | §3.28.1：`dispatches/*` 4 条路由端点层判据；**先红后修，查出 P0 级跨租户写** |
| `backend/app/api/v1/dispatches.py` | **改产品代码** | §3.28.1：新增 `_dispatch_or_404()`，`accept` / `grab` 两个端点都过它（此前**全程无租户校验**，可改写他租户案件的承办人与状态） |
| `backend/tests/test_analysis_endpoint_layer.py` | **新增 11 条**（A1–A10） | §3.28.2：`analyses/*` 6 条路由端点层判据（**结果为无缺陷**；`generate` 的守卫在 `job_handlers.py`） |
| `backend/tests/test_document_endpoint_layer.py` | **新增 9 条**（W1–W8） | §3.29.1：`documents/*` 5 条路由端点层判据；**先红后修，查出 2 个 P0**（跨租户写变量 / 跨租户读正文 + 记错账） |
| `backend/app/api/v1/documents.py` | **改产品代码** | §3.29.1：新增 `_document_or_404()`，`collect` / `render` 都过；并把 `get_document` 的内联重复判据收敛到同一处 |
| `backend/tests/test_compliance_endpoint_layer.py` | **新增 8 条**（K1–K6） | §3.29.2：`compliance/*` 2 条路由端点层判据（**无缺陷**） |
| `backend/tests/test_audit_retention_endpoint_layer.py` | **新增 13 条**（L1–L10） | §3.31：`audit/retention/*` 5 条路由端点层判据；**先红后修，查出一个合规级缺陷**（`purge` 的 `days` 绕过等保 180 天下限） |
| `backend/app/api/v1/audit_retention.py` | **改产品代码** | §3.31.1：`PurgeRequest.days` / `ArchiveRequest.days` 加 `ge=SECURITY_BASELINE_DAYS`（下限只在配置侧生效，请求侧完全没拦） |
| `evidence/verify_route_coverage.py` | 基线 55 → 49 → 39 → 29 → 22 → 16 → 11 → **4**（§3.32 计费 3 + 投诉 4）→ **0**（§3.33 鉴权/知识/问答 4 条） | §3.26 通知 6；§3.27 会话 3 + 复核 7；§3.28 派单 4 + 分析 6；§3.29 文书 5 + 合规 2；§3.30 任务 2 + 归档 4；§3.31 审计留存 5；**§3.32 计费/投诉 7；§3.33 鉴权/知识/问答 4** |
| `evidence/verify_service_tenant_param.py` | **新增探针** | §3.29.3：「服务层不收 `tenant_id`」模式横扫，**基线 20、只防新增** |
| `evidence/run_ci_probes.py` | 分档 8 → **9** → **10** | 登记 `verify_spacing_scale.py` / `verify_font_stack.py`（设计侧）与本轮 `verify_service_tenant_param.py`；判据 0 **当天咬合两次** |
| `backend/tests/test_job_endpoint_layer.py` | **新增 11 条**（J1–J10） | §3.30.1：`jobs/*` 2 条路由端点层判据（**无缺陷**；J8 用记录器替掉真队列，避免真去调 LLM） |
| `backend/tests/test_archive_endpoint_layer.py` | **新增 11 条**（V1–V10） | §3.30.2：`archives/*` 4 条路由端点层判据（**无缺陷**；V9 压的是与业务结果无关的审计留痕） |

`ruff` 全绿；故障注入后 `grep -rn "INJECTED" app/ tests/` ⇒ 无残留。
本轮**未改动任何产品代码**（§3.30 两批均为「无缺陷」）。

---

## 5. 验证结果

### 5.1 全量回归

| 层级 | 命令 | 结果 | 耗时 |
|------|------|------|------|
| 静态 | `ruff check`（新增/改动文件） | ✅ All checks passed | — |
| 中途测点 | `pytest tests -q`（+18+5+5，CORS 尚未落盘） | ✅ **432 passed / 0 failed**，exit 0 | 750.99s |
| 中途测点 | `pytest tests -q`（+4 CORS，含 `main.py` 守卫） | ✅ **436 passed / 0 failed**，exit 0 | 746.40s |
| 中途测点 | `pytest tests -q`（+4 审计覆盖） | ✅ **440 passed / 0 failed**，exit 0 | 751.65s |
| 中途测点 | `pytest tests -q`（+7 审核执行链） | ✅ **447 passed / 0 failed**，exit 0 | 763.61s |
| 中途测点 | `pytest tests/test_job_claim.py -q`（+3 回收预算，修复前） | 🚨 **12 passed / 2 failed**（新判据坐实缺陷） | 170.82s |
| 中途测点 | `pytest tests/test_job_claim.py -q`（+3 回收预算，修复后） | ✅ **14 passed / 0 failed** | 175.90s |
| 中途测点 | `pytest tests -q`（+5 检索接线 +3 回收预算） | ✅ **455 passed / 0 failed**，exit 0 | 801.59s |
| **最终** | `pytest tests -q`（+6 令牌承载 K1–K6） | ✅ **461 passed / 0 failed**，exit 0 | **809.03s** |
| 前端 | `python evidence/verify_token_storage.py` | ✅ **EXIT=0**（F1–F6 全通过） | — |
| 前端自检 | `python evidence/verify_token_storage.py --self-test` | ✅ **6/6 臂转红**，干净样本 0 缺陷 | — |
| **收尾（§3.24/§3.25）** | `pytest tests/ -q`（+20 下载端点 D1–D8） | ✅ **593 passed / 0 failed**，exit 0 | **977.63s** |
| **收尾（§3.26）** | `pytest tests/ -q`（+12 通知端点 N1–N9） | ✅ **605 passed / 0 failed**，exit 0 | **907.97s** |
| **收尾（§3.27）** | `pytest tests/ -q`（+17 会话 C1–C11 +15 复核 R1–R12） | ✅ **637 passed / 0 failed**，exit 0 | **1348.48s** |
| **假红实证** | 同上，**不绕过沙箱**跑一次 | 🚨 **636 passed / 1 failed / 1 error**（`test_archive_service` / `test_storage_path_containment`）⇒ 单独复跑 **2 passed** ⇒ **沙箱拒 `storage/local` 落盘**造成的假红 | — |
| **收尾（§3.28）** | `pytest tests/ -q`（+9 派单 P1–P9 +11 分析 A1–A10） | ✅ **657 passed / 0 failed**，exit 0 | **1217.85s** |
| **收尾（§3.29）** | `pytest tests/ -q`（+9 文书 W1–W8 +8 合规 K1–K6） | ✅ **674 passed / 0 failed**，exit 0 | **1130.96s** |
| **收尾（§3.30）** | `pytest tests/ -q`（+11 任务 J1–J10 +11 归档 V1–V10） | ✅ **696 passed / 0 failed**，exit 0 | **1185.78s** |
| **收尾（§3.31）** | `pytest tests/ -q`（+13 留存 L1–L10） | ✅ **709 passed / 0 failed**，exit 0 | **1216.49s** |
| **先红后修（§3.31）** | 修复**前**跑 `test_audit_retention_endpoint_layer.py` | 🚨 **4 failed / 9 passed**（L4/L5/L5b/L7）⇒ 缺陷坐实 + 1 条判据自身写错 | 36.02s |
| **证据门禁** | `python evidence/run_ci_probes.py`（登记 `verify_service_tenant_param.py` 后） | ✅ **10 通过 / 0 失败 / 0 跳过**，exit 0 | **80s** |
| **证据门禁** | `python evidence/run_ci_probes.py`（**CI 同款命令**） | ✅ **10 通过 / 0 失败 / 0 跳过**，exit 0（设计侧 `verify_font_stack.py` + 本轮 `verify_service_tenant_param.py`） | **80s** |
| **门禁自纠** | 同上（**登记前**） | 🚨 **直接失败**：`verify_adjacent_targets.py` / `verify_spacing_scale.py` **未分类** ⇒ 判据 0 咬合 | — |
| 门禁自检 | `python evidence/run_ci_probes.py --self-test` | ✅ **4/4 臂触发**（含「全部跳过 ⇒ 失败」） | — |
| 路由扫描（修复前） | `verify_route_coverage.py` | 🚨 **56 条零覆盖**（朴素版曾虚报到 67） | — |
| 路由扫描（修复后） | `verify_route_coverage.py` | ✅ **22 条零覆盖** = 棘轮基线，未突破（55 → 49 → 39 → 29 → 22） | 5.4s |
| 模式横扫 | `verify_service_tenant_param.py`（**只报不判**） | 🚨 **20 条候选**：服务层方法不收 `tenant_id` 但端点在调用 | 0.4s |
| 组件接线自检 | `verify_component_wiring.py --self-test` | ✅ **12/12**（原 8 + 棘轮 4） | — |
| CI YAML | `yaml.safe_load(ci.yml)` | ✅ jobs = `backend / frontend / evidence / security-audit / docker-build` | — |
| **回归复验** | `pytest tests/ -q`（带 sqlite env） | ✅ **461 passed / 0 failed**，exit 0 | 829.67s |
| **最终（本轮末）** | `pytest tests/ -q`（+9 租户 +21 权限门控 +26 落盘 +9 上报 +6 计费边界） | ✅ **544 passed / 0 failed**，exit 0 | 855.98s |
| **零判据实证** | 同套件，注入 permissive，排除两个新文件 | 🚨 **473 passed / 0 failed**（门控从未被执行） | 969.01s |
| **零判据实证 3** | 注入「reparse 无客户归属校验」，排除新文件 | 🚨 **544 passed / 0 failed**（§3.19） | 851.96s |

⚠️ 两次都是 `dangerouslyDisableSandbox` 跑的——沙箱会拒绝 SQLite `*.db-journal`，
带沙箱跑会出现大量假 `E`（历史实测 31 error + 1 F，与被拒文件一一对应）。

### 5.2 故障注入结果（**判据有效性的唯一证据**）

| 缺口 | 注入方式 | 期望 | 实测 |
|------|----------|------|------|
| P0-14 | 守卫去掉 `r.tenant_id != tenant_id`（注入 A） | G1/G3/G4 红，G2/G5 绿 | ✅ 3 failed / 2 passed |
| P0-14 | 端点注释掉守卫调用（注入 B） | G4/G5 红 | ✅ 2 failed / 3 passed（G5 改 AST 后） |
| P0-14 | **同一注入 A 跑留档脚本** | — | ✅ **15/15 全绿（假绿）** |
| P0-14 | **同一注入 B 跑留档脚本** | — | ✅ **15/15 全绿（假绿）** |
| P0-3 | 守卫分支置 `if False` | L2 三个参数全红，L1/L3 绿 | ✅ 3 failed / 2 passed |
| P0-3 | `_ENV_KEY_NAME[LOCAL]` 写成 STRONG 变量名 | 仅 LOCAL 参数红 | ✅ 1 failed / 4 passed |
| P0-11 | 注释掉 `_assert_cors_origins_safe()` | 仅 C2 红 | ✅ 1 failed / 3 passed |
| P0-8 | 删除 `void_review` 的 `record(...)` | A1/A3/A4 红，A2 自检绿 | ✅ 3 failed / 1 passed |
| P0-8 | 新增一个无留痕写端点 | A3/A4 红（棘轮生效） | ✅ 2 failed / 2 passed |
| P0-16（体检） | 计费门控改回 `billable = True` | 四条「不计费」全红 | ✅ 5 failed / 32 passed |
| P0-5（体检） | `claim_job` 去掉状态条件 | 独占性 / 终态 / RUNNING 全红 | ✅ 3 failed / 14 passed |
| P0-13 | `check()` 一律返回 PASS（**拦截完全失效**） | M1/M3/M4/M5/M7 红，M2/M6 绿 | ✅ 5 failed / **30 绿（旧用例全无感）** |
| P0-13 | 仅破坏拦截指标 | M7 单条红 | ✅ 1 failed / 6 passed |
| **P0-2** | `query_vector = None`（原缺陷） | 向量召回判据红 | ✅ 1 failed / 16 passed |
| **P0-2** | 返回零向量（更隐蔽的退化） | 同上 | ✅ 1 failed / 16 passed |
| **P0-2** | **删掉 `if query_vector is not None` 保护** | — | 🚨 **17 passed / 0 failed（全绿＝无判据）** |
| **P0-2 R1** | 写侧 `KnowledgeEmbedding(` → `dict(` | R1 红 | ✅ 1 failed / 4 passed |
| **P0-2 R2** | 在产品代码里 import `Retriever` | R2 红（报错含明细） | ✅ 1 failed / 4 passed |
| **P0-2 R3** | `if tenant_id:` → `if False:` | R3 红 | ✅ 1 failed / 4 passed |
| **P0-2 R4** | 给内存后端**补上**租户过滤（"修好"它） | R4 红（登记型判据） | ✅ 1 failed / 21 passed |
| **P0-2 R5** | `search()` 漏传 `tenant_id` | R5 红 | ✅ 1 failed / 4 passed |
| **#4 J1** | 耗尽分支的判据改成恒假 | J1 红 | ✅ 1 failed / 2 passed |
| **#4 J2** | 回收不 `retry_count + 1` | J2 红 | ✅ 1 failed / 2 passed |
| **#4 J3** | lifespan 的 `create_task` 换成别的协程 | J3 红 | ✅ 1 failed（1.66s） |
| **#15（体检）** | `_drain(session)` → `[]`（回滚不丢弃队列） | 幽灵通知防线红 | ✅ **4 failed / 21 passed（判据真实，不补）** |
| **P0-7 K1** | `_base_kwargs.httponly` → `False` | K1 + 既有 csrf 都红 | ✅ 4 failed / 13 passed |
| **P0-7 K1** | **绕过 `set_refresh_cookie()`**（不下发 Cookie） | 🚨 既有判据**全绿** | ✅ **仅 K1 红 / 16 绿** |
| **P0-7 K2** | 响应体回传 `refresh` | K2 红 | ✅ 1 failed / 4 passed |
| **P0-7 K3** | csrf 改成 HttpOnly（过度加固） | K3 红 | ✅ 1 failed / 4 passed |
| **P0-7 K4** | 生产守卫置 `if False` | K4 红 | ✅ 1 failed / 1 passed |
| **P0-7 K5** | `create_access_token` 改 24 小时 | K5 红 | ✅ 1 failed / 1 passed |
| **P0-7 K5** | ⚠️ **只改类默认值**（被 `.env` 覆盖） | — | 🚨 **绿（注入没生效，不是判据通过）** |
| **P0-7 K5/K6** | 改 `.env` 为 1440 | K5 + K6 都红 | ✅ 2 failed |
| **P0-7 F1** | SDK 真实插入 `localStorage.setItem("nlaw_token")` | 门禁 `EXIT=1` | ✅ 1 条缺陷，F1 定位准确 |
| **P0-7 F1–F6** | `--self-test` 内存注入 6 臂 | 全部转红 | ✅ 6/6 转红，干净样本 0 缺陷 |
| **Q-I 棘轮 A** | 把 `InfiniteList` 移出 `KNOWN_GAPS`（**不许涨**） | `EXIT=1` | ✅ 报 `✗ InfiniteList：PREVIEW_ONLY` |
| **Q-I 棘轮 B** | 把已接线的 `TabBar` 塞进 `KNOWN_GAPS`（**不许赖**） | `EXIT=1` | ✅ 报「已接线但仍留在豁免表」 |
| **Q-I 过期口径** | `ConversationCreate.channel` 退回 `str` | 事实1 转红 | ✅ `EXIT=1`；还原 `EXIT=0` |
| **Q-I F4 窗口** | 第一处 `credentials: "include"` → `"omit"` | F4 转红 | 🚨 **改前仍绿（窗口跨调用掩盖）**；改后转红 |
| **Q-I 自检解耦** | 真实 SDK 写入令牌 | 自检 `0` + 实测 `1` | 🚨 **改前自检也挂（误诊为"工具坏"）**；改后诊断正确 |
| **Q-I 判据 0** | 新建 `verify_zzz_tmp_probe.py` 不分类 | 运行器 `EXIT=1` | ✅ 报「仓库里有但没分类」 |
| **Q-I 端到端** | 真实 SDK 写 `localStorage.setItem("access_token")` | 运行器 `EXIT=1` | ✅ 自检 `0` / 实测 `1` / 运行器 `1`，文案「产品缺陷」 |
| **Q-I 运行器自检** | 合成故障：全跳过 / 有缺陷 / 全绿 / 自检挂 | 4 臂各自触发 | ✅ 4/4 |
| **Q-K 探针自检** | 合成源码三臂：全脏 / 全净 / 只修一条 | 各自命中 | ✅ 3/3（首轮**抓出两处判据自身缺陷**，见下） |
| **Q-K 棘轮 A** | 注册端点移出 `AUTH_RATE_LIMITED_PATHS`（**不许涨**） | `EXIT=1` | ✅ 报「未登记，棘轮不许涨」 |
| **Q-K 棘轮 B** | `RegisterRequest` 加 email 但保留豁免（**不许赖**） | `EXIT=1` | ✅ 报「已收口但仍挂豁免，请删」 |
| **Q-K 判据自纠 1** | —— | —— | 🚨 真实代码是 **带注解赋值** `AnnAssign`，只解析 `Assign` ⇒ **把好代码判成缺陷（假红）** |
| **Q-K 判据自纠 2** | —— | —— | 🚨 宽松正则「出现 `return forwarded` 就算」把**已被信任判断保护**的写法也判红 |
| **Q-K 修复·旧实现** | `_client_ip` 退回「无条件采信 XFF 首段」 | R7/R8/R10/R11 **+ T8** 红 | ✅ `5 failed / 30 passed`（防"太松"）；T8 失败原因 `assert 200 == 429` = 换头即开新桶 |
| **Q-K 修复·过头** | `_client_ip` 改成「一律无视 XFF」 | R8/R9/R10/R12 红 | ✅ `4 failed / 20 passed`（防"太严"；**R12 是唯一判别项**） |
| **Q-K 棘轮真实咬合** | 修复落地但**不删**豁免 | 探针 `EXIT=1` | ✅ 报 `XFF_TRUST: HEALED` ⇒ 删豁免后转 `EXIT=0` |
| **§3.13 租户·太松** | `deps.py` 去掉 `user.role == PLATFORM_ADMIN`（无条件采信头部） | X1×4 + X5 红 | ✅ **5 failed / 4 passed**，失败原因＝非管理员切成了目标租户 |
| **§3.13 租户·太严** | 同一条件置 `if False`（管理员也切不了） | X2 红 | ✅ **1 failed / 8 passed**（防"把功能焊死"） |
| **§3.14 门控·太松** | `if not ok:` → `if False:`（一律放行） | P2×4 / P4×2 / P6 红 | ✅ **7 failed / 14 passed** |
| **§3.14 门控·太严** | `if not ok:` → `if True:`（一律 403） | P1 / P3 / P5 / P7×2 红 | ✅ **5 failed / 16 passed**（**P6/P7 成对**才抓得到这一侧） |
| **§3.14 无工厂校验** | 三段参数校验全置 `if False:` | P8 / P9 / R3 红 | ✅ **3 failed / 18 passed** |
| **§3.14 零判据实证** | 注入 permissive 后跑**排除本轮新文件**的既有套件 | —— | 🚨 **473 passed / 0 failed（全绿＝此前无判据）** |
| **§3.15 回到原缺陷** | `_resolve_under_root` 的包含性判断置 `if False:` | S4×5 + S6×5 红 | ✅ **10 failed / 16 passed** |
| **§3.15 焊死** | 同一判断置 `if True:`（一律拒绝） | S3 / S5 红 | ✅ **2 failed / 24 passed**（反向：防上传功能整个废掉） |
| **§3.15 原名落盘** | `_safe_name` 直接返回原始文件名 | S1×9 / S2 / S5 红 | ✅ **11 failed / 15 passed** |
| **§3.15 收紧租户判空** | `assert_tenant` 改成 `is not None` | A3 红 | ✅ **1 failed / 25 passed**（**登记型**判据按设计生效） |
| **§3.16 无上报义务** | `needs_report = False`（整段消失） | R1×2 / R5 / R6 红 | ✅ **4 failed / 5 passed** |
| **§3.16 一律上报** | `needs_report = True` | R3×2 红 | ✅ **2 failed / 7 passed**（防人工队列被淹没） |
| **§3.16 回到语义错配** | 失败时一律落 `PENDING` | R5 红 | ✅ **1 failed / 8 passed**（本轮修的那个点） |
| **§3.16 假上报** | `reported = True` | R5 / R6 / R7 / R1×2 红 | ✅ **5 failed / 4 passed** |
| **§3.16 零判据实证** | 注入「无上报义务」后跑**既有**审核测试 | —— | 🚨 **37 passed / 0 failed（全绿）** ⇒ P0-13 那 7 条对上报完全无感 |
| **§3.17 写死租户** | `dashboard("fixed-tenant", ...)` | B1 红 | ✅ **1 failed / 5 passed** |
| **§3.17 摘掉一个门** | 摘掉 `work-orders` 路由的 `dependencies` | B2 / B4 红 | ✅ **2 failed / 4 passed**（结构 + 运行时**都**红） |
| **§3.17 数据边界失效** | `list_work_orders` 去掉 `where(tenant_id == ...)` | B3 / B5 红 | ✅ **2 failed / 4 passed** |
| **§3.17 功能焊死** | `list_work_orders` 一律返回 `[]` | B5 红 | ✅ **2 failed / 4 passed**（反向量） |
| **§3.17 判据自纠** | —— | —— | 🚨 B1 首版只扫关键字实参 ⇒ **只命中 1/3**，靠 `assert checked >= 3` 自检才没变成静默空集 |
| **§3.18 U1 传错租户** | `save_upload(file, user.tenant_id, ...)` | U1 红 | ✅ **1 failed / 12 passed** |
| **§3.18 U3 落库串号** | `Evidence(tenant_id=user.tenant_id, ...)` | U3 + U4 红 | ✅ **2 failed / 11 passed**（AST 与运行时**同时**红） |
| **§3.18 U4 后台任务串号** | `trigger_evidence_parse(..., user.tenant_id, ...)` | U4 红 | 🚨 **首版 13 passed / 0 failed**；加平台管理员后 ✅ **1 failed / 12 passed** |
| **§3.18 U2 先落盘后鉴权** | 对调 `_case_or_404` 与 `save_upload` 的顺序 | U2 + U5 + U6 红 | ✅ **3 failed / 10 passed** |
| **§3.18 摘掉归属守卫** | 删掉 `_case_or_404` 调用 | U2 + U5 + U6 红 | ✅ **3 failed / 10 passed** |
| **§3.19 回到原缺陷** | 删掉 `_evidence_or_404` 调用 | V1 红 | ✅ **1 failed / 12 passed** |
| **§3.19 绕过纵深防御** | `EvidenceService(db).parse(evidence_id)`（不传租户） | V5 红 | ✅ **1 failed / 12 passed** |
| **§3.19 焊死** | `_evidence_or_404` 一律抛 404 | V3 + V4 红 | ✅ **2 failed / 11 passed**（反向：防重解析功能被废） |
| **§3.19 零判据实证** | 注入「无归属校验」后跑**排除新文件**的既有套件 | —— | 🚨 **544 passed / 0 failed（851.96s）** ⇒ 此前无任何判据 |
| **§3.16 R4 自纠** | —— | —— | 🚨 签名里留了个没用的 `caplog` ⇒ `-p no:logging` 下 error；平时是死重量 |
| **§3.21 C·Bearer 不再豁免** | 删掉 `if Authorization: return False` | C4 红 | ✅ **1 failed / 6 passed** |
| **§3.21 C·中间件一律不查** | `_needs_check` 恒 `False` | C1/C3/C4/C5 红 | ✅ **4 failed / 3 passed** |
| **§3.21 C·一律都查** | `_needs_check` 恒 `True` | C4/C5 红 | ✅ **2 failed / 5 passed**（防过度加固） |
| **§3.21 C·不比内容** | 去掉 `cookie_token != header_token` | C3 红 | ✅ **1 failed / 6 passed** |
| **§3.21 C·strict 吃掉方法过滤** | 删掉 `method in (POST,PUT,PATCH,DELETE)` | C5 红 | ✅ **1 failed / 6 passed** |
| **§3.21 零判据反证 4** | 中间件恒不查，跑既有 3 个 CSRF 相关文件 | —— | 🚨 **29 passed / 0 failed** |
| **§3.22 R·未知角色通配** | 兜底改成 `frozenset({"*"})` | R1/R3 红 | ✅ **2 failed / 7 passed** |
| **§3.22 R·矩阵少一个角色** | `ROLE_PERMISSIONS.pop(ENTERPRISE_USER)` | R2 红 | ✅ **1 failed / 8 passed** |
| **§3.22 H·生产泄露档位** | `llm_providers` 去掉生产置 None | H1 红 | 🚨 **首版锚点打到 CORS 守卫 ⇒ 9 passed（注入没生效）**；改唯一锚点后 ✅ **1 failed / 8 passed** |
| **§3.22 H·永远 healthy** | `llm_ready = True` | H2 红 | ✅ **1 failed / 8 passed** |
| **§3.22 M·取值域校验消失** | `if not in allowed` → `if False` | M1 红 | ✅ **1 failed / 8 passed** |
| **§3.22 M·跨字段校验消失** | `external and not KEY` → `if False` | M2 红 | ✅ **1 failed / 8 passed** |
| **§3.25 ① 回到 user 判等** | `ctx.tenant_id` 改回 `getattr(user, "tenant_id")` | D4 红 | ✅ **1 failed / 19 passed** |
| **§3.25 ② 拆掉租户目录约束** | 去掉 `tenant_root=` 实参 | D5 红 | ✅ **1 failed / 19 passed** |
| **§3.25 ③ 留痕异常不再兜** | `except` 改成 `raise` | D7b 红 | ✅ **1 failed / 19 passed** |
| **§3.25 焊死白名单** | `if ext not in _ALLOWED_EXT` → `if True` | D1 红 | ✅ **1 failed / 19 passed**（反向量） |
| **§3.25 拆掉存储根校验** | `candidate != root` → `if False` | D6 红 | ✅ **1 failed / 19 passed** |
| **§3.25 租户不符改 403** | `NotFoundError` → `PermissionDeniedError` | D2 红 | ✅ **1 failed / 19 passed**（防泄露存在性） |
| **§3.25 不留痕** | 留痕调用前插入 `raise` | D7 红 | ✅ **1 failed / 19 passed** |
| **§3.26 丢掉 user_id** | `_owned` 去掉 `user_id` 条件 | N2/N4/N5/N5b/N7/N7b/N7c 红 | ✅ **7 failed / 5 passed** |
| **§3.26 批量丢归属** | `mark_read_batch` 去掉 `_owned` | N7/N7b/N7c 红 | ✅ **3 failed / 9 passed** |
| **§3.26 详情错码** | 404 的 code 改成 `RESOURCE_NOT_FOUND` | N1/N5 红 | ✅ **2 failed / 10 passed** |
| **§3.26 破坏幂等** | `read_at` 每次刷新 | N6 红 | ✅ **1 failed / 11 passed** |
| **§3.26 静默类型** | 未知 type 返回 None | N8 红 | ✅ **1 failed / 11 passed** |
| **§3.26 接受 user_id 参数** | 列表端点加 `user_id` 查询参数 | N9 红 | ✅ **1 failed / 11 passed**（AST） |
| **§3.26 丢掉 tenant_id** | `_owned` 去掉 `tenant_id` 条件 | 🟢 **12 passed / 0 failed** | ⚠️ **不是判据失效，是发现**：`user_id` 全局唯一且仍在过滤 ⇒ 去掉租户条件**不产生**跨租户读取（详见 §3.26） |
| **§3.27 会话 7 臂 / 复核 9 臂** | 摘守卫 / 换身份 / 改状态机 / 改枚举域等 | —— | ✅ **15 红 / 1 绿**；绿的那臂（`tenantflooroff`）**查出判据自身的洞** ⇒ 补 C4b（所内身份）后转红；`fsmarchiveopen` 同理 ⇒ 补 R12 |
| **§3.28 派单 7 臂 / 分析 7 臂** | 摘守卫 / 反向量 / 顺序对调等 | —— | ✅ **14 全红**；`caseownerlost` 首版因**锚点不唯一**空转（`replace(...,1)` 打到另一处），加唯一性断言后转红 |
| **§3.29 文书 6 臂 / 合规 4 臂** | 摘守卫 / 改租户实参 / 删模板过滤等 | —— | ✅ **10 全红**；两臂坐实 P0（跨租户写变量 / 跨租户读正文 + 记错账） |
| **§3.30 任务 5 臂 / 归档 6 臂** | 摘守卫 / 传死租户 / 删审计 / 删前置条件等 | —— | ✅ **11 全红**；**3 臂首版空转**（CRLF / 注释进括号 / 缩进方法），靠注入器自检抓出，见 §3.30.3 |
| **§3.31 拆掉 `ge=180` 下限** | `PurgeRequest` / `ArchiveRequest` 去掉 `ge` | L4 / L5 / L5b 红 | ✅ **3 failed / 10 passed**（**先红后修**：修前 200 + deleted=5） |
| **§3.31 摘掉一条角色门控** | `retention_purge` 改用 `get_tenant_context` | L2 + L10 红 | ✅ **2 failed / 11 passed** |
| **§3.31 摘掉急停开关** | 删掉 `_ensure_enabled()` | L9 + L10 红 | ✅ **2 failed / 11 passed** |
| **§3.31 默认变成真删** | `confirm` 默认值 `False` → `True` | L6 + L8 + L1 红 | ✅ **3 failed / 10 passed** |
| **§3.31 删掉墓碑审计** | `if write_tombstone:` → `if False:` | L7 红 | ✅ **1 failed / 12 passed** |
| **§3.31 跳过归档行数校验** | `if arc["rows"] != pending:` → `if False:` | 🟢 **首版 13 passed / 0 failed** | ⚠️ **判据有洞**：数据一致时有没有这道校验结果一样 ⇒ 补 **L8c**（造假行数）后 ✅ 1 红 |

最后两行是本轮**唯一没靠人眼、靠自检抓出来**的判据缺陷：合成夹具此前写的是不带注解的
形状，覆盖不到真实语法 ⇒ 已同步把夹具改成复刻真实形状（`methodology.md` 第 65 条）。

还原后 `grep -rn "FAULT-INJECTION" app/ tests/ frontend/packages/sdk evidence/` ⇒ 无残留，
`diff` 比对 `middleware.py` / `schemas/auth.py` / `evidence.py` 与注入前备份**字节一致**，
相关测试 `34 passed`（本轮累计 **80 次**注入）。

> **§3.18 U4 那一臂值得单独说**：注入后 **13 条全绿**，当时最容易得出的结论是
> 「这条链路没问题」。实际是**判据的夹具**有问题——所有测试用户的
> `user.tenant_id` 恒等于 `ctx.tenant_id`，两个恒等的量之间任何判据都测不出差异。
> 补了平台管理员（`user.tenant_id=="platform"` + `X-Tenant-Id` 切换）后才转红。
> ⇒ **注入臂全绿时，先怀疑判据，产品代码未必是清白的。**

---

## 6. 行动清单

### 6.1 已完成（本轮）

- [x] P0-6 上传档限流死档修复 + 18 条判据
- [x] P0-14 复核越权 5 条判据（进 CI）
- [x] P0-3 LLM 侧生产守卫 5 条判据
- [x] P0-11 CORS 生产守卫 + 4 条判据
- [x] P0-8 审计覆盖棘轮 4 条判据（覆盖缺口本身转 Q-F 待裁定）
- [x] P0-13 拦截**执行链** 7 条判据（服务层 + 留痕 + HTTP + 指标）
- [x] P0-16 / P0-5 **体检确认判据真实有效**（未改动代码）
- [x] P0-2 向量检索：判据有效性三分（有 / 无 / 断链）+ 5 条接线与租户判据
- [x] #4 任务恢复：**真缺陷修复**（回收消耗重试预算、耗尽判 FAILED、`run_job` 改累加）+ 3 条判据
- [x] #15 通知：**体检确认幽灵通知防线真实有效**（4 红 / 21 绿，不补）
- [x] P0-7 令牌承载：后端补 6 条（**响应头层**，补上"绕过 helper"这个既有判据抓不到的场景）
- [x] P0-7 令牌承载：前端补 `evidence/verify_token_storage.py`（F1–F6 + 自检 6 臂）
- [x] **16 项 P0 全部覆盖完毕**（#9 阻塞、#12 已分解，不计入未覆盖）
- [x] **Q-I 落地（§3.11）**：`evidence/verify_*.py` 接进 `ci.yml` 的 `evidence` job（**6 探针 / 79s** / 零新增依赖）
- [x] **Q-A 派生核查（§3.12）**：公开注册滥用防线 5 条事实固化 + 探针进门禁（首日绿，2 条欠账挂棘轮）
- [x] **Q-K ② 收口**：`XFF_TRUST` 真修复（`TRUSTED_PROXIES` + 右起不可信跳）+ R7–R12 双向注入 + **删除豁免**
- [x] **决策台账** `decisions-2026-09-20.md`：Q-A/B/C/I 已裁定；Q-K/L/M/**N** 待你定
- [x] 顺手修掉三个「把门禁接进来才暴露」的问题：`conversation_channel_500` 过期口径、
      `token_storage` 的 F4 窗口跨调用、自检读真实文件导致的**误诊**
- [x] **§3.13 租户上下文**：`X-Tenant-Id` 边界补 9 条判据 + 修掉**纯空白头部**静默缺陷
- [x] **§3.14 路由级权限门控**：`require_permissions` 补 21 条判据 + 修掉两个静默失败
      （空清单=公开端点、`mode` 拼错=静默降级）
- [x] **§3.15 上传落盘**：唯一写盘路径补 26 条判据 + 修掉**静默目录穿越**
      （`tenant_id` 未净化即参与拼路径，实测 4 种输入逃出存储根、其中 1 种跨盘符）
- [x] **§3.16 合规上报**（第十四条）：补 9 条判据 + 修掉**状态语义错配**
      （上报失败被错分成「通道未配置」⇒ 运维做错处置、statutory 上报被延误）
- [x] **Q-O 收口（§3.17）**：计费端点补 6 条**数据边界**判据
      （AST 结构 + HTTP 跨租户 + 反向 + 登记型）⇒ 「门」和「门里的东西」都守住了
- [x] **§3.18 证据写端点**：补 8 条判据（U1–U8：AST 结构 + **调用顺序** + HTTP 数据边界
      + 「拒绝后不留文件」+ 反向）⇒ 上传端点此前 **0 覆盖**
- [x] **§3.19 `POST /evidence/{id}/parse` 缺客户归属校验（真缺陷，已修）**：
      同租户客户乙可遍历 `evidence_id` 读到他人材料的 `ocr_text` 全字段**并覆盖解析结果**；
      补 5 条判据（V1–V5）+ `EvidenceService.parse` 补传 `tenant_id`
- [x] **§3.20 判据自纠两处**：U4 夹具恒等导致注入臂假绿（补平台管理员）；
      §3.16 的 R4 有个没用到的 `caplog` 参数（已删）
- [x] **§3.21 CSRF 整机**：补 7 条中间件判据（此前 10 条令牌原语 + 0 条整机），
      第 4 次零判据反证：中间件恒不查 ⇒ 既有 29 条照绿
- [x] **§3.22 其余候选裁定**：`_check_builtin` / `_check_external` 经核查是
      **名字扫描的假阳性**（已覆盖）；`permissions_for` / `health_check` /
      `_validate_moderation_backend` 各补 3 条
- [x] **§3.23 注入脚本自纠两处**：锚点不唯一（打到 CORS 守卫，跑出假绿）；
      多文件注入没逐臂还原（跨文件残留污染后续臂）
- [x] **§3.24 全量路由覆盖扫描**：79 条路由，**55 条零端点层请求**
      （A 类 48：服务层有测试、装配没测；B 类 7：整套没测）；
      已固化为 `evidence/verify_route_coverage.py`，**棘轮基线 55**，进 CI `GATED`
- [x] **§3.26 第二批 `notifications/*` 6 条**：补 12 条端点层判据，
      **结论是无缺陷**（6 条路由此前从未被请求，现在「端点层真的通」是实测而非推断）；
      7 臂注入 6 红 1 绿，绿的那臂查出一个结构事实：
      `user_id` 是唯一关口、`tenant_id` 是纵深防御（丢前者 7 条红，丢后者 0 条红）。
      棘轮基线 55 → 49 → 39 → 29 → **22**
- [x] **§3.25 卷宗下载端点三个真实缺陷（已修）**：
      ① `user.tenant_id` ⇒ `ctx.tenant_id`（平台管理员切换租户后 404）；
      ② 补「租户目录」边界（`%2e%2e` 可读到存储根文件，实测 200 + 内容）；
      ③ 留痕异常不再把下载打成 500。补 20 条判据，7 臂注入全红

### 6.2 需要拍板（新增）

- **Q-D：两个留档验证脚本怎么办？**
  ① 就地作废（判据已由 pytest 覆盖，且静态那一半是假绿）；
  ② 修 e2e 使其幂等（唯一用户名 / 启动时清库）并接进 CI；
  ③ 保持现状继续留档。
  **建议 ①**：留档脚本的价值是「当时的现场」，不是「持续有效的门禁」；
  继续让它躺在那里、偶尔被人手动跑一次看到 5/6 绿，比没有更危险。
- **Q-E：`allow_methods=["*"]` / `allow_headers=["*"]` 是否也要收窄？**
  本轮只处理了 origin（真正的越权面）。方法与头部的通配不造成跨域读取，
  但会让预检形同虚设。**建议单独立项，不阻塞本轮。**
- **Q-F：21 个未写 `audit_logs` 的写端点，哪些必须补留痕？**
  明细见 §3.6。已确认「由服务层 / 业务行代劳」的两类（auth、complaints）**不必补**；
  其余（会话、问答、通知已读、文书起草、计费、任务重试、审计归档清理）需合规拍板。
  **拍板前，新增写端点已被棘轮卡住**（不许涨）。
- **Q-G（新增，最需要拍板）：向量读侧要不要接？** 现状是**只写不读**——
  `KnowledgeEmbedding` 表在涨，但没有任何产品代码读它。
  ① **接**：把 `qa_service._retrieve` / `_tenant_knowledge` 换成 `Retriever`（BM25+向量+rerank）；
  ② **不接**：承认当前是关键词检索，把写侧也停掉，别再往库里堆没人读的向量；
  ③ **接，但只接 Postgres**：与「SQLite 部署不得启用向量召回」绑定（内存后端无租户隔离）。
  **建议 ③**：①② 各有明确代价，③ 同时消掉了 §3.7 附带的租户盲区。
- ~~**Q-I（新增）：前端要不要引入测试运行器？**~~ —— **本轮已按建议落地**（§3.11）：
  `evidence/verify_*.py` 已接进 `ci.yml`（不需要装任何依赖）。
  ⚠️ **但当前目录不是 git 仓库**，这条流水线**从未真正跑过** ⇒ 待用户确认仓库状态（Q-J）。
  另：`vitest`（真运行期前端测试）仍待评估 —— 现有一族是**源码级探针**，
  证明不了「浏览器里真的没写 localStorage」。
- **Q-S（新增，🟠 P1）：卷宗下载要不要按「案件归属」授权？**
  现状只做**租户级**校验：同租户内客户甲只要知道（或猜到）文件名，就能下载客户乙的
  卷宗。这与 §3.19 已修的 `reparse` 缺口是**同一个形状**（守卫按参数名分类，
  缺口也按参数名分布）。**本轮未改**，因为「下载是否要按案件归属授权」属访问模型决策。
  ① **改**：复用 `_evidence_or_404` 的思路，让下载也校验 `case.client_user_id`；
  ② **不改**：明确接受「卷宗按租户授权」，写进权限矩阵说明。
  **建议 ①**（与 §3.19 口径一致）；若选 ②，请把 Q-R 一并裁定，两处口径别再分叉。
- ~~**Q-T（新增，🟡 P2）：剩下 55 条零覆盖路由怎么办？**~~ —— **已按建议 ① 启动**：
  §3.25 清 1 条（查出 3 个缺陷）、§3.26 清 6 条（结论：无缺陷）。
  **剩余 49 条**，棘轮基线同步下调至 49（只许降不许涨 ⇒ 新端点必须带端点层判据）。
  §3.27 再清 10 条（会话 3 + 复核 7）⇒ **剩余 39 条**，基线同步下调至 39。
  §3.28 再清 10 条（派单 4 + 分析 6）⇒ **剩余 29 条**，基线同步下调至 29。
  §3.29 再清 7 条（文书 5 + 合规 2）⇒ **剩余 22 条**，基线同步下调至 22，
  且 **B 类（服务层也零引用）归零** —— 剩下 22 条全是「零件测过、装配没测」。
  **§3.30 再清 6 条（任务 2 + 归档 4）⇒ 剩余 16 条**，基线同步下调至 16。
  ⚠️ 这一批是**按模式挑的**（模式横扫探针的 20 条候选 ∩ 剩余 22 条），
  不是按路由顺序挑的 —— 比逐条试更快，且两批**都无产品缺陷**。
  **§3.31 再清 5 条（`audit/retention/*`）⇒ 剩余 11 条**，基线同步下调至 11。
  ⚠️ 模式横扫**没点名**这一批（20 条候选全在已清模块）⇒ 改用「**破坏性**」排序：
  别的模块最坏是「读/写别人的东西」，`purge` 是**不可逆删除审计日志**。
  实测命中：**查出一个合规级缺陷**（`days` 参数绕过等保 180 天下限）。
  下一批建议：`billing/*`（3，含已挂账的 **Q-P** `billing:write`）
  与 `complaints/*`（4）—— 前者有已知门控错配，后者是外部用户可触达的写端点。
- **Q-W（新增，🟡 P2）：手动 `retry` 把 `retry_count` 归零，算不算绕过重试上限？**
  现状：`POST /jobs/{id}/retry` 会 `job.retry_count = 0`，`JOB_MAX_RETRIES`
  只约束**自动重试**（`run_job` 内部累加）与僵尸回收。
  ⇒ 一个必然失败的任务可以被**无限手动重试**，每次都拿到一整批新预算。
  ① **接受现状**：认为「手动重试 = 人工确认过，值得给新预算」，把这一点写进文档；
  ② **改成累加**：手动重试不归零，预算耗尽后只能改需求重跑。
  **建议 ①**（人工重试与自动重试的语义本就不同），但必须**写下来**，
  否则下次有人看代码会以为是 bug。J8 已把当前行为钉住 ⇒ 改口径时判据会红。
- **Q-X（新增，🟡 P2）：`/audit/retention/policy` 的 `days` 参数是死的。**
  签名写着 `days: int = Query(0, description="传入自定义值可预览生效结果")`，
  函数体里调的却是 `retention_days()`（**不接参数**）⇒ 传什么都返回配置值。
  ① 删掉这个参数（**推荐**）② 真的实现「预览 N 天生效结果」。
  ⇒ 文档承诺了一个不存在的功能，运维会据此做错误的容量判断。
- **Q-Y（新增，🟠 P1）：墓碑审计的「落库」没有端点层判据。**
  `purge` 用 `log_detached_ctx` 走**独立连接**写 `AUDIT_RETENTION_PURGE`，
  那个连接绑的是 `settings.DATABASE_URL`（不是测试库）⇒ 端点层测试**读不到**。
  当前 L7 只钉到「被调用」。「**审计的删除也必须被审计**」是本模块自己写在
  docstring 里的硬要求，而它最关键的那一跳**没有判据**。
  ① 把墓碑改写进主会话（会踩锁，见服务层注释）② 测试里替换全局 session factory。
  **建议先做 ②**（不改产品代码就能补上判据）。
- **Q-H（新增）：僵尸任务 3 次后判 FAILED 是否可接受？**
  本次修复把「永不放弃」改成了「上限即失败」。**建议接受**——
  无限重启必然更糟（持续占 worker 与 AI 额度且永不可见）；
  但需要确认运维侧是否有「失败任务人工重试」入口（目前需重新入队）。

### 6.3 明确未做

- ~~P0-2 / #4 / #15 / P0-7 仅初筛~~ —— 均已升级为故障注入闭环，见 §3.7–3.10。
- ~~**前端判据不在 CI 里**~~ —— 已解决：5 个源码级探针进了 `evidence` job（§3.11）。
  **仍未解决**：前端**没有运行期测试**（无 vitest / jest，0 个 `*.test.*`）。
  现有一族能证明「SDK 源码没把令牌写进 localStorage」，**证明不了浏览器里真的没写**。
- **另外 20 个探针仍未进门禁**（需 CDP / dev server / 生产产物），理由逐条写在
  `run_ci_probes.py` 的 `NOT_GATED`，并带「要接进来还差什么」。
  其中 `dark_mode` 当前**实跑为红**（exit 1）——它不在门禁内，故不影响 CI，
  但**是一条未收口的真缺陷**，需要单独排查。
- **本轮扫荡的是「判据有效性」，不是「功能正确性」**。P0-16 那 5 条转红只证明
  「计费门控被删会被发现」，不证明 99 元定价合理、也不证明模型结论准确。

---

## 7. Non-goals 与证据边界（**必须与结论同读**）

1. **本轮的「扫荡」不是全量审计**。16 项 P0 中，**8 项**（#2/#4/#6/#7/#8/#11/#13/#14）做了
   「补判据 + 故障注入」的完整闭环，**4 项**（#5/#15/#16 + #1）做了故障注入体检并确认**无需补**，
   1 项（P0-12）已分解，1 项（P0-9）确认阻塞 ⇒ **16 项全部过了一遍**。
   ⚠️ **P0-7 的前端那一半是「源码级探针」而非运行期测试**：它能证明
   「SDK 没有把令牌写进 localStorage」，**证明不了「浏览器里真的没写」**
   （例如运行时第三方依赖偷偷写）。彻底解决需要前端测试运行器 ⇒ Q-I。
   ⚠️ **「体检无需补」也要带边界**：#15 只注入了「回滚不丢弃队列」这一种形态，
   不覆盖「同一会话回滚后再提交」等变体（虽已被 `pending_count==0` 间接钉住）。
2. **「故障注入通过」证明的是判据能检出那一种缺陷**，不证明它覆盖了所有可能的缺陷形态。
   例：P0-14 注入的是「删除租户比对」与「注释掉守卫调用」，
   拦不住「守卫比对了一个永远相等的字段」这类更隐蔽的错误。
3. **P0-6 的修复只改了匹配方式**，未重新评估各档阈值（20/5/10/15）是否合适。
4. 本轮**没有**修改 `verify_security_fixes*.py`，其 1/6 常年红仍然存在。

---

## 8. 数字自洽

- 前半轮基线（2026-09-20 实测）：**404 passed** = 384（第十四轮）+ 10（枚举校验）
  + 10（注册越权）
- 本轮新增：18（限流）+ 5（复核）+ 5（LLM 守卫）+ 4（CORS）
  + 4（审计覆盖）+ 7（审核执行链）= **43** ⇒ 447
- 本轮后半追加：5（检索接线 R1–R5）+ 3（回收预算 J1–J3）+ 6（令牌承载 K1–K6）= **14**
  （另有前端 F1–F6 属 `evidence/` 探针，**不计入 pytest 数**）
- 期望全量：447 + 14 = **461**
- **实测 461 passed / 0 failed（809.03s，exit 0）** ⇒ 自洽
- Q-I 落地后**复验**：**461 passed / 0 failed（829.67s，exit 0）** ⇒ 本段未动产品代码，基线不变
  （⚠️ 复验必须带 sqlite env，否则 `.env:13` 的 Postgres 会让 10 个模块收集失败）
- Q-K ② 收口后**复验**：461 + 6（R7–R12）= 期望 **467**
  ⇒ **实测 467 passed / 0 failed（1018.16s，exit 0）** ⇒ 自洽（本次**动了**产品代码）
- 补 T8（整机装配）后**复验**：467 + 1 = 期望 **468**
  ⇒ **实测 468 passed / 0 failed（1011.01s，exit 0）** ⇒ 自洽
- 补 uvicorn 两层守卫（F1–F5）后**复验**：468 + 5 = 期望 **473**
  ⇒ **实测 473 passed / 0 failed（1011.51s，exit 0）** ⇒ 自洽
- 补 §3.13 租户上下文（X1–X5，9 条）+ §3.14 权限门控（P1–P9/R1–R3/W1，21 条）后**复验**：
  473 + 30 = 期望 **503** ⇒ **实测 503 passed / 0 failed（976.30s，exit 0）** ⇒ 自洽
- 🚨 **「此前零判据」的直接证据**：把 `require_permissions` 的门控分支改成恒放行
  （注入 permissive），跑**排除本轮两个新文件之后**的既有套件
  ⇒ **473 passed / 0 failed（969.01s，exit 0）** ⇒
  **一条都没红**。这就是 §3.14「该门控从未被执行过」的实证，不是推断。
- 补 §3.15 落盘路径（S1–S6/A1–A3，26 条）后**复验**：503 + 26 = 期望 **529**
  ⇒ **实测 529 passed / 0 failed（992.72s，exit 0）** ⇒ 自洽
  （⚠️ 该次后台任务的收尾 `rm` 撞上删除守卫报 `SAFE_DELETE_BULK_CONFIRM_REQUIRED`，
  与测试结果无关；pytest 本身 `EXIT=0`）
- 补 §3.16 合规上报（R1–R8，9 条）后**复验**：529 + 9 = 期望 **538**
  ⇒ **实测 538 passed / 0 failed（897.60s，exit 0）** ⇒ 自洽
- 🚨 **第二处「零判据」实证**：把 `needs_report` 置恒 `False`（上报义务整段消失），
  跑既有的 `test_moderation.py` + `test_moderation_enforcement.py`
  ⇒ **37 passed / 0 failed** ⇒ 内容审核的 37 条判据对「有没有上报」**完全无感**
  （它们覆盖的是**拦截执行链**，不是**上报义务**）。
- 补 §3.17 / Q-O 收口（B1–B6，6 条）后**复验**：538 + 6 = 期望 **544**
  ⇒ **实测 544 passed / 0 failed（855.98s，exit 0）** ⇒ 自洽
- 补 §3.18 / §3.19 证据写端点（U1–U8 / V1–V5，13 条）后**复验**：544 + 13 = 期望 **557**
  ⇒ **实测 557 passed / 0 failed（861.42s，exit 0）** ⇒ 自洽
- 🚨 **第三处「零判据」实证**：把 `reparse` 的客户归属校验删掉（回到 §3.19 原缺陷），
  跑**排除新文件**的既有套件 ⇒ **544 passed / 0 failed（851.96s，exit 0）** ⇒
  一条都没红。这是本轮第三次用注入证明「绿 ≠ 有判据」。
- 补 §3.21 / §3.22（C1–C7，7 条 + R1–R3/H1–H3/M1–M3，9 条）后**复验**：
  557 + 16 = 期望 **573** ⇒ **实测 573 passed / 0 failed（880.88s，exit 0）** ⇒ 自洽
- 🚨 **第四处「零判据」实证**：把 CSRF 的 `_needs_check` 改成恒 `False`
  （**中间件完全不查**），跑既有的 3 个 CSRF 相关文件
  ⇒ **29 passed / 0 failed** ⇒ 那 10 条令牌原语测试对「拦不拦」完全无感。

- 补 §3.24 / §3.25（D1–D8，20 条）后**复验**：573 + 20 = 期望 **593**
  ⇒ **实测 593 passed / 0 failed（977.63s，exit 0）** ⇒ 自洽
- 补 §3.26（N1–N9，12 条）后**复验**：593 + 12 = 期望 **605**
  ⇒ **实测 605 passed / 0 failed（907.97s，exit 0）** ⇒ 自洽
- 补 §3.27（会话 17 + 复核 15，32 条）后**复验**：605 + 32 = 期望 **637**
  ⇒ **实测 637 passed / 0 failed（1348.48s，exit 0）** ⇒ 自洽
  ⚠️ 同一次跑**不绕过沙箱**时是 **636 passed / 1 failed / 1 error**，
  单独复跑这两条 ⇒ **2 passed** ⇒ 沙箱拒绝 `storage/local` 落盘造成的**假红**。
- 补 §3.28（派单 9 + 分析 11，20 条）后**复验**：637 + 20 = 期望 **657**
  ⇒ **实测 657 passed / 0 failed（1217.85s，exit 0）** ⇒ 自洽
- 补 §3.29（文书 9 + 合规 8，17 条）后**复验**：657 + 17 = 期望 **674**
  ⇒ **实测 674 passed / 0 failed（1130.96s，exit 0）** ⇒ 自洽
- 补 §3.30（任务 11 + 归档 11，22 条）后**复验**：674 + 22 = 期望 **696**
  ⇒ **实测 696 passed / 0 failed（1185.78s，exit 0）** ⇒ 自洽
- 补 §3.31（留存 13 条）后**复验**：696 + 13 = 期望 **709**
  ⇒ **实测 709 passed / 0 failed（1216.49s，exit 0）** ⇒ 自洽
- **探针分布自洽**：**31** 个 `verify_*.py` = `GATED` **10** + `NOT_GATED` **21**
  （本轮 +1：`verify_service_tenant_param.py`，模式横扫棘轮，进 `GATED`）
  （`run_ci_probes.py` 的判据 0 会在两者之和不等于 31 时**直接报错**；
  ⚠️ 新探针必须叫 `verify_*.py`——清单自检用的是这个 glob，否则会被判成
  「清单里有但仓库没有」而直接失败）
  **本轮判据 0 真的咬合了一次**：设计侧新建的 `verify_adjacent_targets.py` /
  `verify_spacing_scale.py` 没登记 ⇒ 门禁**直接失败**。按分档标准处置：
  只读源码的 `spacing_scale` 进 `GATED`（实测 exit 0）；要 CDP + 4 个 dev server 的
  `adjacent_targets` 进 `NOT_GATED`（实测 **exit 1**，是真缺陷，见设计侧待修项）。
  **同一天判据 0 咬合了第二次**：我自己刚写的 `verify_service_tenant_param.py`
  没登记 ⇒ 门禁同样**直接失败**并点名。判据 0 不认人，这一点反而是它的价值——
  它咬的是「新增探针」这个动作本身，不管写的人是谁。
- **门禁耗时自洽**：6 探针 ~79s → 7 探针 ~80s → 8 探针 ~89s → 9 探针 **~90s**
- **自检臂数自洽**：component_wiring 8 → **12**（+Q9–Q12）；token_storage 6（不变，但
  改成了纯夹具）；runner 新增 **4**（S1–S4）；register_abuse_defense **3**（S1–S3）
- **故障注入自洽**：22（前半段）+ 5（棘轮 A/B、过期口径、F4 窗口、自检解耦）
  + 4（Q-K 棘轮 A/B + 2 次判据自纠复验）+ 3（Q-K 修复的**双向**注入 + T8 整机复验）
  + 6（§3.13 双向：越权 5 红 / 功能丢失 1 红；§3.14 三向：太松 7 红 / 太严 5 红 / 无校验 3 红）
  + 1（**「注入后既有套件仍 473 全绿」** ⇒ 证明此前零判据）
  + 4（§3.15 四向：回到原缺陷 10 红 / 焊死 2 红 / 原名落盘 11 红 / 收紧租户判空 1 红）
  + 5（§3.16：无上报义务 4 红 / 一律上报 2 红 / 回到语义错配 1 红 / 假上报 5 红
      + 1 次「注入后既有审核测试仍 37 全绿」的零判据反证）
  + 4（§3.17 四向：写死租户 1 红 / 摘掉一个门 2 红 / 数据边界失效 2 红 / 功能焊死 2 红）
  + 8（§3.18/§3.19 八向：传错租户 1 红 / 落库串号 2 红 / 后台任务串号 1 红
      / 先落盘后鉴权 3 红 / 摘掉归属守卫 3 红 / 回到原缺陷 1 红 / 绕过纵深防御 1 红
      / 焊死 2 红）
  + 1（**「注入 reparse 缺陷后既有套件仍 544 全绿」** ⇒ 第三次零判据实证）
  + 5（§3.21 CSRF 五向：Bearer 不豁免 1 红 / 一律不查 4 红 / 一律都查 2 红
      / 不比内容 1 红 / strict 吃掉方法过滤 1 红）
  + 6（§3.22：未知角色通配 2 红 / 矩阵少角色 1 红 / 生产泄露档位 1 红
      / 永远 healthy 1 红 / 取值域校验消失 1 红 / 跨字段校验消失 1 红）
  + 1（**「CSRF 中间件恒不查后既有 29 条照绿」** ⇒ 第四次零判据实证）
  + 7（§3.25 七向：回到 user 判等 1 红 / 拆掉租户目录约束 1 红 / 留痕不兜 1 红
      / 焊死白名单 1 红 / 拆掉存储根校验 1 红 / 租户不符改 403 1 红 / 不留痕 1 红）
  + 7（§3.26 七向：摘 `_owned` 的 user_id 7 红 / **摘 tenant_id 0 红（是发现不是失效）**
      / 焊死 404 2 红 / read-all 漏用户过滤 3 红 / 未知类型静默放行 1 红
      / 详情改 403 1 红 / 端点改吃请求参数 1 红）
  + 7（§3.27.1 会话七向：列表丢角色过滤 2 红 / **拆租户底线 0 红（补 C4b 后才 1 红）**
      / 判据退回仅校验 tenant 3 红 / 创建吃 payload.tenant_id 2 红 / 发言一律记 CLIENT 1 红
      / channel 退化成 str 1 红 / 详情不传 ctx.user_id 2 红）
  + 9（§3.27.2 复核九向：ensure 查重丢租户 1 红 / 守卫丢租户判等 2 红
      / 留痕丢内联校验 2 红 / 列表丢租户过滤 1 红 / ensure 不校验 target_type 1 红
      / submit 绕过守卫 2 红 / 白名单全放行 1 红 / assert_transition 整机失效 2 红
      / **只拆归档前置条件 0 红（是发现：与白名单完全冗余）**）
  + 7（§3.28.1 派单七向：守卫丢租户判等 3 红 / accept 绕过 2 红 / **grab 绕过 2 红
      （只修 accept 漏 grab 会被抓到）**/ 列表丢租户 1 红 / 池丢租户 1 红
      / 指定规则失效 1 红 / 案件承办人不再落 1 红）
  + 7（§3.28.2 分析七向：`_load_or_404` 丢租户判等 4 红 / `/case/{id}` 丢内联过滤 2 红
      / **`job_handlers` 的守卫失效 1 红（守卫在别的文件）**/ edit 绕过 2 红
      / versions 绕过 2 红 / decisions 绕过 2 红 / `changed_by` 丢失 1 红）
  + 6（§3.29.1 文书六向：守卫丢租户判等 5 红 / collect 绕过 2 红 / render 绕过 2 红
      / 模板丢租户过滤 1 红 / start 写错租户 2 红 / render 不出正文 1 红）
  + 4（§3.29.2 合规四向：列表丢租户 2 红 / 详情丢租户 3 红
      / create 写错租户 1 红 / title 变可选 1 红）
  + 5（§3.30.1 任务五向：`job_status` 丢租户判等 3 红 / retry 丢租户判等 2 红
      / 去掉「仅失败可重试」1 红 / 不入队 1 红 / 先改状态再查 handler 1 红）
  + 6（§3.30.2 归档六向：get_archive 丢租户 2 红 / versions 丢租户 2 红
      / 材料包详情丢租户 3 红 / 归档传死租户 5 红 / **删掉两条审计 1 红**
      / **删掉服务层定稿前置条件 2 红（守卫在另一个文件里，判据照样抓到）**）
  + 6（§3.31 留存六向：拆掉 `ge=180` 下限 3 红 / 摘角色门控 2 红 / 摘急停开关 2 红
      / 默认改成真删 3 红 / 删墓碑审计 1 红
      / **跳过归档行数校验 0 红（判据有洞，补 L8c 后才 1 红）**）
  = **144 次**，全部还原并复跑确认绿
  （⚠️ 其中 **7 次**「还原失败 / 锚点不唯一 / **注入后语法坏**」是本轮工程事故，
  已修注入器并重跑，详见 §3.27.3、§3.28.3 与 §3.30.3）
  （`app/api/v1/files.py` 经 `grep` 确认无 `user.tenant_id` 残留、`tenant_root` 与
  `try/except` 均在位）
  （`app/api/v1/evidence.py` 经 `grep` 确认无 `user.tenant_id` 残留、修复代码在位）
  （`middleware.py` / `schemas/auth.py` 经 `diff` 确认与备份字节一致，相关测试 34 passed）

---

### 3.32 第八批（组二 2.1 收尾）：`billing/*` 3 条 + `complaints/*` 4 条 —— 查出 1 个真实缺陷 + 登记 Q-AA

**2026-09-21 收口。这是「已修复 ≠ 有判据」清扫的最后一块路由覆盖盲区（棘轮 11 → 4）。**

#### 3.32.1 判据（20 条，全绿）

| 文件 | 条数 | 覆盖路由 | 关键反向向量 / 登记型 |
|---|---|---|---|
| `tests/test_billing_endpoint_layer.py` | **9**（M1–M8b） | `dashboard` / `consume` / `project-revenue` | M2「B 看板必须为空」、M6「纯计算不写库（标记周期 `2099-01`）」、M3 门控登记型 |
| `tests/test_complaint_endpoint_layer.py` | **11**（T1–T10） | `policy` / `stats` / `{id}/handle` / `{ticket_no}` | T2 角色门控、T5 泄露、T7 状态机、T8/T9 留痕 |

- **M2 的教训（与 §3.31 L 同款）**：`dashboard` 响应行**不含 `tenant_id` 字段**，
  旧断言用 `{q.get("tenant_id")…}` 恒为 `{None}` ⇒ 结构性假绿。改为「B 从没消费过 ⇒ 看板必须为空；
  一旦端点把租户写死成 A，这里会冒出 A 的额度行」。
- **M6 的教训（防线与数据冗余 ⇒ 判据空转）**：总表行数对比分不清「写到了标记周期」，
  改用**只命中 `project-revenue` 这道防线**的探针（`MARK_PERIOD = "2099-01"`，干净态必不存在）。

#### 3.32.2 🚨 真实缺陷（已修）：`POST /billing/consume` 不存在 member ⇒ 500

- 端点对不存在的 member 抛 `raise NotFoundError(..., code=ErrorCode.NOT_FOUND)`，
  但 `ErrorCode` **根本没有 `NOT_FOUND` 这个成员** ⇒ 构造异常时 `AttributeError` ⇒ 500。
- 修复：`app/api/v1/billing.py:76` 改为 `400 + VALIDATION_ERROR`
  （与 Q-V 整改口径一致）。判据 M4（`test_consume_unknown_usage_type_is_rejected`）坐实 + 固化。

#### 3.32.3 🆕 登记 Q-AA：`billing/project-revenue` 接受负数营收

- 故障注入 `total_cents=-1` 被照收，端点**不校验营收非负**。
- 处置：登记进 `decisions-2026-09-20.md` §3（Q-AA），等合规/产品裁定「拒收 / 校验 / 接受冲正语义」。
- 判据 M7（`test_project_revenue_rejects_negative`，**登记型**）已钉住现状，口径一改必红。

#### 3.32.4 故障注入（10 臂，全红 + 还原干净）

`backend/_inject_billing_complaints.py`（已删）：

| 臂 | 命中判据 | 备注 |
|---|---|---|
| `bilnotenant` | M2 | 写死 `dashboard("tenant-a",` ⇒ B 看板冒出 A 行 |
| `bilbadcode` | M4 | `total_cents` 非法 usage_type |
| `bilwrongtenant` | M5 | `consume` 扣了非调用方租户 |
| `bilprojwrite` | M6 | `project-revenue` 往 `2099-01` 写了库 |
| `bilgate` | M3 | 摘掉 `consume` 门控（登记型） |
| `cmpnorole` | T2 | 摘掉投诉 handle 角色门控 |
| `cmpleak` | T5 | 跨租户读到他租户工单 |
| `cmpnostatus` | T7 | 状态机被绕过 |
| `cmpnonote` | T8 | 删掉处理留痕 |
| `cmpnoaudit` | T9 | 删掉审计 record 调用 |

**注入器自检**：`svc`（`complaint_service.py`）原文**只在模块加载时抓一次**，避免链式注入把「上一条臂的脏状态」当成原文；`_restore()` 重写全部 3 个文件并 `assert` 字节一致；收尾 `grep INJECTED` 无命中。

**棘轮**：零覆盖路由 **11 → 4**（剩 `auth/me` / `qa/stream` / `knowledge/docs`(+`{doc_id}`)）。

---

### 3.33 第九批（组二 2.1 收尾）：`auth/me` + `knowledge/docs`(+`{doc_id}`) + `qa/stream` —— 棘轮 4 → 0

**2026-09-21 收口。路由覆盖盲区彻底清零（棘轮 4 → 0，B 类全程 0）。**

#### 3.33.1 判据（12 条，全绿）

| 文件 | 条数 | 覆盖路由 | 关键反向向量 |
|---|---|---|---|
| `tests/test_auth_endpoint_layer.py` | **3**（A1–A3） | `GET /auth/me` | A1「返回调用方身份（注入返回 max-id 用户 ⇒ 红）」；A2 AST 钉 `get_current_user`；A3 真应用无覆盖 ⇒ 401/403 |
| `tests/test_knowledge_endpoint_layer.py` | **5**（KD1–KD5） | `GET,POST /knowledge/docs` + `DELETE,GET /knowledge/docs/{doc_id}` | KD1「B 列表必须为空」；KD2/KD3 跨租户读/删拒；KD4「创建归属调用方」；KD5 AST 四端点传 `ctx.tenant_id` |
| `tests/test_qa_stream_endpoint_layer.py` | **4**（QS1–QS4） | `POST /qa/stream` | QS2「流走调用方租户（写死 tenant-b ⇒ 红）」；QS3 无令牌 ⇒ 401；QS4 AST |

#### 3.33.2 两个自伤判据的坑（已修）

- **A1**：`me` 把 user brief **直接**作为 `data` 返回（无 `"user"` 包装）⇒ 旧断言 `_data(r).get("user")` 恒 `{}`。改为 `_data(r)`。
- **QS1/QS2**：`TestClient` 对 `StreamingResponse` 路由**不是上下文管理器** ⇒ 去掉 `with`；
  `monkeypatch.setattr(QAService, "stream", f)` 让 `f` 变成类属性 ⇒ 必须收 `self`
  （签名 `def _make_stream(self, question, tenant_id)`）。

#### 3.33.3 故障注入（8 臂，全红 + 还原干净）

`backend/_inject_remaining.py`（已删）：

| 臂 | 命中判据 | 备注 |
|---|---|---|
| `authme_imposter` | A1 | `me` 返回 max-id 用户（非本人） |
| `authme_noauth` | A3(+A2) | 摘掉 `me` 鉴权依赖 |
| `kddrop_tenant` | KD1 | `list_docs` 写死 `tenant-a` |
| `kdget_tenant` | KD2 | `get_doc` 写死 `tenant-a` |
| `kddel_tenant` | KD3 | `delete_doc` 的 `get`+`delete` **两处**都写死（守卫在 `KnowledgeService.delete` 内部用真实 `ctx.tenant_id` 又查了一次，只改前面不够） |
| `kdcreate_tenant` | KD4 | `create_doc` 写死 `tenant-a`（唯一锚点：`create(` 调用段，因 `tenant_id=ctx.tenant_id,` 出现 2 次） |
| `qastream_tenant` | QS2 | `ask_stream` 写死 `tenant-b` |
| `qastream_noauth` | QS3 | 摘掉 `ask_stream` 鉴权依赖 |

**KD4 的假绿排查**：首跑 `kdcreate_tenant` 报 `exit=0`（判据没红）。直接注入重跑 + 读 `KnowledgeDocOut`
确认响应**含 `tenant_id`**、`create` 确实存传入值 ⇒ 判据本应红。根因是**那一轮 `ORIGINALS` 被上一条臂的残留污染**
（知识库当时带着注入残骸启动），重跑前先 `grep INJECTED` 确认干净 ⇒ 重跑 **8/8 全红**。
⇒ 复用注入器前务必先确认「原文」是干净的，否则 `apply` 的锚点计数会被骗。

**棘轮**：零覆盖路由 **4 → 0**。

---

#### 收尾（组二 2.1 全量）

| 层级 | 命令 | 结果 | 耗时 |
|------|------|------|------|
| 静态 | `ruff check`（5 个新文件） | ✅ All checks passed（4 处 import 排序 `--fix`） | — |
| 收尾（§3.32） | `pytest tests/ -q`（+20 计费 M1–M8b + 投诉 T1–T10） | ✅ **729 passed / 0 failed** | 1216s |
| 收尾（§3.33） | `pytest tests/ -q`（+12 鉴权 A1–A3 + 知识 KD1–KD5 + 问答 QS1–QS4） | ✅ **741 passed / 0 failed** | 1371s |
| **最终（本轮末）** | `pytest tests/ -q`（sqlite env） | ✅ **741 passed / 0 failed**，exit 0 | 1371.38s |
| 路由扫描 | `verify_route_coverage.py` | ✅ **端点层零覆盖 0**（棘轮基线 0） | 4.7s |
| 证据门禁 | `run_ci_probes.py` | ✅ **通过 12 / 失败 0 / 跳过 0**，exit 0 | 80s |
| 残留核查 | `grep -rn INJECTED app/ tests/` | ✅ 无命中（注入器已删） | — |

⚠️ 全量回归都带 sqlite env（`DATABASE_URL` + `DATABASE_URL_SYNC`）+ `dangerouslyDisableSandbox` 跑的——
沙箱会拒 `storage/local` 落盘，造成假红（同 §5.1 旧实证）。

#### 故障注入累计（到 §3.33）

§3.24–§3.31 = 144 次；+ §3.32 **10 臂** + §3.33 **8 臂** = **162 次**，全部还原并复跑确认绿。

#### 本轮末文件变更清单（组二 2.1 收口）

| 文件 | 动作 | 说明 |
|---|---|---|
| `backend/tests/test_billing_endpoint_layer.py` | **新增 9 条**（M1–M8b） | §3.32 计费端点层 |
| `backend/tests/test_complaint_endpoint_layer.py` | **新增 11 条**（T1–T10） | §3.32 投诉端点层 |
| `backend/tests/test_auth_endpoint_layer.py` | **新增 3 条**（A1–A3） | §3.33 `auth/me` |
| `backend/tests/test_knowledge_endpoint_layer.py` | **新增 5 条**（KD1–KD5） | §3.33 知识库端点层 |
| `backend/tests/test_qa_stream_endpoint_layer.py` | **新增 4 条**（QS1–QS4） | §3.33 问答流端点层 |
| `backend/app/api/v1/billing.py` | **改产品代码** | §3.32.2：`ErrorCode.NOT_FOUND` ⇒ `VALIDATION_ERROR`（500 缺陷） |
| `backend/archive/retired-security-verifiers/` | **新增目录 + 3 脚本 + README** | §3.32/2.3（Q-D）：`verify_security_fixes*.py` 三件移入作废 |
| `evidence/verify_route_coverage.py` | 基线 11 → 4 → **0** | §3.32 → §3.33 |
| `backend/_inject_billing_complaints.py` · `backend/_inject_remaining.py` · `backend/_dbg_m6.py` | **已删除** | 注入器/调试脚本，证明后即清，不留仓库 |

`ruff` 全绿；故障注入后 `grep -rn "INJECTED" app/ tests/` ⇒ 无残留。
