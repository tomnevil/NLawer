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


async def init_db() -> None:
    """初始化表结构。

    - 生产（ENVIRONMENT=production）：走 Alembic 迁移，表结构可版本化演进。
    - 开发 / 测试：保留 `create_all`，零依赖、便于单测与本地启动。
    """
    if settings.ENVIRONMENT == "production":
        try:
            await asyncio.to_thread(_run_alembic_upgrade)
            logger.info("数据库迁移完成(alembic upgrade head)")
        except Exception as e:  # 极端兜底：迁移失败不阻断启动
            logger.error(f"alembic 迁移失败，回退到 create_all: {e}")
            async with engine.begin() as conn:
                if settings.is_postgres:
                    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.run_sync(Base.metadata.create_all)
    else:
        async with engine.begin() as conn:
            if settings.is_postgres:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"数据库初始化完成(create_all): {engine.url}")
