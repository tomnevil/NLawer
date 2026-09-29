"""会话与消息 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IMChannel, MessageType


class ConversationCreate(BaseModel):
    external_user_id: str
    # ⚠️ 类型必须是枚举，不能是 `str`。
    #
    # 修复前这里是 `channel: str = IMChannel.WEB_SIM.value`，即**写入侧不校验**；
    # 而 `conversations.channel` 是 `Enum(IMChannel, native_enum=False)`（纯 VARCHAR，
    # DB 层无 CHECK 约束）⇒ 任意字符串都能落库。落库之后，
    # `select(Conversation)` 会在**结果物化阶段**抛
    # `LookupError: 'WEB' is not among the defined enum values`，
    # 于是 `GET /api/v1/conversations` 对**该租户永久 500**——一条脏值打死一个接口。
    #
    # 这里与别处（`DispatchRequest.mode` 等）不同：那些端点在**消费点**显式做了
    # `Enum(payload.x)` 并抛 400，所以 schema 用 `str` 只是「味道不好」；
    # 而 `channel` / `msg_type` 是**直接塞进 ORM 的**，schema 是唯一的校验关口。
    #
    # 用枚举后：Pydantic 接受枚举**值**（`"web_sim"`，与接口输出口径一致），
    # SQLAlchemy 落库时把它规范成枚举**名**（`WEB_SIM`），非法值直接 422。
    channel: IMChannel = IMChannel.WEB_SIM
    tenant_id: Optional[str] = None
    bind_lawyer_id: Optional[int] = None
    client_user_id: Optional[int] = None


class SendMessageRequest(BaseModel):
    text: Optional[str] = Field(None, max_length=4000)
    # 同 `ConversationCreate.channel`：直接进 ORM，schema 是唯一关口。
    # 前端发的是枚举**值**（`"text"`），与这里一致。
    msg_type: MessageType = MessageType.TEXT
    media_url: Optional[str] = None
    card_payload: Optional[dict] = None
    external_user_id: Optional[str] = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    sender: str
    msg_type: str
    content: Optional[str] = None
    media_url: Optional[str] = None
    card_payload: Optional[dict] = None
    citation_ids: Optional[list] = None


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    external_user_id: str
    channel: str
    status: str
    bind_lawyer_id: Optional[int] = None
    case_id: Optional[int] = None
    context: Optional[dict] = None
    last_message_at: Optional[str] = None


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []


class EngineReply(BaseModel):
    reply: str
    card: Optional[dict] = None
    status: str
    dispatch: Optional[dict] = None
