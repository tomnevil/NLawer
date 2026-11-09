"""`POST /qa/stream` 端点层判据（Q-T 收尾批 · §3.33）。

此前零端点层覆盖。流式问答需要 LLM，测试环境无 LLM，故用 monkeypatch
把 `QAService.stream` 替换成产出假 SSE 的桩，重点验证：
1. 端点**要鉴权**（未带令牌必须 401）；
2. 端点把 `ctx.tenant_id` 传给服务层（租户隔离的反向向量）；
3. 流式响应正常返回。
"""
from __future__ import annotations

import ast
import asyncio
import json
import pathlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.rbac import Role
from app.core.security import hash_password
from app.models.base import Base
from app.models.enums import UserStatus
from app.models.identity import Tenant, User

TENANT_A = "tenant-a"


@pytest.fixture(scope="module")
def engine():
    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"qa_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            if not (await s.execute(
                select(Tenant).where(Tenant.tenant_id == TENANT_A)
            )).scalars().first():
                s.add(Tenant(tenant_id=TENANT_A, name="租户 A"))
            u = User(
                username=f"qaep-{uuid.uuid4().hex[:6]}",
                hashed_password=hash_password("Str0ngPass!"),
                full_name="adminA",
                role=Role.FIRM_ADMIN,
                tenant_id=TENANT_A,
                status=UserStatus.ACTIVE,
                is_active=True,
            )
            s.add(u)
            await s.commit()
            return {"a": u.id}

    return asyncio.run(_seed())


@pytest.fixture
def app_factory(engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    f = async_sessionmaker(engine, expire_on_commit=False)

    def _make(user_id):
        async def _db():
            async with f() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        async def _user():
            from app.models.identity import User as U

            async with f() as s:
                return (await s.execute(
                    select(U).where(U.id == user_id)
                )).scalars().one()

        app = create_app()
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user
        return app

    return _make


@pytest.fixture
def mock_stream(monkeypatch):
    """把 `QAService.stream` 换成假 SSE 桩，并记录传进来的 tenant_id。"""
    from app.config import settings
    from app.services.qa_service import QAService

    rec = {"tenant": None}

    def _make_stream(self, question, tenant_id, user_id=None):
        rec["tenant"] = tenant_id

        async def _gen():
            yield "data: " + json.dumps(
                {"type": "delta", "content": " 这是 mock 回答"}, ensure_ascii=False
            ) + "\n\n"
            yield "data: " + json.dumps(
                {"type": "done", "sections": {"answer": "这是 mock 回答"}},
                ensure_ascii=False,
            ) + "\n\n"

        return _gen()

    monkeypatch.setattr(QAService, "stream", _make_stream)
    # 关掉审核，避免依赖 moderator / LLM（与产品降级路径一致）
    monkeypatch.setattr(settings, "MODERATION_CHECK_INPUT", False)
    monkeypatch.setattr(settings, "MODERATION_CHECK_OUTPUT", False)
    return rec


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_stream_returns_content(app_factory, seeded, mock_stream):
    """QS1：流式响应正常返回且含 mock 内容。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "合同有效吗"})
        assert r.status_code == 200, r.text
        body = r.text
    assert "mock 回答" in body, f"流里没有 mock 内容：{body}"


def test_stream_passes_caller_tenant(app_factory, seeded, mock_stream):
    """QS2：端点把 `ctx.tenant_id` 传给服务层（反向向量：写死成别的租户 ⇒ 这条红）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "x"})
        assert r.status_code == 200, r.text
    assert mock_stream["tenant"] == TENANT_A, f"租户没传给服务层：{mock_stream}"


def test_stream_rejects_unauthenticated():
    """QS3：未带令牌必须 401（反向向量）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()  # 不覆盖 get_current_user ⇒ 走真实鉴权
    with TestClient(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "x"})
        assert r.status_code in (401, 403), f"未鉴权却放行：{r.status_code}"


def test_stream_ast_passes_tenant():
    """QS4（AST）：`ask_stream` 把 `ctx.tenant_id` 交给 `svc.stream`。"""
    import app.api.v1.qa as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    seg = None
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "ask_stream":
            seg = ast.get_source_segment(src, node)
    assert seg and "ctx.tenant_id" in seg, f"ask_stream 没传 ctx.tenant_id：{seg}"


# ---------------- A8：流式问答计费 ----------------


def _qa_used(engine, tenant_id):
    """读取当前账期 QA 已用量；没有额度行时返回 0。"""
    from app.api.v1.qa import _period
    from app.models.billing import UsageQuota
    from app.models.enums import UsageType

    async def _q():
        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            row = (
                await s.execute(
                    select(UsageQuota).where(
                        UsageQuota.tenant_id == tenant_id,
                        UsageQuota.usage_type == UsageType.QA,
                        UsageQuota.period == _period(),
                    )
                )
            ).scalars().first()
            return row.used_count if row else 0

    return asyncio.run(_q())


def _patch_stream(monkeypatch, events, *, fail_after=None):
    """把 QAService.stream 替换成给定事件序列；fail_after 用于模拟流中断。"""
    from app.config import settings
    from app.services.qa_service import QAService

    def _make_stream(self, question, tenant_id, user_id=None):
        async def _gen():
            for i, ev in enumerate(events):
                if fail_after is not None and i == fail_after:
                    raise RuntimeError("模拟模型连接中断")
                yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"

        return _gen()

    monkeypatch.setattr(QAService, "stream", _make_stream)
    monkeypatch.setattr(settings, "MODERATION_CHECK_INPUT", False)
    monkeypatch.setattr(settings, "MODERATION_CHECK_OUTPUT", False)


def test_stream_charges_usage_after_success(app_factory, seeded, mock_stream, engine):
    """A8-1：成功生成后按 /qa 同口径扣减，done 事件带 usage；前后差额 = 1。"""
    before = _qa_used(engine, TENANT_A)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "合同有效吗"})
        assert r.status_code == 200, r.text
        assert '"usage"' in r.text, f"done 事件没回传 usage：{r.text}"
    after = _qa_used(engine, TENANT_A)
    assert after - before == 1, f"流式成功应答未计费：before={before} after={after}"


def test_stream_no_charge_when_interrupted(app_factory, seeded, engine, monkeypatch):
    """A8-2：流中途断开（无 done 事件）时不扣费。"""
    _patch_stream(
        monkeypatch,
        [
            {"type": "delta", "content": "半句话"},
            {"type": "done", "sections": {"conclusion": "不应走到这里"}},
        ],
        fail_after=1,
    )
    before = _qa_used(engine, TENANT_A)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "x"})
        assert r.status_code == 200, r.text
        assert "error" in r.text
    after = _qa_used(engine, TENANT_A)
    assert after == before, f"流中断仍扣费：before={before} after={after}"


def test_stream_no_charge_when_generation_failed(app_factory, seeded, engine, monkeypatch):
    """A8-3：模型异常降级为安抚回复（done.failed=True）不计费。"""
    _patch_stream(
        monkeypatch,
        [
            {"type": "done", "failed": True, "sections": {"conclusion": "抱歉，请稍后再试"}},
        ],
    )
    before = _qa_used(engine, TENANT_A)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "x"})
        assert r.status_code == 200, r.text
    after = _qa_used(engine, TENANT_A)
    assert after == before, f"降级回复仍扣费：before={before} after={after}"


# ---------------- 草稿异步生成 + B3 兜底 ----------------


def test_stream_schedules_background_draft_and_charges_once(
    app_factory, seeded, engine, monkeypatch
):
    """草稿异步：done.draft_deferred=True ⇒ 后台任务被调度，且只计费一次。"""
    from app.services.qa_service import QAService

    calls = []

    async def _fake_generate(self, question, answer, tenant_id, user_id=None):
        calls.append((question, answer, tenant_id, user_id))
        return True

    monkeypatch.setattr(QAService, "generate_draft", _fake_generate)
    _patch_stream(
        monkeypatch,
        [
            {
                "type": "done",
                "failed": False,
                "draft_deferred": True,
                "sections": {"conclusion": "先给客户的回答"},
            }
        ],
    )

    before = _qa_used(engine, TENANT_A)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "合同有效吗"})
        assert r.status_code == 200, r.text
    after = _qa_used(engine, TENANT_A)

    assert after - before == 1, f"应为回答成功计费一次：before={before} after={after}"
    assert len(calls) == 1, f"后台草稿任务未执行：{calls}"
    assert calls[0][0] == "合同有效吗" and calls[0][1] == "先给客户的回答", calls
    assert calls[0][2] == TENANT_A, "后台任务必须带租户"


def test_stream_does_not_schedule_draft_when_failed(app_factory, seeded, monkeypatch):
    from app.services.qa_service import QAService

    calls = []

    async def _fake_generate(self, question, answer, tenant_id, user_id=None):
        calls.append(question)
        return True

    monkeypatch.setattr(QAService, "generate_draft", _fake_generate)
    _patch_stream(monkeypatch, [{"type": "done", "failed": True, "sections": {"conclusion": "抱歉"}}])

    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa/stream", json={"question": "x"})
        assert r.status_code == 200, r.text
    assert calls == [], "生成失败不应再派发草稿任务"


def test_rest_qa_skips_billing_when_degraded(app_factory, seeded, engine, monkeypatch):
    """B3：引用校验失败兜底为友好话术后，`/qa` 仍 200，但不计费。"""
    from app.config import settings
    from app.services.qa_service import FALLBACK_ANSWER, QAService

    async def _fake_build(self, question, tenant_id, user_id=None):
        return {
            "answer": FALLBACK_ANSWER,
            "sections": {"conclusion": "", "legal_basis": "", "advice": "", "risk": ""},
            "citations": [],
            "disclaimer": "x",
            "structured": False,
            "draft_saved": False,
            "degraded": True,
            "degrade_reason": "CITATION_MISSING",
            "billable": False,
        }

    monkeypatch.setattr(QAService, "build", _fake_build)
    monkeypatch.setattr(settings, "MODERATION_CHECK_INPUT", False)
    monkeypatch.setattr(settings, "MODERATION_CHECK_OUTPUT", False)

    before = _qa_used(engine, TENANT_A)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/qa", json={"question": "合同有效吗"})
    after = _qa_used(engine, TENANT_A)

    assert r.status_code == 200, r.text
    body = r.json()["data"]
    assert body["degraded"] is True and body["degrade_reason"] == "CITATION_MISSING", body
    assert body["usage"] is None, body
    assert body["draft_saved"] is False, body
    assert after == before, f"生成失败仍扣费：before={before} after={after}"
