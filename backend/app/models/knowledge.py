"""企业专属知识库与合规扫描模型（产品线 B）。"""
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import ComplianceDimension, RiskLevel, ScanStatus


class KnowledgeDoc(Base, TenantMixin, TimestampMixin):
    """企业专属知识库文档（PRD 5.7）。

    隔离硬要求：企业私有知识仅该企业可见（隔离率 100%），
    查询层强制按 tenant_id 过滤，读取前后双重校验归属。
    """

    __tablename__ = "knowledge_docs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300))
    # 内容类型：CONTRACT（合同库）/ POLICY（制度库）/ QA_HISTORY（历史咨询）
    #          / PREFERENCE（偏好设置）/ REGULATION（法规订阅）
    doc_type: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text)
    # 分块后的向量 / 关键词索引元信息
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    # 来源说明（合同编号、制度文号、咨询日期）
    source_ref: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    uploaded_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 是否经律师审核（审核后的合同模板才可引用）
    reviewed_by_lawyer: Mapped[bool] = mapped_column(Integer, default=0)


class ComplianceScan(Base, TenantMixin, TimestampMixin):
    """合规扫描任务（PRD 5.10 四维度）。"""

    __tablename__ = "compliance_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus, native_enum=False, length=32), default=ScanStatus.PENDING
    )
    # 本次扫描的维度列表
    dimensions: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 扫描目标描述（如：某电商公司 2026Q3 用工与宣传合规自查）
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 输入材料摘要（不落敏感全文）
    input_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    overall_risk: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=16), default=RiskLevel.NONE
    )
    # 分维度得分：{"LABOR": {"score": 72, "risk": "MEDIUM"}, ...}
    dimension_scores: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    report_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 对外版本需强制 L3 复核
    is_external: Mapped[bool] = mapped_column(Integer, default=0)
    review_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class ComplianceFinding(Base, TenantMixin, TimestampMixin):
    """合规扫描发现项：单个风险点与整改建议。"""

    __tablename__ = "compliance_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("compliance_scans.id"), index=True
    )
    dimension: Mapped[ComplianceDimension] = mapped_column(
        Enum(ComplianceDimension, native_enum=False, length=32)
    )
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=16), default=RiskLevel.LOW
    )
    # 整改建议
    suggestion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 参考模板 ID
    template_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 关联引用（法规依据）
    citation_ids: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
