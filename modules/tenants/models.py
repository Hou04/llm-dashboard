from sqlalchemy import Column, String, Float, DateTime
from sqlalchemy.sql import func
from core.database import Base

class LLMTenant(Base):
    """
    Master table for tenants.

    Replaces the legacy clients.csv file.
    Provides a source of truth for tenant metadata (name, tier, contact info, etc),
    instead of relying on dynamic discovery from telemetry logs.
    """
    __tablename__ = "llm_tenants"

    tenant_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    tier = Column(String, nullable=False, default="pay_as_you_go")
    monthly_budget_usd = Column(Float, nullable=False, default=100.0)
    contact_email = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LLMTenantCredential(Base):
    """
    Key Vault for Tenant LLM API Keys.
    Stores encrypted provider keys (OpenAI, Anthropic, etc) per tenant.
    """
    __tablename__ = "llm_tenant_credentials"

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    provider = Column(String, nullable=False) # e.g. "openai", "anthropic"
    encrypted_key = Column(String, nullable=False)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
