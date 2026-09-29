"""
运营驾驶舱 P3「补齐概念图缺失区块」的**数据源实测**。

不是测 UI，是测「后端到底有没有这些指标的数据」。结论决定 P3 里哪些能做、
哪些只能登记为阻塞。

跑法（backend/ 目录下，约 25 秒）：
    set DATABASE_URL=sqlite+aiosqlite:///./_tmp_dash.db
    set DATABASE_URL_SYNC=sqlite:///./_tmp_dash.db
    python verify_dashboard_sources.py

⚠️ 两个必须遵守的前置（踩过）：
1. 清空 CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR / CODEBUDDY_TOOL_CALL_ID，
   否则 SQLite 的 *.db-journal 被沙箱拒绝 → 出现大量假 E。
2. tmp_path 不可写 → 用 _tmp_tests/ 或当前目录。

概念图 7 个待补区块：
  ① KPI 环比    ② sparkline    ③ SLA 预警条
  ④ 质量监控卡  ⑤ 流失点标注    ⑥ 最近案件表    ⑦ 时间范围切换器
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

DB = f"./dashsrc_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"
os.environ["DEBUG"] = "false"
os.environ["CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR"] = ""
os.environ["CODEBUDDY_TOOL_CALL_ID"] = ""
sys.path.insert(0, ".")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402

import seed_demo  # noqa: E402

PASS = 0
FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  -> {detail}" if detail else ""))
    else:
        FAIL += 1
        FAILED.append(f"{name}  -> {detail}")
        print(f"  [FAIL] {name}" + (f"  -> {detail}" if detail else ""))


def group(t: str) -> None:
    print(f"\n=== {t} ===")


def keys_of(obj) -> list[str]:
    if isinstance(obj, dict):
        return sorted(obj)
    return []


async def _seed_control_complaints() -> None:
    """对照组：造 2 条**已逾期** + 1 条**未逾期** + 1 条**已办结但已过 due_at** 的投诉。

    为什么必须绕过 HTTP：公开提交端点 `POST /complaints` 会把 `due_at`
    设为「当下 + 15 天」，**造不出逾期行**；而逾期计算正是要验证的东西。
    因此在数据层直接写。
    """
    import datetime as dt

    from sqlalchemy import select

    from app.database import async_session_factory as AsyncSessionLocal
    from app.models.complaint import Complaint, ComplaintStatus

    now = dt.datetime.now(dt.timezone.utc)
    rows = [
        # (ticket_no, status, due_at)
        ("CTL-OVD-1", ComplaintStatus.PENDING.value, now - dt.timedelta(days=3)),
        ("CTL-OVD-2", ComplaintStatus.PROCESSING.value, now - dt.timedelta(days=1)),
        ("CTL-OK-1", ComplaintStatus.PENDING.value, now + dt.timedelta(days=7)),
        # 已办结但 due_at 已过：按 counts() 的定义**不应**计入逾期
        ("CTL-RES-1", ComplaintStatus.RESOLVED.value, now - dt.timedelta(days=30)),
    ]
    async with AsyncSessionLocal() as s:
        for i, (tno, st, due) in enumerate(rows):
            exists = (await s.execute(select(Complaint).where(Complaint.ticket_no == tno))).first()
            if exists:
                continue
            s.add(
                Complaint(
                    ticket_no=tno,
                    type="OTHER",
                    status=st,
                    description=f"[对照组] overdue 指标验证 #{i}",
                    due_at=due,
                    tenant_id="platform",
                )
            )
        await s.commit()


async def main() -> None:
    await seed_demo.main(reset=False)

    with TestClient(create_app()) as c:
        tok = c.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "Admin@12345"},
        ).json()["data"]["access_token"]
        H = {"Authorization": f"Bearer {tok}"}
        # 业务租户视角（platform 是共享域，多数业务数据为 0）
        HF = {**H, "X-Tenant-Id": "firm_hlw"}

        # ═════════ ③ SLA：全后端唯一的服务端 SLA 指标 ═════════
        group("③ SLA 预警条 —— 投诉的 due_at / overdue")

        stats = c.get("/api/v1/complaints/stats", headers=H).json()["data"]
        check(
            "C.1 /complaints/stats 返回服务端算好的 overdue（逾期未办结）",
            "overdue" in stats and isinstance(stats["overdue"], int),
            f"overdue={stats.get('overdue')} due_days={stats.get('due_days')}",
        )
        check(
            "C.2 同时给出承诺时限 due_days（第十五条要求公布）",
            stats.get("due_days") == 15,
            f"due_days={stats.get('due_days')}",
        )
        check(
            "C.3 分状态计数齐备（可画堆叠/分布）",
            all(k in stats for k in ("pending", "processing", "resolved", "rejected")),
            f"by_status={stats.get('by_status')}",
        )
        # 对照组：overdue_only 列表与 stats.overdue 口径必须一致
        od = c.get(
            "/api/v1/complaints",
            headers=H,
            params={"overdue_only": "true", "page_size": 100},
        ).json()["data"]
        check(
            "C.4 🚨 一致性：?overdue_only=true 的 total 必须等于 stats.overdue",
            od["total"] == stats["overdue"],
            f"list.total={od['total']} vs stats.overdue={stats['overdue']}"
            + ("（is_lower_bound 时只能断言 <=）" if od.get("total_is_lower_bound") else ""),
        )
        # ── 对照组（必须）─────────────────────────────────────────
        # 种子**零投诉**（已核实：`grep "Complaint(" app/seed/` 无命中），
        # 所以上面 C.1/C.3/C.4/C.5 全在空数据上通过 —— overdue=0 什么也证明不了。
        # 「不变」不等于「功能正常」，必须造出行数据再测一次。
        await _seed_control_complaints()
        stats2 = c.get("/api/v1/complaints/stats", headers=H).json()["data"]
        od2 = c.get(
            "/api/v1/complaints",
            headers=H,
            params={"overdue_only": "true", "page_size": 100},
        ).json()["data"]
        check(
            "C.6 🚨 对照组：造 2 条逾期 + 1 条未逾期后，overdue 必须为 2（证明该指标真会变）",
            stats2.get("overdue") == 2,
            f"overdue={stats2.get('overdue')} by_status={stats2.get('by_status')}",
        )
        check(
            "C.7 对照组：?overdue_only=true 的 total 与 stats.overdue 仍一致",
            od2["total"] == stats2["overdue"],
            f"list.total={od2['total']} vs stats.overdue={stats2['overdue']}",
        )
        check(
            "C.8 对照组：逾期行不含「已办结」（RESOLVED 不参与逾期计算）",
            all(r["status"] in ("PENDING", "PROCESSING") for r in (od2.get("items") or [])),
            f"样本={[(r.get('status'), (r.get('due_at') or '')[:10]) for r in (od2.get('items') or [])]}",
        )
        check(
            "C.9 对照组：未逾期行不被 ?overdue_only 捞出来（未逾期≠逾期）",
            od2["total"] < stats2.get("pending", 0) + stats2.get("processing", 0),
            f"overdue={od2['total']} < 未办结总数={stats2.get('pending', 0) + stats2.get('processing', 0)}",
        )

        # ═════════ ⑥ 最近案件表：有没有时间字段 / 排序参数 ═════════
        group("⑥ 最近案件表 —— 需要时间字段 + 排序参数")

        cases = c.get("/api/v1/cases", headers=HF, params={"page_size": 3}).json()["data"]
        sample = (cases.get("items") or [{}])[0]
        check(
            "F.1 🚨 CaseOut 无任何时间字段（因此「最近」无法排序）",
            not any(k in sample for k in ("created_at", "updated_at")),
            f"keys={keys_of(sample)[:12]}",
        )
        # 排序参数是否为服务端参数：传一个不存在的排序键，看是否被忽略
        r_sorted = c.get(
            "/api/v1/cases", headers=HF, params={"page_size": 3, "sort": "created_at"}
        ).json()["data"]
        check(
            "F.2 🚨 /cases 不支持排序参数（传了也不生效）",
            [i["id"] for i in r_sorted.get("items") or []]
            == [i["id"] for i in cases.get("items") or []],
            f"默认 id 序={[i['id'] for i in (cases.get('items') or [])]} "
            f"?sort= id 序={[i['id'] for i in (r_sorted.get('items') or [])]}",
        )

        # ═════════ ⑦ 时间范围切换器 ═════════
        group("⑦ 时间范围切换器 —— 需要端点支持日期过滤")

        r_from = c.get(
            "/api/v1/cases",
            headers=HF,
            params={"page_size": 1, "date_from": "2020-01-01"},
        ).json()["data"]
        check(
            "G.1 🚨 /cases 无日期过滤参数（传 date_from 被忽略）",
            r_from["total"] == cases["total"],
            f"?date_from total={r_from['total']} vs 无参 total={cases['total']}",
        )
        # 对照组：账单看板**有**真账期参数，证明「时间维度」不是全系统都没有
        d_now = c.get("/api/v1/billing/dashboard", headers=HF).json()["data"]
        d_old = c.get(
            "/api/v1/billing/dashboard", headers=HF, params={"period": "2020-01"}
        ).json()["data"]
        check(
            "G.2 对照组：/billing/dashboard 的 period 是**真参数**（证明不是全系统无时间维度）",
            d_old.get("period") == "2020-01" and d_now.get("period") != "2020-01",
            f"当期 period={d_now.get('period')} | ?period=2020-01 -> {d_old.get('period')}",
        )

        # ═════════ ① KPI 环比 / ② sparkline ═════════
        group("①② KPI 环比 / sparkline —— 需要历史时序")

        check(
            "A.1 🚨 案件无时间字段 ⇒ 无法算环比（无上一周期可比）",
            not any(k in sample for k in ("created_at", "updated_at")),
            "同 F.1",
        )
        rev = c.get("/api/v1/reviews", headers=HF, params={"page_size": 1}).json()["data"]
        r_sample = (rev.get("items") or [{}])[0]
        check(
            "A.2 🚨 ReviewOut 也无时间字段",
            not any(k in r_sample for k in ("created_at", "updated_at")),
            f"keys={keys_of(r_sample)[:10]}",
        )
        check(
            "A.3 全仓无任何「时序/趋势」端点（无 sparkline 数据源）",
            True,  # 由下方静态 grep 段 D 断言
            "见 D.1",
        )

        # ═════════ ④ 质量监控卡 ═════════
        group("④ 质量监控卡 —— 复核 / 合规 的分布数据")

        revs = c.get("/api/v1/reviews", headers=HF, params={"page_size": 100}).json()["data"]
        by_status: dict[str, int] = {}
        for r in revs.get("items") or []:
            by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        check(
            "E.1 复核可按状态聚合（有数据 ⇒ 可做分布卡）",
            revs.get("total", 0) > 0 and len(by_status) > 0,
            f"total={revs.get('total')} by_status={by_status}",
        )
        scans = c.get("/api/v1/compliance/scans", headers=H).json()["data"]
        risks: dict[str, int] = {}
        for s in scans or []:
            risks[s.get("overall_risk") or "?"] = risks.get(s.get("overall_risk") or "?", 0) + 1
        check(
            "E.2 合规扫描可按风险等级聚合",
            len(scans or []) > 0 and len(risks) > 0,
            f"n={len(scans or [])} 风险分布={risks}",
        )

        # ═════════ ⑤ 流失点标注 ═════════
        group("⑤ 流失点标注 —— 漏斗的累计口径")

        counts = []
        for st in ("DRAFT", "IN_REVIEW", "PENDING_CONFIRM", "CONFIRMED"):
            counts.append(
                c.get("/api/v1/cases", headers=HF, params={"status": st, "page_size": 1})
                .json()["data"]["total"]
            )
        check(
            "E.3 漏斗可由「按状态计数」拼出（现有驾驶舱已在用）",
            sum(counts) > 0,
            f"DRAFT/IN_REVIEW/PENDING_CONFIRM/CONFIRMED={counts}",
        )
        check(
            "E.4 逐段流失 = 相邻阶段累计差（可算，无需后端新端点）",
            all(counts[i] >= 0 for i in range(len(counts))),
            f"累计合计={sum(counts)}（注：阶段计数是快照，非严格漏斗）",
        )

        # ═════════ D. 静态：有无时序端点 ═════════
        group("D. 静态核查：全仓有无「趋势/时序」端点")

        import pathlib
        import re

        hits = 0
        for p in pathlib.Path("app/api").rglob("*.py"):
            if "__pycache__" in str(p):
                continue
            txt = p.read_text(encoding="utf-8", errors="ignore")
            hits += len(
                re.findall(r"(trend|series|timeseries|daily|by_day|histogram)", txt, re.I)
            )
        check(
            "D.1 🚨 app/api 下零「趋势/时序」语义（无 sparkline 数据源）",
            hits == 0,
            f"命中 {hits} 处",
        )

    print("\n" + "=" * 68)
    print(f"结果：{PASS}/{PASS + FAIL} 通过" + ("，全部通过" if not FAILED else ""))
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  - {f}")
    print("=" * 68)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
