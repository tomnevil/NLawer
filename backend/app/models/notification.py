"""通知模型：派单、接单、材料缺失、复核、定稿等节点推送。"""
from typing import Optional

from sqlalchemy import Enum, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, json_col
from app.models.enums import NotificationType


class Notification(Base, TenantMixin, TimestampMixin):
    """用户级通知。

    ## 索引设计（P0-15 读路径落地时补）

    读路径只有两种访问模式，各配一个复合索引：

    | 查询 | 索引 | 为什么 |
    |------|------|--------|
    | 列表：`WHERE tenant_id=? AND user_id=? ORDER BY id DESC LIMIT n` | `(tenant_id, user_id, id)` | 前两列等值定位 + 第三列有序，**无需排序步骤**；只有 `user_id` 单列索引时会退化为「按 user_id 捞出全部通知再排序」 |
    | 未读数：`WHERE tenant_id=? AND user_id=? AND is_read=0 GROUP BY type` | `(tenant_id, user_id, is_read, type)` | 四列全覆盖，`GROUP BY` 在索引内即可完成，不触达数据行 |

    两个索引都以 `(tenant_id, user_id)` 打头而非各自的单列索引：单列索引
    在本表上几乎无用——通知是全表增长最快的表之一（每个业务动作 × 每个
    参与人各一行），单列 `user_id` 索引的选择性随数据增长迅速下降。

    保留模型上原有的 `index=True`（`tenant_id` / `user_id` 单列）是因为
    Alembic 初始迁移已建出它们，删除需额外迁移且收益不明；复合索引在
    查询规划中会被优先选中，单列索引不会造成错误计划。
    """

    __tablename__ = "notifications"

    __table_args__ = (
        Index("ix_notifications_tenant_user_read", "tenant_id", "user_id", "is_read", "type"),
        Index("ix_notifications_tenant_user_id", "tenant_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, native_enum=False, length=64)
    )
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 关联业务对象，前端点击跳转用
    ref_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ref_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(json_col(), nullable=True)
    is_read: Mapped[bool] = mapped_column(Integer, default=0)
    read_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
