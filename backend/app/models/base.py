"""模型 Mixin：租户隔离等横切字段。

`Base` 与 `TimestampMixin` 定义在 `app.database`（单一来源），此处仅重新导出，
业务模型统一从本模块导入，避免多处定义导致元数据分裂。
"""
from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, TimestampMixin  # noqa: F401  集中导出

__all__ = ["Base", "TimestampMixin", "TenantMixin", "json_col"]


class TenantMixin:
    """租户隔离 Mixin。

    `tenant_id` 形如 `firm_hlw`（律所）/ `ent_acme`（企业）/ `platform`（平台）。
    平台级共享数据（法规库、案例库、文书模板库）使用 `platform` 作为租户值。
    """

    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="platform",
        index=True,
        comment="租户标识，业务表按此字段逻辑隔离",
    )


def json_col() -> Any:
    """跨方言兼容的 JSON 列类型（SQLite / Postgres 通用）。"""
    return JSON
