"""
M10 Billing Models — SQLAlchemy table definitions.

Three tables:
  LLMBillingMonthly    — one frozen invoice per tenant per month
  LLMBillingLineItem   — individual charge lines within an invoice
  LLMClientReport      — auto-generated monthly client report

Design decisions:
  - "Frozen" means: once status='finalized', numbers never change.
    This is essential for financial auditing. If a correction is needed,
    a new credit note is issued — the original is never modified.
  - All monetary amounts use Decimal(12,4) — never float for money.
  - year_month stored as integer YYYYMM for simple range queries.
  - Every line item traces back to a date range, model, and agent.
    A client can always ask "why is this line on my invoice?" and
    you can show them the exact calls from llm_token_log.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Index,
    Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class LLMBillingMonthly(Base):
    """
    One invoice per tenant per month.

    Status lifecycle:
      draft     → being calculated, can still be modified
      finalized → frozen, sent to client, never modified
      disputed  → client raised an issue, under review
      corrected → a credit note was issued for this invoice

    year_month: integer YYYYMM e.g. 202603 = March 2026
    This makes it easy to query: WHERE year_month = 202603
    """

    __tablename__ = "llm_billing_monthly"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str]  = mapped_column(String(100), nullable=False)
    year_month: Mapped[int] = mapped_column(Integer, nullable=False)
    # e.g. 202603 for March 2026

    # ── Raw usage (frozen from llm_cost_monthly at snapshot time) ──
    total_calls: Mapped[int]       = mapped_column(BigInteger, nullable=False, default=0)
    total_tokens: Mapped[int]      = mapped_column(BigInteger, nullable=False, default=0)
    total_input_tokens: Mapped[int]= mapped_column(BigInteger, nullable=False, default=0)
    total_output_tokens: Mapped[int]=mapped_column(BigInteger, nullable=False, default=0)

    # Raw cost before any contract rules (direct model pricing)
    raw_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=4), nullable=False, default=Decimal("0")
    )

    # ── Contract rules applied ─────────────────────────────────────
    # Forfait = included tokens in the contract (e.g. 10M tokens/month)
    forfait_tokens_included: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    # Tokens consumed within the forfait (billed at 0 overage)
    forfait_tokens_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    # Tokens consumed ABOVE the forfait (billed at overage rate)
    overage_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )

    # ── Financial result ───────────────────────────────────────────
    # Base fee = the monthly forfait flat fee
    base_fee_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=4), nullable=False, default=Decimal("0")
    )
    # Overage charge = overage_tokens × overage_rate_per_1k
    overage_charge_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=4), nullable=False, default=Decimal("0")
    )
    # Credits = any commercial gesture, incident refund, etc.
    credits_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=4), nullable=False, default=Decimal("0")
    )
    # TOTAL = base_fee + overage_charge - credits
    total_billed_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=4), nullable=False, default=Decimal("0")
    )

    # ── Lifecycle ──────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"
    )
    # draft / finalized / disputed / corrected

    notes: Mapped[str] = mapped_column(Text, nullable=True)

    # ── Timestamps ────────────────────────────────────────────────
    snapshot_taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "year_month",
            name="uq_billing_tenant_month"
        ),
        Index("ix_billing_monthly_tenant",     "tenant_id"),
        Index("ix_billing_monthly_year_month", "year_month"),
        Index("ix_billing_monthly_status",     "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<LLMBillingMonthly tenant={self.tenant_id} "
            f"month={self.year_month} total=${self.total_billed_usd} "
            f"status={self.status}>"
        )


class LLMBillingLineItem(Base):
    """
    One charge line within a monthly invoice.

    Line items make the invoice auditable and explainable.
    A client can see exactly what they are being charged for.

    line_type categories:
      base_fee     — flat monthly forfait fee
      usage_cost   — cost of tokens consumed (by model)
      overage      — tokens above the forfait limit
      credit       — refund or commercial gesture (negative amount)
      premium      — surcharge for advanced model usage

    Every line item has a date range and can be traced to the
    raw logs in llm_token_log for that period.
    """

    __tablename__ = "llm_billing_line_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    billing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Foreign key to LLMBillingMonthly.id (not enforced by FK constraint
    # for performance — enforced at application level)

    tenant_id:  Mapped[str] = mapped_column(String(100), nullable=False)
    year_month: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── What this line represents ──────────────────────────────────
    line_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # base_fee / usage_cost / overage / credit / premium

    description: Mapped[str] = mapped_column(String(300), nullable=False)
    # Human-readable: "gpt-4o usage — enterprise_corp — March 2026"

    # ── Scope (what calls this covers) ────────────────────────────
    model:    Mapped[str] = mapped_column(String(100), nullable=True)
    provider: Mapped[str] = mapped_column(String(50),  nullable=True)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=True)

    # ── Quantities ────────────────────────────────────────────────
    quantity_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    quantity_calls:  Mapped[int] = mapped_column(Integer,    nullable=False, default=0)

    # ── Pricing ───────────────────────────────────────────────────
    unit_price_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=8), nullable=False, default=Decimal("0")
    )
    # For usage lines: price per 1K tokens
    # For base_fee lines: flat amount (quantity=1)

    amount_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=12, scale=4), nullable=False, default=Decimal("0")
    )
    # Negative for credits

    # ── Credit classification (only for credit line items) ────
    credit_type: Mapped[str] = mapped_column(
        String(30), nullable=True, default=None
    )
    # geste_commercial / incident_refund / volume_discount / promo_credit

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_line_items_billing_id",   "billing_id"),
        Index("ix_line_items_tenant_month", "tenant_id", "year_month"),
    )


class LLMClientReport(Base):
    """
    Auto-generated monthly client report.

    Not an invoice — a narrative document explaining the month:
      - What was consumed and why
      - Cost breakdown by agent and model
      - Comparison to previous month
      - Optimization opportunities
      - Next month projection

    report_data stores the full report as JSON.
    This allows flexible structure without schema changes.
    """

    __tablename__ = "llm_client_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id:  Mapped[str] = mapped_column(String(100), nullable=False)
    year_month: Mapped[int] = mapped_column(Integer, nullable=False)
    billing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # ── Report content ────────────────────────────────────────────
    report_title:   Mapped[str]  = mapped_column(String(300), nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=True)
    report_data:    Mapped[str]  = mapped_column(Text, nullable=True)
    # JSON: full report sections

    # ── Status ────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"
    )
    # draft / sent / acknowledged

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "year_month",
            name="uq_report_tenant_month"
        ),
        Index("ix_client_reports_tenant",     "tenant_id"),
        Index("ix_client_reports_year_month", "year_month"),
    )