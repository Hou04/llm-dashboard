"""Merge heads

Revision ID: 449a6a770742
Revises: 9469848792c8, b7a2e8c1f3d5
Create Date: 2026-04-19 22:07:25.519997

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '449a6a770742'
down_revision: Union[str, Sequence[str], None] = ('9469848792c8', 'b7a2e8c1f3d5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
