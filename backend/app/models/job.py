"""异步任务模型。

借鉴 zshortmovies 的 Job/JobStatus 与「先落库再执行」模式，并修正其缺陷：
- 新增 `step_state` 回写每步中间产物，支持断点续跑（原实现步骤间只传内存变量）
- 新增 `retry_count` + `max_retries` 上限与指数退避（原实现 retry 无上限无退避）
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import JobStatus, JobType


class Job(Base, TenantMixin, TimestampMixin):
    """异步任务：案件分析 / 证据解析 / 合规扫描 / 文书生成。

    关键约定：在任何重计算**之前**先落库建行，
    即便进程立刻崩溃，用户输入也不会丢失。
    """

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, native_enum=False, length=32), default=JobType.CASE_ANALYSIS
    )
    # 关联业务对象：case_id / evidence_id / scan_id / document_id
    ref_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.PENDING
    )
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0-100
    step_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # 每步中间产物（可断点续跑的关键）：{"retrieve": {...}, "generate": {...}}
    step_state: Mapped[dict] = mapped_column(json_col(), default=dict)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # 任务输入（脱敏后保存，便于重试与审计）
    input_payload: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    # 任务输出引用（真正的产物落在各自业务表，此处存 ID 摘要）
    output_payload: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 心跳：RUNNING 期间由 worker 定期刷新，用于回收「进程崩溃后卡死」的僵尸任务
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # 执行认领者标识（host:pid），便于多副本排障定位是哪个实例在跑
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    def mark_running(self, step_name: str, claimed_by: Optional[str] = None) -> None:
        self.status = JobStatus.RUNNING
        self.step_name = step_name
        now = datetime.now(timezone.utc)
        if self.started_at is None:
            self.started_at = now
        self.heartbeat_at = now
        if claimed_by:
            self.claimed_by = claimed_by

    def touch_heartbeat(self) -> None:
        """刷新心跳。长任务应在每个步骤边界调用。"""
        self.heartbeat_at = datetime.now(timezone.utc)

    def mark_completed(self, output: Optional[dict] = None) -> None:
        self.status = JobStatus.COMPLETED
        self.progress = 100
        self.finished_at = datetime.now(timezone.utc)
        if output is not None:
            self.output_payload = output

    def mark_failed(self, message: str) -> None:
        self.status = JobStatus.FAILED
        self.error_message = message
        self.finished_at = datetime.now(timezone.utc)
