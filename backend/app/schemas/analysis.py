"""案件分析与证据 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CaseAnalysisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    run_id: Optional[int] = None
    version: int
    summary: Optional[str] = None
    legal_analysis: Optional[str] = None
    related_laws: Optional[list] = None
    similar_cases: Optional[list] = None
    suggestions: Optional[list] = None
    missing_info: Optional[list] = None
    status: str
    required_level: str
    forced_hits: Optional[list] = None
    ai_generated: bool = True
    iteration_notes: Optional[list] = None
    confirmed_by: Optional[int] = None


class AnalysisUpdate(BaseModel):
    """律师编辑六段式内容（驱动版本快照）。"""

    # 2026-09-22 补长度上限（请求体自由文本门禁，取值见
    # deliverables/product-strategy/roadmap-update-pending-rulings-2026-09-22.md §4.1）：
    # `legal_analysis` 是长推理，给最宽档；`summary` 对齐入库侧 `[:2000]`；`change_note` 是改稿说明。
    # ⚠️ 超长一律 **413 报错**，**不做静默截断**（截断会让结论基于残缺文本）。
    summary: Optional[str] = Field(None, max_length=2000)
    legal_analysis: Optional[str] = Field(None, max_length=20000)
    related_laws: Optional[list] = None
    similar_cases: Optional[list] = None
    suggestions: Optional[list] = None
    missing_info: Optional[list] = None
    change_note: Optional[str] = Field(None, max_length=500)


class IterateRequest(BaseModel):
    """律师批注驱动 AI 迭代。"""

    note: str = Field(..., max_length=2000)


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: Optional[int] = None
    name: str
    file_path: str
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    status: str
    category: str
    category_confidence: Optional[float] = None
    extracted: Optional[dict] = None
    ocr_text: Optional[str] = None
    legality_risks: Optional[list] = None
    event_date: Optional[str] = None
    parse_error: Optional[str] = None


class AiRunOut(BaseModel):
    # model_tier / model_name 与 pydantic 的 model_ 保护命名空间冲突，显式关闭该检查
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: int
    pipeline: str
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    status: str
    trigger: str
    model_tier: Optional[str] = None
    model_name: Optional[str] = None
    is_mock: bool = False
    duration_ms: Optional[int] = None
