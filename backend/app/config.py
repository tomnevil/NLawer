"""律小智平台运行配置。

分组原则沿用 AIECO `app/config.py`，按「服务 / 数据库 / JWT / LLM / 存储 / 租户 /
计费 / 复核 / 审计」分组。LLM 相关配置**全部留空即自动降级 MockProvider**，
保证零网络依赖也能完整演示双产品线全流程。
"""
from typing import List, Optional

from pydantic import ConfigDict, model_validator
from pydantic_settings import BaseSettings

#: 出厂默认密钥：仅用于本地零配置启动，生产环境必须替换
WEAK_SECRET_KEY: str = "change-me-nlawer-jwt-secret"


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    # ---- 服务 ----
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    API_RELOAD: bool = True
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    # ---- 可观测性（P0-10）----
    # 日志格式：text（人眼可读，本地开发）/ json（结构化，生产采集器直读）
    LOG_FORMAT: str = "text"
    LOG_LEVEL: str = "INFO"
    # 是否暴露 /metrics（Prometheus 文本格式）。生产默认开启；
    # 需在网关层限制为集群内可达，避免业务指标（如限流命中数）外泄。
    METRICS_ENABLED: bool = True
    # 四个前端应用：web 3000 / lawyer 3001 / admin 3002 / im 3003
    CORS_ORIGINS: str = (
        "http://localhost:3000,http://localhost:3001,"
        "http://localhost:3002,http://localhost:3003"
    )

    # ---- 数据库（异步 URL + Alembic 用同步 URL）----
    DATABASE_URL: str = "sqlite+aiosqlite:///./storage/nlawer.db"
    DATABASE_URL_SYNC: str = "sqlite:///./storage/nlawer.db"

    # ---- 字段加密（P2-11）----
    # 敏感字段信封加密的**独立**主密钥。为空时回退为从 SECRET_KEY 派生
    # （兼容存量），但那样 JWT 密钥的轮换/泄露会连带密文作废——
    # 生产环境必须显式设置（32 字节以上随机串，与 SECRET_KEY 不同值）。
    DATA_ENCRYPTION_KEY: str = ""

    # ---- JWT ----
    SECRET_KEY: str = WEAK_SECRET_KEY
    ALGORITHM: str = "HS256"
    # access 令牌有效期。**由 1440（24h）下调为 30 分钟**：
    # 令牌存 localStorage 时，24h 有效期意味着一次 XSS 即可窃取一整天的完整会话。
    # 缩短后配合 refresh 自动续期，XSS 窗口从"天"级压到"分钟"级。
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_DAYS: int = 7

    # ---- 认证 Cookie / CSRF ----
    # refresh 令牌改存 HttpOnly + SameSite Cookie，前端 JS 无法读取 →
    # XSS 无法窃取长效凭据。代价是必须补 CSRF 防护（见 core/csrf.py）。
    AUTH_COOKIE_ENABLED: bool = True
    AUTH_COOKIE_SECURE: bool = False  # 生产必须置 True（HTTPS only）
    AUTH_COOKIE_SAMESITE: str = "lax"  # lax 已可挡跨站 POST；strict 更严但影响外链回跳
    AUTH_COOKIE_PATH: str = "/"
    REFRESH_COOKIE_NAME: str = "nlaw_rt"
    CSRF_COOKIE_NAME: str = "nlaw_csrf"
    CSRF_HEADER_NAME: str = "X-CSRF-Token"
    # 承载 refresh cookie 的路径前缀：CSRF 校验必须覆盖这些写操作。
    # refresh = 凭 Cookie 换新令牌；logout = 清 Cookie（否则可被第三方站点
    # 静默登出，属低危但真实的 CSRF）。
    CSRF_PROTECTED_PREFIXES: str = "/api/v1/auth/refresh,/api/v1/auth/logout"
    # 是否对**所有**写方法强制 CSRF（开启后需前端全面携带令牌，默认关闭，
    # 仅保护 cookie 承载的 refresh 端点 + 可选开启其他）
    CSRF_STRICT_ALL_WRITES: bool = False

    # ---- 限流（敏感端点，避免影响 SSE / 轮询）----
    # 默认开启：安全默认值。本地开发/单测如无需要可显式设 RATE_LIMIT_ENABLED=false。
    RATE_LIMIT_ENABLED: bool = True
    # 滑动窗口内同一 IP 对单个敏感端点的最大请求数
    RATE_LIMIT_LOGIN_MAX: int = 20
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    # Redis 限流后端（多副本共享计数）。留空则降级为进程内内存计数，
    # 此时多副本部署下每个实例独立计数，实际阈值 = 配置值 × 副本数。
    REDIS_URL: Optional[str] = None
    # 上传类重端点的独立阈值（比登录更严，防止存储与算力滥用）
    RATE_LIMIT_UPLOAD_MAX: int = 10
    # 生成类（AI 重计算）端点阈值
    RATE_LIMIT_GENERATE_MAX: int = 15
    # ---- 可信代理（CIDR 列表，逗号分隔）----
    # 只有**直接对端**落在这个集合里时，才采信它注入的 `X-Forwarded-For`。
    # 🚨 不加这道判断的后果：`X-Forwarded-For` 是**请求头**，客户端可以随便写，
    #   "取首段" ⇒ 攻击者每换一个伪造头就换一个限流桶 ⇒ 注册/登录限流形同虚设。
    # 默认值只含环回：本机反向代理（nginx / Caddy / sidecar）是最常见也最安全的一跳；
    # 远程客户端不可能让 `request.client.host` 变成 127.0.0.1。
    # ⚠️ 若网关在**另一台主机/容器**，必须把它的地址加进来，否则整站会共用网关那一个
    #   桶（限流变严而非变松，是安全方向的失效，但会影响可用性）。
    TRUSTED_PROXIES: str = "127.0.0.1,::1"

    # ---- LLM 模型路由：三档（留空即降级 MockProvider）----
    # 低成本档：通用问答 / 常规文书
    LLM_CHEAP_API_KEY: Optional[str] = None
    LLM_CHEAP_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    LLM_CHEAP_MODEL: str = "qwen-plus"
    # 高精度档：赔偿计算 / 合规判定 / 证据分析
    LLM_STRONG_API_KEY: Optional[str] = None
    LLM_STRONG_BASE_URL: str = "https://api.deepseek.com"
    LLM_STRONG_MODEL: str = "deepseek-chat"
    # 私有化档：敏感数据不出域
    LLM_LOCAL_API_KEY: Optional[str] = None
    LLM_LOCAL_BASE_URL: str = "http://localhost:11434/v1"
    LLM_LOCAL_MODEL: str = "qwen2.5:32b"
    # 调用超时（秒）
    LLM_TIMEOUT_SECONDS: int = 60

    # ---- 合同审查（P0-16）----
    # 合同通常含商业秘密（客户名单、报价、结算方式），默认按「敏感数据不出域」
    # 处理：`True` ⇒ 路由到 ModelTier.LOCAL。
    #
    # ⚠️ 这**不是**一个可以随手关掉的开关：LOCAL 档未配置 Provider 时，
    # `ModelRouter` 在生产环境会**直接抛 ConfigurationError**（router.py:91-97），
    # 即敏感合同在生产**根本无法审查**，返回 status=failed 且不计费。
    # 这是 PRD Q1 的已知阻塞项——需要安全/法务拍板「合同是否属敏感数据」；
    # 在拍板之前保持 `True` 是唯一诚实的选择，**不要为了跑通而改成 False**。
    CONTRACT_REVIEW_SENSITIVE: bool = True

    # ---- 向量检索（留空则只用 BM25）----
    EMBEDDING_API_KEY: Optional[str] = None
    EMBEDDING_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_MODEL: str = "text-embedding-v3"
    EMBEDDING_DIM: int = 1536
    # 向量后端：auto（Postgres+pgvector 可用即用）/ pgvector / memory / none
    VECTOR_BACKEND: str = "auto"

    # ---- 存储 ----
    LOCAL_STORAGE_PATH: str = "./storage/local"

    # ---- 多租户 ----
    DEFAULT_TENANT_ID: str = "platform"
    ALLOW_TENANT_FALLBACK: bool = True

    # ---- 计费（单位：分）----
    WORK_ORDER_PRICE_CENTS: int = 4900
    WORK_ORDER_URGENT_SURCHARGE_CENTS: int = 3000

    # ---- 强制复核阈值（元）----
    FORCED_REVIEW_AMOUNT_THRESHOLD: int = 500000

    # ---- 审计 ----
    AUDIT_ENABLED: bool = True
    # 审计日志保留天数。等保 2.0（三级）要求审计记录留存 >=180 天；
    # 配成更低值时 `retention_days()` 会告警并**强制抬到 180**（宁可多留）。
    AUDIT_RETENTION_DAYS: int = 365
    # 归档目录（清理前必须先落盘，见 services/audit_retention.py）
    AUDIT_ARCHIVE_DIR: str = "storage/audit_archive"
    # 是否开放审计保留期管理端点（平台管理员）。默认开启，便于运维自查合规。
    AUDIT_RETENTION_ADMIN_ENABLED: bool = True

    # ---- 内容安全审核（P0-13，《生成式人工智能服务管理暂行办法》第十四条）----
    # 关闭 = 不合规。生产环境强制开启（见 model_validator）。
    MODERATION_ENABLED: bool = True
    # 后端策略：auto（有 Key 用外部，否则内置词库）/ builtin / external / none
    MODERATION_BACKEND: str = "auto"
    # 外部审核 API（阿里云绿网 / 腾讯天御 / 自建）。留空则仅用内置词库。
    MODERATION_API_KEY: Optional[str] = None
    MODERATION_BASE_URL: str = "https://green-cip.cn-shanghai.aliyuncs.com"
    MODERATION_SERVICE: str = "nlawer-text-scan"
    MODERATION_TIMEOUT_SECONDS: int = 5
    # 内置词库外挂文件（生产由合规团队维护，支持热更新；留空用内置最小集）
    MODERATION_TERMS_FILE: Optional[str] = None
    # 外部审核不可用时的失败模式：
    #   True  = fail-closed（默认，拒绝未审核内容——法律产品宁可拒答不可漏放）
    #   False = fail-open（放行但打标降级）
    MODERATION_FAIL_CLOSED: bool = True
    # 输入侧审核：命中 BLOCK 及以上则拒绝调用模型（"停止生成"）
    MODERATION_CHECK_INPUT: bool = True
    # 输出侧审核：流式逐片段校验跨片段窗口（"停止传输"）
    MODERATION_CHECK_OUTPUT: bool = True
    # 监管上报通道（第十四条要求"向有关主管部门报告"）。
    # 未配置时命中 ESCALATE 的事件会标记 pending_report 由人工兜底，绝不丢弃。
    MODERATION_REPORT_ENABLED: bool = False
    MODERATION_REPORT_URL: Optional[str] = None

    # ---- 异步任务 ----
    # 并发槽：同时运行的 AI 任务上限（= 队列消费者数量）
    JOB_MAX_CONCURRENCY: int = 4
    JOB_MAX_RETRIES: int = 3
    JOB_RETRY_BASE_DELAY: float = 2.0
    # 队列最大长度；满时反压等待（不丢弃任务，任务已落库）
    JOB_QUEUE_MAX_SIZE: int = 500
    # 僵尸任务判定阈值：RUNNING 且超过该秒数无心跳，视为进程崩溃遗留并回收
    JOB_STALE_TIMEOUT_SECONDS: int = 300
    # 僵尸任务回收巡检间隔（秒）
    JOB_RECOVERY_INTERVAL_SECONDS: int = 60

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def trusted_proxies_list(self) -> List[str]:
        """可信代理条目（IP 或 CIDR）。空字符串配置 ⇒ 返回空列表 = **不信任任何代理**。"""
        return [p.strip() for p in self.TRUSTED_PROXIES.split(",") if p.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.DATABASE_URL.startswith("postgres")

    @property
    def vector_backend(self) -> str:
        """解析实际向量后端：auto -> Postgres 可用 pgvector 时用 pgvector，否则内存。"""
        if self.VECTOR_BACKEND != "auto":
            return self.VECTOR_BACKEND
        return "pgvector" if self.is_postgres else "memory"

    @model_validator(mode="after")
    def _fail_fast_on_weak_production_secret(self) -> "Settings":
        """生产环境禁止带默认弱密钥启动（fail-fast，避免弱签名上线）。"""
        if self.ENVIRONMENT == "production" and (
            self.SECRET_KEY == WEAK_SECRET_KEY or self.SECRET_KEY.startswith("change-me")
        ):
            raise ValueError(
                "生产环境禁止使用默认 SECRET_KEY，"
                "请通过环境变量 SECRET_KEY 设置强密钥（建议 32 字节以上随机串）"
            )
        return self

    @model_validator(mode="after")
    def _fail_fast_on_debug_in_production(self) -> "Settings":
        """生产环境禁止 DEBUG=true：SQL echo 会把含令牌/口令哈希的语句写进日志。"""
        if self.ENVIRONMENT == "production" and self.DEBUG:
            raise ValueError(
                "生产环境禁止 DEBUG=true（database.py 的 SQL echo 跟随 DEBUG，"
                "会把含敏感数据的语句写进日志）。请显式设置 DEBUG=false。"
            )
        return self

    @model_validator(mode="after")
    def _fail_fast_on_insecure_cookie_in_production(self) -> "Settings":
        """生产环境启用认证 Cookie 时，必须 Secure + 非 lax 以外的弱配置。

        `Secure=False` 意味着 Cookie 会经明文 HTTP 传输，攻击者在同网络下
        即可嗅探到 refresh 令牌——这会把「HttpOnly 防 XSS」的收益全部抵消。
        宁可启动失败，也不要带这种配置上线。
        """
        if self.ENVIRONMENT == "production" and self.AUTH_COOKIE_ENABLED:
            if not self.AUTH_COOKIE_SECURE:
                raise ValueError(
                    "生产环境启用 AUTH_COOKIE 时必须设置 AUTH_COOKIE_SECURE=true"
                    "（否则 refresh 令牌可经 HTTP 明文嗅探）"
                )
        return self

    @model_validator(mode="after")
    def _fail_fast_on_missing_moderation_in_production(self) -> "Settings":
        """生产环境必须启用内容安全审核。

        《生成式人工智能服务管理暂行办法》第十四条把「发现违法内容并及时
        采取停止生成、停止传输、消除等处置措施」设为**法定义务**，不是可选项。
        缺失审核能力的服务不具备备案条件，也没有理由上线。

        另外：单靠内置词库不能作为生产唯一防线（词库规模与覆盖度不足），
        因此生产环境要求必须配置外部审核 API。
        """
        if self.ENVIRONMENT != "production":
            return self
        if not self.MODERATION_ENABLED:
            raise ValueError(
                "生产环境禁止关闭内容安全审核（MODERATION_ENABLED=false）。"
                "《生成式人工智能服务管理暂行办法》第十四条要求提供者履行违法内容处置义务。"
            )
        if self.MODERATION_BACKEND == "none":
            raise ValueError("生产环境禁止使用 MODERATION_BACKEND=none")
        if self.MODERATION_BACKEND != "external" and not self.MODERATION_API_KEY:
            raise ValueError(
                "生产环境必须配置外部内容审核 API（MODERATION_API_KEY），"
                "仅靠内置最小词库无法满足备案要求"
            )
        return self

    @model_validator(mode="after")
    def _validate_moderation_backend(self) -> "Settings":
        """任意环境下的取值合法性校验（早失败优于运行时报错）。"""
        allowed = {"auto", "builtin", "external", "none"}
        if self.MODERATION_BACKEND not in allowed:
            raise ValueError(
                f"MODERATION_BACKEND 取值无效: {self.MODERATION_BACKEND}，"
                f"应为 {sorted(allowed)} 之一"
            )
        if self.MODERATION_BACKEND == "external" and not self.MODERATION_API_KEY:
            raise ValueError(
                "MODERATION_BACKEND=external 但 MODERATION_API_KEY 为空，无法调用外部审核"
            )
        return self


settings = Settings()
