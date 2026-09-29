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


class DispatchRequest(BaseModel):
    """触发派单：mode 为 designated / auto / pool。"""

    mode: str = DispatchMode.AUTO.value
    bind_lawyer_id: Optional[int] = None
    candidate_lawyer_ids: Optional[list[int]] = None
