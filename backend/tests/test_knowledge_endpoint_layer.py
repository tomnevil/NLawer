"""`knowledge/docs`(+`{doc_id}`) 端点层判据（Q-T 收尾批 · §3.33）。

4 条路由此前零端点层覆盖。`KnowledgeService.get/delete` 已经做了
「读取后二次校验归属」（`doc.tenant_id != tenant_id ⇒ TenantDeniedError`），
本批的判据就是**证明这道隔离真的生效**——而不是只存在于服务层源码里。
"""
from __future__ import annotations

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
    dbfile = base / f"kn_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
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

            def _u(tag, tid, role=Role.FIRM_ADMIN):
                return User(
                    username=f"knp-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            a, b = _u("adminA", TENANT_A), _u("adminB", TENANT_B)
            # 2026-09-22：`DELETE /docs/{id}` 收口为 `knowledge:write`，
            # CLIENT 没有这个码 ⇒ 用于 KD4 判「门控真的拦住了」。
            client = _u("clientA", TENANT_A, Role.CLIENT)
            s.add_all([a, b, client])
            await s.commit()
            return {"a": a.id, "b": b.id, "client": client.id}

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


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def _data(resp):
    return resp.json().get("data") or {}


def _create_doc(c, **kw):
    r = c.post("/api/v1/knowledge/docs", json={
        "title": kw.get("title", "合同模板"),
        "doc_type": kw.get("doc_type", "CONTRACT"),
        "content": kw.get("content", "示例内容"),
    })
    assert r.status_code == 200, r.text
    return _data(r)


def test_list_only_own_tenant(app_factory, seeded):
    """KD1：列表只含本租户文档（反向向量：B 的列表必须为空）。"""
    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        _create_doc(c, title="A 的文档")
        r = c.get("/api/v1/knowledge/docs")
        assert r.status_code == 200, r.text
        items = _data(r).get("items") or []
        assert len(items) == 1, f"A 的列表应只有自己 1 篇：{items}"
    # B 的列表必须为空——若端点把租户写死成 A，这里会冒出 A 的文档
    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        r = c.get("/api/v1/knowledge/docs")
        items = _data(r).get("items") or []
        assert len(items) == 0, f"B 的列表不应出现 A 的文档：{items}"


def test_get_cross_tenant_denied(app_factory, seeded):
    """KD2：跨租户读必须被拒（404/403）。"""
    app_a = app_factory(seeded["a"])
    doc_id = None
    with _client(app_a) as c:
        doc_id = _create_doc(c)["id"]
    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        r = c.get(f"/api/v1/knowledge/docs/{doc_id}")
        assert r.status_code in (403, 404), f"跨租户读应被拒：{r.status_code} {r.text}"


def test_delete_cross_tenant_denied(app_factory, seeded):
    """KD3：跨租户删必须被拒（404/403）；对照：本租户可删。"""
    app_a = app_factory(seeded["a"])
    doc_id = None
    with _client(app_a) as c:
        doc_id = _create_doc(c)["id"]
    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        r = c.delete(f"/api/v1/knowledge/docs/{doc_id}")
        assert r.status_code in (403, 404), f"跨租户删应被拒：{r.status_code} {r.text}"
    with _client(app_a) as c:
        r = c.delete(f"/api/v1/knowledge/docs/{doc_id}")
        assert r.status_code == 200, r.text


def test_client_cannot_delete_knowledge_doc(app_factory, seeded):
    """KD4：客户**不能**删所里的知识库文档 ⇒ 403。

    收口前 `DELETE /docs/{id}` 端点层零授权动作（只按 `ctx.tenant_id` 取数），
    **任何已登录用户（含 CLIENT）都能删**。知识库内容可能构成商业秘密。
    反向向量：管理员仍能删（否则这条会变成「谁都删不了」的假绿）。
    """
    app_admin = app_factory(seeded["a"])
    with _client(app_admin) as c:
        doc_id = _create_doc(c, title="所里的商业秘密")["id"]

    app_client = app_factory(seeded["client"])
    with _client(app_client) as c:
        r = c.delete(f"/api/v1/knowledge/docs/{doc_id}")
        assert r.status_code == 403, f"客户竟然删掉了知识库文档：{r.text}"

    # 反向向量：文档还在，且管理员仍能删
    with _client(app_admin) as c:
        assert c.get(f"/api/v1/knowledge/docs/{doc_id}").status_code == 200
        assert c.delete(f"/api/v1/knowledge/docs/{doc_id}").status_code == 200


def test_create_writes_own_tenant(app_factory, seeded):
    """KD4：创建的文档必须归属调用方租户（反向向量：写死成 A ⇒ 这条红）。"""
    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        d = _create_doc(c, title="B 的文档")
        assert d.get("tenant_id") == TENANT_B, f"创建的文档租户错乱：{d}"


def test_endpoints_pass_tenant_context():
    """KD5（AST）：4 个端点都把 `ctx.tenant_id` 交给服务层。"""
    import ast

    import app.api.v1.knowledge as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for fn in ("create_doc", "list_docs", "get_doc", "delete_doc"):
        seg = None
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == fn:
                seg = ast.get_source_segment(src, node)
        assert seg and "ctx.tenant_id" in seg, f"{fn} 没传 ctx.tenant_id"
