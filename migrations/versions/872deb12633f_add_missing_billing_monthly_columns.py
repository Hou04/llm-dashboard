"""add_missing_billing_monthly_columns

Revision ID: 872deb12633f
Revises: 9ad42406b81f
Create Date: 2026-03-22 23:53:00.876352

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '872deb12633f'
down_revision: Union[str, Sequence[str], None] = '9ad42406b81f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

