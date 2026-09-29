"""异步任务服务：先落库再执行 + **原子认领** + 指数退避 + 断点续跑。

可靠性设计（相对早期实现的修正）：
- **原子认领**（`claim_job`）：用 `UPDATE ... WHERE status IN (PENDING, RUNNING)`
  的 rowcount 做抢占，确保多副本 / 重试并发下**只有一个执行者**能推进同一 Job。
  早期实现是「读到即执行」，多副本会重复执行、重复计费。
- **心跳 + 僵尸回收**：RUNNING 期间刷新 `heartbeat_at`；超过阈值未刷新视为
  进程崩溃遗留，由回收逻辑重置为 PENDING 重新入队。
- **先落库再执行**：任何重计算前先建 Job 行，崩溃不丢用户输入。
- **断点续跑**：worker 从 `job.step_state` 恢复，而非每次重头。
"""
import asyncio
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.metrics import metrics
from app.database import async_session_factory
from app.models.enums import JobType
from app.models.job import Job, JobStatus

# 任务类型 -> 重跑处理器注册表（product 任务的 service 在启动时注册）
JOB_HANDLERS: dict[str, Callable[[Job, AsyncSession], Awaitable[None]]] = {}

#: 终态：不可再被推进的状态
TERMINAL_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED}


def register_job_handler(
    job_type: str,
    worker: Callable[[Job, AsyncSession], Awaitable[None]],
) -> None:
    """注册某类 Job 的重跑 worker，供 /jobs/{id}/retry 调用。"""
    JOB_HANDLERS[job_type] = worker


def _runner_id() -> str:
    """本执行者标识，用于排障时定位任务由哪个实例执行。"""
    return f"{socket.gethostname()}:{os.getpid()}"


#: 本进程标识（模块加载时确定，避免每个任务重复取 hostname）
RUNNER_ID = _runner_id()


class JobService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enqueue(
        self,
        job_type: JobType,
        *,
        ref_type: Optional[str] = None,
        ref_id: Optional[int] = None,
        input_payload: Optional[dict] = None,
        tenant_id: str = "platform",
    ) -> Job:
        """先落库再执行：重计算前先建 Job 行，崩溃不丢输入。"""
        job = Job(
            job_type=job_type,
            ref_type=ref_type,
            ref_id=ref_id,
            status=JobStatus.PENDING,
            input_payload=input_payload or {},
            tenant_id=tenant_id,
        )
        self.db.add(job)
        await self.db.flush()
        return job

    async def get(self, job_id: int) -> Optional[Job]:
        return (
            await self.db.execute(select(Job).where(Job.id == job_id))
        ).scalars().first()


async def claim_job(
    db: AsyncSession,
    job_id: int,
    runner_id: str = RUNNER_ID,
    *,
    allow_running: bool = False,
) -> Optional[Job]:
    """**原子认领**：仅当 Job 处于「待执行」状态时抢占成功。

    通过单条 `UPDATE ... WHERE status IN ('pending','retrying')` 的 rowcount
    判定归属——这是跨进程安全的乐观锁。多副本同时消费同一 Job 时，
    只有一个实例能拿到 rowcount=1，其余拿到 0 并静默退出。

    `allow_running=True` 时额外允许抢占 `RUNNING` 状态（用于接管**已确认僵尸**
    的任务）。默认 False：`RUNNING` 意味着已有实例在跑，绝不重复执行。
    僵尸任务的正确回收路径是 `recover_stale_jobs()` 先重置为 PENDING，
    再由队列重新投递。

    返回认领成功的 Job；若已完成 / 已失败 / 被他人抢占，返回 None。
    """
    now = datetime.now(timezone.utc)
    claimable = [JobStatus.PENDING.value, JobStatus.RETRYING.value]
    if allow_running:
        claimable.append(JobStatus.RUNNING.value)

    result = await db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(claimable))
        .values(
            status=JobStatus.RUNNING.value,
            claimed_by=runner_id,
            heartbeat_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()

    if result.rowcount == 0:
        # 已被他人认领 / 已终态 —— 不是错误，静默退出即可
        logger.debug("Job {} 认领失败（已被处理或已终态），跳过", job_id)
        return None

    # UPDATE 用了 synchronize_session=False，session 身份映射里可能残留
    # 认领前的旧对象（例如调用方先读过这个 Job）。必须先失效再取，
    # 否则调用方拿到的是 status=PENDING 的**陈旧对象**，会误判认领失败
    # 而重复入队 —— 这正是「读到即执行」时代重复执行的根因。
    db.expire_all()
    job = (
        await db.execute(select(Job).where(Job.id == job_id))
    ).scalars().first()
    # 仅首次执行时记录开始时间（`started_at` 为 NULL 才写，避免重试覆盖）
    if job is not None and job.started_at is None:
        job.started_at = now
        await db.commit()
    return job


async def recover_stale_jobs(db: AsyncSession, older_than_seconds: Optional[int] = None) -> int:
    """回收僵尸任务：把长时间无心跳的 RUNNING 任务重置为 PENDING。

    进程崩溃 / 被 kill 时任务会永远停在 RUNNING，没有本机制就需要人工介入。

    **回收要消耗重试预算**（2026-09-20 修复）：原实现只重置状态、不动
    `retry_count`，而 `run_job` 的 `attempt` 每次从 0 重来 ⇒ 一个必然失败的
    任务会在 `RUNNING →(心跳超时)→ PENDING → RUNNING` 之间**无限重启**，
    永不进 FAILED，`JOB_MAX_RETRIES` 上限在回收这条路上完全失效。
    现改为：每次回收 `retry_count + 1`；已达上限的僵尸直接标记 FAILED，
    让它对上层可见，而不是静默无限循环。

    返回**重置为 PENDING** 的任务数（标记 FAILED 的不计在内）。
    """
    threshold = older_than_seconds or settings.JOB_STALE_TIMEOUT_SECONDS
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold)
    stale = (
        Job.status == JobStatus.RUNNING.value,
        Job.heartbeat_at.isnot(None),
        Job.heartbeat_at < cutoff,
    )

    # ① 预算已耗尽：不再重启，直接判失败（否则会无限重启）
    exhausted = await db.execute(
        update(Job)
        .where(*stale, Job.retry_count >= settings.JOB_MAX_RETRIES)
        .values(
            status=JobStatus.FAILED.value,
            finished_at=datetime.now(timezone.utc),
            error_message=(
                f"僵尸任务重试预算耗尽（已重试 >= {settings.JOB_MAX_RETRIES} 次），"
                "停止自动重启，需人工介入或重新入队"
            ),
        )
        .execution_options(synchronize_session=False)
    )

    # ② 仍有预算：重置为 PENDING，并消耗一次预算
    result = await db.execute(
        update(Job)
        .where(*stale, Job.retry_count < settings.JOB_MAX_RETRIES)
        .values(
            status=JobStatus.PENDING.value,
            claimed_by=None,
            retry_count=Job.retry_count + 1,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if result.rowcount or exhausted.rowcount:
        # 同上：批量 UPDATE 绕过身份映射，调用方若持有旧对象会读到 stale 状态
        db.expire_all()
        logger.warning("回收僵尸任务 {} 个（超过 {}s 无心跳）", result.rowcount, threshold)
    if exhausted.rowcount:
        logger.error(
            "僵尸任务 {} 个重试预算已耗尽，已标记 FAILED（不再自动重启）", exhausted.rowcount
        )
        metrics.job_queue_completed_total.inc(("failed",))
    return result.rowcount


async def run_job(
    job_id: int,
    worker: Callable[[Job, AsyncSession], Awaitable[None]],
) -> None:
    """执行单个 Job：**原子认领** + 指数退避重试 + 断点续跑 + 进度 / 心跳刷新。

    worker 负责推进 job（更新 step_state / 进度 / 业务产物）。worker 应从
    `job.step_state` 恢复进度，而非每次重头——实现断点续跑。
    """
    async with async_session_factory() as db:
        job = await claim_job(db, job_id)
        if job is None:
            return  # 已被他人认领或已终态

        job.mark_running(job.step_name or "start")
        await db.commit()

        for attempt in range(settings.JOB_MAX_RETRIES + 1):
            try:
                await worker(job, db)
                job.mark_completed()
                await db.commit()
                metrics.job_queue_completed_total.inc(("succeeded",))
                logger.info("Job {} 执行成功（attempt={}）", job_id, attempt + 1)
                return
            except Exception as exc:  # noqa: BLE001 单任务异常不拖垮 worker 循环
                await db.rollback()
                # 重新取一次，避免 rollback 后对象过期
                job = (
                    await db.execute(select(Job).where(Job.id == job_id))
                ).scalars().first()
                if job is None:
                    logger.warning("Job {} 在重试期间消失", job_id)
                    return

                job.error_message = str(exc)
                # 累加而非覆盖：僵尸回收也会 +1，覆盖写法会把回收累计的预算抹回去，
                # 让「必然失败的任务」在 回收→重启→崩溃 之间循环不止。
                job.retry_count = (job.retry_count or 0) + 1
                job.touch_heartbeat()

                if attempt < settings.JOB_MAX_RETRIES:
                    job.status = JobStatus.RETRYING
                    await db.commit()
                    metrics.job_queue_completed_total.inc(("retried",))
                    delay = settings.JOB_RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Job {} 第{}次失败，{}s 后重试: {}", job_id, attempt + 1, delay, exc
                    )
                    await asyncio.sleep(delay)
                    continue

                job.status = JobStatus.FAILED
                job.finished_at = datetime.now(timezone.utc)
                await db.commit()
                metrics.job_queue_completed_total.inc(("failed",))
                logger.error("Job {} 重试耗尽，标记失败: {}", job_id, exc)
                return


# --------------------------------------------------------------------------
# 进程内任务队列
# --------------------------------------------------------------------------
class JobQueue:
    """进程内异步任务队列 + 常驻消费协程。

    替代 `BackgroundTasks.add_task`：后者的任务是**请求级**的，请求结束即被
    FastAPI 回收，长任务可能被中断；且没有并发上限与优雅停机。

    设计取舍：
    - 任务**先落库**（`enqueue`），入队失败也不丢任务（可由回收逻辑重新入队）。
    - 队列满时**反压等待**而非丢弃，保证不静默丢任务。
    - 多副本下各实例独立消费，靠 `claim_job` 的原子认领保证不重复执行。
    """

    def __init__(
        self,
        *,
        max_concurrency: Optional[int] = None,
        queue_size: Optional[int] = None,
    ) -> None:
        self.max_concurrency = max_concurrency or settings.JOB_MAX_CONCURRENCY
        self._queue: asyncio.Queue[tuple[int, Callable[[Job, AsyncSession], Awaitable[None]]]] = (
            asyncio.Queue(maxsize=queue_size or settings.JOB_QUEUE_MAX_SIZE)
        )
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def enqueue(
        self,
        job_id: int,
        worker: Callable[[Job, AsyncSession], Awaitable[None]],
    ) -> None:
        """投递任务。队列满时等待（反压），不丢弃。"""
        await self._queue.put((job_id, worker))

    async def _consume(self) -> None:
        while self._running:
            try:
                job_id, worker = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await run_job(job_id, worker)
            except Exception as exc:  # noqa: BLE001 兜底，绝不能让消费循环退出
                logger.exception("Job {} 消费异常: {}", job_id, exc)
            finally:
                self._queue.task_done()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._consume(), name=f"job-worker-{i}")
            for i in range(self.max_concurrency)
        ]
        logger.info("任务队列已启动：{} 个并发消费者", self.max_concurrency)

    async def stop(self, timeout: float = 10.0) -> None:
        """优雅停机：停止领取新任务，等待在途任务完成。"""
        if not self._running:
            return
        self._running = False
        for t in self._workers:
            t.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        pending = self._queue.qsize()
        if pending:
            logger.warning("队列中仍有 {} 个未消费任务（已落库，可由回收逻辑重新执行）", pending)
        logger.info("任务队列已停止")

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def refresh_metrics(self) -> None:
        """把队列当前积压同步到指标（由 /metrics 抓取时调用）。"""
        metrics.job_queue_pending.set(self._queue.qsize())


#: 全局队列单例
job_queue = JobQueue()
