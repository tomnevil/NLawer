"""consult report draft table (P0 decouple: four-section -> backend draft)

Revision ID: f1a2b3c4d5e6
Revises: e7c9b3d54f21
Create Date: 2026-09-30

把「四段式」从客户可见回复中剥离：客户在 /qa 或 IM 只看到自然对话，
四段式作为 `consult_reports`(草稿态) 落库，供律师复核（ReviewTargetType.CONSULT_REPORT）确认后回流。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e7c9b3d54f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consult_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("case_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("draft_sections", sa.JSON(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            # native_enum=False + 枚举名（大写）作 server_default，避免 .name 不一致导致读回 500
            sa.Enum("DRAFT", "APPROVED", "REJECTED", name="consultreportstatus", native_enum=False, length=16),
            server_default="DRAFT",
            nullable=False,
        ),
        sa.Column("lawyer_id", sa.Integer(), nullable=True),
        sa.Column("signed_by", sa.Integer(), nullable=True),
        sa.Column("signed_at", sa.String(length=64), nullable=True),
        sa.Column("final_report", sa.JSON(), nullable=True),
        sa.Column("review_id", sa.Integer(), nullable=True),
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
    op.create_index("ix_consult_reports_tenant_id", "consult_reports", ["tenant_id"], unique=False)
    op.create_index("ix_consult_reports_conversation_id", "consult_reports", ["conversation_id"], unique=False)
    op.create_index("ix_consult_reports_case_id", "consult_reports", ["case_id"], unique=False)
    op.create_index("ix_consult_reports_review_id", "consult_reports", ["review_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_consult_reports_review_id", table_name="consult_reports")
    op.drop_index("ix_consult_reports_case_id", table_name="consult_reports")
    op.drop_index("ix_consult_reports_conversation_id", table_name="consult_reports")
    op.drop_index("ix_consult_reports_tenant_id", table_name="consult_reports")
    op.drop_table("consult_reports")
