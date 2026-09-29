"""refresh session revocation (P1-1)

Revision ID: d8e4f2a61b73
Revises: b1f7c2a94e30
Create Date: 2026-09-27

? refresh ???????? / ?? / ?????? refresh_sessions ??
?????? app/models/auth_session.py::RefreshSession ?????
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d8e4f2a61b73"
down_revision: Union[str, None] = "b1f7c2a94e30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # JWT ID???? payload ? jti ?????????
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        # ???????????????????????????
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        # ???????naive UTC?? JWT exp ???????????
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        # ??????????? jti ?????????
        sa.Column("replaced_by", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_sessions_jti", "refresh_sessions", ["jti"], unique=True)
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"], unique=False)
    op.create_index("ix_refresh_sessions_revoked", "refresh_sessions", ["revoked"], unique=False)
    op.create_index("ix_refresh_sessions_expires_at", "refresh_sessions", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_expires_at", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_revoked", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_jti", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
