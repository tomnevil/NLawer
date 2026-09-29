"""平台管理员「租户视角」（`X-Tenant-Id`）端到端验证。

## 为什么需要这个脚本

前端本轮给 SDK 接上了 `X-Tenant-Id` 回填（`packages/sdk/src/index.ts::tenantScope`），
但当时只证明了「后端存在这条通道」（读代码），**没有证明「通道真的通」**。
本脚本补上这一段：走完整的 HTTP 栈（鉴权 → `get_tenant_context` → 路由 → DB）。

## ⚠️ 本脚本第一次运行时推翻了一个错误结论

初版断言 A 写的是「admin 默认视角业务案件数为 **0**」——**实测是 4，断言失败**。
原因是当初只 `grep -n tenant_id app/seed/business.py | head -8` 就下了结论，
漏掉了文件后半段的 `_seed_platform()`（`business.py:243`），它的注释写得很清楚：

    「平台（platform）演示数据集：让默认登录的"平台管理员"也能看到非空驾驶舱。」

也就是说：**有人早就处理过「平台管理员驾驶舱全零」这个问题**，做法是
给 `platform` 租户造 4 条 `PLT-DEMO-*` 演示案件。

### 于是真实情况是（比原来判断的更微妙）

| | 事实 |
|---|---|
| ❌ 原判断 | 驾驶舱全零，因为看错了域 |
| ✅ 实际 | 驾驶舱**非零**，但看到的是 **4 条平台演示案件**，**不是真实业务数据** |
| ✅ 仍成立 | `firm_hlw`（真实业务，10 条）与 `platform`（演示，4 条）是两个域，**默认视角看不到后者** |

**切换租户视角的价值因此从「从零到有」修正为「从演示数据到真实数据」**，
这依然是必要的——但**界面上绝不能再说「这里是 0」**。

**教训**：`head -N` 截断 `grep` 输出后下的结论不算结论。

## 验证什么

| # | 断言 | 为什么重要 |
|---|---|---|
| A | 默认视角返回的行**全部属于 `platform`** 且案号带 `PLT-DEMO` 前缀 | 证明默认看到的是**演示数据**，不是真实业务 |
| B | 带 `X-Tenant-Id: firm_hlw` 后返回的行**全部属于 `firm_hlw`** | 证明切换通道**真的生效**且数据被正确隔离 |
| C | 带 `X-Tenant-Id: ent_acme` 得到的是**第三个**租户的数据 | 证明切换是**定向**的，不是碰巧放开了全部 |
| D | **非平台管理员**带同一个头 → **完全无效** | 🚨 安全边界：一个能切租户的头若对谁都生效，就是完整越权漏洞 |
| E | 投诉举报对平台管理员是**跨租户全局**的 | 证明「投诉举报页在默认视角下就有真实数据」 |
| F | 非平台管理员访问投诉管理端点 → 403 | 证明 E 的全局性来自**角色**，不是「谁都能看」 |

运行：
    cd backend
    python verify_admin_tenant_scope.py
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

# 独立测试库：用 uuid 命名，避免删除文件（删除会触发沙箱守卫）
DB = f"./verify_tscope_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ.setdefault("MODERATION_ENABLED", "true")
# 关掉 SQLAlchemy 回显：本脚本要的是断言结果，不是 SQL 日志
os.environ["SQL_ECHO"] = "false"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

client = TestClient(create_app())

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail[:240]))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


# ═══════════════ 准备：建库 + 灌种子 ═══════════════

import seed_demo  # noqa: E402


def _seed() -> None:
    """复用项目自己的种子脚本，保证与真实开发环境同构。"""
    asyncio.run(seed_demo.main(reset=False))


_seed()


async def _insert_foreign_complaint() -> str:
    """直接写一条 `firm_hlw` 的投诉工单。

    为什么要绕过 HTTP 写入：公开投诉入口 `POST /complaints` 没有鉴权依赖，
    `request.state.user` 取不到用户，`tenant_id` 一律落到 `"platform"`
    （见 `app/api/v1/complaints.py:84`）。也就是说**通过公开入口无法造出
    一个「属于其它租户」的工单**。而 E 要验证的正是「管理员能否看到
    非自己租户的工单」，因此只能在数据层直接构造。
    """
    from app.database import async_session_factory
    from app.models.complaint import Complaint, new_ticket_no

    async with async_session_factory() as session:
        row = Complaint(
            tenant_id="firm_hlw",  # ← 关键：刻意不属于 admin 的 platform
            ticket_no=new_ticket_no(),
            type="ILLEGAL_CONTENT",
            status="PENDING",
            description="验证用：一条属于 firm_hlw 的投诉工单，用于检验平台侧是否跨租户可见。",
        )
        session.add(row)
        await session.commit()
        return row.ticket_no


foreign_ticket = asyncio.run(_insert_foreign_complaint())


# ═══════════════ 辅助 ═══════════════


def login(username: str, password: str) -> str | None:
    resp = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    if resp.status_code != 200:
        return None
    return (resp.json().get("data") or {}).get("access_token")


def cases_page(token: str, tenant_header: str | None = None, size: int = 50):
    """返回 (total, rows, 说明)。`GET /cases` 是 `Page`，`total` 在顶层。"""
    headers = {"Authorization": f"Bearer {token}"}
    if tenant_header:
        headers["X-Tenant-Id"] = tenant_header
    resp = client.get("/api/v1/cases", headers=headers, params={"page_size": size})
    if resp.status_code != 200:
        return None, [], f"HTTP {resp.status_code} {resp.text[:100]}"
    data = resp.json().get("data") or {}
    rows = data.get("items") or []
    return data.get("total"), rows, f"total={data.get('total')} rows={len(rows)}"


def tenants_of(rows) -> set[str]:
    return {r.get("tenant_id") for r in rows}


def all_case_no_prefixed(rows, prefix: str) -> bool:
    return bool(rows) and all(str(r.get("case_no", "")).startswith(prefix) for r in rows)


admin_token = login("admin", "Admin@12345")
firm_token = login("firm_admin", "Firm@12345")

if not admin_token or not firm_token:
    print("登录失败，无法继续：请确认 seed 中 admin / firm_admin 的密码未变")
    raise SystemExit(1)


# ═══════════════ A. 默认视角看到的是「平台演示数据」而非真实业务 ═══════════════

a_total, a_rows, a_detail = cases_page(admin_token)
a_tenants = tenants_of(a_rows)

check(
    "A.1 admin 默认视角能看到案件（非零——修正了初版「恒为 0」的错误判断）",
    a_total is not None and a_total > 0,
    f"{a_detail}",
)
check(
    "A.2 默认视角返回的行全部属于 platform 租户",
    a_tenants == {"platform"},
    f"tenant_id 集合 = {a_tenants or '空'}",
)
check(
    "A.3 默认视角的案号全部带 PLT-DEMO 前缀（是演示数据，不是真实业务）",
    all_case_no_prefixed(a_rows, "PLT-DEMO"),
    f"案号样本 = {[r.get('case_no') for r in a_rows[:3]]}",
)


# ═══════════════ B. 切换到真实业务租户 ═══════════════

b_total, b_rows, b_detail = cases_page(admin_token, "firm_hlw")
b_tenants = tenants_of(b_rows)

check(
    "B.1 admin 带 X-Tenant-Id: firm_hlw 看到案件（>0）",
    b_total is not None and b_total > 0,
    f"{b_detail} —— 切换通道确实生效",
)
check(
    "B.2 切换后返回的行全部属于 firm_hlw（租户隔离未被破坏）",
    b_tenants == {"firm_hlw"},
    f"tenant_id 集合 = {b_tenants or '空'}",
)
check(
    "B.3 切换后拿到的是真实业务案件（案号不含 PLT-DEMO）",
    not all_case_no_prefixed(b_rows, "PLT-DEMO"),
    f"案号样本 = {[r.get('case_no') for r in b_rows[:3]]}",
)
check(
    "B.4 默认视角与切换后的数据量不同（证明「切换确实换了域」）",
    a_total != b_total,
    f"platform={a_total} vs firm_hlw={b_total}",
)


# ═══════════════ C. 切换是定向的 ═══════════════

c_total, c_rows, c_detail = cases_page(admin_token, "ent_acme")
c_tenants = tenants_of(c_rows)

check(
    "C. 带 X-Tenant-Id: ent_acme 得到的是第三个租户的数据（定向切换）",
    c_tenants <= {"ent_acme"} and c_total != b_total,
    f"{c_detail}（firm_hlw={b_total} vs ent_acme={c_total}）tenant_id 集合={c_tenants or '空'}",
)


# ═══════════════ D. 🚨 安全边界：非平台管理员带该头必须无效 ═══════════════

d_base, d_rows, d_base_detail = cases_page(firm_token)
d_hdr, _, d_hdr_detail = cases_page(firm_token, "ent_acme")
d_hdr2, _, d_hdr2_detail = cases_page(firm_token, "platform")

check(
    "D.1 非平台管理员带 X-Tenant-Id: ent_acme → 头部被忽略，数量与租户都不变",
    d_base is not None and d_base == d_hdr and tenants_of(d_rows) == {"firm_hlw"},
    f"不带头 {d_base_detail} / 带 ent_acme {d_hdr_detail}",
)
check(
    "D.2 非平台管理员带 X-Tenant-Id: platform → 头部被忽略（拿不到 platform 的数据）",
    d_base is not None and d_base == d_hdr2,
    f"不带头 {d_base_detail} / 带 platform {d_hdr2_detail}",
)
check(
    "D.3 对照组：非平台管理员确实有数据可看（否则 D.1/D.2 是空断言）",
    d_base is not None and d_base > 0,
    f"{d_base_detail} —— 有数据才有说服力",
)


# ═══════════════ E. 投诉举报是跨租户全局的 ═══════════════

e_stats = client.get(
    "/api/v1/complaints/stats", headers={"Authorization": f"Bearer {admin_token}"}
)
e_ok = e_stats.status_code == 200
e_data = (e_stats.json().get("data") or {}) if e_ok else {}
check(
    "E.1 admin 的投诉看板看到属于 firm_hlw 的工单（跨租户全局）",
    e_ok and (e_data.get("pending") or 0) >= 1,
    f"HTTP {e_stats.status_code} pending={e_data.get('pending')} overdue={e_data.get('overdue')}",
)

e_list = client.get(
    "/api/v1/complaints",
    headers={"Authorization": f"Bearer {admin_token}"},
    params={"page_size": 50},
)
e_rows = (e_list.json().get("data") or {}).get("items") or []
e_tickets = [r.get("ticket_no") for r in e_rows]
e_tenants = {r.get("tenant_id") for r in e_rows}
check(
    "E.2 投诉列表里能查到那条 firm_hlw 的工单号",
    foreign_ticket in e_tickets,
    f"工单 {foreign_ticket} {'在' if foreign_ticket in e_tickets else '不在'}列表中（共 {len(e_tickets)} 条）",
)
check(
    "E.3 投诉列表不受租户视角约束（列表里出现非 platform 的租户）",
    "firm_hlw" in e_tenants,
    f"列表中的 tenant_id 集合 = {e_tenants or '空'}",
)


# ═══════════════ F. 非平台管理员不得访问投诉管理端 ═══════════════

f_resp = client.get("/api/v1/complaints", headers={"Authorization": f"Bearer {firm_token}"})
check(
    "F. 非平台管理员访问投诉工单列表 → 403（E 的全局性来自角色）",
    f_resp.status_code == 403,
    f"HTTP {f_resp.status_code}",
)


# ═══════════════ 汇总 ═══════════════

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
print("\n" + "=" * 68)
print(f"结果：{passed}/{total} 通过")
print("=" * 68)
if passed != total:
    for name, ok, detail in results:
        if not ok:
            print(f"  FAIL  {name}  -> {detail}")
    sys.exit(1)
sys.exit(0)
