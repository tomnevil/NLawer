"""认证 Cookie 读写：refresh 令牌的 HttpOnly 承载。

**设计要点**
- refresh 令牌**不再返回给前端 JS**，改由 HttpOnly Cookie 承载 → XSS 无法读取。
- `SameSite` 默认 `lax`：已可拦截跨站 POST（CSRF 主路径），同时不影响外链静默回跳。
- `Secure` 由 `AUTH_COOKIE_SECURE` 控制，**生产必须为 True**（见 config 校验）。
- CSRF 令牌走**普通 Cookie**（非 HttpOnly），因为它必须被前端 JS 读取后回填到请求头。

> 注意：`access` 令牌仍走 `Authorization: Bearer`（前端内存持有，短时 30 分钟），
> 因为 SSE / 多端场景下 Cookie 反而更难控制。两者结合把风险窗口压到最小：
> **XSS 最多偷到 30 分钟的 access，拿不到 7 天的 refresh。**
"""
from __future__ import annotations

from typing import Optional

from fastapi import Request, Response

from app.config import settings
from app.core.csrf import generate_csrf_token, session_id_from_refresh

#: access 令牌 Cookie（可选启用，默认不用于 Bearer 场景）
ACCESS_COOKIE_NAME = "nlaw_at"


def _base_kwargs() -> dict:
    return {
        "path": settings.AUTH_COOKIE_PATH,
        "secure": settings.AUTH_COOKIE_SECURE,
        "httponly": True,
        "samesite": settings.AUTH_COOKIE_SAMESITE,
    }


def set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """写入 refresh 令牌（HttpOnly，JS 不可读）。"""
    if not settings.AUTH_COOKIE_ENABLED:
        return
    response.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=settings.REFRESH_TOKEN_DAYS * 24 * 3600,
        **_base_kwargs(),
    )


def set_csrf_cookie(response: Response, refresh_token: str) -> None:
    """写入 CSRF 令牌（**非** HttpOnly，前端需读取后回填请求头）。"""
    if not settings.AUTH_COOKIE_ENABLED:
        return
    token = generate_csrf_token(session_id_from_refresh(refresh_token))
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=token,
        max_age=settings.REFRESH_TOKEN_DAYS * 24 * 3600,
        path=settings.AUTH_COOKIE_PATH,
        secure=settings.AUTH_COOKIE_SECURE,
        httponly=False,  # 必须可被 JS 读取，否则无法回填请求头
        samesite=settings.AUTH_COOKIE_SAMESITE,
    )


def clear_auth_cookies(response: Response) -> None:
    """登出：清空 refresh 与 csrf Cookie。"""
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME, path=settings.AUTH_COOKIE_PATH
    )
    response.delete_cookie(settings.CSRF_COOKIE_NAME, path=settings.AUTH_COOKIE_PATH)


def read_refresh_cookie(request: Request) -> Optional[str]:
    return request.cookies.get(settings.REFRESH_COOKIE_NAME)


def read_csrf_cookie(request: Request) -> Optional[str]:
    return request.cookies.get(settings.CSRF_COOKIE_NAME)


__all__ = [
    "ACCESS_COOKIE_NAME",
    "set_refresh_cookie",
    "set_csrf_cookie",
    "clear_auth_cookies",
    "read_refresh_cookie",
    "read_csrf_cookie",
]
