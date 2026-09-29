"""角色枚举与权限矩阵（RBAC）。

角色按两条产品线划分：
- 平台侧：PLATFORM_ADMIN
- 律所侧（产品线 A）：FIRM_ADMIN / LAWYER / ASSISTANT
- 客户侧（产品线 A 的服务对象）：CLIENT
- 企业侧（产品线 B）：ENTERPRISE_ADMIN / ENTERPRISE_USER

权限码命名：`<资源>:<动作>`，支持 `*` 通配（仅 PLATFORM_ADMIN）。
"""
from enum import Enum
from typing import Dict, FrozenSet

from fastapi import Depends


class Role(str, Enum):
    """系统角色。"""

    PLATFORM_ADMIN = "PLATFORM_ADMIN"
    FIRM_ADMIN = "FIRM_ADMIN"
    LAWYER = "LAWYER"
    ASSISTANT = "ASSISTANT"
    CLIENT = "CLIENT"
    ENTERPRISE_ADMIN = "ENTERPRISE_ADMIN"
    ENTERPRISE_USER = "ENTERPRISE_USER"


# 平台管理员通配
_ALL = "*"

#: 角色 -> 权限码集合
ROLE_PERMISSIONS: Dict[Role, FrozenSet[str]] = {
    Role.PLATFORM_ADMIN: frozenset({_ALL}),
    Role.FIRM_ADMIN: frozenset(
        {
            # 租户与成员
            "tenant:read",
            "tenant:update",
            "user:read",
            "user:create",
            "user:update",
            # 案件与派单
            "case:read",
            "case:create",
            "case:update",
            "case:assign",
            "case:close",
            "dispatch:read",
            "dispatch:create",
            "dispatch:rule:write",
            # 办案与证据
            "analysis:read",
            "analysis:create",
            "evidence:read",
            "evidence:create",
            "evidence:update",
            # 复核（律所管理员可做 L3 高级复核）
            "review:read",
            "review:l1",
            "review:l2",
            "review:l3",
            # 归档
            "archive:read",
            "archive:create",
            "hearing_pack:export",
            # 知识库与统计
            "knowledge:read",
            "knowledge:write",
            "stats:read",
            "audit:read",
            "billing:read",
            "billing:write",
        }
    ),
    Role.LAWYER: frozenset(
        {
            "case:read",
            "case:update",
            "case:close",
            "dispatch:read",
            "dispatch:grab",
            "analysis:read",
            "analysis:create",
            "analysis:iterate",
            "evidence:read",
            "evidence:create",
            "evidence:update",
            "review:read",
            "review:l1",
            "review:l2",
            "archive:read",
            "archive:create",
            "hearing_pack:export",
            "knowledge:read",
            "stats:read",
        }
    ),
    Role.ASSISTANT: frozenset(
        {
            "case:read",
            "dispatch:read",
            "analysis:read",
            "analysis:create",
            "evidence:read",
            "evidence:create",
            "evidence:update",
            "review:read",
            "archive:read",
            "knowledge:read",
        }
    ),
    Role.CLIENT: frozenset(
        {
            # 客户仅可看自己的会话、案件与材料
            "conversation:read:own",
            "conversation:write:own",
            "case:read:own",
            "case:create:own",
            "evidence:create:own",
            "evidence:read:own",
            "evidence:read",
            "evidence:create",
            "evidence:update",
            "archive:read:own",
        }
    ),
    Role.ENTERPRISE_ADMIN: frozenset(
        {
            "qa:use",
            "document:read",
            "document:create",
            "document:update",
            "contract:review",
            "compliance:read",
            "compliance:scan",
            "knowledge:read",
            "knowledge:write",
            "billing:read",
            "billing:write",
            "workorder:read",
            "workorder:approve",
            "user:read",
            "user:create",
            "user:update",
            "stats:read",
        }
    ),
    Role.ENTERPRISE_USER: frozenset(
        {
            "qa:use",
            "document:read",
            "document:create",
            "contract:review",
            "compliance:read",
            "knowledge:read",
        }
    ),
}

#: 复核级别 -> 执行该级别复核所需的权限码
REVIEW_LEVEL_PERMISSION: Dict[str, str] = {
    "L1": "review:l1",
    "L2": "review:l2",
    "L3": "review:l3",
}


def permissions_for(role: Role) -> FrozenSet[str]:
    """取角色的权限集合，未知角色返回空集（最小权限原则）。"""
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, code: str) -> bool:
    """判断角色是否具备某权限。"""
    perms = permissions_for(role)
    return _ALL in perms or code in perms


def can_review(role: Role, level: str) -> bool:
    """判断角色是否有资格执行指定级别的复核。"""
    required = REVIEW_LEVEL_PERMISSION.get(level)
    if required is None:
        return False
    return has_permission(role, required)


def can_transition_role(role: Role, from_status: str, to_status: str) -> bool:
    """角色可见的状态流转门禁（业务合法性由 ReviewFSM 另行校验）。

    客户不参与内部复核流转；助理不可确认定稿。
    """
    if role == Role.CLIENT:
        return False
    if role == Role.ASSISTANT and to_status in {"confirmed", "archived"}:
        return False
    return True


def require_roles(*allowed: Role):  # noqa: ANN201
    """FastAPI 依赖工厂：当前用户角色不在 allowed 内即拒绝（403）。"""
    from app.core.deps import get_current_user
    from app.core.errors import PermissionDeniedError

    allowed_set = set(allowed)

    async def _dep(user=Depends(get_current_user)):  # type: ignore[user]
        if user.role not in allowed_set:
            raise PermissionDeniedError(
                f"角色 {user.role.value} 无权执行此操作",
                details={"allowed": [r.value for r in allowed]},
            )
        return user

    return _dep
