"""异步任务 Schema。"""
from typing import Optional

from app.schemas.common import ORMModel


class JobSchema(ORMModel):
    id: int
    job_type: str
    ref_type: Optional[str] = None
    ref_id: Optional[int] = None
    status: str
    progress: int
    step_name: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int
    input_payload: Optional[dict] = None
    output_payload: Optional[dict] = None
    # 2026-09-23 补：worker 约定把中间产物写回 step_state（case_analysis 写
    # analysis_id、contract_review 写 contract_review_id）。此前该字段**未暴露**，
    # 客户端拿到 completed 却取不到产物 id ⇒ 异步链路闭环断裂（真链路冒烟实测）。
    # 只含产物 id / 进度标记，不含正文；且与 input_payload 一样受租户过滤保护。
    step_state: Optional[dict] = None
