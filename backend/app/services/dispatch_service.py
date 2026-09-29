"""派单服务：指定律师 / 系统派单 / 律师抢单 三策略（PRD 5.2）。

- 指定律师（DESIGNATED）：100% 绑定正确，直接使用扫码 / 名片绑定的律师。
- 系统派单（AUTO）：经 dispatch_rules 引擎 + 专业领域 / 负载均衡匹配。
- 律师抢单（POOL）：不自动指派，进入派单池，律师主动接单。
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ErrorCode,
    InvalidStateError,
    NotFoundError,
)
from app.models.case import Case, CaseEvent, Dispatch
from app.models.enums import (
    CaseStatus,
    DispatchMode,
    DispatchStatus,
    NotificationType,
)
from app.models.identity import LawyerProfile, User
from app.services.notification_service import notify
from app.workflows import dispatch_rules


class DispatchService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ---------------- 候选律师 ----------------
    async def _candidates(self, tenant_id: str) -> list[LawyerProfile]:
        result = await self.db.execute(
            select(LawyerProfile)
            .join(User, User.id == LawyerProfile.user_id)
            # 注意：available 是 Boolean 列，SQLite 下 == 1 可行，Postgres 必须用 is_(True)
            .where(LawyerProfile.tenant_id == tenant_id, LawyerProfile.available.is_(True))
        )
        return list(result.scalars().all())

    # ---------------- 三策略 ----------------
    async def dispatch(
        self,
        case: Case,
        *,
        mode: DispatchMode,
        bind_lawyer_id: Optional[int] = None,
        candidate_lawyer_ids: Optional[list[int]] = None,
    ) -> Dispatch:
        """创建派单记录并据策略指派或入池。"""
        if case.status not in (CaseStatus.INTAKE, CaseStatus.PENDING_DISPATCH):
            raise InvalidStateError("案件当前状态不可派单", code=ErrorCode.CASE_INVALID_STATE)

        if mode == DispatchMode.DESIGNATED:
            if bind_lawyer_id is None:
                raise BadRequestError("指定律师派单缺少律师标识", code=ErrorCode.DISPATCH_NO_CANDIDATE)
            lawyer_id = bind_lawyer_id
            reason = "客户指定律师（扫码绑定，100% 命中）"
            score = 100.0
        elif mode == DispatchMode.POOL:
            lawyer_id = None
            reason = "进入抢单池，等待律师接单"
            score = None
        else:  # AUTO
            lawyer_id, score, reason = await self._auto_match(case, candidate_lawyer_ids)

        disp = Dispatch(
            case_id=case.id,
            lawyer_id=lawyer_id,
            mode=mode,
            status=DispatchStatus.PENDING,
            score=score,
            reason=reason,
        )
        self.db.add(disp)
        await self.db.flush()

        case.status = CaseStatus.PENDING_DISPATCH if lawyer_id is None else CaseStatus.DISPATCHED
        if lawyer_id is not None:
            case.lawyer_id = lawyer_id
            await self._on_assigned(case, disp, lawyer_id)
        else:
            await self._notify_pool(case, disp)
        return disp

    async def _auto_match(
        self, case: Case, candidate_lawyer_ids: Optional[list[int]]
    ) -> tuple[Optional[int], Optional[float], str]:
        strategy = await dispatch_rules.resolve_strategy(self.db, case)
        candidates = await self._candidates(case.tenant_id)
        if candidate_lawyer_ids:
            candidates = [c for c in candidates if c.user_id in candidate_lawyer_ids]
        if not candidates:
            raise BadRequestError("当前无可接单律师", code=ErrorCode.DISPATCH_NO_CANDIDATE)

        # 专业领域匹配得分
        def _spec_score(p: LawyerProfile) -> float:
            specs = {s.strip() for s in (p.specialties or "").split(",") if s.strip()}
            if case.dispute_type and case.dispute_type in specs:
                return 50.0
            return 10.0

        if strategy == "LOAD_BALANCE":
            # 负载均衡：在手案件越少越优先
            ranked = sorted(candidates, key=lambda p: p.active_case_count)
        elif strategy == "ROUND_ROBIN":
            ranked = sorted(candidates, key=lambda p: p.id % 1000)
        else:  # SPECIALTY_MATCH
            ranked = sorted(candidates, key=lambda p: -(_spec_score(p) + p.practice_years * 2 - p.active_case_count))

        best = ranked[0]
        score = _spec_score(best) + best.practice_years * 2 - best.active_case_count
        return best.user_id, float(score), f"按策略 {strategy} 命中专业领域匹配"

    # ---------------- 接单 / 抢单 ----------------
    async def accept(self, dispatch_id: int, lawyer_id: int) -> Dispatch:
        disp = await self._get(dispatch_id)
        if disp.status != DispatchStatus.PENDING:
            raise ConflictError("该派单已被处理", code=ErrorCode.CONFLICT)
        case = await self._case(disp.case_id)
        if disp.mode == DispatchMode.DESIGNATED and disp.lawyer_id != lawyer_id:
            raise BadRequestError("非指定律师无法接此单", code=ErrorCode.DISPATCH_RULE_CONFLICT)
        disp.lawyer_id = lawyer_id
        disp.status = DispatchStatus.ACCEPTED
        case.lawyer_id = lawyer_id
        case.status = CaseStatus.ACCEPTED
        await self._on_assigned(case, disp, lawyer_id)
        await self._notify_accepted(case, lawyer_id)
        return disp

    async def grab(self, dispatch_id: int, lawyer_id: int) -> Dispatch:
        """抢单池接单（POOL 模式通用入口，复用 accept 逻辑）。"""
        return await self.accept(dispatch_id, lawyer_id)

    async def _notify_accepted(self, case: Case, lawyer_id: int) -> None:
        """接单后通知**客户**（`CASE_ACCEPTED`）。

        为什么通知客户而不是律师本人：律师刚刚亲手点了「接单」，
        再推一条「你已接单」是纯噪音。真正需要被知会的是**等待中的客户**——
        「谁在办我的案子、什么时候开始办」是委托人最焦虑、也最该被主动告知
        的信息；在传统模式下这通电话通常由律所行政打，属于典型的人工成本。
        """
        if not case.client_user_id:
            return  # 未绑定客户端账号（如线下录入的案件），无接收人
        if case.client_user_id == lawyer_id:
            return  # 律师本人即客户（自助办案场景），不给自己发通知

        lawyer = await self.db.get(User, lawyer_id)
        who = (lawyer.full_name or lawyer.username) if lawyer is not None else "承办律师"
        await notify(
            self.db,
            tenant_id=case.tenant_id,
            user_id=case.client_user_id,
            type=NotificationType.CASE_ACCEPTED,
            content=f"{who} 已接单，正在办理《{case.title}》",
            ref_type="case",
            ref_id=case.id,
        )

    # ---------------- 内部 ----------------
    async def _on_assigned(self, case: Case, disp: Dispatch, lawyer_id: int) -> None:
        self.db.add(
            CaseEvent(
                case_id=case.id,
                event_type="DISPATCH",
                title="已派单",
                description=disp.reason or "系统派单",
                actor_user_id=lawyer_id,
            )
        )
        target = await self.db.get(User, lawyer_id)
        if target is not None:
            await notify(
                self.db,
                tenant_id=case.tenant_id,
                user_id=lawyer_id,
                type=NotificationType.DISPATCH_CREATED,
                content=f"您有新案件待接：《{case.title}》",
                ref_type="case",
                ref_id=case.id,
            )
            # 维护律师在手案件数
            prof = (
                await self.db.execute(
                    select(LawyerProfile).where(LawyerProfile.user_id == lawyer_id)
                )
            ).scalars().first()
            if prof is not None:
                prof.active_case_count = (prof.active_case_count or 0) + 1

    async def _notify_pool(self, case: Case, disp: Dispatch) -> None:
        # 通知全所可接单律师（演示：不逐个查，写一条事件）
        self.db.add(
            CaseEvent(
                case_id=case.id,
                event_type="DISPATCH",
                title="进入抢单池",
                description="等待律师主动接单",
            )
        )

    async def _get(self, dispatch_id: int) -> Dispatch:
        disp = await self.db.get(Dispatch, dispatch_id)
        if disp is None:
            raise NotFoundError("派单不存在", code=ErrorCode.DISPATCH_NOT_FOUND)
        return disp

    async def _case(self, case_id: int) -> Case:
        case = await self.db.get(Case, case_id)
        if case is None:
            raise NotFoundError("案件不存在", code=ErrorCode.CASE_NOT_FOUND)
        return case
