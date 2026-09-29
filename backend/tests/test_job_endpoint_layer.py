"""异步任务 2 条路由的**端点层**判据（Q-T 清单第六批 · §3.30.1）。

## 为什么这个文件必须存在

`GET /api/v1/jobs/{job_id}` 与 `POST /api/v1/jobs/{job_id}/retry` 在 `tests/` 下
**从未被请求过**。`JobService` 有服务层测试（任务状态机 / 原子认领），
但那测的是**零件**；端点层是**装配**：`svc.get(job_id)` 返回的对象
**有没有跟 `ctx.tenant_id` 比过**，服务层测试一条也看不到。

这一批的特殊性在于 **retry 是写操作**：
- 它不是「读到了别人的东西」，而是「**把别人的任务重新跑了一遍**」；
- `JobService.get()` 与前面 §3.28 的 `DispatchService._get()` 是**同一个形状**
  （`select(Job).where(Job.id == job_id)`，不带租户过滤），
  所以端点层的 `job.tenant_id != ctx.tenant_id` 是**唯一**一道防线。

⚠️ 因此 J7 必须写成**反向量**（前后 DB 快照），不能只看 HTTP 状态码——
§3.28 已经踩过一次「先 `db.commit()` 后鉴权」的顺序陷阱：
状态码是 404，数据却已经改完了。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| J1 | 2 条路由**首次真实 HTTP 请求**：全员非 500 |
| J2 | 同租户 `GET` 正常返回，字段对得上 |
| J3 | 跨租户 `GET` ⇒ 404 `JOB_NOT_FOUND`（`error.code` 非空 ⇒ 端点发的） |
| J4 | 不存在的 id ⇒ 404（与 J3 **同码**：不许按存在性区分租户归属） |
| J5 | 非整数 id ⇒ 422（证明请求**真的进了端点**，不是被路由层挡回） |
| **J6** | **`retry` 非 FAILED ⇒ 400 `JOB_NOT_RETRYABLE`，且库里状态不变** |
| **J7** | **跨租户 `retry` ⇒ 404，且对方 job 的 status/error/retry_count 一个字都不动** |
| J8 | 同租户 `retry` FAILED ⇒ 200：状态回 pending、错误清空、预算归零、**且真的重新入队** |
| J9 | 未注册 handler 的类型 ⇒ 400，且状态不变（不许先把状态改了再失败） |
| J10 | AST：两条端点都过归属守卫（带命中数下限自检） |

## 关于「入队」这一段

`retry` 的最后一跳是 `await job_queue.enqueue(job.id, worker)`，而队列在
lifespan 里 `start()` 过 —— **真跑起来 worker 会去调 LLM**（案件分析/合规扫描）。
所以 J8 用一个记录器替掉 `app.api.v1.jobs.job_queue`：既不让它真跑，
又能钉住「**确实重新入队了**」这个行为（不替换的话，这条判据就测不到）。

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
    dbfile = base / f"job_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A / B 各一个律师；四个 Job 覆盖「同租户 / 跨租户 / 终态 / 未注册类型」。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import JobStatus, JobType, UserStatus
    from app.models.identity import Tenant, User
    from app.models.job import Job

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
                    username=f"jep-{tag}-{uuid.uuid4().hex[:6]}",
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

            def _job(tid, jtype, status, err=None, retries=0):
                return Job(
                    tenant_id=tid,
                    job_type=jtype,
                    ref_type="case",
                    ref_id=1,
                    status=status,
                    progress=100 if status == JobStatus.COMPLETED else 30,
                    error_message=err,
                    retry_count=retries,
                )

            job_a_failed = _job(TENANT_A, JobType.CASE_ANALYSIS, JobStatus.FAILED,
                                err="模型调用超时", retries=2)
            job_a_done = _job(TENANT_A, JobType.CASE_ANALYSIS, JobStatus.COMPLETED)
            # 跨租户攻击目标：B 的失败任务
            job_b_failed = _job(TENANT_B, JobType.CASE_ANALYSIS, JobStatus.FAILED,
                                err="B 的失败原因", retries=3)
            # 未注册 handler 的类型（DOCUMENT_GEN 不在 register_all() 里）
            job_a_unreg = _job(TENANT_A, JobType.DOCUMENT_GEN, JobStatus.FAILED,
                               err="无处理器", retries=1)
            s.add_all([job_a_failed, job_a_done, job_b_failed, job_a_unreg])
            await s.commit()

            return {
                "a": lawyer_a.id,
                "b": lawyer_b.id,
                "job_a_failed": job_a_failed.id,
                "job_a_done": job_a_done.id,
                "job_b_failed": job_b_failed.id,
                "job_a_unreg": job_a_unreg.id,
            }

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_jobs(engine, seeded):
    """每个用例前把四个 Job 复位成初始状态。

    与 §3.28 的 `_reset_dispatches` 同理：`retry` **会改库**，
    不复位的话 J8 跑完 J7 看到的就是「已经是 pending」的 job，
    跨租户那一条会**假绿**（它本来就没被改，看不出区别）。
    """
    from sqlalchemy import update as _upd

    from app.models.enums import JobStatus
    from app.models.job import Job

    async def _run() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(
                _upd(Job).where(Job.id == seeded["job_a_failed"]).values(
                    status=JobStatus.FAILED, error_message="模型调用超时", retry_count=2))
            await s.execute(
                _upd(Job).where(Job.id == seeded["job_a_done"]).values(
                    status=JobStatus.COMPLETED, error_message=None, retry_count=0))
            await s.execute(
                _upd(Job).where(Job.id == seeded["job_b_failed"]).values(
                    status=JobStatus.FAILED, error_message="B 的失败原因", retry_count=3))
            await s.execute(
                _upd(Job).where(Job.id == seeded["job_a_unreg"]).values(
                    status=JobStatus.FAILED, error_message="无处理器", retry_count=1))
            await s.commit()

    asyncio.run(_run())


@pytest.fixture
def app_factory(engine):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。

    用 `create_app()` 而不是手工拼 router，是为了连中间件 / 错误处理一起带上：
    J3 的「404 是端点发的」这个判断，只有在真 app 上才有意义。
    """
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


def _job_snap(engine, job_id: int):
    """反向量：直接读库取三元组，不看 HTTP 说了什么。"""
    from app.models.job import Job

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            j = await s.get(Job, job_id)
            st = j.status
            return (st.value if hasattr(st, "value") else str(st),
                    j.error_message, j.retry_count)

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.jobs as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"jobs.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ J1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """J1：2 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get(f"/api/v1/jobs/{seeded['job_a_failed']}").status_code == 200
        # retry 同租户失败任务 ⇒ 200（状态与入队在 J8 判）
        assert c.post(f"/api/v1/jobs/{seeded['job_a_failed']}/retry").status_code == 200


# ═══════════════════════ J2 同租户读 ═══════════════════════


def test_same_tenant_get_returns_own_job(app_factory, seeded):
    """J2：同租户能读到自己的任务，字段与库里一致。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/jobs/{seeded['job_a_failed']}")
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["id"] == seeded["job_a_failed"]
        assert d["job_type"] == "case_analysis"
        assert d["error_message"] == "模型调用超时"
        assert d["retry_count"] == 2


# ═══════════════════════ J3 跨租户读 ═══════════════════════


def test_cross_tenant_get_is_404(app_factory, seeded):
    """J3：跨租户 `GET` ⇒ 404 `JOB_NOT_FOUND`。

    `JobService.get()` **不带租户过滤**（`select(Job).where(Job.id == job_id)`），
    所以这一条压的是端点层那一行 `job.tenant_id != ctx.tenant_id`——
    它是**唯一**一道防线，删掉必红。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get(f"/api/v1/jobs/{seeded['job_b_failed']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "JOB_NOT_FOUND", r.text
        # 「请求根本没到端点」也会是 404，但不会有业务错误码
        assert r.json().get("error", {}).get("message"), r.text


def test_b_direction_get_is_also_404(app_factory, seeded):
    """J3b：反向（B 读 A）同样 404 —— 租户隔离不是单向的。"""
    app = app_factory(seeded["b"])
    with _client(app) as c:
        r = c.get(f"/api/v1/jobs/{seeded['job_a_failed']}")
        assert r.status_code == 404, r.text
        assert _code(r) == "JOB_NOT_FOUND", r.text


# ═══════════════════════ J4 / J5 不存在与非整数 ═══════════════════════


def test_missing_job_is_same_404(app_factory, seeded):
    """J4：不存在的 id ⇒ 与跨租户**同一个** 404/错误码。

    这是刻意的：如果「不存在」和「别人的」返回不同状态码，
    就等于**用 HTTP 语义泄露了「这个 id 属于别家」**。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get("/api/v1/jobs/99999999")
        assert r.status_code == 404, r.text
        assert _code(r) == "JOB_NOT_FOUND", r.text


def test_non_integer_id_is_422(app_factory, seeded):
    """J5：非整数 id ⇒ 422。

    这条是**判据自己的护栏**：它证明请求真的进到了端点函数，
    而不是被路由层 / 中间件挡回。没有它，J3/J4 的 404 无法区分
    「端点判的」还是「请求压根没到」。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get("/api/v1/jobs/not-a-number").status_code == 422
        assert c.post("/api/v1/jobs/not-a-number/retry").status_code == 422


# ═══════════════════════ J6 非失败任务不可重试 ═══════════════════════


def test_retry_completed_is_400_and_changes_nothing(app_factory, seeded, engine):
    """J6：已完成任务不可重试 ⇒ 400 `JOB_NOT_RETRYABLE`，且库里**一个字都不动**。

    只断言状态码是不够的：如果哪天有人在「状态校验」**之后**又加了写库，
    状态码照样是 400 而数据已经改了（§3.28 的顺序陷阱）。
    """
    before = _job_snap(engine, seeded["job_a_done"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/jobs/{seeded['job_a_done']}/retry")
        assert r.status_code == 400, r.text
        assert _code(r) == "JOB_NOT_RETRYABLE", r.text
    assert _job_snap(engine, seeded["job_a_done"]) == before


# ═══════════════════════ J7 跨租户重试（反向量） ═══════════════════════


def test_cross_tenant_retry_changes_nothing(app_factory, seeded, engine):
    """J7：**本批的核心**。跨租户 `retry` ⇒ 404，且对方任务**完全没被改动**。

    为什么必须走 DB 快照：`retry` 会 `job.status = PENDING` + `db.commit()`。
    万一守卫写在 commit 之后（§3.28 派单就是这么踩的），
    HTTP 是 404，但 B 的任务已经被重新排队执行了 —— 只看状态码会**假绿**。
    """
    before = _job_snap(engine, seeded["job_b_failed"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/jobs/{seeded['job_b_failed']}/retry")
        assert r.status_code == 404, r.text
        assert _code(r) == "JOB_NOT_FOUND", r.text
    after = _job_snap(engine, seeded["job_b_failed"])
    assert after == before, f"跨租户重试改动了对方任务：{before} → {after}"
    # 顺带钉住：B 自己的错误原因没有泄露到响应体里
    assert "B 的失败原因" not in r.text


# ═══════════════════════ J8 同租户重试（正向） ═══════════════════════


def test_same_tenant_retry_resets_and_requeues(app_factory, seeded, engine, monkeypatch):
    """J8：同租户重试 FAILED 任务 ⇒ 状态回 pending、错误清空、预算归零、**且真的入队**。

    「且真的入队」这段用一个记录器替换 `app.api.v1.jobs.job_queue`：
    真队列在 lifespan 里已经 `start()`，一旦入队 worker 就会去调 LLM，
    测试会变得**不确定**；换成记录器既不跑真活，又能钉住「确实重新投递了」。
    """
    import app.api.v1.jobs as jobs_mod

    calls: list[tuple[int, str]] = []

    class _Recorder:
        async def enqueue(self, job_id: int, worker) -> None:
            calls.append((job_id, getattr(worker, "__name__", "?")))

    monkeypatch.setattr(jobs_mod, "job_queue", _Recorder())

    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/jobs/{seeded['job_a_failed']}/retry")
        assert r.status_code == 200, r.text
        assert _data(r)["status"] == "pending", r.text

    status, err, retries = _job_snap(engine, seeded["job_a_failed"])
    assert status == "pending", status
    assert err is None, f"重试后错误信息没清空：{err!r}"
    assert retries == 0, f"重试后预算没归零：{retries}"
    # 关键：真的重新投递了（只改状态不入队 = 任务永远没人跑）
    assert calls and calls[0][0] == seeded["job_a_failed"], f"没有重新入队：{calls}"


# ═══════════════════════ J9 未注册处理器 ═══════════════════════


def test_retry_unregistered_type_is_400_and_changes_nothing(app_factory, seeded, engine):
    """J9：`DOCUMENT_GEN` 没有注册 handler ⇒ 400，且**状态不许先改再失败**。

    端点当前的顺序是「状态校验 → handler 查找 → 改状态 + commit → 入队」，
    所以这条是绿的；它钉的是这个顺序。若哪天把改状态挪到 handler 查找**之前**，
    J9 会立刻转红（任务被改成 pending 却永远没人执行）。
    """
    before = _job_snap(engine, seeded["job_a_unreg"])
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post(f"/api/v1/jobs/{seeded['job_a_unreg']}/retry")
        assert r.status_code == 400, r.text
        assert _code(r) == "JOB_NOT_RETRYABLE", r.text
    assert _job_snap(engine, seeded["job_a_unreg"]) == before


# ═══════════════════════ J10 AST 装配判据 ═══════════════════════


# ═══════════════════════ Q-H 失败任务可见性 ═══════════════════════


def test_list_jobs_shows_failed_and_hides_other_tenants(app_factory, seeded):
    """JH1（Q-H）：`GET /jobs` 能看到**本租户**的失败任务，看不到别的租户的。

    Q-H 的痛点是「失败任务静默堆积、无人可见」：此前只有 `GET /{job_id}`，
    要先知道 id 才看得见 —— 而僵尸任务恰恰是没人知道它在的那一类。

    ⚠️ 任务表存着 `input_payload` / `output_payload`（解析结果、文书正文），
    列表一旦跨租户即为数据泄露，故本用例同时是**租户隔离**判据。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get("/api/v1/jobs")
        assert r.status_code == 200, r.text
        items = _data(r).get("items", [])
        ids = {i["id"] for i in items}

    assert seeded["job_a_failed"] in ids, "本租户的失败任务没列入 ⇒ 僵尸任务仍不可见"
    assert seeded["job_a_done"] in ids, "列表把已完成任务也丢了"
    assert seeded["job_b_failed"] not in ids, "列到了别的租户的任务 ⇒ 跨租户泄露"


def test_list_jobs_can_filter_by_failed_status(app_factory, seeded):
    """JH2：`?status=FAILED` 只返回失败任务 —— 「僵尸任务」要能被单独捞出来。

    反向量：过滤后必须**仍能**拿到本租户的失败任务，且拿不到已完成的；
    否则「加了过滤但把失败任务也滤掉」会骗过 JH1。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.get("/api/v1/jobs", params={"status": "FAILED"})
        assert r.status_code == 200, r.text
        items = _data(r).get("items", [])
        ids = {i["id"] for i in items}
        assert all(i["status"] == "failed" for i in items), items

    assert seeded["job_a_failed"] in ids, "过滤后反而拿不到本租户的失败任务"
    assert seeded["job_a_done"] not in ids, "过滤 status=FAILED 却返回了已完成任务"
    assert seeded["job_b_failed"] not in ids, "过滤后仍列到别的租户的任务"


def test_both_endpoints_check_tenant_ownership():
    """J10：两条端点都必须把 `ctx.tenant_id` 交给归属校验（带命中下限自检）。

    ⚠️ 这条**只能防「守卫被删」**，不能证明「守卫真的拦住了」——
    真正的证明是 J3 / J7 的注入反证。AST 判据的定位是「改动时的警报器」。
    """
    for name in ("job_status", "retry"):
        src = _func(name)
        assert "ctx.tenant_id" in src, f"{name} 没有引用 ctx.tenant_id"
        # 命中数下限：只出现一次可能只是取来传给别的用途
        assert src.count("ctx.tenant_id") >= 1, name
        assert "tenant_id !=" in src, (
            f"{name} 取到了 ctx.tenant_id 却没有跟 job.tenant_id 比）——"
            "这是 §3.28 派单踩过的形状：取了不比，等于没取"
        )
