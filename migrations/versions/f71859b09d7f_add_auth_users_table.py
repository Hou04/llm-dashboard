"""add_auth_users_table

Revision ID: f71859b09d7f
Revises: 353f4230b84e
Create Date: 2026-03-29 12:04:59.836344

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f71859b09d7f'
down_revision: Union[str, Sequence[str], None] = '353f4230b84e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_auth_users",
        sa.Column("id",           sa.String(36),   primary_key=True),
        sa.Column("username",     sa.String(100),  nullable=False),
        sa.Column("email",        sa.String(200),  nullable=True),
        sa.Column("hashed_password", sa.String(200), nullable=False),
        sa.Column("role",         sa.String(30),   nullable=False, server_default="tenant_viewer"),
        sa.Column("tenant_id",    sa.String(100),  nullable=True),
        sa.Column("is_active",    sa.Boolean(),    nullable=False, server_default="true"),
        sa.Column("created_at",   sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at",sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_users_username", "llm_auth_users", ["username"], unique=True)
    op.create_index("ix_auth_users_tenant",   "llm_auth_users", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_users_tenant",   "llm_auth_users")
    op.drop_index("ix_auth_users_username", "llm_auth_users")
    op.drop_table("llm_auth_users")