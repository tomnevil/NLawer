# 第六轮 · 前端令牌安全 + CSRF 防护 —— 交付总览

**日期**：2026-09-13
**类型**：安全修复交付总览
**涉及 P0**：P0-7（令牌存放）、P0-11（CSRF 缺失）
**参与**：方向明（主理人）· 产品战略团队

---

## 📌 TL;DR（执行摘要）

- **核心目标**：把 XSS 的爆炸半径从「7 天完整会话被盗」压缩到「30 分钟、单标签页」。
- **关键决策**：access 存**内存**（30 分钟）而非 localStorage；refresh 存 **HttpOnly Cookie**（7 天）；新增**签名式双提交 CSRF**（会话绑定 + TTL 1 小时）；生产环境未开 `AUTH_COOKIE_SECURE` **启动即失败**。
- **关键发现**：批量改写 13 个页面守卫后 `tsc` 报出 **11 个编译错误**（孤儿 `else` + 调用未定义函数）——静态 grep 完全看不出来。另修掉一个 SDK 逻辑缺陷：401 自动刷新重试会把「登出」变成「复活」。
- **下一步**：内容安全审核（P0-13）→ IM 真实打通（P0-9）→ 前端注册/支付（P0-12）。

---

## 🎯 核心结论卡片

| 项目 | 内容 |
|------|------|
| 推荐方案 | 分层令牌：内存 access（30min）+ HttpOnly refresh Cookie（7d）+ 签名双提交 CSRF |
| 优先级 | **P0**（已完成） |
| 预期影响 | XSS 窃取后果降级约 **336 倍**（7 天 → 30 分钟）；写接口具备 CSRF 防护 |
| 资源需求 | 后端 1 人 × 1 天；前端 1 人 × 1 天（含 4 应用类型检查回归） |
| 风险等级 | **低**（已 5 层验证；遗留 3 项已知权衡，均有缓解路径） |

---

## 1. 修复前 / 修复后对照

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| access 存放 | `localStorage`（**JS 可读**） | **内存变量** |
| access 有效期 | **1440 分钟（24 小时）** | **30 分钟** |
| refresh 存放 | `localStorage`（**JS 可读**） | **HttpOnly Cookie**（JS 读不到） |
| refresh 有效期 | 7 天 | 7 天（但不可窃取） |
| CSRF 防护 | **无** | 签名双提交 + 会话绑定 + TTL 3600s |
| 生产配置守卫 | 无 | `SECURE=false` → **启动即抛错** |
| XSS 最坏后果 | 7 天完整会话，改密码也未必失效 | 当前标签页 30 分钟内被借用，刷新即失效 |

**量化**：有效期内可滥用时长从 **10,080 分钟** 降至 **30 分钟**，收敛 **336 倍**。

---

## 2. 后端改动清单

| 文件 | 类型 | 内容 |
|------|------|------|
| `app/config.py` | 修改 | `ACCESS_TOKEN_EXPIRE_MINUTES` 1440→30；新增 8 个 Cookie/CSRF 配置项；新增生产 fail-fast 校验器 |
| `app/core/csrf.py` | **新增** | HMAC-SHA256 签名双提交令牌：`nonce.ts.session_id.sig`，TTL 3600s，会话绑定，恒定时间比较 |
| `app/core/auth_cookies.py` | **新增** | `set_refresh_cookie`（HttpOnly）/ `set_csrf_cookie`（非 HttpOnly）/ `clear_auth_cookies` |
| `app/middleware.py` | 修改 | 新增 `CsrfMiddleware`；Bearer 请求豁免 |
| `app/api/v1/auth.py` | 重写 | `_issue_and_store()` 统一签发；`refresh` 优先读 Cookie；新增 `logout` 端点（清 Cookie + 审计） |
| `app/main.py` | 修改 | 注册 `CsrfMiddleware` |
| `.env` / `.env.example` | 修改 | 同步配置（**注意**：本地 `.env` 会覆盖 `config.py` 默认值，这是首轮 E2E 误报 1440 的原因） |

---

## 3. 前端改动清单

| 文件 | 内容 |
|------|------|
| `packages/sdk/src/index.ts` | 重写令牌策略；`credentials:"include"`；CSRF 自动回填；401 静默刷新重试；新增 `login()` / `logout()` / `restoreSession()`；新增 `_noRetry` |
| `packages/ui/src/components/LoginShell.tsx` | 改走 `login()`（**第 5 条登录路径，首轮遗漏**） |
| `apps/{web,lawyer,admin,im}/app/login/page.tsx` | 改走 `login()` |
| 13 个页面守卫 | `if (!tokenStore.get())` → `restoreSession().then(...)` |
| 2 个登出按钮 + 1 个 401 处理器 | 改走 `logout()` |
| `apps/web/app/qa/page.tsx` SSE | 去掉显式传 token，改由 SDK 内部读取 |

---

## 4. 验证结果（五层）

| 层 | 手段 | 结果 |
|----|------|------|
| 1 编译 | `compileall` + `ast.parse` | ✅ |
| 2 静态不变量 | `verify_p0_7_frontend.py` | ✅ **25 / 25** |
| 3 单元 | `pytest tests/test_csrf.py` | ✅ **12 / 12** |
| 4 真实请求 | `verify_p0_7_csrf.py`（TestClient + Cookie 罐） | ✅ **32 / 32** |
| 5 类型检查 | 4 应用 + ui + sdk 全量 `tsc --noEmit` | ✅ **全绿** |
| 回归 | `pytest tests/` | ✅ **123 / 123** |
| 历史脚本 | 8 个 `verify_*.py` 全部重跑 | ✅ 15+8+17+23+39+12+46+20 = **180** 项全通过 |

---

## 5. 本轮抓到的缺陷（价值最高部分）

### 5.1 编译器抓到 11 个「肉眼没问题」的错误

批量改写 13 个页面守卫后，`tsc` 报出：

```tsx
// 事故形态 A：孤儿 else（TS1128）
restoreSession().then((ok) => {
  if (!ok) router.push("/login");
  else load();
});
else refresh();      // ← 脚本替换留下的孤儿

// 事故形态 B：调用不存在的函数（TS2304）
restoreSession().then((ok) => {
  if (!ok) router.push("/login");
  else load();       // ← 该页根本没有 load()，真实名字是 refresh()
});
```

影响 11 个文件，分布在 4 个应用。**静态 grep 与代码评审都发现不了**。
已固化为 `C.3`（孤儿 else）/ `C.4`（未定义函数调用）两条静态断言。

### 5.2 SDK 逻辑缺陷：登出形同虚设

`request()` 的 401 自动刷新重试没有例外通道：

```
用户点「退出」→ logout 请求 → access 已过期 → 401
  → 自动静默刷新 → 拿到全新 access → 重试 logout 成功
  → 会话被"复活"，用户仍是登录态
```

两个单独看都正确的机制（自动刷新 + 登出）组合起来产生错误行为。
**只有阅读控制流才能发现**。已加 `_noRetry` 标记修复。

### 5.3 遗漏的第五条登录路径

首轮只改了 4 个 `apps/*/app/login/page.tsx`，漏了共享组件
`packages/ui/src/components/LoginShell.tsx`——它仍在走
`http.post + tokenStore.set`。由全量 grep 发现并修复。

---

## ✅ 行动清单

| # | 行动 | 负责方 | 时间窗 |
|---|------|--------|--------|
| 1 | 内容安全审核（P0-13，算法备案前置） | 后端 + 法务 | 第 1 周 |
| 2 | IM 真实打通（P0-9，产品线 A 核心假设） | 后端 | 第 2–3 周 |
| 3 | 前端注册 / 支付（P0-12） | 前端 | 第 5–7 周 |
| 4 | 移动端 Bearer body 通道（按 UA 分流 + 设备指纹） | 前端 + 后端 | 上线前 |
| 5 | `SECRET_KEY` 双密钥平滑过渡（消除滚动发布会掉线） | DevOps | 上线前 |
| 6 | `usage_quotas` 原子扣减（复用第三轮 `claim_job` 写法） | 后端 | 第 2 周 |
| 7 | 审计日志保留期与轮转（等保 ≥6 个月） | 后端 + DevOps | 第 3 周 |

---

## ⚠️ 待确认 / 已知权衡 / Non-goals

### 已知权衡（本轮引入）

1. **移动端需保留 Bearer body 通道** —— 部分 App 内嵌 WebView 对第三方 Cookie 支持受限。建议按 `User-Agent` 分流：WebView 允许正文传 refresh，但强制绑定设备指纹 + 缩短有效期。
2. **滚动发布会导致会话失效** —— `SECRET_KEY` 轮换或跨版本时，HMAC 签名的 CSRF 令牌会失配，用户需重新登录。当前用户量下可接受；量产后需做**双密钥平滑过渡**（新旧密钥同时验证一个周期）。
3. **`CSRF_STRICT_ALL_WRITES` 仍为 `false`** —— 当前只保护 `/auth/refresh` 与 `/auth/logout`。其他写接口依赖 Bearer 天然免疫（浏览器不会自动附加 `Authorization` 头）。**若后续放开 Cookie 直连，必须打开此开关**。

### Non-goals（本轮明确不做）

- 不实现 OAuth2 / SSO（留给 P1）
- 不实现令牌撤销黑名单（当前靠 7 天 TTL + 登出清 Cookie；如需即时踢人，需引入 Redis 黑名单）
- 不改 `usage_quotas` 原子性（独立 P0，下一轮）

---

## 📚 数据来源 & 成员产出索引

- **验证脚本**：`backend/verify_p0_7_csrf.py`（32 项，真实 HTTP）、`backend/verify_p0_7_frontend.py`（25 项，静态不变量）
- **测试**：`backend/tests/test_csrf.py`（12 项）
- **完整记录**：`code-review-optimization-2026-09-12.md` § 修复记录（第六轮）
- **PRD 增补**：`reference/律小智AI法律助手PRD_v2.0.md` § 增补 A-1 第六轮
- **总报告**：`overview.md`（P0 清零进度 15/16；就绪度 42 → 约 62）

---

> 本报告由产品战略团队 AI 协作生成，重要决策请由产品负责人审定。
