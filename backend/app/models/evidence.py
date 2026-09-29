"""证据材料模型（PRD 5.4 证据流水线）。"""
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import EvidenceCategory, EvidenceStatus


class Evidence(Base, TenantMixin, TimestampMixin):
    """证据材料：客户从 IM 上传，AI 自动解析、归类、抽取要素。"""

    __tablename__ = "evidences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("cases.id"), nullable=True, index=True
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    name: Mapped[str] = mapped_column(String(300))
    # 相对存储路径（LOCAL_STORAGE_PATH 下）
    file_path: Mapped[str] = mapped_column(String(500))
    file_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    status: Mapped[EvidenceStatus] = mapped_column(
        Enum(EvidenceStatus, native_enum=False, length=32),
        default=EvidenceStatus.UPLOADED,
    )
    # ---- 自动归类（PRD 五类）----
    category: Mapped[EvidenceCategory] = mapped_column(
        Enum(EvidenceCategory, native_enum=False, length=32),
        default=EvidenceCategory.OTHER,
    )
    # 归类置信度（0-1），低于阈值提示律师确认
    category_confidence: Mapped[Optional[float]] = mapped_column(nullable=True)

    # ---- 要素抽取：日期 / 金额 / 主体 / 签章 ----
    extracted: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    # OCR / 转写文本
    ocr_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 合法性风险提示（复印件、无原件、取证方式等）
    legality_risks: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 证据链时间线中的事件日期
    event_date: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    parse_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EvidenceChecklist(Base, TenantMixin, TimestampMixin):
    """材料清单模板：按纠纷类型定义必备材料，用于缺失提醒。"""

    __tablename__ = "evidence_checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # platform 租户保存通用模板，律所租户可自定义
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    dispute_type: Mapped[str] = mapped_column(String(100), index=True)
    item: Mapped[str] = mapped_column(String(300))
    category: Mapped[EvidenceCategory] = mapped_column(
        Enum(EvidenceCategory, native_enum=False, length=32),
        default=EvidenceCategory.OTHER,
    )
    # 关键程度：1=必备（先追问） 2=重要 3=补充
    priority: Mapped[int] = mapped_column(Integer, default=2)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
