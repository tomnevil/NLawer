"""A6：L3 复核资格**从 LawyerProfile 读取**，并限制非 L3 建/批 L3。

## 缺陷

`reviews.py::_can_triage` 旧实现是
`_is_admin(ctx) or bool(getattr(user, "can_l3_review", False))`。
`can_l3_review` 在 `LawyerProfile` 上，**`User` 上没有这个字段** ⇒ `getattr`
恒为默认值 `False` ⇒ 非管理员的 L3 律师：

- `GET /reviews?scope=pool` 恒 403（看不到待分配池，无法兜底认领）；
- 池内未分配的复核对他们恒 404（`_assert_review_access` 同一条判定）。

同时，`POST /reviews/ensure` 允许任何人把 `required_level=L3` 建出来，
`decide` 又允许非 L3 对 L3 复核「通过」——L3 终审责任可被架空。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| L1 | L2 律师建 L3 复核 ⇒ 403 `REVIEW_LEVEL_INSUFFICIENT`，且不落库 |
| L2 | L3 律师建 L3 复核 ⇒ 200 |
| L3 | 律所管理员建 L3 复核 ⇒ 200（反向向量：门禁不能误伤管理员） |
| L4 | L2 看不到待分配池（403）；L3 能看到（200，含池内任务）——A6 主回归 |
| L5 | L2 对 L3 复核 APPROVED ⇒ 403，状态不变 |
| L6 | L3 对 L3 复核 APPROVED ⇒ 200 `confirmed`（反向向量） |
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import func, select

TENANT = "l3-guard"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"l3_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """一个租户：L3 律师（档案 True）/ L2 律师（档案 False）/ 律所管理员。

    另播三条复核：一条进池（无承办人）、两条 L3 要求级别（分别指派给 L2 / L3）。
    """
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.analysis import CaseAnalysis
    from app.models.base import Base
    from app.models.case import Case
    from app.models.enums import (
        CaseStatus,
        ReviewLevel,
        ReviewStatus,
        ReviewTargetType,
        UserStatus,
    )
    from app.models.identity import LawyerProfile, Tenant, User
    from app.models.review import Review

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            s.add(Tenant(tenant_id=TENANT, name="L3 复核资格租户"))
            await s.flush()

            def _user(tag, role):
                return User(
                    username=f"l3-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=TENANT,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            l3 = _user("l3", Role.LAWYER)
            l2 = _user("l2", Role.LAWYER)
            admin = _user("admin", Role.FIRM_ADMIN)
            s.add_all([l3, l2, admin])
            await s.flush()

            s.add_all([
                LawyerProfile(
                    tenant_id=TENANT, user_id=l3.id,
                    specialties="合同纠纷", practice_years=8, can_l3_review=True,
                ),
                LawyerProfile(
                    tenant_id=TENANT, user_id=l2.id,
                    specialties="劳动争议", practice_years=3, can_l3_review=False,
                ),
            ])

            case = Case(
                tenant_id=TENANT,
                case_no=f"CASE-L3-{uuid.uuid4().hex[:6]}",
                title="L3 复核资格案件",
                status=CaseStatus.ACCEPTED,
                lawyer_id=l2.id,
            )
            s.add(case)
            await s.flush()

            analysis = CaseAnalysis(tenant_id=TENANT, case_id=case.id, version=1)
            s.add(analysis)
            await s.flush()

            pool_review = Review(
                tenant_id=TENANT,
                target_type=ReviewTargetType.DOCUMENT,
                target_id=1001,
                status=ReviewStatus.DRAFT,
                required_level=ReviewLevel.L2,
                assignee_id=None,
            )
            l3_pending_by_l2 = Review(
                tenant_id=TENANT,
                target_type=ReviewTargetType.CASE_ANALYSIS,
                target_id=analysis.id,
                case_id=case.id,
                status=ReviewStatus.PENDING_CONFIRM,
                required_level=ReviewLevel.L3,
                assignee_id=l2.id,
            )
            l3_pending_by_l3 = Review(
                tenant_id=TENANT,
                target_type=ReviewTargetType.DOCUMENT,
                target_id=1002,
                status=ReviewStatus.PENDING_CONFIRM,
                required_level=ReviewLevel.L3,
                assignee_id=l3.id,
            )
            s.add_all([pool_review, l3_pending_by_l2, l3_pending_by_l3])
            await s.commit()

            return {
                "l3": l3.id, "l2": l2.id, "admin": admin.id,
                "analysis_id": analysis.id,
                "pool_review": pool_review.id,
                "l3_by_l2": l3_pending_by_l2.id,
                "l3_by_l3": l3_pending_by_l3.id,
            }

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
                return (
                    await s.execute(select(User).where(User.id == user_id))
                ).scalars().one()

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


def _ensure(c, target_id: int, required_level: str = "L2"):
    return c.post(
        "/api/v1/reviews/ensure",
        params={
            "target_type": "CASE_ANALYSIS",
            "target_id": target_id,
            "required_level": required_level,
        },
    )


def _status(engine, review_id: int) -> str:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.review import Review

    async def _q():
        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return (await s.get(Review, review_id)).status.value

    return asyncio.run(_q())


def _count_reviews(engine) -> int:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.review import Review

    async def _q():
        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return int((await s.execute(select(func.count()).select_from(Review))).scalar_one())

    return asyncio.run(_q())


# ═══════════════════════ L1–L3 建 L3 的门禁 ═══════════════════════


def test_l2_lawyer_cannot_create_l3_review(app_factory, seeded, engine):
    """L1：L2 律师建 L3 复核 ⇒ 403 `REVIEW_LEVEL_INSUFFICIENT`，且不落库。"""
    before = _count_reviews(engine)
    app = app_factory(seeded["l2"])
    with _client(app) as c:
        r = _ensure(c, seeded["analysis_id"], required_level="L3")
    assert r.status_code == 403, r.text
    assert _code(r) == "REVIEW_LEVEL_INSUFFICIENT", r.text
    assert _count_reviews(engine) == before, "被拒绝的 L3 复核仍然落库了"


def test_l3_lawyer_can_create_l3_review(app_factory, seeded):
    """L2：档案 `can_l3_review=True` 的律师建 L3 复核 ⇒ 200。"""
    app = app_factory(seeded["l3"])
    with _client(app) as c:
        r = _ensure(c, seeded["analysis_id"], required_level="L3")
    assert r.status_code == 200, r.text
    assert _data(r)["required_level"] == "L3", r.text


def test_firm_admin_can_create_l3_review(app_factory, seeded):
    """L3（反向向量）：律所管理员不受「非 L3 不得建 L3」影响。"""
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = _ensure(c, seeded["analysis_id"], required_level="L3")
    assert r.status_code == 200, r.text
    assert _data(r)["required_level"] == "L3", r.text


# ═══════════════════════ L4 待分配池可见性（A6 主回归） ═══════════════════════


def test_l2_lawyer_cannot_see_pool(app_factory, seeded):
    """L4a：L2 律师看待分配池 ⇒ 403。"""
    app = app_factory(seeded["l2"])
    with _client(app) as c:
        r = c.get("/api/v1/reviews", params={"scope": "pool"})
    assert r.status_code == 403, r.text
    assert _code(r) == "PERMISSION_DENIED", r.text


def test_l3_lawyer_can_see_pool(app_factory, seeded):
    """L4b：L3 律师看待分配池 ⇒ 200，且能看到池内那条复核。

    旧实现读 `user.can_l3_review`（不存在）⇒ 恒 False ⇒ 这里会 403。
    """
    app = app_factory(seeded["l3"])
    with _client(app) as c:
        r = c.get("/api/v1/reviews", params={"scope": "pool"})
    assert r.status_code == 200, r.text
    ids = {i["id"] for i in _data(r).get("items", [])}
    assert seeded["pool_review"] in ids, f"L3 律师看不到池内复核：{r.text}"


def test_l2_lawyer_cannot_read_pool_review_by_id(app_factory, seeded):
    """L4c：L2 直接按 ID 读池内未分配复核 ⇒ 404（不泄露存在性）。"""
    app = app_factory(seeded["l2"])
    with _client(app) as c:
        r = c.get(f"/api/v1/reviews/{seeded['pool_review']}")
    assert r.status_code == 404, r.text
    assert _code(r) == "REVIEW_NOT_FOUND", r.text


def test_l3_lawyer_can_read_pool_review_by_id(app_factory, seeded):
    """L4d（反向向量）：L3 按 ID 读池内复核 ⇒ 200。

    这条压在 `_assert_review_access` 里的 `await _can_triage(...)`：
    漏掉 `await` 时协程对象恒真 ⇒ **L2 也会被放行**，L4c 就会红。
    """
    app = app_factory(seeded["l3"])
    with _client(app) as c:
        r = c.get(f"/api/v1/reviews/{seeded['pool_review']}")
    assert r.status_code == 200, r.text
    assert _data(r)["id"] == seeded["pool_review"]


# ═══════════════════════ L5–L6 批 L3 的门禁 ═══════════════════════


def test_l2_lawyer_cannot_approve_l3_review(app_factory, seeded, engine):
    """L5：L2 律师对 L3 复核 APPROVED ⇒ 403，状态保持 `pending_confirm`。"""
    rid = seeded["l3_by_l2"]
    assert _status(engine, rid) == "pending_confirm"
    app = app_factory(seeded["l2"])
    with _client(app) as c:
        r = c.post(f"/api/v1/reviews/{rid}/decide", json={"decision": "APPROVED"})
    assert r.status_code == 403, r.text
    assert _code(r) == "REVIEW_LEVEL_INSUFFICIENT", r.text
    assert _status(engine, rid) == "pending_confirm", "L2 把 L3 复核批掉了"


def test_l3_lawyer_can_approve_l3_review(app_factory, seeded, engine):
    """L6（反向向量）：L3 律师对 L3 复核 APPROVED ⇒ `confirmed`。"""
    rid = seeded["l3_by_l3"]
    app = app_factory(seeded["l3"])
    with _client(app) as c:
        r = c.post(f"/api/v1/reviews/{rid}/decide", json={"decision": "APPROVED"})
    assert r.status_code == 200, r.text
    assert _data(r)["status"] == "confirmed", r.text
    assert _status(engine, rid) == "confirmed"
