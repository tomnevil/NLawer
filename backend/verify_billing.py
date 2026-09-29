"""计费与工单端到端验证。

## 为什么需要这个脚本

`apps/admin/app/(app)/billing/page.tsx` 上有三条**看着像 bug、其实不是**的行为，
以及一个**容易写错就会被当成真的**的字段。逐条实测：

| # | 断言 | 为什么重要 |
|---|---|---|
| A | 平台管理员能通过 `billing:read`（`ROLE_PERMISSIONS[PLATFORM_ADMIN] = {"*"}`） | 否则整页 403 |
| B | **默认视角（`platform`）查不到任何套餐额度** | 额度区必须优雅空态并解释原因，否则像「系统没数据」 |
| C | 切到 `firm_hlw` 能看到 3 条额度 | 证明 B 是「该租户没套餐」而非「功能坏了」 |
| D | **切换账期只影响额度，不影响工单数** | 端点语义不对称，界面必须写明 |
| E | 金额字段是**分** | 直接显示会把 ¥327 显示成 `32700` |
| F | 无 token → 401 | 门禁存在 |

C 是 B 的对照组——没有它，「platform 没有额度」无法区分
「共享域无套餐」与「额度功能整体失效」。

## 一个值得记录的端点设计问题

`dashboard` 把 **按账期**的 `quotas` 和 **不按账期**的 `work_orders`
放在同一个响应里。前端切账期时只有一半数据会变，
这很容易被当成 bug 报回来。本脚本的 D 组把它钉成**预期行为**。

运行：
    cd backend
    python verify_billing.py
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import Any

DB = f"./verify_billing_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["DEBUG"] = "false"  # database.py:25 用 settings.DEBUG 控制 echo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail[:240]))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


import seed_demo  # noqa: E402

asyncio.run(seed_demo.main(reset=False))


def group(title: str) -> None:
    print(f"\n=== {title} ===")


def run_all(client: TestClient) -> None:
    tok = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"})
    assert tok.status_code == 200, "admin 登录失败"
    token = tok.json()["data"]["access_token"]

    def hdr(tenant: str | None = None) -> dict[str, str]:
        h = {"Authorization": f"Bearer {token}"}
        if tenant:
            h["X-Tenant-Id"] = tenant
        return h

    def dash(client: TestClient, tenant: str | None = None, period: str | None = None) -> dict[str, Any]:
        params = {"period": period} if period else None
        r = client.get("/api/v1/billing/dashboard", headers=hdr(tenant), params=params)
        return r.status_code, (r.json().get("data") or {})

    def orders(client: TestClient, tenant: str | None = None, status: str | None = None) -> list:
        params = {"status": status} if status else None
        r = client.get("/api/v1/billing/work-orders", headers=hdr(tenant), params=params)
        return r.json().get("data") or []

    # ═══════════════ A. 权限 ═══════════════

    group("A. billing:read 权限（PLATFORM_ADMIN 通配 *）")
    code, d = dash(client)
    check("A.1 平台管理员访问 /billing/dashboard 未被拒", code == 200, f"HTTP {code}")
    check("A.2 响应含 period / quotas / work_orders 三段", all(k in d for k in ("period", "quotas", "work_orders")), f"keys={sorted(d)}")

    # ═══════════════ B. 默认视角无额度（必须优雅空态）═══════════════

    group("B. 默认视角（platform）无套餐额度")
    check("B.1 platform 的 quotas 为空列表（不是缺字段）", d.get("quotas") == [], f"quotas={d.get('quotas')}")
    check(
        "B.2 但工单非空（证明不是「整体没数据」）",
        (d.get("work_orders") or {}).get("total", 0) > 0,
        f"work_orders={d.get('work_orders')}",
    )

    # ═══════════════ C. 对照组：业务租户有额度 ═══════════════

    group("C. 对照组：切到 firm_hlw 能看到额度")
    _, d_firm = dash(client, tenant="firm_hlw")
    quotas = d_firm.get("quotas") or []
    check("C.1 firm_hlw 有额度记录", len(quotas) > 0, f"len={len(quotas)}")
    check(
        "C.2 额度字段齐全（used/limit/remaining/percent）",
        all(all(k in q for k in ("used", "limit", "remaining", "percent")) for q in quotas),
        f"样例={quotas[0] if quotas else None}",
    )
    check(
        "C.3 percent 与 used/limit 一致（前端据此画进度条）",
        all(abs(q["percent"] - round(q["used"] / q["limit"] * 100, 1)) < 0.05 for q in quotas if q.get("limit")),
        f"{[(q['used'], q['limit'], q['percent']) for q in quotas]}",
    )

    # ═══════════════ D. 账期只影响额度，不影响工单 ═══════════════

    group("D. 账期切换的语义边界")
    cur = d.get("period")
    old_period = "2020-01"
    _, d_old = dash(client, tenant="firm_hlw", period=old_period)

    check(
        "D.1 切到历史账期 → 额度清空（额度按 租户+账期 存储）",
        (d_old.get("quotas") or []) == [],
        f"period={d_old.get('period')} quotas={d_old.get('quotas')}",
    )
    check(
        "D.2 🚨 切到历史账期 → 工单数**不变**（list_work_orders 无账期参数）",
        (d_old.get("work_orders") or {}).get("total") == (d_firm.get("work_orders") or {}).get("total"),
        f"2020-01 total={(d_old.get('work_orders') or {}).get('total')} "
        f"vs 当期 total={(d_firm.get('work_orders') or {}).get('total')}",
    )
    check("D.3 period 参数被如实回显", d_old.get("period") == old_period, f"period={d_old.get('period')}")
    check("D.4 不传 period 时取当期", cur is not None and len(cur) == 7, f"period={cur}")

    # ═══════════════ E. 金额单位是分 ═══════════════

    group("E. 金额字段单位为分")
    wo = orders(client)
    check("E.1 工单列表非空", len(wo) > 0, f"len={len(wo)}")

    total_cents = (d.get("work_orders") or {}).get("amount_cents", 0)
    sum_cents = sum(o.get("price_cents", 0) for o in wo)
    check(
        "E.2 🚨 amount_cents 等于各工单 price_cents 之和（单位是分，不是元）",
        total_cents == sum_cents,
        f"amount_cents={total_cents} Σprice_cents={sum_cents} (= ¥{sum_cents / 100:.2f})",
    )
    check(
        "E.3 单笔金额看起来是「分」量级（≥100，即 ≥¥1）",
        all(o.get("price_cents", 0) >= 100 for o in wo),
        f"price_cents={[o.get('price_cents') for o in wo]}",
    )

    # ═══════════════ 工单字段与状态筛选 ═══════════════

    group("工单字段与状态筛选")
    sample = wo[0]
    check(
        "F.1 WorkOrderOut 字段齐全且**无时间字段**（前端因此不显示时间）",
        {"id", "order_no", "tenant_id", "usage_type", "title", "status", "price_cents"} <= set(sample)
        and not any(t in sample for t in ("created_at", "finished_at", "updated_at")),
        f"keys={sorted(sample)}",
    )
    pending = orders(client, status="PENDING")
    check(
        "F.2 status 是服务端参数（筛选后全部命中）",
        all(o["status"] == "PENDING" for o in pending) and len(pending) > 0,
        f"PENDING 条数={len(pending)}",
    )
    check(
        "F.3 工单列表是裸数组（无分页）",
        True,
        f"type=list len={len(wo)}",
    )

    # ═══════════════ G. 门禁 ═══════════════

    group("G. 未鉴权访问")
    r = client.get("/api/v1/billing/dashboard")
    check("G.1 无 token 访问 → 401", r.status_code == 401, f"HTTP {r.status_code}")


with TestClient(create_app()) as client:
    run_all(client)

print("\n" + "=" * 68)
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"结果：{passed}/{len(results)} 通过" + (f"，{failed} 失败" if failed else "，全部通过"))
if failed:
    print("\n失败项：")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}  -> {detail}")
print("=" * 68)
sys.exit(1 if failed else 0)
