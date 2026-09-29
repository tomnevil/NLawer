"""企业知识库 DTO（租户隔离）。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: str
    title: str
    doc_type: str
    content: Optional[str] = None
    source_ref: Optional[str] = None
    tags: Optional[str] = None
    chunk_count: int = 0
    reviewed_by_lawyer: bool = False


class KnowledgeDocCreate(BaseModel):
    # 2026-09-22 补长度上限（门禁）：`content` 给 50k（chunk 400 字 ⇒ 约 125 段，可分批导入）。
    # `source_ref` / `tags` **不设上限**：与正文差 3–4 个数量级，占潜在输入量 <0.1%（已裁定豁免）。
    title: str = Field(..., max_length=100)
    doc_type: str
    content: str = Field(..., max_length=50000)
    source_ref: Optional[str] = None
    tags: Optional[str] = None
