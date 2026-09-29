"""派单记录 + 🚨「派单规则」能力边界端到端验证。

## 为什么需要这个脚本

概念图把这一项写作「**派单规则**」，但实测发现后端的情况比「没做」更微妙：

> **规则引擎是活的，只是看不见、也配不了。**

| 层 | 现状 |
|---|---|
| 模型 `DispatchRule`（`models/case.py:112`） | ✅ 字段完整：条件 JSON / 策略 / 候选白名单 / 优先级 / 启用 |
| 工作流 `workflows/dispatch_rules.py` | ✅ `resolve_strategy()` 在**每次 AUTO 派单**被调用 |
| API `app/api/v1/` | ❌ **零端点**（读、写都没有） |
| 种子数据 | ❌ **零规则行** |

⇒ 后果：所有 AUTO 派单实际都走**兜底策略 `SPECIALTY_MATCH`**，
且用户从任何界面都看不到、也改不了这件事。

本脚本把「引擎在跑」和「无数据无端点」两半**分别验证**——
只验一半会得出「规则功能没实现」或「规则功能没问题」两种错误结论。

## 验证什么

| # | 断言 |
|---|---|
| A | `?status=` / `?mode=` 是**服务端**参数（不是前端假筛选） |
| B | `/dispatches/pool` 的口径是「待接 且（未指定律师 或 POOL）」，与「待接单」不是同一集合 |
| C | 默认视角（`platform`）派单为 0；`firm_hlw` 有 6 条（对照组） |
| D | 🚨 `DispatchRule` 在 `app/api/v1/` 下**零端点**（静态 grep） |
| E | 🚨 规则表**零行** ⇒ `resolve_strategy()` 恒返回兜底 `SPECIALTY_MATCH` |
| F | 种子对**全部**派单写入同一句理由（含 DESIGNATED），属演示数据瑕疵并记录在案 |

运行：
    cd backend
    python verify_dispatches.py
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from typing import Any

DB = f"./verify_dispatch_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["DEBUG"] = "false"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail[:240]))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


def group(title: str) -> None:
    print(f"\n=== {title} ===")


import seed_demo  # noqa: E402

asyncio.run(seed_demo.main(reset=False))

H: dict[str, str] = {}


def run_all(client: TestClient) -> None:
    tok = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"})
    assert tok.status_code == 200
    H["Authorization"] = f"Bearer {tok.json()['data']['access_token']}"

    def hdr(tenant: str | None = None) -> dict[str, str]:
        h = dict(H)
        if tenant:
            h["X-Tenant-Id"] = tenant
        return h

    def page(client: TestClient, tenant: str | None, **params: Any) -> dict[str, Any]:
        r = client.get("/api/v1/dispatches", headers=hdr(tenant), params=params or None)
        return r.json().get("data") or {}

    # ═══════════════ A. 服务端筛选参数 ═══════════════

    group("A. status / mode 是否为服务端参数")
    allp = page(client, "firm_hlw", page_size=100)
    check("A.1 firm_hlw 有派单数据", (allp.get("total") or 0) > 0, f"total={allp.get('total')}")

    by_status = page(client, "firm_hlw", page_size=1, status="PENDING")
    check(
        "A.2 ?status=PENDING 全命中（服务端过滤生效）",
        all(i["status"] == "PENDING" for i in by_status.get("items") or []),
        f"total={by_status.get('total')}",
    )
    by_mode = page(client, "firm_hlw", page_size=1, mode="AUTO")
    check(
        "A.3 ?mode=AUTO 全命中（服务端过滤生效）",
        all(i["mode"] == "AUTO" for i in by_mode.get("items") or []),
        f"total={by_mode.get('total')}",
    )
    check(
        "A.4 列表是 Page 对象（有分页，与合规/工单的裸数组不同）",
        {"items", "total", "pages"} <= set(allp),
        f"keys={sorted(allp)}",
    )

    # ═══════════════ B. 抢单池口径 ═══════════════

    group("B. /dispatches/pool 的口径")
    pool = client.get("/api/v1/dispatches/pool", headers=hdr("firm_hlw"), params={"page_size": 100})
    pool_data = pool.json().get("data") or {}
    pool_items = pool_data.get("items") or []
    check(
        "B.1 抢单池全为 PENDING",
        all(i["status"] == "PENDING" for i in pool_items),
        f"total={pool_data.get('total')}",
    )
    check(
        "B.2 抢单池全满足「未指定律师 或 POOL 模式」",
        all(i["lawyer_id"] is None or i["mode"] == "POOL" for i in pool_items),
        f"样本={[(i['mode'], i['lawyer_id']) for i in pool_items[:4]]}",
    )
    pending_total = page(client, "firm_hlw", page_size=1, status="PENDING").get("total")
    check(
        "B.3 抢单池 ⊆ 待接单（两者口径不同，数字不同是正常的）",
        (pool_data.get("total") or 0) <= (pending_total or 0),
        f"pool={pool_data.get('total')} pending={pending_total}",
    )

    # ═══════════════ C. 租户间差异（含对照组）═══════════════

    group("C. 默认视角 vs 业务租户")
    plat = page(client, None)
    check("C.1 默认视角（platform）派单为 0", (plat.get("total") or 0) == 0, f"total={plat.get('total')}")
    check(
        "C.2 对照组：firm_hlw 有派单（证明 C.1 不是「功能坏了」）",
        (allp.get("total") or 0) > 0,
        f"firm_hlw total={allp.get('total')}",
    )
    check(
        "C.3 firm_hlw 三种派单方式齐全",
        len({i["mode"] for i in (allp.get("items") or [])}) == 3,
        f"modes={sorted({i['mode'] for i in (allp.get('items') or [])})}",
    )

    # ═══════════════ F. 种子理由的瑕疵 ═══════════════

    group("F. 派单理由不反映真实依据（记录在案）")

    # 注意：首版断言写的是「所有 reason 完全相同」，实测 reasons={None, '演示数据：...'}，
    # 即部分派单的 reason 为 NULL —— 断言失败是**脚本缺陷**不是产品缺陷。
    # 真实结论比原断言更强：reason 要么为空、要么同一句演示文案，
    # **从不反映该条派单的实际依据**（mode / score 各异，理由却一样）。
    DEMO_REASON = "演示数据：按专业领域匹配"
    by_mode: dict[str, set] = {}
    for i in allp.get("items") or []:
        by_mode.setdefault(i["mode"], set()).add(i.get("reason"))
    check(
        "F.1 🚨 没有任何派单的 reason 反映其真实依据（要么 NULL，要么同一句演示文案）",
        all(r in (None, DEMO_REASON) for rs in by_mode.values() for r in rs),
        f"mode→reason={ {m: sorted(map(str, rs)) for m, rs in sorted(by_mode.items())} }",
    )
    designated = [i for i in (allp.get("items") or []) if i["mode"] == "DESIGNATED"]
    check(
        "F.2 🚨 DESIGNATED（客户指定）派单的 reason 也写着「按专业领域匹配」",
        bool(designated) and "专业领域" in (designated[0].get("reason") or ""),
        f"mode={designated[0]['mode'] if designated else None} reason={designated[0].get('reason') if designated else None}",
    )
    check(
        "F.3 DispatchOut 无时间字段（前端因此不显示时间）",
        not any(t in (allp["items"][0] if allp.get("items") else {}) for t in ("created_at", "accepted_at")),
        f"keys={sorted((allp.get('items') or [{}])[0])}",
    )


# ═══════════════ D/E. 🚨 派单规则的能力边界 ═══════════════


def rule_checks() -> None:
    group("D. 🚨 DispatchRule 是否有任何 API 端点（静态）")
    grep = subprocess.run(
        ["grep", "-rn", r"DispatchRule\|dispatch_rules", "app/api/"], capture_output=True, text=True
    )
    hits = [ln for ln in grep.stdout.splitlines() if "__pycache__" not in ln]
    check(
        "D.1 app/api/ 下零命中（无任何规则端点）",
        len(hits) == 0,
        f"命中 {len(hits)} 处" + (f": {hits[:2]}" if hits else ""),
    )

    group("E. 🚨 规则表是否有数据 ⇒ resolve_strategy 是否走兜底")
    from sqlalchemy import select  # noqa: E402

    from app.database import async_session_factory  # noqa: E402
    from app.models.case import Case, DispatchRule  # noqa: E402
    from app.workflows import dispatch_rules  # noqa: E402

    async def probe() -> tuple[int, list[str], int]:
        async with async_session_factory() as s:
            rules = list((await s.execute(select(DispatchRule))).scalars().all())
            cases = list((await s.execute(select(Case))).scalars().all())
            out = []
            for c in cases[:5]:
                strat = await dispatch_rules.resolve_strategy(s, c)
                out.append(strat)
            return len(rules), out, len(cases)

    n_rules, strategies, n_cases = asyncio.run(probe())
    check(
        "E.1 规则表零行（种子未造 DispatchRule）",
        n_rules == 0,
        f"dispatch_rules 行数={n_rules}",
    )
    check(
        "E.2 🚨 全部案件解析出的策略都是兜底值 SPECIALTY_MATCH",
        bool(strategies) and set(strategies) == {"SPECIALTY_MATCH"},
        f"样本 {len(strategies)}/{n_cases} 条案件 → {sorted(set(strategies))}",
    )
    check(
        "E.3 引擎确实被调用了（不是空跑）——样本非空",
        len(strategies) > 0,
        f"解析了 {len(strategies)} 条案件",
    )


with TestClient(create_app()) as client:
    run_all(client)

rule_checks()

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
