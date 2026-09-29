# 合同审查异步通道：闭环验证记录（2026-09-23）

> 负责人：方向明（ProductStrategyTeam） · 性质：**验证记录 + 缺陷修复**，不是新决策
> 范围：① lawyer/admin 两端接入 · ② 后端全量回归 · ③ 真链路冒烟

---

## 0. 一句话结论

**异步通道已经真跑通了**（413 → 转异步 → 轮询 → 回读结论，API 层 8/8 全绿）。
过程中揪出**三个**「所有静态/单测/API 防线全绿、却真实伤害用户」的缺陷：

1. `JobSchema` 未暴露 `step_state` ⇒ 前端拿到 `completed` 却取不到产物 id（**最后一米断裂**）；
2. 读回端点**从未被 HTTP 请求过**（跨租户 404 防枚举完全裸奔）；
3. 🚨 **413 在真机上根本不走「决策门」**，而是掉进失败结果页，只给「重试 / 返回修改原文」
   两个**必然再次失败**的出口 —— 这是**真浏览器点一次**才发现的（见 §1.2）。

三个都已修复 + 补判据 + **注入反证**。第 3 个的判据已固化为常驻探针
`evidence/verify_contract_review_gate_e2e.py`（**不再是一次性冒烟**）。

> ⚠️ 一句话教训：**typecheck / lint / 815 条后端测试 / API 冒烟 / 页面 200 全部照绿**，
> 却挡不住「用户点了没反应」这一类缺陷。⇒ 交互类改动必须有**浏览器级**判据。

---

## 1. 缺陷：闭环最后一米断裂

### 现象（真链路冒烟实测）

| 环节 | 实际结果 |
|------|----------|
| worker 执行 | ✅ 跑了，`step_name=review`、`progress=100`、无错误 |
| 落库 | ✅ `jobs.step_state = {"contract_review_id": 2}`（直接查 DB 确认） |
| `GET /jobs/{id}` | ❌ 返回体里**根本没有 `step_state` 字段** |
| 前端 | ❌ 轮询拿到终态却取不到 `contract_review_id` ⇒ 永远进不了回读 |

### 根因

`backend/app/schemas/job.py` 的 `JobSchema` 字段清单里**没有 `step_state`**。
worker 的既有约定恰恰是「把中间产物 id 写回 `step_state`」：

- `case_analysis` → `analysis_id`
- `evidence_parse` → `parsed` / `category`
- `compliance_scan` → `scanned` / `overall_risk`
- `contract_review` → `contract_review_id`（本次新增）

⇒ **同一个缺口对全部既有异步通道都成立**，只是此前没有前端消费方读它，才一直潜伏。

### 修复

`JobSchema` 增加 `step_state: Optional[dict] = None`（只含产物 id / 进度标记，不含正文；
与 `input_payload` 一样受 `ctx.tenant_id` 过滤保护）。纯新增字段，非破坏性。

### 判据 A10（`backend/tests/test_contract_review_async.py`）

```python
def test_job_schema_exposes_step_state():
    assert "step_state" in JobSchema.model_fields, ...
    dumped = JobSchema.model_validate(_FakeJobRow()).model_dump()
    assert dumped["step_state"] == {"contract_review_id": 42}, ...
```

**注入反证**：把字段注释掉 ⇒ `1 failed, 9 passed`；还原 ⇒ `10 passed`。
（不做反证的话，这类「字段存在性」判据极易变成**永久绿**。）

---

## 1.1 顺带补齐的裸奔面：读回端点的**端点层**判据

修完 `step_state` 回头复查时发现一个更要紧的问题：**`GET /contract-review/{review_id}`
从来没有被真正 HTTP 请求过**。已有的 A9 只判「路由挂在 app 上」、A10 只判「schema 有字段」——
两条都是**零件层**，都测不出「归属守卫有没有写」。

这条接口是 **P0 敏感面**：按 id 直接取业务实体（合同原文 + 风险结论），
跨租户必须 **404**（403 会确认资源存在 ⇒ 可被枚举）。

新增 `backend/tests/test_contract_review_readback_endpoint.py`（**7 条，全绿**）：

| 编号 | 性质 | 注入反证 |
|------|------|----------|
| R1 | 同租户 `GET` ⇒ 200，字段对得上 | — |
| R2 | 不存在 id ⇒ 404 `CONTRACT_REVIEW_NOT_FOUND` | — |
| **R3** | **跨租户 ⇒ 404，与「不存在」响应体完全一致**（防枚举） | 去掉归属守卫 ⇒ **R3 转红** ✅ |
| R4 | 非整数 id ⇒ 422（证明真进了端点，不是被路由层挡回） | — |
| R5 | `error_message` **仅** status=failed 时出现 | — |
| **R6** | 超长 ⇒ **413**（非 422）且带 `field`/`max_length`/`guidance` | 摘掉 413 转换 ⇒ 变 422 ⇒ R6 红 |
| **R7** | 异步 ⇒ 202 + `job_id`，且**真的入队** | 删掉 `job_queue.enqueue` ⇒ **接口照返 202、R7 转红** ✅ |

> R7 的反证最有价值：**只判 202 的话，把入队那行删掉接口照样返回 202**，
> 这条判据会**永久绿**而任务永远不执行 —— 正是「假绿」第四类（仪器没判别力）。

## 1.2 🚨 真浏览器端到端：413 **根本不走决策门**（最严重的一个）

### 现象（真实 Chromium，CDP 驱动，账号 `ent_user`）

粘贴 **25000 字**点「开始审查」，页面出现的不是「413 决策门」，而是：

```
本次审查未能完成，未产出任何审查结论
请求失败：文本长度超出上限（source_text 上限 20000 字）。请改用文件上传 + 异步任务…
未计费 / 本次未计费
[重试]  [返回修改原文]
```

⇒ 两个出口都是**死路**：「重试」必然再次 413；「返回修改原文」要用户自己删掉 5000 字。
**B1 决策门（存 .txt 上传 / 选本地文件）用户根本看不到。**

### 根因

`apps/web/app/(app)/contract-review/page.tsx` 的 `run()` catch 对**任何**错误都
`setError(...) + setStage("done")`。413 也被当成「审查失败」⇒ 渲染失败结果页 ⇒
**输入表单连同其中的 `GateBanner` 一起被卸载** ⇒ 决策门永远不可能出现。
（`onContentTooLarge` 其实已经把 `gate` 设上了，只是它所在的子树被卸载了。）

### 为什么此前所有防线全绿

| 防线 | 结果 | 为什么发现不了 |
|------|------|----------------|
| `pnpm typecheck` / lint | ✅ 全绿 | 类型与语法都对 |
| 后端全量回归 | ✅ **815 passed** | 413 语义、异步入队、跨租户 404 全都对 |
| API 层真链路冒烟 | ✅ 8/8 | 走的是 HTTP，看不到前端分支 |
| 页面 HTTP 200 + chunk 内联检查 | ✅ | 页面能出来 ≠ 点下去对 |
| **真实浏览器点一次** | ❌ **红** | 只有它走得到 `setStage("done")` 这条分支 |

### 修复 + 判据 + 反证

`catch` 里先认领 413，留在输入态（原文不动），由 `GateBanner` 给出出口：

```ts
if (isContentTooLarge(e)) { setStage("input"); return; }
```

新增常驻探针 **`evidence/verify_contract_review_gate_e2e.py`**（CDP，判据 C1–C7）：
登录 · 表单渲染 · CountBadge 显示 `25000 / 20000` · **提交后仍在输入态** ·
**决策门两个出口都在** · **原文未被清空** · **转异步后轮询拿到结论页**。

- 修复后：**7/7 通过，exit 0**（结论页真拿到：低风险 / 原文 25000 字 / 覆盖度 已审阅 0/1 条）。
- **注入反证**：把 `isContentTooLarge` 分支注释掉 ⇒ **C4/C5/C6/C7 四条转红，exit 1**；还原 ⇒ 回绿。
- 已登记：`NOT_GATED`（需真浏览器）+ `SELFTEST_ENV_BOUND`（自检要起 Chromium）。

## 2. ② 后端全量回归

```
807 passed, 0 failed   (1274.85s / 21m14s)
```

- **第一轮**：**807 passed / 0 failed**。基线 **798** ⇒ **+9** 正好等于新增的 A1–A9 ⇒ **零既有测试被打破**。
- **第二轮**（补完 A10 与端点层 R1–R7 之后再跑一次锁定最终态）：**815 passed / 0 failed**。
  807 + 1(A10) + 7(R1–R7) = **815**，数字完全对得上 ⇒ 仍然**零破坏**。
- `ruff check backend/app backend/tests`：**All checks passed**（顺带修掉新文件的一处 import 排序）。
- 另有 `test_job_endpoint_layer.py` **13 passed** —— 专门复跑，确认新增字段没打坏任务端点层。

---

## 3. ③ 真链路冒烟

后端 `uvicorn` 起在 8001（SQLite `storage/nlawer.db`，种子数据已灌：3 租户 / 9 账号）。

| # | 环节 | 结果 |
|---|------|------|
| 1 | 种子账号登录取 token | ✅ `lawyer_wang` / `LAWYER` / `firm_hlw` |
| 2 | `GET /auth/me` 校验身份 | ✅ 200 |
| 3a | 构造 25,000 字文本（同步上限 20,000） | ✅ |
| 3b | **同步提交超长文本** | ✅ **413** `code=CONTENT_TOO_LARGE` `field=source_text` `max_length=20000` `guidance=upload_or_async` |
| 4 | 短文本同步提交 | ✅ 200（状态 `degraded`，见下方说明） |
| 5 | **异步提交** | ✅ **202** `job_id=3` |
| 6 | **轮询任务** | ✅ 第 2 次即 `completed` / `progress=100` / `step_state={'contract_review_id': 4}` |
| 7 | **回读结论**（新增 `GET /contract-review/{id}`） | ✅ 200 `id=4` |
| 8 | 任务列表 `GET /api/v1/jobs` | ✅ 200（任务中心页面数据源） |

**失败数：0**。

> ⚠️ 审查结果 `status=degraded` 是**预期**的：后端未配置 LLM（`llm_providers` 全 `false`），
> 走降级路径。**不是缺陷**，不要当缺陷报。

### 前端（三端 dev server 冒烟）

| 端 | 页面 | HTTP | 标题 | 编译/运行错误标记 |
|----|------|------|------|------------------|
| web 3000 | `/contract-review` | 200 | 法务助手 · 律小智 | 无 |
| lawyer 3001 | `/jobs` | 200 | 律师工作台 · 律小智 | 无 |
| admin 3002 | `/jobs` | 200 | 律所运营后台 · 律小智 | 无 |

另做了**防静默缺陷**的专项检查：抽取三端页面引用的 JS chunk，确认
`NEXT_PUBLIC_API_BASE` 内联的是 **8001**（三端均 PASS；正确产物**同时**也含
兜底 8000，这是正常的，不要据此判红）。

---

## 4. 前一轮的「诚实边界」——**已作废，特此更正**

上一版这里写的是「**本环境（Windows）无浏览器自动化能力**，浏览器级交互未做真机验证」。
**这个结论是错的**，已在 2026-09-24 更正：

- 项目本身就有 CDP 工具链（`evidence/cdp.py` + `Browser`），本机
  `%LOCALAPPDATA%\ms-playwright\chromium-1234\chrome-win64\chrome.exe`（Chrome 151）就在，
  **CDP 就是 WebSocket + JSON，无需装 playwright 包**（`design.md` 早已写过）。
- 我把「agent-browser skill 在 Windows 不可用」误当成了「本机不能做浏览器验证」——
  前者是**工具**不可用，后者是**能力**不具备，**两件事**。

⇒ 浏览器级验证**已真实做过**（§1.2），并已固化为常驻探针。

### 现在仍**没有**覆盖的部分

1. 全局 413 **Toast**（`ContentTooLargeGate` 在其它页面触发时的提示）未见实测；
   任务中心 `TaskCenterDrawer` 的**打开/重试**交互、lawyer/admin `/jobs` 页的轮询与重试
   **也还没有**浏览器级判据（只有 web 的合同审查链路有）。
2. 三端之外的 **im** 端未做浏览器验证。
3. 回读结论的**内容质量**不在范围（LLM 未配置，结果本就是降级占位）。
4. ⚠️ **环境地雷**：`tests/test_audit_log.py` / `test_job_claim.py` / `test_vector_retrieval.py`
   用**全局 `engine`**（默认 `./storage/nlawer.db`）并 `drop_all` ⇒ **从仓库根跑一次全量 pytest
   会清空开发库**（本轮实测：种子数据被清、登录全部失败）。详见 §5。

---

## 5. 方法论沉淀

- **「单测全绿」不等于「链路通」**：本轮 9 条判据全绿、worker 也确实写对了库，
  但响应模型漏一个字段就让整条链路在最后一米断掉。⇒ 接线类改动**必须做一次真链路冒烟**，
  不能只靠单测收口。
- 判据要判到**客户端真正消费的那个字段**，而不是「服务写对了」。
- 已记入 `methodology.md` **#206**（原 `#184`）。
- **「typecheck + 815 条测试 + API 冒烟 + 页面 200」全绿，挡不住「点下去不对」**
  （§1.2 的 413 决策门）。⇒ 交互类改动必须留**浏览器级**判据，且**固化成探针**——
  一次性手工冒烟跑完就删，等于把这个缺陷原样留给下一次（我这轮就重复犯了两次）。
  已记入 `methodology.md` **#208**（原 `#186`）。
- **改完仪器要回头修描述**：§4 的「本环境无浏览器自动化」写完当天就被自己推翻，
  若不回头改，后面的人会按错的边界继续规划。**描述过期 = 系统性误导**。

---

## 6. 待办（未变）

- I2 / I3 仍**被 A 组裁定阻塞**，未开工。
- 浏览器级交互验证（见 §4）待人工执行。
