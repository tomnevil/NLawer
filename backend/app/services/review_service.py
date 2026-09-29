"""复核工作流服务：L1 AI 自检 / L2 律师复核 / L3 合伙人终审（PRD 5.5）。

全程留痕（`ReviewRecord`）：谁在何时把什么从什么状态改成了什么状态。
状态流转一律经 `ReviewFSM.assert_transition` 硬校验，非法流转直接拒绝。
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorCode, NotFoundError
from app.core.rbac import Role
from app.models.analysis import CaseAnalysis
from app.models.case import Case
from app.models.enums import (
    CaseStatus,
    NotificationType,
    ReviewDecision,
    ReviewLevel,
    ReviewStatus,
    ReviewTargetType,
)
from app.models.identity import LawyerProfile, User
from app.models.review import Review, ReviewRecord
from app.services.notification_service import notify
from app.workflows.review_fsm import ReviewStateMachine

_LEVEL_ORDER = {ReviewLevel.L1: 1, ReviewLevel.L2: 2, ReviewLevel.L3: 3}


class ReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 创建 / 查询 ----------------
    async def ensure(
        self,
        *,
        target_type: ReviewTargetType,
        target_id: int,
        case_id: Optional[int],
        tenant_id: str,
        required_level: ReviewLevel = ReviewLevel.L2,
        forced_hits: Optional[list] = None,
        assignee_id: Optional[int] = None,
    ) -> Review:
        """幂等取得或创建复核任务（强制复核命中时升级要求级别）。"""
        existing = (
            (
                await self.db.execute(
                    select(Review).where(
                        Review.target_type == target_type,
                        Review.target_id == target_id,
                        Review.tenant_id == tenant_id,
                    )
                )
            ).scalars().first()
        )
        if existing is not None:
            # 命中强制复核时上调要求级别
            if _LEVEL_ORDER[required_level] > _LEVEL_ORDER[existing.required_level]:
                existing.required_level = required_level
            if forced_hits:
                existing.forced_hits = forced_hits
                existing.is_forced = 1
            return existing

        review = Review(
            tenant_id=tenant_id,
            target_type=target_type,
            target_id=target_id,
            case_id=case_id,
            status=ReviewStatus.DRAFT,
            required_level=required_level,
            is_forced=1 if forced_hits else 0,
            forced_hits=forced_hits,
            assignee_id=assignee_id,
        )
        self.db.add(review)
        await self.db.flush()
        await self._record(review, "CREATE", actor_id=assignee_id, to_status=ReviewStatus.DRAFT)
        if forced_hits and assignee_id:
            await notify(
                self.db,
                tenant_id=tenant_id,
                user_id=assignee_id,
                type=NotificationType.REVIEW_REQUIRED,
                content="有新的强制复核任务待处理",
                ref_type=target_type.value,
                ref_id=target_id,
            )
        return review

    async def get(self, review_id: int) -> Review:
        r = await self.db.get(Review, review_id)
        if r is None:
            raise NotFoundError("复核任务不存在", code=ErrorCode.REVIEW_NOT_FOUND)
        return r

    # ---------------- 流转 ----------------
    async def edit(
        self, review_id: int, *, actor: User, changes: Optional[dict] = None, comment: Optional[str] = None
    ) -> Review:
        r = await self.get(review_id)
        cur = r.status
        ReviewStateMachine.assert_transition(cur, ReviewStatus.LAWYER_EDITING)
        r.status = ReviewStatus.LAWYER_EDITING
        await self._record(
            r, "EDIT", actor_id=actor.id, actor_role=actor.role.value,
            from_status=cur, to_status=ReviewStatus.LAWYER_EDITING, changes=changes, comment=comment,
        )
        return r

    async def submit(self, review_id: int, *, actor: User, comment: Optional[str] = None) -> Review:
        r = await self.get(review_id)
        cur = r.status
        ReviewStateMachine.assert_transition(cur, ReviewStatus.PENDING_CONFIRM)
        r.status = ReviewStatus.PENDING_CONFIRM
        await self._record(
            r, "SUBMIT", actor_id=actor.id, actor_role=actor.role.value,
            from_status=cur, to_status=ReviewStatus.PENDING_CONFIRM, comment=comment,
        )
        return r

    async def decide(
        self,
        review_id: int,
        *,
        actor: User,
        decision: ReviewDecision,
        comment: Optional[str] = None,
    ) -> Review:
        """出复核结论。

        APPROVED 时按「已满足级别」判定：
        - 达到要求级别 -> CONFIRMED（可定稿）
        - 未达到       -> 保持 PENDING_CONFIRM，等待更高级别复核（L2 -> L3 升级）
        """
        r = await self.get(review_id)
        actor_level = await self.resolve_actor_level(actor)

        if decision == ReviewDecision.REVISION_REQUESTED:
            cur = r.status
            ReviewStateMachine.assert_transition(cur, ReviewStatus.LAWYER_EDITING)
            r.status = ReviewStatus.LAWYER_EDITING
            r.decision = decision
            r.decided_by = actor.id
            r.comment = comment
            await self._record(
                r, "REQUEST_REVISION", actor_id=actor.id, actor_role=actor.role.value,
                level=actor_level, from_status=cur, to_status=ReviewStatus.LAWYER_EDITING, comment=comment,
            )
            await self._notify_decided(r, actor=actor, comment=comment, outcome="REVISION")
            return r

        if decision == ReviewDecision.REJECTED:
            cur = r.status
            ReviewStateMachine.assert_transition(cur, ReviewStatus.VOIDED)
            r.status = ReviewStatus.VOIDED
            r.decision = decision
            r.decided_by = actor.id
            r.comment = comment
            await self._record(
                r, "REJECT", actor_id=actor.id, actor_role=actor.role.value,
                level=actor_level, from_status=cur, to_status=ReviewStatus.VOIDED, comment=comment,
            )
            await self._notify_decided(r, actor=actor, comment=comment, outcome="REJECTED")
            return r

        # APPROVED
        cur_level = r.satisfied_level
        new_level = actor_level
        if cur_level is not None and _LEVEL_ORDER[cur_level] > _LEVEL_ORDER[new_level]:
            new_level = cur_level
        r.satisfied_level = new_level
        r.decision = ReviewDecision.APPROVED
        r.decided_by = actor.id
        r.comment = comment

        if ReviewStateMachine.level_satisfied(r.required_level, new_level):
            cur = r.status
            ReviewStateMachine.assert_transition(
                cur, ReviewStatus.CONFIRMED,
                required_level=r.required_level, satisfied_level=new_level,
            )
            r.status = ReviewStatus.CONFIRMED
            r.decided_at = _now()
            await self._record(
                r, "APPROVE", actor_id=actor.id, actor_role=actor.role.value,
                level=new_level, from_status=cur, to_status=ReviewStatus.CONFIRMED, comment=comment,
            )
            await self._on_confirmed(r)
            await self._notify_decided(r, actor=actor, comment=comment, outcome="CONFIRMED")
        else:
            await self._record(
                r, "APPROVE_PARTIAL", actor_id=actor.id, actor_role=actor.role.value,
                level=new_level, from_status=r.status, to_status=r.status,
                comment=f"{actor_level.value} 复核通过，待 {r.required_level.value} 终审",
            )
        return r

    async def archive(self, review_id: int, *, actor: User) -> Review:
        """确认后归档（硬约束：仅 CONFIRMED 可归档）。"""
        r = await self.get(review_id)
        cur = r.status
        ReviewStateMachine.assert_transition(
            cur, ReviewStatus.ARCHIVED,
            required_level=r.required_level, satisfied_level=r.satisfied_level,
        )
        r.status = ReviewStatus.ARCHIVED
        await self._record(
            r, "ARCHIVE", actor_id=actor.id, actor_role=actor.role.value,
            from_status=cur, to_status=ReviewStatus.ARCHIVED,
        )
        return r

    async def void(self, review_id: int, *, actor: User, comment: Optional[str] = None) -> Review:
        r = await self.get(review_id)
        cur = r.status
        ReviewStateMachine.assert_transition(cur, ReviewStatus.VOIDED)
        r.status = ReviewStatus.VOIDED
        await self._record(
            r, "VOID", actor_id=actor.id, actor_role=actor.role.value,
            from_status=cur, to_status=ReviewStatus.VOIDED, comment=comment,
        )
        return r

    # ---------------- 辅助 ----------------
    async def resolve_actor_level(self, user: User) -> ReviewLevel:
        """按角色与档案判定该用户可满足的复核级别。"""
        if user.role in (Role.PLATFORM_ADMIN, Role.FIRM_ADMIN):
            return ReviewLevel.L3
        if user.role == Role.LAWYER:
            prof = (
                await self.db.execute(
                    select(LawyerProfile).where(LawyerProfile.user_id == user.id)
                )
            ).scalars().first()
            return ReviewLevel.L3 if (prof is not None and prof.can_l3_review) else ReviewLevel.L2
        return ReviewLevel.L1

    async def records(self, review_id: int) -> list[ReviewRecord]:
        rows = (
            await self.db.execute(
                select(ReviewRecord).where(ReviewRecord.review_id == review_id).order_by(ReviewRecord.id.asc())
            )
        ).scalars().all()
        return list(rows)

    async def _notify_decided(
        self, r: Review, *, actor: User, comment: Optional[str], outcome: str
    ) -> None:
        """复核出结论后通知**承办律师**（`REVIEW_DECIDED`）。

        ## 只对「律师需要行动或有实质后果」的结论发通知

        | 结论 | 是否通知 | 理由 |
        |------|---------|------|
        | `REVISION_REQUESTED` | ✅ | 要律师去改，不通知就卡住 |
        | `REJECTED`（作废） | ✅ | 有实质后果，必须知道 |
        | `APPROVED` 且达到要求级别（CONFIRMED） | ✅ | 可以定稿/归档，是推进信号 |
        | `APPROVED` 但级别不足（待更高级终审） | ❌ | 律师**无事可做**，发了只会变成「知道了但做不了什么」的噪音 |

        最后一条是有意为之：通知系统的价值取决于**信噪比**。一条不需要
        任何行动的通知，代价不是它自己占的那一行，而是它稀释了
        「待接单」「待复核」这些真通知的注意力。
        """
        if r.case_id is None:
            return
        case = await self.db.get(Case, r.case_id)
        if case is None or not case.lawyer_id:
            return
        # 复核人就是承办律师时不必自我通知（律师复核自己的产出）
        if case.lawyer_id == actor.id:
            return

        who = actor.full_name or actor.username
        snippet = (comment or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:80] + "…"

        if outcome == "REVISION":
            content = f"《{case.title}》复核要求修改（{who}）" + (f"：{snippet}" if snippet else "")
        elif outcome == "REJECTED":
            content = f"《{case.title}》复核未通过，已作废（{who}）" + (f"：{snippet}" if snippet else "")
        else:
            content = f"《{case.title}》复核已通过，可以定稿归档（{who}）"

        await notify(
            self.db,
            tenant_id=r.tenant_id,
            user_id=case.lawyer_id,
            type=NotificationType.REVIEW_DECIDED,
            content=content,
            # ref 指向**复核任务**而非案件：律师要处理的是这次复核，
            # 直接进复核页比先进案件再找复核入口少一次跳转
            ref_type="review",
            ref_id=r.id,
            payload={"outcome": outcome, "case_id": case.id, "comment": comment},
        )

    async def _on_confirmed(self, r: Review) -> None:
        """定稿后回写业务对象（当前支持案件分析），并推进案件状态。"""
        if r.target_type == ReviewTargetType.CASE_ANALYSIS:
            a = await self.db.get(CaseAnalysis, r.target_id)
            if a is not None:
                a.status = ReviewStatus.CONFIRMED
                a.confirmed_by = r.decided_by
                a.confirmed_at = _now()
                # 分析定稿后案件进入「已确认定稿」，方可归档
                case = await self.db.get(Case, a.case_id)
                if case is not None:
                    case.status = CaseStatus.CONFIRMED

    async def _record(
        self,
        review: Review,
        action: str,
        *,
        actor_id: Optional[int] = None,
        actor_role: Optional[str] = None,
        level: Optional[ReviewLevel] = None,
        from_status: Optional[ReviewStatus] = None,
        to_status: Optional[ReviewStatus] = None,
        changes: Optional[dict] = None,
        comment: Optional[str] = None,
    ) -> None:
        self.db.add(
            ReviewRecord(
                tenant_id=review.tenant_id,
                review_id=review.id,
                action=action,
                actor_id=actor_id,
                actor_role=actor_role,
                level=level,
                from_status=from_status.value if from_status else None,
                to_status=to_status.value if to_status else None,
                changes=changes,
                comment=comment,
            )
        )


def _now() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")
