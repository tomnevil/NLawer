"""租户上下文守卫：`X-Tenant-Id` 不得让**非平台管理员**切租户。

## 为什么这个文件必须存在（2026-09-20）

`app/core/deps.py::get_tenant_context` 里写着正确的判断：

```python
if user.role == Role.PLATFORM_ADMIN and header_tenant:
    tenant_id = header_tenant
else:
    tenant_id = user.tenant_id
```

**但这条判断此前零判据**——`tests/` 里 `X-Tenant-Id` 只有 **1 处命中，还是
`test_auth_register.py:8` 的文档字符串**（描述历史漏洞），没有任何断言。
更关键的是：所有涉及 `get_tenant_context` 的测试
（`test_evidence_authz.py:169`、`test_moderation_enforcement.py:267`、
`test_review_tenant_guard.py:219`、`test_contract_review_llm.py:624`）
都把它 `dependency_overrides` 成了**桩**，真实逻辑**从未被执行过**。

⇒ 本轮主题「**已修复 ≠ 有判据**」的又一例，而这次是**多租户隔离边界**——
多租户系统里最高危的一类面。少了这些用例，把上面那个 `and` 改成无条件采信，
整条测试链会**全部保持绿**。

## 覆盖清单

- X1 四种非管理员角色带 `X-Tenant-Id` ⇒ 强制自身租户（**核心**）
- X2 **反向对照**：平台管理员**能**切换（否则"一律忽略该头"也能让 X1 全绿，
  而平台运营功能就没了）
- X3 平台管理员不带该头 ⇒ 用自身租户
- X4 空值不触发切换
- X5 **HTTP 层**：走真实的依赖解析链（中间件 + DI），非管理员的头部不生效
"""
from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.deps import get_tenant_context
from app.core.rbac import Role
from app.models.identity import User

NON_ADMIN_ROLES = (
    Role.CLIENT,
    Role.LAWYER,
    Role.FIRM_ADMIN,
    Role.ENTERPRISE_ADMIN,
)


def _req(tenant: str | None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if tenant is not None:
        headers.append((b"x-tenant-id", tenant.encode()))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": headers,
            "client": ("203.0.113.9", 50000),
            "server": ("testserver", 80),
        }
    )


def _user(role: Role, tenant_id: str) -> User:
    return User(
        id=1,
        username="u",
        hashed_password="not-a-real-hash",
        role=role,
        tenant_id=tenant_id,
    )


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
async def test_non_admin_cannot_switch_tenant_via_header(role):
    """X1（核心）：非平台管理员带 `X-Tenant-Id` ⇒ 强制使用自身租户。

    少了这条，把 `deps.py:86` 的 `and header_tenant:` 条件里
    `user.role == Role.PLATFORM_ADMIN` 这一段删掉，全库测试**仍然全绿**。
    """
    ctx = await get_tenant_context(_req("tenant-victim"), _user(role, "tenant-own"))
    assert ctx.tenant_id == "tenant-own", (
        f"{role.value} 通过 X-Tenant-Id 切到了别的租户 ⇒ 跨租户越权复活"
    )


async def test_platform_admin_can_switch_tenant():
    """X2（**反向对照**）：平台管理员带该头 ⇒ **应当**能切换。

    ⚠️ 没有这条，「一律忽略 `X-Tenant-Id`」这种**过度收紧**也能让 X1 全绿——
    那时平台运营（跨租户管理）直接不可用，比越权更早被发现，但同样是坏的。
    """
    ctx = await get_tenant_context(
        _req("tenant-target"), _user(Role.PLATFORM_ADMIN, "tenant-own")
    )
    assert ctx.tenant_id == "tenant-target"


async def test_platform_admin_without_header_uses_own_tenant():
    """X3：管理员不带该头 ⇒ 用自身租户（不能因为"是管理员"就变成空租户）。"""
    ctx = await get_tenant_context(_req(None), _user(Role.PLATFORM_ADMIN, "tenant-own"))
    assert ctx.tenant_id == "tenant-own"


@pytest.mark.parametrize("blank", ["", "   "])
async def test_admin_blank_header_does_not_switch_to_blank(blank):
    """X4：空白的 `X-Tenant-Id` 不应把租户切成空白。

    空串是 falsy、走 else 分支，本就安全；**纯空格是 truthy**，
    若将来有人把判断改成 `is not None`，租户会被切成 `"   "` ⇒ 全表查不到数据。
    这条提前把这个形状钉住。
    """
    ctx = await get_tenant_context(_req(blank), _user(Role.PLATFORM_ADMIN, "tenant-own"))
    assert ctx.tenant_id == "tenant-own"


async def test_http_layer_non_admin_header_has_no_effect():
    """X5（**HTTP 层**）：走真实依赖解析链，非管理员的 `X-Tenant-Id` 不生效。

    前四条直接调依赖函数；这一条补齐「中间件 + 依赖注入」这一层——
    中间任何一环把头部提前塞进 state、或用别的键覆盖了 `tenant_id`，
    前四条都会全绿而越权复活。

    做法：`create_app()` 后挂一个**测试专用**回显路由，并只覆盖 `get_current_user`
    （不覆盖 `get_tenant_context`），让**真实**的租户解析逻辑跑起来。
    """
    from fastapi.testclient import TestClient

    from app.core.deps import get_current_user, get_tenant_context
    from app.main import create_app

    app = create_app()
    victim_user = _user(Role.CLIENT, "tenant-own")
    app.dependency_overrides[get_current_user] = lambda: victim_user

    @app.get("/__tenant_echo__")
    async def _echo(ctx=__import__("fastapi").Depends(get_tenant_context)):
        return {"tenant_id": ctx.tenant_id, "role": str(ctx.role)}

    client = TestClient(app)
    resp = client.get("/__tenant_echo__", headers={"X-Tenant-Id": "tenant-victim"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["tenant_id"] == "tenant-own", (
        "HTTP 层：普通客户的 X-Tenant-Id 生效了 ⇒ 跨租户越权"
    )
