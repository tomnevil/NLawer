# 端到端业务链路验证报告 · NLawer（律小智）

> 验证时间：2026-09-18 ｜ 后端：uvicorn :8001 / SQLite `backend/storage/nlawer.db`
> 四端前端：:3000(web) :3001(lawyer) :3002(admin) :3003(im) 均连 8001
> 脚本：`C:\Users\RS\AppData\Local\Temp\verify_chain.py`（纯 urllib，禁用代理直连本机）

## 结论

完整链路 **咨询 → 派单 → 律师接单 → L2/L3 复核 → 定稿/归档** 已在真实后端跑通，
全部步骤 HTTP 200，复核状态机（PRD 5.5）行为完全符合预期。

## 逐步结果

| 阶段 | 步骤 | 关键返回 |
|---|---|---|
| A 咨询 | 创建会话 | conv_id=8，HTTP 200 |
| A 咨询 | 客户发消息（触发会话引擎） | HTTP 200，AI 回复带《劳动合同法》第87条等引用，status=`BOT` |
| B 派单 | DESIGNATED → lawyer_li(id=4) | dispatch_id=7，status=`PENDING` |
| B 接单 | accept | status=`ACCEPTED`，异步 job_id=13 |
| C 分析 | 等待六段式分析生成 | analysis_id=7，status=`draft` |
| C 复核 | ensure(required_level=L3) | review_id=6，status=`draft` |
| C 复核 | submit（承办律师） | status=`pending_confirm` |
| C 复核 | **L2 决定**（lawyer_li，L2-only）APPROVED | 仍 `pending_confirm`，satisfied=`L2`（**部分通过**） |
| C 复核 | **L3 终审**（lawyer_wang，L3）APPROVED | `confirmed`，satisfied=`L3` |
| C 定稿 | archive（仅 CONFIRMED 可） | `archived` |

链路核验：**全部通过 ✅**

## 关键发现：一处真实环境缺陷（已修复）

- **根因**：种子开发库 `nlawer.db` 与当前 ORM 模型发生 **schema 漂移**。
  `init_db()` 的 `create_all` 兜底**只创建缺失的表，不补全缺失的列**，
  因此模型后来新增的列在种子库里不存在。
- **漂移清单**（与 `app.models.base.Base.metadata` 比对）：
  - `jobs` 缺 `claimed_by`(VARCHAR128)、`heartbeat_at`(DATETIME)
  - `contract_reviews` 缺 14 列（analysis_status / completion_tokens / cost_cents /
    coverage / disclaimer / duration_ms / error_message / is_mock / model_name /
    model_tier / prompt_tokens / run_id / source / status）
- **症状**：后端日志反复 `僵尸任务巡检异常: (sqlite3.OperationalError) no such column: claimed_by`。
- **影响链路**：律师接单后触发异步案件分析 job，worker 执行 `UPDATE jobs SET status=RUNNING, claimed_by=...`
  因缺列报错 → job 永不完成 → 分析不生成 → 复核阶段直接卡死。**这是会导致链路跑不通的真实缺陷**。
- **修复**：按模型 metadata + SQLite dialect 自动生成 ALTER（幂等、可重跑），
  补齐两表缺失列；`contract_reviews` 的 NOT NULL 列带类型化默认值（字符串 `''`、数值 `0`、布尔 `0`）。
- **验证修复生效**：修复后 accept 触发的 job 13 → `status=COMPLETED`，
  `claimed_by='DESKTOP-K0KGM9F:35016'`（worker 成功认领并执行）。

## 验证脚本最初的三处 bug（不是产品缺陷）

1. `channel:"WEB"` → 应 `"WEB_SIM"`（`ImChannel` 枚举取值为 `WEB_SIM|WECOM|FEISHU`）。
2. `reviews/ensure` 的 `target_type:"case_analysis"` → 应 `"CASE_ANALYSIS"`（`ReviewTargetType` 枚举，大写）。
3. 派单前未筛选状态：原库 **0 条可派单案件**，脚本回退到 ARCHIVED 案件触发 `CASE_INVALID_STATE`。
   已插入演示案件 id=7（`PENDING_DISPATCH`，tenant `firm_hlw`）并改为只选 `INTAKE/PENDING_DISPATCH`。

## 复核 FSM 行为确认（PRD 5.5 · 三级复核）

- `ensure(required_level=L3)` → `draft`
- `submit` → `pending_confirm`
- L2 复核（律师李，`can_l3_review=0`）APPROVED → **仍 `pending_confirm`**（satisfied=L2，等待更高级别）
- L3 终审（王律师，`can_l3_review=1`）APPROVED → `confirmed`（satisfied=L3 满足要求级别）
- `archive` 硬约束：仅 `CONFIRMED` 可归档 → `archived`
- 旁证：`resolve_actor_level` 中 FIRM_ADMIN/PLATFORM_ADMIN 与 `can_l3_review` 律师解析为 L3，
  其余律师为 L2（`lawyer_profiles`：user2/3/5 = L3，user4(李) = L2）。

## 最终 DB 状态（硬证据）

```
jobs 13           : status=COMPLETED, claimed_by='DESKTOP-K0KGM9F:35016', heartbeat_at 已写入
cases 7           : status=CONFIRMED
case_analyses 7   : status=CONFIRMED, confirmed_by=3
reviews 6         : status=ARCHIVED, required_level=L3, satisfied_level=L3, decision=APPROVED
```

## 复现方式

```bash
cd backend
"C:/Users/RS/.workbuddy-ai/binaries/python/envs/nlawer/Scripts/python.exe" \
  "C:/Users/RS/AppData/Local/Temp/verify_chain.py"
```

> 注：演示案件 id=7 为本次验证插入；重复跑前可改 `case_no` 或先 `DELETE FROM cases WHERE case_no='V-DEMO-20260918-001'`。
> 跨端跳转演示：web(3000)/lawyer(3001)/admin(3002)/im(3003) 四个 `next dev` 均直连 8001，可演示端到端导航。
