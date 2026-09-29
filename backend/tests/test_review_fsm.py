"""复核状态机硬约束测试（PRD 5.5）。

覆盖：
- 合法转移白名单
- 非法流转一律拒绝
- 未提交复核不可定稿
- 未确认不可归档
- 定稿时复核级别必须达标
"""
import pytest

from app.models.enums import ReviewLevel, ReviewStatus
from app.workflows.review_fsm import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    ReviewStateMachine,
)


@pytest.mark.parametrize(
    "cur,nxt",
    [
        (ReviewStatus.DRAFT, ReviewStatus.LAWYER_EDITING),
        (ReviewStatus.DRAFT, ReviewStatus.PENDING_CONFIRM),
        (ReviewStatus.LAWYER_EDITING, ReviewStatus.PENDING_CONFIRM),
        (ReviewStatus.PENDING_CONFIRM, ReviewStatus.CONFIRMED),
        (ReviewStatus.PENDING_CONFIRM, ReviewStatus.LAWYER_EDITING),
        (ReviewStatus.CONFIRMED, ReviewStatus.ARCHIVED),
        (ReviewStatus.CONFIRMED, ReviewStatus.LAWYER_EDITING),
        (ReviewStatus.VOIDED, ReviewStatus.DRAFT),
    ],
)
def test_allowed_transitions(cur, nxt):
    assert ReviewStateMachine.can_transition(cur, nxt) is True


@pytest.mark.parametrize(
    "cur,nxt",
    [
        (ReviewStatus.DRAFT, ReviewStatus.ARCHIVED),   # 跳过定稿直接归档
        (ReviewStatus.DRAFT, ReviewStatus.CONFIRMED),   # 跳过提交直接定稿
        (ReviewStatus.LAWYER_EDITING, ReviewStatus.CONFIRMED),
        (ReviewStatus.PENDING_CONFIRM, ReviewStatus.ARCHIVED),  # 未确认不可归档
        (ReviewStatus.ARCHIVED, ReviewStatus.CONFIRMED),        # 终态不可流转
        (ReviewStatus.ARCHIVED, ReviewStatus.DRAFT),
    ],
)
def test_illegal_transitions_rejected(cur, nxt):
    assert ReviewStateMachine.can_transition(cur, nxt) is False
    with pytest.raises(InvalidTransitionError):
        ReviewStateMachine.assert_transition(cur, nxt)


def test_white_list_consistency():
    # ARCHIVED 为终态：无任何后继
    assert ALLOWED_TRANSITIONS[ReviewStatus.ARCHIVED] == set()


def test_confirm_requires_pending_confirm():
    # 从草稿尝试直接定稿 -> 拒绝
    with pytest.raises(InvalidTransitionError):
        ReviewStateMachine.assert_transition(
            ReviewStatus.DRAFT, ReviewStatus.CONFIRMED, required_level=ReviewLevel.L3
        )


def test_archive_requires_confirmed():
    with pytest.raises(InvalidTransitionError):
        ReviewStateMachine.assert_transition(
            ReviewStatus.PENDING_CONFIRM, ReviewStatus.ARCHIVED
        )


def test_confirm_blocks_when_level_insufficient():
    # 要求 L3，但仅 L2 复核通过 -> 拒绝
    with pytest.raises(InvalidTransitionError):
        ReviewStateMachine.assert_transition(
            ReviewStatus.PENDING_CONFIRM,
            ReviewStatus.CONFIRMED,
            required_level=ReviewLevel.L3,
            satisfied_level=ReviewLevel.L2,
        )


def test_confirm_ok_when_level_satisfied():
    ReviewStateMachine.assert_transition(
        ReviewStatus.PENDING_CONFIRM,
        ReviewStatus.CONFIRMED,
        required_level=ReviewLevel.L3,
        satisfied_level=ReviewLevel.L3,
    )
    # L3 满足 L1 要求
    ReviewStateMachine.assert_transition(
        ReviewStatus.PENDING_CONFIRM,
        ReviewStatus.CONFIRMED,
        required_level=ReviewLevel.L1,
        satisfied_level=ReviewLevel.L3,
    )


def test_level_satisfied_helper():
    assert ReviewStateMachine.level_satisfied(ReviewLevel.L3, ReviewLevel.L2) is False
    assert ReviewStateMachine.level_satisfied(ReviewLevel.L1, ReviewLevel.L2) is True
    assert ReviewStateMachine.level_satisfied(ReviewLevel.L2, None) is False
