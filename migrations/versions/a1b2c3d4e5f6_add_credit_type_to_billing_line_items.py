"""add credit_type to billing line items

Revision ID: a1b2c3d4e5f6
Revises: f5ac64edf4f7
Create Date: 2026-04-05 12:00:00.000000+00:00

Adds the credit_type column to llm_billing_line_items table.
This column is only populated for credit-type line items
(geste_commercial, incident_refund, volume_discount, promo_credit).
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "3486c40e2bda"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add credit_type column — nullable, only used for credit line items
    op.add_column(
        "llm_billing_line_items",
        sa.Column("credit_type", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("llm_billing_line_items", "credit_type")
