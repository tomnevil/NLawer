"""统一消息协议（多 IM 解耦核心）。

不同 IM 平台（网页模拟 / 企业微信 / 飞书）的消息结构各异，这里用
`UnifiedMessage` 归一化入站与出站消息，使上层业务（会话引擎、派单、
证据收集）无需关心来源渠道，规避 PRD 风险清单中「IM 平台接口变更 / 封禁」。
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MessageType(str, Enum):
    """统一消息类型（对齐 models.enums.MessageType）。"""

    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    VOICE = "voice"
    VIDEO = "video"
    CARD = "card"
    EVENT = "event"


@dataclass(frozen=True)
class UnifiedMessage:
    """渠道无关的归一化消息。

    `tenant_id` 由机器人名片 / 二维码的绑定关系解析，保证请求落到正确的律所租户；
    `bind_lawyer_id` 为扫码指定的律师，保证 PRD 要求的「100% 绑定正确」。
    """

    channel: str  # "web_sim" | "wecom" | "feishu"
    external_user_id: str
    msg_type: MessageType
    text: Optional[str] = None
    media_url: Optional[str] = None
    tenant_id: Optional[str] = None  # 由绑定关系解析
    bind_lawyer_id: Optional[int] = None
    card_payload: Optional[dict] = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundMessage:
    """出站消息（发往 IM 平台）。"""

    to: str  # 外部用户标识
    msg_type: MessageType
    text: Optional[str] = None
    media_url: Optional[str] = None
    card_payload: Optional[dict] = None


class IMAdapter:
    """IM 适配器抽象基类。

    每个渠道实现 `parse_inbound`（平台回调 -> UnifiedMessage）与
    `send_outbound`（UnifiedMessage -> 平台接口）。
    """

    channel: str = "base"

    async def parse_inbound(self, payload: dict) -> UnifiedMessage:
        raise NotImplementedError

    async def send_outbound(self, msg: OutboundMessage) -> None:
        raise NotImplementedError

    async def resolve_tenant(self, external_user_id: str) -> Optional[str]:
        """由外部用户标识解析其归属律所租户（子类可覆盖）。"""
        return None

    async def resolve_bind_lawyer(self, external_user_id: str) -> Optional[int]:
        """由扫码 / 名片绑定解析指定律师（子类可覆盖）。"""
        return None

    def build_reply(self, to: str, text: str, *, card: Optional[dict] = None) -> OutboundMessage:
        return OutboundMessage(
            to=to,
            msg_type=MessageType.CARD if card is not None else MessageType.TEXT,
            text=text,
            card_payload=card,
        )
