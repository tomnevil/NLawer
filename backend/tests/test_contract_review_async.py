"""异步合同审查通道的判据（2026-09-23 I1 增补）。

## 为什么必须补这条通道

`source_text` 是**唯一无截断的成本放大口**（`_build_prompt` 全量入 prompt，
5 万字 ≈ 14 分/次）。同步端点已限 **20k**，超限返 **413**
（`guidance=upload_or_async`）—— 但在本文件之前，`JobType` 里**根本没有
`contract_review`**，413 指引用户走的「异步」是**一条不存在的路**。
这条通道是 B1 决策门选项①（一键存 .txt 上传 → 转异步）的落地前提。

## 覆盖清单

| 编号 | 性质 | 反向向量 |
|------|------|----------|
| A1 | `JobType.CONTRACT_REVIEW` 存在且值为 `contract_review` | — |
| A2 | **worker 已注册**（`JOB_HANDLERS` 能取到）—— 函数级单测测不出「有人删了 `register_job_handler`」 | 删注册 ⇒ A2 红 |
| A3 | **路由已挂到 app** —— 同样测不出「有人删了 `@router.post`」 | 删路由 ⇒ A3 红 |
| A4 | 异步上限 500k：500000 收、500001 拒；同步仍 20k | 双向边界 |
| A5 | worker 正常跑：写回 `step_state.contract_review_id`、进度到 100 | — |
| A6 | **断点续跑**：`step_state` 已有 id ⇒ 短路、不再调服务 | 把短路删掉 ⇒ A6 红 |
| A7 | 缺 `source_text` ⇒ `ValueError`（不是静默通过） | — |
| A8 | **同步/异步共用输入审核** `_moderate_contract_input` —— 异步漏审 = 加长文本即可绕过内容安全 | 任一端改回内联 ⇒ A8 红 |
| A9 | **跑完能取回结论**：`GET /contract-review/{review_id}` 已挂到 app | 删路由 ⇒ A9 红 |
| A10 | **闭环最后一米**：任务响应暴露 `step_state`（否则拿到 completed 也取不到产物 id） | 删字段 ⇒ A10 红 |

⚠️ A5/A6 用 **stub 替掉 `ContractReviewService`**，不碰真实 DB：
本文件钉的是「通道接线与 worker 语义」，审查算法本身由
`test_contract_review_llm.py` 覆盖。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.enums import JobType
from app.schemas.document import ContractReviewAsyncRequest, ContractReviewRequest

# ═══════════════════════ 辅助 ═══════════════════════


class _FakeJob:
    """最小 Job 替身：只实现 worker 真正读写的字段。"""

    def __init__(self, payload: dict, step_state: dict | None = None) -> None:
        self.input_payload = payload
        self.step_state = step_state if step_state is not None else {}
        self.tenant_id = "tenant-a"
        self.progress = 0
        self.step_name = None


def _payload(text: str = "甲方应在收到货物后 30 日内支付全部价款。") -> dict:
    return {"title": "采购合同", "source_text": text, "user_id": 1, "evidence_id": None}


# ═══════════════════════ A1–A3 接线 ═══════════════════════


def test_job_type_has_contract_review():
    """A1：枚举项存在，且落库值稳定为 `contract_review`（改值 = 破坏历史数据）。"""
    assert JobType.CONTRACT_REVIEW.value == "contract_review"


def test_worker_is_registered():
    """A2：worker 必须真的注册进 `JOB_HANDLERS`。

    只测「函数存在」是不够的：删掉 `register_job_handler` 那一行，函数照样
    在、单测照样绿，但线上 `/jobs/{id}/retry` 会直接报「未注册重跑处理器」。
    """
    from app.services import job_handlers
    from app.services.job_service import JOB_HANDLERS

    job_handlers.register_all()
    assert JOB_HANDLERS.get(JobType.CONTRACT_REVIEW.value) is job_handlers._contract_review_worker


def test_async_route_is_registered_on_app():
    """A3：异步端点必须真的挂在 app 上（删路由 ⇒ 前端 404，单测不红）。"""
    from app.main import create_app

    app = create_app()
    # ⚠️ 必须用 `app.openapi()["paths"]`，**不能用 `app.routes`**：
    # 本版本的 `app.routes` 只列应用自身的路由（实测 10 条，全是 /api/health 与文档），
    # `include_router` 进来的 84 条**不在其中** —— 用 app.routes 判「路由是否注册」
    # 会得到**假阴性**（路由明明在，却判未注册）。
    paths = app.openapi()["paths"]
    assert "/api/v1/documents/contract-review/async" in paths, "异步合同审查路由未注册"
    assert "post" in paths["/api/v1/documents/contract-review/async"]


# ═══════════════════════ A4 上限边界 ═══════════════════════


def test_async_limit_is_500k_and_sync_stays_20k():
    """A4：异步 500k、同步 20k；**双向**都验（收的那一侧也要钉，防把功能焊死）。"""
    ok_async = ContractReviewAsyncRequest(title="t", source_text="合" * 500_000)
    assert len(ok_async.source_text) == 500_000

    with pytest.raises(ValidationError):
        ContractReviewAsyncRequest(title="t", source_text="合" * 500_001)

    ok_sync = ContractReviewRequest(title="t", source_text="合" * 20_000)
    assert len(ok_sync.source_text) == 20_000

    with pytest.raises(ValidationError):
        ContractReviewRequest(title="t", source_text="合" * 20_001)


# ═══════════════════════ A5–A7 worker 语义 ═══════════════════════


@pytest.mark.asyncio
async def test_worker_runs_review_and_records_id(monkeypatch):
    """A5：正常跑完要写回 `contract_review_id` 并把进度推到 100。"""
    from app.services import job_handlers

    calls = []

    class _StubService:
        def __init__(self, db) -> None:
            pass

        async def review(self, **kw):
            calls.append(kw)
            return SimpleNamespace(id=42)

    monkeypatch.setattr(job_handlers, "ContractReviewService", _StubService)

    job = _FakeJob(_payload("甲方与乙方约定如下。"))
    await job_handlers._contract_review_worker(job, db=None)

    assert job.step_name == "review"
    assert job.progress == 100
    assert job.step_state["contract_review_id"] == 42
    # 归属必须跟着 job 走，而不是调用方传什么就是什么
    assert calls[0]["tenant_id"] == "tenant-a"
    assert calls[0]["source_text"] == "甲方与乙方约定如下。"


@pytest.mark.asyncio
async def test_worker_short_circuits_when_already_reviewed(monkeypatch):
    """A6：已审查过 ⇒ 短路、不再调服务（重跑会重复烧 token）。

    反向向量：若把这段短路删掉，本例会红（服务被调用）。
    """
    from app.services import job_handlers

    def _must_not_be_called(*a, **kw):  # pragma: no cover - 触发即失败
        raise AssertionError("已审查过的任务不应再次调用审查服务")

    monkeypatch.setattr(job_handlers, "ContractReviewService", _must_not_be_called)

    job = _FakeJob(_payload(), step_state={"contract_review_id": 7})
    await job_handlers._contract_review_worker(job, db=None)

    assert job.progress == 100
    assert job.step_state["contract_review_id"] == 7


@pytest.mark.asyncio
async def test_worker_rejects_missing_source_text():
    """A7：缺原文必须显式报错，不能静默产出「无风险」的空审查。"""
    from app.services import job_handlers

    job = _FakeJob({"title": "t", "user_id": 1})
    with pytest.raises(ValueError):
        await job_handlers._contract_review_worker(job, db=None)


# ═══════════════════════ A8 审核不漂移 ═══════════════════════


def test_result_readback_route_is_registered():
    """A9：**异步跑完必须能取回结论**，否则「转异步」仍是死路。

    `POST /contract-review/async` 只回 `job_id`，审查实体是 worker 内部建的。
    没有 `GET /contract-review/{review_id}`，任务完成了用户也拿不到结论——
    413 的 `guidance=upload_or_async` 就还是指向一个不存在的能力。
    """
    from app.main import create_app

    app = create_app()
    paths = app.openapi()["paths"]
    key = "/api/v1/documents/contract-review/{review_id}"
    assert key in paths, "缺少「按 id 取回审查结论」的读接口"
    assert "get" in paths[key]


def test_both_review_endpoints_share_moderation():
    """A8：同步/异步都走同一个 `_moderate_contract_input`。

    异步若漏审，等于开一条后门：把文本加长到 >20k 走异步即可绕过同步端的
    内容安全拦截。判据必须钉住「两处都调用」，而不只是「helper 存在」。
    """
    from app.api.v1 import documents

    for fn_name in ("review_contract", "review_contract_async"):
        src = inspect.getsource(getattr(documents, fn_name))
        assert "_moderate_contract_input" in src, f"{fn_name} 未走共用输入审核"


# ═══════════════════════ A10 闭环最后一米 ═══════════════════════


class _FakeJobRow:
    """最小 Job 行替身：只覆盖 `JobSchema` 的必填字段。"""

    id = 2
    job_type = "contract_review"
    ref_type = "contract_review"
    ref_id = None
    status = "completed"
    progress = 100
    step_name = "review"
    error_message = None
    retry_count = 0
    input_payload = {}
    output_payload = None
    step_state = {"contract_review_id": 42}


def test_job_schema_exposes_step_state():
    """A10：任务响应**必须带** `step_state` —— 否则异步链路在最后一米断裂。

    真链路冒烟（2026-09-23）实测发现的反直觉缺口：worker 确实把
    `{"contract_review_id": 2}` 写进了 DB，任务也到了 completed，
    但 `JobSchema` 未暴露该字段 ⇒ 客户端轮询拿到终态却**取不到产物 id**，
    413 的 `guidance=upload_or_async` 引导用户绕了一圈仍然拿不到结论。

    ⚠️ 同一缺口对既有通道同样成立（`case_analysis` 写 `analysis_id`、
    `evidence_parse` 写 `parsed`），只是此前没有前端消费方才一直潜伏。
    """
    from app.schemas.job import JobSchema

    assert "step_state" in JobSchema.model_fields, (
        "任务响应未暴露 step_state：worker 写了产物 id 也传不回客户端"
    )

    dumped = JobSchema.model_validate(_FakeJobRow()).model_dump()
    assert dumped["step_state"] == {"contract_review_id": 42}, (
        f"step_state 未随响应序列化，实际={dumped.get('step_state')!r}"
    )
