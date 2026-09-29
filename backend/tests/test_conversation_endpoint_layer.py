"""会话 4 个路由的**端点层**判据（Q-T 清单第二批）。

## 为什么这个文件必须存在

`GET/POST /api/v1/conversations*` 共 4 条路由，在 `tests/` 下**从未被请求过**。
会话的服务层判据（`test_conversation_access.py`，25 条）非常扎实——但它测的是
`conversation_access.can_access_conversation` 这个**纯函数**。

纯函数测过 ≠ 端点测过。装配层可能出的错一个都测不到：

| 装配层可能出错的地方 | 纯函数测试能否发现 |
|---|---|
| 端点忘了调 `load_accessible_conversation` | ❌ 不能 |
| 调了但把 `ctx.user_id` 写成 `conv.client_user_id` | ❌ 不能 |
| 列表的 `Role.CLIENT` 过滤丢了 | ❌ 不能 |
| `ConversationOut` 字段与 ORM 对不上 ⇒ 500 | ❌ 不能 |

而会话是**客户咨询内容**，受《律师法》保密义务约束——装配层漏一个参数
就是同租户互读（`conversation_access.py` 的模块 docstring 已经论证过：
`POST /auth/register` 是公开自助注册且角色固定为 CLIENT、租户固定为默认租户，
**所有自助注册客户落在同一个租户里**）。

⚠️ 与 §3.26 同样：**先跑判据再下结论**，没有预设「这里有缺陷」。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| C1 | 4 条路由**首次真实 HTTP 请求**：全员非 500（装配层冒烟） |
| C2 | 创建：请求体里的 `tenant_id` **被忽略**（伪造租户落不到 B） |
| C3 | 列表：客户只见自己 / 律师见本租户 / 跨租户为空（三向） |
| C4 | 详情跨租户 ⇒ **404 `CONVERSATION_NOT_FOUND`**，不存在与无权同一个响应 |
| C5 | 详情**同租户他人** ⇒ 404（端点层坐实 `conversation_access` 已接线） |
| C6 | 发消息越权 ⇒ 404，**且一条 Message 都没落库**（反向量） |
| C7 | 律师发言 ⇒ `sender=LAWYER` 不是 CLIENT，且 `reply == ""`（AI 不抢答） |
| C8/C9 | 非法 `channel` / `msg_type` ⇒ **422**（schema 是唯一关口） |
| C10 | AST：端点不接受请求体里的 `tenant_id`；详情与发消息共用同一守卫 |
| C11 | **发现（Q-U）**：客户可伪造 `client_user_id`/`bind_lawyer_id` 归属 |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）：
覆盖掉它就测不到「`tenant_id` 到底从哪来」。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import func, select

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"conv_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A：客户甲 + 客户丁（同租户另一客户）+ 律师乙；租户 B：客户丙。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.conversation import Conversation
    from app.models.enums import ConversationStatus, IMChannel, UserStatus
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

            def _user(tag, role, tid):
                return User(
                    username=f"cep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            client_a = _user("clientA", Role.CLIENT, TENANT_A)
            client_d = _user("clientD", Role.CLIENT, TENANT_A)  # 同租户另一客户
            lawyer_b = _user("lawyerB", Role.LAWYER, TENANT_A)
            client_c = _user("clientC", Role.CLIENT, TENANT_B)  # 跨租户
            s.add_all([client_a, client_d, lawyer_b, client_c])
            await s.flush()

            def _conv(tag, tid, owner):
                return Conversation(
                    tenant_id=tid,
                    external_user_id=f"ext-{tag}-{uuid.uuid4().hex[:6]}",
                    channel=IMChannel.WEB_SIM,
                    status=ConversationStatus.BOT,
                    client_user_id=owner,
                )

            conv_a = _conv("a", TENANT_A, client_a.id)
            conv_d = _conv("d", TENANT_A, client_d.id)
            conv_c = _conv("c", TENANT_B, client_c.id)
            s.add_all([conv_a, conv_d, conv_c])
            await s.commit()

            return {
                "a": client_a.id,
                "d": client_d.id,
                "w": lawyer_b.id,
                "c": client_c.id,
                "conv_a": conv_a.id,
                "conv_d": conv_d.id,
                "conv_c": conv_c.id,
            }

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


def _count_messages(engine, conversation_id: int) -> int:
    from app.models.conversation import Message

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return int((await s.execute(
                select(func.count()).select_from(Message).where(
                    Message.conversation_id == conversation_id)
            )).scalar_one())

    return asyncio.run(_q())


def _src() -> str:
    import app.api.v1.conversations as mod

    return pathlib.Path(mod.__file__).read_text(encoding="utf-8")


def _func(name: str) -> str:
    src = _src()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"conversations.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ C1 装配层冒烟 ═══════════════════════


def test_all_four_routes_respond(app_factory, seeded):
    """C1：4 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r1 = c.get("/api/v1/conversations")
        r2 = c.get(f"/api/v1/conversations/{seeded['conv_a']}")
        r3 = c.get("/api/v1/conversations/999999")
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        # 不存在：必须是 404 而不是 500（守卫抛的是 NotFoundError 不是裸异常）
        assert r3.status_code == 404, r3.text
        assert _code(r3) == "CONVERSATION_NOT_FOUND", r3.text


def test_create_and_send_smoke(app_factory, seeded):
    """C1b：`POST` 两条路由的冒烟（创建 → 发消息，走完整链路）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/conversations", json={"external_user_id": "smoke-1"})
        assert r.status_code == 200, r.text
        cid = _data(r)["id"]
        r2 = c.post(f"/api/v1/conversations/{cid}/messages",
                    json={"text": "你好，我想咨询一下", "msg_type": "text"})
        assert r2.status_code == 200, r2.text
        assert "reply" in _data(r2), r2.text


# ═══════════════════════ C2 伪造租户 ═══════════════════════


def test_create_ignores_forged_tenant_id(app_factory, seeded):
    """C2：请求体里塞 `tenant_id: tenant-b` ⇒ 会话仍落在**调用者所属**租户。

    `ConversationCreate` 里有 `tenant_id: Optional[str] = None` 这个字段，
    但 `create_conversation` 写的是 `tenant_id=ctx.tenant_id`。
    这条判据钉住「schema 有这个字段」≠「端点会用它」——
    哪天有人图省事改成 `payload.tenant_id or ctx.tenant_id`，
    客户就能把会话建进任意租户。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/conversations", json={
            "external_user_id": f"forged-{uuid.uuid4().hex[:6]}",
            "tenant_id": TENANT_B,
        })
        assert r.status_code == 200, r.text
        assert _data(r)["tenant_id"] == TENANT_A, r.text


# ═══════════════════════ C3 列表三向 ═══════════════════════


def test_list_client_sees_only_own(app_factory, seeded):
    """C3a：客户甲只看到自己的会话（看不到同租户客户丁的）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        items = _data(c.get("/api/v1/conversations")).get("items", [])
        ids = {i["id"] for i in items}
        assert seeded["conv_a"] in ids
        assert seeded["conv_d"] not in ids, "同租户他人会话进了列表 ⇒ 角色过滤丢了"
        assert seeded["conv_c"] not in ids


def test_list_lawyer_sees_whole_tenant(app_factory, seeded):
    """C3b：律师看本租户全部（但不能跨租户）。"""
    app = app_factory(seeded["w"])
    with _client(app) as c:
        items = _data(c.get("/api/v1/conversations")).get("items", [])
        ids = {i["id"] for i in items}
        assert seeded["conv_a"] in ids and seeded["conv_d"] in ids
        assert seeded["conv_c"] not in ids, "跨租户会话进了列表 ⇒ 租户底线破了"


def test_list_cross_tenant_is_empty_of_others(app_factory, seeded):
    """C3c：租户 B 的客户只看到自己的。"""
    app = app_factory(seeded["c"])
    with _client(app) as c:
        items = _data(c.get("/api/v1/conversations")).get("items", [])
        ids = {i["id"] for i in items}
        assert seeded["conv_c"] in ids
        assert seeded["conv_a"] not in ids and seeded["conv_d"] not in ids


# ═══════════════════════ C4/C5 详情越权 ═══════════════════════


def test_detail_cross_tenant_is_404_not_403(app_factory, seeded):
    """C4：跨租户 ⇒ 404，且**与不存在的 id 完全同形**（不泄露存在性）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r_cross = c.get(f"/api/v1/conversations/{seeded['conv_c']}")
        r_missing = c.get("/api/v1/conversations/999999")
        assert r_cross.status_code == 404, r_cross.text
        assert r_missing.status_code == 404, r_missing.text
        assert _code(r_cross) == _code(r_missing) == "CONVERSATION_NOT_FOUND"
        assert r_cross.json()["error"]["message"] == r_missing.json()["error"]["message"]


def test_detail_cross_tenant_staff_is_also_404(app_factory, seeded):
    """C4b：跨租户访问必须**用所内身份**再判一次。

    为什么要补这条：C4 用的是客户身份，而客户本来就命中「端用户不得跨用户」
    （判据 4），租户底线（判据 2）对它**是冗余的**——
    实测把租户底线整段删掉，C4 依然全绿。只有所内身份（STAFF_ROLES）
    才会真正压到租户底线这一层：**少了这条，租户底线就没有判据**。
    """
    app = app_factory(seeded["w"])  # 租户 A 的律师
    with _client(app) as c:
        r = c.get(f"/api/v1/conversations/{seeded['conv_c']}")  # 租户 B 的会话
        assert r.status_code == 404, r.text
        assert _code(r) == "CONVERSATION_NOT_FOUND", r.text


def test_detail_same_tenant_other_client_is_404(app_factory, seeded):
    """C5：**同租户**他人会话 ⇒ 404。

    这是 `conversation_access.py` 收敛的历史缺陷（详情曾只校验 `tenant_id`）。
    服务层已经判过纯函数，这里判的是「端点真的把 `ctx.user_id` 传进去了」。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/conversations/{seeded['conv_d']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "CONVERSATION_NOT_FOUND", r.text
        assert "ext-d" not in r.text, "把他人会话内容吐出来了"


# ═══════════════════════ C6 发消息越权（带反向量） ═══════════════════════


def test_send_message_to_others_conversation_is_rejected_and_writes_nothing(
    app_factory, seeded, engine,
):
    """C6：往他人会话发消息 ⇒ 404，**且一条 Message 都没落库**。

    只断言 404 是不够的：如果实现是「先落库再鉴权」，状态码依然是 404，
    但脏数据已经写进对方的消息流了。必须查库确认（`methodology.md` 76）。
    """
    before = _count_messages(engine, seeded["conv_d"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/conversations/{seeded['conv_d']}/messages",
                   json={"text": "我往别人会话里塞一句", "msg_type": "text"})
        assert r.status_code == 404, r.text
    assert _count_messages(engine, seeded["conv_d"]) == before, "越权消息落库了"


# ═══════════════════════ C7 律师发言不被记成客户 ═══════════════════════


def test_lawyer_message_is_recorded_as_lawyer_and_no_ai_reply(app_factory, seeded, engine):
    """C7：律师发言 `sender=LAWYER`，且 `reply` 为空串（AI 不抢答）。"""
    app = app_factory(seeded["w"])
    with _client(app) as c:
        r = c.post(f"/api/v1/conversations/{seeded['conv_a']}/messages",
                   json={"text": "您好，我是承办律师", "msg_type": "text"})
        assert r.status_code == 200, r.text
        assert _data(r)["reply"] == "", "所内人员发言不应触发 AI 应答"

    from app.models.conversation import Message
    from app.models.enums import MessageSender

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            rows = (await s.execute(
                select(Message).where(Message.conversation_id == seeded["conv_a"])
            )).scalars().all()
            return [m.sender for m in rows]

    senders = asyncio.run(_q())
    assert MessageSender.LAWYER in senders, f"律师发言被记成了 {senders}"
    assert MessageSender.CLIENT not in senders, "律师的话被记成客户说的"


# ═══════════════════════ C8/C9 schema 是唯一关口 ═══════════════════════


def test_illegal_channel_is_422(app_factory, seeded):
    """C8：非法 `channel` ⇒ 422。

    `schemas/conversation.py` 的注释讲清了为什么必须在 schema 层拦：
    `channel` 是**直接塞进 ORM** 的，DB 是纯 VARCHAR 无 CHECK 约束，
    一条脏值会让 `select(Conversation)` 在**结果物化阶段**抛 `LookupError`
    ⇒ 该租户的列表接口**永久 500**。schema 是唯一的关口。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/conversations",
                   json={"external_user_id": "bad-channel", "channel": "WEB"})
        assert r.status_code == 422, r.text


def test_illegal_msg_type_is_422(app_factory, seeded):
    """C9：非法 `msg_type` ⇒ 422（同 `channel`，直接进 ORM）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/conversations/{seeded['conv_a']}/messages",
                   json={"text": "x", "msg_type": "txt"})
        assert r.status_code == 422, r.text


# ═══════════════════════ C10 AST 装配层 ═══════════════════════


def test_create_does_not_consume_payload_tenant_id():
    """C10a：`create_conversation` 不得读 `payload.tenant_id`。"""
    seg = _func("create_conversation")
    assert "payload.tenant_id" not in seg, "端点开始吃请求体里的 tenant_id 了"
    assert "ctx.tenant_id" in seg, "租户必须来自 ctx"


def test_detail_and_send_share_the_single_access_guard():
    """C10b：详情与发消息**共用**同一个归属守卫。

    历史缺陷的根源就是这两个端点各写一份判据、且与列表不一致。
    收敛后必须都走 `load_accessible_conversation`。
    """
    for name in ("get_conversation", "send_message"):
        seg = _func(name)
        assert "load_accessible_conversation" in seg, f"{name} 没走唯一归属守卫"
        assert "ctx.user_id" in seg, f"{name} 没把 ctx.user_id 传进守卫"
        assert "ctx.tenant_id" in seg, f"{name} 没把 ctx.tenant_id 传进守卫"


def test_list_filters_by_role_and_tenant():
    """C10c：列表的租户过滤 + 客户角色过滤都在位。"""
    seg = _func("list_conversations")
    assert "Conversation.tenant_id == ctx.tenant_id" in seg
    assert "Role.CLIENT" in seg, "客户只看自己这一条过滤丢了"


# ═══════════════════════ C11 发现：归属可伪造（Q-U） ═══════════════════════


def test_client_cannot_forge_conversation_attribution(app_factory, seeded, engine):
    """C11（Q-U 已裁定）：客户创建会话传 `client_user_id`/`bind_lawyer_id` 必须被忽略。

    Q-U 拍板：归属字段只能由**所内人员**（非 CLIENT 角色）指定；客户传了也按
    「自己 / 不绑定」处理，避免事后不可追溯的归属伪造（所内代建会话确实需要指定）。

    - 客户甲传 `client_user_id=客户丁`、`bind_lawyer_id=律师乙`
      ⇒ 落库后 `client_user_id` 仍是客户甲本人、`bind_lawyer_id` 为 None；
      这条会话归甲，甲自己能读到（不再是孤儿数据）。
    - 所内人员（律师）传 `client_user_id=客户丁` ⇒ 生效（反向量：门禁没过度收紧）。
    """
    from app.models.conversation import Conversation

    def _read(cid):
        async def _q():
            from sqlalchemy.ext.asyncio import async_sessionmaker

            f = async_sessionmaker(engine, expire_on_commit=False)
            async with f() as s:
                return (await s.get(Conversation, cid))

        row = asyncio.run(_q())
        return row.client_user_id, row.bind_lawyer_id

    # 客户侧：伪造被忽略
    app_client = app_factory(seeded["a"])
    with _client(app_client) as c:
        r = c.post("/api/v1/conversations", json={
            "external_user_id": f"forge-{uuid.uuid4().hex[:6]}",
            "client_user_id": seeded["d"],
            "bind_lawyer_id": seeded["w"],
        })
        assert r.status_code == 200, r.text
        forged_id = _data(r)["id"]
    cuid, blid = _read(forged_id)
    assert cuid == seeded["a"], f"客户伪造了 client_user_id：{cuid}"
    assert blid is None, f"客户伪造了 bind_lawyer_id：{blid}"
    # 该会话归客户甲本人，自己能读到（不再是无主孤儿数据）
    with _client(app_client) as c:
        assert c.get(f"/api/v1/conversations/{forged_id}").status_code == 200

    # 所内人员侧：归因生效（反向量）
    app_staff = app_factory(seeded["w"])
    with _client(app_staff) as c:
        r = c.post("/api/v1/conversations", json={
            "external_user_id": f"staff-{uuid.uuid4().hex[:6]}",
            "client_user_id": seeded["d"],
        })
        assert r.status_code == 200, r.text
        cid2 = _data(r)["id"]
    cuid2, _ = _read(cid2)
    assert cuid2 == seeded["d"], f"所内人员指定 client_user_id 未生效：{cuid2}"
