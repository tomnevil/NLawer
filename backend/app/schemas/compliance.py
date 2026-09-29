"""合规扫描 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ComplianceScanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    dimensions: Optional[list] = None
    scope: Optional[str] = None
    input_summary: Optional[str] = None
    overall_risk: str
    dimension_scores: Optional[dict] = None
    report_summary: Optional[str] = None
    is_external: bool = False
    review_status: Optional[str] = None


class ComplianceFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scan_id: int
    dimension: str
    title: str
    description: Optional[str] = None
    risk_level: str
    suggestion: Optional[str] = None
    template_id: Optional[int] = None
    citation_ids: Optional[list] = None


class ComplianceScanCreate(BaseModel):
    # 2026-09-22 补长度上限（请求体自由文本门禁）：`input_summary` 对齐既有 `[:2000]` 切片。
    # ⚠️ **本类在 `document.py` 里还有一份同名定义**，两边必须同步改 ——
    # 否则门禁的去重会按文件顺序取先出现者，扫描结果**随文件排序抖动**。
    title: str = Field(..., max_length=100)
    dimensions: Optional[list[str]] = None
    scope: Optional[str] = Field(None, max_length=500)
    input_summary: Optional[str] = Field(None, max_length=2000)
    is_external: bool = False
