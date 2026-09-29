"""FastAPI 应用入口：应用工厂 + 中间件 + 路由 + 健康检查。

相比 zinteligencevideoagent 的模块级单例，此处改为 `create_app()` 工厂，
便于测试时注入不同配置。
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from loguru import logger

from app.api.router import api_router
from app.config import settings
from app.core.errors import ConfigurationError
from app.core.log_config import configure_logging
from app.core.metrics import metrics
from app.database import async_session_factory, init_db
from app.middleware import (
    CsrfMiddleware,
    ErrorHandlerMiddleware,
    RateLimitMiddleware,
    RequestContextMiddleware,
)
from app.models.complaint import COMPLAINT_DUE_DAYS
from app.services import notification_push
from app.services.audit_retention import SECURITY_BASELINE_DAYS, retention_days
from app.services.connection_manager import manager
from app.services.job_handlers import register_all
from app.services.job_service import job_queue, recover_stale_jobs


async def _stale_job_recovery_loop() -> None:
    """周期性回收僵尸任务（进程崩溃后卡在 RUNNING 的 Job）。"""
    import asyncio

    while True:
        try:
            await asyncio.sleep(settings.JOB_RECOVERY_INTERVAL_SECONDS)
            async with async_session_factory() as db:
                await recover_stale_jobs(db)
        except asyncio.CancelledError:
            break
        except Exception as exc:  # noqa: BLE001 巡检失败不能终止循环
            logger.warning("僵尸任务巡检异常: {}", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 确保 SQLite 目录存在（aiosqlite 不会自动建父目录）
    if settings.is_sqlite:
        db_path = settings.DATABASE_URL.split("///")[-1]
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
    os.makedirs(settings.LOCAL_STORAGE_PATH, exist_ok=True)

    await init_db()
    logger.info("数据库初始化完成：{}", settings.DATABASE_URL.split("///")[-1])

    # 注册异步任务处理器（案件分析 / 证据解析 / 合规扫描 / 文书生成）
    register_all()
    logger.info("Job 处理器注册完成")

    # 通知实时推送桥：显式声明「已启用」，并保证模块被导入
    # （事件监听器在 import 时注册；若无人 import 则推送静默失效）
    notification_push.install()
    logger.info(
        "通知实时推送已启用（单用户连接上限 {}；进程内推送，多副本部署下"
        "推送仅在持有连接的副本生效，其余副本依赖前端轮询兜底）",
        manager.max_per_user,
    )

    # 启动常驻任务队列（取代请求级 BackgroundTasks）+ 僵尸任务巡检
    import asyncio

    await job_queue.start()
    recovery_task = asyncio.create_task(_stale_job_recovery_loop(), name="job-recovery")

    yield

    # 优雅停机：先停巡检，再等待在途任务收尾
    recovery_task.cancel()
    await asyncio.gather(recovery_task, return_exceptions=True)
    await job_queue.stop()
    # 关闭全部 WS 连接（1001 going away）：让客户端主动重连到新实例，
    # 而不是把一个已被服务端遗弃的连接留在那里反复失败
    closed = await manager.close_all()
    # 指标同步归零：进程若在同一实例内被重新拉起（测试 / 热重载），
    # 残留的非零 active 会显示「有连接」而实际没有，误导排查。
    metrics.ws_connections_active.set(0, ("notifications",))
    if closed:
        logger.info("已关闭 {} 条 WebSocket 连接（服务端下线）", closed)
    logger.info("律小智服务正在关闭")


def _assert_forwarded_allow_ips_safe() -> None:
    """生产环境禁止 uvicorn 的 `--forwarded-allow-ips=*`。

    🚨 这条守卫守的是 **Q-K 修复的下游**——少了它，应用层那份信任判断会被架空：

    uvicorn 的 `ProxyHeadersMiddleware`（`uvicorn/middleware/proxy_headers.py:32-40`）在
    `always_trust`（即 `forwarded_allow_ips` 含 `*`）时取
    **`x_forwarded_for_hosts[0]`**——**左起第一个，正是客户端可以随便写的那个**——
    然后**直接改写 `scope["client"]`**（第 68 行）。这一步发生在**我们的中间件之前**，
    于是 `middleware.py::_client_ip` 拿到的"直接对端"已经是攻击者伪造的值，
    `TRUSTED_PROXIES` 判断自然不成立，每换一个头 = 换一个限流桶 ⇒ **修复被完全绕过**。

    该值来自 `--forwarded-allow-ips` 或环境变量 `FORWARDED_ALLOW_IPS`
    （`uvicorn/config.py:333`，默认 `127.0.0.1`）。**在网关 / CDN / ALB 后面把它设成 `*`**
    是极常见的"先让它跑起来"操作，一旦发生服务会**静默**失去 IP 维度的一切控制
    （限流分桶 + 审计日志里的来源 IP）。

    ⇒ 与 `_assert_cors_origins_safe` 同款原则：**显式失败优于静默错误**。
    """
    raw = os.environ.get("FORWARDED_ALLOW_IPS")
    if not raw:
        return
    entries = [e.strip() for e in raw.split(",") if e.strip()]
    if "*" not in entries:
        return

    msg = (
        "FORWARDED_ALLOW_IPS 不得包含 '*'：uvicorn 会据 X-Forwarded-For 的**左起第一个**"
        "（客户端可伪造）改写 scope['client']，发生在应用层信任判断**之前**，"
        "会使限流分桶与审计来源 IP 全部可被攻击者指定"
    )
    if settings.ENVIRONMENT == "production":
        raise ConfigurationError(msg, details={"forwarded_allow_ips": entries})
    logger.warning("⚠️ {}（当前 ENVIRONMENT={}，未阻断）", msg, settings.ENVIRONMENT)


def _assert_cors_origins_safe() -> None:
    """生产环境禁止「通配 origin + 凭据」——这两者在 CORS 语义上互斥。

    🚨 为什么必须显式失败（而不是靠文档约定）：

    `CORSMiddleware(allow_origins=["*"], allow_credentials=True)` **不会**报错，
    也不会被浏览器拦成安全的样子——Starlette 在这个组合下走的是
    `starlette/middleware/cors.py:167`：

    ```python
    if self.allow_all_origins and self.allow_credentials:
        self.allow_explicit_origin(headers, origin)   # 原样回显请求方 origin
    ```

    即**任意站点**都能发起带 Cookie 的跨域请求并读到响应，等价于把登录态
    开放给全网。而本服务 `allow_credentials=True` 是**硬编码**的（Cookie 承载
    refresh token 需要它），所以唯一的开关就是 `CORS_ORIGINS`。

    把 `CORS_ORIGINS=*` 写进生产环境是极常见的「先让它跑起来」操作，
    一旦发生，服务会**静默**变成全开放。按 P0-3 的同款原则
    （显式失败优于静默错误），这里在生产环境直接拒绝启动。
    """
    origins = settings.cors_origins_list
    if settings.ENVIRONMENT == "production" and "*" in origins:
        raise ConfigurationError(
            "生产环境 CORS_ORIGINS 不得包含通配 '*'：它与 allow_credentials=True 互斥，"
            "会被 Starlette 解释为「回显任意 origin」，等价于向全网开放登录态读取",
            details={"cors_origins": origins},
        )


async def _too_long_to_413(request: Request, exc: RequestValidationError):
    """`string_too_long` 从默认 422 提升为 **413**（内容过长 ⇒ 转上传/异步）。

    2026-09-22 I1 收口尾巴（见
    `deliverables/product-strategy/request-text-limits-closure-2026-09-22.md` §5 遗留）：
    裁定表要求「超限 413 + 提示转上传，禁静默截断」。

    ⚠️ 只有 `string_too_long` 升 413；缺字段 / 类型错等**仍走 422**（委托默认处理器），
    否则会丢「缺必填字段」这类更该红的信号。放在模块级只为测试能 import 它。
    """
    long_errs = [e for e in exc.errors() if e.get("type") == "string_too_long"]
    if not long_errs:
        return await request_validation_exception_handler(request, exc)
    first = long_errs[0]
    ctx = first.get("ctx", {}) or {}
    max_len = ctx.get("max_length")
    field = ".".join(
        str(p) for p in first.get("loc", ()) if isinstance(p, (str, int)) and p != "body"
    )
    return JSONResponse(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        content={
            "success": False,
            "error": {
                "code": "CONTENT_TOO_LARGE",
                "message": (
                    f"文本长度超出上限{f'（{field} 上限 {max_len} 字）' if max_len else ''}"
                    "。请改用文件上传 + 异步任务，系统会按文档/页数处理，不做截断。"
                ),
                "field": field or None,
                "max_length": max_len,
                "guidance": "upload_or_async",
            },
        },
    )


def create_app() -> FastAPI:
    # 结构化日志需在应用装配前完成，否则启动早期日志会丢失上下文。
    configure_logging(settings.LOG_FORMAT, settings.LOG_LEVEL)

    # P2-13：OpenAPI 全量枚举端点利于侦察，生产环境关闭交互式文档与 schema
    # （前端不依赖它们；联调/预发环境不受影响）。
    _is_prod = settings.ENVIRONMENT == "production"
    app = FastAPI(
        title="律小智 AI 法务助手 API",
        description="律所智能协作平台（LawyerOS）+ 个人/企业法务助手（Legal Copilot）",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if _is_prod else "/api/docs",
        redoc_url=None if _is_prod else "/api/redoc",
        openapi_url=None if _is_prod else "/api/openapi.json",
    )

    # 中间件顺序：后注册者位于外层。ErrorHandler 需在其捕获目标之外。
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(RequestContextMiddleware)
    # CSRF：保护 Cookie 承载的 refresh 端点（Bearer 请求自动豁免）
    if settings.AUTH_COOKIE_ENABLED:
        app.add_middleware(
            CsrfMiddleware,
            enabled=True,
            protected_prefixes=tuple(
                p.strip() for p in settings.CSRF_PROTECTED_PREFIXES.split(",") if p.strip()
            ),
            strict_all_writes=settings.CSRF_STRICT_ALL_WRITES,
            header_name=settings.CSRF_HEADER_NAME,
        )
    # 限流：默认开启（安全默认值）。见 settings.RATE_LIMIT_ENABLED
    if settings.RATE_LIMIT_ENABLED:
        app.add_middleware(
            RateLimitMiddleware,
            enabled=True,
            max_requests=settings.RATE_LIMIT_LOGIN_MAX,
            window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS,
            upload_max=settings.RATE_LIMIT_UPLOAD_MAX,
            generate_max=settings.RATE_LIMIT_GENERATE_MAX,
        )
    # CORS 必须最外层：Cookie 跨域携带要求 allow_credentials + 精确 origin
    # （通配 "*" 与 credentials 互斥，浏览器会直接拒绝）
    _assert_cors_origins_safe()
    _assert_forwarded_allow_ips_safe()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Response-Time"],
    )

    app.include_router(api_router, prefix="/api/v1")

    # 2026-09-22 I1 收口尾巴：超长文本从默认 422 提升为 **413**（内容过长 ⇒ 转上传/异步）。
    # 裁定表要求「超限 413 + 提示转上传，禁静默截断」
    # （见 deliverables/product-strategy/request-text-limits-closure-2026-09-22.md §5 遗留）。
    # ⚠️ 处理器本体在模块级 `_too_long_to_413`（便于测试 import）；这里只注册。
    app.add_exception_handler(RequestValidationError, _too_long_to_413)


    @app.get("/api/health", tags=["系统"])
    async def health_check():
        # 模型配置自检：生产环境若某档为 False，该档请求会直接 500
        # （见 ai/router.py 的 ConfigurationError 守卫），运维可据此快速定位。
        from app.ai.router import provider_status

        providers = provider_status()
        if settings.ENVIRONMENT == "production":
            # 生产环境只回传是否齐备，不暴露档位细节
            llm_ready = all(providers.values())
        else:
            llm_ready = True

        return {
            "success": True,
            "data": {
                "status": "healthy" if llm_ready else "degraded",
                "service": "NLawer API",
                "version": "0.1.0",
                "environment": settings.ENVIRONMENT,
                "database": "sqlite" if settings.is_sqlite else "postgres",
                # 非生产环境回传各档位明细，便于本地排查"为何走了 Mock"
                "llm_providers": providers if settings.ENVIRONMENT != "production" else None,
                # 运维可观测性：队列积压与限流后端类型
                "job_queue_pending": job_queue.pending,
                "rate_limit_backend": "redis" if settings.REDIS_URL else "memory",
                # 内容安全（P0-13）：合规配置自查。《生成式人工智能服务管理暂行办法》
                # 第十四条要求提供者履行违法内容处置义务——关闭即不具备备案条件，
                # 因此这里显式暴露，便于运维与合规巡检时一眼确认。
                "moderation": {
                    "enabled": settings.MODERATION_ENABLED,
                    "backend": settings.MODERATION_BACKEND,
                    "external_configured": bool(settings.MODERATION_API_KEY),
                    "check_input": settings.MODERATION_CHECK_INPUT,
                    "check_output": settings.MODERATION_CHECK_OUTPUT,
                    "fail_closed": settings.MODERATION_FAIL_CLOSED,
                    "report_channel_configured": bool(
                        settings.MODERATION_REPORT_ENABLED and settings.MODERATION_REPORT_URL
                    ),
                },
                # 投诉举报（P0-13 / 第十五条）：算法备案会核查「是否有便捷入口、
                # 是否公布处理流程与反馈时限」，因此同样暴露供合规巡检。
                "complaint": {
                    "enabled": True,
                    "due_days": COMPLAINT_DUE_DAYS,
                    "anonymous_allowed": True,
                },
                # 审计保留期（P1 / 等保 2.0 三级）：留存不少于 180 天。
                # 低于下限时服务会强制抬到 180 天，因此这里暴露的是**生效值**，
                # 便于合规巡检直接比对配置与生效值是否一致。
                "audit_retention": {
                    "enabled": settings.AUDIT_ENABLED,
                    "effective_days": retention_days(),
                    "baseline_days": SECURITY_BASELINE_DAYS,
                    "configured_days": settings.AUDIT_RETENTION_DAYS,
                    "admin_enabled": settings.AUDIT_RETENTION_ADMIN_ENABLED,
                },
            },
        }

    # ------------------------------------------------------------------
    # 探针拆分（P0-10）：liveness 与 readiness 语义不同，混用会导致
    # 「数据库挂了但进程没挂」时编排器误杀容器，反而扩大故障。
    # ------------------------------------------------------------------

    @app.get("/api/health/livez", tags=["系统"])
    async def liveness_probe():
        """存活探针：只回答「进程还在吗」。

        **绝不能在这里检查数据库**——依赖不可用时应由 readiness 摘流量，
        而不是让编排器重启一个本身健康的进程（重启治不了下游故障，
        只会引发滚动重启风暴）。
        """
        return {"status": "alive"}

    @app.get("/api/health/readyz", tags=["系统"])
    async def readiness_probe():
        """就绪探针：回答「能否接流量」，含真实 DB 往返。

        用 `SELECT 1` 而非连接池状态判断——连接可能被中间件/防火墙静默断开，
        只有真正往返一次才能发现。失败返回 503（非 200），编排器据此摘除流量。
        """
        from fastapi.responses import JSONResponse
        from sqlalchemy import text

        db_ok = True
        db_error = None
        try:
            async with async_session_factory() as db:
                await db.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 探针必须吞异常并转为状态码
            db_ok = False
            db_error = type(exc).__name__
            logger.warning("就绪探针数据库检查失败: {}", exc)

        payload = {
            "status": "ready" if db_ok else "not_ready",
            "checks": {"database": "ok" if db_ok else f"error:{db_error}"},
        }
        return JSONResponse(status_code=200 if db_ok else 503, content=payload)

    @app.get("/metrics", tags=["系统"], response_class=PlainTextResponse)
    async def prometheus_metrics(request: Request):
        """Prometheus 文本格式指标（P0-10）。

        - 暴露方式：默认仅内网/集群内可达（生产由 Ingress 限制），
          不随 `/api` 前缀走，便于与网关的对外路由分离。
        - P2-13：生产环境额外强制**仅环回抓取**——指标含限流命中数等
          业务信号，注释约定「Ingress 限制」不足以兜底，代码层再设一道门；
          远程抓取应经反代把流量落在环回/sidecar 上。
        - 队列积压在抓取时刷新，避免为指标引入后台定时任务。
        - `/metrics` 自身不计入请求指标（见 middleware.METRICS_EXCLUDED_PATHS），
          否则抓取频率变化会造成指标抖动。
        """
        if not settings.METRICS_ENABLED:
            return PlainTextResponse("metrics disabled\n", status_code=404)
        client_host = request.client.host if request.client else ""
        if settings.ENVIRONMENT == "production" and client_host not in ("127.0.0.1", "::1"):
            logger.warning("拒绝非环回 /metrics 抓取: {}", client_host)
            return PlainTextResponse("not found\n", status_code=404)
        job_queue.refresh_metrics()
        return PlainTextResponse(
            metrics.render(), media_type="text/plain; version=0.0.4; charset=utf-8"
        )

    @app.get("/", tags=["系统"])
    async def root():
        return {"service": "律小智 AI 法务助手", "version": "0.1.0", "docs": "/api/docs"}

    # 本地存储**不再以 StaticFiles 挂载**（原实现无鉴权，任何人可直连 URL 下载
    # 他人租户证据与文书）。改由 `app/api/v1/files.py` 提供带租户校验的下载接口。
    storage_dir = os.path.abspath(settings.LOCAL_STORAGE_PATH)
    os.makedirs(storage_dir, exist_ok=True)

    return app


app = create_app()
