# 裁定 R-2026-10-16：跨租户/跨用户「对象详情」一律返回 404（非 403）

> 裁定编号：R-2026-10-16
> 类别：安全 / 授权语义（enumeration resistance）
> 状态：✅ 已裁定（行为符合设计，无需改代码）
> 触发：P0 待拍板项「客户乙读客户甲 ⇒ 404（非 403）」
> 关联：P2-10（2026-09-29 产品决策）、NR-07、loop-closure R3、roadmap 待拍板「收口」项

---

## 一、结论（先行）

**「客户乙读客户甲（或跨租户）的对象详情端点返回 404 而非 403」是有意的安全设计，不是缺陷。无需任何代码改动。**

- 返回 **404** 是为了**枚举防护（IDOR 防御）**：攻击者无法用「404 vs 403」的差异推断目标资源是否存在。
- 返回 **403** 只用于**能力/角色门禁**（你这个角色/权限根本不允许使用此功能或集合），语义是「无权限」而非「不存在」。
- 当前后端所有「按 ID 取详情」的资源端点均已落地该约定，行为**一致**，无跨租户数据泄露。

唯一遗留是一项**文档瑕疵**（非行为缺陷）：`reviews.py` L42 注释写「越权时按 404 处理…而非 403」，但该函数的 CLIENT 角色门禁（L45）确实返回 403——属能力门禁，正确。建议把注释改成「**归属/租户越权**按 404，能力/角色越权按 403」，避免后人误读为 bug（见 §六）。

---

## 二、背景与触发

P0 待拍板项记录了「客户乙读客户甲 ⇒ 404（非 403）」这一观感，需要书面裁定其是否为缺陷。

经核对后端授权层及历史产品决策，该语义是一条**贯穿多轮迭代的既定安全原则**：

- `roadmap-update-pending-rulings-2026-09-22.md:88`：「🔴 跨客户（analyses/case/{id} · archives/cases/{id} · … · dispatches · dispatches/pool）｜6｜**收口**｜客户乙读客户甲案情 ⇒ **404**（非 403，防枚举）」。
- `loop-closure-verification-2026-09-23.md:75`：「跨租户必须 **404**（403 会确认资源存在 ⇒ 可被枚举）」；R3 验收「跨租户 ⇒ 404，与『不存在』响应体完全一致」。
- `prd-notification-readpath-2026-09-16.md:NR-07`：单条读他人/跨租户通知 ⇒ **404（非 403）**，并给出 IDOR 经典探测论证（通知含案件标题/客户信息，泄露存在性即信息泄露）。
- `backend/app/api/v1/files.py:75` 注释显式写明：「路径中的 `tenant_id` **必须等于当前请求的租户上下文**……否则直接 404（不返回 403，避免泄露文件是否存在）」。

故本裁定是对既有原则的一次**代码级复核与书面固化**，结论是「已符合设计」。

---

## 三、调研证据：全资源端点审计

状态码映射（来自 `backend/app/core/errors.py`）：
`NotFoundError` → **404**；`PermissionDeniedError` / `TenantDeniedError` → **403**。

| 模块 | 详情/按-ID 守卫 | 跨租户/非归属返回 | 列表/角色/能力门禁返回 |
|---|---|---|---|
| `cases.py` | `_case_or_404` (L72–80)：`tenant_id` 不符、CLIENT 非本人、LAWYER 无团队授权 ⇒ `NotFoundError` | **404** ✅ | `list_cases` (L107) 律师查他人无授权 ⇒ `PermissionDeniedError` **403** ✅ |
| `reviews.py` | `_review_or_404` (L62–63) + `_assert_review_access` (L50)：非承办/非管理员 ⇒ `NotFoundError` | **404** ✅ | L45 客户角色、L95/L104/L109/L114 角色/能力门禁 ⇒ `PermissionDeniedError` **403** ✅ |
| `documents.py` | `_document_or_404` (L58)、合同审查 (L271)：`tenant_id` 不符 ⇒ `NotFoundError` | **404** ✅ | — |
| `evidence.py` | (L45) 案件 `tenant_id` 不符、(L180) `ev.tenant_id` 不符 ⇒ `NotFoundError` | **404** ✅ | — |
| `archives.py` | (L46/L58/L103)：`tenant_id` 不符 ⇒ `NotFoundError` | **404** ✅ | — |
| `analyses.py` | `_load_or_404` (L27)：`tenant_id` 不符 ⇒ `NotFoundError` | **404** ✅ | — |
| `dispatches.py` | `_dispatch_or_404` (L39)：`tenant_id` 不符 ⇒ `NotFoundError` | **404** ✅ | — |
| `jobs.py` | (L65/L79)：`job.tenant_id != ctx.tenant_id` ⇒ `NotFoundError` | **404** ✅ | — |
| `notifications.py` | (L178/L197)：(tenant_id,user_id) 双过滤 ⇒ `NotFoundError` | **404** ✅（用户级资源，同档） | — |
| `files.py` | (L88)：`ctx.tenant_id != tenant_id` ⇒ `NotFoundError`（注释明示防枚举） | **404** ✅ | L54/L58 非法路径 ⇒ `PermissionDeniedError` **403** ✅ |
| `knowledge.py` | `KnowledgeService.get(doc_id, ctx.tenant_id)`（服务层租户过滤） | **404** ✅ | — |
| `conversations.py` | 归属判据单一来源 `conversation_access`（L90/L118） | **404** ✅ | — |

**结论**：所有按-ID 取详情的端点均对跨租户/非归属返回 **404**；所有 **403** 均落在「角色/能力/集合可见性/路径合法性」门禁，无一例把「对象归属失败」误报成 403。行为完全一致，无跨租户泄露。

---

## 四、设计原则界定（固化，供后续评审引用）

```
对象级详情 / 按 ID 取资源：
  请求者身份合法（已登录、令牌有效）但「这个具体对象不归你/不属你租户」
    ⇒ NotFoundError ⇒ HTTP 404
    ⇒ 响应体与「对象真的不存在」完全一致 ⇒ 防枚举（IDOR 防御）

集合级 / 角色 / 能力门禁：
  请求者角色或权限根本不允许使用此端点或查看此集合
    ⇒ PermissionDeniedError / TenantDeniedError ⇒ HTTP 403
    ⇒ 语义是「无权限」，不承载任何对象存在性信息
```

两条线互补、不冲突：
- 详情 404 = 「在你的世界里这个 ID 不存在」（对象级枚举防护）。
- 列表/角色 403 = 「你没有看这个集合的资格」（集合级能力门禁）。
- 同一资源在详情走 404、在列表走 403 属**正确且标准**的分裂，不是不一致。

---

## 五、一致性确认

- 历史缺陷已收口：`reviews.py`（`edit/decide/archive/void` 曾仅 `get_current_user` 无租户校验，任意登录用户可篡改他人租户复核）、`evidence.py`（曾仅校验租户、同租户可遍历他人 `evidence_id`）、`documents.py`（`DocumentService._get` 不带租户过滤）均已补端点层 `(id, tenant_id)` 或 `(tenant_id, user_id)` 双重守卫。
- 平台管理员跨租户（complaints / audit_retention / `X-Tenant-Id` 切换）属设计内行为，不在本裁定范围。

---

## 六、一处文档瑕疵（非行为缺陷，建议性）

`backend/app/api/v1/reviews.py` L42 注释：

> 「越权时按 404 处理（不向越权者泄露任务存在），而非 403。」

而同函数 L45 对 `ctx.role == Role.CLIENT` 返回 `PermissionDeniedError`（403）。这本是**正确的能力门禁**（客户角色被禁止进入复核工作流，列表端 L95 同样 403 客户），但注释的「而非 403」易被后人误读成「此处 L45 是 bug」。

**建议**（可选，不影响行为）：将 L42 注释收紧为——

> 「**归属/租户越权**时按 404 处理（不泄露任务存在，防枚举）；**能力/角色越权**（如客户角色）按 403，属功能不可用语义。」

---

## 七、后续动作

| 项 | 内容 | 优先级 | 处置 |
|---|---|---|---|
| 1 | 裁定落地（本文件） | — | ✅ 完成 |
| 2 | `reviews.py` L42 注释措辞收紧 | 低（文档） | 建议；不阻塞，可随下次触碰该文件顺手改 |
| 3 | 回归探针 `verify_cross_tenant_detail_404`（断言各详情端点在跨租户/非归属时返回 404、且不泄露存在性） | 中（P3） | **可选**：现有 `loop-closure` R3 + 各端 `_*_or_404` 测试已覆盖该语义；若引入新资源端点，建议复用本裁定原则并补对应归属守卫测试，不必新建通用探针 |

**判定**：本次 P0 项为「书面裁定」，结论为符合设计、无需改码。不自动新建回归探针（避免与既有 `loop-closure` 测试重复），仅将原则固化供后续评审引用。

---

## 八、引用

- `backend/app/core/errors.py`（状态码映射）
- `backend/app/api/v1/{cases,reviews,documents,evidence,archives,analyses,dispatches,jobs,notifications,files,knowledge,conversations}.py`（端点层归属守卫）
- `roadmap-update-pending-rulings-2026-09-22.md:88`（跨客户 404 收口项）
- `loop-closure-verification-2026-09-23.md:75, R3`
- `prd-notification-readpath-2026-09-16.md:NR-07`（IDOR 404 论证）
- `deliverables/ui-design/ui-optimization-stage-summary-2026-10-15.md`（P0 待拍板来源）
