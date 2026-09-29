"""任务原子认领与僵尸回收的单元测试（防多副本重复执行）。

这些用例守护的是**多副本正确性**：早期实现「读到即执行」，
两个副本会重复执行同一 Job、重复消耗 AI 额度。改动 claim 逻辑时
务必保证本文件全绿。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import JobStatus, JobType
from app.models.job import Job


@pytest.fixture()
async def session():
    """独立内存库会话：建表后交出，用完整表以隔离用例。"""
    from app import models  # noqa: F401
    from app.database import Base, async_session_factory, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as s:
        yield s


async def _make_job(session, status: JobStatus = JobStatus.PENDING, **kw) -> Job:
    job = Job(
        job_type=JobType.CASE_ANALYSIS,
        ref_type="case",
        ref_id=1,
        status=status,
        tenant_id="platform",
        input_payload={},
        **kw,
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    return job


@pytest.mark.asyncio
async def test_claim_succeeds_for_pending(session):
    from app.services.job_service import claim_job

    job = await _make_job(session)
    claimed = await claim_job(session, job.id, "runner-A")

    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    assert claimed.claimed_by == "runner-A"
    assert claimed.started_at is not None
    assert claimed.heartbeat_at is not None


@pytest.mark.asyncio
async def test_claim_is_exclusive(session):
    """核心用例：同一 Job 只能被认领一次。"""
    from app.database import async_session_factory
    from app.services.job_service import claim_job

    job = await _make_job(session)
    async with async_session_factory() as s1, async_session_factory() as s2:
        r1, r2 = await asyncio.gather(
            claim_job(s1, job.id, "runner-A"),
            claim_job(s2, job.id, "runner-B"),
        )

    winners = [r for r in (r1, r2) if r is not None]
    assert len(winners) == 1, "并发认领必须只有一个成功，否则会重复执行"


@pytest.mark.asyncio
async def test_claim_rejects_terminal_states(session):
    from app.services.job_service import claim_job

    for st in (JobStatus.COMPLETED, JobStatus.FAILED):
        job = await _make_job(session, status=st)
        assert await claim_job(session, job.id, "runner-X") is None


@pytest.mark.asyncio
async def test_claim_rejects_running_by_default(session):
    """RUNNING 意味着已有实例在跑，默认不可抢占。"""
    from app.services.job_service import claim_job

    job = await _make_job(session, status=JobStatus.RUNNING)
    assert await claim_job(session, job.id, "runner-Y") is None


@pytest.mark.asyncio
async def test_claim_allows_running_when_explicit(session):
    """显式允许时才可接管 RUNNING（供确认僵尸后使用）。"""
    from app.services.job_service import claim_job

    job = await _make_job(session, status=JobStatus.RUNNING)
    claimed = await claim_job(session, job.id, "runner-Z", allow_running=True)
    assert claimed is not None
    assert claimed.claimed_by == "runner-Z"


@pytest.mark.asyncio
async def test_claim_retryable(session):
    """RETRYING 状态可被重新认领。"""
    from app.services.job_service import claim_job

    job = await _make_job(session, status=JobStatus.RETRYING)
    assert await claim_job(session, job.id, "runner-A") is not None


def _naive(dt: datetime) -> datetime:
    """归一化时间戳：SQLite 不存时区，回读会丢 tzinfo，比较前先抹平。"""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


@pytest.mark.asyncio
async def test_started_at_not_overwritten_on_reclaim(session):
    from app.services.job_service import claim_job

    job = await _make_job(session)
    first = await claim_job(session, job.id, "runner-A")
    assert first.started_at is not None
    started = first.started_at

    # 模拟重试：置回 RETRYING 后再次认领
    first.status = JobStatus.RETRYING
    await session.commit()
    second = await claim_job(session, job.id, "runner-B")

    assert second.started_at is not None
    assert _naive(second.started_at) == _naive(started), "重试不应覆盖首次开始时间"


@pytest.mark.asyncio
async def test_recover_stale_jobs(session):
    """僵尸任务（无心跳超时）应被回收为 PENDING 并清空 claimed_by。"""
    from app.services.job_service import recover_stale_jobs

    stale = await _make_job(
        session,
        status=JobStatus.RUNNING,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=9999),
        claimed_by="dead",
    )
    fresh = await _make_job(
        session,
        status=JobStatus.RUNNING,
        heartbeat_at=datetime.now(timezone.utc),
        claimed_by="alive",
    )

    n = await recover_stale_jobs(session, older_than_seconds=300)
    assert n == 1

    await session.refresh(stale)
    await session.refresh(fresh)
    assert stale.status == JobStatus.PENDING
    assert stale.claimed_by is None
    assert fresh.status == JobStatus.RUNNING, "心跳正常的任务不得被误回收"


@pytest.mark.asyncio
async def test_recover_ignores_jobs_without_heartbeat(session):
    """从未开始执行（heartbeat 为 NULL）的任务不应被回收逻辑误判。"""
    from app.services.job_service import recover_stale_jobs

    await _make_job(session, status=JobStatus.PENDING)
    n = await recover_stale_jobs(session, older_than_seconds=300)
    assert n == 0


@pytest.mark.asyncio
async def test_queue_backpressure_does_not_drop():
    """队列满时应反压等待，而非静默丢弃任务。"""
    from app.services.job_service import JobQueue

    q = JobQueue(max_concurrency=1, queue_size=2)
    await q.enqueue(1, lambda *a: None)
    await q.enqueue(2, lambda *a: None)

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.enqueue(3, lambda *a: None), timeout=0.3)

    assert q.pending == 2, "反压后队列不应增长也不应丢任务"


@pytest.mark.asyncio
async def test_queue_stop_is_clean():
    from app.services.job_service import JobQueue

    q = JobQueue(max_concurrency=2, queue_size=4)
    await q.start()
    assert len(q._workers) == 2
    await q.stop(timeout=1.0)
    assert q._workers == []
    assert q._running is False


# ---------------------------------------------------------------------------
# 僵尸回收的重试预算（2026-09-20 复核新增）
#
# `run_job` 的进程内重试受 `JOB_MAX_RETRIES` 约束，但 `recover_stale_jobs`
# 把任务重置为 PENDING 时**不动 `retry_count`**，下一次 `run_job` 的 `attempt`
# 又从 0 开始并把 `retry_count` 覆盖写回 1 ⇒ 必然失败的任务会在
# RUNNING →(心跳超时)→ PENDING → RUNNING 之间无限循环，永不进 FAILED。
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recovery_does_not_revive_exhausted_job(session):
    """已耗尽重试预算的僵尸任务，回收后不得回到可执行状态。

    否则「重试上限」形同虚设：一个必然失败的任务会无限重启，持续占用
    worker 与 AI 额度，且永远不会进入 FAILED 让上层可见。
    """
    from app.config import settings
    from app.services.job_service import recover_stale_jobs

    job = await _make_job(
        session,
        status=JobStatus.RUNNING,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=9999),
        claimed_by="dead",
    )
    job.retry_count = settings.JOB_MAX_RETRIES
    await session.commit()

    await recover_stale_jobs(session, older_than_seconds=300)
    await session.refresh(job)

    assert job.status != JobStatus.PENDING, (
        f"重试已耗尽（retry_count={job.retry_count} >= JOB_MAX_RETRIES="
        f"{settings.JOB_MAX_RETRIES}）的任务被回收成了 PENDING，会无限重启"
    )
    assert job.status == JobStatus.FAILED, (
        f"预期标记为 FAILED 以便上层可见，实际：{job.status}"
    )


@pytest.mark.asyncio
async def test_recovery_consumes_retry_budget(session):
    """每次回收消耗一次重试预算（retry_count 递增），不重置、不静默归零。"""
    from app.services.job_service import recover_stale_jobs

    job = await _make_job(
        session,
        status=JobStatus.RUNNING,
        heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=9999),
        claimed_by="dead",
    )
    before = job.retry_count

    await recover_stale_jobs(session, older_than_seconds=300)
    await session.refresh(job)

    assert job.status == JobStatus.PENDING, "预算未耗尽时应正常回收为 PENDING"
    assert job.retry_count == before + 1, (
        f"回收必须消耗一次重试预算：回收前 {before}，回收后 {job.retry_count}"
    )


@pytest.mark.asyncio
async def test_recovery_loop_is_started_in_lifespan():
    """接线判据：`main.py` 必须把僵尸巡检作为常驻任务启动。

    少了这行，`recover_stale_jobs` 永远不会被调用，而所有单元测试照样全绿
    （它们直接调函数）⇒ 「函数有判据」不等于「调度有判据」。
    """
    import ast
    from pathlib import Path

    import app as app_pkg

    main_path = Path(app_pkg.__file__).resolve().parent / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))

    started = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "create_task"
        and any(
            isinstance(a, ast.Call)
            and isinstance(a.func, ast.Name)
            and a.func.id == "_stale_job_recovery_loop"
            for a in n.args
        )
    ]
    assert started, (
        "`app/main.py` 里找不到 `asyncio.create_task(_stale_job_recovery_loop())`。"
        "僵尸巡检不启动 ⇒ 崩溃遗留的 RUNNING 任务永不回收，且不报任何错。"
    )
