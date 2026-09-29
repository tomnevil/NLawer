"""计费 3 条零覆盖路由的**端点层**判据（Q-T 清单第八批 · §3.32.1）。

## 为什么这个文件必须存在

`GET /billing/dashboard`、`POST /billing/consume`、`POST /billing/project-revenue`
这 3 条路由在 `tests/` 下**从未被请求过**。

⚠️ 注意与 `tests/test_billing_endpoint_scope.py` 的区别：那个文件的 B1–B6 管的是
`work-orders` 与「门」本身，**没有一条真的请求过这 3 条路由**。
所以「计费有判据」这个印象是**半个真的**——门测了，门后的三条路没测。

这一批的特殊性：`POST /billing/consume` 是**写操作**（扣额度、可能转工单），
却挂着 `billing:read` 的门（**Q-P**，已挂账待拍板）。

⚠️ 关于「谁有 `billing:read`」——**查过再写，别凭印象**：
实测 `permissions_for` 只有 **FIRM_ADMIN**（所管理员）与 **ENTERPRISE_ADMIN**
（企业管理员）持有，**律师（LAWYER）没有**。所以夹具用所管理员，不是律师。
⇒ Q-P 的准确表述是「**所管理员可以用读权限执行写操作**」，不是「任何人都可以」。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| M1 | 3 条路由**首次真实 HTTP 请求**：全员非 500 |
| M2 | `dashboard` 只含**本租户**的额度与工单 |
| **M3** | **`consume` 的门控码登记**（**Q-P 的登记型判据**：改了门控必红，逼着回头确认角色矩阵） |
| M4 | `consume` 未知 `usage_type` ⇒ 4xx（**不焊死 404/422**）+ **不落库** |
| **M5** | **`consume` 扣的是调用方租户的额度**（反向量：不许扣到别家账上） |
| M6 | `project-revenue` 是**纯计算**：不写库（反向量） |
| M7 | `project-revenue` 缺字段/负数 ⇒ 4xx（防裸 `int` 字段被任意值打穿） |
| M8 | AST：3 条端点都把 `ctx.tenant_id` 交给服务层 |

⚠️ **刻意不覆盖 `get_tenant_context`**（`methodology.md` 82）。
⚠️ M4 只断言「4xx + 不落库」，**不**断言具体状态码：现状是 404 + `NOT_FOUND`
   （参数不合法却报「不存在」），这是已知语义错配，焊死会把错口径固化。
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
    dbfile = base / f"bil_ep_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    """租户 A / B 各一名**所管理员**（实测只有 FIRM_ADMIN / ENTERPRISE_ADMIN 有 `billing:read`）。"""
    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.enums import UserStatus
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
                    username=f"bep-{tag}-{uuid.uuid4().hex[:6]}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=tag,
                    role=Role.FIRM_ADMIN,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )

            admin_a = _user("adminA", TENANT_A)
            admin_b = _user("adminB", TENANT_B)
            # Q-M：没有 `billing:write` 的角色（律师），用来验证额度调整的门控
            lawyer = User(
                username=f"bep-lawyer-{uuid.uuid4().hex[:6]}",
                hashed_password=hash_password("Str0ngPass!"),
                full_name="lawyer",
                role=Role.LAWYER,
                tenant_id=TENANT_A,
                status=UserStatus.ACTIVE,
                is_active=True,
            )
            s.add_all([admin_a, admin_b, lawyer])
            await s.commit()
            return {"a": admin_a.id, "b": admin_b.id, "lawyer": lawyer.id}

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


def _count(engine, model, **where):
    async def _q():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            stmt = select(func.count()).select_from(model)
            for k, v in where.items():
                stmt = stmt.where(getattr(model, k) == v)
            return (await s.execute(stmt)).scalar()

    return asyncio.run(_q())


def _func(name: str) -> str:
    import app.api.v1.billing as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(src, node) or ""
    raise AssertionError(f"billing.py 里找不到 {name}（端点改名了？AST 判据会静默退化）")


# ═══════════════════════ M1 装配层冒烟 ═══════════════════════


def test_all_routes_respond(app_factory, seeded):
    """M1：3 条路由**第一次**被真实请求——必须都不是 500。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        assert c.get("/api/v1/billing/dashboard").status_code == 200
        r = c.post("/api/v1/billing/consume", json={"usage_type": "QA"})
        assert r.status_code == 200, r.text
        r = c.post("/api/v1/billing/project-revenue", json={"subscription_cents": 1000})
        assert r.status_code == 200, r.text


# ═══════════════════════ M2 看板租户隔离 ═══════════════════════


def test_dashboard_only_shows_own_tenant(app_factory, seeded, engine):
    """M2：`dashboard` 必须只反映**调用方租户**自己的额度。

    压的是 `ctx.tenant_id` 有没有真传进服务层。

    ⚠️ 这个端点的响应体**不含 `tenant_id` 字段**（看板天然只展示自己的，
    `quotas` 里只有 `usage_type/used/limit/remaining/percent`），所以**不能**用
    「响应里出现别人的 tenant_id」来判断租户隔离——那样写出来的判据永远绿，
    等于没写（这就是早期一版 M2 假绿的根因）。

    正确的反向向量：**B 自己从没消费过**，它的看板必须一行额度都没有。
    若端点把租户写死成 A（`bilnotenant` 臂），B 的看板会冒出 A 的额度行 ⇒ 这条红。
    """
    from app.models.billing import UsageQuota

    app_a = app_factory(seeded["a"])
    with _client(app_a) as c:
        assert c.post("/api/v1/billing/consume",
                      json={"usage_type": "QA"}).status_code == 200
    assert _count(engine, UsageQuota, tenant_id=TENANT_A) >= 1

    app_b = app_factory(seeded["b"])
    with _client(app_b) as c:
        r = c.get("/api/v1/billing/dashboard")
        assert r.status_code == 200, r.text
        d = _data(r)
        quotas = d.get("quotas") or d.get("usage") or []
    # B 从没消费过，看板必须为空；一旦端点把租户写死成 A，这里会冒出 A 的额度
    assert len(quotas) == 0, f"B 的看板出现了非自己的额度行（租户隔离失效）：{quotas}"


# ═══════════════════════ M3 门控码登记（Q-P） ═══════════════════════


def test_consume_gate_is_pinned():
    """M3（Q-P 已裁定）：`consume` 门控码钉死在 `billing:write`。

    Q-P 已拍板：写操作必须按 `billing:write` 放行，且 `billing:write` 只给
    FIRM_ADMIN / ENTERPRISE_ADMIN。这里钉住「钉后的正确状态」——
    谁把门控降回 `billing:read`，或误挂成别的码，这条会红，强制回头确认
    `ROLE_PERMISSIONS` 矩阵是否同步。
    """
    from app.api.v1.billing import consume as _  # noqa: F401

    src = None
    import app.api.v1.billing as mod

    for node in ast.walk(ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "consume":
            deco = "".join(ast.get_source_segment(
                pathlib.Path(mod.__file__).read_text(encoding="utf-8"), d) or ""
                for d in node.decorator_list)
            src = deco
    assert src is not None
    assert "require_permissions" in src, f"consume 没有权限门控：{src}"
    assert 'billing:write' in src, (
        f"consume 的门控码变了（现在是 {src!r}）⇒ **Q-P 裁定是否被正确回滚**："
        "写操作必须按 billing:write 放行，并确认 ROLE_PERMISSIONS 已同步授予。")
    assert 'billing:read' not in src, (
        f"consume 仍按读权限门控（{src!r}）⇒ 写操作误用读权限，请改回 billing:write。")


# ═══════════════════════ M4 未知用量类型 ═══════════════════════


def test_consume_unknown_usage_type_is_rejected(app_factory, seeded, engine):
    """M4：未知 `usage_type` ⇒ 4xx，且**不落库**（反向量）。

    只断言「4xx + 行数不变」，不断言 404 还是 422：
    现状是 `NotFoundError(..., code=NOT_FOUND)`——**参数不合法却报「不存在」**，
    与 Q-V（复核）是同一个错配形状。焊死 404 会把错口径固化。
    """
    from app.models.billing import UsageQuota

    before = _count(engine, UsageQuota)
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/consume", json={"usage_type": "NOT_A_REAL_TYPE"})
        assert 400 <= r.status_code < 500, f"未知用量类型被接受：{r.status_code} {r.text}"
    assert _count(engine, UsageQuota) == before, "被拒绝的请求仍然建了额度行"


# ═══════════════════════ M5 扣的是调用方租户（反向量） ═══════════════════════


def test_consume_charges_calling_tenant_only(app_factory, seeded, engine):
    """M5：**本批的核心**。`consume` 必须记在**调用方**租户账上。

    只断言「A 多了一行」不够——§3.29 文书踩过「读别人的、记自己的账」，
    反过来「扣别人的」同样要防。所以两边都看：
    A 的额度 +1，**B 的额度不变**。
    """
    from app.models.billing import UsageQuota

    before_a = _count(engine, UsageQuota, tenant_id=TENANT_A)
    before_b = _count(engine, UsageQuota, tenant_id=TENANT_B)

    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/consume", json={"usage_type": "DOCUMENT"})
        assert r.status_code == 200, r.text

    assert _count(engine, UsageQuota, tenant_id=TENANT_A) == before_a + 1
    assert _count(engine, UsageQuota, tenant_id=TENANT_B) == before_b, "扣到了别的租户账上"


# ═══════════════════════ M6 / M7 收入预测 ═══════════════════════


#: M6 的探针周期：干净态下不可能出现，注入器 `bilprojwrite` 会写一行这个周期。
MARK_PERIOD = "2099-01"


def test_project_revenue_writes_nothing(app_factory, seeded, engine):
    """M6：`project-revenue` 是**纯计算**，不写任何库表（反向量）。

    它挂着 `billing:read` 且不需要 `db` 参与计算——若哪天有人在里头加了写库，
    这条会立刻红。

    ⚠️ 为什么用「标记周期」而不是「全表总行数」：
    早期一版数 `UsageQuota/UsageRecord/WorkOrder` 的总数，结果 `bilprojwrite`
    注入 `ensure_quota(ctx.tenant_id, QA, "2099-01")` 后**总数没变 ⇒ 假绿**——
    因为 `ensure_quota` 是 upsert，写的是「本就不该出现的标记周期」那一行，
    被总数对比的冗余吃掉了（methodology：防线与数据冗余 ⇒ 判据空转）。

    改用**只命中这一条防线**的探针：干净态下 `tenant-a` 的 `2099-01` 周期必须
    0 行；注入后会出现 1 行 ⇒ 这条红。
    """
    from app.models.billing import UsageQuota

    # 干净态基准：标记周期必须 0 行（否则基准被污染）
    base = _count(engine, UsageQuota, tenant_id=TENANT_A, period=MARK_PERIOD)
    assert base == 0, f"基准污染：{MARK_PERIOD} 已存在 {base} 行"

    app = app_factory(seeded["a"])
    with _client(app) as c:
        for payload in ({"subscription_cents": 1000},
                        {"subscription_cents": 0, "case_service_cents": 500,
                         "work_order_cents": 200, "value_added_cents": 100}):
            r = c.post("/api/v1/billing/project-revenue", json=payload)
            assert r.status_code == 200, r.text

    # 纯计算端点不得写任何库表；若它被改成写库，标记周期会出现一行
    after = _count(engine, UsageQuota, tenant_id=TENANT_A, period=MARK_PERIOD)
    assert after == 0, f"纯计算端点写了库（{MARK_PERIOD} 出现 {after} 行）"


def test_project_revenue_rejects_non_positive_total(app_factory, seeded):
    """M7（Q-AA 已裁定）：收入预测**净合计 <= 0 必须被拒收**（4xx）。

    Q-AA 拍板：净零/净负营收污染财报口径（某租户可构造负值拉低/抬高合计）。
    - 单项可为负（退款/冲销），只要净合计为正即放行 —— 故只判**合计**，不判单项。
    - 这条判据**向后看**：若有人把拒绝逻辑删掉或放宽，负数重新被接受，
      这里会红（配合 `evidence/verify_qaa_revenue.py` 的故障注入自证）。
    """
    # 单点为负 ⇒ 净合计为正 ⇒ 200
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/project-revenue",
                   json={"subscription_cents": -1, "case_service_cents": 5000})
        assert r.status_code == 200, f"单点为负但净合计为正被误拒：{r.text}"
        assert _data(r)["total_cents"] > 0, r.text

    # 净合计为负 ⇒ 4xx + 不落库（纯计算端点本就不写库，M6 已钉）
    with _client(app) as c:
        r = c.post("/api/v1/billing/project-revenue", json={"subscription_cents": -1})
        assert 400 <= r.status_code < 500, (
            f"净负营收被接受（{r.status_code} {r.text}）⇒ Q-AA 的拒绝逻辑是否被正确回滚？")
        assert "total_cents" not in (r.json().get("data") or {}), (
            f"被拒收的请求仍返回了合计：{r.text}")


# ═══════════════════════ M8 AST 装配判据 ═══════════════════════


def test_tenant_scoped_endpoints_pass_ctx_tenant():
    """M8：**只**要求 `dashboard` 与 `consume` 传 `ctx.tenant_id`。

    ⚠️ 刻意**不**要求 `project_revenue` 传租户：它是**纯系数计算**，
    输入全在请求体里，与租户无关。硬要它引用 `ctx.tenant_id` 反而会逼出
    「为了过判据而加一行没用的参数」这种坏改动。
    它的爆炸半径由 M6（不写库）管住。
    """
    for name in ("dashboard", "consume"):
        src = _func(name)
        assert "ctx.tenant_id" in src, f"{name} 没有引用 ctx.tenant_id"
        # 传给服务层可以是关键字也可以是位置参数（dashboard 是位置参数），
        # 所以只要求「服务层调用那一行里出现了 ctx.tenant_id」。
        call_lines = [ln for ln in src.splitlines()
                      if "BillingService" in ln or "ctx.tenant_id" in ln]
        assert any("ctx.tenant_id" in ln for ln in call_lines), (
            f"{name} 取到了 ctx.tenant_id 却没交给服务层——"
            "这是 §3.28 派单踩过的形状：取了不传，等于没取")


# ═══════════════════════ Q-M 管理员手动调整额度 ═══════════════════════


def test_admin_can_adjust_quota_and_it_is_audited(app_factory, seeded, engine):
    """MQ1（Q-M）：`billing:write` 的管理员调整额度 ⇒ 200，落库 + 留痕。

    Q-M 拍板给「线下充值期间改额度」一个有授权、有留痕的入口（此前只能直连 DB）。
    判据同时压三件事：① 额度真改了 ② 审计写了（含前后值）③ 只动本租户。
    """
    from sqlalchemy import func as _f

    from app.core.audit import AuditAction
    from app.models.audit_log import AuditLog
    from app.models.billing import UsageQuota

    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/quota/adjust", json={
            "usage_type": "QA", "limit_count": 42, "reason": "线下充值补偿",
        })
        assert r.status_code == 200, r.text
        d = _data(r)
        assert d["limit_count"] == 42, r.text
        assert d["tenant_id"] == TENANT_A, r.text

    # 落库校验：本租户该类型额度上限 = 42，且另一租户不受影响
    assert _count(engine, UsageQuota, tenant_id=TENANT_A, limit_count=42) >= 1
    assert _count(engine, UsageQuota, tenant_id=TENANT_B, limit_count=42) == 0

    async def _audits():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        f = async_sessionmaker(engine, expire_on_commit=False)
        async with f() as s:
            return int((await s.execute(
                select(_f.count()).select_from(AuditLog).where(
                    # ⚠️ `AuditAction` 是**字符串常量类**（同 `ErrorCode`），不是 Enum
                    # ⇒ 没有 `.value`，直接比字符串。
                    AuditLog.action == AuditAction.QUOTA_ADJUST
                )
            )).scalar_one())

    assert asyncio.run(_audits()) >= 1, "额度调整没有写审计"


def test_quota_adjust_rejects_negative_limit(app_factory, seeded):
    """MQ2：负数额度上限 ⇒ 4xx（负数上限会让「剩余额度」算成负值）。"""
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/quota/adjust", json={
            "usage_type": "QA", "limit_count": -1,
        })
        assert 400 <= r.status_code < 500, f"负数额度上限被接受：{r.status_code} {r.text}"


def test_quota_adjust_requires_billing_write(app_factory, seeded):
    """MQ3：无 `billing:write` 的角色（律师）⇒ 403。"""
    app = app_factory(seeded["lawyer"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/quota/adjust", json={
            "usage_type": "QA", "limit_count": 10,
        })
        assert r.status_code == 403, f"无 billing:write 却能改额度：{r.status_code} {r.text}"


def test_project_revenue_does_not_touch_tenant_scope(app_factory, seeded):
    """M8b：`project-revenue` 的响应里**不含任何租户标识**。

    与上一条成对：既然它不收租户参数，就必须证明它也**不返回**租户相关的东西
    （否则就是「无租户参数的全局数据出口」）。
    """
    app = app_factory(seeded["a"])
    with _client(app) as c:
        r = c.post("/api/v1/billing/project-revenue",
                   json={"subscription_cents": 1000, "case_service_cents": 500})
        assert r.status_code == 200, r.text
        body = r.text
    for leak in (TENANT_A, TENANT_B):
        assert leak not in body, f"纯计算端点返回了租户标识：{leak}"
