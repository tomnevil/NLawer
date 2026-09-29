"""会话与消息模型（IM 接待层）。

Conversation 是客户与律所的一条会话主线，可关联案件与指定律师；
Message 保存全部往来消息（含 AI、律师、客户、系统事件），支持多模态。
"""
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import (
    ConversationStatus,
    IMChannel,
    MessageSender,
    MessageType,
)


class Conversation(Base, TenantMixin, TimestampMixin):
    """会话：客户在 IM 中与律所机器人的一条对话主线。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 律所侧租户（机器人归属）
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # 客户用户 ID（可为空：IM 用户首次进入尚未注册时先留空）
    client_user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    # IM 侧外部用户标识
    external_user_id: Mapped[str] = mapped_column(String(200), index=True)
    channel: Mapped[IMChannel] = mapped_column(
        Enum(IMChannel, native_enum=False, length=32), default=IMChannel.WEB_SIM
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, native_enum=False, length=32),
        default=ConversationStatus.BOT,
    )
    # 扫码 / 名片绑定的指定律师（PRD 要求 100% 绑定正确）
    bind_lawyer_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    case_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("cases.id"), nullable=True
    )
    # 会话上下文快照：已收集的意图、实体、待补材料等
    context: Mapped[dict] = mapped_column(json_col(), default=dict)
    last_message_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class Message(Base, TenantMixin, TimestampMixin):
    """消息：IM 往来记录，支持文本 / 图片 / 文件 / 语音 / 卡片 / 事件。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("conversations.id"), index=True
    )
    sender: Mapped[MessageSender] = mapped_column(
        Enum(MessageSender, native_enum=False, length=32), default=MessageSender.CLIENT
    )
    msg_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, native_enum=False, length=32), default=MessageType.TEXT
    )
    # 文本内容；语音消息存放转写结果
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 媒体文件相对路径（存储于 LOCAL_STORAGE_PATH）
    media_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 结构化卡片负载：材料清单 / 满意度评价 / 四段式回答
    card_payload: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    # 该消息关联的引用 ID 列表，前端据此渲染溯源角标
    citation_ids: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 发送者用户 ID（AI 消息为空）
    sender_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
