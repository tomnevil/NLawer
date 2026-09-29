"""案件接口：列表（含派单池）/ 详情 / 时间线 / 触发派单 / 团队可见授权。"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.deps import (
    get_current_user,
    get_db,
    get_tenant_context,
    require_permissions,
)
from app.core.errors import ErrorCode, NotFoundError, PermissionDeniedError
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.core.rbac import Role
from app.models.case import Case, CaseEvent
from app.models.case_access import (
    GRANT_SCOPE_TEAM_READ,
    GRANT_STATUS_APPROVED,
    GRANT_STATUS_PENDING,
    GRANT_STATUS_REJECTED,
    CaseAccessGrant,
)
from app.models.enums import DispatchMode
from app.schemas.case import CaseOut, DispatchOut, DispatchRequest
from app.services.dispatch_service import DispatchService


def _now_db() -> datetime:
    """naive UTC：SQLite 的 DateTime 列无时区，比较双方必须同为 naive。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)

router = APIRouter(prefix="/cases", tags=["案件"])


async def _has_active_team_grant(db: AsyncSession, user_id: int, tenant_id: str) -> bool:
    """P2-10：律师是否持有**有效期内**的团队案件可见授权。

    审批通过（APPROVED）且未过期才有效；到期自动失效，无需定时任务。
    """
    row = (
        await db.execute(
            select(CaseAccessGrant).where(
                CaseAccessGrant.grantee_user_id == user_id,
                CaseAccessGrant.tenant_id == tenant_id,
                CaseAccessGrant.status == GRANT_STATUS_APPROVED,
                CaseAccessGrant.scope == GRANT_SCOPE_TEAM_READ,
                CaseAccessGrant.expires_at > _now_db(),
            )
        )
    ).scalars().first()
    return row is not None


async def _case_or_404(db: AsyncSession, case_id: int, ctx) -> Case:
    """加载案件并执行**租户 + 角色归属**校验（所有按 case_id 操作的端点必须经此守卫）。

    历史缺陷：`case_events` 仅按 `case_id` 查询事件表，未校验案件归属，
    任意已登录用户遍历 `case_id` 即可读取他人案件的时间线、涉案描述等
    敏感信息——对法律产品属致命数据泄露（违反《律师法》保密义务与 PIPL）。
    现统一收口到本函数，与 `get_case()` 的校验口径保持一致。

    P2-10（2026-09-29 产品决策）：律师可见粒度为「本人承办 + 团队内申请、
    授权有效期内可见他人承办」。律师访问**非本人承办**的案件（时间线 /
    文书 / 证据等一切按 case_id 的入口）必须持有效团队授权；无权时按
    「不存在」处理——不泄露案件是否存在，防遍历探测。
    """
    case = await db.get(Case, case_id)
    if case is None or case.tenant_id != ctx.tenant_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    # 客户只能访问自己的案件
    if ctx.role == Role.CLIENT and case.client_user_id != ctx.user_id:
        raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    # 律师：本人承办直通；他人承办需有效期内团队授权（无权 = 不存在，防探测）
    if ctx.role == Role.LAWYER and case.lawyer_id != ctx.user_id:
        if not await _has_active_team_grant(db, ctx.user_id, ctx.tenant_id):
            raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
    return case


@router.get("", response_model=dict, summary="案件列表")
async def list_cases(
    params: PaginationParams = Depends(),
    status: str | None = Query(None),
    grade: str | None = Query(None),
    dispute_type: str | None = Query(None),
    lawyer_id: int | None = Query(None),
    keyword: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    base = select(Case).where(Case.tenant_id == ctx.tenant_id)
    if ctx.role == Role.CLIENT:
        base = base.where(Case.client_user_id == ctx.user_id)
    elif ctx.role == Role.LAWYER:
        # P2-10：本人承办默认可见；显式检索他人承办需有效期内团队授权
        #（团队内申请 -> 律所管理员审批）。列表场景无权时明确 403 告知
        # 原因与路径（详情守卫按「不存在」处理以防探测，两者语义互补）。
        if lawyer_id in (None, ctx.user_id):
            base = base.where(Case.lawyer_id == ctx.user_id)
        elif await _has_active_team_grant(db, ctx.user_id, ctx.tenant_id):
            base = base.where(Case.lawyer_id == lawyer_id)
        else:
            raise PermissionDeniedError(
                "查看他人承办案件需要有效的团队授权（团队内申请，管理员审批）",
            )
    if status:
        base = base.where(Case.status == status)
    if grade:
        base = base.where(Case.grade == grade)
    if dispute_type:
        base = base.where(Case.dispute_type == dispute_type)
    if lawyer_id and ctx.role != Role.LAWYER:
        # LAWYER 分支已在上方按授权语义处理完毕；此通用过滤只服务
        # 管理员/助理等租户内角色，避免绕过授权检查。
        base = base.where(Case.lawyer_id == lawyer_id)
    if keyword:
        base = base.where(Case.title.like(f"%{keyword}%"))

    # 有界计数：关键词 LIKE 无法用索引，无界 COUNT 在 55 万行实测 115ms
    # （而取数据仅 0.53ms）。有界计数把"用户翻不完的场景"降级为下界，实测 0.96ms。
    total, is_lower_bound = await count_bounded(db, base)
    rows = list(
        (
            await db.execute(base.order_by(Case.id.desc()).offset(params.offset).limit(params.limit))
        ).scalars().all()
    )
    return ok(
        Page.build(
            [CaseOut.model_validate(r).model_dump() for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.get("/pool", response_model=dict, summary="派单池（待抢单案件）")
async def dispatch_pool(
    params: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    base = select(Case).where(Case.tenant_id == ctx.tenant_id, Case.status == "PENDING_DISPATCH")
    # 有界计数：判据是「匹配集是否远大于一页」。
    # 小匹配集（如 0 条）无收益（实测 1.0x）——因为此时计数本就走完小集合；
    # 但大律所场景（单租户 4.3 万行 → 匹配 8572 条）实测 30.99ms → 0.95ms（32.6x）。
    # 线上匹配集事先不可知，故统一采用有界计数：小集合零损失、大集合高收益。
    total, is_lower_bound = await count_bounded(db, base)
    rows = list(
        (await db.execute(base.order_by(Case.id.desc()).offset(params.offset).limit(params.limit)))
        .scalars()
        .all()
    )
    return ok(
        Page.build(
            [CaseOut.model_validate(r).model_dump() for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.get("/stats", response_model=dict, summary="按状态计数（驾驶舱聚合）")
async def case_stats(
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """一条 GROUP BY 替代驾驶舱的 9 个逐状态计数请求（P1-10 聚合端点）。

    语义说明：
    - `status` 列有索引，GROUP BY 为**精确计数**，不存在关键词 LIKE /
      无界 COUNT 的下界问题，因此不返回 `total_is_lower_bound`（恒为精确值）。
    - 租户与角色可见性与 `list_cases` 的默认视图**完全一致**（CLIENT 只见
      本人委托、LAWYER 只见本人承办），漏斗数字与列表页可对账。
    - 本路由必须注册在 `/{case_id}` 之前：否则 `GET /cases/stats` 会被
      详情路由吞掉，`case_id="stats"` 解析 int 直接 422。
    """
    base = select(Case.status, func.count()).where(Case.tenant_id == ctx.tenant_id)
    if ctx.role == Role.CLIENT:
        base = base.where(Case.client_user_id == ctx.user_id)
    elif ctx.role == Role.LAWYER:
        base = base.where(Case.lawyer_id == ctx.user_id)
    rows = (await db.execute(base.group_by(Case.status))).all()
    return ok({"by_status": {status: n for status, n in rows}})


# ── 团队可见授权（P2-10）：申请 / 列表 / 审批 ─────────────────────────
# 注意：以下路由必须注册在 /{case_id} 之前（同 /stats 的路由顺序约束：
# 否则 GET /cases/access-requests 会被详情路由吞掉，case_id 解析 int 422）。


class AccessRequestCreate(BaseModel):
    """团队可见授权申请体。"""

    reason: str = Field(default="", max_length=500, description="申请理由（审批页展示）")
    days: int = Field(default=30, ge=1, le=90, description="期望授权天数（1-90）")


class AccessRequestDecision(BaseModel):
    """审批决定体。"""

    action: str = Field(..., pattern="^(approve|reject)$")
    days: int = Field(default=30, ge=1, le=90, description="批准的授权天数（1-90）")


@router.post("/access-requests", response_model=dict, summary="申请团队案件可见授权")
async def create_access_request(
    payload: AccessRequestCreate,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """律师发起申请：落库为 PENDING，等待律所管理员审批（POST /decision）。"""
    if ctx.role != Role.LAWYER:
        raise PermissionDeniedError("仅律师可申请团队案件可见授权")
    grant = CaseAccessGrant(
        tenant_id=ctx.tenant_id,
        grantee_user_id=ctx.user_id,
        status=GRANT_STATUS_PENDING,
        scope=GRANT_SCOPE_TEAM_READ,
        reason=payload.reason or None,
        expires_at=_now_db(),  # PENDING 阶段占位；审批通过时改写为授权窗
    )
    db.add(grant)
    await db.commit()
    return ok({"id": grant.id, "status": grant.status})


@router.get("/access-requests", response_model=dict, summary="团队可见授权申请列表")
async def list_access_requests(
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    """律师看自己的申请；律所管理员/平台管理员看本租户全部（审批视角）。"""
    if ctx.role == Role.CLIENT:
        raise PermissionDeniedError("无权查看授权申请")
    base = select(CaseAccessGrant).where(CaseAccessGrant.tenant_id == ctx.tenant_id)
    if ctx.role == Role.LAWYER:
        base = base.where(CaseAccessGrant.grantee_user_id == ctx.user_id)
    rows = (
        await db.execute(base.order_by(CaseAccessGrant.id.desc()).limit(100))
    ).scalars().all()
    return ok(
        [
            {
                "id": r.id,
                "grantee_user_id": r.grantee_user_id,
                "granted_by": r.granted_by,
                "status": r.status,
                "expires_at": r.expires_at.isoformat() if r.expires_at else None,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    )


@router.post(
    "/access-requests/{grant_id}/decision",
    response_model=dict,
    summary="审批团队可见授权申请",
    dependencies=[Depends(require_permissions("case:assign"))],
)
async def decide_access_request(
    grant_id: int,
    payload: AccessRequestDecision,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    """律所管理员审批：approve 写授权窗（expires_at = now + days）；
    reject 关闭申请。仅 PENDING 可处理（重复处理 403）。

    授权表本身即审批留痕（status / granted_by / expires_at / 时间戳），
    不再重复写审计。
    """
    grant = await db.get(CaseAccessGrant, grant_id)
    if grant is None or grant.tenant_id != ctx.tenant_id:
        raise NotFoundError("申请不存在", code=ErrorCode.CASE_NOT_FOUND)
    if grant.status != GRANT_STATUS_PENDING:
        raise PermissionDeniedError("该申请已处理")
    if payload.action == "approve":
        grant.status = GRANT_STATUS_APPROVED
        grant.expires_at = _now_db() + timedelta(days=payload.days)
    else:
        grant.status = GRANT_STATUS_REJECTED
        grant.expires_at = _now_db()
    grant.granted_by = user.id
    await db.commit()
    return ok(
        {"id": grant.id, "status": grant.status, "expires_at": grant.expires_at.isoformat()}
    )


@router.get("/{case_id}", response_model=dict, summary="案件详情")
async def get_case(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    case = await _case_or_404(db, case_id, ctx)
    return ok(CaseOut.model_validate(case).model_dump())


@router.get("/{case_id}/events", response_model=dict, summary="案件时间线")
async def case_events(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    # 安全：先校验案件归属，再读取事件——否则任意用户可越权读取他人案件动态
    await _case_or_404(db, case_id, ctx)
    rows = list(
        (
            await db.execute(
                select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id.asc())
            )
        ).scalars().all()
    )
    return ok(
        [
            {
                "id": e.id,
                "event_type": e.event_type,
                "title": e.title,
                "description": e.description,
                "occurred_at": e.occurred_at,
                "actor_user_id": e.actor_user_id,
            }
            for e in rows
        ]
    )


@router.post(
    "/{case_id}/dispatch",
    response_model=dict,
    summary="触发派单",
    # RBAC 门禁（审查待复核项裁决后修复）：此前仅 get_current_user + 租户
    # 上下文，CLIENT 理论上可对自己的案件触发派单。case:assign 为 RBAC
    # 注册表中的既有权限码（律所管理员/管理员持有）。
    dependencies=[Depends(require_permissions("case:assign"))],
)
async def dispatch_case(
    case_id: int,
    payload: DispatchRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
    user=Depends(get_current_user),
):
    case = await _case_or_404(db, case_id, ctx)
    try:
        mode = DispatchMode(payload.mode)
    except ValueError:
        from app.core.errors import BadRequestError

        raise BadRequestError(f"未知派单方式：{payload.mode}", code=ErrorCode.DISPATCH_RULE_CONFLICT)

    disp = await DispatchService(db).dispatch(
        case, mode=mode, bind_lawyer_id=payload.bind_lawyer_id, candidate_lawyer_ids=payload.candidate_lawyer_ids
    )
    # 派单是案件责任归属的起点：谁发起、按什么策略、指派给谁，都需可还原
    await record(
        db,
        AuditAction.DISPATCH_CREATE,
        "dispatch",
        disp.id,
        actor=user,
        tenant_id=ctx.tenant_id,
        detail={
            "case_id": case.id,
            "mode": str(mode),
            "lawyer_id": disp.lawyer_id,
            "bind_lawyer_id": payload.bind_lawyer_id,
        },
    )
    await db.commit()
    return ok(DispatchOut.model_validate(disp).model_dump())
