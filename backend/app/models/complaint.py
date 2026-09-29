"""投诉举报记录（P0-13）。

法律依据：《生成式人工智能服务管理暂行办法》**第十五条**——
「提供者应当建立健全**投诉、举报**机制，设置便捷的投诉、举报入口，
公布处理流程和反馈时限，及时受理、处理公众投诉举报并反馈处理结果。」

设计要点：
1. **入口必须便捷且无需登录也能到达**（公众投诉不能要求先注册），
   因此 API 层允许匿名提交，仅记录来源 IP 作为追溯依据。
2. **必须有处理流程与反馈时限**。`due_at` 落库即承诺时限（默认 15 个自然日，
   与《网络信息内容生态治理规定》的通常口径一致），逾期未处理即违约。
3. **处理结果必须可回查**。`handle_note` / `result` 落库，
   投诉人可凭 `ticket_no` 查询进展，形成闭环。
4. 与 `moderation_records` 通过 `related_moderation_id` 关联：
   误判申诉能直接定位到当时那条审核判定，避免"重新审一遍却查不到原判"。
"""
import datetime
import secrets
from enum import Enum
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col


class ComplaintType(str, Enum):
    """投诉举报类型。"""

    CONTENT_MISJUDGED = "CONTENT_MISJUDGED"  # 误判申诉：认为内容被错误拦截
    ILLEGAL_CONTENT = "ILLEGAL_CONTENT"      # 举报违法内容（AI 生成或用户发布）
    SERVICE_ABUSE = "SERVICE_ABUSE"          # 举报他人滥用服务
    OTHER = "OTHER"


class ComplaintStatus(str, Enum):
    """处理状态。"""

    PENDING = "PENDING"          # 待受理
    PROCESSING = "PROCESSING"    # 处理中
    RESOLVED = "RESOLVED"        # 已办结
    REJECTED = "REJECTED"        # 不予受理（需说明理由）


#: 承诺反馈时限（自然日）。第十五条要求「公布处理流程和反馈时限」。
COMPLAINT_DUE_DAYS = 15


def new_ticket_no() -> str:
    """生成工单号：`AI` + UTC 日期 + 6 位随机，便于人工口头转述。"""
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    return f"AI{day}{secrets.token_hex(3).upper()}"


class Complaint(Base, TenantMixin, TimestampMixin):
    """一条投诉/举报工单。"""

    __tablename__ = "complaints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: 对外工单号（投诉人凭此查询进展）
    ticket_no: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=ComplaintStatus.PENDING.value, index=True
    )

    # ---- 投诉人（可为匿名公众）----
    #: 登录用户提交时记录；匿名公众投诉为 None
    reporter_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    contact: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # ---- 投诉内容 ----
    #: 投诉人描述。**允许留存正文**——这是投诉材料本身，不是违规内容，
    #: 删除会让投诉无法被处理（与 moderation_records 不存原文的原则不冲突）。
    description: Mapped[str] = mapped_column(Text)
    #: 被投诉的对象（如 AI 回答的会话 id / 文书 id）
    target_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: 关联的审核判定（误判申诉场景，便于直接调阅原判）
    related_moderation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ---- 处理（流程 + 时限 + 反馈）----
    #: 承诺反馈截止时间（超出即违约，管理员看板按此排序）
    due_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    handler_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    handle_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    handled_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: 是否已向投诉人反馈结果
    feedback_sent: Mapped[bool] = mapped_column(Integer, default=0)

    # ---- 来源 ----
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    detail: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
