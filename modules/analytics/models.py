"""
Analytics module — database models.

Two pre-aggregated tables:
- llm_cost_daily:   one row per tenant per day
- llm_cost_monthly: one row per tenant per month

These are populated by background jobs that read llm_token_log
and aggregate it. They are NOT written to directly by the gateway.

Why pre-aggregate?
  Querying llm_token_log for "give me daily costs for the last 90 days"
  would scan millions of rows on every dashboard load. Pre-aggregating
  reduces that to 90 rows — one per day. 1000x faster read.
"""

import uuid
from datetime import date as dateobj
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class LLMCostDaily(Base):
    """
    Pre-aggregated daily cost and token usage per tenant.

    One row per (tenant_id, date). Populated nightly by the
    aggregation job. Read by the analytics API for trend charts.

    Unique constraint on (tenant_id, date) enforces exactly
    one row per tenant per day. The job uses INSERT ON CONFLICT
    UPDATE to upsert cleanly.
    """

    __tablename__ = "llm_cost_daily"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    date: Mapped[dateobj] = mapped_column(Date, nullable=False)

    # Token counts
    total_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    successful_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    failed_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)

    # Cost
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=8), default=Decimal("0")
    )

    # Most-used model on this day (informational)
    top_model: Mapped[str] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "date", name="uq_cost_daily_tenant_date"),
        Index("ix_cost_daily_tenant_date", "tenant_id", "date"),
        Index("ix_cost_daily_date", "date"),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.id = self.id or uuid.uuid4()
        self.total_calls = self.total_calls or 0
        self.successful_calls = self.successful_calls or 0
        self.failed_calls = self.failed_calls or 0
        self.input_tokens = self.input_tokens or 0
        self.output_tokens = self.output_tokens or 0
        self.total_tokens = self.total_tokens or 0
        self.total_cost_usd = self.total_cost_usd or Decimal("0")


class LLMCostMonthly(Base):
    """
    Pre-aggregated monthly cost and token usage per tenant.

    One row per (tenant_id, year_month). Populated by the monthly
    rollup job. Used for billing summaries and month-over-month
    trend comparisons.

    year_month is stored as an integer: YYYYMM (e.g. 202603 for March 2026).
    This makes range queries simple: WHERE year_month BETWEEN 202601 AND 202603.
    """

    __tablename__ = "llm_cost_monthly"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    year_month: Mapped[int] = mapped_column(Integer, nullable=False)
    # 202603 = March 2026

    # Token counts
    total_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    successful_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    failed_calls: Mapped[int] = mapped_column(BigInteger, default=0)
    total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)

    # Cost
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=8), default=Decimal("0")
    )

    # Month-over-month change (filled in by rollup job)
    cost_change_pct: Mapped[float] = mapped_column(
        Numeric(precision=8, scale=4), nullable=True
    )
    tokens_change_pct: Mapped[float] = mapped_column(
        Numeric(precision=8, scale=4), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "year_month",
            name="uq_cost_monthly_tenant_month"
        ),
        Index("ix_cost_monthly_tenant_month", "tenant_id", "year_month"),
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.id = self.id or uuid.uuid4()
        self.total_calls = self.total_calls or 0
        self.successful_calls = self.successful_calls or 0
        self.failed_calls = self.failed_calls or 0
        self.total_tokens = self.total_tokens or 0
        self.total_cost_usd = self.total_cost_usd or Decimal("0")

    @property
    def year(self) -> int:
        return self.year_month // 100

    @property
    def month(self) -> int:
        return self.year_month % 100
class LLMPricingRule(Base):
    """
    Versioned pricing rules for each LLM model.

    Each row says: "For model X from provider Y, between
    effective_from and effective_to, input costs Z per 1K tokens
    and output costs W per 1K tokens."

    When effective_to is NULL, the rule is currently active.

    To update pricing:
    1. Set effective_to on the old rule to today
    2. Insert a new row with effective_from = today + 1

    This preserves history — you can always recalculate what
    a call cost at the time it was made.
    """

    __tablename__ = "llm_pricing_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)

    # Prices per 1,000 tokens (industry standard unit)
    input_price_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=8), nullable=False
    )
    output_price_per_1k: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=8), nullable=False
    )

    # Version window
    effective_from: Mapped[dateobj] = mapped_column(Date, nullable=False)
    effective_to: Mapped[dateobj] = mapped_column(Date, nullable=True)
    # NULL effective_to means "currently active"

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        Index("ix_pricing_provider_model", "provider", "model"),
        Index("ix_pricing_effective_from", "effective_from"),
    )    