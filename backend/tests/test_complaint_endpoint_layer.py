"""投诉举报 4 条零覆盖路由的**端点层**判据（Q-T 清单第八批 · §3.32.2）。

## 为什么这个文件必须存在

`GET /complaints/policy`、`GET /complaints/stats`、
`POST /complaints/{id}/handle`、`GET /complaints/{ticket_no}` 这 4 条路由
在 `tests/` 下**从未被请求过**。

这一批的风险性质与前面都不一样——它是**外部公众可触达**的模块：

- `POST /complaints`（提交）与 `GET /complaints/{ticket_no}`（回查）**无鉴权**，
  这是《暂行办法》第十五条「便捷入口」的**有意设计**，不是缺口；
- 代价是：**工单号就是唯一凭证**。所以「工单号不可枚举」与
  「公开视图不泄露内部字段」这两条，是这个模块的**承重墙**。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| T1 | 4 条路由**首次真实 HTTP 请求**：全员非 500 |
| T2 | `/stats` 与列表需平台管理员 ⇒ 非管理员 403（**逐条**验） |
| T3 | `/policy` 公开，且公布时限与流程（第十五条要求） |
| T4 | `GET /{ticket_no}` 公开可查（正向：凭号能查到） |
| **T5** | **公开视图不泄露内部字段**（contact / reporter_id / ip / handler_id / tenant_id） |
| **T6** | **工单号不可枚举**：两次提交不同号；瞎猜 ⇒ 404 且**不泄露存在性差异** |
| T7 | `handle` 未知 status ⇒ 4xx + **状态不变**（反向量） |
| T8 | `handle` 办结/不予受理**必须填结论**（<5 字 ⇒ 4xx + 状态不变） |
| T9 | `handle` 正常办结 ⇒ 200，status/handled_at/feedback_sent 更新 + **写审计** |
| T10 | AST：3 条管理端点都有 `require_roles(PLATFORM_ADMIN)` |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
⚠️ T6 不断言「必须用 secrets」——那太实现细节了。断言的是**行为**：
   两次不同、且猜不中。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import func, select

TENANT_A = "tenant-a"


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
    """平台管理员 1 名 + 普通客户 1 名；预置 2 条投诉工单。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.complaint import Complaint, ComplaintStatus
    from app.models.enums import UserStatus
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in ("platform", TENANT_A):
                exists = (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid))).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid, role):
                return User(
                    username=f"cep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            admin = _user("admin", "platform", Role.PLATFORM_ADMIN)
            client = _user("client", TENANT_A, Role.CLIENT)
            s.add_all([admin, client])
            await s.flush()

            def _row(tag):
                return Complaint(
                    tenant_id=TENANT_A,
                    ticket_no=f"AI20260101{tag}",
                    type="OTHER",
                    status=ComplaintStatus.PENDING,
                    description=f"{tag} 的投诉内容",
                    contact=f"contact-{tag}@example.com",
                    reporter_id=client.id,
                    ip_address="203.0.113.9",
                )

            c1 = _row("AAAAAA")
            c2 = _row("BBBBBB")
            s.add_all([c1, c2])
            await s.commit()
            return {
                "admin": admin.id,
                "client": client.id,
                "c1": c1.id,
                "c2": c2.id,
                "t1": c1.ticket_no,
                "t2": c2.ticket_no,
            }

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_complaints(engine, seeded):
    """每个用例前把两条工单复位成 PENDING / 未处理。

    `handle` 会改状态并写 `handled_at`，不复位的话 T7/T8 跑在 T9 之后
    看到的就是「已办结」的状态 ⇒ 「拒绝」与「没轮到」分不开。
    """
    from sqlalchemy import delete
    from sqlalchemy import update as _upd

    from app.models.audit_log import AuditLog
    from app.models.complaint import Complaint, ComplaintStatus

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            # T9 要断言「处置后恰好 1 条审计」，不清理的话 T1 留下的那条会让它假红
            await s.execute(delete(AuditLog))
            await s.flush()
            await s.execute(_upd(Complaint).values(
                status=ComplaintStatus.PENDING,
                handle_note=None, handled_at=None,
                handler_id=None, feedback_sent=0))
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


def _audit_count(engine, action: str):
    from app.models.audit_log import AuditLog

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return (await s.execute(
                select(func.count()).select_from(AuditLog).where(
                    AuditLog.action == action))).scalar()

    return asyncio.run(_q())


def _snap(engine, cid: int):
    from app.models.complaint import Complaint

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            r = await s.get(Complaint, cid)
            st = r.status
            return (st.value if hasattr(st, "value") else str(st),
                    r.handle_note, r.handled_at is not None, bool(r.feedback_sent))

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.complaints as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"complaints.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


ADMIN_ROUTES_GET = ("/api/v1/complaints/stats", "/api/v1/complaints")


# ═══════════════════════ T1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """T1：4 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        assert c.get("/api/v1/complaints/policy").status_code == 200
        assert c.get("/api/v1/complaints/stats").status_code == 200
        r = c.get(f"/api/v1/complaints/{seeded['t1']}")
        assert r.status_code == 200, r.text
        r = c.post(f"/api/v1/complaints/{seeded['c1']}/handle",
                   json={"status": "PROCESSING", "handle_note": ""})
        assert r.status_code == 200, r.text


# ═══════════════════════ T2 角色门控 ═══════════════════════


def test_non_admin_is_403_on_admin_routes(app_factory, seeded):
    """T2：`/stats` 与列表需平台管理员 ⇒ **逐条**验（门控按路由挂）。"""
    app = app_factory(seeded["client"])
    with _client(app) as c:
        for path in ADMIN_ROUTES_GET:
            r = c.get(path)
            assert r.status_code == 403, f"{path} ⇒ {r.status_code}，{r.text}"
        r = c.post(f"/api/v1/complaints/{seeded['c1']}/handle",
                   json={"status": "RESOLVED", "handle_note": "已处理完毕，谢谢反馈"})
        assert r.status_code == 403, r.text


# ═══════════════════════ T3 政策公开 ═══════════════════════


def test_policy_is_public_and_publishes_due_days(app_factory, seeded):
    """T3：`/policy` 公开（第十五条「公布处理流程和反馈时限」）。"""
    app = app_factory(seeded["client"])
    with _client(app) as c:
        r = c.get("/api/v1/complaints/policy")
        assert r.status_code == 200, r.text
        d = _data(r)
        assert isinstance(d["due_days"], int) and d["due_days"] > 0, d
        assert len(d["flow"]) >= 3, d


# ═══════════════════════ T4 / T5 公开回查 ═══════════════════════


def test_ticket_lookup_is_public(app_factory, seeded):
    """T4：凭工单号可回查（**不要求登录**——第十五条「便捷入口」）。"""
    app = app_factory(seeded["client"])
    with _client(app) as c:
        r = c.get(f"/api/v1/complaints/{seeded['t1']}")
        assert r.status_code == 200, r.text
        assert _data(r)["ticket_no"] == seeded["t1"], r.text


def test_public_view_leaks_no_internal_fields(app_factory, seeded):
    """T5：**承重墙**。公开视图**不得**包含内部字段。

    提交入口是匿名的，回查也是匿名的 ⇒ 工单号是唯一凭证。
    一旦公开视图把 `contact` / `reporter_id` / `ip_address` / `handler_id`
    带出去，就等于**凭一个工单号可以取到投诉人的联系方式与 IP**。
    """
    app = app_factory(seeded["client"])
    with _client(app) as c:
        r = c.get(f"/api/v1/complaints/{seeded['t1']}")
        assert r.status_code == 200, r.text
    leaked = [k for k in ("contact", "reporter_id", "ip_address",
                          "handler_id", "tenant_id", "related_moderation_id")
              if k in r.text]
    assert not leaked, f"公开视图泄露了内部字段：{leaked}"
    # 内容本身要能看到（否则这条判据可能只是「整个响应是空的」）
    assert "AAAAAA 的投诉内容" in r.text, "公开视图连投诉内容都没返回"


# ═══════════════════════ T6 工单号不可枚举 ═══════════════════════


def test_ticket_numbers_are_not_guessable(app_factory, seeded):
    """T6：工单号不可枚举 —— 两次提交不同号，且**猜不出**第三条的号。

    `POST /complaints` 本身已有覆盖，这里只把它当**造号工具**用：
    重点是「凭号可查」这个设计成立的前提是「号不可猜」。
    """
    app = app_factory(seeded["client"])
    with _client(app) as c:
        # 描述有「不少于 5 个字」的下限，别写太短
        r1 = c.post("/api/v1/complaints",
                    json={"type": "OTHER", "description": "第一次投诉的具体问题描述"})
        r2 = c.post("/api/v1/complaints",
                    json={"type": "OTHER", "description": "第二次投诉的具体问题描述"})
        assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
        n1, n2 = _data(r1)["ticket_no"], _data(r2)["ticket_no"]
        assert n1 != n2, "两次提交的工单号相同"

        # 拿一个明显靠猜的号（同一天 + 全 0 尾号）⇒ 必须是 404
        guess = n1[:10] + "000000"
        bad = c.get(f"/api/v1/complaints/{guess}")
        assert bad.status_code == 404, f"猜号竟然命中：{bad.status_code} {bad.text}"

        # 存在性不得从响应差异里泄露：猜错与查对**只差** 404/200，
        # 错误信息不许包含任何投诉内容
        assert "AAAAAA 的投诉内容" not in bad.text


def test_unknown_ticket_is_404_without_hint(app_factory, seeded):
    """T6b：不存在的工单 ⇒ 404，且错误信息里**不含**任何一条真实工单的内容。"""
    app = app_factory(seeded["client"])
    with _client(app) as c:
        r = c.get("/api/v1/complaints/AI20990101ZZZZZZ")
        assert r.status_code == 404, r.text
        assert "AAAAAA 的投诉内容" not in r.text
        assert "BBBBBB 的投诉内容" not in r.text


# ═══════════════════════ T7 / T8 handle 前置校验 ═══════════════════════


def test_handle_rejects_unknown_status(app_factory, seeded, engine):
    """T7：未知 status ⇒ 4xx，且**状态不变**（反向量）。"""
    before = _snap(engine, seeded["c1"])
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post(f"/api/v1/complaints/{seeded['c1']}/handle",
                   json={"status": "NOT_A_STATUS", "handle_note": "随便写点"})
        assert 400 <= r.status_code < 500, f"未知状态被接受：{r.status_code} {r.text}"
    assert _snap(engine, seeded["c1"]) == before, "被拒绝的 handle 改动了工单"


def test_handle_requires_conclusion_when_closing(app_factory, seeded, engine):
    """T8：办结 / 不予受理**必须**填处理结论（<5 字 ⇒ 拒绝）。

    这条压的是「投诉人必须得到答复」这个**业务底线**——
    它跟「状态合法性校验」是两层，都要有判据（只测前者时删掉后者照样全绿）。
    """
    before = _snap(engine, seeded["c2"])
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        for status in ("RESOLVED", "REJECTED"):
            r = c.post(f"/api/v1/complaints/{seeded['c2']}/handle",
                       json={"status": status, "handle_note": "太短"})
            assert 400 <= r.status_code < 500, f"{status} 缺结论却被接受：{r.status_code} {r.text}"
    assert _snap(engine, seeded["c2"]) == before, "缺结论的 handle 改动了工单"


# ═══════════════════════ T9 handle 正向 + 留痕 ═══════════════════════


def test_handle_closes_and_writes_audit(app_factory, seeded, engine):
    """T9：正常办结 ⇒ 状态更新 + `handled_at` / `feedback_sent` 落位 + **写审计**。

    「写审计」这一段与业务结果无关（不写也照样 200）⇒ 只有直接查
    `audit_logs` 才测得到，和 §3.30 的 V9 是同一类「静默要求」。
    """
    from app.core.audit import AuditAction

    assert _audit_count(engine, AuditAction.COMPLAINT_HANDLE) == 0
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post(f"/api/v1/complaints/{seeded['c1']}/handle",
                   json={"status": "RESOLVED", "handle_note": "已核实并处理完毕，感谢反馈"})
        assert r.status_code == 200, r.text
        assert _data(r)["status"] == "RESOLVED", r.text

    status, note, handled, feedback = _snap(engine, seeded["c1"])
    assert status == "RESOLVED", status
    assert note and len(note) >= 5, note
    assert handled is True, "办结了却没有 handled_at"
    assert feedback is True, "办结了却没标 feedback_sent（投诉人得不到答复）"
    assert _audit_count(engine, AuditAction.COMPLAINT_HANDLE) == 1, "处置投诉没留审计"


# ═══════════════════════ T10 AST 装配判据 ═══════════════════════


def test_admin_endpoints_have_role_gate():
    """T10：3 条管理端点都必须有平台管理员门控（带命中下限自检）。"""
    for name in ("complaint_stats", "list_complaints", "handle_complaint"):
        src = _func(name)
        assert "require_roles(Role.PLATFORM_ADMIN)" in src, f"{name} 没有平台管理员门控"
