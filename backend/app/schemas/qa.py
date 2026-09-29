"""智能问答 DTO（四段式 + 引用溯源）。"""
from typing import Any, Optional

from pydantic import BaseModel


class CitationOut(BaseModel):
    id: int
    type: str
    title: str
    excerpt: Optional[str] = None
    effective_date: Optional[str] = None
    timeliness_warning: Optional[str] = None
    court: Optional[str] = None
    judgment_date: Optional[str] = None


class QAOut(BaseModel):
    sections: dict[str, Any]
    citations: list[CitationOut]
    disclaimer: str
    usage: Optional[dict] = None


class QAResponse(BaseModel):
    question: str
