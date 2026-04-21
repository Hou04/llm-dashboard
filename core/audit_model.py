"""
Audit Log — tracks admin actions for compliance and debugging.

Every state-changing operation by an admin is recorded:
- User CRUD (create, deactivate)
- Governance rule changes (create, update, delete)
- Pipeline runs
- Configuration changes
- API key management

The audit log is append-only and immutable. Entries cannot be
updated or deleted — this is by design for compliance.
"""

from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Column, String, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from core.database import Base
import uuid


class LLMAuditLog(Base):
    """
    Immutable audit log table.

    Each row represents one admin action with:
    - Who did it (user_id, username, role)
    - What they did (action, resource_type, resource_id)
    - When (timestamp)
    - Details (JSON payload with before/after states)
    """
    __tablename__ = "llm_audit_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    timestamp = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    # Who
    user_id = Column(String, nullable=False, index=True)
    username = Column(String, nullable=False)
    role = Column(String, nullable=False)
    tenant_id = Column(String, nullable=True, index=True)

    # What
    action = Column(String, nullable=False, index=True)  # create, update, delete, login, etc.
    resource_type = Column(String, nullable=False, index=True)  # user, rule, api_key, pipeline, config
    resource_id = Column(String, nullable=True)  # ID of the affected resource

    # Context
    description = Column(Text, nullable=True)  # Human-readable summary
    details = Column(JSONB, nullable=True)  # Structured before/after data
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    # Status
    status = Column(String, default="success")  # success, failure, error

    __table_args__ = (
        Index("ix_audit_log_timestamp", "timestamp"),
        Index("ix_audit_log_action_resource", "action", "resource_type"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.resource_type} by {self.username}>"
