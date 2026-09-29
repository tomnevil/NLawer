"""企业微信适配器骨架（待接入凭证）。

仅实现回调签名的占位与协议转换，不接真实企业微信 API，规避 PRD 风险清单中
「IM 平台接口变更 / 封禁」。上线前补全 `WECHAT_WORK_TOKEN/ASE_KEY` 等配置即可。
"""
from typing import Optional

from loguru import logger

from app.im.protocol import IMAdapter, MessageType, OutboundMessage, UnifiedMessage


class WeComAdapter(IMAdapter):
    channel = "wecom"

    async def parse_inbound(self, payload: dict) -> UnifiedMessage:
        """企业微信回调 -> UnifiedMessage（占位实现）。

        真实场景需解析 XML/JSON 加密报文，此处仅做最小映射。
        """
        logger.debug("WeCom 入站回调（骨架未接真实 API）")
        content = payload.get("text", {}).get("content") if isinstance(payload.get("text"), dict) else payload.get("text")
        return UnifiedMessage(
            channel=self.channel,
            external_user_id=str(payload.get("external_userid") or payload.get("FromUserName") or ""),
            tenant_id=payload.get("tenant_id"),
            msg_type=MessageType.TEXT,
            text=content,
            raw=payload,
        )

    async def send_outbound(self, msg: OutboundMessage) -> None:
        """真实场景调用企业微信「发送应用消息」接口；骨架仅记录。"""
        logger.debug("WeCom 出站（骨架未接真实 API）-> {}: {}", msg.to, (msg.text or "")[:60])

    async def resolve_tenant(self, external_user_id: str) -> Optional[str]:
        return None

    async def resolve_bind_lawyer(self, external_user_id: str) -> Optional[int]:
        return None
