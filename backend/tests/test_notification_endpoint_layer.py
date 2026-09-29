"""通知 6 个路由的**端点层**判据（Q-T 清单第一批）。

## 为什么这个文件必须存在

`GET/POST /api/v1/notifications*` 共 6 条路由，在 `tests/` 下**从未被请求过**。
而通知的**服务层**有 4 个测试文件、约 100 KB
（`test_notification_api.py` / `_producers` / `_push` / `_since_id`）——
`test_notification_api.py` 里 `client.get()` 的命中数是 **0**，它测的是服务层。

⇒ 典型的「零件测过、装配没测」（`methodology.md` 89）。服务层再扎实，也证明不了
「端点真的把 `ctx.user_id` 传进去了」——而通知是**用户级**资源，这一个参数传错
就是同律所内互读（含案件标题、客户信息、复核结论）。

⚠️ 与 §3.19 / §3.25 不同：本轮**先跑判据再下结论**，没有预设「这里有缺陷」。
如果 6 条端点都是绿的，那也是一条信息——它把「端点层真的通」从推断变成实测。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| N1 | 6 条路由**首次真实 HTTP 请求**：全员 200/404 而非 500（装配层冒烟） |
| N2 | 列表：只返回自己的（同租户另一用户看不到） |
| N3 | 列表：跨租户看不到（双向） |
| N4 | 未读数：只算自己的 |
| N5 | 详情越权 ⇒ **404 `NOTIFICATION_NOT_FOUND`**，不是 403（不泄露存在性） |
| N6 | `read-one` **幂等**：重复调用 `read_at` 不刷新、`unread_total` 不漂移 |
| N7 | `read-all`：自己清干净，**别人的一条都没动**（反向量：防把全表清了） |
| N8 | `_parse_type` 未知类型 ⇒ **400**（不做「静默返回空列表」） |
| N9 | AST：端点**不接受** `user_id` / `tenant_id` 请求参数，一律取自 ctx |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）：
覆盖掉它就测不到「`user_id` 到底从哪来」。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

_ITEM_KEYS = ("items",)


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"notif_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A：客户甲 + 律师乙；租户 B：客户丙。每人各 2 条通知。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import NotificationType, UserStatus
    from app.models.identity import Tenant, User
    from app.services import notification_service as svc

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

            def _user(tag, role, tid):
                return User(
                    username=f"nep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            client_a = _user("clientA", Role.CLIENT, TENANT_A)
            lawyer_b = _user("lawyerB", Role.LAWYER, TENANT_A)  # 同租户另一人
            client_c = _user("clientC", Role.CLIENT, TENANT_B)  # 跨租户
            s.add_all([client_a, lawyer_b, client_c])
            await s.flush()

            ids: dict[str, list[int]] = {}
            for tag, u, tid in (
                ("a", client_a, TENANT_A),
                ("b", lawyer_b, TENANT_A),
                ("c", client_c, TENANT_B),
            ):
                got = []
                for i in range(2):
                    n = await svc.notify(
                        s,
                        tenant_id=tid,
                        user_id=u.id,
                        type=NotificationType.REVIEW_REQUIRED,
                        content=f"{tag} 的第 {i + 1} 条",
                    )
                    assert n is not None, "notify 返回 None ⇒ 夹具没铺上数据"
                    got.append(n.id)
                ids[tag] = got
            await s.commit()

            return {
                "a": client_a.id,
                "b": lawyer_b.id,
                "c": client_c.id,
                "ids": ids,
            }

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_unread(engine, seeded):
    """每个用例前把所有通知复位成未读。

    ⚠️ 不做复位就踩到**用例间顺序依赖**：`seeded` 是 module 级（建库开销大），
    N7 的「一键已读」会把客户甲清空 ⇒ 排在它后面的 N7c 拿到 `updated=0`，
    看起来像「批量接口不工作」。换随机顺序又会变假绿。
    与本仓库 `test_evidence_upload_scope.py` 那条「预置证据」是同一个道理。
    """
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.notification import Notification

    async def _run() -> None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(update(Notification).values(is_read=0, read_at=None))
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


def _unread(engine, user_id: int) -> int:
    import asyncio as _a

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.models.notification import Notification

    async def _q():
        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            rows = (await s.execute(
                select(Notification).where(
                    Notification.user_id == user_id, Notification.is_read == 0
                )
            )).scalars().all()
            return len(rows)

    return _a.run(_q())


# ═══════════════════════ N1 装配层冒烟 ═══════════════════════


def test_all_six_routes_respond(app_factory, seeded):
    """N1：6 条路由**第一次**被真实请求——必须都不是 500。

    这是本文件最朴素也最要紧的一条：此前这些端点**从未被执行过**，
    一次 500（schema 字段不匹配 / 参数名写错 / 依赖没接上）都不会有人发现。
    """
    app = app_factory(seeded["a"])
    own = seeded["ids"]["a"][0]
    with _client(app) as c:
        cases = [
            ("get", "/api/v1/notifications", 200),
            ("get", "/api/v1/notifications/unread-count", 200),
            ("get", f"/api/v1/notifications/{own}", 200),
            ("post", f"/api/v1/notifications/{own}/read", 200),
            ("post", "/api/v1/notifications/read", 200),
            ("post", "/api/v1/notifications/read-all", 200),
        ]
        for verb, path, want in cases:
            kwargs = {"json": {}} if verb == "post" else {}
            resp = getattr(c, verb)(path, **kwargs)
            assert resp.status_code == want, f"{verb.upper()} {path} ⇒ {resp.status_code}: {resp.text[:200]}"
            assert resp.json().get("success") is True


# ═══════════════════════ N2–N4 读隔离 ═══════════════════════


def test_list_returns_only_own_notifications(app_factory, seeded):
    """N2：列表只返回自己的——同租户律师乙的通知一条都不该出现。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.get("/api/v1/notifications?page_size=50")
    assert resp.status_code == 200
    items = _data(resp)["items"]
    assert {i["id"] for i in items} == set(seeded["ids"]["a"])
    for i in items:
        assert "a 的第" in i["content"], i


def test_list_excludes_other_tenant(app_factory, seeded):
    """N3：跨租户看不到（租户 B 的客户丙）。

    与 §3.19 / §3.25 同源：只按 `user_id` 过滤会让跨租户数据串进来，
    只按 `tenant_id` 过滤会让同租户互读——两个条件必须**同时**在 WHERE 里。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.get("/api/v1/notifications?page_size=50")
    ids = {i["id"] for i in _data(resp)["items"]}
    assert not ids & set(seeded["ids"]["c"])


def test_unread_count_only_counts_own(app_factory, seeded, engine):
    """N4：未读数只算自己的（2 条，不是 6 条）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.get("/api/v1/notifications/unread-count")
    assert resp.status_code == 200
    data = _data(resp)
    assert data["total"] == 2, data
    assert data["by_type"]["REVIEW_REQUIRED"] == 2, data


# ═══════════════════════ N5 越权 ═══════════════════════


def test_detail_of_others_notification_is_404_not_403(app_factory, seeded):
    """N5：读他人通知 ⇒ **404** `NOTIFICATION_NOT_FOUND`。

    403 会告诉攻击者「这个 id 存在，只是你没权限」，可用来枚举系统内通知总量
    与他人活跃度。模块 docstring 明确选了 404，这里把它钉住。
    """
    other = seeded["ids"]["b"][0]
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.get(f"/api/v1/notifications/{other}")
    assert resp.status_code == 404, resp.text
    assert _code(resp) == "NOTIFICATION_NOT_FOUND", resp.text


def test_read_one_of_others_notification_is_404(app_factory, seeded, engine):
    """N5b：标记他人已读 ⇒ 404，且**对方的未读数不变**（不能只回错码不改数据）。"""
    other = seeded["ids"]["b"][0]
    before = _unread(engine, seeded["b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.post(f"/api/v1/notifications/{other}/read")
    assert resp.status_code == 404, resp.text
    assert _unread(engine, seeded["b"]) == before


# ═══════════════════════ N6 幂等 ═══════════════════════


def test_read_one_is_idempotent(app_factory, seeded, engine):
    """N6：重复标记已读 ⇒ 200，`read_at` **不被刷新**，`unread_total` 不漂移。

    幂等不是锦上添花：前端重试 / 用户连点 / 离线补传都会重复触发。
    `read_at` 被刷新的话，「用户什么时候第一次看到」这个事实就被抹掉了。
    """
    own = seeded["ids"]["a"][0]
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r1 = c.post(f"/api/v1/notifications/{own}/read")
        first_at = _data(r1)["notification"]["read_at"]
        first_unread = _data(r1)["unread_total"]
        r2 = c.post(f"/api/v1/notifications/{own}/read")
    assert r1.status_code == r2.status_code == 200
    assert _data(r2)["notification"]["read_at"] == first_at
    assert _data(r2)["unread_total"] == first_unread
    assert _unread(engine, seeded["a"]) == 1  # 2 条里只清了 1 条


# ═══════════════════════ N7 批量 / 全部已读 ═══════════════════════


def test_read_all_clears_only_own(app_factory, seeded, engine):
    """N7：一键已读 ⇒ 自己清干净，**同租户律师乙一条都没动**。

    反向量很关键：只测「自己的清了」，那么一个把 WHERE 里 `user_id` 条件
    写漏的实现（清空全租户）也能全绿——那正是本模块 docstring 里点名的失效模式。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.post("/api/v1/notifications/read-all")
    assert resp.status_code == 200, resp.text
    assert _data(resp)["unread_total"] == 0
    assert _unread(engine, seeded["a"]) == 0
    # 别人的：同租户 + 跨租户都不得受影响
    assert _unread(engine, seeded["b"]) == 2
    assert _unread(engine, seeded["c"]) == 2


def test_read_batch_empty_ids_equals_read_all(app_factory, seeded, engine):
    """N7b：空 `ids` ⇒ 全部已读（与 `read-all` 等价，模块 docstring 的承诺）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.post("/api/v1/notifications/read", json={})
    assert resp.status_code == 200, resp.text
    assert _data(resp)["unread_total"] == 0
    assert _unread(engine, seeded["b"]) == 2


def test_read_batch_skips_foreign_ids_silently(app_factory, seeded, engine):
    """N7c：批量里混入他人 id ⇒ 静默跳过，不报错，且**对方未读数不变**。

    模块 docstring 的取舍：批量操作里只要有一个越权 id 就让整批失败，
    会把「前端缓存了过期 id」这类正常情况变成用户可见的报错。
    但「静默」绝不能变成「照样改」——所以断言对方的未读数。
    """
    mixed = [seeded["ids"]["a"][0], seeded["ids"]["b"][0]]
    app = app_factory(seeded["a"])
    with _client(app) as c:
        resp = c.post("/api/v1/notifications/read", json={"ids": mixed})
    assert resp.status_code == 200, resp.text
    assert _data(resp)["updated"] == 1, "只应更新属于自己的那一条"
    assert _unread(engine, seeded["b"]) == 2


# ═══════════════════════ N8 类型参数 ═══════════════════════


def test_unknown_type_returns_400(app_factory, seeded):
    """N8：未知通知类型 ⇒ **400**，不是「静默返回空列表」。

    `REVIEW_REQUIRED` 拼成 `REVIEW_REQUIRE` 若静默返回空，前端会当成
    「没有这类通知」，排查成本极高（`_parse_type` 的 docstring 就是这个理由）。
    反向量：合法类型必须 200。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        bad = c.get("/api/v1/notifications?type=REVIEW_REQUIRE")
        good = c.get("/api/v1/notifications?type=REVIEW_REQUIRED")
    assert bad.status_code == 400, bad.text
    assert _code(bad) == "VALIDATION_ERROR", bad.text
    assert good.status_code == 200, good.text


# ═══════════════════════ N9 AST ═══════════════════════


def test_endpoints_never_accept_identity_from_request():
    """N9：6 个端点都不得接受 `user_id` / `tenant_id` 作为请求参数。

    模块 docstring 第 3 条：「`user_id` 一律取自 `get_tenant_context`，不接受任何
    请求参数传入——否则等于开放了『按 user_id 读任意人通知』的后门」。

    ⚠️ 带**命中数下界自检**（`methodology.md` 80）：端点数不足 6 就直接失败，
    否则 AST 判据会静默退化成空集并报通过。
    """
    import ast

    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "app"
        / "api"
        / "v1"
        / "notifications.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(src)

    endpoints = [
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef)
        and any(
            isinstance(d, ast.Call) and getattr(d.func, "attr", "") in
            {"get", "post", "put", "patch", "delete"}
            for d in n.decorator_list
        )
    ]
    assert len(endpoints) == 6, f"端点数 {len(endpoints)} ≠ 6，判据已失效"

    for fn in endpoints:
        names = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        assert "user_id" not in names, f"{fn.name} 接受 user_id 请求参数"
        assert "tenant_id" not in names, f"{fn.name} 接受 tenant_id 请求参数"
        seg = ast.get_source_segment(src, fn) or ""
        assert "ctx.user_id" in seg, f"{fn.name} 没有用 ctx.user_id"
        assert "ctx.tenant_id" in seg, f"{fn.name} 没有用 ctx.tenant_id"
