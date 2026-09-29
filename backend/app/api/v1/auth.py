"""认证接口：注册 / 登录 / 刷新 / 登出 / 当前用户。

**令牌承载方式（P0-7 修复）**
- refresh 令牌 -> HttpOnly Cookie（JS 不可读，XSS 偷不走）
- access 令牌 -> 响应体返回，前端内存持有并放 Authorization Bearer（30 分钟）
- csrf 令牌 -> 非 HttpOnly Cookie（JS 需读取后回填 X-CSRF-Token）

**服务端会话（P1-1）**
- 登录/刷新：refresh 的 jti 登记 RefreshSession 表；刷新走轮换
  （旧 jti 吊销 + 重放检测），登出按 jti 服务端吊销。
- 响应体的 refresh_token 字段仅为兼容存量客户端保留
  （AUTH_COOKIE_ENABLED=false 时才真正回传），默认不下发；
  Cookie 模式下 refresh 端点也不再接受 body 令牌（P1-2 收口）。
"""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.audit import AuditAction, log_detached_ctx
from app.core.auth_cookies import (
    clear_auth_cookies,
    read_refresh_cookie,
    set_csrf_cookie,
    set_refresh_cookie,
)
from app.core.deps import get_current_user, get_db
from app.core.errors import ErrorCode, UnauthorizedError
from app.core.pagination import ok
from app.core.rbac import Role
from app.models.identity import Tenant, User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    UserBrief,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["认证"])


async def _brief(db: AsyncSession, user: User) -> UserBrief:
    tenant = (
        await db.execute(select(Tenant).where(Tenant.tenant_id == user.tenant_id))
    ).scalars().first()
    return UserBrief(
        id=user.id,
        username=user.username,
        full_name=user.full_name,
        role=user.role,
        tenant_id=user.tenant_id,
        tenant_name=tenant.name if tenant else None,
    )


async def _issue_and_store(response: Response, user: User, brief: UserBrief, *, svc: AuthService) -> dict:
    """签发（含会话登记）-> 写 Cookie -> 返回响应体（不含 refresh，除非显式关闭 Cookie）。"""
    access, refresh, expires_in = await svc.issue_session_tokens(user)
    set_refresh_cookie(response, refresh)
    set_csrf_cookie(response, refresh)
    return {
        "access_token": access,
        # Cookie 模式下不回传 refresh（避免又回到 JS 可读的老问题）
        "refresh_token": refresh if not settings.AUTH_COOKIE_ENABLED else "",
        "token_type": "bearer",
        "expires_in": expires_in,
        "user": brief.model_dump(),
    }


@router.post("/register", response_model=dict, summary="注册")
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """公开自助注册。

    安全约束：角色固定为 CLIENT、租户固定为默认租户，不接受客户端指定
    （防止自助提权为 PLATFORM_ADMIN / 越权指定其他租户）。
    律师、律所管理员、企业管理员、平台管理员账号由 seed 脚本或受控后台接口创建。
    """
    user = await AuthService(db).register(
        username=payload.username,
        password=payload.password,
        full_name=payload.full_name,
        role=Role.CLIENT,
        tenant_id=None,
    )
    return ok(await _brief(db, user))


@router.post("/login", response_model=dict, summary="登录")
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db)
    user = await svc.authenticate(payload.username, payload.password)
    brief = await _brief(db, user)
    tokens = await _issue_and_store(response, user, brief, svc=svc)
    return ok({**tokens})
@router.post("/refresh", response_model=dict, summary="刷新令牌")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """刷新访问令牌（P1-1 轮换 + P1-2 收口）。

    refresh 令牌来源：HttpOnly Cookie。请求体回落仅在
    AUTH_COOKIE_ENABLED=false 的遗留模式下接受——Cookie 模式下接受
    body 等于把 7 天凭据重新暴露给 JS，抵消整个 Cookie 设计（P1-2）。

    流程：CSRF 中间件前置校验（TTL 过期豁免见 middleware）->
    rotate_refresh_session（服务端吊销旧 jti + 重放检测）->
    新令牌对写 Cookie（新 jti + 新 csrf cookie 随响应重发）。
    """
    refresh_token = read_refresh_cookie(request)
    if not refresh_token and not settings.AUTH_COOKIE_ENABLED:
        # 遗留模式（显式关闭 Cookie）才允许 body 携带 refresh
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 body 为空/非 JSON 都视为未提供
            body = {}
        if isinstance(body, dict):
            refresh_token = body.get("refresh_token")

    if not refresh_token:
        raise UnauthorizedError("缺少刷新令牌", code=ErrorCode.AUTH_REQUIRED)

    svc = AuthService(db)
    user, access, refresh, expires_in = await svc.rotate_refresh_session(refresh_token)
    brief = await _brief(db, user)
    set_refresh_cookie(response, refresh)
    set_csrf_cookie(response, refresh)
    return ok(
        {
            "access_token": access,
            "refresh_token": refresh if not settings.AUTH_COOKIE_ENABLED else "",
            "token_type": "bearer",
            "expires_in": expires_in,
            "user": brief.model_dump(),
        }
    )


@router.post("/logout", response_model=dict, summary="登出")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """按 jti 服务端吊销 refresh 会话 + 清空认证 Cookie。

    同时写一条 LOGOUT 审计：登出是安全事件序列的终点，
    与 LOGIN / LOGIN_FAILED 配对才能还原完整会话生命周期。
    P1-1：吊销发生在服务端（RefreshSession 表），Cookie 只是载体。
    """
    refresh_token = read_refresh_cookie(request)
    user = None
    svc = AuthService(db)
    if refresh_token:
        # 先独立事务吊销（提交释放锁），再解析用户写独立审计——
        # SQLite 单写者：主会话持锁 + detached 审计写会互卡（实测 500 根因）
        await svc.revoke_refresh_session_detached(refresh_token)
        try:
            user = await svc.user_from_refresh_token(refresh_token)
        except Exception:  # noqa: BLE001 令牌已失效也应允许登出
            user = None

    if user is not None:
        await log_detached_ctx(
            AuditAction.LOGOUT,
            "user",
            user.id,
            actor_id=user.id,
            actor_role=user.role.value,
            tenant_id=user.tenant_id,
        )

    clear_auth_cookies(response)
    return ok({"logged_out": True})


@router.get("/me", response_model=dict, summary="当前登录用户")
async def me(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return ok(await _brief(db, user))
