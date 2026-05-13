"""Phase 1: Virtual Keys table + Prompt Inspector columns

Creates:
  - llm_virtual_keys table (per-team API keys with budgets & permissions)
  - prompt_text, completion_text, pii_redacted columns on llm_token_log

Revision ID: a2b3c4d5e6f7
Revises: 6fb178031d24
Create Date: 2026-05-01 10:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a2b3c4d5e6f7'
down_revision: Union[str, Sequence[str], None] = '6fb178031d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create llm_virtual_keys table and add prompt columns to llm_token_log."""

    # ── 1. Create llm_virtual_keys table ────────────────────────────
    op.create_table(
        'llm_virtual_keys',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False,
                  comment="Human-readable label, e.g. 'ML Pipeline - Prod'"),
        sa.Column('key_prefix', sa.String(length=20), nullable=False,
                  comment='First 16 chars for display: llm_vk_live_xxxx'),
        sa.Column('key_hash', sa.String(length=128), nullable=False,
                  comment='BLAKE2b-256 hash of the full key'),
        sa.Column('tenant_id', sa.String(length=100), nullable=False,
                  comment='Tenant that owns this key'),
        sa.Column('created_by', sa.String(length=36), nullable=False,
                  comment='User ID who created this key'),
        sa.Column('allowed_models', sa.Text(), nullable=True,
                  comment='Comma-separated list of allowed model names. NULL = all models allowed.'),
        sa.Column('environment', sa.String(length=10), nullable=False,
                  server_default='live',
                  comment='Key environment: live or test'),
        sa.Column('budget_usd', sa.Float(), nullable=True,
                  comment='Monthly budget cap in USD. NULL = unlimited.'),
        sa.Column('budget_used_usd', sa.Float(), nullable=False,
                  server_default='0.0',
                  comment='Running total of spend this month in USD.'),
        sa.Column('rate_limit_rpm', sa.Integer(), nullable=True,
                  comment='Max requests per minute. NULL = no rate limit.'),
        sa.Column('is_active', sa.Boolean(), nullable=False,
                  server_default=sa.text('true')),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True,
                  comment='Key expiration. NULL = never expires.'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True,
                  comment='Set when key is revoked. NULL = active.'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_virtual_keys_tenant', 'llm_virtual_keys', ['tenant_id'])
    op.create_index('ix_virtual_keys_hash', 'llm_virtual_keys', ['key_hash'], unique=True)

    # ── 2. Add Prompt Inspector columns to llm_token_log ────────────
    op.add_column('llm_token_log', sa.Column(
        'prompt_text', sa.Text(), nullable=True,
        comment='The prompt/input text sent to the LLM. Stored for the Request Inspector.',
    ))
    op.add_column('llm_token_log', sa.Column(
        'completion_text', sa.Text(), nullable=True,
        comment='The completion/response text from the LLM. Stored for the Request Inspector.',
    ))
    op.add_column('llm_token_log', sa.Column(
        'pii_redacted', sa.Boolean(), nullable=False,
        server_default='false',
        comment='True if PII was detected and redacted in prompt/completion text.',
    ))


def downgrade() -> None:
    """Remove prompt columns and virtual keys table."""
    # ── 1. Drop Prompt Inspector columns ────────────────────────────
    op.drop_column('llm_token_log', 'pii_redacted')
    op.drop_column('llm_token_log', 'completion_text')
    op.drop_column('llm_token_log', 'prompt_text')

    # ── 2. Drop llm_virtual_keys table ──────────────────────────────
    op.drop_index('ix_virtual_keys_hash', table_name='llm_virtual_keys')
    op.drop_index('ix_virtual_keys_tenant', table_name='llm_virtual_keys')
    op.drop_table('llm_virtual_keys')
