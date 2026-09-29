"""文书 6 条路由的**端点层**判据（Q-T 清单第五批）。

## 为什么这个文件必须存在

`GET/POST /api/v1/documents*` 共 6 条路由，在 `tests/` 下**从未被请求过**——
而且 `grep -rl "documents\\|compliance" tests/` 是**空的**：
文书模块**连服务层都没有测试**（比 §3.28 的派单更彻底：派单至少有
`test_dispatch_rules.py`，文书是一个都没有）。

文书是**客户案件的实体产出**（起诉状、答辩状、律师函…），`content` 里是
填完变量后的完整正文。装配层漏一个参数就是**正文跨租户可读**。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| W1 | 6 条路由**首次真实 HTTP 请求**：全员非 500 |
| W2 | 模板库：本租户 + platform，**不含其他租户** |
| W3 | `start`：落在本租户 |
| **W4** | **跨租户 `collect` ⇒ 404 `DOCUMENT_NOT_FOUND`，且不改对方的 variables** |
| **W5** | **跨租户 `render` ⇒ 404，且不返回对方正文、不记调用方租户的账** |
| W6 | 同租户 `collect` / `render` 正常工作（防把功能焊死） |
| W7 | `GET /{doc_id}` 跨租户 ⇒ 404（这条**本来就有**内联校验，守住不退化） |
| W8 | AST：`collect` / `render` 都过归属守卫（带命中数下限自检） |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
⚠️ `render` 会走 `BillingService.consume`；本文件的 `app_factory` 用真实
`create_app()`，**不 mock 计费**，就是为了连「记错租户的账」一起钉住。
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
    dbfile = base / f"doc_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A / B 各一套模板 + 各一条文书；另有 platform 模板。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.document import Document, DocumentTemplate
    from app.models.enums import DocumentStatus, UserStatus
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
                    username=f"dcep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.LAWYER,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            s.add_all([lawyer_a, lawyer_b])
            await s.flush()

            def _tpl(tid, tag):
                return DocumentTemplate(
                    tenant_id=tid,
                    code=f"tpl-{tag}-{uuid.uuid4().hex[:6]}",
                    name=f"{tag} 的模板",
                    lifecycle="LITIGATION",
                    body="甲方：{{party_a}}；乙方：{{party_b}}。",
                    variables=[
                        {"key": "party_a", "label": "甲方", "required": True},
                        {"key": "party_b", "label": "乙方", "required": True},
                    ],
                )

            tpl_a = _tpl(TENANT_A, "A")
            tpl_b = _tpl(TENANT_B, "B")
            tpl_p = _tpl("platform", "P")
            s.add_all([tpl_a, tpl_b, tpl_p])
            await s.flush()

            def _doc(tid, tpl, tag):
                return Document(
                    tenant_id=tid,
                    template_id=tpl.id,
                    title=f"{tag} 的文书",
                    content="",
                    status=DocumentStatus.COLLECTING,
                    created_by=None,
                )

            doc_a = _doc(TENANT_A, tpl_a, "A")
            doc_b = _doc(TENANT_B, tpl_b, "B")  # 跨租户目标
            s.add_all([doc_a, doc_b])
            await s.commit()

            return {
                "a": lawyer_a.id,
                "b": lawyer_b.id,
                "tpl_a": tpl_a.id,
                "tpl_b": tpl_b.id,
                "tpl_p": tpl_p.id,
                "doc_a": doc_a.id,
                "doc_b": doc_b.id,
            }

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_docs(engine, seeded):
    """每个用例前把两条文书复位成「未收集、未生成」。

    与 §3.26 的 `_reset_unread`、§3.28 的 `_reset_dispatches` 同理：
    `render` 会写 `content` 并把状态推到 `GENERATED`，不复位的话
    W6 跑完 W4/W5 看到的就是「已经生成过」的状态 ⇒ **假绿**。
    """
    from sqlalchemy import update as _upd

    from app.models.document import Document
    from app.models.enums import DocumentStatus

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(_upd(Document).values(
                content="", variables=None, missing_variables=None,
                status=DocumentStatus.COLLECTING))
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


def _doc_snap(engine, doc_id: int):
    from app.models.document import Document

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            d = await s.get(Document, doc_id)
            return (d.status.value, d.content, dict(d.variables or {}))

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.documents as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"documents.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ W1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """W1：6 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get("/api/v1/documents/templates").status_code == 200
        r = c.post("/api/v1/documents/start", json={"template_id": seeded["tpl_a"]})
        assert r.status_code == 200, r.text
        new_id = _data(r)["id"]
        assert c.get(f"/api/v1/documents/{new_id}").status_code == 200
        r = c.post(f"/api/v1/documents/{new_id}/collect",
                   json={"variables": {"party_a": "甲公司", "party_b": "乙公司"}})
        assert r.status_code == 200, r.text
        r = c.post(f"/api/v1/documents/{new_id}/render")
        assert r.status_code == 200, r.text
    # contract-review 走输入审核 + 模型调用，只在这里冒烟一次
    app2 = app_factory(seeded["a"])
    with _client(app2) as c2:
        r = c2.post("/api/v1/documents/contract-review",
                    json={"title": "审查测试", "source_text": "甲方与乙方约定如下。"})
        assert r.status_code == 200, r.text


# ═══════════════════════ W2 模板库可见范围 ═══════════════════════


def test_templates_include_own_and_platform_only(app_factory, seeded):
    """W2：模板库 = 本租户 + platform，**不含其他租户**。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        ids = {t["id"] for t in _data(c.get("/api/v1/documents/templates")) or []}
        assert seeded["tpl_a"] in ids, "本租户模板没出现"
        assert seeded["tpl_p"] in ids, "platform 模板没出现"
        assert seeded["tpl_b"] not in ids, "其他租户的模板泄漏了"


# ═══════════════════════ W3 start 落在本租户 ═══════════════════════


def test_start_lands_in_callers_tenant(app_factory, seeded):
    """W3：`start` 落在本租户 ⇒ **租户 B 看不见它**。

    `DocumentOut` 里没有 `tenant_id` 字段，没法直接断言归属；
    改用「跨租户读不到」间接钉住——这也是更有意义的判据
    （直接比对字段值只能证明写对了，证明不了**别人读不到**）。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/documents/start", json={"template_id": seeded["tpl_a"]})
        assert r.status_code == 200, r.text
        new_id = _data(r)["id"]
        assert c.get(f"/api/v1/documents/{new_id}").status_code == 200

    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        r = c.get(f"/api/v1/documents/{new_id}")
        assert r.status_code == 404, "租户 A 建的文书被租户 B 读到了"
        assert _code(r) == "DOCUMENT_NOT_FOUND", r.text


# ═══════════════════════ W4/W5 跨租户 collect / render ═══════════════════════


def test_cross_tenant_collect_is_404_and_writes_nothing(app_factory, seeded, engine):
    """W4：跨租户 `collect` ⇒ 404，**且对方的 variables 一字未改**。"""
    before = _doc_snap(engine, seeded["doc_b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/documents/{seeded['doc_b']}/collect",
                   json={"variables": {"party_a": "我塞进去的"}})
        assert r.status_code == 404, r.text
        assert _code(r) == "DOCUMENT_NOT_FOUND", r.text
    assert _doc_snap(engine, seeded["doc_b"]) == before, "跨租户改写了对方的文书变量"


def test_cross_tenant_render_is_404_and_leaks_nothing(app_factory, seeded, engine):
    """W5：跨租户 `render` ⇒ 404，**正文不外泄、且不记调用方租户的账**。

    这条最要紧：`DocumentOut.content` 是**渲染后的完整正文**，
    `render_document` 还会用 `ctx.tenant_id` 调 `BillingService.consume`
    ⇒ 跨租户 render 同时是**读越权**和**记错账**。
    """
    before = _doc_snap(engine, seeded["doc_b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/documents/{seeded['doc_b']}/render")
        assert r.status_code == 404, r.text
        assert _code(r) == "DOCUMENT_NOT_FOUND", r.text
        assert "乙方" not in r.text and "甲公司" not in r.text, "把对方正文吐出来了"
    assert _doc_snap(engine, seeded["doc_b"]) == before, "跨租户把对方文书渲染并落库了"


def test_cross_tenant_render_does_not_bill_caller_tenant(app_factory, seeded, engine):
    """W5b：跨租户 render **不得**在调用方租户名下产生计费流水。"""
    from app.models.billing import UsageRecord

    async def _count(tid: str) -> int:
        from sqlalchemy import func as _f
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return int((await s.execute(
                select(_f.count()).select_from(UsageRecord)
                .where(UsageRecord.tenant_id == tid))).scalar_one())

    before = asyncio.run(_count(TENANT_A))
    app = app_factory(seeded["a"])
    with _client(app) as c:
        c.post(f"/api/v1/documents/{seeded['doc_b']}/render")
    assert asyncio.run(_count(TENANT_A)) == before, "跨租户 render 记了调用方租户的账"


# ═══════════════════════ W6 同租户正常路径 ═══════════════════════


def test_same_tenant_collect_and_render_work(app_factory, seeded, engine):
    """W6：同租户 `collect` → `render` 正常出正文（防把功能焊死）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/documents/{seeded['doc_a']}/collect",
                   json={"variables": {"party_a": "甲公司", "party_b": "乙公司"}})
        assert r.status_code == 200, r.text
        assert _data(r)["status"] == "DRAFT", r.text  # 必填都齐了

        r = c.post(f"/api/v1/documents/{seeded['doc_a']}/render")
        assert r.status_code == 200, r.text
        assert "甲公司" in (_data(r)["content"] or ""), r.text
        assert _data(r)["status"] == "GENERATED", r.text


# ═══════════════════════ W7 详情已有校验不退化 ═══════════════════════


def test_get_document_cross_tenant_is_404(app_factory, seeded):
    """W7：`GET /{doc_id}` 跨租户 ⇒ 404（这条本来就有内联校验，守住）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/documents/{seeded['doc_b']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "DOCUMENT_NOT_FOUND", r.text


# ═══════════════════════ W8 AST 装配层 ═══════════════════════


def test_collect_and_render_pass_tenant_to_a_guard():
    """W8：`collect` / `render` 必须把 `ctx.tenant_id` 交给归属守卫。

    ⚠️ 带**命中数下限自检**：端点改名会让 `ast.walk` 找不到 ⇒ 退化成空集合
    ⇒ 判据静默通过（`methodology.md` 89）。
    """
    hits = 0
    for name in ("collect_variables", "render_document"):
        seg = _func(name)
        assert "DocumentService(db)" in seg, f"{name} 没走文书服务"
        if "_document_or_404" in seg or "tenant_id=ctx.tenant_id" in seg:
            hits += 1
    assert hits == 2, f"只有 {hits}/2 个端点带了租户归属 ⇒ 有端点裸奔"
