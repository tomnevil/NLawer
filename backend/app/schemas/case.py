"""案件与派单 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.enums import DispatchMode


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_no: str
    tenant_id: str
    title: str
    client_user_id: Optional[int] = None
    lawyer_id: Optional[int] = None
    conversation_id: Optional[int] = None
    status: str
    intent: Optional[str] = None
    grade: str
    dispute_type: Optional[str] = None
    party_a: Optional[str] = None
    party_b: Optional[str] = None
    focus: Optional[str] = None
    claim_amount: Optional[float] = None
    urgency: int = 0
    complexity: int = 0
    require_formal_opinion: bool = False
    summary: Optional[str] = None


class CaseListParams(BaseModel):
    status: Optional[str] = None
    grade: Optional[str] = None
    dispute_type: Optional[str] = None
    lawyer_id: Optional[int] = None
    keyword: Optional[str] = None


class DispatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    lawyer_id: Optional[int] = None
    mode: str
    status: str
    score: Optional[float] = None
    reason: Optional[str] = None


class CaseBriefOut(BaseModel):
    """抢单池行内联的**案件摘要**——一份**刻意收窄的投影**。

    ## 为什么不是 `CaseOut` 的子集缩写

    `GET /dispatches/pool` 把案件发给**尚未承办它**的律师。这些律师对案件的
    可见性受 `cases._case_or_404` 的 P2-10 规则约束（本人承办 / 有效团队授权，
    否则按「不存在」处理）。所以池摘要**不能**复用 `CaseOut`：
    它必须是一份**独立的、显式的白名单**，字段集恰为「决定要不要接这一单」
    所需的最小集。

    ## 白名单判据

    字段集**必须与前端 `apps/lawyer/app/(app)/dispatches/page.tsx` 实际渲染的
    字段一一对应**——多一个字段就是多一分无理由的暴露，少一个字段页面就少一块。
    该对应关系由 `tests/test_dispatch_pool_brief.py` 的 T2 守着（键集精确相等）。

    ## 刻意**不**给的字段（都是有内容、且对「要不要接」无决策价值的敏感项）

    | 字段 | 为什么不给 |
    |---|---|
    | `party_a` / `party_b` | 当事人姓名（个人信息） |
    | `summary` | 案情全文 |
    | `client_user_id` / `conversation_id` | 指向客户与会话的内部 id |
    | `tenant_id` / `lawyer_id` | 租户与承办人——池子里本来就**没有**承办人 |
    | `intent` / `complexity` / `urgency` / `require_formal_opinion` | 页面上没有，给了就是纯暴露 |

    ⚠️ 改这里的字段集，必须**同时**改前端渲染与 T2 的键集，否则判据会红。
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    case_no: str
    title: str
    grade: str
    status: str
    dispute_type: Optional[str] = None
    claim_amount: Optional[float] = None
    focus: Optional[str] = None


class PoolItemOut(DispatchOut):
    """`GET /dispatches/pool` 的行 = 派单字段 + **内联案件摘要**。

    🚨 摘要**必须嵌套**（`case_brief`），**不许摊平**：
    `DispatchOut.lawyer_id` 是「这条派单指向的律师」，`Case.lawyer_id` 是
    「案件的承办人」——**同名不同义**。摊平会让后者静默覆盖前者，
    于是「派单还没指定人」这个事实在响应里消失，前端无法区分。
    """

    case_brief: Optional[CaseBriefOut] = None


class DispatchRequest(BaseModel):
    """触发派单：mode 为 designated / auto / pool。"""

    mode: str = DispatchMode.AUTO.value
    bind_lawyer_id: Optional[int] = None
    candidate_lawyer_ids: Optional[list[int]] = None
