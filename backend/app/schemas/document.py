"""文书与合同 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    lifecycle: str
    description: Optional[str] = None
    variables: Optional[list] = None
    is_high_risk: bool = False
    reviewed_by_lawyer: bool = False
    usage_count: int = 0


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    template_id: Optional[int] = None
    case_id: Optional[int] = None
    title: str
    content: Optional[str] = None
    status: str
    variables: Optional[dict] = None
    missing_variables: Optional[list] = None
    risk_findings: Optional[list] = None
    overall_risk: str
    review_status: str
    required_level: str


class ContractReviewRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=100)
    # 2026-09-22 补长度上限（门禁 P1 项）：同步端点 **20k**。
    # 理由：`_build_prompt` 把原文**全量入 prompt 且不截断**（`contract_review.py:809`），
    # 5 万字一次 ≈ 14 分 ⇒ 这是全库**唯一无截断的成本放大口**（入库侧反而只留 `[:5000]`）。
    # ⚠️ 超限 **413 + 提示转上传/异步**（异步通道 500k，见 `ComplianceScanCreate` 之外的上传链路），
    # **绝不静默截断** —— 截断 = 审查结论基于残缺合同，是最不能接受的失败模式。
    source_text: str = Field(..., max_length=20000)
    evidence_id: Optional[int] = None


class ContractReviewAsyncRequest(BaseModel):
    """异步合同审查（长合同走这条）。

    与同步端点的**唯一区别**是上限：同步 20k、异步 **500k**。
    分档理由见 `ContractReviewRequest.source_text` 的注释——同步端点把原文
    全量入 prompt，5 万字一次 ≈ 14 分；异步走 job，不占请求连接。

    ⚠️ 上限仍是**硬边界**、超限 413、**绝不静默截断**：截断 = 审查结论基于
    残缺合同，是最不能接受的失败模式（瑞思：合同原文是最不能忍截断的一类）。
    """

    title: Optional[str] = Field(None, max_length=100)
    source_text: str = Field(..., max_length=500000)
    evidence_id: Optional[int] = None


class ComplianceScanCreate(BaseModel):
    # ⚠️ 与 `compliance.py` 的同名类**必须同步**（门禁去重取先出现者，不同步会让扫描结果抖动）。
    title: str = Field(..., max_length=100)
    dimensions: Optional[list[str]] = None
    scope: Optional[str] = Field(None, max_length=500)
    input_summary: Optional[str] = Field(None, max_length=2000)
    is_external: bool = False
