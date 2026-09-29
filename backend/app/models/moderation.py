"""内容审核记录（P0-13）。

对应《生成式人工智能服务管理暂行办法》第十四条「保存有关记录」的强制要求。

设计要点：
1. **不落违规原文**。原文入库存的是二次违规内容，且可能被前端回显。
   本表只存 `content_hash`（可追溯同一内容）、`content_len`、
   命中分类与级别，以及**脱敏摘要**。
2. **必须能还原"谁、何时、从何处、什么内容、如何处置"**——等保与算法备案
   的检查口径与审计日志一致，因此字段刻意对齐 `audit_logs`。
3. **上报状态必须显式跟踪**。第十四条要求"向有关主管部门报告"，
   未配通道时置 `PENDING`，由人工兜底——绝不能静默丢弃。
"""
from enum import Enum
from typing import Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col


class ReportStatus(str, Enum):
    """监管上报状态（第十四条）。"""

    NOT_REQUIRED = "NOT_REQUIRED"  # 未达上报门槛（REVIEW / BLOCK 以下）
    PENDING = "PENDING"            # 需上报但通道未配置 → 人工兜底
    REPORTED = "REPORTED"          # 已成功上报
    FAILED = "FAILED"              # 上报失败，需重试


class ModerationRecord(Base, TenantMixin, TimestampMixin):
    """一条内容审核留痕。"""

    __tablename__ = "moderation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ---- 主体（与 audit_logs 对齐，便于联合排查）----
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ---- 审核对象 ----
    #: input = 用户输入；output = AI 生成内容
    side: Mapped[str] = mapped_column(String(16), index=True)
    #: 业务场景：qa / document / compliance / contract_review / evidence / conversation
    scene: Mapped[str] = mapped_column(String(32), index=True)
    #: 关联资源（如 document_id / case_id），便于定位
    resource_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ---- 内容指纹（不落原文）----
    #: sha256 前 32 位。同一违规内容重复提交可聚类，也便于跨库比对
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    content_len: Mapped[int] = mapped_column(Integer, default=0)
    #: 归一化摘要哈希（用于识别"归一化后相同"的绕过变体）
    normalized_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # ---- 判定结果 ----
    level: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(32), index=True)
    #: 命中的类别列表（不含命中词，避免词库外泄）
    categories: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    #: 命中数与各条脱敏详情（分类/级别/匹配方式/matched_hash）
    detail: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    #: 审核后端：builtin / external / degraded
    backend: Mapped[str] = mapped_column(String(16), default="builtin")
    #: 是否降级判定（外部审核不可用时的 fail-open 放行）
    degraded: Mapped[bool] = mapped_column(Integer, default=0)

    # ---- 处置与上报（第十四条）----
    #: 是否已向用户拒答
    user_notified: Mapped[bool] = mapped_column(Integer, default=0)
    #: 是否需限制该用户功能（ESCALATE 级）
    user_restricted: Mapped[bool] = mapped_column(Integer, default=0)
    report_status: Mapped[str] = mapped_column(
        String(16), default=ReportStatus.NOT_REQUIRED.value, index=True
    )
    report_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- 来源 ----
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
