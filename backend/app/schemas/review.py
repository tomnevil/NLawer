"""复核与归档 DTO。"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_type: str
    target_id: int
    case_id: Optional[int] = None
    status: str
    required_level: str
    satisfied_level: Optional[str] = None
    is_forced: bool = False
    forced_hits: Optional[list] = None
    assignee_id: Optional[int] = None
    decided_by: Optional[int] = None
    decision: Optional[str] = None
    comment: Optional[str] = None


class ReviewAction(BaseModel):
    """复核动作：submit / decide / edit / archive / void。"""

    decision: Optional[str] = None  # APPROVED / REJECTED / REVISION_REQUESTED
    comment: Optional[str] = Field(None, max_length=1000)
    changes: Optional[dict] = None


class ReviewRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    review_id: int
    action: str
    actor_id: Optional[int] = None
    actor_role: Optional[str] = None
    level: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    changes: Optional[dict] = None
    comment: Optional[str] = None


class ArchiveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    archive_no: str
    title: str
    dossier: Optional[dict] = None
    current_version: int
    archived_by: Optional[int] = None
    archived_at: Optional[str] = None
    retention_years: int = 10
    hearing_pack_path: Optional[str] = None


class HearingPackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    title: str
    sections: Optional[dict] = None
    file_path: Optional[str] = None
    generated_by: Optional[int] = None
