"""add billing tokens and calls

Revision ID: 353f4230b84e
Revises: 872deb12633f
Create Date: 2026-03-28 12:07:40.303241

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '353f4230b84e'
down_revision: Union[str, Sequence[str], None] = '872deb12633f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass

def downgrade() -> None:
    pass
