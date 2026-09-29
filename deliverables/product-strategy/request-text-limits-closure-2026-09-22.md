# 请求体自由文本上限：I1 收口（2026-09-22）

**日期**：2026-09-22
**类型**：门禁建设 + 迭代 I1 落地
**前置**：`request-text-limits-gate-2026-09-22.md`（建尺子）· `roadmap-update-pending-rulings-2026-09-22.md`（裁定表与路线图）
**范围**：只做路线图里的 **I1 · 铺路**（文本上限补齐）。I2/I3 仍待 A 组拍板，未动。

---

## 📌 TL;DR（执行摘要）

- **收口结果：无上限自由文本 20 条 → 2 条**（剩的两条是已裁定永久豁免的短字段）。
- 但**真正值钱的不是收口，是扩面**：建尺子时那把尺子只量到 16 条，收口过程中发现它**漏了两整类**——
  ① 内部辅助函数被当成端点（响应体假红）② `app/api/` 里定义的 7 个请求体模型**完全没扫**。
  修好后立刻多出 **4 条真欠账**，其中包括 **`QARequest.question`（问答主入口，直接喂模型）**。
- **静态尺子不够**：它只能证明源码里写了 `max_length`，改注释就能骗过；
  故补了 **38 条运行时判据**（超长必拒 + 正好等于上限必放行 + 禁止静默截断）。
- 两处仪器失真全部用**注入反证**（摘掉判据 ⇒ 转红；还原 ⇒ 回绿），并新增自检臂 Q9–Q11 锁住。
- **413 语义收口**（I1 尾巴）：`string_too_long` 从默认 422 升 **413**（`CONTENT_TOO_LARGE` + `guidance: upload_or_async` + 明说「不做截断」），
  其余 `ValidationError` 仍 422；用 **4 条判据**锁住，其中「注册本身」也成了判据（防有人删 `add_exception_handler` 静默退回 422）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|---|---|
| 收口前后 | 无上限自由文本 **20 → 2**（14 条已登记 + 4 条扩面后新增，全部补上限；2 条豁免） |
| 产品代码改动 | 9 个 schema/API 文件，18 个字段加 `max_length` |
| 新增判据 | 静态：自检 8 → **11 臂**；运行时：`tests/test_request_text_limits.py` **38 条** |
| 验证 | 全量回归 **798 passed / 0 failed**（基线 756 → 798，+42）；CI 门禁 阶段 2 **18/18** · 阶段 3 **7/7**；`ruff check app` 全绿 |
| 遗留 | 仅 I1 设计稿 B1 决策门 / B2 长任务进度条**待设计**；413 语义已收口（见 §5） |

---

## 1. 两处仪器失真（本轮最值钱的发现）

### 1.1 失真 ④ —— 内部辅助函数被当成端点

`request_models()` 首版遍历 `app/api/**` 里**所有函数**的入参注解，于是
`auth.py:52` 的 `async def _issue_and_store(response, user, brief: UserBrief, *, svc)`
被当成端点 ⇒ `UserBrief`（`TokenResponse.user`，**纯响应体**）进了请求体模型清单
⇒ 2 条假红：`UserBrief.full_name` / `UserBrief.tenant_name`。

⚠️ **假红的危害方向很坏**：裁定表会照着它建议「给这两个字段加 `max_length`」，而它们是**响应**字段，
加了会**截断前端显示的名字**——拿合规的名义做坏功能。

**改法**：端点 = 带 HTTP 方法装饰器的函数。实测全库 `add_api_route` **零命中** ⇒ 装饰器判定可靠；
若将来改用 `add_api_route`，判据 A 的 `MIN_MODELS` 守卫会因模型数突降而报警。

### 1.2 失真 ⑤ —— 请求体模型不一定住在 `app/schemas/`

`scan()` 首版只扫 `app/schemas/*.py`，而项目把 **7 个请求体模型直接定义在 API 文件里**：

| 文件 | 模型 |
|---|---|
| `app/api/v1/audit_retention.py` | `PurgeRequest` · `ArchiveRequest` |
| `app/api/v1/complaints.py` | `ComplaintCreate` · `ComplaintHandle` |
| `app/api/v1/documents.py` | `StartRequest` · `CollectRequest` |
| `app/api/v1/qa.py` | `QARequest` |

⇒ 4 条真自由文本**全在尺子外面**，且每一条都比已登记 16 条里大半更重要：

| 字段 | 为什么重要 |
|---|---|
| `QARequest.question` | **问答主入口**，直接喂模型 |
| `ComplaintCreate.description` | 上限只写在服务层 `complaint_service.py:59`，**探针看不见** |
| `ComplaintCreate.contact` | 完全无上限 |
| `ComplaintHandle.handle_note` | 完全无上限 |

⇒ **扩面后欠账 16 → 20**，收口后 **20 → 2**。

> **教训**：「0 欠账」的置信度取决于**扫描范围**，不只是判据写得好不好。
> 仪器量不到的地方，会被读成安全。

---

## 2. 落地取值（18 个字段）

取值来源：`roadmap-update-pending-rulings-2026-09-22.md` §4.1 裁定表；
后 4 条为扩面后新暴露，沿用既有锚点（5000 服务层上限 / 100 `full_name` / 2000 `input_summary`）。

| 字段 | 上限 | 依据 |
|---|---|---|
| `ContractReviewRequest.source_text` | **20 000** | 唯一无截断成本口（`contract_review.py:809` 全量入 prompt） |
| `ContractReviewRequest.title` | 100 | 对齐 `full_name=100` |
| `KnowledgeDocCreate.content` | 50 000 | chunk 400 字 ⇒ 约 125 段，可分批导入 |
| `KnowledgeDocCreate.title` | 100 | 同上 |
| `AnalysisUpdate.legal_analysis` | 20 000 | 长推理，最宽档 |
| `AnalysisUpdate.summary` | 2 000 | 对齐入库侧 `[:2000]` |
| `AnalysisUpdate.change_note` | 500 | 改稿说明 |
| `IterateRequest.note` | 2 000 | 批注驱动迭代 |
| `ComplianceScanCreate.input_summary` | 2 000 | 对齐既有 `[:2000]` |
| `ComplianceScanCreate.scope` | 500 | 扫描范围 |
| `ComplianceScanCreate.title` | 100 | 同上（**两个文件各一份定义，已同步**） |
| `SendMessageRequest.text` | 4 000 | 短输入极少触顶 |
| `ReviewAction.comment` | 1 000 | 复核留痕 |
| `QuotaAdjust.reason` | 1 000 | 额度调整理由 |
| `QARequest.question` | 4 000 | 🆕 问答主入口 |
| `ComplaintCreate.description` | 5 000 | 🆕 从服务层**上移**到 schema |
| `ComplaintCreate.contact` | 100 | 🆕 对齐 `full_name=100` |
| `ComplaintHandle.handle_note` | 2 000 | 🆕 对齐 `input_summary` |
| `KnowledgeDocCreate.source_ref` / `.tags` | **不设** | 已裁定豁免（A4）：差 3–4 个数量级，占输入 <0.1% |

⚠️ **超限行为**：pydantic 默认抛 `ValidationError` ⇒ HTTP **422**。
裁定表要求的是 **413 + 转上传指引**；**413 语义本轮已收口**（见 §5）：新增
`app/main.py` 模块级处理器 `_too_long_to_413`，把 `RequestValidationError` 里的
`string_too_long` 从默认 422 提升为 **413**（`HTTP_413_CONTENT_TOO_LARGE`），
返回 `guidance: "upload_or_async"` + 明确「**系统会按文档/页数处理，不做截断**」；
缺字段 / 类型错等**其余错误仍走 422**（委托 FastAPI 默认处理器），以免丢掉更该红的信号。
**「禁止静默截断」**因此成立 —— 截断是最不能接受的失败模式，pydantic 不截断、413 文案也明说。

---

## 3. 两半判据：静态 + 运行时

| | 静态探针 `verify_request_text_limits.py` | 运行时 `tests/test_request_text_limits.py` |
|---|---|---|
| 证明什么 | 源码里**写了** `max_length` | **真的会拒**（且正好等于上限会放行） |
| 能被什么骗过 | 改注释/改字符串 | —— |
| 判据数 | 自检 **11 臂** + 棘轮（OPEN ⊆ KNOWN） | **38 条** |

运行时判据的三条反向向量：

1. **超长必拒**（18 条）；
2. **正好等于上限必放行**（18 条）—— 否则把上限写小一半也测不出来（正常输入被误杀）；
3. **豁免字段仍无上限**（`source_ref` / `tags` 5 000 字通过）—— 防顺手加限误杀长 URL/长标签；
4. **禁止静默截断**：`source_text` 20 001 字必拒、20 000 字原样返回。

> 🚨 反向向量当场抓到 **2 条空转的臂**：`ComplianceScanCreate.scope` / `.input_summary`
> 的构造里漏了必填 `title` ⇒ 缺字段本身就抛错 ⇒ **就算把 `max_length` 摘掉也照样红（假绿）**。
> 已修（补 `title`）。

---

## 4. 注入反证（每处都做了「坏 ⇒ 红」+ 还原回绿）

| # | 注入 | 结果 |
|---|---|---|
| 1 | 恢复「所有函数都算端点」（`INJECT_ALL_FUNCS=1`） | 自检 **Q9 转红**（`UserBrief` 被采集）；主扫描点名 2 条假红 |
| 2 | 摘掉 `IterateRequest.note` 的 `max_length` | 棘轮转红并点名 `IterateRequest.note` |
| 3 | 摘掉 `QARequest.question` 的 `max_length` | 运行时判据 `test_oversized_text_is_rejected[QARequest.question]` 转红 |

三处均已还原，复跑回绿；`grep -rn INJECT` 无残留。

自检还**顺手抓到一个真 bug**：`_model_files()` 没按路径去重 ⇒ 两个根目录重叠时文件被扫两次
（模型数 3 → 6）。已修（`dict.fromkeys`）。

---

## 5. 413 语义收口（I1 尾巴，本轮完成）

### 5.1 为什么是 413 不是 422

- **422 的缺陷**：「请求体格式不可处理」。它把「文本超长」和「缺必填字段」「类型错」混在一起，
  前端无法区分「是不是该改成文件上传」，只能笼统报「参数错误」—— 丢了裁定表要的「**转上传**」信号。
- **403 的缺陷**：会确认资源存在（破坏防枚举）；这里不是权限问题，不取。
- **413 的语义**：「**内容过长**（Content Too Large）」。正好对应「你这段塞不进同步接口，请改文件上传 + 异步」。
  `StarletteDeprecationWarning` 提示 `HTTP_413_REQUEST_ENTITY_TOO_LARGE` 已废弃 ⇒ 使用 `HTTP_413_CONTENT_TOO_LARGE`。

### 5.2 实现要点（`app/main.py`）

- 处理器**放在模块级**（`_too_long_to_413`），只为让测试能 `import` 它；
  `create_app()` 里只做 `app.add_exception_handler(RequestValidationError, _too_long_to_413)` 注册。
- 只把 `string_too_long` 升 413；其余 `ValidationError` 委托 `await request_validation_exception_handler(...)` 走默认 422。
- 返回结构（便于前端区分）：

  ```json
  {
    "success": false,
    "error": {
      "code": "CONTENT_TOO_LARGE",
      "message": "文本长度超出上限（question 上限 4000 字）。请改用文件上传 + 异步任务，系统会按文档/页数处理，不做截断。",
      "field": "question",
      "max_length": 4000,
      "guidance": "upload_or_async"
    }
  }
  ```

### 5.3 判据（4 条，`tests/test_text_too_long_413.py`）

| 判据 | 验证什么 |
|---|---|
| `test_string_too_long_returns_413_with_guidance` | `string_too_long` ⇒ 413 + `CONTENT_TOO_LARGE` + `guidance: upload_or_async` + 文案含「上传」「截断」 |
| `test_other_validation_error_still_422` | 非超长（缺字段/类型错）⇒ 仍 422，信号不被吞 |
| `test_mixed_error_prefers_413_when_too_long_present` | 混合错误里只要有 `string_too_long` 就优先 413 |
| `test_handler_is_registered_on_app` | 🚨 **注册本身也是判据**：对函数本体的单测测不出「有人删了 `add_exception_handler`」——删了函数还在、单测还绿，但生产静默退回 422。这条断言 `app.exception_handlers[RequestValidationError] is _too_long_to_413`。 |

### 5.4 注入反证

- 注释掉 `create_app()` 里的注册行（注入为 `# INJECTED_FOR_PROOF: app.add_exception_handler(...)`）
  ⇒ `test_handler_is_registered_on_app` **FAILED**（证明「注册」这条判据真的有判别力）；还原后回绿。
- 中间件合规性：`ErrorHandlerMiddleware` 只拦截 `call_next` 抛出的**异常**；我们的处理器返回的是**正常 413 响应**（不抛异常），不会被吞。

## ✅ 行动清单

| # | 动作 | 状态 |
|---|---|---|
| 1 | 9 个文件 18 个字段补 `max_length` | ✅ 完成 |
| 2 | 修两处仪器失真 + 自检 8 → 11 臂 | ✅ 完成 |
| 3 | 补 38 条运行时判据 | ✅ 完成 |
| 4 | 三处注入反证 + 还原 | ✅ 完成 |
| 5 | **413 语义**（`string_too_long` ⇒ 413 + 转上传指引，其余仍 422） | ✅ 完成（`app/main.py` `_too_long_to_413` + `tests/test_text_too_long_413.py` 4 条） |
| 6 | 设计稿 B1 决策门 / B2 长任务进度条 | ⬜ 待设计（I1 剩余） |
| 7 | I2 显式共享 / I3 收口 + 灰度 | ⬜ 待 A 组拍板 |

---

## ⚠️ 待确认 / 假设 / Non-goals

- **待确认（产品）**：413 文案的「转上传」引导已锁为 `guidance: "upload_or_async"` + 明确「按文档/页数处理、不做截断」。
  **未带 `Retry-After`**：语义是「换上传方式」而非「稍后重试」，不适用；若前端要做限流提示再议。
- **假设**：4 条结构性短字段永久豁免；新增字段仍按「量级差 3 个数量级以上」判断。
- **假设**：`ComplaintCreate.description` 服务层的 5000 判断保留（双保险），未删除。
- **Non-goals**：本轮不改 I2/I3；不动 2 条豁免字段；不改前端（413 已收口，见 §5）。

---

## 📊 验证结果

- 静态：`请求体模型 23 个 · str 字段 42 个 · 无上限自由文本 2 个`（全部在豁免清单内）
- 自检：**11 臂全绿**
- 运行时：`tests/test_request_text_limits.py` **38 passed**
- 413 语义：`tests/test_text_too_long_413.py` **4 passed**（升 413 / 其余仍 422 / 混合优先 413 / 注册锁）
- CI 门禁：`[阶段 2] 通过 18 · 失败 0` · `[阶段 3] 通过 7 · 失败 0`
- `ruff check app`：**All checks passed**
- 全量回归（含 413 收口，**798 passed / 0 failed**，1212.46s）⇒ 基线 **756 → 798**，总增量 **+42**（38 运行时 + 4 413），**无既有用例被破坏**。
