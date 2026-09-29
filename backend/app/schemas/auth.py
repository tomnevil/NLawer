"""认证相关 Schema。"""
from typing import Optional

from pydantic import BaseModel, Field

from app.core.rbac import Role


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserBrief"


class UserBrief(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    role: Role
    tenant_id: str
    tenant_name: Optional[str] = None

    model_config = {"from_attributes": True}


class RegisterRequest(BaseModel):
    """公开注册请求。

    安全约束：**不接受客户端指定角色与租户**。
    早期版本曾暴露 `role` / `tenant_id` 字段并直接透传给 AuthService，
    导致任何人可通过 `POST /auth/register` 自助注册为 PLATFORM_ADMIN
    （再借 `X-Tenant-Id` 头跨租户读取全平台数据）。
    现统一由服务端固定为 CLIENT + 默认租户；管理员/律师账号只能由
    seed 脚本或受控后台接口（require_permissions）创建。
    """

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(None, max_length=100)


class RefreshRequest(BaseModel):
    refresh_token: str


TokenResponse.model_rebuild()
