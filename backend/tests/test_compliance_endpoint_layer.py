"""合规扫描 3 条路由的**端点层**判据（Q-T 清单第五批）。

## 为什么这个文件必须存在

`GET/POST /api/v1/compliance/scans*` 共 3 条路由，在 `tests/` 下**从未被请求过**，
而且 `grep -rl "compliance" tests/` 是**空的**——与文书同批，都是
**服务层和端点层双双零覆盖**的模块。

合规扫描结论**可能作为对外交付依据**（`compliance.py:56` 自己写了
「必须能追溯谁发起的、扫什么范围」），`scope` / `input_summary` 里是客户
内部的真实经营信息。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| K1 | 3 条路由**首次真实 HTTP 请求**：全员非 500 |
| K2 | 列表租户隔离（双向） |
| K3 | 跨租户读详情 ⇒ 404 `SCAN_NOT_FOUND`，**且 findings 不外泄** |
| K4 | `create` 落在本租户 ⇒ 租户 B 读不到 |
| K5 | 缺 `title` ⇒ 422（schema 是唯一关口） |
| K6 | AST：列表有租户过滤、详情有内联校验、`create` 用 `ctx.tenant_id` |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
"""
from __future__ import annotations

import ast
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
    dbfile = base / f"cmp_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A / B 各一条扫描任务。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
    from app.models.identity import Tenant, User
    from app.models.knowledge import ComplianceScan

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in (TENANT_A, TENANT_B):
                exists = (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid))).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid, role=Role.LAWYER):
                return User(
                    username=f"cep2-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            # 2026-09-22：`POST /scans` 收口为 `compliance:scan`（当前仅 ENTERPRISE_ADMIN）
            # ⇒ 建扫描的用例改用 admin 身份；client 用于判「门控真的拦住了」。
            admin_a = _user("adminA", TENANT_A, Role.ENTERPRISE_ADMIN)
            client_a = _user("clientA", TENANT_A, Role.CLIENT)
            s.add_all([lawyer_a, lawyer_b, admin_a, client_a])
            await s.flush()

            # 直接构造 ORM 行：`ComplianceService.create_scan` 会顺带落
            # `dimension_scores` 等默认值，本文件的判据不需要它们，
            # 用服务层反而把「夹具」和「被测对象」耦在一起。
            sa = ComplianceScan(tenant_id=TENANT_A, title="A 的劳动用工扫描",
                                scope="全公司", input_summary="500 人规模")
            sb = ComplianceScan(tenant_id=TENANT_B, title="B 的数据隐私扫描",
                                scope="B 方范围", input_summary="B 方摘要")
            s.add_all([sa, sb])
            await s.commit()

            return {"a": lawyer_a.id, "b": lawyer_b.id,
                    "admin_a": admin_a.id, "client_a": client_a.id,
                    "scan_a": sa.id, "scan_b": sb.id}

    return asyncio.run(_seed())


@pytest.fixture
def app_factory(engine):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。"""
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


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


def _data(resp):
    return resp.json().get("data") or {}


def _code(resp) -> str:
    return str(resp.json().get("error", {}).get("code", ""))


def _func(name: str) -> str:
    import app.api.v1.compliance as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"compliance.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ K1 装配层冒烟 ═══════════════════════


def test_all_three_routes_respond(app_factory, seeded):
    """K1：3 条路由**第一次**被真实请求——必须都不是 500。

    ⚠️ `POST /scans` 自 2026-09-22 起挂 `compliance:scan`，故用 `admin_a` 身份；
    两条读路由没有权限码门控，仍用律师身份（保持原覆盖）。
    """
    with _client(app_factory(seeded["a"])) as c:
        assert c.get("/api/v1/compliance/scans").status_code == 200
        assert c.get(f"/api/v1/compliance/scans/{seeded['scan_a']}").status_code == 200
    with _client(app_factory(seeded["admin_a"])) as c:
        r = c.post("/api/v1/compliance/scans", json={"title": "冒烟扫描"})
        assert r.status_code == 200, r.text
        assert "job_id" in _data(r), r.text


# ═══════════════════════ K2 列表租户隔离 ═══════════════════════


def test_list_is_tenant_scoped_both_directions(app_factory, seeded):
    """K2：列表双向租户隔离。"""
    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        rows = _data(c.get("/api/v1/compliance/scans")) or []
        ids = {r["id"] for r in rows}
        assert seeded["scan_a"] in ids
        assert seeded["scan_b"] not in ids

    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        rows = _data(c.get("/api/v1/compliance/scans")) or []
        ids = {r["id"] for r in rows}
        assert seeded["scan_b"] in ids
        assert seeded["scan_a"] not in ids


# ═══════════════════════ K3 跨租户读详情 ═══════════════════════


def test_get_scan_cross_tenant_is_404_and_leaks_nothing(app_factory, seeded):
    """K3：跨租户读详情 ⇒ 404，**findings 与 scope/input_summary 都不外泄**。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/compliance/scans/{seeded['scan_b']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "SCAN_NOT_FOUND", r.text
        for leak in ("B 的数据隐私扫描", "B 方范围", "B 方摘要"):
            assert leak not in r.text, f"外泄了：{leak}"


# ═══════════════════════ K4 create 落在本租户 ═══════════════════════


def test_create_lands_in_callers_tenant(app_factory, seeded):
    """K4：`create` 落在本租户 ⇒ 租户 B 读不到。"""
    app = app_factory(seeded["admin_a"])
    with _client(app) as c:
        r = c.post("/api/v1/compliance/scans",
                   json={"title": "A 新建的广告营销扫描", "dimensions": ["AD"]})
        assert r.status_code == 200, r.text
        new_id = _data(r)["id"]

    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        r = c.get(f"/api/v1/compliance/scans/{new_id}")
        assert r.status_code == 404, "租户 A 建的扫描被租户 B 读到了"
        assert _code(r) == "SCAN_NOT_FOUND", r.text


# ═══════════════════════ K5 schema 关口 ═══════════════════════


def test_missing_title_is_422(app_factory, seeded):
    """K5：缺 `title` ⇒ 422（schema 是唯一关口）。"""
    app = app_factory(seeded["admin_a"])
    with _client(app) as c:
        r = c.post("/api/v1/compliance/scans", json={"dimensions": ["LABOR"]})
        assert r.status_code == 422, r.text


# ═══════════ K7 端点层门控（2026-09-22 收口 TENANT 档）═══════════


def test_client_cannot_create_scan(app_factory, seeded):
    """K7：客户**不能**发起合规扫描 ⇒ 403。

    收口前 `POST /scans` 端点层零授权动作（只传 `ctx.tenant_id`），
    任何已登录用户（含 CLIENT）都能发起 —— 扫描消耗算力，
    且结论可能作为对外交付依据，`compliance.py` 自己的文档头就写明了这点。
    """
    app = app_factory(seeded["client_a"])
    with _client(app) as c:
        r = c.post("/api/v1/compliance/scans", json={"title": "客户发起的扫描"})
        assert r.status_code == 403, f"客户竟然发起了合规扫描：{r.text}"


# ═══════════════════════ K6 AST 装配层 ═══════════════════════


def test_list_filters_by_tenant():
    """K6a：列表必须带 `ctx.tenant_id` 过滤。"""
    seg = _func("list_scans")
    assert "ComplianceScan.tenant_id == ctx.tenant_id" in seg, "列表丢了租户过滤"


def test_get_scan_checks_tenant_inline():
    """K6b：详情必须有内联租户校验。"""
    seg = _func("get_scan")
    assert "scan.tenant_id != ctx.tenant_id" in seg, "详情丢了租户校验"


def test_create_uses_ctx_tenant_not_user_tenant():
    """K6c：`create` 的 `tenant_id` 必须来自 `ctx`，不能来自 `user`。"""
    seg = _func("create_scan")
    assert "tenant_id=ctx.tenant_id" in seg, "create 的租户来源不是 ctx"
    assert "user.tenant_id" not in seg, "又用回 user.tenant_id 了"
