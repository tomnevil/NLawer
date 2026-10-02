"""会话接口：IM 接待入口（创建会话 / 列表 / 详情 / 发送消息走会话引擎）。"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_tenant_context
from app.core.pagination import Page, PaginationParams, count_bounded, ok
from app.core.rbac import Role
from app.models.conversation import Conversation, Message
from app.models.enums import MessageSender
from app.schemas.conversation import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    EngineReply,
    MessageOut,
    SendMessageRequest,
)
from app.services.conversation_access import load_accessible_conversation
from app.services.conversation_engine import ConversationEngine

router = APIRouter(prefix="/conversations", tags=["会话接待"])


@router.post("", response_model=dict, summary="创建会话")
async def create_conversation(
    payload: ConversationCreate,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    # Q-U（2026-09-21 裁定）：归属字段只能由**所内人员**指定。
    # 客户传了 `client_user_id` / `bind_lawyer_id` 也按「自己 / 不绑定」处理，
    # 避免事后不可追溯的归属伪造（所内代建会话确实需要指定归属）。
    if ctx.role == Role.CLIENT:
        client_user_id = ctx.user_id
        bind_lawyer_id = None
    else:
        client_user_id = payload.client_user_id or ctx.user_id
        bind_lawyer_id = payload.bind_lawyer_id
    conv = Conversation(
        tenant_id=ctx.tenant_id,
        external_user_id=payload.external_user_id,
        channel=payload.channel,
        client_user_id=client_user_id,
        bind_lawyer_id=bind_lawyer_id,
    )
    db.add(conv)
    await db.flush()
    return ok(ConversationOut.model_validate(conv).model_dump())


@router.get("", response_model=dict, summary="会话列表")
async def list_conversations(
    params: PaginationParams = Depends(),
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    # 客户只看自己的会话；律师 / 管理员看本租户全部
    base = select(Conversation).where(Conversation.tenant_id == ctx.tenant_id)
    if ctx.role == Role.CLIENT:
        base = base.where(Conversation.client_user_id == ctx.user_id)

    # 有界计数：大律所场景下单租户会话可达数万条，无界 COUNT 必须走完
    # （实测 30 万行/200 租户时 cases/reviews 同形状比值 1.2x 无收益，
    # 但单租户占全表 1/7 时扩大到 54~72x）。小集合零损失、大集合高收益。
    total, is_lower_bound = await count_bounded(db, base)

    rows = list(
        (
            await db.execute(
                base.order_by(Conversation.id.desc()).offset(params.offset).limit(params.limit)
            )
        ).scalars().all()
    )
    page = Page.build(
        [ConversationOut.model_validate(r).model_dump() for r in rows],
        total,
        params,
        total_is_lower_bound=is_lower_bound,
    )
    return ok(page.model_dump())


@router.get("/{conversation_id}", response_model=dict, summary="会话详情（含消息）")
async def get_conversation(
    conversation_id: int,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    # 归属判据走唯一来源 `conversation_access`。修复前这里只校验 `tenant_id`，
    # 导致「列表把会话藏起来、详情页却直接放行」——知道 id 即可绕过列表。
    conv = await load_accessible_conversation(
        db,
        conversation_id,
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        role=ctx.role,
    )
    msgs = list(
        (await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
        )).scalars().all()
    )
    detail = ConversationDetail(
        **ConversationOut.model_validate(conv).model_dump(),
        messages=[MessageOut.model_validate(m).model_dump() for m in msgs],
    )
    return ok(detail.model_dump())


@router.post("/{conversation_id}/messages", response_model=dict, summary="发送消息（触发会话引擎）")
async def send_message(
    conversation_id: int,
    payload: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    ctx=Depends(get_tenant_context),
):
    # 归属判据走唯一来源（修复前仅校验 tenant_id，同租户任意用户可往他人会话发言）
    conv = await load_accessible_conversation(
        db,
        conversation_id,
        user_id=ctx.user_id,
        tenant_id=ctx.tenant_id,
        role=ctx.role,
    )

    # `sender` 按已校验身份推导。修复前这里（以及 WS 侧）硬编码为 CLIENT，
    # 律师通过接口发言会被记成客户——污染客户消息流，且事后无法从数据上
    # 区分「这句话是谁说的」。
    is_client = conv.client_user_id is not None and conv.client_user_id == ctx.user_id
    sender = MessageSender.CLIENT if is_client else MessageSender.LAWYER

    db.add(
        Message(
            tenant_id=conv.tenant_id,
            conversation_id=conversation_id,
            sender=sender,
            msg_type=payload.msg_type,
            content=payload.text,
            media_url=payload.media_url,
            card_payload=payload.card_payload,
        )
    )
    await db.flush()

    # 只有端用户（客户）的消息才驱动 AI 接待/转人工；所内人员发言是对客户的
    # 回复，不应再触发 AI 应答（否则会出现「律师说话 → AI 抢答」）。
    if sender != MessageSender.CLIENT:
        await db.commit()
        # `reply` 为空是刻意的：该字段语义是「AI 的回复」。所内人员发言没有
        # AI 回复，返回空串让前端不渲染 AI 气泡——若塞一句占位文案，
        # 前端会把它当成 AI 说的话显示出来。
        return ok(EngineReply(reply="", status=conv.status.value).model_dump())

    engine = ConversationEngine(db)
    result = await engine.process(conversation_id, payload.text or "", card=payload.card_payload)
    await db.commit()
    return ok(EngineReply(**result).model_dump())
