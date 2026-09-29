"""IM 适配器注册表与分发。

渠道标识 -> 适配器实例的单例映射；会话引擎与 WebSocket 处理器经此取得
对应适配器，对上层屏蔽渠道差异。
"""
from app.im.feishu import FeishuAdapter
from app.im.protocol import IMAdapter, UnifiedMessage
from app.im.web_sim import WebSimAdapter
from app.im.wecom import WeComAdapter

_REGISTRY: dict[str, IMAdapter] = {
    WebSimAdapter.channel: WebSimAdapter(),
    WeComAdapter.channel: WeComAdapter(),
    FeishuAdapter.channel: FeishuAdapter(),
}


def register_adapter(adapter: IMAdapter) -> None:
    _REGISTRY[adapter.channel] = adapter


def get_adapter(channel: str) -> IMAdapter:
    """取得指定渠道适配器；未知渠道回退到网页模拟 IM（保证可演示）。"""
    return _REGISTRY.get(channel, _REGISTRY[WebSimAdapter.channel])


def list_channels() -> list[str]:
    return list(_REGISTRY.keys())


async def dispatch_inbound(payload: dict, channel: str = "web_sim") -> UnifiedMessage:
    """统一入站入口：解析平台回调为 UnifiedMessage。"""
    adapter = get_adapter(channel)
    msg = await adapter.parse_inbound(payload)
    if msg.tenant_id is None:
        resolved = await adapter.resolve_tenant(msg.external_user_id)
        msg = UnifiedMessage(
            channel=msg.channel,
            external_user_id=msg.external_user_id,
            tenant_id=resolved,
            bind_lawyer_id=msg.bind_lawyer_id,
            msg_type=msg.msg_type,
            text=msg.text,
            media_url=msg.media_url,
            card_payload=msg.card_payload,
            raw=msg.raw,
        )
    return msg
