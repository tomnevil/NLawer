# 第十五轮·收尾二（2026-09-20）：三条「零判据」核心边界

> 概览。逐条证据、注入明细、数字自洽见 `round15b-fixed-but-unguarded-sweep-2026-09-20.md` §3.13 / §3.14 / §3.15。

## TL;DR

用「已修复 ≠ 有判据」这把尺子继续扫，抓出**三条此前从未被测试执行过的核心边界**，
并在补判据的过程中又发现**四处静默失败**（都不报错，比抛异常难查）。

| | 边界 | 此前判据 | 本轮 | 顺带发现 |
|---|---|---|---|---|
| ① | `X-Tenant-Id` 租户切换 | **0**（真实逻辑被 4 个文件 stub 掉） | +9 条 | 🐞 纯空白头部 ⇒ 租户切成 `"   "`，全表查不到数据**且不报错** |
| ② | `require_permissions` 路由级权限门控 | **0**（唯一使用点 `billing.py` 无人测） | +21 条 | 🐞 空清单 ⇒ `all([]) is True` ⇒ **端点被公开**<br>🐞 `mode` 拼错 ⇒ **静默降级成 any** |
| ③ | 上传落盘 `save_upload` | **0**（`test_evidence_authz.py` 只测读端点） | +26 条 | 🐞 **`tenant_id` 未净化即拼路径 ⇒ 静默目录穿越**（实测 4 种输入逃出存储根，含 1 种跨盘符） |
| ④ | 合规上报（第十四条） | **0**（37 条审核判据全无感） | +9 条 | 🐞 上报失败被错分成「通道未配置」⇒ 运维做错处置、statutory 上报被延误 |
| ⑤ | 计费端点**数据边界**（Q-O） | **0**（`/api/v1/billing*` 从未被请求） | +6 条 | ⚠️ 派生 **Q-P**：`billing:read`（读）放行 `POST /consume`（写） |
| ⑥ | 证据**写**端点（上传 / 重解析） | **0**（`test_evidence_authz.py` 只测 GET） | +13 条 | 🐞 `POST /evidence/{id}/parse` **无客户归属校验** ⇒ 同租户可读他人 `ocr_text` **并覆盖解析结果**<br>🐞 服务层自带的 `tenant_id` 纵深防御端点没用上 |
| ⑦ | CSRF **中间件整机** | **0**（10 条令牌原语；它保护的两个端点从未被请求） | +7 条 | — |
| ⑧ | `permissions_for` / `/api/health` / 审核后端配置 | **0**（health 被请求过但**响应内容零断言**） | +9 条 | — |
| ⑨ | **全量路由 × 端点层请求次数** | **79 条路由里 55 条从未被请求** | 扫描器 + 棘轮 | ⚠️ A 类 48 条是「零件测过、装配没测」的系统性复现 |
| ⑩ | 卷宗下载端点 `GET /files/{tenant}/{subdir}/{filename}` | **0**（全站唯一直吐磁盘文件的接口） | +20 条 | 🐞 用 `user.tenant_id` 而非 `ctx.tenant_id` ⇒ **平台管理员切换租户后 404**<br>🐞 只校验「在存储根内」⇒ `%2e%2e` 能读到**存储根**下不属于任何租户的文件<br>🐞 留痕抛错 ⇒ 下载 **500**（与自身 docstring 承诺不符） |

## 最重要的那一个数

把 `require_permissions` 的门控分支改成**恒放行**，跑**排除本轮新文件**的既有测试套件：

```
473 passed / 0 failed（969.01s，exit 0）
```

**一条都没红。** 这不是推断——这是「该门控在全量回归里从未被执行过」的直接证据。
绿不等于有判据，这是本轮反复验证的那件事。

## 改动

**产品代码**（`backend/app/core/deps.py`，2 处）

1. `get_tenant_context`：`(request.headers.get("X-Tenant-Id") or "").strip() or None`
   —— `"   "` 是 truthy，原写法挡不住。
2. `require_permissions` / `require_roles`：参数在**工厂阶段**（模块导入时）校验，
   空清单和非法 `mode` 直接 `ValueError` ⇒ **启动时就炸**，不等线上被扫。

3. `storage_service.save_upload`：新增 `_resolve_under_root()` —— 落盘前把路径解析成
   绝对路径并验证仍在存储根内，**不同盘符一律按逃逸处理**。

**判据**（`backend/tests/`，3 个新文件，56 条）

- `test_tenant_context_guard.py`（X1–X5，9 条）：角色**参数化 4 个**，含 HTTP 层。
- `test_require_permissions_guard.py`（P1–P9 / R1–R3 / W1，21 条）：
  all/any 双 mode、超管通配（反向）、**HTTP 层成对**（无权限 403 / 有权限 200）。
- `test_storage_path_containment.py`（S1–S6 / A1–A3，26 条）：
  `_safe_name` 用**形状白名单**、路径包含性、落盘反向量、`assert_tenant` 含登记型。

**故障注入**：本轮 +39 次（租户双向、门控三向、落盘四向、上报四向、计费四向、证据八向、
CSRF 五向、角色/健康/配置六向、四次零判据反证），累计 **73 次**，
全部还原后复跑确认绿，`ruff check app/ tests/` 全绿，临时脚本已清理。

**判据自己被抓到两次**（都写进了 `methodology.md`）：

1. **U4 的夹具是废的**：注入「端点把 `user.tenant_id` 传给后台任务」⇒ **13 条全绿**。
   根因不在产品代码，而在**夹具**——所有测试用户 `user.tenant_id` 恒等于 `ctx.tenant_id`，
   两个恒等的量之间**任何判据都测不出差异**。补了平台管理员
   （自身租户 `platform`，靠 `X-Tenant-Id` 切到目标租户）后才转红。
2. **§3.16 的 R4 留了个没用到的 `caplog`**：平时"通过"只是因为 logging 插件在，
   关掉插件就 `fixture 'caplog' not found` ⇒ error。已删。
3. **注入锚点不唯一**：`main.py` 有 2 处 `if settings.ENVIRONMENT == "production":`，
   `replace(...,1)` 打到了 CORS 守卫 ⇒ **注入没生效**，跑出「9 passed」差点被读成
   「判据抓不到」。⇒ 锚点带相邻注释使其唯一，每臂后 `grep -n INJECTED` 确认行号。
4. **多文件注入没逐臂还原**：脚本按原文件重写**单个**文件 ⇒ 只改 A 的臂不会清掉
   上一臂留在 B 的注入，6 臂里 4 臂结果被污染。⇒ 循环体必须是「注入 → 跑 → 还原」。

### 12 个候选已全部裁定

`_check_builtin` / `_check_external` 是**名字扫描的假阳性**（`test_moderation.py`
通过 `ContentModerator(backend="builtin")` 已覆盖）——
⇒ 函数名零引用只能用来**找候选**，结论必须靠注入或行为核查（`methodology.md` 73）。

## 验证

| 项 | 结果 |
|---|---|
| 全量回归 | ✅ **573 passed / 0 failed**（880.88s）= 473 + 9 + 21 + 26 + 9 + 6 + 13 + 16 |
| 注入 permissive 后跑既有套件 | 🚨 **473 passed / 0 failed** ⇒ 权限门控此前零判据（实证） |
| 注入「无上报义务」后跑既有审核测试 | 🚨 **37 passed / 0 failed** ⇒ 上报链路此前零判据（实证） |
| 注入「reparse 无归属校验」后跑既有套件 | 🚨 **544 passed / 0 failed** ⇒ 证据写端点此前零判据（实证） |
| `_needs_check` 恒 False 后跑既有 3 个 CSRF 相关文件 | 🚨 **29 passed / 0 failed** ⇒ CSRF 整机此前零判据（实证） |
| `ruff check app/ tests/` | ✅ All checks passed |

## 踩到的两个环境坑（与产品无关，但会误导判断）

1. **「函数名的测试引用数」有假阳性**：扫「守卫类函数 + tests 零引用」命中 12 个，
   其中 `_assert_forwarded_allow_ips_safe` **其实已覆盖**（测试没写函数名，靠 import 触发）。
   ⇒ 只能用来找候选，结论必须靠注入验证。
2. **沙箱下 pytest 的 `tmp_path` 不可用**：`PermissionError: [WinError 5] 拒绝访问`
   （系统临时目录不可写），12 个用例 error ⇒ 改用仓库内 `_tmp_tests/`。

## 需要你知道 / 拍板

- ~~**Q-O**~~ ✅ **已收口**（§3.17）：补了 6 条数据边界判据。
- **Q-P（新增，🟠 P1）**：`POST /billing/consume`（**写**）由 `billing:read`（**读**）放行 ——
  `ROLE_PERMISSIONS` 里**没有 `billing:write`**。建议 ① 新增 `billing:write`
  并只给 FIRM_ADMIN / ENTERPRISE_ADMIN；或 ③ 明确接受现状并写进权限矩阵说明。
  这是「补完门里的东西才看见门本身的问题」的自然结果。
- ~~**证据上传端点 0 覆盖**~~ ✅ **已收口**（§3.18）：补 8 条端点层判据，
  含「拒绝后磁盘不留文件」与「调用顺序」两条此前没人想到的。
- **Q-R（新增，🟠 P1）**：证据端点**完全没有权限码门控**（router 无 `dependencies=`、
  无 `require_permissions`），与 `billing.py` 四个路由全挂门口径不一致。
  归属校验挡得住「别人的案件」，挡不住「本租户内谁都能上传/重解析」。
  建议 ① 引入 `evidence:read` / `evidence:write` 并接入 `ROLE_PERMISSIONS`；
  或 ② 明确接受「证据按案件归属授权」并写进权限矩阵说明。
- **W1（登记，不修）**：`deps.require_roles` 与 `rbac.require_roles` **同名不同义**
  （一个返回 `TenantContext`、一个返回 `User`），两者都在用。已核查 `knowledge.py`
  自己声明了租户上下文 ⇒ **不是漏洞**，仅可读性隐患；登记为的是将来合并时强制回头确认。
- **部署提醒（沿用 Q-K）**：网关不在本机 ⇒ 把网关地址加进 `TRUSTED_PROXIES`；
  生产**绝不能**设 `FORWARDED_ALLOW_IPS=*`。
- **Q-S（新增，🟠 P1）**：卷宗下载**要不要按案件归属授权**？现状只做租户级校验
  ⇒ 同租户客户甲知道文件名即可下载客户乙的卷宗（与 §3.19 同形状）。
  建议 ① 改（与 reparse 口径一致）；若接受现状，请与 Q-R 一起写进权限矩阵。
- **Q-T（新增，🟡 P2）**：零覆盖路由的清理节奏。
  建议 ① 逐轮清 3–5 条（棘轮已保证不涨），从 `notifications/*`、`conversations/*` 开始。
  **已推进到第三批**：55 → 49（通知 6 条）→ **39**（会话 3 + 复核 7）。
  ⚠️ 这一批的价值不在「查出缺陷」（两模块均为**无缺陷**），而在查出
  **两处「防线冗余导致判据空转」**——详见 §3.27。
- **Q-U（新增，🟡 P2）**：会话创建时 `client_user_id` / `bind_lawyer_id` **可被客户伪造**。
  无数据泄露（伪造者自己读不到），但**归属污染**且事后不可追溯。
  建议 ① 限制为所内人员才能指定。
- **Q-V（新增，🟡 P2）**：复核两处**错误语义错配**——`ensure` 非法 `target_type`
  返回 404「复核任务不存在」；`decide` 未知结论的 code 是 `REVIEW_ALREADY_DECIDED`。
  建议 ① 改 422 + 新增 `REVIEW_INVALID_PARAM`。本轮判据**未断言**具体状态码，
  以免把错误语义焊死、挡住后续修正。
- **部署提醒（§3.21 新增）**：`CSRF_STRICT_ALL_WRITES` 默认 `False` ⇒
  目前只有 refresh / logout 两个端点受 CSRF 保护，其余写端点靠
  「Bearer 免疫」这条前提成立。若将来把 **access token 也改存 Cookie**，
  这条前提立刻失效，必须同时打开该开关（C7 已把默认配置钉住，不会静默变空）。
