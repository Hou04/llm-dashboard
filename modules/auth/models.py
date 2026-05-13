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
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
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
    last_activity_at: Mapped[datetime] = mapped_column(
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


class LLMVirtualKey(Base):
    """
    Virtual API key with per-team permissions and budgets.

    Virtual keys allow tenants to create multiple API keys, each with:
    - Restricted set of allowed models (e.g., only gpt-4o-mini)
    - Monthly budget cap in USD
    - Per-minute rate limiting
    - Separate live/test environments

    Key format:
      Live: llm_vk_live_<32-hex>
      Test: llm_vk_test_<32-hex>

    The raw key is only shown once at creation. We store a BLAKE2b hash.
    """

    __tablename__ = "llm_virtual_keys"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False,
        comment="Human-readable label, e.g. 'ML Pipeline - Prod'"
    )
    key_prefix: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="First 16 chars for display: llm_vk_live_xxxx"
    )
    key_hash: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True,
        comment="BLAKE2b-256 hash of the full key"
    )

    # ---- Ownership ----
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="Tenant that owns this key"
    )
    created_by: Mapped[str] = mapped_column(
        String(36), nullable=False,
        comment="User ID who created this key"
    )

    # ---- Permissions ----
    allowed_models: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Comma-separated list of allowed model names. NULL = all models allowed."
    )
    environment: Mapped[str] = mapped_column(
        String(10), nullable=False, default="live",
        comment="Key environment: live or test"
    )

    # ---- Budget & Rate Limits ----
    budget_usd: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True,
        comment="Monthly budget cap in USD. NULL = unlimited."
    )
    budget_used_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0,
        comment="Running total of spend this month in USD."
    )
    rate_limit_rpm: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Max requests per minute. NULL = no rate limit."
    )

    # ---- Lifecycle ----
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Key expiration. NULL = never expires."
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Set when key is revoked. NULL = active."
    )

    __table_args__ = (
        Index("ix_virtual_keys_tenant", "tenant_id"),
        Index("ix_virtual_keys_hash",   "key_hash", unique=True),
    )

    def __repr__(self) -> str:
        return f"<LLMVirtualKey {self.key_prefix}… tenant={self.tenant_id} env={self.environment}>"

    @property
    def allowed_models_list(self) -> list[str]:
        """Parse comma-separated allowed_models into a list."""
        if not self.allowed_models:
            return []
        return [m.strip() for m in self.allowed_models.split(",") if m.strip()]

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at < datetime.now(timezone.utc)

    @property
    def is_budget_exceeded(self) -> bool:
        if self.budget_usd is None:
            return False
        return self.budget_used_usd >= self.budget_usd