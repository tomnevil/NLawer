"""派单服务：指定律师 / 系统派单 / 律师抢单 三策略（PRD 5.2）。

- 指定律师（DESIGNATED）：100% 绑定正确，直接使用扫码 / 名片绑定的律师。
- 系统派单（AUTO）：经 dispatch_rules 引擎 + 专业领域 / 负载均衡匹配。
- 律师抢单（POOL）：不自动指派，进入派单池，律师主动接单。
"""
from typing import Optional

from sqlalchemy import case as sql_case
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BadRequestError,
    ConflictError,
    ErrorCode,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
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
            tenant_id=case.tenant_id,
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
            # A9：只在「案件责任人真正发生变化」时调整在手案件数。
            # 以 case.lawyer_id 前后值为准（而不是 dispatch 次数），
            # 同一律师重复派单 +0；改派时旧律师 -1、新律师 +1。
            previous = case.lawyer_id
            case.lawyer_id = lawyer_id
            await self._sync_lawyer_load(previous, lawyer_id)
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
        """律师接单（三种策略的统一收口，A2 / A3 / B1）。

        授权与规则（2026-11-09 收紧）：

        - **律师档案必须存在且可用**：`LawyerProfile` 缺失（A3(c)：无档案的
          `LAWYER` 账号）或 `available=False` 一律 403。缺档案的律师接单后
          会在 `_on_assigned` 里找不到档案、在手案件数永远不记，等于责任链
          断点，因此必须在写入前拒绝。
        - `POOL`：仅当 `lawyer_id IS NULL`（尚无归属）时允许抢单；已有归属
          说明这单不再属于池子 ⇒ 409。
        - `AUTO` / `DESIGNATED`：仅允许**被指派的律师本人**接单，其余人 403。
          旧实现只拦 `DESIGNATED`，`AUTO` 的律师一栏可被任意律师覆盖。
        - **并发**：以条件更新 `UPDATE ... WHERE status='PENDING'` 抢占，
          `rowcount == 0` 即已被他人接走 ⇒ 409。仅靠先读后写会出现两个律师
          同时读到 `PENDING`、两次写都“成功”（B1）。
        """
        disp = await self._get(dispatch_id)
        if disp.status != DispatchStatus.PENDING:
            raise ConflictError("该派单已被处理", code=ErrorCode.CONFLICT)

        # A3(c)：律师档案必须存在且可接单
        prof = (
            await self.db.execute(
                select(LawyerProfile).where(
                    LawyerProfile.user_id == lawyer_id,
                    LawyerProfile.tenant_id == disp.tenant_id,
                )
            )
        ).scalars().first()
        if prof is None:
            raise PermissionDeniedError(
                "当前账号没有律师档案，无法接单", code=ErrorCode.GRAB_NOT_ALLOWED
            )
        if not prof.available:
            raise PermissionDeniedError(
                "当前律师档案不可用（已停用或暂停接单）", code=ErrorCode.GRAB_NOT_ALLOWED
            )

        # A2 / A3：指派规则收紧
        if disp.mode == DispatchMode.POOL:
            if disp.lawyer_id is not None:
                raise ConflictError(
                    "该派单已指定律师，不能抢单", code=ErrorCode.DISPATCH_RULE_CONFLICT
                )
        elif disp.lawyer_id != lawyer_id:
            raise PermissionDeniedError(
                "该派单已指派给其他律师，不能接单",
                code=ErrorCode.DISPATCH_RULE_CONFLICT,
                details={"dispatch_mode": str(disp.mode)},
            )

        # B1：条件更新是唯一写入口，输家拿不到行
        result = await self.db.execute(
            update(Dispatch)
            .where(Dispatch.id == disp.id, Dispatch.status == DispatchStatus.PENDING)
            .values(lawyer_id=lawyer_id, status=DispatchStatus.ACCEPTED)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            raise ConflictError("该派单已被其他律师接单", code=ErrorCode.CONFLICT)

        disp.lawyer_id = lawyer_id
        disp.status = DispatchStatus.ACCEPTED
        case = await self._case(disp.case_id)
        # A9：接单是「案件责任人落定」的时点。以 case.lawyer_id 前后值同步计数：
        #   POOL（此前无归属）→ +1；AUTO/DESIGNATED（派单时已记过）→ 前后同人，+0。
        # 这样「派单 + 接单」整条链路只会让在手案件数 +1。
        previous = case.lawyer_id
        case.lawyer_id = lawyer_id
        case.status = CaseStatus.ACCEPTED
        await self._sync_lawyer_load(previous, lawyer_id)
        # POOL 到接单这一刻才建立归属，事件/通知需补记一次；
        # AUTO / DESIGNATED 在创建派单时已由 `_on_assigned` 记过。
        if disp.mode == DispatchMode.POOL:
            await self._on_assigned(case, disp, lawyer_id)
        await self._notify_accepted(case, lawyer_id)
        return disp

    async def grab(self, dispatch_id: int, lawyer_id: int) -> Dispatch:
        """抢单池专用入口：仅 `POOL` 派单可抢（其余规则由 `accept` 统一收口）。"""
        disp = await self._get(dispatch_id)
        if disp.mode != DispatchMode.POOL:
            raise ConflictError(
                "该派单不是抢单池派单", code=ErrorCode.GRAB_NOT_ALLOWED
            )
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
                tenant_id=case.tenant_id,
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

    async def _sync_lawyer_load(self, previous: Optional[int], new: Optional[int]) -> None:
        """按案件责任人「前后值」增减在手案件数（A9 唯一计数入口）。

        幂等：`previous == new` 时不做任何事，因此重复派单 / 重复接单都不会双计数。
        改派（old -> new）一次调用即完成旧律师 -1、新律师 +1。
        """
        if previous == new:
            return
        if previous is not None:
            await adjust_active_case_count(self.db, previous, -1)
        if new is not None:
            await adjust_active_case_count(self.db, new, 1)

    async def _notify_pool(self, case: Case, disp: Dispatch) -> None:
        # 通知全所可接单律师（演示：不逐个查，写一条事件）
        self.db.add(
            CaseEvent(
                tenant_id=case.tenant_id,
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


async def adjust_active_case_count(
    db: AsyncSession, lawyer_id: Optional[int], delta: int
) -> None:
    """原子增减律师在手案件数（A9）。

    用单条条件 UPDATE 而不是「读-改-写」ORM 属性：并发派单 / 结案时不会因
    丢失更新把计数算错；同时用 `CASE` 表达式保证不会减到负数。
    无律师归属或变化量为 0 时直接返回。
    """
    if lawyer_id is None or delta == 0:
        return
    base = func.coalesce(LawyerProfile.active_case_count, 0)
    next_value = base + delta
    await db.execute(
        update(LawyerProfile)
        .where(LawyerProfile.user_id == lawyer_id)
        .values(active_case_count=sql_case((next_value < 0, 0), else_=next_value))
        .execution_options(synchronize_session=False)
    )
