"""AI 运行与决策审计模型。

范式来源：AIAcquisition `services/agent_service.py` 的
`AgentRun` / `AgentDecision` / `AgentApproval` 三表设计 ——
「一次运行一行记录 + 每步决策审计 + 人工审批队列」。

本项目中：
- `AiRun`     = 一次 AI 流水线运行（案件分析 / 证据解析 / 合规扫描）
- `AiDecision` = 每一步的决策留痕（为何选此模型、检索到什么、是否命中强制复核）
- 人工审批队列由 `review.py` 的 `Review` 承担（对应 AgentApproval）
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import JobStatus


class AiRun(Base, TenantMixin, TimestampMixin):
    """一次 AI 流水线运行记录。"""

    __tablename__ = "ai_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    # 流水线类型：CASE_ANALYSIS / EVIDENCE_PARSE / COMPLIANCE_SCAN / DOCUMENT_GEN
    pipeline: Mapped[str] = mapped_column(String(64))
    # 关联业务对象
    ref_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.PENDING
    )
    # 触发来源：SYSTEM_AUTO（接单后自动）/ LAWYER_ITERATE（律师要求迭代）/ USER_MANUAL
    trigger: Mapped[str] = mapped_column(String(64), default="SYSTEM_AUTO")

    # 模型路由信息（埋点：任务类型、模型档位、token、耗时）
    model_tier: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_mock: Mapped[bool] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_cents: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 各阶段产出计数（对齐 AgentRun 的 discover/generate/execute 计数范式）
    stage_counts: Mapped[dict] = mapped_column(json_col(), default=dict)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def finish(self, status: JobStatus, error: Optional[str] = None) -> None:
        self.status = status
        self.finished_at = datetime.now(timezone.utc)
        if error:
            self.error_message = error


class AiDecision(Base, TenantMixin, TimestampMixin):
    """AI 每步决策审计：谁在何时基于什么做出什么决定。"""

    __tablename__ = "ai_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, index=True)
    # 阶段名：retrieve / generate / grade / forced_review_check / citation_validate
    stage: Mapped[str] = mapped_column(String(64))
    # 决策结果：PROCEED / ESCALATE / RETRY / ABORT
    decision: Mapped[str] = mapped_column(String(64))
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 决策依据快照（检索命中、规则命中、模型输出摘要），不落敏感原文
    payload: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
