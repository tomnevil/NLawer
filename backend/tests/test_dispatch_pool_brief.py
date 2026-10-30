"""抢单池「内联案件摘要」的契约判据（2026-10-10）。

## 为什么必须新开一个文件

`test_dispatch_endpoint_layer.py::P3` 只断言**「池里有哪些 dispatch」**，
从未断言**「池里每条 `case_id` 调用方都读得到」**。于是下面这个矛盾
在库里活了 9 天没人发现：

| 端点 | 可见性口径 |
|---|---|
| `GET /dispatches/pool` | **租户级**（`tenant_id` + `PENDING` + 未指定/POOL） |
| `GET /cases/{id}` | **承办人级**（P2-10：本人承办 / 有效团队授权，否则 404） |

⇒ 池子把「你还没资格读」的案件 id 交出去，前端再按 `case_id` 回查标题
⇒ **池内案件对任何律师都读不到** ⇒ 派单池永远只显示「案件 #N」。
（2026-10-10 由 `evidence/dispatch_pool_404_repro_2026-10-10.txt` 三账号对照 +
注入反证查清；`lawyer/dispatches` 上那条 `GET /cases/10` 404 就是这个。）

**根因不是数据错，是「两个端点对同一资源 id 的可见性口径不一致」。**
这类缺陷**单端点测试永远发现不了**——只能靠交叉口径判据。本文件就是那条。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| T1 | 池内**每一条**都带非空摘要；且该摘要**是详情端点给不出的**（双向断言，防「零判据」） |
| T2 | `case_brief` 键集**恰等于**白名单（精确相等，多一个少一个都红） |
| T3 | 摘要 join **按租户收窄**（本租户派单指向他租户案件 ⇒ 摘要必须为 `None`）+ 反证 |
| T4 | 敏感字段（当事人姓名 / 案情全文）**即使库里有值也不出现在响应里** + 数据存在性证明 |
| T5 | （跨层）派单池页不得再逐条回查 `GET /cases/{id}`（源码级地板） |
| T6 | **真种子**里池内案件不得带承办人、不得离开待派单态（+ 守卫的注入反证） |

⚠️ T1 的两半**必须同时成立**才有意义：
  - 只有「有摘要」⇒ 无法区分「内联生效」与「恰好这个律师有权限」；
  - 只有「详情 404」⇒ 那是 P2-10 的既有判据，跟池子无关。
  两者一起才说明「池子自己补了摘要、而详情仍然照旧拒绝」。
"""
from __future__ import annotations

import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import or_, select

TENANT_A = "pool-tenant-a"
TENANT_B = "pool-tenant-b"

#: 🚨 白名单**唯一真源**（测试侧）。
#: 与 `app/schemas/case.py::CaseBriefOut` 的字段集、以及前端
#: `apps/lawyer/app/(app)/dispatches/page.tsx` 的 `CaseBrief` 接口
#: **三者必须精确相等**。改任何一处都要同步改另外两处，否则 T2 立刻红。
BRIEF_KEYS = {
    "id",
    "case_no",
    "title",
    "grade",
    "status",
    "dispute_type",
    "claim_amount",
    "focus",
}

#: 绝不允许出现在池摘要里的字段。
FORBIDDEN_KEYS = {
    "party_a",  # 当事人姓名（个人信息）
    "party_b",
    "summary",  # 案情全文
    "client_user_id",  # 指向客户的内部 id
    "conversation_id",
    "tenant_id",
    "lawyer_id",  # 池子里本来就没有承办人
    "intent",
    "complexity",
    "urgency",
    "require_formal_opinion",
}


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"pool_brief_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A：律师甲（调用方）、律师丙（另一承办人）；租户 B：律师乙。

    关键构造：
      * `case_owned_by_c` 在租户 A 内**归律师丙承办**，但派单进了抢单池
        ⇒ 律师甲**必须**拿得到它的摘要（这是本文件存在的理由）。
      * `dirty_a` 是**租户 A 的派单指向租户 B 的案件**——库层面拦不住的脏数据，
        用来验证 join 真的按租户收窄。
      * `case_a` 上塞了 `party_a` / `summary`，用来证明敏感字段被**主动剔除**，
        而不是「恰好库里没值」。
    """
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.case import Case, Dispatch
    from app.models.enums import (
        CaseGrade,
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
                if not (await s.execute(
                        select(Tenant).where(Tenant.tenant_id == tid))).scalars().first():
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid):
                return User(
                    username=f"pool-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.LAWYER,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_c = _user("lawyerC", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            s.add_all([lawyer_a, lawyer_c, lawyer_b])
            await s.flush()

            def _case(tid, tag, **kw):
                c = Case(
                    tenant_id=tid,
                    case_no=f"POOL-{tag}-{uuid.uuid4().hex[:6]}",
                    title=f"{tag} 的案件",
                    status=CaseStatus.PENDING_DISPATCH,
                    grade=CaseGrade.B,
                    dispute_type="劳动争议",
                    claim_amount=12345.0,
                    focus="争议焦点：单方调岗降薪",
                    **kw,
                )
                s.add(c)
                return c

            # 甲自己承办的池内案件
            case_a = _case(TENANT_A, "A", lawyer_id=lawyer_a.id,
                           party_a="张三", party_b="某公司",
                           summary="案情全文：这段绝不该出现在抢单池里")
            # 🎯 丙承办、甲无权读 —— 池摘要必须仍然拿得到
            case_c = _case(TENANT_A, "C", lawyer_id=lawyer_c.id)
            # 租户 B 的案件：作为「脏数据指向」的目标
            case_b = _case(TENANT_B, "B", lawyer_id=lawyer_b.id)
            await s.flush()

            def _disp(tid, case_id, mode=DispatchMode.POOL, status=DispatchStatus.PENDING):
                d = Dispatch(
                    tenant_id=tid, case_id=case_id, mode=mode, status=status, lawyer_id=None,
                )
                s.add(d)
                return d

            pool_a = _disp(TENANT_A, case_a.id)
            pool_c = _disp(TENANT_A, case_c.id)
            # 🚨 注入夹具：租户 A 的派单指向**租户 B**的案件
            dirty_a = _disp(TENANT_A, case_b.id)
            await s.commit()

            return {
                "a": lawyer_a.id,
                "c": lawyer_c.id,
                "b": lawyer_b.id,
                "case_a": case_a.id,
                "case_c": case_c.id,
                "case_b": case_b.id,
                "pool_a": pool_a.id,
                "pool_c": pool_c.id,
                "dirty_a": dirty_a.id,
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


def _pool(c) -> list[dict]:
    r = c.get("/api/v1/dispatches/pool")
    assert r.status_code == 200, r.text
    return r.json()["data"]["items"]


# ═══════════════════════ T1 ═══════════════════════


def test_t1_every_pool_row_carries_brief_that_detail_endpoint_denies(app_factory, seeded):
    """T1：池内**每一条**都带非空摘要，**且**该摘要是详情端点给不出的。

    这是此前缺失的**交叉口径判据**。两半都必须成立：

    * 前半（有摘要）：池子自己补齐了标题 ⇒ 页面能显示「房屋租赁合同纠纷咨询」
      而不是「案件 #4」。
    * 后半（详情仍 404）：**证明前半不是「恰好这个律师有权限」**——
      甲对 `case_c` 确实无权（P2-10 照旧拒绝），但摘要仍然到了。
      没有这半，T1 在「有人把 `_case_or_404` 放宽了」的情况下依然会绿，
      而那恰恰是我们**不想要**的修法。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        items = _pool(c)
        by_case = {i["case_id"]: i for i in items}

        # ⚠️ 排除 T3 的**人造脏数据行**（租户 A 的派单指向租户 B 的案件）：
        #    那一行**必须没有**摘要，由 T3 单独断言。不排除会让 T1 与 T3 互相打架
        #    —— 「每行都要有摘要」与「跨租户那行不许有摘要」不可能同时成立。
        legit = {cid: i for cid, i in by_case.items() if cid != seeded["case_b"]}

        # ── 前半：每条都有摘要 ──
        assert legit, "池子里没有合法行 ⇒ 判据无意义"
        missing = [cid for cid, i in legit.items() if not i.get("case_brief")]
        assert not missing, f"这些 case_id 没带摘要（页面只能显示「案件 #N」）：{missing}"

        # 🎯 甲无权读、但摘要必须到位的这一条
        assert seeded["case_c"] in by_case, "丙承办的池单没进池 ⇒ 判据失去目标"
        brief_c = by_case[seeded["case_c"]]["case_brief"]
        assert brief_c is not None
        assert brief_c["title"] == "C 的案件"

        # ── 后半：详情端点对同一条**照旧**拒绝（P2-10 未被动摇）──
        r = c.get(f"/api/v1/cases/{seeded['case_c']}")
        assert r.status_code == 404, (
            "详情端点不再拒绝非承办律师了 —— 若这是**有意**放宽 P2-10，"
            f"请连同本判据一起改；否则说明归属守卫被削弱了。实测 {r.status_code}"
        )
        assert str(r.json()["error"]["code"]) == "CASE_NOT_FOUND"


# ═══════════════════════ T2 ═══════════════════════


def test_t2_brief_keys_are_exactly_the_whitelist(app_factory, seeded):
    """T2：`case_brief` 的键集**恰等于**白名单——多一个少一个都判红。

    用**精确相等**而不是「包含」：池摘要是发给**尚未承办**该案件的律师的，
    多给一个字段就是多一分无理由的暴露。用子集断言会让「顺手加个字段」
    永远不被发现。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        briefs = [i["case_brief"] for i in _pool(c) if i.get("case_brief")]
    assert briefs, "池子里没有摘要 ⇒ 判据无意义"
    for b in briefs:
        assert set(b) == BRIEF_KEYS, (
            f"摘要键集漂移：多了 {set(b) - BRIEF_KEYS}，少了 {BRIEF_KEYS - set(b)}。"
            " 三处（CaseBriefOut / 前端 CaseBrief / 本文件 BRIEF_KEYS）必须同步改。"
        )


# ═══════════════════════ T3 ═══════════════════════


def test_t3_brief_join_is_tenant_scoped(engine, app_factory, seeded):
    """T3：摘要 join **按租户收窄** —— 本租户派单指向他租户案件时摘要必须为 `None`。

    派单表的 `case_id` 只有外键约束，「本租户的派单指向他租户的案件」这种脏数据
    在库层面拦不住。少写 `Case.tenant_id == tenant_id` 就会把**别家租户的案件标题**
    通过本租户的池子漏出去。

    反证（本用例后半）：**同一条 `case_id` 不带租户条件去查是查得到的** ——
    证明「摘要为 None」是租户条件在起作用，而不是「这条案件恰好不存在」。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        by_case = {i["case_id"]: i for i in _pool(c)}
    assert seeded["case_b"] in by_case, (
        "指向他租户案件的派单没进池 ⇒ 判据失去目标（夹具没生效）"
    )
    leaked = by_case[seeded["case_b"]]["case_brief"]
    assert leaked is None, (
        f"🚨 跨租户泄露：本租户的池子里出现了租户 {TENANT_B} 的案件摘要 {leaked}"
    )

    # ── 反证：证明这个 case_id 背后**确实**有一条带标题的他租户案件 ──
    from app.models.case import Case

    async def _probe():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            # ① 不带租户条件 ⇒ 命中（说明数据在）
            hit = await s.get(Case, seeded["case_b"])
            # ② 带租户条件 ⇒ 落空（说明 join 的过滤条件真的在干活）
            scoped = (await s.execute(
                select(Case).where(Case.id == seeded["case_b"], Case.tenant_id == TENANT_A)
            )).scalars().first()
            return hit, scoped

    hit, scoped = asyncio.run(_probe())
    assert hit is not None and hit.title == "B 的案件", (
        "反证失效：这个 case_id 背后没有案件 ⇒ T3 前半可能只是「查不到」而非「被租户挡住」"
    )
    assert scoped is None, "反证失效：租户条件没能把这条案件挡在外面"


# ═══════════════════════ T4 ═══════════════════════


def test_t4_sensitive_fields_never_leak_even_when_populated(engine, app_factory, seeded):
    """T4：敏感字段**即使库里有值**也不出现在响应里。

    ⚠️ 必须先证明**库里真的有值**（否则「没泄露」可能只是「本来就没填」，
    属于零判据）。`case_a` 在夹具里被塞了 `party_a="张三"`、
    `summary="案情全文：这段绝不该出现在抢单池里"`。

    这里直接对**整个响应体**做字符串扫描，而不是只检查 `case_brief` 的键——
    因为泄露也可能来自「有人把 `case_brief` 摊平进了 `PoolItemOut`」。
    """
    import json

    app = app_factory(seeded["a"])
    with _client(app) as c:
        raw = c.get("/api/v1/dispatches/pool").text

    # ── 先证明库里确实有这些值（零判据防线）──
    from app.models.case import Case

    async def _probe():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return await s.get(Case, seeded["case_a"])

    case_a = asyncio.run(_probe())
    assert case_a.party_a == "张三" and case_a.summary, (
        "夹具没把敏感字段填上 ⇒ T4 变成零判据（「没泄露」只是「本来就没有」）"
    )

    # ── 再断言整个响应体里一个都不许出现 ──
    payload = json.loads(raw)
    for cid, item in {i["case_id"]: i for i in payload["data"]["items"]}.items():
        if item.get("case_brief") is None:
            continue
        extra = set(item["case_brief"]) & FORBIDDEN_KEYS
        assert not extra, f"case_id={cid} 的摘要里出现了敏感字段：{extra}"
    for needle in ("张三", "某公司", "案情全文"):
        assert needle not in raw, (
            f"🚨 响应体里出现了 {needle!r} —— 池摘要是**白名单投影**，"
            "当事人姓名 / 案情全文不得外泄"
        )


# ═══════════════════════ T5（跨层） ═══════════════════════

PAGE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "apps" / "lawyer" / "app" / "(app)" / "dispatches" / "page.tsx"
)


def test_t5_frontend_no_longer_re_queries_case_detail():
    """T5（跨层地板）：派单池页**不得**再逐条回查 `GET /cases/{id}`。

    ## 为什么这条必须存在，且必须在这里

    本文件 T1–T4 守的是**后端契约**（池子自带摘要）。但这次改动还有**另一半在
    源码层**：前端不再逐条回查。若有人把那段 `Promise.allSettled` 回查加回来：

    * T1–T4 **全绿**（后端契约没变）；
    * 唯一会红的是 `evidence/verify_runtime_health.py`（池页会再打出一串 404）
      —— 而它只在 `browser-all` job 里跑，**该 job 仅 main / 手动触发**
      ⇒ PR 上无人拦截（`methodology.md` 坑 74：「有个 job 会跑它」≠
      「每个 PR 都会跑它」）。

    ⇒ 在**每个 PR 都跑**的后端测试里放一条源码级地板，成本最低。

    ⚠️ 用「**不得出现** + **必须出现**」成对断言：只查前者的话，把整个文件删空
    也能通过（零判据）。
    """
    assert PAGE.exists(), f"找不到派单池页面：{PAGE}（路径变了？判据需同步）"
    src = PAGE.read_text(encoding="utf-8")

    # 去掉注释再查，避免把「说明为什么不再这么做」的注释判成违规
    code_lines = [
        ln for ln in src.splitlines()
        if not ln.lstrip().startswith(("*", "/*", "//"))
    ]
    code = "\n".join(code_lines)

    assert "/api/v1/cases/" not in code, (
        "派单池页又出现了 `/api/v1/cases/` 回查 —— 该做法在 P2-10 之后对池内案件"
        "必然 404（池按租户取、详情按承办人鉴权），且会退回 N+1。"
        " 摘要请用响应里的 `case_brief`。"
    )
    assert "case_brief" in code, (
        "派单池页没引用 `case_brief` ⇒ 摘要没被真正用上（页面会退回「案件 #N」）"
    )


# ═══════════════════════ T6（真种子） ═══════════════════════

#: `seed_business` 按**用户名**查这几个演示账号（`app/seed/business.py::_get_user`）。
#: 缺哪个，对应案件/派单的承办人就是 `None` —— 会让 T6 变成零判据。
FIRM_DEMO_USERS = (
    ("client", "CLIENT"),
    ("lawyer_wang", "LAWYER"),
    ("lawyer_li", "LAWYER"),
    ("lawyer_zhao", "LAWYER"),
    ("firm_admin", "FIRM_ADMIN"),
)


@pytest.fixture(scope="module")
def firm_seeded(engine):
    """把**真实**的 `app/seed/business.py` 灌进临时库（不碰开发库）。

    ## 为什么必须用**真种子**，而不是复用 `seeded` 夹具

    `seeded` 夹具**故意**造了「池内案件带承办人」（`case_a` / `case_c` 都带
    `lawyer_id`）—— T1 依赖它证明「摘要到位 ≠ 恰好这个律师有权限」。
    所以 T6 这条不变量**不能**在那个夹具上测，否则两条判据互相打架
    （2026-10-10 已在 T1/T3 上踩过同一个坑：「每行都要有摘要」与
    「跨租户那行不许有摘要」不可能同时成立）。

    而缺陷恰恰出在**真种子**里（`0004→wang` / `0010→zhao`），所以只能跑真种子。
    """
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
    from app.models.identity import Tenant, User
    from app.seed.business import seed_business

    async def _run():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid, name in (("firm_hlw", "华立威律所"), ("platform", "平台")):
                exists = (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid))).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=name))
            await s.flush()

            for uname, role_name in FIRM_DEMO_USERS:
                exists = (await s.execute(
                    select(User).where(User.username == uname))).scalars().first()
                if exists:
                    continue
                s.add(
                    User(
                        username=uname,
                        hashed_password=hash_password("Str0ngPass!"),
                        full_name=uname,
                        role=getattr(Role, role_name),
                        tenant_id="firm_hlw",
                        status=UserStatus.ACTIVE,
                        is_active=True,
                    )
                )
            await s.flush()

            # 🚨 这一步会调用 `_assert_pool_invariant` —— 种子一旦漂移，这里直接抛。
            stats = await seed_business(s)
            await s.commit()
            return stats

    stats = asyncio.run(_run())
    assert stats.get("dispatches"), "真种子没产出派单 ⇒ T6 失去目标"
    return stats


def _firm_pool_rows(engine) -> list[tuple[str, int | None, object]]:
    """按 `grab_pool` 的谓词从**真种子**库里取池内案件，返回 `(case_no, lawyer_id, status)`。"""
    from app.models.case import Case, Dispatch
    from app.models.enums import DispatchMode, DispatchStatus

    async def _probe():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            rows = (
                await s.execute(
                    select(Dispatch, Case)
                    .join(Case, Case.id == Dispatch.case_id)
                    .where(
                        Dispatch.tenant_id == "firm_hlw",
                        Case.tenant_id == "firm_hlw",
                        Dispatch.status == DispatchStatus.PENDING,
                        or_(Dispatch.lawyer_id.is_(None), Dispatch.mode == DispatchMode.POOL),
                    )
                )
            ).all()
            return [(c.case_no, c.lawyer_id, c.status) for _d, c in rows]

    return asyncio.run(_probe())


def test_t6_seeded_pool_cases_have_no_assignee(firm_seeded, engine):
    """T6：**真种子**里的池内案件不得带承办人，且必须处于待派单态。

    这是 `_assert_pool_invariant` 的端到端版本 —— 守卫在**播种期**就会炸，
    本用例断言它在**当前**种子上确实通过，并逐条钉住事实。

    ## 这条缺陷为什么能活 9 天

    种子给池内案件预指派了承办人（`0004→wang` / `0010→zhao`），于是：

    * 池子只对那**一个人**可见（其余律师点进详情 404）；
    * 更要命的是它**掩盖**了「池内案件读不到标题」这个真实缺陷 ——
      演示时用 `wang` 登录，`case 4` 读得到、只有 `case 10` 红，
      看起来像「个别数据有问题」，而不是「池子与详情的可见性口径互相矛盾」。
    """
    from app.models.enums import CaseStatus

    pool = _firm_pool_rows(engine)

    # ① 判据必须有目标（防「池子空了所以通过」这种零判据）
    nos = {no for no, _l, _s in pool}
    assert {"HLW-DEMO-0004", "HLW-DEMO-0010"} <= nos, (
        f"池子里找不到那两条演示案件 ⇒ 判据失去目标。实测池内：{sorted(nos)}"
    )

    # ② 不变量：池内案件**不得**有承办人
    bad = [(no, lid) for no, lid, _s in pool if lid is not None]
    assert not bad, (
        f"🚨 池内案件被预指派了承办人：{bad}。池子按**租户**取、详情按**承办人**"
        "鉴权（P2-10）⇒ 带承办人会让池子只对该一人可见（其余律师 404），"
        "并把这个缺陷**掩盖**成「个别案件读不到」。池内案件必须 lawyer_id IS NULL。"
    )

    # ③ 不变量：池内案件**必须**处于待派单态
    bad_status = [(no, s) for no, _l, s in pool if s != CaseStatus.PENDING_DISPATCH]
    assert not bad_status, (
        f"🚨 池内案件不在待派单态：{bad_status}。`dispatch_service` 规定"
        " `lawyer_id is None ⇒ status=PENDING_DISPATCH`，两者必须一致。"
    )


def test_t6b_pool_invariant_guard_fires_on_drift():
    """T6b（**注入反证**）：把池内案件「预指派」回去 ⇒ 守卫必须抛。

    没有这一条，T6 只是「读了一遍数据」—— 守卫要是恒不抛（或根本没被调用），
    T6 照样能绿。这里直接对守卫注入两种漂移，并留一个**对照臂**证明它
    不是「恒抛」的假判据。
    """
    from app.models.case import Case
    from app.models.enums import CaseGrade, CaseStatus, DispatchMode, DispatchStatus
    from app.seed.business import _assert_pool_invariant

    def _case(no: str, lawyer_id: int | None, status: CaseStatus) -> Case:
        return Case(
            case_no=no,
            tenant_id="firm_hlw",
            title="t",
            lawyer_id=lawyer_id,
            status=status,
            grade=CaseGrade.C,
        )

    # ① 破坏「无承办人」⇒ 必须抛
    with pytest.raises(RuntimeError, match="预指派"):
        _assert_pool_invariant(
            [(_case("X-1", 7, CaseStatus.PENDING_DISPATCH), None,
              DispatchMode.POOL, DispatchStatus.PENDING)]
        )

    # ② 破坏「待派单态」⇒ 必须抛
    with pytest.raises(RuntimeError, match="待派单态"):
        _assert_pool_invariant(
            [(_case("X-2", None, CaseStatus.INTAKE), None,
              DispatchMode.POOL, DispatchStatus.PENDING)]
        )

    # ③ 对照臂：正确形态**不得**抛（否则守卫是「恒抛」的假判据）
    _assert_pool_invariant(
        [(_case("X-3", None, CaseStatus.PENDING_DISPATCH), None,
          DispatchMode.POOL, DispatchStatus.PENDING)]
    )

    # ④ 对照臂：DESIGNATED 预指派是**合法**演示态，不受池不变量约束
    #    （`grab_pool` 的谓词会把它排掉，所以它不进池）
    _assert_pool_invariant(
        [(_case("X-4", 7, CaseStatus.DISPATCHED), 7,
          DispatchMode.DESIGNATED, DispatchStatus.PENDING)]
    )
