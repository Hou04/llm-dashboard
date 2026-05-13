"""add status field to llm_tenant_contracts

Revision ID: fbb5364a64fb
Revises: b03d535661e5
Create Date: 2026-05-07 19:00:05.425298

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbb5364a64fb'
down_revision: Union[str, Sequence[str], None] = 'b03d535661e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('llm_tenant_contracts', sa.Column('status', sa.String(length=20), nullable=False, server_default='active', comment='Contract workflow status: draft, proposed, active, rejected'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('llm_tenant_contracts', 'status')
