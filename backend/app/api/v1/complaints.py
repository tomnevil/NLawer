"""投诉举报接口（P0-13 / 《暂行办法》第十五条）。

第十五条：「提供者应当建立健全投诉、举报机制，设置**便捷**的投诉、举报入口，
公布处理流程和反馈时限，及时受理、处理公众投诉举报并反馈处理结果。」

**为什么提交入口允许匿名？**
「公众投诉举报」的主体是公众，不是注册用户。若入口要求先登录，
等于把「便捷入口」四个字作废，也直接违反第十七条算法备案时对
投诉渠道的审查口径。因此：
- `POST /complaints` —— **无鉴权**，允许匿名；靠限流 + IP 留痕控制滥用
- `GET  /complaints/{ticket_no}` —— **无鉴权**，凭工单号回查（工单号即凭证）
- `GET  /complaints` / `POST /complaints/{id}/handle` —— **需管理员**，
  且强制租户隔离
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit_context import get_audit_context
from app.core.deps import get_db, require_roles
from app.core.errors import NotFoundError
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.core.rbac import Role
from app.models.complaint import Complaint
from app.services.complaint_service import ComplaintService

router = APIRouter(prefix="/complaints", tags=["投诉举报"])


class ComplaintCreate(BaseModel):
    type: str = Field(..., description="CONTENT_MISJUDGED / ILLEGAL_CONTENT / SERVICE_ABUSE / OTHER")
    # 2026-09-22：把「描述 ≤ 5000」从服务层上移到 schema（原在 `complaint_service.py:59`
    # 抛 `BadRequestError`，**新探针看不见** ⇒ 统一到 schema，让门禁能量到它）。
    description: str = Field(..., max_length=5000, description="问题描述")
    contact: Optional[str] = Field(None, max_length=100, description="联系方式（选填，便于反馈）")
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    related_moderation_id: Optional[int] = None


class ComplaintHandle(BaseModel):
    status: str = Field(..., description="PROCESSING / RESOLVED / REJECTED")
    handle_note: str = Field("", max_length=2000, description="处理结论（办结/不予受理必填）")


def _public_view(row: Complaint) -> dict:
    """对外视图：隐去处理人 ID 等内部字段，保留投诉人关心的进展。"""
    return {
        "ticket_no": row.ticket_no,
        "type": row.type,
        "status": row.status,
        "description": row.description,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "handle_note": row.handle_note,
        "handled_at": row.handled_at.isoformat() if row.handled_at else None,
        "feedback_sent": bool(row.feedback_sent),
    }


@router.post("", response_model=dict, summary="提交投诉举报（公开入口，支持匿名）")
async def submit_complaint(
    payload: ComplaintCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """公开投诉入口——**不要求登录**，这是第十五条「便捷入口」的底线。

    滥用控制靠三道：请求限流（见 middleware 的 `GENERATE_PATH_SUFFIXES` 兜底
    之外，本路径另配限流）、描述长度上限、来源 IP 留痕。
    """
    ctx = get_audit_context()
    user = getattr(request.state, "user", None)

    row = await ComplaintService(db).submit(
        type_=payload.type,
        description=payload.description,
        reporter=user,
        contact=payload.contact,
        target_type=payload.target_type,
        target_id=payload.target_id,
        related_moderation_id=payload.related_moderation_id,
        tenant_id=getattr(user, "tenant_id", None) or "platform",
        ip_address=ctx.get("ip_address"),
        user_agent=ctx.get("user_agent"),
    )
    await db.commit()
    return ok(
        {
            "ticket_no": row.ticket_no,
            "status": row.status,
            "due_at": row.due_at.isoformat() if row.due_at else None,
            "message": "已受理。您可凭工单号查询处理进展，我们将在承诺时限内反馈。",
        }
    )


@router.get("/policy", response_model=dict, summary="公布处理流程与反馈时限（第十五条）")
async def complaint_policy():
    """第十五条要求「公布处理流程和反馈时限」——因此单列一个公开端点。"""
    from app.models.complaint import COMPLAINT_DUE_DAYS

    return ok(
        {
            "due_days": COMPLAINT_DUE_DAYS,
            "flow": [
                {"step": 1, "name": "提交", "desc": "通过本入口提交，无需登录，即时获取工单号"},
                {"step": 2, "name": "受理", "desc": "1 个工作日内完成形式审查并分派处理人"},
                {"step": 3, "name": "核实", "desc": "调阅相关审核记录与生成内容，必要时联系投诉人补充材料"},
                {"step": 4, "name": "办结反馈", "desc": f"自受理起 {COMPLAINT_DUE_DAYS} 个自然日内给出处理结论"},
                {"step": 5, "name": "申诉", "desc": "对处理结果有异议的，可再次提交并引用原工单号"},
            ],
            "channels": ["应用内「投诉举报」入口", "客服邮箱", "平台公示联系方式"],
        }
    )


@router.get("/stats", response_model=dict, summary="投诉处理看板（管理员）")
async def complaint_stats(
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    data = await ComplaintService(db).counts(tenant_id=None)
    return ok(data)


@router.get("", response_model=dict, summary="投诉工单列表（管理员）")
async def list_complaints(
    params: PaginationParams = Depends(),
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    overdue_only: bool = Query(False, description="仅看逾期未办结"),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    svc = ComplaintService(db)
    stmt = svc.list_query(status=status, type_=type, overdue_only=overdue_only)

    # 有界计数：本表是**分布高度偏斜**的典型——投诉工单绝大多数处于
    # `PENDING`（实测占 90%），因此 `ix_complaints_status` 几乎无法收窄，
    # COUNT 必须走完 27 万行；而数据页靠 LIMIT 短路。
    # 30 万行实测：无界 COUNT 50.81ms vs 数据页 0.37ms（**138.7x**）→ 有界 0.37ms。
    total, is_lower_bound = await count_bounded(db, stmt)
    rows = (
        await db.execute(stmt.offset(params.offset).limit(params.limit))
    ).scalars().all()

    return ok(
        Page.build(
            [_admin_view(r) for r in rows],
            total,
            params,
            total_is_lower_bound=is_lower_bound,
        ).model_dump()
    )


@router.post("/{complaint_id}/handle", response_model=dict, summary="处理投诉（管理员）")
async def handle_complaint(
    complaint_id: int,
    payload: ComplaintHandle,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(require_roles(Role.PLATFORM_ADMIN)),
):
    from app.models.identity import User

    handler = (
        await db.execute(select(User).where(User.id == ctx.user_id))
    ).scalars().first()

    row = await ComplaintService(db).handle(
        complaint_id,
        status=payload.status,
        handle_note=payload.handle_note,
        handler=handler,
        tenant_id=None,  # 平台管理员可跨租户处理
    )
    await db.commit()
    return ok(_admin_view(row))


@router.get("/{ticket_no}", response_model=dict, summary="按工单号查询进展（公开）")
async def get_complaint(
    ticket_no: str,
    db: AsyncSession = Depends(get_db),
):
    """凭工单号回查。工单号即凭证（含随机段，不可枚举）。"""
    row = await ComplaintService(db).get_by_ticket(ticket_no)
    if row is None:
        raise NotFoundError("工单不存在，请核对工单号")
    return ok(_public_view(row))


def _admin_view(row: Complaint) -> dict:
    return {
        **_public_view(row),
        "id": row.id,
        "tenant_id": row.tenant_id,
        "reporter_id": row.reporter_id,
        "contact": row.contact,
        "target_type": row.target_type,
        "target_id": row.target_id,
        "related_moderation_id": row.related_moderation_id,
        "handler_id": row.handler_id,
        "ip_address": row.ip_address,
    }
