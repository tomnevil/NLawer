"""并发扣减与审计保留期验证（P1）。

本脚本的目的是**先证明缺陷存在**，再验证修复。
两轮观察到的规律：涉及并发与持久化的缺陷，静态审查一律看不出来。

覆盖：
  A. 并发扣减 —— 修复前后行为对比（超额消费是否发生）
  B. 超量转工单 —— 并发下是否会产生重复工单
  C. 审计保留期 —— 轮转/清理是否有实现
  D. 审计写入 —— 是否仍是各写各的（无统一口径）

用法：python verify_p1_concurrency_retention.py
退出码：0 全通过 / 1 有失败
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

DB = f"./verify_p1_{uuid.uuid4().hex[:8]}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB}"
os.environ["DATABASE_URL_SYNC"] = f"sqlite:///{DB}"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.models.billing import UsageQuota, UsageRecord, WorkOrder  # noqa: E402
from app.models.enums import UsageType  # noqa: E402
from app.models.base import Base  # noqa: E402
import app.models  # noqa: E402,F401

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail[:220]))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  -> {detail[:180]}" if detail else ""))


engine = create_async_engine(
    f"sqlite+aiosqlite:///{DB}",
    echo=False,
    connect_args={"timeout": 30},
)
factory = async_sessionmaker(engine, expire_on_commit=False)


async def _setup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


asyncio.run(_setup())


# ═══════════════ A. 并发扣减 ═══════════════
async def _concurrent_consume(limit: int, n: int, *, use_fix: bool) -> tuple[int, int, int]:
    """并发发起 n 次扣减，额度 limit。返回 (used_count, 成功次数, 超额转工单数)。

    SQLite 的写锁是库级，因此这里用**独立会话并发**模拟多副本竞争：
    每个协程各开一个 session，同时尝试"读额度 → 判断 → 自增"。
    """
    tenant = f"t_{uuid.uuid4().hex[:6]}"
    period = "2026-09"

    # 预置额度
    async with factory() as s:
        s.add(
            UsageQuota(
                tenant_id=tenant,
                usage_type=UsageType.DOCUMENT,
                period=period,
                limit_count=limit,
                used_count=0,
            )
        )
        await s.commit()

    gate = asyncio.Event()
    ok_count = 0
    exceeded_count = 0

    async def one(i: int):
        nonlocal ok_count, exceeded_count
        async with factory() as s:
            await gate.wait()
            from app.services.billing_service import BillingService

            svc = BillingService(s)
            if use_fix:
                r = await svc.consume_atomic(
                    tenant_id=tenant,
                    user_id=None,
                    usage_type=UsageType.DOCUMENT,
                    period=period,
                    ref_type="verify",
                    ref_id=i,
                )
            else:
                r = await svc.consume(
                    tenant_id=tenant,
                    user_id=None,
                    usage_type=UsageType.DOCUMENT,
                    period=period,
                    ref_type="verify",
                    ref_id=i,
                )
            await s.commit()
            if r["exceeded"]:
                exceeded_count += 1
            else:
                ok_count += 1

    tasks = [asyncio.create_task(one(i)) for i in range(n)]
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    async with factory() as s:
        q = (
            await s.execute(
                select(UsageQuota).where(UsageQuota.tenant_id == tenant)
            )
        ).scalars().first()
        wo = (
            await s.execute(
                select(func.count()).select_from(WorkOrder).where(WorkOrder.tenant_id == tenant)
            )
        ).scalar() or 0
    return (q.used_count or 0), ok_count, int(wo)


# 先用旧实现跑一次，量化缺陷
LIMIT = 5
N = 20
used_old, ok_old, _ = asyncio.run(_concurrent_consume(LIMIT, N, use_fix=False))
check(
    "A.1 【修复前】并发扣减不超额 —— 预期失败，用于证明缺陷真实存在",
    used_old <= LIMIT,
    f"limit={LIMIT} 并发={N} → used_count={used_old}（超额 {max(used_old - LIMIT, 0)} 次）",
)
print(f"      ↑ 旧实现实测：额度 {LIMIT}，{N} 并发 → used_count={used_old}")

used_new, ok_new, wo_new = asyncio.run(_concurrent_consume(LIMIT, N, use_fix=True))
check(
    "A.2 【修复后】并发扣减绝不超额（used_count 恒 <= limit）",
    used_new <= LIMIT,
    f"limit={LIMIT} 并发={N} → used_count={used_new} ok={ok_new}",
)
check(
    "A.3 【修复后】成功计数与 used_count 一致（不发生丢失更新）",
    ok_new == used_new,
    f"ok={ok_new} used={used_new}",
)
check(
    "A.4 【修复后】超出额度的请求全部转工单（不漏单）",
    wo_new >= N - LIMIT,
    f"并发={N} limit={LIMIT} → 工单={wo_new}（期望 >= {N - LIMIT}）",
)


# ═══════════════ B. 工单号唯一性 ═══════════════
async def _wo_unique() -> tuple[int, int]:
    """并发转工单时，订单号是否唯一（4 位随机后缀有碰撞风险）。"""
    tenant = f"t_{uuid.uuid4().hex[:6]}"
    period = "2026-09"
    async with factory() as s:
        s.add(
            UsageQuota(
                tenant_id=tenant, usage_type=UsageType.DOCUMENT,
                period=period, limit_count=0, used_count=0,
            )
        )
        await s.commit()

    gate = asyncio.Event()

    async def one(i: int):
        async with factory() as s:
            await gate.wait()
            from app.services.billing_service import BillingService

            try:
                await BillingService(s).consume(
                    tenant_id=tenant, user_id=None, usage_type=UsageType.DOCUMENT,
                    period=period, ref_type="verify", ref_id=i,
                )
                await s.commit()
            except Exception:
                await s.rollback()

    tasks = [asyncio.create_task(one(i)) for i in range(40)]
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(*tasks, return_exceptions=True)

    async with factory() as s:
        rows = list((await s.execute(select(WorkOrder))).scalars().all())
    nos = [r.order_no for r in rows]
    return len(nos), len(set(nos))


total, uniq = asyncio.run(_wo_unique())
check(
    "B.1 工单号在并发下唯一（无碰撞导致覆盖/冲突）",
    total == uniq,
    f"生成={total} 唯一={uniq}（碰撞={total - uniq}）",
)


# ═══════════════ C. 审计保留期与轮转 ═══════════════
def test_retention_impl_exists() -> tuple[bool, str]:
    """审计保留期是否有实现（等保要求 >=6 个月）。

    注意：`archive.py` 里的 `retention_years` 是**案件归档保存期限**，
    与审计日志保留期是两回事——早先版本的关键词扫描把它误判为命中，
    因此这里改为检查**审计专用**的符号，避免假阳性。
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent
    hits: list[str] = []
    for p in (root / "app").rglob("*.py"):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        for kw in ("AUDIT_RETENTION_DAYS", "audit_retention", "purge_audit", "cleanup_audit"):
            if kw in txt:
                hits.append(f"{p.name}:{kw}")
    return bool(hits), ",".join(hits[:6])


ok, detail = test_retention_impl_exists()
check("C.1 审计日志保留期/轮转机制已实现（等保 >=6 个月）", ok, detail or "全库无实现")

from app.config import settings  # noqa: E402

check(
    "C.2 保留天数可配置（运维可按等保要求调整）",
    hasattr(settings, "AUDIT_RETENTION_DAYS"),
    f"AUDIT_RETENTION_DAYS={getattr(settings, 'AUDIT_RETENTION_DAYS', 'MISSING')}",
)
if hasattr(settings, "AUDIT_RETENTION_DAYS"):
    check(
        "C.3 默认保留期满足等保要求（>=180 天）",
        settings.AUDIT_RETENTION_DAYS >= 180,
        f"={settings.AUDIT_RETENTION_DAYS} 天",
    )


# —— C.4/C.5：不是"有没有写"，而是"行为对不对" ——
def test_retention_force_raise() -> tuple[bool, str]:
    """把保留期配成 30 天（明确不合规）时，必须**拒绝遵从**并抬到 180。

    只检查"配置项存在"是不够的：如果服务老老实实按 30 天执行，
    配置项存在反而成了合规陷阱。因此这里直接改内存中的 settings 验证行为。
    """
    from app.services.audit_retention import SECURITY_BASELINE_DAYS, retention_days

    original = settings.AUDIT_RETENTION_DAYS
    try:
        settings.AUDIT_RETENTION_DAYS = 30
        effective = retention_days()
        ok = effective == SECURITY_BASELINE_DAYS
        return ok, f"配 30 → 生效 {effective}（下限 {SECURITY_BASELINE_DAYS}）"
    finally:
        settings.AUDIT_RETENTION_DAYS = original


def test_retention_on_demand_raise_only() -> tuple[bool, str]:
    """配成合规值（如 730）时必须**原样生效**——不能把用户的合理配置也压回 180。"""
    from app.services.audit_retention import retention_days

    original = settings.AUDIT_RETENTION_DAYS
    try:
        settings.AUDIT_RETENTION_DAYS = 730
        effective = retention_days()
        return effective == 730, f"配 730 → 生效 {effective}"
    finally:
        settings.AUDIT_RETENTION_DAYS = original


ok, detail = test_retention_force_raise()
check("C.4 保留期低于等保下限时强制抬到 180 天（不遵从错误配置）", ok, detail)

ok, detail = test_retention_on_demand_raise_only()
check("C.5 合规范围内的高保留期原样生效（不误伤合理配置）", ok, detail)


# ═══════════════ D. 归档（清理前须可导出）═══════════════
async def test_archive_impl_exists() -> tuple[bool, str]:
    """清理审计前必须能整批导出——否则等保检查时"昨天还在的日志今天没了"。

    这里不满足于"关键字扫描"（那只能证明有人写了 `archive` 三个字），
    而是**真的造过期数据 → 调 archive() → 校验产物**：
    文件存在、行数等于过期行数、sha256 非空、内容是合法 JSONL。
    """
    import gzip
    import json
    import pathlib

    from app.config import settings
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    # 先确认模块存在（防止"函数名被改没了"却仍通过）
    svc_file = pathlib.Path(__file__).resolve().parent / "app" / "services" / "audit_retention.py"
    if not svc_file.exists():
        return False, "未找到 services/audit_retention.py"

    # 造 3 条"已过保留期"的日志（created_at 回拨 400 天）
    import datetime

    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=400)
    async with factory() as db:
        for i in range(3):
            db.add(
                AuditLog(
                    tenant_id="t_arc",
                    actor_id=None,
                    actor_role=None,
                    action=f"ARC_TEST_{i}",
                    resource_type="verify",
                    resource_id=None,
                    detail={"i": i},
                    ip_address=None,
                    user_agent=None,
                    request_id=None,
                    success=True,
                    created_at=old,
                )
            )
        await db.commit()

        out_dir = os.path.join("_tmp_verify", "audit_archive")
        arc = await AuditRetentionService(db).archive(days=180, out_dir=out_dir)

        if not os.path.exists(arc["path"]):
            return False, "archive() 未产出文件"
        if arc["rows"] < 3:
            return False, f"归档行数 {arc['rows']} < 造出的 3 条"

        # 校验 sha256 与文件内容一致（导出可校验是"可归档"的核心）
        import hashlib

        hasher = hashlib.sha256()
        n = 0
        with gzip.open(arc["path"], "rt", encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if not line:
                    continue
                json.loads(line)  # 非法 JSON 会抛异常 → 直接判失败
                hasher.update((line + "\n").encode("utf-8"))
                n += 1
        if n != arc["rows"]:
            return False, f"文件实读 {n} 行 != 返回 {arc['rows']} 行"
        if hasher.hexdigest() != arc["sha256"]:
            return False, "sha256 与文件内容不一致"

        return True, f"{svc_file.name}: {arc['rows']} 行已导出且 sha256 校验通过"


ok, detail = asyncio.run(test_archive_impl_exists())
check("D.1 审计归档出口已实现并可通过 sha256 校验（清理前可导出留档）", ok, detail)


async def test_purge_defaults_to_dry_run() -> tuple[bool, str]:
    """purge() 默认必须 dry-run——清理不可逆，默认值要站在安全一侧。

    只调 `purge()`（不传 confirm）后，过期行数**必须不变**。
    """
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    async with factory() as db:
        before = int(
            (await db.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
        )
        res = await AuditRetentionService(db).purge(days=180)
        after = int(
            (await db.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
        )
    if not res.get("dry_run"):
        return False, "默认调用竟然真的删除了（危险）"
    return after == before, f"dry_run={res.get('dry_run')} would_delete={res.get('would_delete')} 行数 {before}→{after}"


async def test_purge_with_confirm_removes_expired() -> tuple[bool, str]:
    """显式 confirm 后应真的删除过期行，且**留下 UND_RETENTION_PURGE 墓碑审计**。"""
    from app.core.audit import AuditAction
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    async with factory() as db:
        expired_before = int(
            (
                await db.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.action.like("ARC_TEST_%"))
                )
            ).scalar()
            or 0
        )
        res = await AuditRetentionService(db).purge(days=180, confirm=True)
        await db.commit()
        remaining = int(
            (
                await db.execute(
                    select(func.count()).select_from(AuditLog).where(AuditLog.action.like("ARC_TEST_%"))
                )
            ).scalar()
            or 0
        )
        tombstone = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == AuditAction.AUDIT_RETENTION_PURGE)
                )
            ).scalar()
            or 0
        )

    if res.get("dry_run"):
        return False, "confirm=True 仍走 dry-run"
    if remaining != 0:
        return False, f"过期行未清干净，剩余 {remaining}"
    if tombstone == 0:
        return False, "清理未留痕（审计的删除也必须被审计）"
    return True, f"删除={res.get('deleted')}（原过期 {expired_before}）墓碑审计={tombstone}"


ok, detail = asyncio.run(test_purge_defaults_to_dry_run())
check("D.2 清理默认 dry-run（不传 confirm 绝不删除）", ok, detail)

ok, detail = asyncio.run(test_purge_with_confirm_removes_expired())
check("D.3 显式 confirm 后清理生效并留痕 AUDIT_RETENTION_PURGE", ok, detail)


# ═══════════════ 汇总 ═══════════════
passed = sum(1 for _, ok, _ in results if ok)
failed = len(results) - passed
print(f"\n{'=' * 68}")
print(f"P1 并发扣减与审计保留期验证：{passed} 通过 / {failed} 失败")
print("=" * 68)
if failed:
    for name, ok, detail in results:
        if not ok:
            print(f"  [FAIL] {name}  -> {detail}")
    sys.exit(1)
print("全部通过。")
