# 裁定材料：`POST /reviews/ensure` 的承办人指派

> 状态：**已按选项 A 落地**（2026-10-12 拍板）。发现于「修 12 条既有测试失败」过程中。
> 触发链已实证；用户已裁定采纳 **A**，实现见文末「六、落地记录」。

---

## 一、缺陷（一句话）

`POST /api/v1/reviews/ensure` 在**不传 `assignee_id`** 时不给复核指派承办人 ⇒
复核落进「待分配池」⇒ 而 `cf281be` 加的**复核可见性门禁**只放行
「承办人本人 / 管理员 / （池 + 调度资格）」。

于是**同一个律师**刚建完复核就**读不到、也提交不了**它：

```
POST /reviews/ensure            → 200（复核建成，assignee_id = NULL）
POST /reviews/{id}/submit       → 404 REVIEW_NOT_FOUND   ← 门禁拒了
```

## 二、证据

- **实证**（修前）：`tests/test_review_endpoint_layer.py::test_zero_coverage_routes_all_respond`
  里 `_ensure(c, 8001)` 后 `GET /reviews/{rid}/records` 得
  `404 {"code":"REVIEW_NOT_FOUND","message":"复核任务不存在"}`。
  该用例的调用者是**普通律师**（`Role.LAWYER`，`can_l3_review=False`）。
- **前端确实走这条路**：`frontend/apps/lawyer/app/(app)/cases/[id]/page.tsx:612`
  调 `/api/v1/reviews/ensure` 时**只传** `target_type / target_id / case_id / required_level`，
  紧接着 `:621` 调 `/{r.id}/submit`。
- **谁能触发**：`can_l3_review=False` 的律师（演示账号 `lawyer_li`）。
  种子案件的复核**都带承办人**（`li`/`zhao`/`wang`/`admin`）⇒ 演示主路径不触发，
  但「给一个还没有复核的案件**新生成分析 → 提交复核**」必触发。
- **与设计文档冲突**：`docs/design/consultation-agent-review-flow.md` §3.4 明确
  「草稿创建 `Review` 后，复用 `DispatchService` 派单（指定律师优先；否则自动派给
  `can_l3_review` 律师终审）」—— 即**复核应当有承办人**，而不是裸进池。

## 三、为什么必须拍板（三个修法各有代价）

| 选项 | 做法 | 优点 | 代价 / 风险 |
|---|---|---|---|
| **A** | 后端：`ensure` 对**律师**调用者默认 `assignee_id = ctx.user_id`（显式传入时不覆盖） | 前端零改动即恢复；语义是「谁发起谁承办」；与 §3.4「应有承办人」一致 | 改**产品行为**；需确认「发起人 = 承办人」是否总成立（若复核本意是**交给别人**做，A 会把复核锁在自己名下，L3 也看不见） |
| **B** | 前端：`ensure` 补传 `assignee_id` | 后端零改动；指派来源显式 | 前端要自己决定「派给谁」——普通律师未必知道该派给谁；离线补做（`SYNC_SECTION.reviews`）也要跟着改 |
| **C** | 后端：门禁放行「复核的创建者」 | 最贴近直觉（我建的我能看） | `Review` **没有 `created_by` 字段**（只有 `assignee_id`，`_record(... actor_id=assignee_id)`）⇒ 要加列 + 迁移 + 回填；成本最高 |

## 四、推荐

**A**（后端默认指派给律师调用者），理由：
1. 前端是**同一个人**「建 → 交」，指派给自己是唯一自洽的解释；
2. 与 §3.4 的「复核应当有承办人」一致（B 把决策推给前端，C 要加列）；
3. 显式传入 `assignee_id` 时**不覆盖** ⇒ 系统派单（`consult_review.py` 走的就是显式）完全不受影响；
4. 客户（`CLIENT`）调用**不**默认指派 —— 客户不参复核，其发起的复核仍进池待律师认领。

⚠️ 若选 A，须连带：
- 补一条判据钉住「律师 `ensure` 后能 `submit`」与「客户 `ensure` 仍进池」两条**反向臂**；
- 复核 `test_list_is_tenant_scoped_both_directions` 等夹具（本次已把夹具补成「带承办人」的真实形态）。

## 五、本轮**不**做的

- 不改 `_assert_review_access`（门禁本身是 `cf281be` 的**有意设计**，属共享口径）；
- 不动 `ReviewService.ensure` 的签名（只改端点层的**默认值**，服务层保持显式）；
- 已修的 12 条测试**不改回**：它们现在反映的是「**显式指派**」的正确用法（即 B 的前半段），
  与选 A 不冲突（A 只是让不传时也有默认值）。

## 六、落地记录（选项 A）

**改动点**：`backend/app/api/v1/reviews.py::ensure_review`

1. **默认指派**：`assignee_id` 缺省且调用者是律师时，取 `ctx.user_id`：
   ```python
   effective_assignee = assignee_id
   if effective_assignee is None and ctx.role == Role.LAWYER:
       effective_assignee = ctx.user_id
   ```
   显式传入时不覆盖 ⇒ 系统派单（`consult_review.py`）与既有显式调用路径**完全不受影响**。
2. **认领池内旧复核**：若 `ensure` 幂等命中的是一条**已存在的无承办人**复核（历史上落进池的），
   且本次调用者是律师 ⇒ 一并认领给自己（`claimed_from_pool=True` 记进审计 `detail`）。
   否则「先被客户/旧代码建进池 → 律师再 ensure」仍是 404，等于没修。
3. **审计**：`REVIEW_CREATE` 的 `detail` 增记 `assignee_id` 与 `claimed_from_pool`。
4. **边界**：客户（`CLIENT`）调用**不**默认指派 ⇒ 客户发起的复核仍进池待律师认领（与第四节第 4 条一致）。

**判据**（`backend/tests/test_review_endpoint_layer.py`，R13 三条臂）：

| 用例 | 断言 |
|---|---|
| `test_lawyer_ensure_without_assignee_is_self_assigned` | 律师不传 `assignee_id` ⇒ `ensure` 后 `assignee_id == 本人`，且**紧随的 `submit` 必须 200**（正面钉住缺陷链） |
| `test_lawyer_ensure_claims_unassigned_existing_review` | 先由客户把复核建进池（`assignee_id=NULL`）⇒ 律师 `ensure` 后认领给自己 |
| `test_client_ensure_still_lands_in_pool` | **反向臂**：客户 `ensure` 仍 `assignee_id=NULL`（不许把 A 做成「人人自派」） |

**注入反证**（已执行并恢复）：摘掉 `effective_assignee = ctx.user_id` 三行 ⇒
R13 / R13b **判红**、R13c（客户）**仍绿** ⇒ 证明三条臂确实在测 A，而非恒绿。

