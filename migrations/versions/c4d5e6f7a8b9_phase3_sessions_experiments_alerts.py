"""Phase 3: Sessions, Experiments, Alert Config tables

Creates:
  - llm_sessions (Feature 8: session tracing)
  - llm_experiments (Feature 9: prompt A/B testing)
  - llm_alert_configs (Feature 10: per-tenant webhook config)
  - llm_alert_history (Feature 10: alert audit log)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-05-01 11:13:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'c4d5e6f7a8b9'
down_revision: Union[str, Sequence[str], None] = 'b3c4d5e6f7a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. llm_sessions ─────────────────────────────────────────
    op.create_table(
        'llm_sessions',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('session_id', sa.String(100), nullable=False, unique=True,
                  comment='Client-provided session identifier'),
        sa.Column('tenant_id', sa.String(100), nullable=False),
        sa.Column('user_id', sa.String(100), nullable=True),
        sa.Column('agent_id', sa.String(100), nullable=True),
        sa.Column('name', sa.String(200), nullable=True),
        sa.Column('total_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_cost_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('total_duration_ms', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='active'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('metadata', JSONB(), nullable=True),
        sa.Column('tags', JSONB(), nullable=True),
        sa.Column('call_chain', JSONB(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_session_tenant', 'llm_sessions', ['tenant_id'])
    op.create_index('ix_session_started', 'llm_sessions', ['started_at'])
    op.create_index('ix_session_agent', 'llm_sessions', ['tenant_id', 'agent_id'])

    # ── 2. llm_experiments ──────────────────────────────────────
    op.create_table(
        'llm_experiments',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('prompt_name', sa.String(200), nullable=False),
        sa.Column('variant_a_version', sa.Integer(), nullable=False),
        sa.Column('variant_b_version', sa.Integer(), nullable=False),
        sa.Column('traffic_split', sa.Float(), nullable=False, server_default='0.5'),
        sa.Column('primary_metric', sa.String(50), nullable=False, server_default="'quality_score'"),
        # Variant A metrics
        sa.Column('a_requests', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('a_avg_latency_ms', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('a_avg_cost_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('a_avg_quality', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('a_error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('a_total_tokens', sa.Integer(), nullable=False, server_default='0'),
        # Variant B metrics
        sa.Column('b_requests', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('b_avg_latency_ms', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('b_avg_cost_usd', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('b_avg_quality', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('b_error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('b_total_tokens', sa.Integer(), nullable=False, server_default='0'),
        # Results
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('winner', sa.String(1), nullable=True),
        sa.Column('p_value', sa.Float(), nullable=True),
        sa.Column('confidence_level', sa.Float(), nullable=True),
        sa.Column('min_samples', sa.Integer(), nullable=False, server_default='100'),
        # Lifecycle
        sa.Column('created_by', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_experiment_tenant', 'llm_experiments', ['tenant_id'])
    op.create_index('ix_experiment_prompt', 'llm_experiments', ['tenant_id', 'prompt_name'])
    op.create_index('ix_experiment_status', 'llm_experiments', ['status'])

    # ── 3. llm_alert_configs ────────────────────────────────────
    op.create_table(
        'llm_alert_configs',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(100), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('channel_type', sa.String(20), nullable=False),
        sa.Column('webhook_url', sa.String(500), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('alert_types', JSONB(), nullable=True),
        sa.Column('severity_filter', sa.String(20), nullable=True),
        sa.Column('created_by', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_tested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_alert_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_alerts_sent', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alert_config_tenant', 'llm_alert_configs', ['tenant_id'])

    # ── 4. llm_alert_history ────────────────────────────────────
    op.create_table(
        'llm_alert_history',
        sa.Column('id', sa.String(36), nullable=False),
        sa.Column('tenant_id', sa.String(100), nullable=False),
        sa.Column('config_id', sa.String(36), nullable=False),
        sa.Column('channel_type', sa.String(20), nullable=False),
        sa.Column('alert_type', sa.String(50), nullable=False),
        sa.Column('payload', JSONB(), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.String(500), nullable=True),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alert_history_tenant', 'llm_alert_history', ['tenant_id'])
    op.create_index('ix_alert_history_sent', 'llm_alert_history', ['sent_at'])


def downgrade() -> None:
    op.drop_index('ix_alert_history_sent', table_name='llm_alert_history')
    op.drop_index('ix_alert_history_tenant', table_name='llm_alert_history')
    op.drop_table('llm_alert_history')

    op.drop_index('ix_alert_config_tenant', table_name='llm_alert_configs')
    op.drop_table('llm_alert_configs')

    op.drop_index('ix_experiment_status', table_name='llm_experiments')
    op.drop_index('ix_experiment_prompt', table_name='llm_experiments')
    op.drop_index('ix_experiment_tenant', table_name='llm_experiments')
    op.drop_table('llm_experiments')

    op.drop_index('ix_session_agent', table_name='llm_sessions')
    op.drop_index('ix_session_started', table_name='llm_sessions')
    op.drop_index('ix_session_tenant', table_name='llm_sessions')
    op.drop_table('llm_sessions')
