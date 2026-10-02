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
