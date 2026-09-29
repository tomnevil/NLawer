"""notification read-path indexes (P0-15)

Revision ID: c4a91f7e2b83
Revises: 75e6058a795d
Create Date: 2026-09-16 01:50:00.000000

为 `notifications` 表补两个复合索引，支撑 P0-15 引入的读路径。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c4a91f7e2b83"
down_revision: Union[str, None] = "75e6058a795d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 列表查询：WHERE tenant_id=? AND user_id=? ORDER BY id DESC LIMIT n
    # 前两列等值 + 第三列有序 ⇒ 无排序步骤；缺它则退化为「捞出该用户全部
    # 通知再排序」，在通知量大的律所账号上会随历史累积线性劣化。
    op.create_index(
        "ix_notifications_tenant_user_id",
        "notifications",
        ["tenant_id", "user_id", "id"],
        unique=False,
    )

    # 未读数：WHERE tenant_id=? AND user_id=? AND is_read=0 GROUP BY type
    # 四列全覆盖（covering index），聚合在索引内完成，不触达数据行。
    op.create_index(
        "ix_notifications_tenant_user_read",
        "notifications",
        ["tenant_id", "user_id", "is_read", "type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_tenant_user_read", table_name="notifications")
    op.drop_index("ix_notifications_tenant_user_id", table_name="notifications")
