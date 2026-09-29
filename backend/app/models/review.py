"""复核工作流模型（PRD 5.5，贯穿双产品线）。

`Review` 同时承担 AIAcquisition `AgentApproval` 的人工审批队列职责：
命中强制复核清单时创建待审记录，未确认则业务对象不可定稿 / 不可归档。
"""
from typing import Optional

from sqlalchemy import Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import ReviewDecision, ReviewLevel, ReviewStatus, ReviewTargetType


class Review(Base, TenantMixin, TimestampMixin):
    """复核任务：对一个业务对象（分析 / 文书 / 证据清单 / 合规报告）的复核。"""

    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[ReviewTargetType] = mapped_column(
        Enum(ReviewTargetType, native_enum=False, length=32)
    )
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    case_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False, length=32), default=ReviewStatus.DRAFT
    )
    # 要求的复核级别（强制复核命中时升级为 L2/L3）
    required_level: Mapped[ReviewLevel] = mapped_column(
        Enum(ReviewLevel, native_enum=False, length=8), default=ReviewLevel.L2
    )
    # 已满足的最高级别
    satisfied_level: Mapped[Optional[ReviewLevel]] = mapped_column(
        Enum(ReviewLevel, native_enum=False, length=8), nullable=True
    )
    # 是否为强制复核（命中 PRD 五类清单）
    is_forced: Mapped[bool] = mapped_column(Integer, default=0)
    # 命中原因列表：[{rule_code, reason, level}]
    forced_hits: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)

    assignee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decided_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decision: Mapped[Optional[ReviewDecision]] = mapped_column(
        Enum(ReviewDecision, native_enum=False, length=32), nullable=True
    )
    decided_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (Index("ix_reviews_tenant_status", "tenant_id", "status"),)


class ReviewRecord(Base, TenantMixin, TimestampMixin):
    """复核留痕：记录 AI 输出 -> 律师修改 -> 确认的完整过程（谁、何时、改了什么）。"""

    __tablename__ = "review_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[int] = mapped_column(Integer, index=True)
    # 动作：CREATE / EDIT / SUBMIT / APPROVE / REJECT / REQUEST_REVISION / ARCHIVE / VOID
    action: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    level: Mapped[Optional[ReviewLevel]] = mapped_column(
        Enum(ReviewLevel, native_enum=False, length=8), nullable=True
    )
    # 状态流转前后
    from_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # 变更内容（字段名级 diff 或批注文本），不落敏感全文
    changes: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
