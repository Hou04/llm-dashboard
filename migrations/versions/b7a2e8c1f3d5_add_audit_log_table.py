"""Add audit log table

Revision ID: b7a2e8c1f3d5
Revises: f5ac64edf4f7
Create Date: 2026-04-19 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b7a2e8c1f3d5'
down_revision: Union[str, None] = 'f5ac64edf4f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_audit_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('tenant_id', sa.String(), nullable=True),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('resource_type', sa.String(), nullable=False),
        sa.Column('resource_id', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('details', postgresql.JSONB(), nullable=True),
        sa.Column('ip_address', sa.String(), nullable=True),
        sa.Column('user_agent', sa.String(), nullable=True),
        sa.Column('status', sa.String(), server_default='success'),
        sa.PrimaryKeyConstraint('id'),
    )

    # Indexes for common query patterns
    op.create_index('ix_audit_log_user_id', 'llm_audit_log', ['user_id'])
    op.create_index('ix_audit_log_tenant_id', 'llm_audit_log', ['tenant_id'])
    op.create_index('ix_audit_log_action', 'llm_audit_log', ['action'])
    op.create_index('ix_audit_log_resource_type', 'llm_audit_log', ['resource_type'])
    op.create_index('ix_audit_log_timestamp', 'llm_audit_log', ['timestamp'])
    op.create_index('ix_audit_log_action_resource', 'llm_audit_log', ['action', 'resource_type'])


def downgrade() -> None:
    op.drop_index('ix_audit_log_action_resource', table_name='llm_audit_log')
    op.drop_index('ix_audit_log_timestamp', table_name='llm_audit_log')
    op.drop_index('ix_audit_log_resource_type', table_name='llm_audit_log')
    op.drop_index('ix_audit_log_action', table_name='llm_audit_log')
    op.drop_index('ix_audit_log_tenant_id', table_name='llm_audit_log')
    op.drop_index('ix_audit_log_user_id', table_name='llm_audit_log')
    op.drop_table('llm_audit_log')
