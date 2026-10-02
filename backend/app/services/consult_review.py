"""咨询报告草稿自动入律师复核（Phase 1 / T1.2）。

四段式草稿在后台生成后，自动创建 `Review`（级别按可派律师能力定 L2/L3），
并经轻量派单逻辑指派给可接单律师，等待其确认/驳回。无律师可派时静默跳过，
草稿仍保留为 `draft` 态，不阻断客户的自然回复。
"""
from typing import Optional, Tuple

from sqlalchemy import select

from app.models.consult_report import ConsultReport
from app.models.enums import NotificationType, ReviewLevel, ReviewTargetType
from app.models.identity import LawyerProfile
from app.services.notification_service import notify
from app.services.review_service import ReviewService


async def assign_consult_reviewer(db, tenant_id: str) -> Optional[Tuple[int, ReviewLevel]]:
    """挑一个可接单律师：优先可 L3 终审，其次按在手案件少优先。"""
    rows = (
        await db.execute(
            select(LawyerProfile).where(
                LawyerProfile.tenant_id == tenant_id, LawyerProfile.available.is_(True)
            )
        )
    ).scalars().all()
    if not rows:
        return None
    rows = sorted(rows, key=lambda p: (not p.can_l3_review, p.active_case_count or 0))
    lawyer = rows[0]
    level = ReviewLevel.L3 if lawyer.can_l3_review else ReviewLevel.L2
    return lawyer.user_id, level


async def dispatch_consult_report_review(db, report: ConsultReport, tenant_id: str) -> Optional[int]:
    """为草稿建 Review 并派给律师；返回 review.id（无律师可派时返回 None）。"""
    picked = await assign_consult_reviewer(db, tenant_id)
    if picked is None:
        return None
    lawyer_id, required_level = picked
    review = await ReviewService(db).ensure(
        target_type=ReviewTargetType.CONSULT_REPORT,
        target_id=report.id,
        case_id=report.case_id,
        tenant_id=tenant_id,
        required_level=required_level,
        assignee_id=lawyer_id,
    )
    report.review_id = review.id
    report.lawyer_id = lawyer_id
    await notify(
        db,
        tenant_id=tenant_id,
        user_id=lawyer_id,
        type=NotificationType.REVIEW_REQUIRED,
        content="有新的咨询报告草稿待复核确认",
        ref_type=ReviewTargetType.CONSULT_REPORT.value,
        ref_id=report.id,
    )
    return review.id
