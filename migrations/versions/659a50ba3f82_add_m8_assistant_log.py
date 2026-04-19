"""add_m8_assistant_log

Revision ID: 659a50ba3f82
Revises: a58e5e539f77
Create Date: 2026-03-22 16:23:13.661303

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '659a50ba3f82'
down_revision: Union[str, Sequence[str], None] = 'a58e5e539f77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS llm_dashboard_assistant_log (
            id               VARCHAR(36)              NOT NULL,
            user_id          VARCHAR(100)             NOT NULL DEFAULT 'anonymous',
            question_preview VARCHAR(200),
            question_type    VARCHAR(100),
            tenant_scope     VARCHAR(100),
            tokens_used      INTEGER                  NOT NULL DEFAULT 0,
            llm_model_used   VARCHAR(100),
            response_time_ms INTEGER,
            created_at       TIMESTAMP WITH TIME ZONE NOT NULL,
            PRIMARY KEY (id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_assistant_log_created
        ON llm_dashboard_assistant_log (created_at)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_assistant_log_user
        ON llm_dashboard_assistant_log (user_id)
    """)

def downgrade() -> None:
    op.drop_index("ix_assistant_log_user",    "llm_dashboard_assistant_log")
    op.drop_index("ix_assistant_log_created", "llm_dashboard_assistant_log")
    op.drop_table("llm_dashboard_assistant_log")