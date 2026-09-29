"""飞书适配器骨架（待接入凭证）。

与 WeCom 同理，仅提供协议转换占位，不接真实飞书开放平台 API，规避封禁风险。
"""
from typing import Optional

from loguru import logger

from app.im.protocol import IMAdapter, MessageType, OutboundMessage, UnifiedMessage


class FeishuAdapter(IMAdapter):
    channel = "feishu"

    async def parse_inbound(self, payload: dict) -> UnifiedMessage:
        """飞书事件回调 -> UnifiedMessage（占位实现）。"""
        logger.debug("Feishu 入站回调（骨架未接真实 API）")
        event = payload.get("event", {}) if isinstance(payload.get("event"), dict) else {}
        msg = event.get("message", {}) if isinstance(event.get("message"), dict) else {}
        content = msg.get("content", {})
        text = content.get("text") if isinstance(content, dict) else None
        return UnifiedMessage(
            channel=self.channel,
            external_user_id=str(msg.get("sender", {}).get("sender_id", "") if isinstance(msg.get("sender"), dict) else payload.get("sender_id", "")),
            tenant_id=payload.get("tenant_id"),
            msg_type=MessageType.TEXT,
            text=text,
            raw=payload,
        )

    async def send_outbound(self, msg: OutboundMessage) -> None:
        """真实场景调用飞书「发送消息」接口；骨架仅记录。"""
        logger.debug("Feishu 出站（骨架未接真实 API）-> {}: {}", msg.to, (msg.text or "")[:60])

    async def resolve_tenant(self, external_user_id: str) -> Optional[str]:
        return None

    async def resolve_bind_lawyer(self, external_user_id: str) -> Optional[int]:
        return None
