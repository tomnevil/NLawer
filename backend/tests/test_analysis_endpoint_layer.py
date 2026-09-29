"""案件分析 6 条路由的**端点层**判据（Q-T 清单第四批）。

## 为什么这个文件必须存在

`GET/POST/PUT /api/v1/analyses*` 共 6 条路由，在 `tests/` 下**从未被请求过**。
分析是**AI 办案的核心资产**：六段式结论、法律依据、相似判例、缺失材料清单，
且 `PUT /{id}` 会生成版本快照（PRD 要求「AI 输出 → 律师修改 → 确认」全程可追溯）。

⚠️ 与 §3.28 的派单不同：本模块读代码看起来**守卫都在位**
（`_load_or_404` + 内联租户过滤），所以本轮同样**先跑判据再下结论**。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| A1 | 6 条路由**首次真实 HTTP 请求**：全员非 500 |
| A2 | `GET /case/{id}` 跨租户 ⇒ 404 `ANALYSIS_NOT_FOUND`（查询带租户过滤） |
| A3 | `POST /case/{id}/generate` 跨租户 ⇒ 404 `CASE_NOT_FOUND`（守卫在 `job_handlers`） |
| A4 | `PUT /{id}` 跨租户 ⇒ 404，**且对方内容一字未改**（反向量） |
| A5/A6 | 跨租户读 `versions` / `decisions` ⇒ 404 |
| A7 | 同租户 `PUT` 正常工作：版本 +1、落快照、`changed_by` 正确 |
| A8 | 同租户 `versions` 能取回快照；`decisions` 返回列表 |
| A9 | AST：4 个 `{analysis_id}` 路由都过 `_load_or_404`（带命中数下限自检） |
| A10 | 跨租户 `iterate` ⇒ 404（守卫在 Copilot **之前**） |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
⚠️ **刻意不做同租户 `iterate` 的正向判据**：`CaseCopilot.generate` 走 LLM，
后端未配 LLM（`MEMORY.md` 硬约束 5）⇒ 正向结果依赖降级路径、不稳定。
跨租户那条**在 Copilot 之前就被守卫挡住**，不依赖 LLM，故保留。
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
    dbfile = base / f"ana_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A / B 各一个案件 + 一条分析 + 一个 AI 决策。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.ai_run import AiDecision
    from app.models.analysis import CaseAnalysis
    from app.models.base import Base
    from app.models.case import Case
    from app.models.enums import CaseStatus, UserStatus
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

            def _user(tag, tid, role=Role.LAWYER):
                return User(
                    username=f"aep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            lawyer_a = _user("lawyerA", TENANT_A)
            lawyer_b = _user("lawyerB", TENANT_B)
            s.add_all([lawyer_a, lawyer_b])
            await s.flush()

            def _case(tid, tag):
                return Case(
                    tenant_id=tid,
                    case_no=f"CASE-{tag}-{uuid.uuid4().hex[:6]}",
                    title=f"{tag} 的案件",
                    status=CaseStatus.ACCEPTED,
                )

            case_a = _case(TENANT_A, "A")
            case_b = _case(TENANT_B, "B")

            # 2026-09-22：`POST /case/{id}/generate` 补客户归属校验（A11）。
            # 同租户两个客户 + 一个归属 client_a 的案件。
            client_a = _user("clientA", TENANT_A, Role.CLIENT)
            client_b = _user("clientB", TENANT_A, Role.CLIENT)
            s.add_all([client_a, client_b])
            await s.flush()

            case_client = _case(TENANT_A, "CA")
            case_client.client_user_id = client_a.id

            s.add_all([case_a, case_b, case_client])
            await s.flush()

            def _analysis(tid, case, tag, run_id):
                return CaseAnalysis(
                    tenant_id=tid,
                    case_id=case.id,
                    run_id=run_id,
                    version=1,
                    summary=f"{tag} 的结论",
                    legal_analysis=f"{tag} 的法律依据",
                )

            ana_a = _analysis(TENANT_A, case_a, "A", 5001)
            ana_b = _analysis(TENANT_B, case_b, "B", 5002)
            s.add_all([ana_a, ana_b])
            await s.flush()

            s.add(AiDecision(run_id=5001, stage="RETRIEVE",
                             decision="命中 3 条法条", reason="向量召回", duration_ms=12))
            await s.commit()

            return {
                "a": lawyer_a.id,
                "b": lawyer_b.id,
                "client_a": client_a.id,
                "client_b": client_b.id,
                "case_a": case_a.id,
                "case_b": case_b.id,
                "case_client": case_client.id,
                "ana_a": ana_a.id,
                "ana_b": ana_b.id,
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


def _analysis_snap(engine, analysis_id: int):
    from app.models.analysis import CaseAnalysis

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            a = await s.get(CaseAnalysis, analysis_id)
            return (a.version, a.summary, a.legal_analysis)

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.analyses as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"analyses.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ A1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """A1：6 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get(f"/api/v1/analyses/case/{seeded['case_a']}").status_code == 200
        assert c.get(f"/api/v1/analyses/{seeded['ana_a']}/versions").status_code == 200
        assert c.get(f"/api/v1/analyses/{seeded['ana_a']}/decisions").status_code == 200
        r = c.put(f"/api/v1/analyses/{seeded['ana_a']}", json={"summary": "改过的结论"})
        assert r.status_code == 200, r.text
        r = c.post(f"/api/v1/analyses/case/{seeded['case_a']}/generate")
        assert r.status_code == 200, r.text
        # iterate 走 LLM，只做跨租户那条（A10），不在这里冒烟


# ═══════════════════════ A2/A3 案件维度越权 ═══════════════════════


def test_get_case_analysis_cross_tenant_is_404(app_factory, seeded):
    """A2：`GET /case/{id}` 跨租户 ⇒ 404。

    注意这里的判据是**查询本身带租户过滤**（`CaseAnalysis.tenant_id == ctx.tenant_id`），
    不是先取后判 ⇒ 跨租户表现为「尚未生成分析」，与无数据同形。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/analyses/case/{seeded['case_b']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "ANALYSIS_NOT_FOUND", r.text


def test_generate_cross_tenant_is_404(app_factory, seeded):
    """A3：`POST /case/{id}/generate` 跨租户 ⇒ 404。

    ⚠️ 这条的守卫**不在 `analyses.py`**——`generate_analysis` 只把 `ctx.tenant_id`
    透传给 `trigger_case_analysis`，真正的判等在 `job_handlers.py:141`。
    与 `methodology.md` 93 同形：**同一个边界的第二份实现在别的文件里**。
    判据必须钉住行为，不能只扫 `analyses.py`。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/analyses/case/{seeded['case_b']}/generate")
        assert r.status_code == 404, r.text
        assert _code(r) == "CASE_NOT_FOUND", r.text


def test_generate_by_other_client_same_tenant_is_404(app_factory, seeded):
    """A11：同租户的客户乙对客户甲的案件触发 AI 生成 ⇒ 404。

    收口前 `generate_analysis` 只把 `ctx.tenant_id` 透传下去（跨租户那道守卫
    在 `job_handlers`，一直都在）⇒ **同租户的任意客户都能对他人案件触发生成**。
    这是 A3 的另一半：A3 管跨租户，A11 管同租户横向。
    反向向量：客户甲对自己的案件可以触发（否则这条会变成「谁都触发不了」的假绿）。
    """
    app_other = app_factory(seeded["client_b"])
    with _client(app_other) as c:
        r = c.post(f"/api/v1/analyses/case/{seeded['case_client']}/generate")
        assert r.status_code == 404, f"客户乙竟然触发了他人案件的生成：{r.text}"
        assert _code(r) == "CASE_NOT_FOUND", r.text

    app_owner = app_factory(seeded["client_a"])
    with _client(app_owner) as c:
        r = c.post(f"/api/v1/analyses/case/{seeded['case_client']}/generate")
        assert r.status_code == 200, f"客户甲反而触发不了自己的案件：{r.text}"


# ═══════════════════════ A4 跨租户编辑（带反向量） ═══════════════════════


def test_update_cross_tenant_is_404_and_writes_nothing(app_factory, seeded, engine):
    """A4：跨租户 `PUT` ⇒ 404，**且对方分析一字未改**。"""
    before = _analysis_snap(engine, seeded["ana_b"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.put(f"/api/v1/analyses/{seeded['ana_b']}",
                  json={"summary": "我改了别人的结论", "legal_analysis": "越权写入"})
        assert r.status_code == 404, r.text
        assert _code(r) == "ANALYSIS_NOT_FOUND", r.text
    assert _analysis_snap(engine, seeded["ana_b"]) == before, "跨租户把对方的分析改了"


# ═══════════════════════ A5/A6 跨租户读版本与决策 ═══════════════════════


def test_versions_cross_tenant_is_404(app_factory, seeded):
    """A5：跨租户读版本历史 ⇒ 404（版本快照含正文，不能外泄）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/analyses/{seeded['ana_b']}/versions")
        assert r.status_code == 404, r.text
        assert _code(r) == "ANALYSIS_NOT_FOUND", r.text


def test_decisions_cross_tenant_is_404(app_factory, seeded):
    """A6：跨租户读 AI 决策审计 ⇒ 404。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/analyses/{seeded['ana_b']}/decisions")
        assert r.status_code == 404, r.text
        assert _code(r) == "ANALYSIS_NOT_FOUND", r.text


# ═══════════════════════ A7/A8 同租户正常路径 ═══════════════════════


def test_same_tenant_update_bumps_version_and_writes_snapshot(
    app_factory, seeded, engine,
):
    """A7：同租户 `PUT` ⇒ 版本 +1、落快照、`changed_by` 是调用者。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.put(f"/api/v1/analyses/{seeded['ana_a']}",
                  json={"summary": "律师改过的结论", "change_note": "核对事实"})
        assert r.status_code == 200, r.text
        assert _data(r)["version"] >= 2, r.text
        assert _data(r)["summary"] == "律师改过的结论", r.text

    from app.models.analysis import CaseAnalysisVersion

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            rows = (await s.execute(
                select(CaseAnalysisVersion)
                .where(CaseAnalysisVersion.analysis_id == seeded["ana_a"])
                .order_by(CaseAnalysisVersion.version.asc())
            )).scalars().all()
            return [(v.version, v.change_note, v.changed_by) for v in rows]

    rows = asyncio.run(_q())
    assert rows, "编辑没落版本快照 ⇒ PRD 的「修改可追溯」是空的"
    assert rows[-1][1] == "核对事实", rows
    assert rows[-1][2] == seeded["a"], f"changed_by 不是调用者：{rows}"


def test_versions_and_decisions_are_readable_by_owner(app_factory, seeded):
    """A8：同租户能取回版本快照与 AI 决策（反向量：守卫不能把功能焊死）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        vs = _data(c.get(f"/api/v1/analyses/{seeded['ana_a']}/versions")) or []
        assert vs, "版本历史为空 ⇒ A7 的快照没落库或读不出来"
        assert vs[-1]["snapshot"]["summary"] == "律师改过的结论", vs

        ds = _data(c.get(f"/api/v1/analyses/{seeded['ana_a']}/decisions")) or []
        assert ds and ds[0]["stage"] == "RETRIEVE", ds


# ═══════════════════════ A9 AST 装配层 ═══════════════════════


def test_every_analysis_id_route_goes_through_the_guard():
    """A9：4 个 `{analysis_id}` 路由全部经过 `_load_or_404`。

    ⚠️ 带**命中数下限自检**（`methodology.md` 89）：端点改名会让 `ast.walk`
    找不到 ⇒ 退化成空集合 ⇒ 判据静默通过。
    """
    targets = ["update_analysis", "iterate_analysis",
               "analysis_decisions", "analysis_versions"]
    hit = [t for t in targets if "_load_or_404" in _func(t)]
    assert len(hit) == 4, f"只有 {hit} 走了守卫 ⇒ 有端点裸奔"


def test_case_scoped_routes_filter_by_ctx_tenant():
    """A9b：`/case/{case_id}` 两条路由必须带 `ctx.tenant_id`。"""
    for name in ("get_case_analysis", "generate_analysis"):
        seg = _func(name)
        assert "ctx.tenant_id" in seg, f"{name} 没用 ctx.tenant_id"


# ═══════════════════════ A10 跨租户迭代 ═══════════════════════


def test_iterate_cross_tenant_is_404(app_factory, seeded):
    """A10：跨租户 `iterate` ⇒ 404，且**守卫在 Copilot 之前**。

    这条不依赖 LLM：`_load_or_404` 在调用 `CaseCopilot.generate` 之前就抛了，
    所以即使后端没配 LLM 也能稳定断言。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/analyses/{seeded['ana_b']}/iterate", json={"note": "再改一版"})
        assert r.status_code == 404, r.text
        assert _code(r) == "ANALYSIS_NOT_FOUND", r.text
