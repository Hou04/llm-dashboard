"""
Contract & Pricing models — database-backed replacements for hardcoded dicts.

Tables:
  llm_tenant_contracts  — per-tenant billing contract definitions
  llm_model_pricing     — per-model pricing tiers with date ranges

These tables make the billing engine fully dynamic:
- Any tenant can be onboarded without code changes
- Pricing can be updated without redeployment
- Contract terms are auditable and versioned by date
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    String, BigInteger, Boolean, Date, DateTime,
    Numeric, Text, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class LLMTenantContract(Base):
    """Per-tenant billing contract.

    Determines how a tenant is billed each month:
      pay_as_you_go  — raw cost only, no base fee
      forfait        — flat fee + overage for tokens above allowance
      hybrid         — base fee + custom per-model rates
    """
    __tablename__ = "llm_tenant_contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        comment="Contract identifier",
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True,
        comment="Tenant this contract applies to",
    )
    contract_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pay_as_you_go",
        comment="Contract type: pay_as_you_go, forfait, hybrid",
    )
    base_fee_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=2), nullable=False, default=Decimal("0"),
        comment="Monthly flat fee in USD",
    )
    forfait_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0,
        comment="Included token allowance per month",
    )
    overage_rate_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(precision=10, scale=6), nullable=False, default=Decimal("0"),
        comment="Cost per 1K tokens above forfait allowance",
    )
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, default="USD",
        comment="Billing currency",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
        comment="Active contracts are used for billing; inactive are archived",
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active",
        comment="Contract workflow status: draft, proposed, active, rejected",
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
        comment="Human-readable contract description",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        {"comment": "Per-tenant billing contracts. Replaces hardcoded dict."},
    )

    def __repr__(self) -> str:
        return (
            f"<LLMTenantContract tenant={self.tenant_id} "
            f"type={self.contract_type} active={self.is_active}>"
        )

    def to_dict(self) -> dict:
        """Convert to the dict format expected by BillingService._apply_contract."""
        return {
            "contract_type": self.contract_type,
            "status": self.status,
            "base_fee_usd": float(self.base_fee_usd),
            "forfait_tokens": self.forfait_tokens,
            "overage_rate_per_1k": float(self.overage_rate_per_1k),
            "currency": self.currency,
        }


class LLMModelPricing(Base):
    """Per-model pricing tiers.

    Supports date-ranged pricing so historical cost calculations
    always use the price that was in effect at call time.
    """
    __tablename__ = "llm_model_pricing"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        comment="Pricing record identifier",
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True,
        comment="LLM model name (e.g. gpt-4, mistral, claude-3-haiku)",
    )
    provider: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="Provider name. Auto-inferred if not set.",
    )
    input_price_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=8), nullable=False, default=Decimal("0"),
        comment="USD cost per 1,000 input tokens",
    )
    output_price_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=8), nullable=False, default=Decimal("0"),
        comment="USD cost per 1,000 output tokens",
    )
    valid_from: Mapped[date] = mapped_column(
        Date, nullable=False,
        comment="Date this pricing tier becomes effective",
    )
    valid_until: Mapped[Optional[date]] = mapped_column(
        Date, nullable=True,
        comment="Date this pricing tier expires. NULL = currently active.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_model_pricing_model_valid", "model", "valid_from"),
        {"comment": "Model pricing tiers with date ranges."},
    )

    def __repr__(self) -> str:
        return (
            f"<LLMModelPricing model={self.model} "
            f"in={self.input_price_per_1k} out={self.output_price_per_1k} "
            f"from={self.valid_from}>"
        )

    @staticmethod
    def infer_provider(model_name: str) -> str:
        """Infer provider from model name when not explicitly set."""
        name = model_name.lower()
        if any(p in name for p in ("gpt", "o1", "o3", "davinci", "chatgpt")):
            return "openai"
        if any(p in name for p in ("claude",)):
            return "anthropic"
        if any(p in name for p in ("gemini", "palm", "bard")):
            return "google"
        if any(p in name for p in ("mistral", "mixtral", "codestral")):
            return "mistral"
        if any(p in name for p in ("llama", "meta-llama")):
            return "meta"
        if any(p in name for p in ("command", "coral")):
            return "cohere"
        return "unknown"
