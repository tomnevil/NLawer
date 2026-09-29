"""复核 9 个路由的**端点层**判据（Q-T 清单第二批）。

## 为什么这个文件必须存在

`reviews.py` 的注释自陈了一段历史缺陷：`edit` / `decide` / `archive` / `void`
四个写端点**仅依赖 `get_current_user`，未做租户校验**，任何登录用户可凭
`review_id` 篡改或定稿他人租户的复核内容。修复收口在 `_review_or_404()`。

`tests/test_review_tenant_guard.py` 已经为它写了 5 条判据（G1–G5），
`test_review_fsm.py` 测了状态机白名单。**但路由覆盖扫描显示仍有 7 条路由
从未被请求过**（`ensure` / `records` / `submit` / `decide` / `archive` / `void` / 列表）。

零件齐了、装配没测。装配层可能出的错：

| 装配层可能出错的地方 | 纯函数/服务层测试能否发现 |
|---|---|
| `ensure` 没把 `ctx.tenant_id` 传下去 | ❌ 不能（`ensure` 的租户参数来自调用方） |
| 写端点忘了调 `_review_or_404` | ❌ 不能（G5 是 AST，但只覆盖 4 个中的 4 个签名，不含 `ensure`） |
| 走了守卫但 `record()` 用了 `user.tenant_id` | ❌ 不能 |
| `ReviewOut` 与 ORM 对不上 ⇒ 500 | ❌ 不能 |

⚠️ 与 §3.26 同样：**先跑判据再下结论**，没有预设「这里有缺陷」。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| R1 | 7 条零覆盖路由**首次真实 HTTP 请求**：全员非 500 |
| R2 | 列表：租户隔离（双向） |
| R3 | `ensure` **幂等**且**按租户隔离**（同 target 跨租户不复用） |
| R4 | `ensure` 非法 `target_type` ⇒ 4xx **且不落库**（反向量） |
| R5 | 跨租户写（`decide`）⇒ 404 `REVIEW_NOT_FOUND`，**且对方状态未变** |
| R6 | 完整链路走端点：`ensure → submit → decide → archive`，逐状态断言 |
| R7 | `records`：留痕按动作落库，`actor_id` 正确 |
| R8 | 非法流转（DRAFT 直接 archive）⇒ 4xx `REVIEW_TRANSITION_DENIED`，状态未变 |
| R9 | 跨租户读 `records` ⇒ 404 |
| R10 | AST：4 个写端点都过 `_review_or_404`；详情/留痕有内联租户校验 |
| R11 | `decide` 未知结论 ⇒ 4xx（现状用 `REVIEW_ALREADY_DECIDED`，见 Q-V） |

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
    dbfile = base / f"rev_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A：律师甲；租户 B：律师乙。各有一条 DRAFT 复核。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import ReviewStatus, ReviewTargetType, UserStatus
    from app.models.identity import Tenant, User
    from app.models.review import Review

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
                    username=f"rep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            # 2026-09-22：`POST /reviews/ensure` 补案件归属校验（R12）
            client_a = _user("clientA", TENANT_A, Role.CLIENT)
            client_b = _user("clientB", TENANT_A, Role.CLIENT)
            s.add_all([lawyer_a, lawyer_b, client_a, client_b])
            await s.flush()

            from app.models.case import Case
            from app.models.enums import CaseStatus

            case_client = Case(
                tenant_id=TENANT_A,
                case_no=f"CASE-REV-{uuid.uuid4().hex[:6]}",
                title="客户甲的案件",
                status=CaseStatus.ACCEPTED,
                client_user_id=client_a.id,
            )
            s.add(case_client)
            await s.flush()

            def _review(tid, target_id):
                return Review(
                    tenant_id=tid,
                    target_type=ReviewTargetType.CASE_ANALYSIS,
                    target_id=target_id,
                    status=ReviewStatus.DRAFT,
                )

            rev_a = _review(TENANT_A, 7001)
            rev_b = _review(TENANT_B, 7002)
            s.add_all([rev_a, rev_b])
            await s.commit()

            return {"a": lawyer_a.id, "b": lawyer_b.id,
                    "client_a": client_a.id, "client_b": client_b.id,
                    "case_client": case_client.id,
                    "rev_a": rev_a.id, "rev_b": rev_b.id}

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


def _ensure(c, target_id: int, target_type: str = "CASE_ANALYSIS", case_id: int | None = None):
    params = {"target_type": target_type, "target_id": target_id}
    if case_id is not None:
        params["case_id"] = case_id
    return c.post("/api/v1/reviews/ensure", params=params)


def _status(engine, review_id: int) -> str:
    from app.models.review import Review

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return (await s.get(Review, review_id)).status.value

    return asyncio.run(_q())


def _count_reviews(engine) -> int:
    from sqlalchemy import func as _f

    from app.models.review import Review

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return int((await s.execute(
                select(_f.count()).select_from(Review))).scalar_one())

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.reviews as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"reviews.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ R1 装配层冒烟 ═══════════════════════


def test_zero_coverage_routes_all_respond(app_factory, seeded):
    """R1：7 条零覆盖路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r1 = c.get("/api/v1/reviews")
        r2 = _ensure(c, 8001)
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        rid = _data(r2)["id"]

        r3 = c.get(f"/api/v1/reviews/{rid}/records")
        r4 = c.get(f"/api/v1/reviews/{rid}")
        assert r3.status_code == 200, r3.text
        assert r4.status_code == 200, r4.text

        r5 = c.post(f"/api/v1/reviews/{rid}/submit", json={"comment": "提交复核"})
        assert r5.status_code == 200, r5.text
        r6 = c.post(f"/api/v1/reviews/{rid}/decide", json={"decision": "APPROVED"})
        assert r6.status_code == 200, r6.text
        r7 = c.post(f"/api/v1/reviews/{rid}/archive")
        assert r7.status_code == 200, r7.text

        # void 需要一条 DRAFT：ensure 一条新的
        rid2 = _data(_ensure(c, 8002))["id"]
        r8 = c.post(f"/api/v1/reviews/{rid2}/void", json={"comment": "作废"})
        assert r8.status_code == 200, r8.text

        # edit 已被 test_review_tenant_guard 覆盖过，这里一并冒烟
        rid3 = _data(_ensure(c, 8003))["id"]
        r9 = c.post(f"/api/v1/reviews/{rid3}/edit", json={"comment": "改一版"})
        assert r9.status_code == 200, r9.text


# ═══════════════════════ R2 列表租户隔离 ═══════════════════════


def test_list_is_tenant_scoped_both_directions(app_factory, seeded):
    """R2：列表双向租户隔离。"""
    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        ids = {i["id"] for i in _data(c.get("/api/v1/reviews")).get("items", [])}
        assert seeded["rev_a"] in ids
        assert seeded["rev_b"] not in ids

    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        ids = {i["id"] for i in _data(c.get("/api/v1/reviews")).get("items", [])}
        assert seeded["rev_b"] in ids
        assert seeded["rev_a"] not in ids


# ═══════════════════════ R3 ensure 幂等 + 租户隔离 ═══════════════════════


def test_ensure_is_idempotent_and_tenant_scoped(app_factory, seeded, engine):
    """R3：`ensure` 幂等，且幂等的钥匙里**必须带 tenant_id**。

    `ReviewService.ensure` 的查重条件是
    `(target_type, target_id, tenant_id)`。若调用方漏传 `tenant_id`，
    租户 A 的 `ensure` 会**命中并返回租户 B 的复核任务**——
    这是一条比「越权写」更隐蔽的串号：状态码 200，数据也是真的，
    只是**不是你的**。
    """
    tid = 9001
    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        first = _data(_ensure(c, tid))
        second = _data(_ensure(c, tid))
        assert first["id"] == second["id"], "ensure 不幂等 ⇒ 一次调用一条脏数据"

    # 租户 B 用**同一个 target_id** ensure ⇒ 必须是另一条，不能复用 A 的
    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        other = _data(_ensure(c, tid))
        assert other["id"] != first["id"], "跨租户复用了同一条复核任务 ⇒ 串号"


# ═══════════════════════ R4 非法参数不落库 ═══════════════════════


def test_ensure_illegal_target_type_rejects_and_writes_nothing(
    app_factory, seeded, engine,
):
    """R4（Q-V 已裁定）：非法 `target_type` ⇒ **422 `REVIEW_INVALID_PARAM`**，
    且一条 Review 都没多出来。

    Q-V 把「参数不合法」从 404 `REVIEW_NOT_FOUND` 改成了 422 `REVIEW_INVALID_PARAM`
    —— 调用方现在能区分「我传错了值」与「资源不存在」。这条判据**向后看**：
    若有人把状态码或 error.code 改回去，这里会红。
    """
    before = _count_reviews(engine)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = _ensure(c, 9100, target_type="NOT_A_TARGET_TYPE")
        assert r.status_code == 422, r.text
        assert _code(r) == "REVIEW_INVALID_PARAM", r.text
    assert _count_reviews(engine) == before, "非法参数还是落库了"


# ═══════════════════════ R5 跨租户写（带反向量） ═══════════════════════


def test_cross_tenant_decide_is_404_and_changes_nothing(app_factory, seeded, engine):
    """R5：跨租户 `decide` ⇒ 404，且**对方那条状态一字未动**。"""
    before = _status(engine, seeded["rev_b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/reviews/{seeded['rev_b']}/decide",
                   json={"decision": "APPROVED"})
        assert r.status_code == 404, r.text
        assert _code(r) == "REVIEW_NOT_FOUND", r.text
    assert _status(engine, seeded["rev_b"]) == before, "跨租户把别人的状态改了"


def test_cross_tenant_submit_is_404(app_factory, seeded, engine):
    """R5b：跨租户 `submit` ⇒ 404（守卫对**每个**写端点都得生效，不能只钉 decide）。"""
    before = _status(engine, seeded["rev_b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/reviews/{seeded['rev_b']}/submit", json={"comment": "x"})
        assert r.status_code == 404, r.text
        assert _code(r) == "REVIEW_NOT_FOUND", r.text
    assert _status(engine, seeded["rev_b"]) == before


# ═══════════════════════ R6 链路走端点 ═══════════════════════


def test_full_chain_through_endpoints(app_factory, seeded, engine):
    """R6：`ensure → submit → decide → archive` 全程走端点，逐状态断言。

    状态机白名单在 `test_review_fsm.py` 里测的是**纯表**；
    这里测的是「端点真的把状态机接上了」——
    若哪个端点绕过服务层直接改状态，纯表测试一条都不会红。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        rid = _data(_ensure(c, 9200))["id"]
        assert _status(engine, rid) == "draft"

        r = c.post(f"/api/v1/reviews/{rid}/submit", json={"comment": "提交"})
        assert r.status_code == 200, r.text
        assert _data(r)["status"] == "pending_confirm", r.text

        r = c.post(f"/api/v1/reviews/{rid}/decide",
                   json={"decision": "APPROVED", "comment": "通过"})
        assert r.status_code == 200, r.text
        assert _data(r)["status"] == "confirmed", r.text

        r = c.post(f"/api/v1/reviews/{rid}/archive")
        assert r.status_code == 200, r.text
        assert _data(r)["status"] == "archived", r.text


# ═══════════════════════ R7 留痕 ═══════════════════════


def test_records_are_written_by_the_endpoints(app_factory, seeded):
    """R7：留痕由端点写入，`actor_id` 是**调用者**。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        rid = _data(_ensure(c, 9300))["id"]
        c.post(f"/api/v1/reviews/{rid}/submit", json={"comment": "提交"})
        c.post(f"/api/v1/reviews/{rid}/decide", json={"decision": "APPROVED"})

        rows = _data(c.get(f"/api/v1/reviews/{rid}/records")) or []
        actions = [r["action"] for r in rows]
        assert "CREATE" in actions, actions
        assert "SUBMIT" in actions, actions
        assert "APPROVE" in actions, actions
        assert all(r["actor_id"] == seeded["a"] for r in rows if r["action"] != "CREATE")


# ═══════════════════════ R8 非法流转 ═══════════════════════


def test_illegal_transition_rejected_and_status_unchanged(app_factory, seeded, engine):
    """R8：DRAFT 直接 `archive` ⇒ 4xx `REVIEW_TRANSITION_DENIED`，状态未变。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        rid = _data(_ensure(c, 9400))["id"]
        before = _status(engine, rid)
        r = c.post(f"/api/v1/reviews/{rid}/archive")
        assert r.status_code >= 400, r.text
        assert _code(r) == "REVIEW_TRANSITION_DENIED", r.text
    assert _status(engine, rid) == before


# ═══════════════════════ R9 跨租户读留痕 ═══════════════════════


def test_cross_tenant_records_is_404(app_factory, seeded):
    """R9：跨租户读留痕 ⇒ 404（`review_records` 的内联租户校验）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/reviews/{seeded['rev_b']}/records")
        assert r.status_code == 404, r.text
        assert _code(r) == "REVIEW_NOT_FOUND", r.text


# ═══════════════════════ R10 AST 装配层 ═══════════════════════


def test_four_write_endpoints_go_through_the_guard():
    """R10a：`submit` / `decide` / `archive` / `void` 全部经过 `_review_or_404`。

    ⚠️ 必须带**命中数下限自检**：不校验数量时，端点一旦改名，
    `ast.walk` 找不到就退化成空集合 ⇒ **判据静默通过**（`methodology.md` 89）。
    """
    targets = ["submit_review", "decide_review", "archive_review", "void_review"]
    hit = [t for t in targets if "_review_or_404" in _func(t)]
    assert len(hit) == 4, f"只有 {hit} 走了守卫 ⇒ 有写端点裸奔"


def test_read_endpoints_have_inline_tenant_check():
    """R10b：详情 / 留痕有内联租户校验（它们不走 `_review_or_404`）。"""
    for name in ("get_review", "review_records"):
        seg = _func(name)
        assert "r.tenant_id != ctx.tenant_id" in seg, f"{name} 没有租户校验"
        assert "ctx.tenant_id" in seg


def test_ensure_passes_ctx_tenant_not_user_tenant():
    """R10c：`ensure` 的 `tenant_id` 必须来自 `ctx`，不能来自 `user`。

    `user.tenant_id` 与 `ctx.tenant_id` 只在**平台管理员切换租户**时不同
    （`methodology.md` 88）——这是本仓库已经踩过四次的坑。
    """
    seg = _func("ensure_review")
    assert "tenant_id=ctx.tenant_id" in seg, "ensure 的租户来源不是 ctx"
    assert "user.tenant_id" not in seg, "又用回 user.tenant_id 了"


def test_archived_review_cannot_be_edited(app_factory, seeded, engine):
    """R12：**终态**归档后不能再改 —— 这条压的是状态机**白名单**那一层。

    为什么单列一条：`assert_transition` 有**两层独立**防线——
    白名单 + 「未确认不可归档 / 未提交不可定稿」的前置条件。
    只删白名单，R8（DRAFT 直接归档）依然被前置条件挡住 ⇒ **白名单没有判据**。
    必须找一个**只有白名单管、前置条件不管**的流转来钉它：
    `ARCHIVED → LAWYER_EDITING` 正是这样一条（前置条件只管 CONFIRMED/ARCHIVED
    这两种**目标**状态，不管 LAWYER_EDITING）。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        rid = _data(_ensure(c, 9600))["id"]
        c.post(f"/api/v1/reviews/{rid}/submit", json={"comment": "提交"})
        c.post(f"/api/v1/reviews/{rid}/decide", json={"decision": "APPROVED"})
        r = c.post(f"/api/v1/reviews/{rid}/archive")
        assert r.status_code == 200, r.text
        assert _status(engine, rid) == "archived"

        r = c.post(f"/api/v1/reviews/{rid}/edit", json={"comment": "归档后再改一版"})
        assert r.status_code >= 400, r.text
        assert _code(r) == "REVIEW_TRANSITION_DENIED", r.text
    assert _status(engine, rid) == "archived", "归档后还是被改了"


# ═══════════════════════ R11 未知结论 ═══════════════════════


def test_unknown_decision_is_rejected(app_factory, seeded, engine):
    """R11（Q-V 已裁定）：`decide` 未知结论 ⇒ **422 `REVIEW_INVALID_PARAM`**，状态不变。

    Q-V 把「未知结论值」从 `REVIEW_ALREADY_DECIDED`（那是「已出过结论」的状态码）
    改成了 `REVIEW_INVALID_PARAM`，消除语义错配。这条判据向后看：
    若状态码/error.code 被改回旧语义，这里会红。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        rid = _data(_ensure(c, 9500))["id"]
        c.post(f"/api/v1/reviews/{rid}/submit", json={"comment": "提交"})
        before = _status(engine, rid)
        r = c.post(f"/api/v1/reviews/{rid}/decide", json={"decision": "WHATEVER"})
        assert r.status_code == 422, r.text
        assert _code(r) == "REVIEW_INVALID_PARAM", r.text
    assert _status(engine, rid) == before


# ═══════════ R12 `ensure` 的归属校验（2026-09-22 收口 TENANT 档）═══════════


def test_client_cannot_ensure_review_on_others_case(app_factory, seeded):
    """R12：客户对**他人案件**发起复核 ⇒ 404 `CASE_NOT_FOUND`。

    收口前 `ensure_review` 只把 `ctx.tenant_id` 传下去 ⇒ 同租户的任意客户
    都能对他人的分析/文书/证据发起复核（把他人材料推进复核流程）。
    """
    app_other = app_factory(seeded["client_b"])
    with _client(app_other) as c:
        r = _ensure(c, 9601, case_id=seeded["case_client"])
        assert r.status_code == 404, f"客户乙竟然对他人案件发起了复核：{r.text}"
        assert _code(r) == "CASE_NOT_FOUND", r.text


def test_client_without_case_id_cannot_ensure_review(app_factory, seeded):
    """R12b：客户**不挂案件**发起复核 ⇒ 404。

    不给 `case_id` 就没法判断归属，放行等于允许对任意 `target_id` 建复核。
    """
    app_client = app_factory(seeded["client_a"])
    with _client(app_client) as c:
        r = _ensure(c, 9602)
        assert r.status_code == 404, f"客户不挂案件竟然也能建复核：{r.text}"
        assert _code(r) == "CASE_NOT_FOUND", r.text


def test_client_can_ensure_review_on_own_case(app_factory, seeded):
    """R12c（**反向向量**）：客户对自己案件发起复核 ⇒ 200。

    没有这条，R12 会变成「谁都建不了复核」的假绿。
    """
    app_client = app_factory(seeded["client_a"])
    with _client(app_client) as c:
        r = _ensure(c, 9603, case_id=seeded["case_client"])
        assert r.status_code == 200, f"客户甲反而建不了自己案件的复核：{r.text}"
