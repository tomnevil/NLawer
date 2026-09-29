"""perf_indexes

Revision ID: 75e6058a795d
Revises: 031c74c6de97
Create Date: 2026-09-08 16:21:15.724441
"""
from typing import Sequence, Union

from alembic import op

revision: str = '75e6058a795d'
down_revision: Union[str, None] = '031c74c6de97'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 复合索引：高频过滤列 (tenant_id, status) / (lawyer_id, status)
    op.create_index('ix_cases_tenant_status', 'cases', ['tenant_id', 'status'], unique=False)
    op.create_index('ix_cases_lawyer_status', 'cases', ['lawyer_id', 'status'], unique=False)
    op.create_index('ix_dispatches_tenant_status', 'dispatches', ['tenant_id', 'status'], unique=False)
    op.create_index('ix_dispatches_lawyer_status', 'dispatches', ['lawyer_id', 'status'], unique=False)
    op.create_index('ix_reviews_tenant_status', 'reviews', ['tenant_id', 'status'], unique=False)
    op.create_index('ix_work_orders_tenant_status', 'work_orders', ['tenant_id', 'status'], unique=False)

    # pgvector HNSW 索引（仅 Postgres）：加速余弦相似度召回，SQLite 跳过
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.create_index(
            'ix_knowledge_embeddings_embedding_hnsw',
            'knowledge_embeddings',
            ['embedding'],
            unique=False,
            postgresql_using='hnsw',
            postgresql_ops={'embedding': 'vector_cosine_ops'},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.drop_index('ix_knowledge_embeddings_embedding_hnsw', table_name='knowledge_embeddings')

    op.drop_index('ix_work_orders_tenant_status', table_name='work_orders')
    op.drop_index('ix_reviews_tenant_status', table_name='reviews')
    op.drop_index('ix_dispatches_lawyer_status', table_name='dispatches')
    op.drop_index('ix_dispatches_tenant_status', table_name='dispatches')
    op.drop_index('ix_cases_lawyer_status', table_name='cases')
    op.drop_index('ix_cases_tenant_status', table_name='cases')
