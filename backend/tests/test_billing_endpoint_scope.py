"""计费端点的**数据边界**判据（Q-O，2026-09-20）。

## 为什么这个文件必须存在

§3.14 补了 `require_permissions`（**门**）的 21 条判据，但门里面是什么，当时没测：

| 事实 | 实测 |
|------|------|
| `/api/v1/billing*` 在 `tests/` 下的请求次数 | **0** |
| `tests/` 里唯一带 `billing` 的文件 | `test_billing_service.py`——测**服务层**，且把计费服务 monkeypatch 掉了 |
| 4 个路由中带 `{租户数据}` 的 | `dashboard` / `work-orders` / `consume`（`project-revenue` 是纯计算） |

⇒ **「谁能进门」有判据了，「进门后能不能看到别人的账单」还没有。**
本轮补的就是后者 + 一层结构判据（防将来新加的端点漏挂门、或漏带租户）。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| B1 | **AST 结构**：凡传 `tenant_id` 给 `BillingService` 的调用，值必须来自 `ctx.tenant_id` |
| B2 | **AST 结构**：`billing` 下**每个**路由都挂在 `require_permissions` 上（枚举路由，不写死名单） |
| B3 | HTTP **数据边界**：租户 B 的管理员读工单 ⇒ 只看得到 B 的，看不到 A 的 |
| B4 | HTTP：无 `billing:read` 的角色 ⇒ 403（**真实路由级**依赖，不是直接调函数） |
| B5 | **反向量**：有权限的角色 ⇒ 200 且能看到本租户工单（防把端点焊死） |
| B6 | **登记型**：`POST /consume`（**写**操作）由 `billing:read`（**读**权限）放行 |

## 关于 B6

`ROLE_PERMISSIONS` 里**根本没有 `billing:write` 这个权限码**，所以扣减用量这种
写操作只能挂在 read 上。要不要新增 `billing:write` 并重排角色矩阵是**产品决策**
（会影响 FIRM_ADMIN / ENTERPRISE_ADMIN 的实际能力），已登记为 **Q-P**，本轮不擅自改。
这条判据的作用是：将来谁加了 `billing:write` 或改了门控，这里会红，强制回头确认。
"""
from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from sqlalchemy import select

TENANT_A = "billing-scope-a"
TENANT_B = "billing-scope-b"

BILLING_FILE = pathlib.Path(__file__).resolve().parent.parent / "app" / "api" / "v1" / "billing.py"


# ═══════════════════════ B1 / B2 结构判据 ═══════════════════════


def _module() -> ast.Module:
    return ast.parse(BILLING_FILE.read_text(encoding="utf-8"))


def _tenant_id_arg(call: ast.Call) -> ast.expr | None:
    """找出一次 `BillingService(...).xxx(...)` 调用里对应 `tenant_id` 形参的实参。

    ⚠️ 不能只扫关键字实参：实测 `dashboard(ctx.tenant_id, period)` 和
    `list_work_orders(ctx.tenant_id, status=status)` 都是**位置参数**，
    只扫 `tenant_id=` 会命中 1 处（而非 3 处）⇒ 判据静默退化成空集。
    ⇒ 用 `inspect.signature` 把形参名映射到实参位置。
    """
    import inspect

    from app.services.billing_service import BillingService

    fn = call.func
    if not isinstance(fn, ast.Attribute):
        return None
    owner = fn.value
    if not (isinstance(owner, ast.Call) and isinstance(owner.func, ast.Name) and owner.func.id == "BillingService"):
        return None

    method = getattr(BillingService, fn.attr, None)
    if method is None:
        return None
    try:
        params = list(inspect.signature(method).parameters)[1:]  # 去掉 self
    except (TypeError, ValueError):
        return None
    if "tenant_id" not in params:
        return None  # 该方法不按租户取数（如 project_revenue 是纯计算），不参与判定

    for kw in call.keywords:
        if kw.arg == "tenant_id":
            return kw.value
    idx = params.index("tenant_id")
    if idx < len(call.args):
        return call.args[idx]
    return None


def _is_ctx_tenant(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "tenant_id"
        and isinstance(node.value, ast.Name)
        and node.value.id == "ctx"
    )


def test_every_tenant_id_argument_comes_from_context():
    """B1：传给 `BillingService` 的 `tenant_id` 必须来自 `ctx.tenant_id`。

    为什么用 AST 而不是只测一次 HTTP：写死 `settings.DEFAULT_TENANT_ID` 或常量租户的
    端点，在单租户测试环境里**照样返回 200 和看起来正常的数据**，
    只有真到多租户环境才会串号 —— 那时候已经在泄露了。
    """
    bad: list[str] = []
    checked = 0

    for node in ast.walk(_module()):
        if not isinstance(node, ast.Call):
            continue
        arg = _tenant_id_arg(node)
        if arg is None:
            continue
        checked += 1
        if not _is_ctx_tenant(arg):
            bad.append(f"{ast.unparse(node.func)} -> {ast.unparse(arg)}")

    # 自检：形状变了（比如全改成位置参数、或方法改名）会让判据静默变成空集
    assert checked >= 3, (
        f"只定位到 {checked} 处 tenant_id 实参 —— 路由/服务写法可能变了，判据会静默失效"
    )
    assert not bad, f"有 tenant_id 不是来自 ctx：{bad}"


def test_every_billing_route_is_permission_gated():
    """B2：`billing` 下**每个**路由都必须挂在 `require_permissions` 上。

    **枚举路由**而不是写死 `["dashboard", "list_work_orders", ...]`：
    硬编码清单只能防住**今天这几个**端点，将来新加一个漏挂门的计费端点，
    硬编码判据照样全绿。判据要盯的是**性质**（计费路由 ⇒ 必须过权限门）。
    """
    tree = _module()
    routes = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if dec.func.attr not in {"get", "post", "put", "patch", "delete"}:
                continue
            routes.append((node.name, dec))

    assert len(routes) >= 4, f"只枚举到 {len(routes)} 个路由，判据可能已失效"

    ungated = []
    for name, dec in routes:
        blob = ast.unparse(dec)
        if "require_permissions" not in blob:
            ungated.append(name)

    assert not ungated, f"以下计费路由没有权限门控：{ungated}"


# ═══════════════════════ 夹具 ═══════════════════════


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    import app.models  # noqa: F401

    base = pathlib.Path(__file__).resolve().parent.parent / "_tmp_tests"
    base.mkdir(exist_ok=True)
    dbfile = base / f"billing_scope_{uuid.uuid4().hex[:8]}.db"
    return create_async_engine(f"sqlite+aiosqlite:///{dbfile}", echo=False)


@pytest.fixture(scope="module")
def seeded(engine):
    import asyncio

    import app.models  # noqa: F401
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models.base import Base
    from app.models.billing import WorkOrder
    from app.models.enums import UsageType, UserStatus, WorkOrderStatus
    from app.models.identity import Tenant, User

    async def _seed():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            for tid in (TENANT_A, TENANT_B):
                if not (await s.execute(select(Tenant).where(Tenant.tenant_id == tid))).scalars().first():
                    s.add(Tenant(tenant_id=tid, name=f"租户 {tid}"))
            await s.flush()

            ids = {}
            for tid, tag in ((TENANT_A, "a"), (TENANT_B, "b")):
                u = User(
                    username=f"billing-admin-{tag}",
                    hashed_password=hash_password("Str0ngPass!"),
                    full_name=f"{tid} 管理员",
                    role=Role.FIRM_ADMIN,
                    tenant_id=tid,
                    status=UserStatus.ACTIVE,
                    is_active=True,
                )
                s.add(u)
                await s.flush()
                ids[tag] = u.id

                s.add(
                    WorkOrder(
                        order_no=f"WO-{tag}-001",
                        tenant_id=tid,
                        created_by=u.id,
                        usage_type=UsageType.CONTRACT_REVIEW,
                        title=f"{tid} 的工单",
                        status=WorkOrderStatus.PENDING,
                    )
                )

            # 无 billing:read 的角色（LAWYER），同租户 A
            lawyer = User(
                username="billing-lawyer",
                hashed_password=hash_password("Str0ngPass!"),
                full_name="律师",
                role=Role.LAWYER,
                tenant_id=TENANT_A,
                status=UserStatus.ACTIVE,
                is_active=True,
            )
            s.add(lawyer)
            await s.flush()
            ids["lawyer"] = lawyer.id
            await s.commit()
            return ids

    return asyncio.run(_seed())


def _app_with_user(engine, user_id: int):
    """真实 `create_app()` + 只覆盖 `get_db` / `get_current_user`。

    ⚠️ **刻意不覆盖 `get_tenant_context`** —— 与 §3.13 那条发现直接相关：
    覆盖掉它就测不到「租户上下文到底从哪来」，而那正是这次要验的东西。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.deps import get_current_user, get_db
    from app.main import create_app

    factory = async_sessionmaker(engine, expire_on_commit=False)

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
            return (await s.execute(select(User).where(User.id == user_id))).scalars().one()

    app = create_app()
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = _override_user
    return app


# ═══════════════════════ B3–B5 HTTP 层 ═══════════════════════


def test_tenant_b_admin_cannot_see_tenant_a_work_orders(engine, seeded):
    """B3：**数据边界**——租户 B 的管理员读工单，看不到租户 A 的。

    §3.14 判的是「LAWYER 能不能进门」，这条判的是「FIRM_ADMIN 进门后
    会不会顺手把别人的账单也看走」。服务层 `list_work_orders` 有
    `where(WorkOrder.tenant_id == tenant_id)`，但**端点有没有把 ctx.tenant_id 传进去**
    此前没有任何判据（B1 从结构上补，这条从运行时补）。
    """
    from fastapi.testclient import TestClient

    app = _app_with_user(engine, seeded["b"])
    with TestClient(app) as client:
        resp = client.get("/api/v1/billing/work-orders")

    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    body = resp.json()
    blob = str(body)
    assert TENANT_B in blob, f"本租户的工单没返回：{blob[:300]}"
    assert "WO-a-001" not in blob, f"跨租户读到了租户 A 的工单：{blob[:300]}"


def test_role_without_billing_read_is_rejected(engine, seeded):
    """B4：无 `billing:read` 的角色 ⇒ 403，且走的是**真实路由级**依赖。

    §3.14 的 P6 用的是测试专用路由；这条打的是**产品自己的** `/billing/work-orders`，
    补上「billing.py 那 4 处 `dependencies=[...]` 真的挂上了」这一环。
    """
    from fastapi.testclient import TestClient

    app = _app_with_user(engine, seeded["lawyer"])
    with TestClient(app) as client:
        resp = client.get("/api/v1/billing/work-orders")

    assert resp.status_code == 403, (
        f"无 billing:read 却被放行：{resp.status_code} {resp.text[:200]}"
    )


def test_tenant_a_admin_can_read_own_work_orders(engine, seeded):
    """B5：**反向量**——有权限的角色必须能看到**自己**租户的工单。

    没有这条，把 `list_work_orders` 改成一律返回空列表，B3 依然全绿
    （「看不到别人的」满足），而正常功能其实已经废了。
    """
    from fastapi.testclient import TestClient

    app = _app_with_user(engine, seeded["a"])
    with TestClient(app) as client:
        resp = client.get("/api/v1/billing/work-orders")

    assert resp.status_code == 200, f"{resp.status_code} {resp.text[:200]}"
    assert "WO-a-001" in str(resp.json()), "本租户的工单没返回 ⇒ 功能被焊死了"
    assert "WO-b-001" not in str(resp.json()), "读到了租户 B 的工单"


# ═══════════════════════ B6 登记型 ═══════════════════════


def test_consume_write_endpoint_is_gated_by_billing_write():
    """B6（Q-P 已裁定）：`POST /billing/consume`（写）必须按 `billing:write` 放行。

    **现状已改为**：`billing:write` 已落地，仅授予 `FIRM_ADMIN` / `ENTERPRISE_ADMIN`；
    `consume`（写操作）门控由 `billing:read` 升为 `billing:write`。

    ⇒ 这条判据**向前看**：将来谁把门控降回 `billing:read`、或把 `billing:write`
    滥发给非管理员角色（如 LAWYER/只读财务岗），这里会红，强制回头确认。
    """
    from app.core.rbac import ROLE_PERMISSIONS, Role

    all_perms = {p for perms in ROLE_PERMISSIONS.values() for p in perms}
    assert "billing:write" in all_perms, (
        "billing:write 消失了 ⇒ 请确认 Q-P 的裁定是否被正确回滚"
    )

    # billing:write 只能给管理员（FIRM_ADMIN / ENTERPRISE_ADMIN），不得外溢
    holders = {role.value for role, perms in ROLE_PERMISSIONS.items() if "billing:write" in perms}
    assert holders == {Role.FIRM_ADMIN.value, Role.ENTERPRISE_ADMIN.value}, (
        f"billing:write 授予范围超出管理员：{holders}"
    )

    tree = _module()
    consume_gated_by_write = False
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "consume":
            for dec in node.decorator_list:
                if "billing:write" in ast.unparse(dec):
                    consume_gated_by_write = True
    assert consume_gated_by_write, "consume 没有按 billing:write 门控 ⇒ 回头确认 Q-P 落地"
