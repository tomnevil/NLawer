"""team case visibility grants (P2-10)

Revision ID: e7c9b3d54f21
Revises: d8e4f2a61b73
Create Date: 2026-09-29

?? case_access_grants ???????????????-????
??????????????????????
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7c9b3d54f21"
down_revision: Union[str, None] = "d8e4f2a61b73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "case_access_grants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("grantee_user_id", sa.Integer(), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("scope", sa.String(length=32), nullable=False, server_default="team_read"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
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
    op.create_index("ix_case_access_grants_grantee_user_id", "case_access_grants", ["grantee_user_id"], unique=False)
    op.create_index("ix_case_access_grants_status", "case_access_grants", ["status"], unique=False)
    op.create_index("ix_case_access_grants_expires_at", "case_access_grants", ["expires_at"], unique=False)
    op.create_index("ix_case_access_grants_tenant_id", "case_access_grants", ["tenant_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_case_access_grants_tenant_id", table_name="case_access_grants")
    op.drop_index("ix_case_access_grants_expires_at", table_name="case_access_grants")
    op.drop_index("ix_case_access_grants_status", table_name="case_access_grants")
    op.drop_index("ix_case_access_grants_grantee_user_id", table_name="case_access_grants")
    op.drop_table("case_access_grants")
