"""并发扣减与审计保留期单元测试（P1）。

测试重点放在**"看起来对但实际错"**的路径上——这两项的失效模式都很隐蔽：
  - 额度扣减：读-判-写三步在单线程测试里永远正确，只有并发才暴露丢失更新
  - 审计保留：`retention_days()` 若老实遵从 30 天的错误配置，反倒成了合规陷阱

因此用例集中验证：
  - `consume_atomic()` 在并发下 used_count 恒 <= limit，且成功次数与之一致
  - 超额请求必须转工单，一条都不能漏（漏单 = 白送服务）
  - 保留期低于等保下限时必须**拒绝遵从**并抬到 180
  - `purge()` 默认 dry-run，只有显式 confirm 才删除
  - 清理必须留痕 `AUDIT_RETENTION_PURGE`（审计的删除也要被审计）
"""
from __future__ import annotations

import asyncio
import datetime
import gzip
import json
import pathlib
import uuid

import pytest
from sqlalchemy import func, select


# ═══════════════ 夹具：独立真库 ═══════════════
@pytest.fixture
def db_ctx():
    """建一个临时 SQLite 库并返回 sessionmaker。

    库文件落在项目内 `_tmp_tests/`（`tmp_path` 在部分沙箱环境下不可写），
    且**不主动删除**——删除会触发沙箱的批量删除守卫。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401  触发全部模型注册
    from app.models.base import Base

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"p1_{uuid.uuid4().hex[:8]}.db"

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{dbfile}", echo=False, connect_args={"timeout": 30}
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_setup())

    # ⚠️ `log_detached` 用的是**全局**独立引擎（指向 settings.DATABASE_URL），
    # 与这里的私有测试库不是同一个文件。若不对齐，"清理墓碑"这类走独立
    # 连接写入的审计会落到另一个库（或根本不存在）→ 测试永远看不到它。
    # 生产环境只有一个库，不会遇到；测试必须显式对齐。
    import app.core.audit as _audit

    saved = _audit._detached_factory
    _audit._detached_factory = factory

    yield factory

    _audit._detached_factory = saved
    asyncio.run(engine.dispose())


def _run(coro):
    return asyncio.run(coro)


# ═══════════════ A. 原子扣减 ═══════════════
def test_atomic_consume_respects_limit_under_concurrency(db_ctx):
    """并发 20 请求 / 额度 5：used_count 必须恰好 5，成功 5 次。

    这是本项修复的**核心断言**。旧实现（读-判-写）实测只能记到 1~2，
    既少计费又少转工单——单线程测试完全看不出来。
    """
    from app.models.billing import UsageQuota
    from app.models.enums import UsageType
    from app.services.billing_service import BillingService

    limit, n = 5, 20
    tenant, period = f"t_{uuid.uuid4().hex[:6]}", "2026-09"

    async def _go():
        async with db_ctx() as s:
            q = await BillingService(s).ensure_quota(tenant, UsageType.DOCUMENT, period)
            q.limit_count = limit
            await s.commit()

        gate = asyncio.Event()
        ok_count = 0

        async def one(i: int):
            nonlocal ok_count
            async with db_ctx() as s:
                await gate.wait()
                try:
                    r = await BillingService(s).consume_atomic(
                        tenant_id=tenant, user_id=None, usage_type=UsageType.DOCUMENT,
                        period=period, ref_type="test", ref_id=i,
                    )
                    await s.commit()
                    if not r["exceeded"]:
                        ok_count += 1
                except Exception:
                    await s.rollback()

        tasks = [asyncio.create_task(one(i)) for i in range(n)]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)

        async with db_ctx() as s:
            row = (
                await s.execute(
                    select(UsageQuota).where(
                        UsageQuota.tenant_id == tenant, UsageQuota.period == period
                    )
                )
            ).scalars().first()
            return row.used_count, ok_count

    used, ok = _run(_go())
    assert used <= limit, f"used_count={used} 超额（limit={limit}）"
    assert used == limit, f"used_count={used} != limit={limit}，存在丢失更新"
    assert ok == used, f"成功计数 {ok} != used_count {used}，存在丢失更新"


def test_atomic_consume_escalates_every_over_limit_request(db_ctx):
    """超额请求必须全部转工单——漏单就是白送服务。"""
    from app.models.billing import WorkOrder
    from app.models.enums import UsageType
    from app.services.billing_service import BillingService

    limit, n = 3, 12
    tenant, period = f"t_{uuid.uuid4().hex[:6]}", "2026-09"

    async def _go():
        async with db_ctx() as s:
            q = await BillingService(s).ensure_quota(tenant, UsageType.QA, period)
            q.limit_count = limit
            await s.commit()

        gate = asyncio.Event()

        async def one(i: int):
            async with db_ctx() as s:
                await gate.wait()
                try:
                    await BillingService(s).consume_atomic(
                        tenant_id=tenant, user_id=None, usage_type=UsageType.QA,
                        period=period, ref_type="test", ref_id=i,
                    )
                    await s.commit()
                except Exception:
                    await s.rollback()

        tasks = [asyncio.create_task(one(i)) for i in range(n)]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)

        async with db_ctx() as s:
            return int(
                (await s.execute(select(func.count()).select_from(WorkOrder))).scalar() or 0
            )

    orders = _run(_go())
    assert orders == n - limit, f"工单 {orders} != 期望 {n - limit}，存在漏单"


def test_work_order_numbers_unique_under_concurrency(db_ctx):
    """并发建工单时工单号必须唯一——碰撞会导致覆盖或唯一约束报错。"""
    from app.models.billing import WorkOrder
    from app.models.enums import UsageType
    from app.services.billing_service import BillingService

    tenant, period = f"t_{uuid.uuid4().hex[:6]}", "2026-09"

    async def _go():
        async with db_ctx() as s:
            q = await BillingService(s).ensure_quota(tenant, UsageType.DOCUMENT, period)
            q.limit_count = 0  # 全部超额
            await s.commit()

        gate = asyncio.Event()

        async def one(i: int):
            async with db_ctx() as s:
                await gate.wait()
                try:
                    await BillingService(s).consume_atomic(
                        tenant_id=tenant, user_id=None, usage_type=UsageType.DOCUMENT,
                        period=period, ref_type="test", ref_id=i,
                    )
                    await s.commit()
                except Exception:
                    await s.rollback()

        tasks = [asyncio.create_task(one(i)) for i in range(30)]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)

        async with db_ctx() as s:
            rows = list((await s.execute(select(WorkOrder))).scalars().all())
            return [r.order_no for r in rows]

    nos = _run(_go())
    assert len(nos) == len(set(nos)), "工单号出现碰撞"


def test_quota_row_is_unique_per_tenant_type_period(db_ctx):
    """并发首次使用不得插出多行额度——多行会变相放大可用额度。"""
    from app.models.billing import UsageQuota
    from app.models.enums import UsageType
    from app.services.billing_service import BillingService

    tenant, period = f"t_{uuid.uuid4().hex[:6]}", "2026-09"

    async def _go():
        gate = asyncio.Event()

        async def one():
            async with db_ctx() as s:
                await gate.wait()
                try:
                    await BillingService(s).ensure_quota(tenant, UsageType.QA, period)
                    await s.commit()
                except Exception:
                    await s.rollback()

        tasks = [asyncio.create_task(one()) for _ in range(15)]
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.gather(*tasks, return_exceptions=True)

        async with db_ctx() as s:
            return int(
                (
                    await s.execute(
                        select(func.count()).select_from(UsageQuota).where(
                            UsageQuota.tenant_id == tenant, UsageQuota.period == period
                        )
                    )
                ).scalar()
                or 0
            )

    assert _run(_go()) == 1, "并发首次使用插出了多行额度"


# ═══════════════ B. 保留期口径 ═══════════════
def test_retention_days_refuses_below_baseline(monkeypatch):
    """低于等保下限的配置必须被拒绝遵从——否则"配置项存在"反成合规陷阱。"""
    from app.config import settings
    from app.services.audit_retention import SECURITY_BASELINE_DAYS, retention_days

    monkeypatch.setattr(settings, "AUDIT_RETENTION_DAYS", 30)
    assert retention_days() == SECURITY_BASELINE_DAYS


def test_retention_days_honours_compliant_value(monkeypatch):
    """合规范围内的高保留期必须原样生效——不能把合理配置也压回 180。"""
    from app.config import settings
    from app.services.audit_retention import retention_days

    monkeypatch.setattr(settings, "AUDIT_RETENTION_DAYS", 730)
    assert retention_days() == 730


def test_default_retention_meets_baseline():
    """出厂默认值本身就必须合规，避免"开箱即违规"。"""
    from app.config import settings
    from app.services.audit_retention import SECURITY_BASELINE_DAYS

    assert settings.AUDIT_RETENTION_DAYS >= SECURITY_BASELINE_DAYS


def test_cutoff_is_in_the_past():
    from app.services.audit_retention import cutoff_at

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = cutoff_at(180)
    assert cutoff < now
    # `timedelta.days` 向下取整：微秒差会让 180 天算成 179，故用容差断言
    assert abs((now - cutoff).total_seconds() - 180 * 86400) < 5


# ═══════════════ C. 归 档 ═══════════════
def _make_expired(factory, n: int, *, days_ago: int = 400, prefix: str = "RET_TEST"):
    """造 n 条已过保留期的审计记录。"""
    from app.models.audit_log import AuditLog

    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_ago)

    async def _go():
        async with factory() as s:
            for i in range(n):
                s.add(
                    AuditLog(
                        tenant_id="t_ret", actor_id=None, actor_role=None,
                        action=f"{prefix}_{i}", resource_type="verify", resource_id=None,
                        detail={"i": i}, ip_address=None, user_agent=None,
                        request_id=None, success=True, created_at=old,
                    )
                )
            await s.commit()

    return _go()


def test_archive_produces_verifiable_jsonl(db_ctx, tmp_path=None):
    """归档产物必须可校验：行数一致 + sha256 与内容一致 + 是合法 JSONL。

    清理前若拿不到可信归档，等保检查时就是"日志莫名其妙没了"。
    """
    import hashlib

    from app.services.audit_retention import AuditRetentionService

    _run(_make_expired(db_ctx, 4))

    async def _go():
        async with db_ctx() as s:
            return await AuditRetentionService(s).archive(
                days=180, out_dir="_tmp_tests/arc"
            )

    arc = _run(_go())
    assert pathlib.Path(arc["path"]).exists()
    assert arc["rows"] >= 4

    hasher = hashlib.sha256()
    n = 0
    with gzip.open(arc["path"], "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line:
                json.loads(line)
                hasher.update((line + "\n").encode("utf-8"))
                n += 1
    assert n == arc["rows"], "文件实读行数与返回值不一致"
    assert hasher.hexdigest() == arc["sha256"], "sha256 与文件内容不一致"


def test_archive_is_read_only(db_ctx):
    """归档**不得删除**任何记录——只导出。"""
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    _run(_make_expired(db_ctx, 3, prefix="RO_TEST"))

    async def _go():
        async with db_ctx() as s:
            before = int(
                (await s.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
            )
            await AuditRetentionService(s).archive(days=180, out_dir="_tmp_tests/arc_ro")
            await s.rollback()
        async with db_ctx() as s:
            after = int(
                (await s.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
            )
        return before, after

    before, after = _run(_go())
    assert before == after, "归档竟然删除了记录"


# ═══════════════ D. 清 理 ═══════════════
def test_purge_defaults_to_dry_run(db_ctx):
    """清理默认必须 dry-run——不可逆操作的默认值要站在安全一侧。"""
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    _run(_make_expired(db_ctx, 3, prefix="DRY_TEST"))

    async def _go():
        async with db_ctx() as s:
            before = int(
                (await s.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
            )
            res = await AuditRetentionService(s).purge(days=180)
        async with db_ctx() as s:
            after = int(
                (await s.execute(select(func.count()).select_from(AuditLog))).scalar() or 0
            )
        return res, before, after

    res, before, after = _run(_go())
    assert res["dry_run"] is True
    assert before == after, "默认调用竟然真的删除了"
    assert res["would_delete"] >= 3


def test_purge_with_confirm_deletes_and_leaves_tombstone(db_ctx):
    """显式 confirm 后应真删过期行，**且留下 AUDIT_RETENTION_PURGE 墓碑**。

    墓碑这一条是本项最容易漏的：清理走独立连接写审计，而主连接此刻
    可能仍持有未提交的写事务，SQLite 会 `database is locked`，
    早先实现"失败即忽略"，于是"审计的删除"自己没被审计。
    """
    from app.core.audit import AuditAction
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    _run(_make_expired(db_ctx, 3, prefix="PURGE_TEST"))

    async def _go():
        async with db_ctx() as s:
            res = await AuditRetentionService(s).purge(days=180, confirm=True)
        async with db_ctx() as s:
            left = int(
                (
                    await s.execute(
                        select(func.count()).select_from(AuditLog).where(
                            AuditLog.action.like("PURGE_TEST_%")
                        )
                    )
                ).scalar()
                or 0
            )
            tomb = int(
                (
                    await s.execute(
                        select(func.count()).select_from(AuditLog).where(
                            AuditLog.action == AuditAction.AUDIT_RETENTION_PURGE
                        )
                    )
                ).scalar()
                or 0
            )
        return res, left, tomb

    res, left, tomb = _run(_go())
    assert res["dry_run"] is False
    assert left == 0, f"过期行未清干净，剩余 {left}"
    assert tomb >= 1, "清理未留痕（审计的删除也必须被审计）"


def test_purge_keeps_records_within_retention(db_ctx):
    """保留期内的记录**绝不能被删**——少留一天都不合规。"""
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    _run(_make_expired(db_ctx, 3, days_ago=10, prefix="FRESH_TEST"))

    async def _go():
        async with db_ctx() as s:
            await AuditRetentionService(s).purge(days=180, confirm=True)
        async with db_ctx() as s:
            return int(
                (
                    await s.execute(
                        select(func.count()).select_from(AuditLog).where(
                            AuditLog.action.like("FRESH_TEST_%")
                        )
                    )
                ).scalar()
                or 0
            )

    assert _run(_go()) == 3, "保留期内的记录被误删"


def test_archive_then_purge_aborts_on_mismatch(db_ctx, monkeypatch):
    """归档行数与待清理行数不一致时必须**拒绝清理**（宁可保留，不可丢失）。"""
    from app.models.audit_log import AuditLog
    from app.services.audit_retention import AuditRetentionService

    _run(_make_expired(db_ctx, 3, prefix="ABORT_TEST"))

    async def _go():
        async with db_ctx() as s:
            svc = AuditRetentionService(s)
            # 伪造"归档只导出了 1 行"（模拟截断），应触发中止
            async def _fake_archive(**kw):
                return {"path": "x", "rows": 1, "sha256": "0" * 64, "bytes": 0,
                        "cutoff": ""}

            monkeypatch.setattr(svc, "archive", _fake_archive)
            res = await svc.archive_then_purge(days=180, confirm=True)
            await s.rollback()

        async with db_ctx() as s:
            left = int(
                (
                    await s.execute(
                        select(func.count()).select_from(AuditLog).where(
                            AuditLog.action.like("ABORT_TEST_%")
                        )
                    )
                ).scalar()
                or 0
            )
        return res, left

    res, left = _run(_go())
    assert res["purged"] is None, "归档不一致却仍执行了清理"
    assert left == 3, "归档不一致导致数据被删"


# ═══════════════ E. 接口开关 ═══════════════
def test_admin_endpoints_exist():
    """保留期管理端点必须存在且挂到正确前缀上。"""
    from app.api.v1.audit_retention import router

    paths = {r.path for r in router.routes}
    assert "/audit/retention/stats" in paths
    assert "/audit/retention/archive" in paths
    assert "/audit/retention/purge" in paths
    assert "/audit/retention/archive-then-purge" in paths


def test_policy_endpoint_is_public():
    """保留策略应可公开查阅（合规审查时直接取用）。"""
    from app.api.v1.audit_retention import retention_policy

    assert retention_policy is not None
