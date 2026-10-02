"""咨询报告草稿（P0 解耦：四段式从客户可见回复中剥离，转后台草稿）。

客户在 /qa 或 IM 中只看到自然对话；四段式作为 `ConsultReport`(草稿态) 落库，
供后续律师复核（ReviewTargetType.CONSULT_REPORT）确认后回流给客户。
"""
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col


class ConsultReportStatus(str, PyEnum):
    """草稿状态：DRAFT=AI 初稿（客户不可见）；APPROVED=律师已确认定稿；REJECTED=驳回。"""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ConsultReport(Base, TenantMixin, TimestampMixin):
    """咨询报告：四段式草稿 + 自然回复，关联会话/案件（可选）。"""

    __tablename__ = "consult_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 关联会话（IM 场景有；/qa 匿名场景可空）
    conversation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=True, index=True
    )
    case_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("cases.id"), nullable=True, index=True
    )
    # 提问用户（/qa 登录态有；IM 可空）
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    # 原始提问
    question: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 客户侧可见的自然对话回复
    answer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # 四段式草稿（后台，客户不可见；律师复核对象）
    draft_sections: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    # 引用溯源（随草稿进入复核）
    citations: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 草稿状态
    status: Mapped[ConsultReportStatus] = mapped_column(
        Enum(ConsultReportStatus, native_enum=False, length=16),
        default=ConsultReportStatus.DRAFT,
    )
    # 指派 / 确认律师
    lawyer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 律师确认后的定稿（可与草稿不同）
    final_report: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    # 关联复核任务
    review_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
