"""派单 4 个路由的**端点层**判据（Q-T 清单第四批）。

## 为什么这个文件必须存在

`GET/POST /api/v1/dispatches*` 共 4 条路由，在 `tests/` 下**从未被请求过**。
派单的服务层只有 `test_dispatch_rules.py`（2 KB，纯派单规则引擎），
`test_csrf_middleware_dispatch.py` 是 CSRF 中间件——**没人测过派单端点**。

而派单是**责任链的起点**：`accept` / `grab` 会把 `case.lawyer_id` 改成接单人、
把案件状态推进到 `ACCEPTED`。装配层漏一个参数，后果不是「读到了别人的数据」，
而是「**别人律所的案件被挂到了我名下、并且被推进了状态**」。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| P1 | 4 条路由**首次真实 HTTP 请求**：全员非 500 |
| P2 | 列表租户隔离（双向） |
| P3 | 抢单池：只含本租户 PENDING 且（未指定 / POOL） |
| **P4** | **跨租户 `accept` ⇒ 404 `DISPATCH_NOT_FOUND`** |
| **P5** | **跨租户 `grab` ⇒ 404** |
| **P6** | 反向量：跨租户接单**不得改动对方**的 dispatch / case |
| P7 | 同租户接单**正常工作**（防止修完把功能焊死） |
| P8 | 同租户但不是指定律师 ⇒ 400 `DISPATCH_RULE_CONFLICT`（规则仍生效） |
| P9 | AST：接单/抢单把 `ctx.tenant_id` 交给守卫（带命中数下限自检） |

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
    dbfile = base / f"disp_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A：律师甲（+ 律师丙，用于「非指定律师」）；租户 B：律师乙。

    每个租户各一条 POOL 待接派单 + 一条指定律师派单。
    """
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.case import Case, Dispatch
    from app.models.enums import (
        CaseStatus,
        DispatchMode,
        DispatchStatus,
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
                exists = (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid))).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid):
                return User(
                    username=f"dep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.LAWYER,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_c = _user("lawyerC", TENANT_A)  # 同租户另一律师（非指定）
            lawyer_b = _user("lawyerB", TENANT_B)
            s.add_all([lawyer_a, lawyer_c, lawyer_b])
            await s.flush()

            def _case(tid, tag):
                return Case(
                    tenant_id=tid,
                    case_no=f"CASE-{tag}-{uuid.uuid4().hex[:6]}",
                    title=f"{tag} 的案件",
                    status=CaseStatus.DISPATCHED,
                )

            case_a = _case(TENANT_A, "A")
            case_b = _case(TENANT_B, "B")
            case_d = _case(TENANT_A, "D")
            s.add_all([case_a, case_b, case_d])
            await s.flush()

            def _disp(tid, case, mode, lawyer_id):
                return Dispatch(
                    tenant_id=tid,
                    case_id=case.id,
                    mode=mode,
                    status=DispatchStatus.PENDING,
                    lawyer_id=lawyer_id,
                )

            # A 的抢单池单（未指定律师）
            pool_a = _disp(TENANT_A, case_a, DispatchMode.POOL, None)
            # B 的抢单池单 —— 跨租户攻击目标
            pool_b = _disp(TENANT_B, case_b, DispatchMode.POOL, None)
            # A 的指定派单（指定给律师丙）⇒ 律师甲来接应被拒
            designated_a = _disp(TENANT_A, case_d, DispatchMode.DESIGNATED, lawyer_c.id)
            s.add_all([pool_a, pool_b, designated_a])
            await s.commit()

            return {
                "a": lawyer_a.id,
                "c": lawyer_c.id,
                "b": lawyer_b.id,
                "pool_a": pool_a.id,
                "pool_b": pool_b.id,
                "desig_a": designated_a.id,
                "case_a": case_a.id,
                "case_b": case_b.id,
                "case_d": case_d.id,
            }

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_dispatches(engine, seeded):
    """每个用例前把所有派单复位成 PENDING / 未接，案件复位成 DISPATCHED。

    与 §3.26 的 `_reset_unread` 同理：module 级 `seeded` 省建库开销，
    但接单会**改状态**，不复位的话 P7 跑完 P4/P5/P6 看到的就是「已被处理」的
    409，看起来像「越权被挡住了」——**假绿**。
    """
    from app.models.case import Case, Dispatch
    from app.models.enums import CaseStatus, DispatchStatus

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(__import__("sqlalchemy").update(Dispatch).values(
                status=DispatchStatus.PENDING, lawyer_id=None))
            await s.execute(__import__("sqlalchemy").update(Case).values(
                status=CaseStatus.DISPATCHED, lawyer_id=None))
            await s.commit()
        # 指定派单要保留它的指定律师（否则 P8 的「非指定律师」判据失效）
        async with factory() as s:
            d = await s.get(Dispatch, seeded["desig_a"])
            d.lawyer_id = seeded["c"]
            await s.commit()

    asyncio.run(_run())


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


def _snap(engine, dispatch_id: int, case_id: int):
    """取（派单 status/lawyer, 案件 status/lawyer）快照，用于反向量断言。"""
    from app.models.case import Case, Dispatch

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            d = await s.get(Dispatch, dispatch_id)
            c = await s.get(Case, case_id)
            return (d.status.value, d.lawyer_id, c.status.value, c.lawyer_id)

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.dispatches as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"dispatches.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ P1 装配层冒烟 ═══════════════════════


def test_all_four_routes_respond(app_factory, seeded):
    """P1：4 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get("/api/v1/dispatches").status_code == 200
        assert c.get("/api/v1/dispatches/pool").status_code == 200
        r = c.get("/api/v1/dispatches/999999")
        # 列表类端点不接 id，999999 会走 pool 的路径冲突 ⇒ 断言池子里没有它即可
        assert r.status_code in (200, 404), r.text
        # accept / grab 在 P4/P5/P7 里冒烟
    app2 = app_factory(seeded["a"])
    with _client(app2) as c2:
        assert c2.post(f"/api/v1/dispatches/{seeded['pool_a']}/accept").status_code == 200


# ═══════════════════════ P2/P3 列表与池的租户隔离 ═══════════════════════


def test_list_is_tenant_scoped_both_directions(app_factory, seeded):
    """P2：列表双向租户隔离。"""
    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        ids = {i["id"] for i in _data(c.get("/api/v1/dispatches")).get("items", [])}
        assert seeded["pool_a"] in ids and seeded["desig_a"] in ids
        assert seeded["pool_b"] not in ids

    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        ids = {i["id"] for i in _data(c.get("/api/v1/dispatches")).get("items", [])}
        assert seeded["pool_b"] in ids
        assert seeded["pool_a"] not in ids and seeded["desig_a"] not in ids


def test_pool_contains_only_own_tenant_pending(app_factory, seeded):
    """P3：抢单池只含本租户的 PENDING 且（未指定 / POOL）。"""
    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        ids = {i["id"] for i in _data(c.get("/api/v1/dispatches/pool")).get("items", [])}
        assert seeded["pool_a"] in ids, "本租户的池单没进池 ⇒ 抢单功能废了"
        assert seeded["pool_b"] not in ids, "跨租户的池单进来了"
        assert seeded["desig_a"] not in ids, "指定派单不该出现在抢单池"


# ═══════════════════════ P4/P5 跨租户接单（预期红 ⇒ 坐实缺陷） ═══════════════════════


def test_cross_tenant_accept_is_404(app_factory, seeded):
    """P4：租户 A 的律师接租户 B 的派单 ⇒ **404 `DISPATCH_NOT_FOUND`**。

    不是 403：全局自增 id 下，403 会泄露「这个 id 真实存在」
    （`conversation_access.py` 的同款论证）。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/dispatches/{seeded['pool_b']}/accept")
        assert r.status_code == 404, r.text
        assert _code(r) == "DISPATCH_NOT_FOUND", r.text


def test_cross_tenant_grab_is_404(app_factory, seeded):
    """P5：跨租户 `grab` ⇒ 404。

    `grab` 与 `accept` 是**两个端点**、同一份服务层逻辑；
    只钉 `accept` 的话，修完 `accept` 而漏掉 `grab` 同样全绿。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/dispatches/{seeded['pool_b']}/grab")
        assert r.status_code == 404, r.text
        assert _code(r) == "DISPATCH_NOT_FOUND", r.text


def test_cross_tenant_accept_changes_nothing(app_factory, seeded, engine):
    """P6：**反向量**——跨租户接单不得改动对方的派单与案件。

    只断言 404 是不够的：若实现是「先改数据再判租户」，状态码仍是 404，
    但对方案件的 `lawyer_id` 已经被换成我了（`methodology.md` 76）。
    """
    before = _snap(engine, seeded["pool_b"], seeded["case_b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        c.post(f"/api/v1/dispatches/{seeded['pool_b']}/accept")
        c.post(f"/api/v1/dispatches/{seeded['pool_b']}/grab")
    after = _snap(engine, seeded["pool_b"], seeded["case_b"])
    assert after == before, f"跨租户接单改动了对方数据：{before} → {after}"
    assert after[1] != seeded["a"], "对方的案件被挂到跨租户律师名下了"
    assert after[3] != seeded["a"], "对方案件的 lawyer_id 被改成了跨租户律师"


# ═══════════════════════ P7/P8 同租户正常与规则 ═══════════════════════


def test_same_tenant_accept_works(app_factory, seeded, engine):
    """P7：同租户接单**正常工作**（修完不能把功能焊死）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/dispatches/{seeded['pool_a']}/accept")
        assert r.status_code == 200, r.text
        assert _data(r)["lawyer_id"] == seeded["a"], r.text
        assert _data(r)["status"] == "ACCEPTED", r.text

    status, lawyer, case_status, case_lawyer = _snap(
        engine, seeded["pool_a"], seeded["case_a"])
    assert status == "ACCEPTED" and lawyer == seeded["a"]
    assert case_status == "ACCEPTED" and case_lawyer == seeded["a"]


def test_designated_dispatch_rejects_other_lawyer(app_factory, seeded):
    """P8：指定派单 ⇒ 非指定律师接单被拒（业务规则仍生效）。"""
    app = app_factory(seeded["a"])  # 律师甲，但派单指定的是律师丙
    with _client(app) as c:
        r = c.post(f"/api/v1/dispatches/{seeded['desig_a']}/accept")
        assert r.status_code == 400, r.text
        assert _code(r) == "DISPATCH_RULE_CONFLICT", r.text


# ═══════════════════════ P9 AST 装配层 ═══════════════════════


def test_accept_and_grab_pass_tenant_to_a_guard():
    """P9：接单 / 抢单必须把 `ctx.tenant_id` 交给**某个**归属守卫。

    ⚠️ 带**命中数下限自检**：端点改名会让 `ast.walk` 找不到 ⇒ 退化成空集合
    ⇒ 判据静默通过（`methodology.md` 89）。
    """
    hits = 0
    for name in ("accept_dispatch", "grab_dispatch"):
        seg = _func(name)
        assert "DispatchService(db)" in seg, f"{name} 没走派单服务"
        if "_dispatch_or_404" in seg or "tenant_id=ctx.tenant_id" in seg:
            hits += 1
    assert hits == 2, f"只有 {hits}/2 个接单端点带了租户归属 ⇒ 有端点裸奔"
