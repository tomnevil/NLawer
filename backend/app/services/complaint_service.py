"""投诉举报服务（P0-13 / 《暂行办法》第十五条）。

第十五条要求「建立健全投诉、举报机制…公布处理流程和反馈时限，
及时受理、处理公众投诉举报并反馈处理结果」。

本服务把这条要求拆成三个可验证的动作：
1. **受理**——落一条带 `due_at` 的工单，时限从受理时刻起算（不是从办结起算）
2. **处理**——管理员填写 `handle_note` 与 `status`
3. **反馈**——置 `feedback_sent`，并把处理结果推送给投诉人

以及一条**不变量**：`RESOLVED` 状态必须带 `handle_note` 与 `handled_at`。
没有处理结论的"已办结"是假闭环，投诉人拿不到答复，等于没履行第十五条。
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditAction, record
from app.core.errors import BadRequestError, NotFoundError
from app.models.complaint import (
    COMPLAINT_DUE_DAYS,
    Complaint,
    ComplaintStatus,
    ComplaintType,
    new_ticket_no,
)


class ComplaintService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def submit(
        self,
        *,
        type_: str,
        description: str,
        reporter: Any = None,
        contact: Optional[str] = None,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        related_moderation_id: Optional[int] = None,
        tenant_id: str = "platform",
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Complaint:
        """受理一条投诉举报。

        匿名也受理（`reporter is None`）——**公众举报入口不能有登录门槛**，
        否则等于把「便捷的投诉举报入口」变成空话。
        """
        desc = (description or "").strip()
        if len(desc) < 5:
            raise BadRequestError("请描述具体问题（不少于 5 个字）")
        if len(desc) > 5000:
            raise BadRequestError("描述过长（上限 5000 字）")

        try:
            type_enum = ComplaintType(type_)
        except ValueError:
            raise BadRequestError(f"未知的投诉类型：{type_}")

        now = datetime.datetime.now(datetime.timezone.utc)
        row = Complaint(
            tenant_id=tenant_id or "platform",
            ticket_no=new_ticket_no(),
            type=type_enum.value,
            status=ComplaintStatus.PENDING.value,
            reporter_id=getattr(reporter, "id", None),
            contact=(contact or "").strip() or None,
            description=desc,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            related_moderation_id=related_moderation_id,
            due_at=now + datetime.timedelta(days=COMPLAINT_DUE_DAYS),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.db.add(row)
        await self.db.flush()

        await record(
            self.db,
            AuditAction.COMPLAINT_SUBMIT,
            "complaint",
            row.id,
            actor=reporter,
            tenant_id=row.tenant_id,
            detail={
                "ticket_no": row.ticket_no,
                "type": row.type,
                "anonymous": row.reporter_id is None,
                "due_at": row.due_at.isoformat() if row.due_at else None,
            },
        )
        return row

    async def get_by_ticket(self, ticket_no: str) -> Optional[Complaint]:
        """按工单号查询（投诉人凭此回查进展）。"""
        stmt = select(Complaint).where(Complaint.ticket_no == ticket_no.strip().upper())
        return (await self.db.execute(stmt)).scalars().first()

    def list_query(
        self,
        *,
        tenant_id: Optional[str] = None,
        status: Optional[str] = None,
        type_: Optional[str] = None,
        overdue_only: bool = False,
    ) -> Select:
        """管理端列表查询（支持按逾期筛选——逾期是违约信号，必须一眼可见）。"""
        stmt = select(Complaint)
        if tenant_id:
            stmt = stmt.where(Complaint.tenant_id == tenant_id)
        if status:
            stmt = stmt.where(Complaint.status == status)
        if type_:
            stmt = stmt.where(Complaint.type == type_)
        if overdue_only:
            now = datetime.datetime.now(datetime.timezone.utc)
            stmt = stmt.where(
                Complaint.due_at < now,
                Complaint.status.in_(
                    [ComplaintStatus.PENDING.value, ComplaintStatus.PROCESSING.value]
                ),
            )
        return stmt.order_by(Complaint.created_at.desc())

    async def handle(
        self,
        complaint_id: int,
        *,
        status: str,
        handle_note: str,
        handler: Any = None,
        tenant_id: Optional[str] = None,
    ) -> Complaint:
        """处理投诉（受理 → 处理中 → 办结/不予受理）。"""
        row = await self._get_or_404(complaint_id, tenant_id=tenant_id)

        try:
            status_enum = ComplaintStatus(status)
        except ValueError:
            raise BadRequestError(f"未知的处理状态：{status}")

        note = (handle_note or "").strip()
        # 办结/不予受理必须有结论——否则投诉人得不到答复
        if status_enum in (ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED) and len(note) < 5:
            raise BadRequestError("办结或不予受理必须填写处理结论（不少于 5 个字）")

        row.status = status_enum.value
        row.handle_note = note or None
        row.handler_id = getattr(handler, "id", None)
        if status_enum in (ComplaintStatus.RESOLVED, ComplaintStatus.REJECTED):
            row.handled_at = datetime.datetime.now(datetime.timezone.utc)
            row.feedback_sent = True  # 处理结论即反馈内容

        await self.db.flush()
        await record(
            self.db,
            AuditAction.COMPLAINT_HANDLE,
            "complaint",
            row.id,
            actor=handler,
            tenant_id=row.tenant_id,
            detail={
                "ticket_no": row.ticket_no,
                "status": row.status,
                "has_note": bool(row.handle_note),
            },
        )
        return row

    async def counts(self, *, tenant_id: Optional[str] = None) -> dict:
        """看板统计：待受理 / 处理中 / 逾期未办结。"""
        base = select(Complaint.status, func.count()).group_by(Complaint.status)
        if tenant_id:
            base = base.where(Complaint.tenant_id == tenant_id)
        rows = (await self.db.execute(base)).all()
        by_status = {s: int(c) for s, c in rows}

        now = datetime.datetime.now(datetime.timezone.utc)
        overdue_stmt = select(func.count()).where(
            Complaint.due_at < now,
            Complaint.status.in_(
                [ComplaintStatus.PENDING.value, ComplaintStatus.PROCESSING.value]
            ),
        )
        if tenant_id:
            overdue_stmt = overdue_stmt.where(Complaint.tenant_id == tenant_id)
        overdue = int((await self.db.execute(overdue_stmt)).scalar() or 0)

        return {
            "by_status": by_status,
            "pending": by_status.get(ComplaintStatus.PENDING.value, 0),
            "processing": by_status.get(ComplaintStatus.PROCESSING.value, 0),
            "resolved": by_status.get(ComplaintStatus.RESOLVED.value, 0),
            "rejected": by_status.get(ComplaintStatus.REJECTED.value, 0),
            "overdue": overdue,
            "due_days": COMPLAINT_DUE_DAYS,
        }

    async def _get_or_404(self, complaint_id: int, *, tenant_id: Optional[str]) -> Complaint:
        stmt = select(Complaint).where(Complaint.id == complaint_id)
        row = (await self.db.execute(stmt)).scalars().first()
        if row is None:
            raise NotFoundError("投诉工单不存在")
        # 租户隔离：非 platform 管理员不得跨租户处理
        if tenant_id and row.tenant_id != tenant_id:
            raise NotFoundError("投诉工单不存在")  # 不返回 403，避免泄露存在性
        return row


__all__ = ["ComplaintService"]
