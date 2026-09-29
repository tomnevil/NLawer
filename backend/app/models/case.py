"""案件、派单与案件事件模型（产品线 A 核心）。"""
from typing import Optional

from sqlalchemy import Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import (
    CaseGrade,
    CaseStatus,
    DispatchMode,
    DispatchStatus,
    IntentType,
)


class Case(Base, TenantMixin, TimestampMixin):
    """案件：由咨询转化而来的结构化业务主体。"""

    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300))
    # 客户与承办律师
    client_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    lawyer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("conversations.id"), nullable=True
    )

    status: Mapped[CaseStatus] = mapped_column(
        Enum(CaseStatus, native_enum=False, length=32), default=CaseStatus.INTAKE
    )
    intent: Mapped[Optional[IntentType]] = mapped_column(
        Enum(IntentType, native_enum=False, length=32), nullable=True
    )
    grade: Mapped[CaseGrade] = mapped_column(
        Enum(CaseGrade, native_enum=False, length=8), default=CaseGrade.C
    )
    # 纠纷类型：劳动争议 / 合同纠纷 / 婚姻家庭 / 交通事故 / 侵权纠纷 ...
    dispute_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # 案件要素（PRD 5.3 案件摘要字段）
    party_a: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    party_b: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    focus: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    claim_amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    urgency: Mapped[int] = mapped_column(Integer, default=0)  # 0-3，越高越紧急
    complexity: Mapped[int] = mapped_column(Integer, default=0)  # 0-3

    # 客户是否明确要求出具正式意见（触发强制 L3 复核）
    require_formal_opinion: Mapped[bool] = mapped_column(Integer, default=0)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_cases_tenant_status", "tenant_id", "status"),
        Index("ix_cases_lawyer_status", "lawyer_id", "status"),
    )


class CaseEvent(Base, TenantMixin, TimestampMixin):
    """案件事件（时间线节点）：材料提交、状态流转、复核结论等。"""

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 事件发生时间（业务时间，可与 created_at 不同）
    occurred_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)


class Dispatch(Base, TenantMixin, TimestampMixin):
    """派单记录：一次派单尝试（指定律师 / 系统派单 / 抢单）。"""

    __tablename__ = "dispatches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), index=True)
    lawyer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    mode: Mapped[DispatchMode] = mapped_column(
        Enum(DispatchMode, native_enum=False, length=32), default=DispatchMode.AUTO
    )
    status: Mapped[DispatchStatus] = mapped_column(
        Enum(DispatchStatus, native_enum=False, length=32),
        default=DispatchStatus.PENDING,
    )
    # 匹配得分与命中规则说明，用于派单可解释性
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_dispatches_tenant_status", "tenant_id", "status"),
        Index("ix_dispatches_lawyer_status", "lawyer_id", "status"),
    )


class DispatchRule(Base, TenantMixin, TimestampMixin):
    """派单规则（可配置）：律所管理员自定义匹配策略。"""

    __tablename__ = "dispatch_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200))
    # 规则条件 JSON：
    # {"dispute_type": ["劳动争议"], "grade_in": ["S","A"], "min_practice_years": 3}
    conditions: Mapped[dict] = mapped_column(json_col(), default=dict)
    # 策略：SPECIALTY_MATCH / ROUND_ROBIN / LOAD_BALANCE / DESIGNATED
    strategy: Mapped[str] = mapped_column(String(64), default="SPECIALTY_MATCH")
    # 候选律师白名单（留空表示全所）
    candidate_lawyer_ids: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Integer, default=1)
