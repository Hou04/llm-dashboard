"""
Auth models — SQLAlchemy table definitions.

Tables:
  llm_auth_users   — all human users (admin, tenant staff, viewers)
  llm_api_keys     — long-lived API keys for programmatic/CI access

Roles (llm_auth_users.role):
  super_admin     — full access to all tenants and system configuration
  tenant_admin    — full access to their own tenant, can manage rules
  tenant_viewer   — read-only access to their own tenant
  api_key_agent   — API-key-based service account (machine access)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class LLMAuthUser(Base):
    __tablename__ = "llm_auth_users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username:        Mapped[str]  = mapped_column(String(100), nullable=False, unique=True)
    email:           Mapped[str]  = mapped_column(String(200), nullable=True)
    hashed_password: Mapped[str]  = mapped_column(String(300), nullable=False)

    # super_admin / tenant_admin / tenant_viewer
    role:      Mapped[str]  = mapped_column(String(30),  nullable=False, default="tenant_viewer")

    # None for super_admin (sees everything); required for tenant roles
    tenant_id: Mapped[str]  = mapped_column(String(100), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_auth_users_username", "username", unique=True),
        Index("ix_auth_users_tenant",   "tenant_id"),
    )

    def __repr__(self) -> str:
        return f"<LLMAuthUser {self.username} role={self.role} tenant={self.tenant_id}>"


class LLMApiKey(Base):
    """
    Long-lived API key for programmatic / service-account access.

    The *raw* key (llm_sk_<random>) is only shown once at creation time.
    We store only a BLAKE2b hash — the raw key cannot be recovered.

    Scope: a key is always scoped to a single tenant.
    super_admin can create cross-tenant keys by setting tenant_id = None.
    """

    __tablename__ = "llm_api_keys"

    id:           Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name:         Mapped[str] = mapped_column(String(100), nullable=False)  # human label
    key_prefix:   Mapped[str] = mapped_column(String(16),  nullable=False)  # first 8 chars for display
    hashed_key:   Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    tenant_id:    Mapped[str]  = mapped_column(String(100), nullable=True)
    created_by:   Mapped[str]  = mapped_column(String(36),  nullable=False)  # user.id
    is_active:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at:   Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_api_keys_tenant",   "tenant_id"),
        Index("ix_api_keys_prefix",   "key_prefix"),
    )

    def __repr__(self) -> str:
        return f"<LLMApiKey {self.key_prefix}… tenant={self.tenant_id}>"