"""WebSocket 端点：IM 实时收发 + 通知实时推送。

## 两个端点，两种语义

| 端点 | 方向 | 用途 |
|------|------|------|
| `/ws/conversations/{id}` | 客户端发起 → 服务端回 | 模拟 IM 收发（客户 <-> AI / 律师） |
| `/ws/notifications` | **服务端主动推** | 通知实时送达（P0-15 收口项） |

修复前**只有前者**，而它是「逐会话回显循环」——能回复发消息的那个人，
但**没有任何连接注册表**，因此**无法主动推送**。这直接卡住门槛一的出口标准
（真实企微中跑通「咨询→派单→接单→AI 摘要→律师确认」）：该闭环的消息触达
依赖通知推送，而**即使企微适配器接通，通知也送不出去**。

## 鉴权：`user_id` 只取自 JWT

通知端点的推送键**只来自 JWT 派生的 `user.id`**，端点**不接受任何
「订阅某个 user_id / topic」的入参**。这是从 **API 形状**上杜绝越权订阅：
只要不存在这个参数，就不存在「前端伪造订阅他人」的攻击面，
而不是依赖运行时校验去堵。

## 连接期间 token 过期

连接建立时校验一次；**连接存续期间不强制断开**。理由：长连接内续期需要
额外的令牌轮换协议，复杂度与收益不成比例；安全窗口以 `access_token` 的
TTL 为界，前端在**重连时**用最新 token 重建连接。
（该取舍已列入 PRD §8 的 Q7。）
"""
import asyncio
import json
import time

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy import select

from app.core.errors import NotFoundError
from app.core.metrics import metrics
from app.core.security import decode_token_or_none
from app.database import async_session_factory
from app.models.conversation import Message
from app.models.enums import MessageSender, MessageType
from app.models.identity import User
from app.services import notification_service as notif_svc
from app.services.connection_manager import manager
from app.services.conversation_access import (
    load_accessible_conversation,
)
from app.services.conversation_engine import ConversationEngine

router = APIRouter(prefix="/ws", tags=["实时通讯"])

#: 指标用的端点标签。连接注册表目前只服务通知通道；
#: 若将来新增通道，应为其建独立注册表实例并换用各自的标签值。
ENDPOINT_NOTIFICATIONS = "notifications"

# ---------------- 心跳与超时参数（模块级，便于测试覆盖） ----------------

#: 服务端主动心跳间隔。移动网络下的中间设备（NAT / 代理）常在 60s 左右
#: 回收空闲连接，30s 心跳留出一倍余量。
HEARTBEAT_INTERVAL: float = 30.0
#: 空闲超时：连续该时长未收到任何客户端帧即判定为「假活」并回收。
#: 取 3 倍心跳——单次心跳丢失（移动网络抖动）不应直接断连。
IDLE_TIMEOUT: float = 90.0

# ---------------- 关闭码 ----------------

#: 1008 = policy violation（未授权）
CLOSE_UNAUTHORIZED = 1008
#: 1001 = going away（服务端主动下线 / 空闲回收）。客户端据此**主动重连**，
#: 而不是当作错误反复重试。
CLOSE_GOING_AWAY = 1001
#: 4404 = 应用层「不存在」（对齐第一轮 REST 的 404 语义，不可枚举）
CLOSE_NOT_FOUND = 4404
#: 4429 = 单用户连接数超限（Too Many Requests 语义，客户端应提示而非重试）
CLOSE_TOO_MANY = 4429

# ---------------- 帧类型 ----------------

FRAME_CONNECTED = "connected"
FRAME_PING = "ping"
FRAME_PONG = "pong"
FRAME_ERROR = "error"


async def _auth_user(token: str | None) -> tuple[User | None, str]:
    """校验 WS 令牌，返回 `(user, reason)`。

    `user is None` 时 `reason` 说明失败原因，供 `ws_auth_failed_total{reason}`
    区分。区分原因的意义：`missing_token` 通常是前端 bug（漏带 token），
    `invalid_token` 是过期/伪造，`user_not_found` 是账号被删——
    三者的排查方向完全不同，合并成一个计数等于放弃定位能力。

    `sub` 的 `int()` 转换包在 try 里：令牌载荷可被伪造，`sub` 未必是数字。
    裸转换会让一个畸形令牌在 WS 端点里抛 `ValueError`（500 / 连接直接断），
    而正确行为是「鉴权失败」——攻击者不该能用一个字符串让服务端报错。
    """
    if not token:
        return None, "missing_token"
    payload = decode_token_or_none(token)
    if payload is None:
        return None, "invalid_token"
    sub = payload.get("sub")
    if not sub:
        return None, "invalid_token"
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None, "invalid_token"
    async with async_session_factory() as db:
        user = (
            (await db.execute(select(User).where(User.id == user_id))).scalars().first()
        )
    if user is None:
        return None, "user_not_found"
    return user, ""


# ═══════════════════════════════════════════════════════════════════════════
# 通知实时推送
# ═══════════════════════════════════════════════════════════════════════════


@router.websocket("/notifications")
async def notifications_ws(websocket: WebSocket, token: str | None = Query(None)):
    """通知推送通道。

    建立流程：鉴权 → 登记到 `ConnectionManager` → 下发一次未读快照
    （前端据此立即校正角标，不必再多发一次 HTTP）→ 心跳保活。

    推送内容由 `notification_push` 在**业务事务提交后**投递
    （见该模块 docstring：推送早于提交会产生「幽灵通知」）。
    """
    await websocket.accept()

    user, reason = await _auth_user(token)
    if user is None:
        metrics.ws_auth_failed_total.inc((reason,))
        await websocket.send_json({"type": FRAME_ERROR, "message": "未授权或令牌无效"})
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    # 登记失败 = 该用户连接数已达上限。**必须关闭**，不能继续挂着：
    # 一个「连上了但永远收不到推送」的半死连接比明确拒绝更难排查。
    if not await manager.register(user.id, websocket):
        metrics.ws_connection_rejected_total.inc(("over_limit",))
        await websocket.send_json(
            {"type": FRAME_ERROR, "message": "连接数已达上限，请关闭其他页面后重试"}
        )
        await websocket.close(code=CLOSE_TOO_MANY)
        logger.warning(
            "通知连接被拒（超单用户上限 {}）：user_id={}", manager.max_per_user, user.id
        )
        return

    metrics.ws_connections_total.inc((ENDPOINT_NOTIFICATIONS,))
    metrics.ws_connections_active.set(
        manager.total_connections(), (ENDPOINT_NOTIFICATIONS,)
    )
    logger.info(
        "通知连接建立：user_id={} 该用户连接数={} 在线用户={}",
        user.id, manager.count(user.id), manager.online_users(),
    )

    try:
        # 连接即下发未读快照：前端切到实时通道后可立即丢掉一次 HTTP 往返，
        # 且在重连场景下能马上把角标纠正到服务端真值（避免乐观增量漂移）。
        async with async_session_factory() as db:
            unread = await notif_svc.unread_count(
                db, tenant_id=user.tenant_id, user_id=user.id
            )
        await websocket.send_json(
            {
                "type": FRAME_CONNECTED,
                "data": {
                    "user_id": user.id,
                    "unread_total": unread["total"],
                    "latest_id": unread["latest_id"],
                },
            }
        )

        last_seen = time.monotonic()
        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                silent = time.monotonic() - last_seen
                if silent >= IDLE_TIMEOUT:
                    # 「假活」连接：TCP 未收到 FIN（拔网线 / 进电梯 / 被代理静默丢弃），
                    # 仅靠 receive 永远不会返回，只能靠空闲超时回收。
                    metrics.ws_heartbeat_timeout_total.inc()
                    logger.info(
                        "通知连接空闲超时回收：user_id={} 静默 {:.0f}s",
                        user.id, silent,
                    )
                    await websocket.close(code=CLOSE_GOING_AWAY)
                    break
                await websocket.send_json({"type": FRAME_PING})
                continue

            last_seen = time.monotonic()
            # 客户端帧一律视为保活信号。`pong` 是约定应答；其余帧（含非法 JSON）
            # 不解析、不报错——该通道**只推不收**，接收侧的唯一职责就是保活。
            if raw == FRAME_PONG:
                continue
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == FRAME_PONG:
                continue

    except WebSocketDisconnect:
        logger.debug("通知连接断开：user_id={}", user.id)
    finally:
        # `finally` 是唯一可靠的位置：正常关闭 / 异常 / 取消都会经过，
        # 漏掉它会让注册表里的死连接永久驻留（内存泄漏 + 推送不断失败）。
        await manager.unregister(user.id, websocket)
        # 指标同步收敛：`active` 若与真实连接数脱钩，会表现为「面板显示有连接
        # 但推送全是 no_connection」，误导排查方向。
        metrics.ws_connections_active.set(
            manager.total_connections(), (ENDPOINT_NOTIFICATIONS,)
        )
        logger.debug(
            "通知连接已注销：user_id={} 剩余连接数={}", user.id, manager.count(user.id)
        )


# ═══════════════════════════════════════════════════════════════════════════
# IM 会话收发
# ═══════════════════════════════════════════════════════════════════════════


@router.websocket("/conversations/{conversation_id}")
async def conversation_ws(
    websocket: WebSocket,
    conversation_id: int,
    token: str | None = Query(None),
):
    """会话内实时收发。

    ## 归属校验（本轮修复）

    修复前判据是 `conv.tenant_id != user.tenant_id and conv.client_user_id != user.id`
    ——放行条件是「同租户 **或** 是本会话客户」，即**同租户内任意用户可进入
    任意会话**。叠加「自助注册客户全部落在默认租户」后，等于任意注册客户
    可读写任意其他客户的会话。现统一走 `conversation_access` 的唯一判据。

    ## `sender` 不再硬编码

    修复前写死 `sender=MessageSender.CLIENT`，**律师通过该端点发消息会被
    记成客户**——污染客户消息流，且事后无法从数据上区分谁说的。
    现按已校验的身份推导：会话客户本人 → `CLIENT`，其余（所内人员）→ `LAWYER`。
    """
    await websocket.accept()
    user, reason = await _auth_user(token)
    if user is None:
        metrics.ws_auth_failed_total.inc((reason,))
        await websocket.send_json({"type": FRAME_ERROR, "message": "未授权或令牌无效"})
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = {"text": raw}

            text = payload.get("text") or ""
            if not text.strip():
                continue

            # ⚠️ 这条路径**没有 Pydantic schema**（`payload` 是 `json.loads` 出来的裸 dict）
            # ⇒ 必须在这里显式校验，`SendMessageRequest` 的收紧管不到它。
            #
            # 缺了这一段，一帧 `{"text":"x","msg_type":"BOGUS"}` 就能把越界值落库；
            # 而 `messages.msg_type` 是 `Enum(MessageType, native_enum=False)`
            # （纯 VARCHAR，DB 层无 CHECK）⇒ `select(Message)`（会话详情，
            # `conversations.py:92`）会在**结果物化阶段**抛 `LookupError`
            # ⇒ 该会话的详情接口**永久 500**。
            #
            # 与 `notifications.py` 的 `_parse_type` 同策略：**显式拒绝**，
            # 不做「未知类型静默降级」——静默降级会把客户端的拼写错误
            # 伪装成一条正常消息，事后无法从数据上察觉。
            raw_type = payload.get("msg_type")
            try:
                msg_type = MessageType(raw_type) if raw_type else MessageType.TEXT
            except ValueError:
                await websocket.send_json(
                    {"type": FRAME_ERROR, "message": f"未知消息类型：{raw_type}"}
                )
                continue

            async with async_session_factory() as db:
                # 单一来源判据；无权与不存在同为 404 语义（不可枚举）
                try:
                    conv = await load_accessible_conversation(
                        db,
                        conversation_id,
                        user_id=user.id,
                        tenant_id=user.tenant_id,
                        role=user.role,
                    )
                except NotFoundError:
                    logger.info(
                        "会话访问被拒：user_id={} role={} conversation_id={}",
                        user.id, user.role.value, conversation_id,
                    )
                    await websocket.send_json(
                        {"type": FRAME_ERROR, "message": "会话不存在"}
                    )
                    await websocket.close(code=CLOSE_NOT_FOUND)
                    return

                # 身份 → sender：客户本人是 CLIENT，其余放行者均为所内人员
                is_client = (
                    conv.client_user_id is not None and conv.client_user_id == user.id
                )
                sender = MessageSender.CLIENT if is_client else MessageSender.LAWYER

                db.add(
                    Message(
                        tenant_id=conv.tenant_id,
                        conversation_id=conversation_id,
                        sender=sender,
                        msg_type=msg_type,
                        content=text,
                        media_url=payload.get("media_url"),
                    )
                )
                await db.flush()

                # 端用户（非所内人员）的发消息走会话引擎（AI 接待 / 转人工）；
                # 所内人员发言是对客户的回复，不应再触发 AI 应答。
                if sender == MessageSender.CLIENT:
                    engine = ConversationEngine(db)
                    try:
                        result = await engine.process(conversation_id, text)
                        await db.commit()
                    except Exception as exc:  # noqa: BLE001  单条消息失败不中断连接
                        await db.rollback()
                        logger.warning("会话引擎处理失败: {}", exc)
                        await websocket.send_json(
                            {"type": FRAME_ERROR, "message": "处理失败，请稍后重试"}
                        )
                        continue
                else:
                    await db.commit()
                    result = {"status": conv.status.value}

                await websocket.send_json({"type": "message", **result})
    except WebSocketDisconnect:
        logger.debug("WebSocket 断开：会话 {}", conversation_id)
