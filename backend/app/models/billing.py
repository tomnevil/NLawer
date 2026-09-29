"""用量与工单计费模型（PRD 5.7 / 11.1）。

收入预测借鉴 YouTubeBoardcast `monetization/revenue_projector.py` 的分项系数法：
按服务分项（订阅 / 案件服务 / 工单 / 增值）加权求和，得到 breakdown + total。
"""
from typing import Optional

from sqlalchemy import Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import UsageType, WorkOrderStatus


class Subscription(Base, TenantMixin, TimestampMixin):
    """订阅：律所按席位、企业按月按量套餐。"""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 套餐：FIRM_BASIC / FIRM_PRO / FIRM_FLAGSHIP / FREE / PERSONAL / ENT_BASIC ...
    plan_code: Mapped[str] = mapped_column(String(64))
    plan_name: Mapped[str] = mapped_column(String(200))
    # 席位数（律所）/ 子账号数（企业）
    seats: Mapped[int] = mapped_column(Integer, default=1)
    # 月费（分）
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    start_date: Mapped[str] = mapped_column(String(32))
    end_date: Mapped[str] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Integer, default=1)


class UsageQuota(Base, TenantMixin, TimestampMixin):
    """用量额度：按月按量套餐的额度与实时剩余（PRD 用量看板）。

    **唯一约束**：`(tenant_id, usage_type, period)` 必须唯一。
    没有它，并发首用时 `ensure_quota` 会插入多行同一额度，
    后续 `first()` 只读其中一行，其余成为僵尸数据（额度被"分裂"，
    实际可用额度被放大）。应用层只能缓解，数据库约束才是根治。
    """

    __tablename__ = "usage_quotas"
    __table_args__ = (
        Index(
            "uq_usage_quota_tenant_type_period",
            "tenant_id",
            "usage_type",
            "period",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    usage_type: Mapped[UsageType] = mapped_column(
        Enum(UsageType, native_enum=False, length=32)
    )
    # 账期，形如 2026-09
    period: Mapped[str] = mapped_column(String(16), index=True)
    limit_count: Mapped[int] = mapped_column(Integer, default=0)
    used_count: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def remaining(self) -> int:
        return max(self.limit_count - self.used_count, 0)

    @property
    def exhausted(self) -> bool:
        return self.limit_count > 0 and self.used_count >= self.limit_count


class UsageRecord(Base, TenantMixin, TimestampMixin):
    """用量流水：每次问答 / 文书 / 审查 / 扫描的实时扣减记录。"""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage_type: Mapped[UsageType] = mapped_column(
        Enum(UsageType, native_enum=False, length=32)
    )
    period: Mapped[str] = mapped_column(String(16), index=True)
    # 关联业务对象
    ref_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 是否因超量转为工单
    converted_to_work_order: Mapped[bool] = mapped_column(Integer, default=0)


class WorkOrder(Base, TenantMixin, TimestampMixin):
    """工单：超出定制量自动转工单，按单计费（可升级 AI + 人工律师）。"""

    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage_type: Mapped[UsageType] = mapped_column(
        Enum(UsageType, native_enum=False, length=32)
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[WorkOrderStatus] = mapped_column(
        Enum(WorkOrderStatus, native_enum=False, length=32),
        default=WorkOrderStatus.PENDING,
    )
    urgent: Mapped[bool] = mapped_column(Integer, default=0)
    # 金额（分）：标准价 + 加急上浮
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    # 升级为 AI + 人工律师处理
    escalate_to_lawyer: Mapped[bool] = mapped_column(Integer, default=0)
    assigned_lawyer_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 关联业务对象
    ref_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 计费审计
    billing_note: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)

    __table_args__ = (Index("ix_work_orders_tenant_status", "tenant_id", "status"),)
