"""第三轮可靠性/安全加固验证：任务原子认领 + 队列化 + 限流加固。

覆盖：
  R-1  原子认领：并发认领同一 Job，只有一个成功（防多副本重复执行）
  R-2  终态保护：已 COMPLETED / FAILED 的 Job 不可被认领
  R-3  断点续跑：worker 抛错后重试，step_state 保留
  R-4  僵尸回收：超时无心跳的 RUNNING 任务被重置为 PENDING
  R-5  队列：入队 → 常驻消费 → Job 落 COMPLETED；满队列反压不丢弃
  R-6  优雅停机：stop() 后不再消费新任务
  R-7  限流默认开启：未显式关闭时生效
  R-8  限流分档：生成/上传端点使用更严阈值
  R-9  限流内存后端：超阈值返回 429 + Retry-After
  R-10 限流内存态清理：key 数量不随请求无界增长

用法：python verify_reliability_round3.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./storage/verify_r3.db"
os.environ["DATABASE_URL_SYNC"] = "sqlite:///./storage/verify_r3.db"
os.environ["ENVIRONMENT"] = "development"
os.environ["SECRET_KEY"] = "verify-only-local-strong-secret-key-32chars"
os.environ["DEBUG"] = "false"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f"  -> {detail}" if detail else ""))


async def _prepare() -> None:
    from app import models  # noqa: F401
    from app.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def main() -> int:
    import inspect

    from app.config import settings
    from app.middleware import (
        AUTH_RATE_LIMITED_PATHS,
        GENERATE_PATH_SUFFIXES,
        UPLOAD_PATH_SUFFIXES,
        RateLimitMiddleware,
    )
    from app.services import job_service as js

    asyncio.run(_prepare())

    # ------------------------------------------------------------ R-7/R-8 配置与分档
    check(
        "R-7 限流默认开启（安全默认值）",
        settings.RATE_LIMIT_ENABLED is True,
        f"RATE_LIMIT_ENABLED={settings.RATE_LIMIT_ENABLED}",
    )
    check(
        "R-8a 认证端点分档存在",
        "/api/v1/auth/login" in AUTH_RATE_LIMITED_PATHS
        and "/api/v1/auth/register" in AUTH_RATE_LIMITED_PATHS,
        f"{len(AUTH_RATE_LIMITED_PATHS)} 个",
    )
    check(
        "R-8b 上传档后缀已定义",
        "evidence/cases" in "".join(UPLOAD_PATH_SUFFIXES) or len(UPLOAD_PATH_SUFFIXES) > 0,
        f"{UPLOAD_PATH_SUFFIXES}",
    )
    check(
        "R-8c 生成档后缀已定义",
        "/generate" in GENERATE_PATH_SUFFIXES,
        f"{GENERATE_PATH_SUFFIXES}",
    )

    mw_src = inspect.getsource(RateLimitMiddleware)
    check(
        "R-8d 限流器按路径解析不同阈值",
        "_resolve_limit" in mw_src and "self.upload_max" in mw_src,
        "",
    )
    check(
        "R-8e 限流器支持可插拔后端（Redis/内存）",
        "self.backend" in mw_src and "build_backend" in inspect.getsource(sys.modules["app.middleware"]),
        "",
    )

    # ------------------------------------------------------------ R-9/R-10 内存后端
    from app.core.rate_limit_backend import MemoryRateLimitBackend

    async def _test_memory_backend():
        be = MemoryRateLimitBackend()
        key = "1.2.3.4:/x"
        allowed_flags = []
        for _ in range(5):
            ok_, _ra = await be.hit(key, 3, 60)
            allowed_flags.append(ok_)
        return allowed_flags

    flags = asyncio.run(_test_memory_backend())
    check(
        "R-9 内存后端：超过阈值后拒绝",
        flags == [True, True, True, False, False],
        f"flags={flags}",
    )

    async def _test_retry_after():
        be = MemoryRateLimitBackend()
        key = "1.2.3.4:/y"
        for _ in range(3):
            await be.hit(key, 3, 60)
        allowed, ra = await be.hit(key, 3, 60)
        return allowed, ra

    allowed, ra = asyncio.run(_test_retry_after())
    check("R-9b 被拒时返回正向 Retry-After", (not allowed) and ra > 0, f"retry_after={ra}")

    async def _test_sweep():
        from collections import deque as _dq

        be = MemoryRateLimitBackend()
        # 200 个「早已过期」的 key（时间戳取 1 小时前）
        stale_ts = time.time() - 3600
        for i in range(200):
            be._hits[f"stale{i}:/p"].append(stale_ts)
        # 模拟巡检间隔已到（wall-clock 方式）
        be._last_sweep = time.time() - 120
        await be.hit("fresh:/p", 3, 60)
        return len(be._hits)

    remaining = asyncio.run(_test_sweep())
    check(
        "R-10 内存后端会清理过期 key（防无界增长）",
        remaining == 1,
        f"清理后仅剩 {remaining} 个活跃 key（原 200 个过期）",
    )

    # ------------------------------------------------------------ R-1 ~ R-6 任务可靠性
    from app.models.enums import JobStatus, JobType
    from app.models.job import Job
    from app.database import async_session_factory
    from sqlalchemy import select

    async def _make_job(status: JobStatus = JobStatus.PENDING) -> int:
        async with async_session_factory() as s:
            j = Job(
                job_type=JobType.CASE_ANALYSIS,
                ref_type="case",
                ref_id=1,
                status=status,
                tenant_id="platform",
                input_payload={},
            )
            s.add(j)
            await s.commit()
            await s.refresh(j)
            return j.id

    # R-1 并发认领同一 Job
    async def _test_concurrent_claim():
        jid = await _make_job()
        async with async_session_factory() as s1, async_session_factory() as s2:
            r = await asyncio.gather(
                js.claim_job(s1, jid, "runner-A"),
                js.claim_job(s2, jid, "runner-B"),
                return_exceptions=True,
            )
        winners = [x for x in r if isinstance(x, Job) and x is not None]
        return jid, winners, r

    jid1, winners, raw = asyncio.run(_test_concurrent_claim())
    check(
        "R-1 并发认领同一 Job 只有一个成功",
        len(winners) == 1,
        f"成功数={len(winners)}, 结果类型={[type(x).__name__ for x in raw]}",
    )
    if winners:
        check(
            "R-1b 认领成功者被标记 claimed_by",
            winners[0].claimed_by in ("runner-A", "runner-B"),
            f"claimed_by={winners[0].claimed_by}",
        )

    # R-2 终态不可认领
    async def _test_terminal_protection():
        out = {}
        for st in (JobStatus.COMPLETED, JobStatus.FAILED):
            jid = await _make_job(st)
            async with async_session_factory() as s:
                r = await js.claim_job(s, jid, "runner-X")
            out[st.value] = r is None
        return out

    term = asyncio.run(_test_terminal_protection())
    check(
        "R-2 已完成/已失败的任务不可被认领",
        all(term.values()),
        f"{term}",
    )

    # R-4 僵尸回收
    async def _test_stale_recovery():
        from datetime import datetime, timedelta, timezone

        async with async_session_factory() as s:
            stale = Job(
                job_type=JobType.CASE_ANALYSIS,
                ref_type="case",
                ref_id=9,
                status=JobStatus.RUNNING,
                tenant_id="platform",
                heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=9999),
                claimed_by="dead-runner",
            )
            fresh = Job(
                job_type=JobType.CASE_ANALYSIS,
                ref_type="case",
                ref_id=10,
                status=JobStatus.RUNNING,
                tenant_id="platform",
                heartbeat_at=datetime.now(timezone.utc),
                claimed_by="alive-runner",
            )
            s.add_all([stale, fresh])
            await s.commit()
            await s.refresh(stale)
            await s.refresh(fresh)
            stale_id, fresh_id = stale.id, fresh.id

        async with async_session_factory() as s:
            n = await js.recover_stale_jobs(s, older_than_seconds=300)

        async with async_session_factory() as s:
            st_row = (await s.execute(select(Job).where(Job.id == stale_id))).scalars().first()
            fr_row = (await s.execute(select(Job).where(Job.id == fresh_id))).scalars().first()
        return n, st_row.status, fr_row.status, st_row.claimed_by

    n, stale_status, fresh_status, stale_claimed = asyncio.run(_test_stale_recovery())
    check("R-4a 僵尸任务被回收为 PENDING", stale_status == JobStatus.PENDING, f"status={stale_status}")
    check("R-4b 回收时清空 claimed_by", stale_claimed is None, f"claimed_by={stale_claimed}")
    check("R-4c 心跳正常的任务不被误回收", fresh_status == JobStatus.RUNNING, f"status={fresh_status}")

    # R-3 + R-5 队列执行与断点续跑
    async def _test_queue_and_resume():
        jid = await _make_job()
        attempts = {"n": 0}
        seen_state = {"value": None}

        async def _flaky_worker(job: Job, db) -> None:
            attempts["n"] += 1
            seen_state["value"] = dict(job.step_state or {})
            # 首次失败，第二次成功（验证重试 + step_state 保留）
            job.step_state = {"partial": True}
            await db.commit()
            if attempts["n"] == 1:
                raise RuntimeError("模拟首次失败")

        q = js.JobQueue(max_concurrency=1, queue_size=10)
        await q.start()
        await q.enqueue(jid, _flaky_worker)
        # 等待消费完成（最多 10s）
        for _ in range(100):
            async with async_session_factory() as s:
                row = (await s.execute(select(Job).where(Job.id == jid))).scalars().first()
            if row is not None and row.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                break
            await asyncio.sleep(0.1)
        await q.stop()
        return row.status, attempts["n"], row.step_state

    q_status, q_attempts, q_state = asyncio.run(_test_queue_and_resume())
    check("R-5a 队列能消费任务并落 COMPLETED", q_status == JobStatus.COMPLETED, f"status={q_status}")
    check("R-3 失败后自动重试（≥2 次尝试）", q_attempts >= 2, f"attempts={q_attempts}")
    check(
        "R-3b 重试时 step_state 保留（断点续跑）",
        isinstance(q_state, dict) and q_state.get("partial") is True,
        f"step_state={q_state}",
    )

    # R-6 优雅停机
    async def _test_stop():
        q = js.JobQueue(max_concurrency=1, queue_size=10)
        await q.start()
        await q.stop(timeout=1.0)
        return q._running, len(q._workers)

    running, worker_count = asyncio.run(_test_stop())
    check(
        "R-6 优雅停机后不再消费新任务",
        running is False and worker_count == 0,
        f"running={running}, workers={worker_count}",
    )

    # R-5b 队列满时反压（不丢弃）
    async def _test_backpressure():
        q = js.JobQueue(max_concurrency=1, queue_size=2)
        # 不 start，队列无消费者，填满 2 个
        await q.enqueue(1, lambda *a: None)
        await q.enqueue(2, lambda *a: None)
        # 第 3 个应阻塞 —— 用超时验证「等待而非丢弃」
        try:
            await asyncio.wait_for(q.enqueue(3, lambda *a: None), timeout=0.5)
            return "not_blocked", q.pending
        except asyncio.TimeoutError:
            return "blocked", q.pending

    bp, pending = asyncio.run(_test_backpressure())
    check(
        "R-5b 队列满时反压等待而非丢弃任务",
        bp == "blocked" and pending == 2,
        f"{bp}, pending={pending}",
    )

    # R-11 run_job 不再直接暴露（改由队列消费），且已移除 BackgroundTasks 依赖
    jh_src = open("app/services/job_handlers.py", encoding="utf-8").read()
    check(
        "R-11 任务投递已从 BackgroundTasks 改为常驻队列",
        "background.add_task" not in jh_src and "job_queue.enqueue" in jh_src,
        "",
    )


    # ------------------------------------------------------------ R-12 Redis 降级
    async def _test_redis_fallback():
        from app.core.rate_limit_backend import RedisRateLimitBackend

        be = RedisRateLimitBackend("redis://127.0.0.1:9999/0")  # 故意指向不存在的实例
        r = [await be.hit("k", 2, 60) for _ in range(3)]
        return r, be._degraded

    r_series, degraded = asyncio.run(_test_redis_fallback())
    check(
        "R-12a Redis 不可用时自动降级（不阻断登录注册）",
        degraded is True and r_series[0][0] is True,
        f"degraded={degraded}, 首次={r_series[0]}",
    )
    check(
        "R-12b 降级后仍按内存阈值拒绝",
        r_series[2][0] is False,
        f"第三次={r_series[2]}",
    )

    print("-" * 66)
    failed = [x for x in results if not x[1]]
    print(f"总计 {len(results)} 项，通过 {len(results) - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        for nm, _, d in failed:
            print(f"  [FAIL] {nm}  {d}")
        return 1
    print(">>> 第三轮可靠性加固全部验证通过 <<<")
    return 0


if __name__ == "__main__":
    sys.exit(main())
