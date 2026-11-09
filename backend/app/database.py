"""数据库基座：异步引擎 + 会话工厂 + 声明式基类 + 时间戳 Mixin。

SQLite 与 Postgres 双方言兼容：SQLite 不支持连接池参数，故按方言条件化。
`TimestampMixin` 同时声明 Python 侧 default/onupdate 与 server_default，
避免异步会话下 Pydantic 序列化触发惰性 IO 抛 MissingGreenlet。
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

from sqlalchemy import DateTime, create_engine, func, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

logger = logging.getLogger(__name__)

_engine_kwargs: dict[str, Any] = {"echo": settings.DEBUG}
if not settings.is_sqlite:
    # 仅 Postgres 支持连接池参数
    _engine_kwargs.update(pool_size=20, max_overflow=10, pool_pre_ping=True)

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 同步引擎（仅 Alembic 迁移使用）
sync_engine = create_engine(settings.DATABASE_URL_SYNC, pool_pre_ping=True)


class Base(DeclarativeBase):
    """声明式基类。"""


class TimestampMixin:
    """创建 / 更新时间。

    Python 侧 default 让值在 flush 前即写入对象，避免异步会话下
    Pydantic 序列化触发惰性加载抛 MissingGreenlet。
    server_default 保留，使绕过 ORM 的写入仍有兜底。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每请求一个会话，正常结束提交，异常回滚。"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def _run_alembic_upgrade() -> None:
    """同步执行 Alembic 迁移到 head（生产环境初始化表结构用）。"""
    from alembic.config import Config

    from alembic import command

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(base_dir, "alembic.ini"))
    command.upgrade(cfg, "head")


async def _pgvector_installed_on(conn) -> bool:
    """查询当前连接所在库是否**已安装** pgvector 扩展。"""
    value = (
        await conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'"))
    ).scalar()
    return bool(value)


async def _pgvector_installed() -> bool:
    if not settings.is_postgres:
        return False
    async with engine.connect() as conn:
        return await _pgvector_installed_on(conn)


def _sync_vector_state(installed: bool) -> None:
    """把扩展可用性同步到 ORM 列类型与向量后端开关。

    - 扩展不可用：`knowledge_embeddings.embedding` 降级为 JSON 文本列，
      否则 `create_all` 会在 `vector` 类型上直接失败（整体启动崩）。
    - `PGVECTOR_READY` 供 `build_vector_store()` 判断是否可安全使用 PgVectorStore。
    """
    from app.models import embedding as embedding_model

    embedding_model.PGVECTOR_READY = installed
    if settings.is_postgres and not installed:
        embedding_model.KnowledgeEmbedding.__table__.c.embedding.type = (
            embedding_model.JsonVector()
        )
        logger.warning(
            "pgvector 不可用：knowledge_embeddings.embedding 降级为 JSON 文本，"
            "向量检索回退内存/BM25（不影响其它功能启动）"
        )


async def _prepare_postgres_vector(conn) -> bool:
    """按向量后端需要决定是否创建 pgvector，并处理不可用降级。

    仅当 `VECTOR_BACKEND` 解析为 `pgvector` 时才尝试 `CREATE EXTENSION`；
    `memory`/`none` 不再无条件建扩展。扩展缺失或无权限时记录明确错误并降级，
    不阻断应用启动。
    """
    if not settings.is_postgres:
        _sync_vector_state(False)
        return False

    installed = await _pgvector_installed_on(conn)
    if not installed and settings.vector_backend == "pgvector":
        try:
            # 独立 SAVEPOINT：CREATE EXTENSION 失败不让外层事务进入 aborted 状态
            async with conn.begin_nested():
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            installed = True
            logger.info("pgvector 扩展已启用")
        except Exception as e:  # noqa: BLE001 - 权限/扩展缺失均降级
            installed = False
            logger.error(
                "pgvector 扩展不可用或无权创建（%s）。"
                "请改用 pgvector/pgvector:pg16 镜像并授予 CREATE EXTENSION 权限，"
                "或在 backend/.env 设置 VECTOR_BACKEND=memory；"
                "本次将向量列降级为 JSON 文本并继续启动。",
                e,
            )

    _sync_vector_state(installed)
    return installed


async def init_db() -> None:
    """初始化表结构。

    - 生产（ENVIRONMENT=production）：走 Alembic 迁移，表结构可版本化演进。
    - 开发 / 测试：保留 `create_all`，零依赖、便于单测与本地启动。
    - Postgres：只有向量后端需要 pgvector 时才建扩展；不可用时降级而非崩溃。
    """
    if settings.ENVIRONMENT == "production":
        try:
            await asyncio.to_thread(_run_alembic_upgrade)
            logger.info("数据库迁移完成(alembic upgrade head)")
            # 迁移里可能已建扩展；探测后同步 ORM 列类型与向量后端开关
            _sync_vector_state(await _pgvector_installed())
        except Exception as e:  # 极端兜底：迁移失败不阻断启动
            logger.error(f"alembic 迁移失败，回退到 create_all: {e}")
            async with engine.begin() as conn:
                await _prepare_postgres_vector(conn)
                await conn.run_sync(Base.metadata.create_all)
    else:
        async with engine.begin() as conn:
            await _prepare_postgres_vector(conn)
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"数据库初始化完成(create_all): {engine.url}")
