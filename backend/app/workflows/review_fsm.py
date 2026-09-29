"""复核状态机（PRD 5.5 硬约束核心）。

借鉴 zunicorn-agent `workflows/` 的「状态 -> 合法后继状态」白名单表设计：
非法流转直接抛 `InvalidTransitionError`，从机制上保证
「未确认不可定稿」「未定稿不可归档」。

本文件为纯逻辑（无 DB 依赖），便于单元测试覆盖全部转移组合。
"""
from typing import Optional

from app.core.errors import ErrorCode, InvalidStateError
from app.models.enums import ReviewLevel, ReviewStatus


class InvalidTransitionError(InvalidStateError):
    """非法状态流转。"""

    def __init__(self, message: str, *, cur: Optional[ReviewStatus] = None, nxt: Optional[ReviewStatus] = None):
        super().__init__(message, code=ErrorCode.REVIEW_TRANSITION_DENIED)
        self.details = {
            "from": cur.value if cur else None,
            "to": nxt.value if nxt else None,
        }


# 合法转移白名单：未列出的组合一律拒绝
ALLOWED_TRANSITIONS: dict[ReviewStatus, set[ReviewStatus]] = {
    ReviewStatus.DRAFT: {ReviewStatus.LAWYER_EDITING, ReviewStatus.PENDING_CONFIRM, ReviewStatus.VOIDED},
    ReviewStatus.LAWYER_EDITING: {ReviewStatus.PENDING_CONFIRM, ReviewStatus.DRAFT, ReviewStatus.VOIDED},
    ReviewStatus.PENDING_CONFIRM: {ReviewStatus.CONFIRMED, ReviewStatus.LAWYER_EDITING, ReviewStatus.VOIDED},
    # 已确认可回溯修改（回到律师修改中），但不可直接跳过归档
    ReviewStatus.CONFIRMED: {ReviewStatus.ARCHIVED, ReviewStatus.LAWYER_EDITING},
    ReviewStatus.ARCHIVED: set(),  # 终态
    ReviewStatus.VOIDED: {ReviewStatus.DRAFT},
}

_LEVEL_ORDER = {ReviewLevel.L1: 1, ReviewLevel.L2: 2, ReviewLevel.L3: 3}


class ReviewStateMachine:
    @staticmethod
    def can_transition(cur: ReviewStatus, nxt: ReviewStatus) -> bool:
        return nxt in ALLOWED_TRANSITIONS.get(cur, set())

    @staticmethod
    def assert_transition(
        cur: ReviewStatus,
        nxt: ReviewStatus,
        *,
        actor_role: str = "",
        required_level: ReviewLevel = ReviewLevel.L1,
        satisfied_level: Optional[ReviewLevel] = None,
    ) -> None:
        """校验流转合法性 + 复核级别达标。

        1. 白名单校验：非法组合抛 InvalidTransitionError
        2. 终稿/归档校验：未确认不可定稿（CONFIRMED）、不可归档（ARCHIVED）
        3. 级别校验：定稿时 satisfied_level 必须 >= required_level
        """
        if not ReviewStateMachine.can_transition(cur, nxt):
            raise InvalidTransitionError(
                f"不允许的状态流转：{cur.value} -> {nxt.value}", cur=cur, nxt=nxt
            )

        if nxt == ReviewStatus.CONFIRMED:
            if cur not in (ReviewStatus.PENDING_CONFIRM,):
                raise InvalidTransitionError("未提交复核不可定稿", cur=cur, nxt=nxt)
            if satisfied_level is None or _LEVEL_ORDER[satisfied_level] < _LEVEL_ORDER[required_level]:
                raise InvalidTransitionError(
                    f"复核级别不足：要求 {required_level.value}，当前 {satisfied_level.value if satisfied_level else '无'}",
                    cur=cur,
                    nxt=nxt,
                )

        if nxt == ReviewStatus.ARCHIVED and cur != ReviewStatus.CONFIRMED:
            raise InvalidTransitionError("未确认定稿不可归档", cur=cur, nxt=nxt)

    @staticmethod
    def level_satisfied(required: ReviewLevel, satisfied: Optional[ReviewLevel]) -> bool:
        if satisfied is None:
            return False
        return _LEVEL_ORDER[satisfied] >= _LEVEL_ORDER[required]
