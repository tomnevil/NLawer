"""计费与工单 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UsageType


class WorkOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_no: str
    tenant_id: str
    usage_type: str
    title: str
    status: str
    urgent: bool = False
    price_cents: int = 0
    escalate_to_lawyer: bool = False
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    billing_note: Optional[dict] = None


class UsageConsume(BaseModel):
    usage_type: str
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    urgent: bool = False


class RevenueProjection(BaseModel):
    subscription_cents: int = 0
    case_service_cents: int = 0
    work_order_cents: int = 0
    value_added_cents: int = 0


class QuotaAdjust(BaseModel):
    """管理员手动调整额度（Q-M）。

    `limit_count` 是**调整后的新上限**（绝对值，不是增量）——
    线下充值/补偿场景按最终额度下单更不容易出错，也便于审计复盘。

    ⚠️ `usage_type` 直接用 `UsageType` 枚举（而不是 `str`）：
    `tests/test_enum_input_validation.py` 的类级防线要求「映射到枚举列的请求体字段
    必须是枚举类型」，否则脏值会绕过写入校验直接落库。
    （`UsageConsume.usage_type` 是历史遗留的 `str`，在豁免清单里，新 schema 不沿用。）
    """

    usage_type: UsageType
    limit_count: int
    period: Optional[str] = None
    reason: Optional[str] = Field(None, max_length=1000)
