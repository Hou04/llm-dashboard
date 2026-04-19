"""add vector embedding to explanation

Revision ID: c546839a6fda
Revises: 8aa130b812be
Create Date: 2026-04-04 17:53:28.910006

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import pgvector.sqlalchemy


# revision identifiers, used by Alembic.
revision: str = 'c546839a6fda'
down_revision: Union[str, Sequence[str], None] = '8aa130b812be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute('CREATE EXTENSION IF NOT EXISTS vector;')
    op.add_column('llm_token_explanations', sa.Column('embedding', pgvector.sqlalchemy.Vector(384), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('llm_token_explanations', 'embedding')
