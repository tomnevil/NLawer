"""中间件：错误处理、请求日志、请求 ID、限流。

Starlette `BaseHTTPMiddleware` 要求实现 `dispatch(request, call_next)`，
包装顺序由 `add_middleware` 决定（后注册者位于外层）。
CORS 需在 `main.py` 中作为最外层注册；异常处理中间件位于其内，
保证异常向外传播时能被归一化。
"""
import ipaddress
import time
import uuid
from typing import Awaitable, Callable, Iterable, Tuple

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.core.audit_context import set_audit_context
from app.core.csrf import (
    session_id_from_refresh,
    validate_csrf_token,
    validate_csrf_token_allow_expired,
)
from app.core.errors import AppError, http_exception_to_app_error
from app.core.log_config import set_request_id
from app.core.metrics import metrics, normalize_path
from app.core.rate_limit_backend import RateLimitBackend, build_backend

#: 认证类敏感端点（防暴力破解 / 批量注册）
AUTH_RATE_LIMITED_PATHS: Tuple[str, ...] = (
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
)

#: 上传类端点**前缀**（防存储与算力滥用）
#:
#: 🚨 这里必须用前缀而非后缀：路由是 `POST /evidence/cases/{case_id}`，
#: **运行时路径以案件 ID 结尾**（`/api/v1/evidence/cases/42`），
#: 用 `endswith("/evidence/cases")` 永远为假 ⇒ 上传档限流**从未生效**。
#: 生成档可以用后缀，是因为它的后缀（`/parse`、`/generate`、`/dispatch`）
#: 恰好是路径**最后一段**；上传档的后缀是**父路径**，两者不是同一类。
UPLOAD_PATH_PREFIXES: Tuple[str, ...] = (
    "/api/v1/evidence/cases/",  # POST /api/v1/evidence/cases/{case_id}
)

#: 兼容旧命名（历史值语义有误，保留仅为不破坏外部引用）
UPLOAD_PATH_SUFFIXES: Tuple[str, ...] = (
    "/evidence/cases",
)

#: 公开写入类端点（无需登录即可提交 → 必须限流，否则被脚本灌爆）
#: 注意：这是**精确路径**而非后缀——`/complaints/{id}/handle` 是管理员接口，
#: 不应与公众提交共享桶。
PUBLIC_WRITE_PATHS: Tuple[str, ...] = (
    "/api/v1/complaints",
)

#: 生成类（AI 重计算）端点后缀（防刷 AI 成本）
GENERATE_PATH_SUFFIXES: Tuple[str, ...] = (
    "/generate",
    "/scan",
    "/parse",
    "/dispatch",
)

#: 兼容旧命名
DEFAULT_RATE_LIMITED_PATHS = AUTH_RATE_LIMITED_PATHS

#: 不纳入指标的路径（探针与指标端点本身）。
#: 探针会被 K8s 每几秒打一次，混入请求量/耗时会把业务指标稀释成噪声；
#: `/metrics` 若自计自身，抓取频率变化会造成指标抖动。
METRICS_EXCLUDED_PATHS: Tuple[str, ...] = (
    "/metrics",
    "/api/health",
    "/api/health/livez",
    "/api/health/readyz",
)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """注入 X-Request-ID、建立审计上下文、记录结构化日志与请求指标。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # P2-8：X-Request-ID 是客户端可注入的头——原样回显并写入日志存在
        # 日志注入面（超长 / 换行 / 控制字符）。仅接受 8-64 位十六进制与
        # 连字符（兼容 uuid4 与常见短 id），否则丢弃换新生成。
        client_request_id = request.headers.get("X-Request-ID") or ""
        request_id = (
            client_request_id
            if 8 <= len(client_request_id) <= 64
            and all(c in "0123456789abcdefABCDEF-" for c in client_request_id)
            else uuid.uuid4().hex
        )
        request.state.request_id = request_id
        # 让 service 层深处的日志自动带上 request_id（contextvars 异步安全）
        set_request_id(request_id)

        # 建立审计上下文：让任意深度的 service 层都能拿到 IP / UA / request_id，
        # 无需把 Request 对象层层透传（见 core/audit_context.py）
        set_audit_context(
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
            request_id=request_id,
        )

        path = request.url.path
        tracked = not any(path.startswith(p) for p in METRICS_EXCLUDED_PATHS)
        if tracked:
            metrics.http_requests_in_progress.inc()

        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            if tracked:
                metrics.http_requests_in_progress.dec()

        elapsed_ms = (time.perf_counter() - started) * 1000

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"

        if tracked:
            # 路径在此处归一化（normalize_path），防止 /cases/{uuid} 撑爆指标基数
            metrics.record_request(request.method, path, response.status_code, elapsed_ms)

        # 慢请求告警；不记录请求体（可能含卷宗原文等敏感数据）
        if elapsed_ms > 3000:
            logger.warning(
                "慢请求 {method} {path} {ms:.0f}ms status={status}",
                method=request.method,
                path=path,
                ms=elapsed_ms,
                status=response.status_code,
            )
        return response


def _is_trusted(addr: str, trusted: Iterable[str]) -> bool:
    """地址是否落在可信代理集合里（支持单 IP 与 CIDR）。

    ⚠️ 解析失败一律按**不可信**处理：对端可能是 Unix socket / 主机名 / 测试客户端
    （starlette `TestClient` 的 `client.host` 是 `"testclient"` 而非 IP）。
    这里如果抛异常，整站每个请求都会 500——所以宁可"不信任"，不能"崩"。
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue  # 配置里写了非法条目：跳过，不因此崩
    return False


def _client_ip(request: Request) -> str:
    """取客户端 IP：**只有直接对端是可信代理时**才采信 `X-Forwarded-For`。

    🚨 旧实现无条件返回 XFF 首段 ⇒ `X-Forwarded-For` 是请求头、客户端可任意伪造，
    于是"每换一个伪造头 = 换一个限流桶"，注册/登录限流形同虚设（Q-K）。

    采信时的取值规则：**从右往左**跳过所有可信代理条目，取第一个不可信的那一跳。
    为什么要从右往左——nginx 的 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for`
    是**追加**，客户端伪造的值会留在**左边**；取首段恰好取到攻击者写的那个值。
    """
    peer = request.client.host if request.client else ""
    trusted = settings.trusted_proxies_list

    if peer and _is_trusted(peer, trusted):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            # 从右往左找第一个**不可信**的跳；全部可信则退回最左一跳（链全由己方代理构成）
            for hop in reversed(hops):
                if not _is_trusted(hop, trusted):
                    return hop
            return hops[0]
    return peer or "unknown"


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """把 AppError / HTTPException / 未捕获异常归一为标准错误响应体。"""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except AppError as exc:
            return JSONResponse(status_code=exc.status_code, content=exc.to_dict())
        except Exception as exc:  # noqa: BLE001 统一兜底
            # FastAPI 内置 HTTPException 走归一化路径
            status_code = getattr(exc, "status_code", None)
            if status_code is not None:
                app_error = http_exception_to_app_error(exc)
                return JSONResponse(
                    status_code=app_error.status_code, content=app_error.to_dict()
                )
            # 指标：异常类型是低基数的（类名），可安全作为标签
            metrics.unhandled_errors_total.inc((type(exc).__name__,))
            logger.exception("未捕获异常: {}", exc)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "success": False,
                    "error": {
                        "code": "INTERNAL_SERVER_ERROR",
                        "message": "服务器内部错误",
                        "details": None,
                    },
                },
            )


class CsrfMiddleware(BaseHTTPMiddleware):
    """CSRF 防护（签名式双提交 Cookie）。

    **为什么要有它？** refresh 令牌改存 HttpOnly Cookie 后，浏览器会自动携带，
    这挡住了 XSS 窃取，但也打开了 CSRF 通道。本中间件校验
    「请求头 `X-CSRF-Token` == Cookie 中令牌」且**签名与会话绑定有效**。

    保护范围（默认）：
      - `CSRF_PROTECTED_PREFIXES` 内的路径（即 `/api/v1/auth/refresh`）—— 这些
        端点凭 Cookie 即可换发新令牌，是最危险的 CSRF 目标。
      - `CSRF_STRICT_ALL_WRITES=True` 时扩展到所有写方法（PUT/PATCH/DELETE
        及特定 POST），需前端全面配合。

    豁免原则：**携带 `Authorization: Bearer` 的请求天然免疫 CSRF**
    （浏览器不会自动附加该头），因此有 Bearer 时直接放行——这保证了
    移动端 / 第三方调用方不被误伤。
    """

    def __init__(
        self,
        app,  # type: ignore[no-untyped-def]
        *,
        enabled: bool = True,
        protected_prefixes: Tuple[str, ...] = (),
        strict_all_writes: bool = False,
        header_name: str = "X-CSRF-Token",
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.protected_prefixes = tuple(protected_prefixes)
        self.strict_all_writes = strict_all_writes
        self.header_name = header_name

    def _needs_check(self, request: Request) -> bool:
        if not self.enabled:
            return False
        # Bearer 请求免疫 CSRF（浏览器不会自动带上 Authorization 头）
        if request.headers.get("Authorization"):
            return False
        path = request.url.path
        if any(path.startswith(p) for p in self.protected_prefixes):
            return True
        if self.strict_all_writes and request.method in ("POST", "PUT", "PATCH", "DELETE"):
            return True
        return False

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not self._needs_check(request):
            return await call_next(request)

        cookie_token = request.cookies.get(settings.CSRF_COOKIE_NAME)
        header_token = request.headers.get(self.header_name)

        # 会话绑定：CSRF 令牌签名里嵌了 refresh 会话标识
        refresh = request.cookies.get(settings.REFRESH_COOKIE_NAME) or ""
        sid = session_id_from_refresh(refresh) if refresh else ""

        if not cookie_token or not header_token or cookie_token != header_token:
            metrics.csrf_rejections_total.inc(("missing_or_mismatch",))
            logger.warning("CSRF 校验失败（令牌缺失或不匹配）: {} {}", request.method, request.url.path)
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "success": False,
                    "error": {
                        "code": "CSRF_TOKEN_INVALID",
                        "message": "CSRF 令牌缺失或不匹配",
                        "details": None,
                    },
                },
            )
        if not validate_csrf_token(cookie_token, session_id=sid):
            # P2-7 TTL 豁免：令牌过期但签名 + 会话绑定有效，且路径属于
            # 「凭 Cookie 换发」的受保护前缀（refresh / logout）时放行。
            # 空闲超过 1 小时后 csrf 令牌过期而 Cookie 仍在（max_age 7 天），
            # 严格校验会把一次正常空闲变成误判掉线；豁免后 refresh 成功
            # 即随响应重发新令牌。防伪造性质（签名、sid 绑定、双提交）保留；
            # strict_all_writes 扩展出的一般写路径不匹配前缀，不享受豁免。
            path = request.url.path
            exempt = any(
                path.startswith(p) for p in self.protected_prefixes
            ) and validate_csrf_token_allow_expired(cookie_token, session_id=sid)
            if not exempt:
                metrics.csrf_rejections_total.inc(("invalid_signature",))
                logger.warning("CSRF 校验失败（签名或时效无效）: {} {}", request.method, path)
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "success": False,
                        "error": {
                            "code": "CSRF_TOKEN_INVALID",
                            "message": "CSRF 令牌无效或已过期",
                            "details": None,
                        },
                    },
                )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """敏感端点限流（滑动窗口）。

    分三档阈值，只保护**写操作**，不做全局限流，以免影响 SSE 流式输出与前端轮询：
      - 认证档（`AUTH_RATE_LIMITED_PATHS`）：防暴力破解 / 批量注册
      - 上传档（`UPLOAD_PATH_PREFIXES`）：防存储滥用
      - 生成档（`GENERATE_PATH_SUFFIXES`）：防刷 AI 成本

    后端可插拔：配置 `REDIS_URL` 时用 Redis 共享计数（多副本正确）；
    否则进程内计数（单副本正确，多副本阈值为 配置值 × 副本数）。
    """

    def __init__(
        self,
        app,  # type: ignore[no-untyped-def]
        *,
        enabled: bool = True,
        max_requests: int = 20,
        window_seconds: int = 60,
        upload_max: int = 10,
        generate_max: int = 15,
        public_write_max: int = 5,
        paths: Tuple[str, ...] = AUTH_RATE_LIMITED_PATHS,
        public_write_paths: Tuple[str, ...] = PUBLIC_WRITE_PATHS,
        backend: RateLimitBackend | None = None,
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.max_requests = max(1, max_requests)
        self.window_seconds = max(1, window_seconds)
        self.paths = set(paths)
        self.public_write_paths = set(public_write_paths)
        self.upload_max = max(1, upload_max)
        self.generate_max = max(1, generate_max)
        self.public_write_max = max(1, public_write_max)
        self.backend: RateLimitBackend = backend or build_backend(settings.REDIS_URL)

    @staticmethod
    def _client_ip(request: Request) -> str:
        """取客户端 IP（与审计上下文共用同一实现，避免两处规则漂移）。"""
        return _client_ip(request)

    def _resolve_limit(self, path: str) -> int | None:
        """返回该路径适用的阈值；不受限则返回 None。

        顺序有讲究：精确路径优先于后缀匹配，否则公开写入端点会先被
        更宽松的规则命中，导致限流失效。
        """
        if path in self.paths:
            return self.max_requests
        if path in self.public_write_paths:
            return self.public_write_max
        if any(path.startswith(p) for p in UPLOAD_PATH_PREFIXES):
            return self.upload_max
        if any(path.endswith(s) for s in GENERATE_PATH_SUFFIXES):
            return self.generate_max
        return None

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # 覆盖全部写方法（P2-6）：只限 POST 时，PUT/PATCH/DELETE 是绕过门。
        # _resolve_limit 未命中的路径返回 None 直接放行，普通读请求不受影响。
        if not self.enabled or request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)

        limit = self._resolve_limit(request.url.path)
        if limit is None:
            return await call_next(request)

        # 与 metrics 同口径归一化（P2-5）：/evidence/cases/{id} 这类带 ID 的路径
        # 若用原始 path 做桶 key，每个 ID 一个新桶，攻击者遍历 ID 即可绕过限流。
        key = f"{self._client_ip(request)}:{normalize_path(request.url.path)}"
        allowed, retry_after = await self.backend.hit(key, limit, self.window_seconds)

        if not allowed:
            # 必须归一化：限流端点里含 /evidence/cases/{id} 这类带 ID 的路径，
            # 直接用原始 path 会为每个案件生成一条时间序列（基数爆炸）。
            metrics.rate_limit_hits_total.inc((normalize_path(request.url.path),))
            logger.warning("触发限流 {} {}（阈值 {}）", request.method, request.url.path, limit)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "请求过于频繁，请稍后再试",
                        "details": {"retry_after_seconds": retry_after},
                    },
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)

