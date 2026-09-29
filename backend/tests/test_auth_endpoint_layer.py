"""`GET /auth/me` 端点层判据（Q-T 清单收尾批 · §3.33）。

这路由此前零端点层覆盖：`tests/` 从没请求过它。

`/auth/me` 天然只返回「当前登录用户」——没有租户参数可混淆，
所以核心判据是「**返回的就是令牌持有者本人**」，而不是某个硬编码 / 别人的身份。
"""
from __future__ import annotations

import ast
import asyncio
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
TENANT_B = "tenant-b"


@pytest.fixture(scope="module")
def engine():
    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"auth_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """A=id 1、B=id 2，两个租户各一名所管理员。

    注入器 `authme_imposter` 会改成「返回 id 最大的用户」，所以必须保证 B 的 id 最大。
    """
    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            for tid in (TENANT_A, TENANT_B):
                if not (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid)
                )).scalars().first():
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))

            def _u(tag, tid):
                return User(
                    username=f"aep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.FIRM_ADMIN,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            a, b = _u("adminA", TENANT_A), _u("adminB", TENANT_B)
            s.add_all([a, b])
            await s.commit()
            return {"a": a.id, "b": b.id}

    return asyncio.run(_seed())


@pytest.fixture
def app_factory(engine):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    f = async_sessionmaker(engine, expire_on_commit=False)

    def _make(user_id=None):
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
        if user_id is not None:
            app.dependency_overrides[get_current_user] = _user
        return app

    return _make


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def _data(resp):
    return resp.json().get("data") or {}


def _func(name: str) -> str:
    import app.api.v1.auth as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"auth.py 里找不到 {name}")


def test_me_returns_caller_identity(app_factory, seeded):
    """A1：`/auth/me` 返回**令牌持有者本人**（反向向量：返回别人身份 ⇒ 这条红）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get("/api/v1/auth/me")
        assert r.status_code == 200, r.text
        u = _data(r) or {}
        assert u.get("id") == seeded["a"], f"返回了非本人的用户：{u}"
        assert u.get("tenant_id") == TENANT_A, f"租户错乱：{u}"


def test_me_requires_auth_dependency():
    """A2（AST 登记）：`me` 必须依赖 `get_current_user`，不能裸读库或写死身份。

    （`authme_noauth` 臂摘掉依赖后这条会红，逼着改口径时回头确认。）
    """
    src = _func("me")
    assert "get_current_user" in src, f"me 没有鉴权依赖：{src}"


def test_me_rejects_unauthenticated():
    """A3：未带令牌必须 401（反向向量）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()  # 不覆盖 get_current_user ⇒ 走真实鉴权
    with TestClient(app) as c:
        r = c.get("/api/v1/auth/me")
        assert r.status_code in (401, 403), f"未鉴权却放行：{r.status_code}"
