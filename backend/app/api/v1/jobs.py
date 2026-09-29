"""异步任务接口：进度查询与失败重试。"""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_tenant_context
from app.core.errors import BadRequestError, ErrorCode, NotFoundError
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.models.enums import JobStatus
from app.models.job import Job
from app.schemas.job import JobSchema
from app.services.job_service import JOB_HANDLERS, JobService, job_queue

router = APIRouter(prefix="/jobs", tags=["异步任务"])


@router.get("", summary="任务列表（可按状态过滤，用于发现失败/僵尸任务）")
async def list_jobs(
    params: PaginationParams = Depends(),
    status: Optional[str] = Query(None, description="按状态过滤，如 FAILED"),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """Q-H（2026-09-21 裁定）：给失败/僵尸任务一个**可见**入口。

    此前只有 `GET /{job_id}` 与 `POST /{job_id}/retry` —— 要先**知道 id** 才能看、
    才能重试。而僵尸任务（重试 3 次后判 FAILED）恰恰是「没人知道它在」的那一类：
    失败任务静默堆积、无人可见，重试入口形同虚设。

    ⚠️ 必须按 `ctx.tenant_id` 过滤：任务表里存着 `input_payload` /
    `output_payload`（含文书正文、解析结果等），跨租户列出即为数据泄露。
    """
    base = select(Job).where(Job.tenant_id == ctx.tenant_id)
    if status:
        base = base.where(Job.status == status)

    total, is_lower_bound = await count_bounded(db, base)
    rows = list(
        (
            await db.execute(
                base.order_by(Job.id.desc()).offset(params.offset).limit(params.limit)
            )
        ).scalars().all()
    )
    page = Page.build(
        [JobSchema.model_validate(r).model_dump() for r in rows],
        total,
        params,
        total_is_lower_bound=is_lower_bound,
    )
    return ok(page.model_dump())


@router.get("/{job_id}", summary="任务进度")
async def job_status(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    svc = JobService(db)
    job = await svc.get(job_id)
    if job is None or job.tenant_id != ctx.tenant_id:
        raise NotFoundError("任务不存在", code=ErrorCode.JOB_NOT_FOUND)
    return ok(JobSchema.model_validate(job).model_dump())


@router.post("/{job_id}/retry", summary="重试失败任务")
async def retry(
    job_id: int,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    svc = JobService(db)
    job = await svc.get(job_id)
    if job is None or job.tenant_id != ctx.tenant_id:
        raise NotFoundError("任务不存在", code=ErrorCode.JOB_NOT_FOUND)
    if job.status != JobStatus.FAILED:
        raise BadRequestError("仅失败任务可重试", code=ErrorCode.JOB_NOT_RETRYABLE)

    worker = JOB_HANDLERS.get(job.job_type.value)
    if worker is None:
        raise BadRequestError("未注册重跑处理器", code=ErrorCode.JOB_NOT_RETRYABLE)

    job.status = JobStatus.PENDING.value
    job.error_message = None
    job.retry_count = 0
    await db.commit()

    # 投入常驻队列（run_job 内部原子认领 + 开新会话，支持断点续跑）
    await job_queue.enqueue(job.id, worker)
    return ok({"job_id": job.id, "status": "pending"})
