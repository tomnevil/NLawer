"""文书模板与文书模型（PRD 5.9 文书自动化 + 合同审查）。"""
from typing import Optional

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import (
    ContractAnalysisStatus,
    ContractReviewSource,
    ContractReviewStatus,
    DocumentStatus,
    ReviewLevel,
    ReviewStatus,
    RiskLevel,
)


class DocumentTemplate(Base, TenantMixin, TimestampMixin):
    """文书模板：200+ 常用法律文书，按生命周期分类。"""

    __tablename__ = "document_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # platform = 公共模板库；律所租户 = 自有模板
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(300))
    # 生命周期分类：劳动用工 / 合同交易 / 婚姻家庭 / 公司治理 / 诉讼文书
    lifecycle: Mapped[str] = mapped_column(String(64), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 模板正文，Jinja2 风格占位符：{{ party_a }} {{ amount }}
    body: Mapped[str] = mapped_column(Text)
    # 变量定义：[{key, label, required, type, placeholder, options}]
    variables: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 是否高风险文书（离婚协议 / 遗嘱 / 股权转让 / 认罪认罚 -> 强制 L3）
    is_high_risk: Mapped[bool] = mapped_column(Integer, default=0)
    # 律师审核状态
    reviewed_by_lawyer: Mapped[bool] = mapped_column(Integer, default=0)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class Document(Base, TenantMixin, TimestampMixin):
    """文书实例：由模板生成，支持在线编辑、风险提示与导出。"""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("document_templates.id"), nullable=True
    )
    case_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("cases.id"), nullable=True, index=True
    )
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    title: Mapped[str] = mapped_column(String(300))
    # 渲染后的正文
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False, length=32),
        default=DocumentStatus.DRAFT,
    )
    # 已收集变量：{"party_a": "张三", "amount": "50000"}
    variables: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    # 必填变量缺失项（收集完整率 100% 才可生成）
    missing_variables: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)

    # 风险提示：[{clause, risk_level, issue, suggestion}]
    risk_findings: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    overall_risk: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=16), default=RiskLevel.NONE
    )

    # 复核状态
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False, length=32), default=ReviewStatus.DRAFT
    )
    required_level: Mapped[ReviewLevel] = mapped_column(
        Enum(ReviewLevel, native_enum=False, length=8), default=ReviewLevel.L1
    )
    confirmed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 导出文件相对路径
    export_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class ContractReview(Base, TenantMixin, TimestampMixin):
    """合同审查记录：用户上传已有合同，AI 标注风险条款并给出修改建议。"""

    __tablename__ = "contract_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evidence_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    # 原文（OCR / 用户粘贴）
    source_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 风险条款（P0-16 契约）：
    # [{clause_index, clause_no, char_start, char_end, original, dimension,
    #   risk_level, consequence, suggestion_text, basis_type, citation,
    #   clause, issue, suggestion  # 旧键，向后兼容
    #  }]
    findings: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    overall_risk: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, native_enum=False, length=16), default=RiskLevel.NONE
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- 来源与审查深度（P0-16）----
    # 这三列是**计费诚实性的依据**：接口只在 source=llm && status=success 时扣费，
    # 因此它们必须落库，否则事后无法回答「这 99 元收在什么产出上」。
    source: Mapped[ContractReviewSource] = mapped_column(
        Enum(ContractReviewSource, native_enum=False, length=16),
        default=ContractReviewSource.RULE,
    )
    # 默认 `degraded` 而非 `success`：一行没有显式写入产出状态的记录，
    # 其真实语义是「未经验证的产出」。默认成 success 会让任何遗漏写入的
    # 路径**静默变成「模型审查成功」**，而 success 正是计费门控的判据之一。
    status: Mapped[ContractReviewStatus] = mapped_column(
        Enum(ContractReviewStatus, native_enum=False, length=16),
        default=ContractReviewStatus.DEGRADED,
    )
    analysis_status: Mapped[ContractAnalysisStatus] = mapped_column(
        Enum(ContractAnalysisStatus, native_enum=False, length=32),
        default=ContractAnalysisStatus.PRESCREEN_ONLY,
    )
    # {total_clauses, reviewed_clauses, reviewed_ratio}
    coverage: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    disclaimer: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    # 失败原因（**面向用户**的文案，不是原始异常）。
    # 原始异常（含环境变量名、base_url）只落 `AiRun.error_message`——那是运维视角；
    # 这一列是用户视角，前端会原样展示，因此必须是人话且不泄露基础设施细节。
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # ---- 留痕（对齐 AiRun 字段，便于不经 join 直接还原单次审查）----
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    model_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 单次审查的模型成本（分）。**必须非 NULL**：NULL 会让「这次审查花了多少钱」
    # 永久无法回答（现状 `ai_runs.cost_cents` 全为 NULL 即此坑）。
    cost_cents: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
