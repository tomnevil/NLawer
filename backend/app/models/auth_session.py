"""Refresh 会话表：服务端吊销 + 轮换重放检测（审查 P1-1）。

为什么需要它：refresh 令牌此前是纯无状态 JWT，logout 只清 Cookie，
令牌本体在 7 天内始终有效，被窃后登出形同虚设。本表给每个 refresh
令牌一个服务端登记的 jti，吊销 / 轮换 / 重放检测全部在服务端闭环。

重放检测语义：已吊销的 jti 再次出现 = 令牌被窃后双方竞相刷新，
立即吊销该用户全部会话（宁可全端重登，不可放行窃取者）。

部署注意：Postgres 生产需 alembic 迁移；开发 SQLite 由 create_all 建表。
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class RefreshSession(Base, TimestampMixin):
    """一个 refresh 令牌的服务端登记（jti -> 吊销状态）。

    不继承 TenantMixin：会话归属于用户而非租户，吊销按 user_id 操作。
    """

    __tablename__ = "refresh_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    replaced_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
