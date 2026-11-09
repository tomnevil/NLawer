"""Alembic 环境：从 app 元数据生成/应用迁移。

- 导入 `app.models` 以完整注册 `Base.metadata`（autogenerate 才不会漏表）。
- 连接串使用同步 URL（settings.DATABASE_URL_SYNC），与生产 `sync_engine` 对齐。
- Postgres 方言且 `VECTOR_BACKEND=pgvector` 时才尝试建扩展；不可用时降级为 Text。
- 迁移文件本身对 `knowledge_embeddings.embedding` 做方言/扩展可用性分支。
"""
# 确保 backend 目录在 sys.path，便于 `import app`
import logging
import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool, text

from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.models  # noqa: E402,F401  全量注册模型
from app.config import settings  # noqa: E402
from app.models.base import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass

# 用 settings 中的同步连接串覆盖 alembic.ini 占位
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    logger = logging.getLogger("alembic.env")

    with connectable.connect() as connection:
        # 仅当向量后端真正需要 pgvector 时才建扩展：
        #   - VECTOR_BACKEND=memory/none 不碰扩展，标准 postgres 镜像也能迁移
        #   - 扩展缺失/无权限时记录明确错误并降级（迁移文件改用 Text 列），
        #     不让整个应用因向量能力不可用而启动失败
        if connection.dialect.name == "postgresql" and settings.vector_backend == "pgvector":
            try:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                connection.commit()
            except Exception as exc:  # noqa: BLE001 - 权限/扩展缺失均降级
                connection.rollback()
                logger.error(
                    "pgvector 扩展不可用或无权创建（%s）。请改用 pgvector/pgvector:pg16 "
                    "镜像并授予 CREATE EXTENSION 权限，或在 .env 设置 "
                    "VECTOR_BACKEND=memory；本次迁移将 embedding 列降级为 Text。",
                    exc,
                )

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
