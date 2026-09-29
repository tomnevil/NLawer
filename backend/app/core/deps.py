"""FastAPI 依赖层：鉴权、租户上下文、分页、角色门禁。

租户上下文是本项目的核心横切关注点：所有业务查询必须经此注入的
`tenant_id` 过滤，禁止裸查询（PRD：企业私有知识隔离率 100%）。
"""
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ErrorCode,
    PermissionDeniedError,
    TenantDeniedError,
    UnauthorizedError,
)
from app.core.pagination import PaginationParams
from app.core.rbac import Role, has_permission
from app.core.security import decode_token_or_none
from app.database import get_db  # noqa: F401  统一从 deps 暴露
from app.models.identity import User

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class TenantContext:
    """租户上下文：由已认证用户派生，供服务层做隔离过滤。"""

    tenant_id: str
    user_id: int
    role: Role

    def assert_tenant(self, target_tenant_id: Optional[str]) -> None:
        """校验目标资源归属当前租户，不匹配则拒绝（读取前后双重校验之一）。"""
        if target_tenant_id and target_tenant_id != self.tenant_id:
            raise TenantDeniedError(
                "无权访问其他租户的数据",
                code=ErrorCode.TENANT_DENIED,
                details={"target_tenant": target_tenant_id},
            )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """校验 JWT access token 并加载用户。"""
    if credentials is None or not credentials.credentials:
        raise UnauthorizedError("缺少 Bearer 令牌", code=ErrorCode.AUTH_REQUIRED)

    payload = decode_token_or_none(credentials.credentials)
    if payload is None:
        raise UnauthorizedError("令牌无效或已过期", code=ErrorCode.AUTH_TOKEN_INVALID)
    if payload.get("type") != "access":
        raise UnauthorizedError("令牌类型不匹配", code=ErrorCode.AUTH_TOKEN_INVALID)

    subject = payload.get("sub")
    if not subject:
        raise UnauthorizedError("令牌缺少主体信息", code=ErrorCode.AUTH_TOKEN_INVALID)

    user = (await db.execute(select(User).where(User.id == int(subject)))).scalars().first()
    if user is None:
        raise UnauthorizedError("用户不存在", code=ErrorCode.AUTH_TOKEN_INVALID)
    if not user.is_active or user.status.value != "ACTIVE":
        raise UnauthorizedError("账号已被停用", code=ErrorCode.AUTH_USER_INACTIVE)

    request.state.user_id = user.id
    return user


async def get_tenant_context(
    request: Request,
    user: User = Depends(get_current_user),
) -> TenantContext:
    """构建租户上下文。

    平台管理员可通过 `X-Tenant-Id` 头部切换到目标租户进行运营管理；
    其余角色强制使用自身所属租户，忽略该头部。

    ⚠️ 头部必须先 `strip()` 再判空：**纯空白字符串是 truthy**，
    若直接 `if header_tenant:` 会把租户切成 `"   "` ⇒ 全表查不到数据，
    且不报错（静默错误）。2026-09-20 由 `tests/test_tenant_context_guard.py::X4` 钉住。
    """
    header_tenant = (request.headers.get("X-Tenant-Id") or "").strip() or None
    if user.role == Role.PLATFORM_ADMIN and header_tenant:
        tenant_id = header_tenant
    else:
        tenant_id = user.tenant_id

    ctx = TenantContext(tenant_id=tenant_id, user_id=user.id, role=user.role)
    request.state.tenant_id = tenant_id
    return ctx


def require_permissions(*codes: str, mode: str = "all"):
    """路由级权限依赖。

    用法：ctx = Depends(require_permissions("case:assign"))

    ⚠️ 参数在**工厂阶段**（即路由定义 / 模块导入时）就校验，不在请求阶段：
    - `mode` 写错（如 `"anyy"`）原本会静默退化成 `any`；
    - 空权限码清单在 `mode="all"` 下 `all([]) is True` ⇒ **端点被公开**，
      且完全不报错（静默失败）。
    两者都属于「写错了一行，安全边界凭空消失」⇒ 必须在启动时就炸。
    2026-09-20 由 `tests/test_require_permissions_guard.py::P8/P9` 钉住。
    """
    if mode not in ("all", "any"):
        raise ValueError(
            f"require_permissions: mode 必须是 'all' 或 'any'，收到 {mode!r}"
        )
    if not codes:
        raise ValueError(
            "require_permissions: 权限码清单不能为空 —— "
            "mode='all' 下 all([]) 恒真，等于把该端点公开"
        )

    async def dependency(
        user: User = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_context),
    ) -> TenantContext:
        checks = [has_permission(user.role, c) for c in codes]
        ok = all(checks) if mode == "all" else any(checks)
        if not ok:
            raise PermissionDeniedError(
                "权限不足",
                details={"required_permissions": list(codes), "mode": mode},
            )
        return ctx

    return dependency


def require_roles(*roles: Role):
    """路由级角色依赖。

    ⚠️ 空角色清单会让 `user.role not in ()` 恒真 ⇒ **任何人都会被 403**，
    同样是静默失败，一并在工厂阶段拒绝。
    """
    if not roles:
        raise ValueError("require_roles: 角色清单不能为空 —— 空清单会拒绝所有人")

    async def dependency(
        user: User = Depends(get_current_user),
        ctx: TenantContext = Depends(get_tenant_context),
    ) -> TenantContext:
        if user.role not in roles:
            raise PermissionDeniedError(
                "角色无权访问", details={"required_roles": [r.value for r in roles]}
            )
        return ctx

    return dependency


__all__ = [
    "PaginationParams",
    "TenantContext",
    "get_current_user",
    "get_tenant_context",
    "require_permissions",
    "require_roles",
    "Query",
]
