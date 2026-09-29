"""合同审查「读回端点」与「413 门控最后一跳」的**端点层**判据（2026-09-23 I1 增补）。

## 为什么这个文件必须存在

真链路冒烟（2026-09-23）把整条异步链路跑通了，但**没有任何一条自动化判据真正
HTTP 请求过** `GET /api/v1/documents/contract-review/{review_id}`：

- `test_contract_review_async.py` 的 **A9** 只判「路由挂在 app 上」
  （`app.openapi()["paths"]` 里有这一条）——**测不出归属守卫有没有写**；
- **A10** 只判「`JobSchema` 有 `step_state` 字段」——**测不出 HTTP 响应真的带了回来**。

两者都是「零件层」。而这条读回接口是 **P0 敏感面**：按 id 直接取业务实体，
跨租户必须 **404**（403 会确认资源存在 ⇒ 可被枚举，与 P0 判据同一纪律）。
服务层测零件、端点层测装配——**装配层裸奔就是裸奔**。

## 覆盖清单

| 编号 | 性质 | 反向向量 |
|------|------|----------|
| R1 | 同租户 `GET` ⇒ 200，字段对得上 | — |
| R2 | 不存在 id ⇒ 404 `CONTRACT_REVIEW_NOT_FOUND` | — |
| **R3** | **跨租户 `GET` ⇒ 404，与 R2 同码**（不许按存在性区分租户归属，防枚举） | 守卫删掉 ⇒ 200 泄漏 |
| R4 | 非整数 id ⇒ 422（证明请求**真的进了端点**，不是被路由层挡回） | — |
| R5 | `error_message` **仅** `status=failed` 时出现（其它状态不得出现该键） | 去掉条件 ⇒ R5 红 |
| **R6** | 同步端点 25k ⇒ **413** `CONTENT_TOO_LARGE`，payload 带 `field`/`max_length`/`guidance` | 摘掉 413 转换 ⇒ 变 422 ⇒ R6 红 |
| **R7** | 异步端点 ⇒ 202 + `job_id`，且**真的入队**（替换队列，不让它真跑） | 忘记 `job_queue.enqueue` ⇒ R7 红 |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）：它本身有专门判据，
这里钉的是「端点有没有拿它做归属比较」。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"cr_read_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A / B 各一个律师；A 有「降级」与「失败」两条审查记录，B 有一条。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.document import ContractReview
    from app.models.enums import (
        ContractAnalysisStatus,
        ContractReviewSource,
        ContractReviewStatus,
        RiskLevel,
        UserStatus,
    )
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in (TENANT_A, TENANT_B):
                exists = (
                    await s.execute(select(Tenant).where(Tenant.tenant_id == tid))
                ).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid):
                return User(
                    username=f"crr-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.LAWYER,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            s.add_all([lawyer_a, lawyer_b])
            await s.flush()

            def _cr(tid, title, status, err=None):
                return ContractReview(
                    tenant_id=tid,
                    created_by=lawyer_a.id if tid == TENANT_A else lawyer_b.id,
                    title=title,
                    source_text="甲方应当在收货后三十日内支付全部价款。" * 5,
                    source=ContractReviewSource.RULE,
                    status=status,
                    analysis_status=ContractAnalysisStatus.PRESCREEN_ONLY,
                    overall_risk=RiskLevel.LOW,
                    summary="演示结论",
                    findings=[],
                    error_message=err,
                )

            cr_a = _cr(TENANT_A, "A 的采购合同", ContractReviewStatus.DEGRADED)
            cr_a_failed = _cr(
                TENANT_A, "A 的失败合同", ContractReviewStatus.FAILED, err="模型调用超时"
            )
            # 跨租户攻击目标：B 的审查记录
            cr_b = _cr(TENANT_B, "B 的采购合同", ContractReviewStatus.DEGRADED)
            s.add_all([cr_a, cr_a_failed, cr_b])
            await s.commit()

            return {
                "a": lawyer_a.id,
                "b": lawyer_b.id,
                "cr_a": cr_a.id,
                "cr_a_failed": cr_a_failed.id,
                "cr_b": cr_b.id,
            }

    return asyncio.run(_seed())


@pytest.fixture
def app_factory(engine, seeded):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。

    用 `create_app()` 而不是手工拼 router，是为了**连中间件与 413 转换一起带上**：
    R6 判的就是「`RequestValidationError` 被转换成 413」，只有真 app 才有这层。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    factory = async_sessionmaker(engine, expire_on_commit=False)

    def _make(user_id: int):
        async def _override_get_db():
            async with factory() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        async def _override_user():
            from app.models.identity import User

            async with factory() as s:
                return (
                    await s.execute(select(User).where(User.id == user_id))
                ).scalars().one()

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        return app

    return _make


def _data(resp):
    return resp.json().get("data") or {}


def _err(resp):
    return resp.json().get("error") or {}


# ═══════════════════════ R1–R5 读回端点 ═══════════════════════


def test_same_tenant_readback_returns_review(app_factory, seeded):
    """R1：同租户按 id 取回，字段与同步端点同口径。"""
    from fastapi.testclient import TestClient

    client = TestClient(app_factory(seeded["a"]))
    resp = client.get(f"/api/v1/documents/contract-review/{seeded['cr_a']}")

    assert resp.status_code == 200, resp.text
    data = _data(resp)
    assert data["id"] == seeded["cr_a"]
    assert data["title"] == "A 的采购合同"
    assert data["status"] == "degraded"
    # 与同步端点同口径：这几个键是前端渲染结论页的必需字段
    for key in ("source", "analysis_status", "overall_risk", "coverage", "findings"):
        assert key in data, f"读回响应缺少 {key}"


def test_missing_id_is_404(app_factory, seeded):
    """R2：不存在的 id ⇒ 404（且是端点发的，带业务错误码）。"""
    from fastapi.testclient import TestClient

    client = TestClient(app_factory(seeded["a"]))
    resp = client.get("/api/v1/documents/contract-review/999999")

    assert resp.status_code == 404, resp.text
    assert _err(resp).get("code") == "CONTRACT_REVIEW_NOT_FOUND"


def test_cross_tenant_readback_is_404_not_403(app_factory, seeded):
    """R3：**跨租户读 ⇒ 404，与「不存在」同码** —— 403 会确认资源存在，可被枚举。

    这是本轮新增读回接口的 **P0 判据**：审查记录里存着合同原文与风险结论，
    按 id 撞库一旦返回 403，攻击者就能枚举出「哪些 id 属于别人」。
    """
    from fastapi.testclient import TestClient

    client_a = TestClient(app_factory(seeded["a"]))
    hit = client_a.get(f"/api/v1/documents/contract-review/{seeded['cr_b']}")
    miss = client_a.get("/api/v1/documents/contract-review/999999")

    assert hit.status_code == 404, f"跨租户读泄露了归属：HTTP {hit.status_code}"
    # 与 R2 严格同码：不许按存在性区分租户归属
    assert hit.status_code == miss.status_code
    assert hit.json() == miss.json(), "跨租户与不存在的响应体必须完全一致"
    # 响应体里绝不能带出对方的任何字段
    assert "B 的采购合同" not in hit.text


def test_non_integer_id_is_422(app_factory, seeded):
    """R4：非整数 id ⇒ 422（证明请求**真的进了端点**，不是被路由层挡回 404）。"""
    from fastapi.testclient import TestClient

    client = TestClient(app_factory(seeded["a"]))
    resp = client.get("/api/v1/documents/contract-review/not-a-number")

    assert resp.status_code == 422, resp.text


def test_error_message_only_when_failed(app_factory, seeded):
    """R5：`error_message` **仅**在 status=failed 时出现。

    键存在与否本身是语义（「不适用」与「空字符串」不同）。成功/降级的记录
    带上这个键，前端会渲染出一个空的失败原因块；更糟的是它可能来自上一次失败。
    """
    from fastapi.testclient import TestClient

    client = TestClient(app_factory(seeded["a"]))

    ok = _data(client.get(f"/api/v1/documents/contract-review/{seeded['cr_a']}"))
    assert "error_message" not in ok, "非失败记录不得带 error_message"

    failed = _data(client.get(f"/api/v1/documents/contract-review/{seeded['cr_a_failed']}"))
    assert failed["status"] == "failed"
    assert failed.get("error_message") == "模型调用超时"


# ═══════════════════════ R6–R7 413 门控与异步入队 ═══════════════════════


def test_oversized_text_is_413_with_guidance(app_factory, seeded):
    """R6：超长文本 ⇒ **413**（不是 422），且 payload 能驱动前端决策门。

    模型层的边界判据（`test_contract_review_async.py` A4）测的是「Pydantic 拒不拒」。
    这里判的是**最后一跳**：`RequestValidationError` 有没有被转换成 413，
    以及 `field` / `max_length` / `guidance` 三个键在不在——
    **前端的 GateBanner 就是靠这三个键决定「提示什么、给哪两个选项」**，
    缺任何一个，用户看到的就是一句没有出路的报错。

    ⚠️ 判 413 而不是 422：`guidance=upload_or_async` 的语义是「内容超出处理上限」，
    422 会被前端当成「参数填错了」，从而不给「转异步」这个出口。
    """
    from fastapi.testclient import TestClient

    client = TestClient(app_factory(seeded["a"]))
    resp = client.post(
        "/api/v1/documents/contract-review",
        # 「甲方与乙方约定如下。」= 10 字 ⇒ ×3000 = 30000 字，越过同步上限 20000
        json={"title": "超长合同", "source_text": "甲方与乙方约定如下。" * 3000},
    )

    assert resp.status_code == 413, f"应为 413，实际 {resp.status_code}：{resp.text[:200]}"
    err = _err(resp)
    assert err.get("code") == "CONTENT_TOO_LARGE"
    assert err.get("field") == "source_text"
    assert err.get("max_length") == 20000
    assert err.get("guidance") == "upload_or_async"


def test_async_endpoint_enqueues_job(app_factory, seeded, monkeypatch):
    """R7：异步端点 ⇒ 202 + `job_id`，且**真的投进了队列**。

    不真跑：`job_queue` 换成记录器（worker 会去调 LLM）。
    判据钉的是「端点**确实入队了**」——只判 202 的话，
    有人把 `job_queue.enqueue(...)` 删掉，接口照样返回 202，任务却永远不会执行。
    """
    from fastapi.testclient import TestClient

    import app.services.job_handlers as jh

    calls: list[tuple[int, object]] = []

    class _Rec:
        async def enqueue(self, job_id, worker):
            calls.append((job_id, worker))

    monkeypatch.setattr(jh, "job_queue", _Rec())

    client = TestClient(app_factory(seeded["a"]))
    resp = client.post(
        "/api/v1/documents/contract-review/async",
        json={"title": "异步合同", "source_text": "甲方应在三十日内付款。"},
    )

    assert resp.status_code == 202, resp.text
    job_id = _data(resp).get("job_id")
    assert isinstance(job_id, int) and job_id > 0, f"未返回有效 job_id：{_data(resp)}"

    assert len(calls) == 1, f"未入队或重复入队：{calls}"
    assert calls[0][0] == job_id
    assert calls[0][1] is jh._contract_review_worker, "入队的不是合同审查 worker"
