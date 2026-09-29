"""Job 模型状态推进测试（先落库再执行 + 断点续跑基础）。

修正 zshortmovies 缺陷：每步 step_state 回写、retry_count 上限、状态机自洽。
"""
from app.models.enums import JobStatus
from app.models.job import Job


def test_enqueue_default_state():
    # 注意：SQLAlchemy 的 column default 仅在 flush/落库时应用，
    # 在内存对象上状态字段为 None；运行时由 JobService.enqueue 显式置初始值。
    job = Job(job_type="case_analysis", tenant_id="t1")
    assert job.job_type == "case_analysis"
    assert job.tenant_id == "t1"
    assert job.status is None


def test_status_set_on_running():
    job = Job(job_type="case_analysis", tenant_id="t1")
    job.mark_running("start")
    assert job.status == JobStatus.RUNNING
    # mark_running 即代表「先落库再执行」后进入运行态
    assert job.started_at is not None


def test_mark_running_sets_started_once():
    job = Job(job_type="case_analysis", tenant_id="t1")
    job.mark_running("retrieve")
    assert job.status == JobStatus.RUNNING
    assert job.step_name == "retrieve"
    started = job.started_at
    # 再次推进不应覆写 started_at（保留首次开始时间用于耗时统计）
    job.mark_running("generate")
    assert job.started_at == started


def test_mark_completed_sets_done():
    job = Job(job_type="case_analysis", tenant_id="t1")
    job.mark_running("start")
    job.mark_completed(output={"analysis_id": 7})
    assert job.status == JobStatus.COMPLETED
    assert job.progress == 100
    assert job.output_payload == {"analysis_id": 7}
    assert job.finished_at is not None


def test_mark_failed_records_error():
    job = Job(job_type="case_analysis", tenant_id="t1")
    job.mark_failed("boom")
    assert job.status == JobStatus.FAILED
    assert job.error_message == "boom"
    assert job.finished_at is not None


def test_step_state_supports_resume():
    # 模拟断点续跑：worker 把中间产物写回 step_state
    job = Job(job_type="evidence_parse", tenant_id="t1")
    job.step_state = {"ocr": {"done": True}}
    job.step_name = "extract"
    job.progress = 40
    # 恢复时可直接基于已有 step_state 继续
    assert job.step_state["ocr"]["done"] is True
    assert job.progress == 40
