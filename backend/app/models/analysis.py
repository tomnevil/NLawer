"""案件分析模型：PRD 5.3 六段式 AI 辅助办案成果。"""
from typing import Optional

from sqlalchemy import Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import ReviewLevel, ReviewStatus


class CaseAnalysis(Base, TenantMixin, TimestampMixin):
    """案件分析：接单后 AI 生成的六段式成果，律师可编辑并驱动迭代。"""

    __tablename__ = "case_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cases.id"), index=True
    )
    run_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)

    # ---- PRD 5.3 六段式 ----
    # 1. 案件摘要：当事人、相对方、纠纷类型、争议焦点、标的额、时间线
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 2. 法律分析：可能涉及的法律关系与请求权基础
    legal_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 3. 相关法条：[{law_name, article_no, excerpt, citation_id}]
    #    ⚠️ 键名是 `law_name` / `article_no`（**不是** `law` / `article`）。
    #    权威来源：`app/services/case_copilot.py::_build_sections`，
    #    消费方：`apps/lawyer` 的 `RelatedLaw` 与 `apps/admin` 复核详情。
    #    改键名必须同时改这两端 + `analyses.py:103` 的 `citation_id` 取值。
    related_laws: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 4. 类案参考：[{case_no, title, court, holding, citation_id}]
    similar_cases: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 5. 初步建议：[{path, pros, cons}]
    suggestions: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    # 6. 待补充信息：[{item, reason, priority}]，`priority` 是**数字** 1/2（不是 "high"）
    missing_info: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)

    # ---- 复核状态（PRD 5.5 状态机）----
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, native_enum=False, length=32), default=ReviewStatus.DRAFT
    )
    required_level: Mapped[ReviewLevel] = mapped_column(
        Enum(ReviewLevel, native_enum=False, length=8), default=ReviewLevel.L1
    )
    # 强制复核命中记录（PRD 5.5 五类场景）
    forced_hits: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)

    # 是否由 AI 生成（False = 律师完全手写），影响前端责任标识
    ai_generated: Mapped[bool] = mapped_column(Integer, default=1)
    # 律师迭代指令历史
    iteration_notes: Mapped[Optional[list]] = mapped_column(json_col(), nullable=True)
    confirmed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    confirmed_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class CaseAnalysisVersion(Base, TenantMixin, TimestampMixin):
    """分析历史版本：支持回溯对比与审计（PRD 5.6）。"""

    __tablename__ = "case_analysis_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(Integer, index=True)
    version: Mapped[int] = mapped_column(Integer)
    # 完整快照，便于 diff
    snapshot: Mapped[dict] = mapped_column(json_col(), default=dict)
    change_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    changed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
