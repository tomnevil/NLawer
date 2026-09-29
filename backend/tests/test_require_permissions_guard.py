"""路由级权限门控 `require_permissions` / `require_roles` 的判据（2026-09-20）。

## 为什么这个文件必须存在

在第十五轮「已修复 ≠ 有判据」清查里发现：

- `require_permissions` 在 `tests/` 下 **0 命中**；
- 它唯一的实际使用点是 `app/api/v1/billing.py` 的 4 处**路由级** `dependencies=[...]`；
- `tests/` 下 **没有任何用例请求过 `/api/v1/billing*`**（只有 `test_billing_service.py`
  测服务层，还把计费服务 monkeypatch 掉了）；
- `tests/` 里 `dependency_overrides` 覆盖的键只有 `get_db` / `get_current_user` /
  `get_tenant_context`，**从不覆盖 `require_permissions`** —— 也就是说它既没被直接测，
  也没被间接绕过式地测。

⇒ **这个门控函数在全量回归里一次都没被执行过。** 把 `if not ok:` 那一分支删掉
（即「任何登录用户都能读计费」），全量回归会**全绿**。

这不是假设：本文件的 P1–P7 就是用来把这条性质钉住的判据，且已用故障注入验证
（见 `round15b-...md` §5.2 的 Q-P 行）。

## 覆盖清单

| 编号 | 性质 |
|------|------|
| P1 | `mode="all"`：权限齐全 ⇒ 放行 |
| P2 | `mode="all"`：缺一条 ⇒ 403（**参数化 4 个角色**，防"只挡了某个角色"） |
| P3 | `mode="any"`：命中一条 ⇒ 放行 |
| P4 | `mode="any"`：一条都不命中 ⇒ 403 |
| P5 | `PLATFORM_ADMIN` 的 `*` 通配 ⇒ 放行（反向量：加固不能把超管挡在外面） |
| P6 | HTTP 层：真实 `create_app()` + 路由级 `dependencies` ⇒ 无权限 403 |
| P7 | HTTP 层反向量：有权限 ⇒ 200（防"把门焊死"） |
| P8 | 空权限码清单 ⇒ **工厂阶段** ValueError（防 `all([])` 恒真把端点公开） |
| P9 | `mode` 写错 ⇒ 工厂阶段 ValueError（防静默退化成 `any`） |
| R1 | `require_roles`：允许的角色 ⇒ 放行 |
| R2 | `require_roles`：不允许的角色 ⇒ 403 |
| R3 | `require_roles()` 空清单 ⇒ 工厂阶段 ValueError |
| W1 | 登记：`deps.require_roles` 与 `rbac.require_roles` 同名不同义（**已知隐患**） |
"""
from __future__ import annotations

import pytest

from app.core.deps import require_permissions, require_roles
from app.core.rbac import Role

# ── 事实基线（与 app/core/rbac.py 的 ROLE_PERMISSIONS 对齐）───────────────
# billing:read ∈ {FIRM_ADMIN, ENTERPRISE_ADMIN}；case:read ∈ {FIRM_ADMIN, LAWYER, ASSISTANT}
HAS_BILLING = (Role.FIRM_ADMIN, Role.ENTERPRISE_ADMIN)
NO_BILLING = (Role.LAWYER, Role.ASSISTANT, Role.CLIENT, Role.ENTERPRISE_USER)

PERM_DENIED_STATUS = 403


def _user(role: Role):
    """构造一个**不落库**的用户对象：门控只用到 role / tenant_id / id。

    ⚠️ 必须走正常构造器 `User(...)`：用 `User.__new__(User)` 会绕过 SQLAlchemy
    的 instrumentation，`u.id = 1` 直接抛 `AttributeError: 'NoneType' has no
    attribute 'set'`（这是实测撞到的，不是推测）。
    """
    from app.models.enums import UserStatus
    from app.models.identity import User

    return User(
        username=f"perm-{role.value.lower()}",
        hashed_password="x",
        role=role,
        tenant_id="perm-guard-t1",
        status=UserStatus.ACTIVE,
        is_active=True,
    )


def _ctx(role: Role):
    from app.core.deps import TenantContext

    return TenantContext(tenant_id="perm-guard-t1", user_id=1, role=role)


async def _call(dep, role: Role):
    """直接调用依赖函数（绕过 FastAPI 注入，只测门控逻辑本身）。"""
    return await dep(user=_user(role), ctx=_ctx(role))


def _is_denied(excinfo) -> bool:
    err = excinfo.value
    return getattr(err, "status_code", None) == PERM_DENIED_STATUS


# ═══════════════════════ P1–P5 门控函数层 ═══════════════════════


async def test_all_mode_passes_when_every_permission_present():
    """P1：mode="all" 且权限齐全 ⇒ 返回 ctx，不抛错。"""
    dep = require_permissions("billing:read", "case:read", mode="all")
    ctx = await _call(dep, Role.FIRM_ADMIN)
    assert ctx.tenant_id == "perm-guard-t1"


@pytest.mark.parametrize("role", NO_BILLING)
async def test_all_mode_denies_when_any_permission_missing(role):
    """P2：mode="all" 缺任意一条 ⇒ 403。

    参数化成 4 个角色，是为了防住「只把某个角色挡住了」的写法——
    若判据只测 CLIENT，那么「LAWYER 能读计费」这种漏法照样全绿。
    """
    dep = require_permissions("billing:read", "case:read", mode="all")
    with pytest.raises(Exception) as ei:
        await _call(dep, role)
    assert _is_denied(ei), f"{role.value} 缺 billing:read 却放行了"


async def test_any_mode_passes_when_one_permission_present():
    """P3：mode="any" 命中一条 ⇒ 放行。"""
    dep = require_permissions("billing:read", "case:read", mode="any")
    # LAWYER 有 case:read 但没有 billing:read
    ctx = await _call(dep, Role.LAWYER)
    assert ctx.role == Role.LAWYER


@pytest.mark.parametrize("role", (Role.CLIENT, Role.ENTERPRISE_USER))
async def test_any_mode_denies_when_none_present(role):
    """P4：mode="any" 一条都不命中 ⇒ 403。"""
    dep = require_permissions("billing:read", "case:read", mode="any")
    with pytest.raises(Exception) as ei:
        await _call(dep, role)
    assert _is_denied(ei), f"{role.value} 一条权限都没有却放行了"


async def test_platform_admin_wildcard_still_passes():
    """P5：PLATFORM_ADMIN 的 `*` 通配必须仍然放行。

    **反向量**：这条存在是为了防「过度加固」。若有人把 `has_permission` 的通配
    分支删掉，P1–P4 仍全绿（因为普通角色行为没变），只有这条会红。
    """
    dep = require_permissions("billing:read", "whatever:nonexistent", mode="all")
    ctx = await _call(dep, Role.PLATFORM_ADMIN)
    assert ctx.role == Role.PLATFORM_ADMIN


# ═══════════════════════ P6–P7 HTTP 层 ═══════════════════════


def _build_app():
    """真实 `create_app()` + 一个**测试专用**路由，门控写在路由级 dependencies 上。

    刻意复刻 `billing.py` 的挂载形状（`APIRouter(dependencies=[...])`），
    因为门控没被直接调用过——历史上这类漏洞正是「端点/路由压根没挂门控」。
    只测函数层会完全漏掉那一类。
    """
    from fastapi import APIRouter, Depends

    from app.core.deps import get_current_user, require_permissions
    from app.main import create_app

    app = create_app()

    router = APIRouter(
        prefix="/__perm_guard__",
        dependencies=[Depends(require_permissions("billing:read"))],
    )

    @router.get("/probe")
    async def _probe():
        return {"ok": True}

    app.include_router(router)

    async def _override_user(role: Role):
        return _user(role)

    return app, get_current_user, _override_user


def test_http_layer_denies_role_without_permission():
    """P6：真实 HTTP 链路里，无 billing:read 的角色 ⇒ 403。"""
    from fastapi.testclient import TestClient

    app, get_current_user, _override_user = _build_app()
    app.dependency_overrides[get_current_user] = lambda: _user(Role.LAWYER)

    with TestClient(app) as client:
        resp = client.get("/__perm_guard__/probe")

    assert resp.status_code == PERM_DENIED_STATUS, (
        f"路由级权限门控未生效：{resp.status_code} {resp.text[:200]}"
    )


@pytest.mark.parametrize("role", HAS_BILLING)
def test_http_layer_allows_role_with_permission(role):
    """P7：反向量——有权限的角色必须 200。

    没有这条，把门控改成「一律 403」P6 依然绿，门就焊死了。
    """
    from fastapi.testclient import TestClient

    app, get_current_user, _ = _build_app()
    app.dependency_overrides[get_current_user] = lambda: _user(role)

    with TestClient(app) as client:
        resp = client.get("/__perm_guard__/probe")

    assert resp.status_code == 200, (
        f"{role.value} 有 billing:read 却被挡：{resp.status_code} {resp.text[:200]}"
    )


# ═══════════════════════ P8–P9 工厂阶段校验 ═══════════════════════


def test_empty_permission_list_is_rejected_at_factory_time():
    """P8：空权限码清单 ⇒ 必须在**工厂阶段**报错。

    `all([]) is True` ⇒ `require_permissions()` 会把端点**公开**且一声不响。
    一个手滑写成 `require_permissions(*[])` 就足以让计费数据裸奔，
    所以这里要求它**启动时就炸**，而不是等到线上被扫。
    """
    with pytest.raises(ValueError, match="权限码清单不能为空"):
        require_permissions()


def test_invalid_mode_is_rejected_at_factory_time():
    """P9：`mode` 写错 ⇒ 工厂阶段报错。

    原实现是 `all(checks) if mode == "all" else any(checks)`：
    `mode="anyy"` 会**静默退化成 any**，语义从「全都要」变成「有一个就行」。
    """
    with pytest.raises(ValueError, match="mode"):
        require_permissions("billing:read", mode="anyy")


# ═══════════════════════ R1–R3 require_roles ═══════════════════════


async def test_require_roles_allows_listed_role():
    """R1：允许的角色 ⇒ 放行并返回 ctx。"""
    dep = require_roles(Role.PLATFORM_ADMIN)
    ctx = await _call(dep, Role.PLATFORM_ADMIN)
    assert ctx.role == Role.PLATFORM_ADMIN


@pytest.mark.parametrize("role", NO_BILLING)
async def test_require_roles_denies_unlisted_role(role):
    """R2：不在清单里的角色 ⇒ 403。"""
    dep = require_roles(Role.PLATFORM_ADMIN)
    with pytest.raises(Exception) as ei:
        await _call(dep, role)
    assert _is_denied(ei), f"{role.value} 不在白名单却放行了"


def test_require_roles_rejects_empty_list():
    """R3：空角色清单 ⇒ 工厂阶段报错（`user.role not in ()` 恒真 ⇒ 拒绝所有人）。"""
    with pytest.raises(ValueError, match="角色清单不能为空"):
        require_roles()


# ═══════════════════════ W1 登记型判据 ═══════════════════════


def test_two_require_roles_definitions_are_a_known_wart():
    """W1：**登记型**判据——记录「两个同名 require_roles」这一已知隐患。

    - `app/core/deps.py::require_roles` 返回 `TenantContext`，额外依赖 `get_tenant_context`；
    - `app/core/rbac.py::require_roles` 返回 `User`，**只依赖** `get_current_user`。

    两者都在用（`audit_retention.py` / `complaints.py` 用前者，`knowledge.py` 用后者）。
    已确认 `knowledge.py::create_doc` 自己声明了 `ctx=Depends(get_tenant_context)`，
    **租户上下文没有丢**，所以目前只是可读性隐患，不是漏洞。

    ⇒ 这条判据的作用是：**将来谁合并/删除其中一个，这里会红**，强制回头确认
    所有调用点的返回值语义是否还成立（而不是让人在 IDE 里误 import 了另一个）。
    """
    from app.core import deps as deps_mod
    from app.core import rbac as rbac_mod

    assert hasattr(deps_mod, "require_roles"), "deps.require_roles 消失了？"
    assert hasattr(rbac_mod, "require_roles"), "rbac.require_roles 消失了？"
    assert deps_mod.require_roles is not rbac_mod.require_roles, (
        "两个 require_roles 已合并 —— 请确认所有调用点的返回值语义"
    )
