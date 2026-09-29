"""复核接口：队列 / 提交 / 出结论 / 留痕 / 归档。"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import get_current_user, get_db, get_tenant_context
from app.core.errors import ErrorCode, NotFoundError, UnprocessableEntityError
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.core.rbac import Role
from app.models.case import Case
from app.models.enums import ReviewDecision, ReviewTargetType
from app.models.review import Review
from app.schemas.review import ReviewAction, ReviewOut, ReviewRecordOut
from app.services.review_service import ReviewService

router = APIRouter(prefix="/reviews", tags=["复核工作流"])


async def _review_or_404(db: AsyncSession, review_id: int, tenant_id: str) -> Review:
    """加载复核任务并校验租户归属（写操作必须经此守卫）。

    历史缺陷：`edit` / `decide` / `archive` / `void` 四个写端点仅依赖
    `get_current_user`，未做租户校验，任何登录用户可凭 review_id 篡改
    或定稿**他人租户**的复核内容。此处统一收口。
    """
    r = await db.get(Review, review_id)
    if r is None or r.tenant_id != tenant_id:
        raise NotFoundError("复核任务不存在", code=ErrorCode.REVIEW_NOT_FOUND)
    return r


async def _case_or_404(db: AsyncSession, case_id: int, ctx) -> Case:
    """与 `cases.py:_case_or_404` **同口径**：租户 + 客户归属双重校验。

    2026-09-22 收口（端点层授权门禁 TENANT 档）：`POST /reviews/ensure`
    此前只把 `ctx.tenant_id` 传下去，**同租户的任意客户都能对他人案件的
    分析/文书/证据发起复核**（把他人材料推进复核流程）。
    """
    case = await db.get(Case, case_id)
    if case is None or case.tenant_id != ctx.tenant_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    if ctx.role == Role.CLIENT and case.client_user_id != ctx.user_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    return case


@router.get("", response_model=dict, summary="复核队列")
async def list_reviews(
    params: PaginationParams = Depends(),
    status: str | None = Query(None),
    target_type: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    base = select(Review).where(Review.tenant_id == ctx.tenant_id)
    if status:
        base = base.where(Review.status == status)
    if target_type:
        base = base.where(Review.target_type == target_type)

    # 有界计数：小匹配集无收益（实测 1.1x），但大律所场景下
    # 单租户复核任务可达数十万条，此时无界 COUNT 与数据页拉开 30x 以上。
    # 线上匹配集不可预知，统一采用有界计数：小集合零损失、大集合高收益。
    total, is_lower_bound = await count_bounded(db, base)
    rows = list(
        (await db.execute(base.order_by(Review.id.desc()).offset(params.offset).limit(params.limit)))
        .scalars()
        .all()
    )
    return ok(
        Page.build(
            [ReviewOut.model_validate(r).model_dump() for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.post("/ensure", response_model=dict, summary="创建/取得复核任务")
async def ensure_review(
    target_type: str,
    target_id: int,
    case_id: int | None = None,
    required_level: str = "L2",
    assignee_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    from app.models.enums import ReviewLevel

    try:
        tt = ReviewTargetType(target_type)
        lv = ReviewLevel(required_level)
    except ValueError:
        # Q-V（2026-09-21 裁定）：参数不合法属 422 语义，报 `REVIEW_INVALID_PARAM`，
        # 不再借用 404 `REVIEW_NOT_FOUND`（调用方无法区分「我传错了」与「东西不存在」）。
        raise UnprocessableEntityError("参数不合法", code=ErrorCode.REVIEW_INVALID_PARAM)

    if case_id is not None:
        await _case_or_404(db, case_id, ctx)
    elif ctx.role == Role.CLIENT:
        # 客户发起复核必须挂在自己的案件上 —— 不给 case_id 就无法判断归属，
        # 放行等于允许对任意 target_id 建复核，故一律拒。
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)

    r = await ReviewService(db).ensure(
        target_type=tt, target_id=target_id, case_id=case_id,
        tenant_id=ctx.tenant_id, required_level=lv, assignee_id=assignee_id,
    )
    await record(
        db,
        AuditAction.REVIEW_CREATE,
        "review",
        r.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "target_type": tt.value,
            "target_id": target_id,
            "case_id": case_id,
            "required_level": lv.value,
            "assignee_id": assignee_id,
        },
    )
    await db.commit()
    return ok(ReviewOut.model_validate(r).model_dump())


@router.get("/{review_id}", response_model=dict, summary="复核详情")
async def get_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    r = await db.get(Review, review_id)
    if r is None or r.tenant_id != ctx.tenant_id:
        raise NotFoundError("复核任务不存在", code=ErrorCode.REVIEW_NOT_FOUND)
    return ok(ReviewOut.model_validate(r).model_dump())


@router.get("/{review_id}/records", response_model=dict, summary="复核留痕")
async def review_records(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    r = await db.get(Review, review_id)
    if r is None or r.tenant_id != ctx.tenant_id:
        raise NotFoundError("复核任务不存在", code=ErrorCode.REVIEW_NOT_FOUND)
    rows = await ReviewService(db).records(review_id)
    return ok([ReviewRecordOut.model_validate(x).model_dump() for x in rows])


@router.post("/{review_id}/submit", response_model=dict, summary="提交复核")
async def submit_review(
    review_id: int,
    payload: ReviewAction,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _review_or_404(db, review_id, ctx.tenant_id)
    r = await ReviewService(db).submit(review_id, actor=user, comment=payload.comment)
    await record(
        db,
        AuditAction.REVIEW_SUBMIT,
        "review",
        review_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"comment": payload.comment},
    )
    await db.commit()
    return ok(ReviewOut.model_validate(r).model_dump())


@router.post("/{review_id}/edit", response_model=dict, summary="律师修改")
async def edit_review(
    review_id: int,
    payload: ReviewAction,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _review_or_404(db, review_id, ctx.tenant_id)
    r = await ReviewService(db).edit(review_id, actor=user, changes=payload.changes, comment=payload.comment)
    await record(
        db,
        AuditAction.REVIEW_EDIT,
        "review",
        review_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        # 只记字段级 diff，不落文书正文（避免敏感内容进审计表）
        detail={"changed_fields": sorted((payload.changes or {}).keys()), "comment": payload.comment},
    )
    await db.commit()
    return ok(ReviewOut.model_validate(r).model_dump())


@router.post("/{review_id}/decide", response_model=dict, summary="出复核结论")
async def decide_review(
    review_id: int,
    payload: ReviewAction,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _review_or_404(db, review_id, ctx.tenant_id)
    try:
        decision = ReviewDecision(payload.decision or "APPROVED")
    except ValueError:
        # Q-V（2026-09-21 裁定）：未知结论值属 422 参数错误，报 `REVIEW_INVALID_PARAM`，
        # 不再借用 `REVIEW_ALREADY_DECIDED`（那是「已出过结论」的状态码，语义错配）。
        raise UnprocessableEntityError(
            f"未知复核结论：{payload.decision}", code=ErrorCode.REVIEW_INVALID_PARAM)

    r = await ReviewService(db).decide(review_id, actor=user, decision=decision, comment=payload.comment)
    await record(
        db,
        AuditAction.REVIEW_APPROVE
        if decision == ReviewDecision.APPROVED
        else AuditAction.REVIEW_REJECT,
        "review",
        review_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        # 复核结论是责任认定的关键证据，必须记录结论与理由
        detail={"decision": decision.value, "comment": payload.comment},
    )
    await db.commit()
    return ok(ReviewOut.model_validate(r).model_dump())


@router.post("/{review_id}/archive", response_model=dict, summary="定稿后归档")
async def archive_review(
    review_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _review_or_404(db, review_id, ctx.tenant_id)
    r = await ReviewService(db).archive(review_id, actor=user)
    await record(
        db,
        AuditAction.REVIEW_ARCHIVE,
        "review",
        review_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"note": "定稿后归档（不可再修改）"},
    )
    await db.commit()
    return ok(ReviewOut.model_validate(r).model_dump())


@router.post("/{review_id}/void", response_model=dict, summary="作废")
async def void_review(
    review_id: int,
    payload: ReviewAction,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    await _review_or_404(db, review_id, ctx.tenant_id)
    r = await ReviewService(db).void(review_id, actor=user, comment=payload.comment)
    await record(
        db,
        AuditAction.REVIEW_VOID,
        "review",
        review_id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={"comment": payload.comment},
    )
    await db.commit()
    return ok(ReviewOut.model_validate(r).model_dump())

