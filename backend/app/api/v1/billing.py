"""计费与工单接口（PRD 5.7 / 11.1）。"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context, require_permissions
from app.core.errors import BadRequestError, ErrorCode
from app.core.pagination import ok
from app.models.enums import UsageType
from app.schemas.billing import (
    QuotaAdjust,
    RevenueProjection,
    UsageConsume,
    WorkOrderOut,
)
from app.services.billing_service import BillingService

router = APIRouter(prefix="/billing", tags=["用量与计费"])


def _period() -> str:
    import datetime

    return datetime.datetime.now().strftime("%Y-%m")


@router.get(
    "/dashboard",
    response_model=dict,
    summary="用量看板（实时扣减 + 工单）",
    dependencies=[Depends(require_permissions("billing:read"))],
)
async def dashboard(
    period: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    data = await BillingService(db).dashboard(ctx.tenant_id, period or _period())
    return ok(data)


@router.get(
    "/work-orders",
    response_model=dict,
    summary="工单列表",
    dependencies=[Depends(require_permissions("billing:read"))],
)
async def list_work_orders(
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    rows = await BillingService(db).list_work_orders(ctx.tenant_id, status=status)
    return ok([WorkOrderOut.model_validate(r).model_dump() for r in rows])


@router.post(
    "/consume",
    response_model=dict,
    summary="手动扣减一次用量（超量转工单）",
    dependencies=[Depends(require_permissions("billing:write"))],
)
async def consume(
    payload: UsageConsume,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    try:
        usage_type = UsageType(payload.usage_type)
    except ValueError:
        # 🚨 历史缺陷（M4 坐实）：此处原本写的是
        #     `raise NotFoundError(..., code=ErrorCode.NOT_FOUND)`
        #     而 `ErrorCode` **根本没有 `NOT_FOUND` 这个成员** ⇒ 构造异常时
        #     先抛 `AttributeError` ⇒ 客户端收到的是 **500**，
        #     这条「未知类型 ⇒ 404」的分支**从未被真正执行过**。
        #     语义也不对：参数不合法不是「资源不存在」。
        #     改为 400 + `VALIDATION_ERROR`（与 Q-V 的整改口径一致）。
        raise BadRequestError(
            f"未知用量类型：{payload.usage_type}",
            code=ErrorCode.VALIDATION_ERROR,
        )
    result = await BillingService(db).consume_atomic(
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        usage_type=usage_type,
        period=_period(),
        ref_type=payload.ref_type,
        ref_id=payload.ref_id,
        urgent=payload.urgent,
    )
    await db.commit()
    return ok(result)


@router.post(
    "/project-revenue",
    response_model=dict,
    summary="收入预测（分项系数法）",
    dependencies=[Depends(require_permissions("billing:read"))],
)
async def project_revenue(
    payload: RevenueProjection,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    result = BillingService(db).project_revenue(
        subscription_cents=payload.subscription_cents,
        case_service_cents=payload.case_service_cents,
        work_order_cents=payload.work_order_cents,
        value_added_cents=payload.value_added_cents,
    )
    return ok(result)


@router.post(
    "/quota/adjust",
    response_model=dict,
    summary="管理员手动调整额度上限（线下充值/补偿）",
    dependencies=[Depends(require_permissions("billing:write"))],
)
async def adjust_quota(
    payload: QuotaAdjust,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    """Q-M（2026-09-21 裁定）：给「线下充值期间改额度」一个**有授权、有留痕**的入口。

    此前只能直连数据库人工改 —— 无授权、无审计、改错了不可追溯。
    本端点沿用 Q-P 的 `billing:write` 口径（仅 FIRM_ADMIN / ENTERPRISE_ADMIN），
    并把**调整前后**的额度写进审计，满足「工单与额度变更关联留痕」的合规要求。

    ⚠️ `limit_count` 是**设置后的新上限**（绝对值），不是增量。
    """
    # `usage_type` 由 `QuotaAdjust` 的枚举字段在**入参解析阶段**校验
    # （非法值 ⇒ FastAPI 422），无需在此手工 `UsageType(...)` 再判一次。
    usage_type = payload.usage_type
    period = payload.period or _period()
    before = await BillingService(db).get_quota_row(ctx.tenant_id, usage_type, period)
    before_limit = before.limit_count if before is not None else None

    q = await BillingService(db).set_quota_limit(
        tenant_id=ctx.tenant_id,
        usage_type=usage_type,
        period=period,
        limit_count=payload.limit_count,
    )
    # 额度变更影响计费与转工单判断，必须留痕（含前后值，便于复盘与对账）
    await record(
        db,
        AuditAction.QUOTA_ADJUST,
        "usage_quota",
        q.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "usage_type": usage_type.value,
            "period": period,
            "before_limit": before_limit,
            "after_limit": q.limit_count,
            "used_count": q.used_count,
            "reason": payload.reason,
        },
    )
    await db.commit()
    return ok({
        "id": q.id,
        "tenant_id": q.tenant_id,
        "usage_type": usage_type.value,
        "period": q.period,
        "limit_count": q.limit_count,
        "used_count": q.used_count,
        "before_limit": before_limit,
    })
