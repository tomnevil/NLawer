"""内置网页模拟 IM 适配器。

无需任何第三方凭证即可演示完整接待链路：客户在 `apps/im` 网页端发消息，
后端经此适配器归一化后交给会话引擎处理，再以 OutboundMessage 原路回推。
"""
from typing import Optional

from loguru import logger

from app.im.protocol import IMAdapter, MessageType, OutboundMessage, UnifiedMessage


class WebSimAdapter(IMAdapter):
    """网页模拟 IM：消息以 JSON 经 WebSocket 收发，无需平台签名。"""

    channel = "web_sim"

    async def parse_inbound(self, payload: dict) -> UnifiedMessage:
        raw_type = payload.get("msg_type") or payload.get("type") or "text"
        try:
            msg_type = MessageType(raw_type)
        except ValueError:
            msg_type = MessageType.TEXT

        return UnifiedMessage(
            channel=self.channel,
            external_user_id=str(payload.get("external_user_id") or payload.get("from") or ""),
            tenant_id=payload.get("tenant_id"),
            bind_lawyer_id=payload.get("bind_lawyer_id"),
            msg_type=msg_type,
            text=payload.get("text"),
            media_url=payload.get("media_url"),
            card_payload=payload.get("card_payload"),
            raw=payload,
        )

    async def send_outbound(self, msg: OutboundMessage) -> None:
        """网页模拟 IM 的出站由 WebSocket 处理器负责推送，此处仅记录。"""
        logger.debug("WebSim 出站 -> {}: {}", msg.to, (msg.text or "")[:60])

    async def resolve_tenant(self, external_user_id: str) -> Optional[str]:
        return None

    async def resolve_bind_lawyer(self, external_user_id: str) -> Optional[int]:
        return None
