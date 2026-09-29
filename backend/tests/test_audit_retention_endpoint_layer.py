"""审计留存 5 条路由的**端点层**判据（Q-T 清单第七批 · §3.31）。

## 为什么这个文件必须存在

`app/api/v1/audit_retention.py` 的 5 条路由在 `tests/` 下**从未被请求过**。
`tests/test_p1_concurrency_retention.py` 有 8 条**服务层**测试（保留期下限、
dry-run、墓碑、并发），但它们**全部用合规值 `days=180` 调用** ⇒
服务层证明了「配置错了会被抬到 180 天」，**没人问过「请求参数错了会怎样」**。

这一批的风险性质与前面六批完全不同：
- 前面几批是**租户隔离**（读/写别人的东西）；
- 这一批是**合规下限**——`purge` 是**不可逆删除**，删的是**审计日志本身**。
  等保 2.0 三级要求审计记录留存**不少于 6 个月**，而删除接口是唯一能违反它的入口。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| L1 | 5 条路由**首次真实 HTTP 请求**：全员非 500 |
| L2 | 非平台管理员（律师 / 租户管理员）⇒ 403（4 条管理端点**逐条**验） |
| L3 | `/policy` 公开可读，且 `baseline_days == 180` |
| **L4** | **🚨 `purge` 传 `days=1` ⇒ 必须被拒，且一行都不许删**（等保下限） |
| **L5** | **🚨 `days=0` / 负数同样被拒** |
| L6 | 默认 dry-run：不传 `confirm` ⇒ 只统计不删除（反向量） |
| L7 | `confirm=true` + 合规 `days` ⇒ 真的删除，且**墓碑审计被调用** |
| L8 | `archive-then-purge`：dry-run 不删；`confirm` 才删，且返回归档与清理两段 |
| L9 | `AUDIT_RETENTION_ADMIN_ENABLED=false` ⇒ 4 条管理端点**全部**被拒（急停开关） |
| L10 | AST：4 条管理端点都过 `require_roles(PLATFORM_ADMIN)` + `_ensure_enabled()` |

## 已知边界（**不要当成已经覆盖**）

⚠️ **墓碑审计的「落库」这一跳没有端点层判据**：`purge` 用
`log_detached_ctx` 走**独立连接**写墓碑，而那个连接绑的是
`settings.DATABASE_URL`（不是本文件的测试库）⇒ 在本文件里**读不到**。
L7 只能钉到「`log_detached_ctx` **被调用**」这一层，**钉不到「真的写进去了」**。
⇒ 已登记为待补项（见 §6.2）。

⚠️ `archive` 会**真的落盘** gzip 文件 ⇒ 跑这一文件需要允许写
`storage/audit_archive`（沙箱下会假红，与 §3.27 的 `storage/local` 同源）。

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid

import pytest
from sqlalchemy import func, select

SECURITY_BASELINE_DAYS = 180


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"ret_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """平台管理员 1 名 + 律师 1 名；审计日志 5 条过期（400 天前）+ 2 条近期。"""
    import datetime

    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.audit_log import AuditLog
    from app.models.base import Base
    from app.models.enums import UserStatus
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in ("platform", "tenant-a"):
                exists = (await s.execute(
                    select(Tenant).where(Tenant.tenant_id == tid))).scalars().first()
                if not exists:
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            def _user(tag, tid, role):
                return User(
                    username=f"rep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=role,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            admin = _user("platadmin", "platform", Role.PLATFORM_ADMIN)
            lawyer = _user("lawyer", "tenant-a", Role.LAWYER)
            s.add_all([admin, lawyer])
            await s.flush()

            now = datetime.datetime.now(datetime.timezone.utc)
            old = now - datetime.timedelta(days=400)
            rows = []
            for i in range(5):
                rows.append(AuditLog(
                    tenant_id="tenant-a", actor_id=lawyer.id, action="OLD_ACTION",
                    resource_type="case", resource_id=i,
                    created_at=old - datetime.timedelta(days=i), success=1))
            for i in range(2):
                rows.append(AuditLog(
                    tenant_id="tenant-a", actor_id=lawyer.id, action="NEW_ACTION",
                    resource_type="case", resource_id=100 + i,
                    created_at=now, success=1))
            s.add_all(rows)
            await s.commit()

            return {"admin": admin.id, "lawyer": lawyer.id}

    return asyncio.run(_seed())


@pytest.fixture(autouse=True)
def _reset_logs(engine, seeded):
    """每个用例前把审计日志复位成「5 条过期 + 2 条近期」。

    `purge(confirm=true)` 会**真的删行**，不复位的话 L7 跑完 L6 看到的就是
    空库 ⇒「dry-run 没删」这条会**假绿**（本来就没东西可删）。
    """
    import datetime

    from app.models.audit_log import AuditLog

    async def _run() -> None:
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await s.execute(delete(AuditLog))
            await s.flush()
            now = datetime.datetime.now(datetime.timezone.utc)
            old = now - datetime.timedelta(days=400)
            for i in range(5):
                s.add(AuditLog(
                    tenant_id="tenant-a", actor_id=seeded["lawyer"],
                    action="OLD_ACTION", resource_type="case", resource_id=i,
                    created_at=old - datetime.timedelta(days=i), success=1))
            for i in range(2):
                s.add(AuditLog(
                    tenant_id="tenant-a", actor_id=seeded["lawyer"],
                    action="NEW_ACTION", resource_type="case", resource_id=100 + i,
                    created_at=now, success=1))
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


def _count_logs(engine) -> int:
    from app.models.audit_log import AuditLog

    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return (await s.execute(
                select(func.count()).select_from(AuditLog))).scalar()

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.audit_retention as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"audit_retention.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


ADMIN_ROUTES = (
    ("get", "/api/v1/audit/retention/stats"),
    ("post", "/api/v1/audit/retention/archive"),
    ("post", "/api/v1/audit/retention/purge"),
    ("post", "/api/v1/audit/retention/archive-then-purge"),
)


# ═══════════════════════ L1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """L1：5 条路由**第一次**被真实请求——管理员身份下全员非 500。"""
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        assert c.get("/api/v1/audit/retention/stats").status_code == 200
        assert c.get("/api/v1/audit/retention/policy").status_code == 200
        r = c.post("/api/v1/audit/retention/purge", json={"days": 400})
        assert r.status_code == 200, r.text
        assert _data(r)["dry_run"] is True, r.text
        r = c.post("/api/v1/audit/retention/archive", json={"days": 400,
                                                            "out_dir": "_tmp_tests/arc_l1"})
        assert r.status_code == 200, r.text
        r = c.post("/api/v1/audit/retention/archive-then-purge", json={"days": 400})
        assert r.status_code == 200, r.text


# ═══════════════════════ L2 角色门控 ═══════════════════════


def test_non_platform_admin_is_403_on_every_admin_route(app_factory, seeded):
    """L2：4 条管理端点**逐条**验角色——不是只挑一条代表。

    `require_roles` 是**按路由**挂的，漏挂一条就少一条门。
    逐条列出来，将来新增端点忘了挂会立刻红。
    """
    app = app_factory(seeded["lawyer"])
    with _client(app) as c:
        for method, path in ADMIN_ROUTES:
            r = getattr(c, method)(path, json={}) if method == "post" else getattr(c, method)(path)
            assert r.status_code == 403, f"{method.upper()} {path} ⇒ {r.status_code}，{r.text}"


# ═══════════════════════ L3 策略公开 ═══════════════════════


def test_policy_is_public_and_states_baseline(app_factory, seeded):
    """L3：`/policy` 公开可读（合规审查时直接取用），且明确写出 180 天下限。"""
    app = app_factory(seeded["lawyer"])
    with _client(app) as c:
        r = c.get("/api/v1/audit/retention/policy")
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["baseline_days"] == SECURITY_BASELINE_DAYS, d
        assert d["effective_days"] >= SECURITY_BASELINE_DAYS, d


# ═══════════════════════ L4 / L5 保留期下限（**核心**） ═══════════════════════


def test_purge_refuses_days_below_baseline(app_factory, seeded, engine):
    """L4：**本批的核心**。`purge` 传 `days=1` ⇒ 必须被拒，且**一行都不许删**。

    等保 2.0（三级）要求审计记录留存**不少于 6 个月**。
    `retention_days()` 对**配置值**做了下限保护（配 30 天会抬到 180 天），
    但 `cutoff_at(days)` 对**请求参数**是 `days if days is not None else retention_days()`
    ⇒ **请求里传 1 天就按 1 天删**。这是配置侧防护代替不了参数侧防护的典型。

    断言写成「不是 2xx + 行数不变」，不写死 400 还是 422
    （避免把错误语义焊死，见 Q-V 的教训）。
    """
    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/purge",
                   json={"days": 1, "confirm": True})
        assert not (200 <= r.status_code < 300), (
            f"传 days=1 竟然被接受（{r.status_code}）——"
            f"等保要求留存 {SECURITY_BASELINE_DAYS} 天，这个请求能把审计日志清掉：{r.text}")
    assert _count_logs(engine) == before, "低于下限的清理请求删掉了日志"


def test_purge_refuses_zero_and_negative_days(app_factory, seeded, engine):
    """L5：`days=0`（= 全删）与负数同样被拒。

    `days=0` 的 cutoff 就是「此刻」⇒ 等价**清空整张审计表**，
    比 L4 更极端，必须单独钉一条。
    """
    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        for bad in (0, -1, -365):
            r = c.post("/api/v1/audit/retention/purge",
                       json={"days": bad, "confirm": True})
            assert not (200 <= r.status_code < 300), (
                f"days={bad} 竟然被接受（{r.status_code}）：{r.text}")
    assert _count_logs(engine) == before


def test_archive_refuses_days_below_baseline(app_factory, seeded):
    """L5b：`archive` 同样拒绝低于下限的 `days`（同一形状，别只修一个端点）。"""
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/archive", json={"days": 1})
        assert not (200 <= r.status_code < 300), f"archive 接受了 days=1：{r.status_code} {r.text}"


# ═══════════════════════ L6 默认 dry-run ═══════════════════════


def test_purge_defaults_to_dry_run(app_factory, seeded, engine):
    """L6：不传 `confirm` ⇒ 只统计不删除（反向量：行数必须不变）。"""
    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/purge", json={"days": 400})
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["dry_run"] is True, d
        assert d["would_delete"] == 5, d
    assert _count_logs(engine) == before, "dry-run 竟然删了数据"


# ═══════════════════════ L7 确认后真的删 + 墓碑 ═══════════════════════


def test_purge_with_confirm_deletes(app_factory, seeded, engine, monkeypatch):
    """L7：`confirm=true` + 合规 days ⇒ 真的删除，且**墓碑审计被调用**。

    「审计的删除也必须被审计」——墓碑走**独立连接**，绑的是
    `settings.DATABASE_URL` 而不是本文件的测试库 ⇒ **读不到落库结果**。
    所以这一条只钉到「`log_detached_ctx` 被以 `AUDIT_RETENTION_PURGE` 调用」
    这一层（落库那一跳仍是缺口，见模块 docstring 的已知边界）。
    """
    import app.core.audit as audit_mod
    from app.core.audit import AuditAction

    seen: list[tuple[str, str]] = []
    real = audit_mod.log_detached_ctx

    async def _spy(action, resource_type, **kwargs):  # noqa: ANN001
        seen.append((str(getattr(action, "value", action)), resource_type))
        return await real(action, resource_type, **kwargs)

    monkeypatch.setattr(audit_mod, "log_detached_ctx", _spy)

    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/purge",
                   json={"days": 400, "confirm": True})
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["dry_run"] is False, d
        assert d["deleted"] == 5, d
    assert _count_logs(engine) == before - 5, "确认后没有真的删除"
    # `AuditAction` 是 `str` 子类，**没有 `.value`** —— 别按别的枚举写
    assert (str(AuditAction.AUDIT_RETENTION_PURGE), "audit_log") in seen, (
        f"删除审计日志却没有写墓碑：{seen}")


# ═══════════════════════ L8 归档后清理 ═══════════════════════


def test_archive_then_purge_dry_run_deletes_nothing(app_factory, seeded, engine):
    """L8：推荐入口默认也是 dry-run —— 行数不变（反向量）。"""
    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/archive-then-purge", json={"days": 400})
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["archived"] is not None, d
        assert _count_logs(engine) == before, "dry-run 的 archive-then-purge 删了数据"


def test_archive_then_purge_with_confirm_deletes(app_factory, seeded, engine):
    """L8b：显式确认 ⇒ 归档校验通过后真的清理，且返回两段结果。"""
    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/archive-then-purge",
                   json={"days": 400, "confirm": True})
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["archived"]["rows"] == 5, d
        assert d["purged"]["deleted"] == 5, d
    assert _count_logs(engine) == before - 5


def test_archive_then_purge_aborts_when_counts_mismatch(
        app_factory, seeded, engine, monkeypatch):
    """L8c：归档行数与待清理行数**不一致** ⇒ 拒绝清理（宁可保留，不可丢失）。

    ⚠️ 这条是被**注入臂逼出来的**：`retarcnopurge`（把 `if arc["rows"] != pending`
    改成 `if False`）首版跑出来**全绿**——因为数据一致时，有没有这道校验
    **结果完全一样**。这是「防线与数据冗余 ⇒ 判据空转」的又一次出现
    （`methodology.md` 95：必须为每条防线找「只命中它」的探针）。

    如何在 HTTP 层制造不一致：替掉 `AuditRetentionService.archive` 让它报一个
    **假的行数**（等价「归档产物被截断」），其余全部走真实代码路径。
    """
    from app.services.audit_retention import AuditRetentionService

    async def _truncated_archive(self, *, days=None, out_dir=None, batch_size=2000):
        # 假装归档只写出了 2 行（实际待清理 5 行）
        return {"path": "_truncated", "rows": 2, "sha256": "0" * 64,
                "bytes": 1, "cutoff": ""}

    monkeypatch.setattr(AuditRetentionService, "archive", _truncated_archive)

    before = _count_logs(engine)
    app = app_factory(seeded["admin"])
    with _client(app) as c:
        r = c.post("/api/v1/audit/retention/archive-then-purge",
                   json={"days": 400, "confirm": True})
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["purged"] is None, f"行数不一致却执行了清理：{d}"
        assert "中止" in (d.get("note") or ""), d
    assert _count_logs(engine) == before, "行数不一致时仍然清理了审计日志"


# ═══════════════════════ L9 急停开关 ═══════════════════════


def test_admin_switch_off_blocks_every_admin_route(app_factory, seeded, monkeypatch):
    """L9：`AUDIT_RETENTION_ADMIN_ENABLED=false` ⇒ 4 条管理端点**全部**被拒。

    这是运维的「急停」：清理不可逆，需要一个总开关。
    ⚠️ `_ensure_enabled()` 抛的是 `ConfigurationError`（HTTP **500**）——
    语义上「功能被关掉」更像 4xx，但 500 + `CONFIGURATION_ERROR` 能让监控告警，
    属于有意取舍。这条把**现状**钉住；若将来改成 403，这条会红 ⇒ 不会静默漂移。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "AUDIT_RETENTION_ADMIN_ENABLED", False, raising=False)

    app = app_factory(seeded["admin"])
    with _client(app) as c:
        for method, path in ADMIN_ROUTES:
            r = getattr(c, method)(path, json={}) if method == "post" else getattr(c, method)(path)
            assert r.status_code >= 400, (
                f"开关关闭后 {method.upper()} {path} 仍能调用（{r.status_code}）：{r.text}")


# ═══════════════════════ L10 AST 装配判据 ═══════════════════════


def test_every_admin_endpoint_has_role_gate_and_switch():
    """L10：4 条管理端点都必须同时有角色门控与急停开关（带命中下限自检）。

    ⚠️ AST 只能防「守卫被删」，证明不了「守卫拦得住」——
    真正的证明是 L2 / L9 的注入反证。
    """
    for name in ("retention_stats", "retention_archive",
                 "retention_purge", "retention_archive_then_purge"):
        src = _func(name)
        assert "require_roles(Role.PLATFORM_ADMIN)" in src, f"{name} 没有平台管理员门控"
        assert "_ensure_enabled()" in src, f"{name} 没有急停开关"
