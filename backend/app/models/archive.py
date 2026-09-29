"""归档与开庭材料包模型（PRD 5.6）。"""
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col


class Archive(Base, TenantMixin, TimestampMixin):
    """案件卷宗：定稿材料统一归档，按角色分级授权。"""

    __tablename__ = "archives"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cases.id"), unique=True, index=True
    )
    archive_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))

    # 卷宗结构化清单：诉状、证据、法律检索、时间线、沟通记录、审批记录
    # {"pleadings": [...], "evidence_ids": [...], "research": [...], "timeline": [...]}
    dossier: Mapped[dict] = mapped_column(json_col(), default=dict)

    # 版本控制：当前版本号，历史版本见 ArchiveVersion
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    archived_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    archived_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 保存期限（年），到期提醒
    retention_years: Mapped[int] = mapped_column(Integer, default=10)
    # 开庭材料包导出路径
    hearing_pack_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ArchiveVersion(Base, TenantMixin, TimestampMixin):
    """卷宗历史版本：支持回溯对比。"""

    __tablename__ = "archive_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archive_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(json_col(), default=dict)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class HearingPack(Base, TenantMixin, TimestampMixin):
    """开庭材料包：目录 + 起诉状/答辩状 + 证据清单 + 质证提纲 + 庭审要点。"""

    __tablename__ = "hearing_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(Integer, ForeignKey("cases.id"), index=True)
    archive_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    # 五个组成部分的内容
    sections: Mapped[dict] = mapped_column(json_col(), default=dict)
    # 生成的文件相对路径（docx / pdf）
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    generated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
