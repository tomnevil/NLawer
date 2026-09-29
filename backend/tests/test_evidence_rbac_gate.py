"""Q-R（2026-09-21）证据端点**权限码门控**判据。

证据端点此前**完全无权限码门控**，仅靠 `_case_or_404` 做案件归属隔离。
Q-R 裁定：证据 router 统一按 `evidence:read` 门控，写端点（上传 / 重解析）
再叠加 `evidence:create` / `evidence:update`。归属隔离仍由 `_case_or_404`
兜底（客户只能碰自己案件的材料）。

覆盖：
- R-gate-A：AST 枚举证据路由，写端点自身必须挂对应写权限；router 级统一挂 read。
- R-gate-B：无 `evidence:read` 的角色（如 ENTERPRISE_ADMIN）⇒ 403。
- R-gate-C（反向量）：有 `evidence:read` 的 FIRM_ADMIN 过门（案件不存在 ⇒ 404，非 403）。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "evidence-gate-a"
TENANT_B = "evidence-gate-b"

EVIDENCE_FILE = pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "evidence.py"


def _src() -> str:
    return EVIDENCE_FILE.read_text(encoding="utf-8")


def test_every_evidence_write_route_is_permission_gated():
    """R-gate-A：证据写端点（upload / reparse）自身必须挂对应写权限；
    router 级统一挂 `evidence:read`。"""
    tree = ast.parse(_src())
    router_blob = ast.unparse(tree)
    assert "require_permissions" in router_blob, "证据 router 没有统一权限门控"

    # ⚠️ 必须看 **decorator_list**：`ast.get_source_segment` 只从 `async def` 开始，
    # 不含装饰器 ⇒ 用它检查门控会得到一个永远不含 `require_permissions` 的字符串。
    for name in ("upload_evidence", "reparse"):
        deco = None
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
                deco = "".join(ast.unparse(d) for d in node.decorator_list)
        assert deco is not None, f"找不到 {name}"
        assert "require_permissions" in deco, f"{name} 的装饰器没有写权限门控：{deco}"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"evidence_gate_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in (TENANT_A, TENANT_B):
                if not (await s.execute(
                        select(Tenant).where(Tenant.tenant_id == tid))).scalars().first():
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()
            firm = User(
                username="gate-firm", hashed_password=hash_password("Str0ngPass!"),
                full_name="所管理员", role=Role.FIRM_ADMIN, tenant_id=TENANT_A,
                status=UserStatus.ACTIVE, is_active=True,
            )
            ent = User(
                username="gate-ent", hashed_password=hash_password("Str0ngPass!"),
                full_name="企业管理员", role=Role.ENTERPRISE_ADMIN, tenant_id=TENANT_B,
                status=UserStatus.ACTIVE, is_active=True,
            )
            s.add_all([firm, ent])
            await s.commit()
            return {"firm": firm.id, "ent": ent.id}

    return asyncio.run(_seed())


@pytest.fixture
def app_factory(engine):
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
                return (await s.execute(
                    select(User).where(User.id == user_id))).scalars().one()

        app = create_app()
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_current_user] = _override_user
        return app

    return _make


def test_no_evidence_role_is_rejected(app_factory, seeded):
    """R-gate-B：无 `evidence:read` 的角色（ENTERPRISE_ADMIN）⇒ 403。

    门控在端点体之前触发，无需案件存在。
    """
    from fastapi.testclient import TestClient

    app = app_factory(seeded["ent"])
    with TestClient(app) as c:
        r = c.get("/api/v1/evidence/cases/999999")
        assert r.status_code == 403, f"无 evidence 权限却放行：{r.status_code} {r.text[:200]}"


def test_firm_admin_passes_the_gate(app_factory, seeded):
    """R-gate-C（反向量）：有 `evidence:read` 的 FIRM_ADMIN 过门
    （案件不存在 ⇒ 404，而非 403）。"""
    from fastapi.testclient import TestClient

    app = app_factory(seeded["firm"])
    with TestClient(app) as c:
        r = c.get("/api/v1/evidence/cases/999999")
        assert r.status_code == 404, f"门控误拒所管理员：{r.status_code} {r.text[:200]}"
        assert r.status_code != 403
