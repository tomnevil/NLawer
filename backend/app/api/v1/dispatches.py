"""派单接口：列表 / 接单 / 抢单。"""
from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context
from app.core.errors import ErrorCode, NotFoundError
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.models.case import Dispatch
from app.models.enums import DispatchStatus
from app.schemas.case import DispatchOut
from app.services.dispatch_service import DispatchService
from app.services.job_handlers import trigger_case_analysis

router = APIRouter(prefix="/dispatches", tags=["派单"])


async def _dispatch_or_404(db: AsyncSession, dispatch_id: int, tenant_id: str) -> Dispatch:
    """加载派单并校验租户归属（**接单 / 抢单必须经此守卫**）。

    历史缺陷（2026-09-21 由 `tests/test_dispatch_endpoint_layer.py::P4–P6` 坐实）：
    `accept` / `grab` 直接 `DispatchService(db).accept(dispatch_id, ctx.user_id)`，
    而 `DispatchService._get()` 只按主键取、**不带租户过滤**，服务层也不接收
    `tenant_id` ⇒ 租户 A 的律师凭 `dispatch_id` 即可接下**租户 B** 的派单。

    后果不是「读到了别人的数据」，而是**责任链被改写**：
    `accept()` 会同时把 `case.lawyer_id` 换成接单人、把案件推进到 `ACCEPTED`，
    并触发 `_on_assigned` / `_notify_accepted`（用租户 A 律师的名字给租户 B 的客户发通知）。

    ⚠️ 更隐蔽的是**顺序**：端点在 `db.commit()` **之后**才调 `trigger_case_analysis`
    （它内部有租户校验）⇒ 越权写已经落库，接口才返回 404。
    只盯状态码会以为「被挡住了」（`methodology.md` 76：先落盘后鉴权）。

    收口方式与 `analyses._load_or_404` / `reviews._review_or_404` / `cases._case_or_404`
    保持一致：端点层先按 `(id, tenant_id)` 取，取不到即 404。
    """
    d = await db.get(Dispatch, dispatch_id)
    if d is None or d.tenant_id != tenant_id:
        raise NotFoundError("派单不存在", code=ErrorCode.DISPATCH_NOT_FOUND)
    return d


@router.get("", response_model=dict, summary="派单列表")
async def list_dispatches(
    params: PaginationParams = Depends(),
    status: str | None = Query(None),
    mode: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    base = select(Dispatch).where(Dispatch.tenant_id == ctx.tenant_id)
    if status:
        base = base.where(Dispatch.status == status)
    if mode:
        base = base.where(Dispatch.mode == mode)

    # 有界计数：`status` / `mode` 均为 `Enum(native_enum=False)`，**没有索引**，
    # 但 tenant 索引会把工作集收窄到单租户规模，实测小租户下比值仅 1.3x（无收益）。
    # 大律所场景（单租户占全表 1/7）实测 31.73ms → 0.77ms（54.1x）。
    total, is_lower_bound = await count_bounded(db, base)
    rows = list(
        (
            await db.execute(base.order_by(Dispatch.id.desc()).offset(params.offset).limit(params.limit))
        ).scalars().all()
    )
    return ok(
        Page.build(
            [DispatchOut.model_validate(r).model_dump() for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.get("/pool", response_model=dict, summary="可抢单池（待接且未指定律师）")
async def grab_pool(
    params: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    base = select(Dispatch).where(
        Dispatch.tenant_id == ctx.tenant_id,
        Dispatch.status == DispatchStatus.PENDING,
        or_(Dispatch.lawyer_id.is_(None), Dispatch.mode == "POOL"),
    )
    # 有界计数：本池的 `or_(lawyer_id IS NULL, mode='POOL')` 是有选择性的谓词，
    # 无界 COUNT 必须在租户全部派单上逐一求值。实测（30 万行）比值 12.9x，
    # 极化为单租户时扩大到 54x。
    total, is_lower_bound = await count_bounded(db, base)
    rows = list(
        (await db.execute(base.order_by(Dispatch.id.desc()).offset(params.offset).limit(params.limit)))
        .scalars()
        .all()
    )
    return ok(
        Page.build(
            [DispatchOut.model_validate(r).model_dump() for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.post("/{dispatch_id}/accept", response_model=dict, summary="接单 / 抢单")
async def accept_dispatch(
    dispatch_id: int,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _dispatch_or_404(db, dispatch_id, ctx.tenant_id)
    disp = await DispatchService(db).accept(dispatch_id, ctx.user_id)
    # 接单改变案件责任人，属责任链关键节点，必须留痕
    await record(
        db,
        AuditAction.DISPATCH_ACCEPT,
        "dispatch",
        disp.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"case_id": disp.case_id, "lawyer_id": disp.lawyer_id, "mode": str(disp.mode)},
    )
    await db.commit()
    # 接单后自动触发六段式案件分析（异步，接口立即返回 job_id）
    job_id = await trigger_case_analysis(db, disp.case_id, ctx.tenant_id, background)
    return ok({**DispatchOut.model_validate(disp).model_dump(), "job_id": job_id})


@router.post("/{dispatch_id}/grab", response_model=dict, summary="抢单（POOL 模式）")
async def grab_dispatch(
    dispatch_id: int,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _dispatch_or_404(db, dispatch_id, ctx.tenant_id)
    disp = await DispatchService(db).grab(dispatch_id, ctx.user_id)
    await record(
        db,
        AuditAction.DISPATCH_ACCEPT,
        "dispatch",
        disp.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "case_id": disp.case_id,
            "lawyer_id": disp.lawyer_id,
            "mode": str(disp.mode),
            "via": "GRAB",
        },
    )
    await db.commit()
    job_id = await trigger_case_analysis(db, disp.case_id, ctx.tenant_id, background)
    return ok({**DispatchOut.model_validate(disp).model_dump(), "job_id": job_id})
