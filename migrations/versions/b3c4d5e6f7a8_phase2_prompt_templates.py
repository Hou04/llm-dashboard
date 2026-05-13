"""Phase 2: Create llm_prompt_templates table

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-05-01 10:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create llm_prompt_templates table for prompt versioning CMS."""
    op.create_table(
        'llm_prompt_templates',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=100), nullable=False,
                  comment='Tenant that owns this prompt template'),
        sa.Column('name', sa.String(length=200), nullable=False,
                  comment="Prompt identifier, e.g. 'summarize_report'"),
        sa.Column('slug', sa.String(length=200), nullable=False,
                  comment="URL-safe version of name"),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1',
                  comment='Auto-incrementing version per (tenant_id, name)'),

        # Content
        sa.Column('content', sa.Text(), nullable=False,
                  comment='Prompt template text with {{variable}} placeholders'),
        sa.Column('system_message', sa.Text(), nullable=True,
                  comment='Optional system message prepended to the prompt'),
        sa.Column('description', sa.String(length=1000), nullable=True,
                  comment='What this prompt does'),

        # Variables
        sa.Column('variables', JSONB(), nullable=True,
                  comment='Variable definitions: [{name, type, required, default, description}]'),

        # Configuration
        sa.Column('model', sa.String(length=100), nullable=True,
                  comment="Preferred model for this prompt"),
        sa.Column('max_tokens', sa.Integer(), nullable=True),
        sa.Column('temperature', sa.Float(), nullable=True),
        sa.Column('tags', JSONB(), nullable=True,
                  comment='Categorization tags'),

        # Lifecycle
        sa.Column('status', sa.String(length=20), nullable=False, server_default='draft',
                  comment='draft | published | archived'),
        sa.Column('is_published', sa.Boolean(), nullable=False, server_default='false',
                  comment='True if this version is active/published'),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_by', sa.String(length=36), nullable=True),

        # Authorship
        sa.Column('created_by', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),

        # Versioning
        sa.Column('parent_version_id', sa.String(length=36), nullable=True,
                  comment='ID of the previous version'),
        sa.Column('change_summary', sa.String(length=500), nullable=True,
                  comment='What changed in this version'),

        # Usage metrics
        sa.Column('usage_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_tokens', sa.Float(), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'name', 'version', name='uq_prompt_tenant_name_version'),
    )

    # Indexes
    op.create_index('ix_prompt_tenant_name', 'llm_prompt_templates', ['tenant_id', 'name'])
    op.create_index('ix_prompt_published', 'llm_prompt_templates', ['tenant_id', 'name', 'is_published'])
    op.create_index('ix_prompt_slug', 'llm_prompt_templates', ['tenant_id', 'slug'])
    op.create_index('ix_prompt_tenant', 'llm_prompt_templates', ['tenant_id'])


def downgrade() -> None:
    """Drop llm_prompt_templates table."""
    op.drop_index('ix_prompt_tenant', table_name='llm_prompt_templates')
    op.drop_index('ix_prompt_slug', table_name='llm_prompt_templates')
    op.drop_index('ix_prompt_published', table_name='llm_prompt_templates')
    op.drop_index('ix_prompt_tenant_name', table_name='llm_prompt_templates')
    op.drop_table('llm_prompt_templates')
