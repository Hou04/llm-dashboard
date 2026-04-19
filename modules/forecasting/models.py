"""
Forecasting module — database models.

Two tables:
  llm_forecast      — one row per (tenant, forecast_date, scenario).
                      Three scenarios per day: pessimistic/likely/optimistic.
                      Regenerated on each forecast run (upsert by unique key).

  llm_budget_risk   — one row per tenant when forecast predicts budget
                      exhaustion within the configured horizon.
                      Used for the executive dashboard risk feed.
"""

import uuid
from datetime import datetime, date, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime,
    Float, Index, Integer, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from decimal import Decimal

from core.database import Base


class LLMForecast(Base):
    """
    One row per tenant per forecast date per scenario.

    scenario: "pessimistic" / "likely" / "optimistic"

    forecast_date: the future calendar day this row predicts.
    generated_at:  when the forecast was computed (for staleness checks).

    The unique constraint on (tenant_id, forecast_date, scenario) allows
    upsert — re-running the forecast replaces old predictions cleanly.
    """

    __tablename__ = "llm_forecast"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(100), nullable=False, default="*")
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)
    scenario: Mapped[str] = mapped_column(String(20), nullable=False)
    # "pessimistic", "likely", "optimistic"

    # Predicted daily token usage
    predicted_tokens: Mapped[float] = mapped_column(Float, nullable=False)

    # Predicted daily cost (derived from predicted_tokens × current avg cost/token)
    predicted_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(precision=14, scale=8), nullable=False, default=Decimal("0")
    )

    # Trend contribution (what the growth line says, before seasonality)
    trend_value: Mapped[float] = mapped_column(Float, nullable=True)

    # Seasonal multiplier applied for this day-of-week (e.g. 0.15 for Sunday)
    seasonal_multiplier: Mapped[float] = mapped_column(Float, nullable=True)

    # Confidence: how wide the uncertainty band is at this horizon
    # Grows with forecast horizon — day 1 is narrow, day 30 is wide
    confidence_lower: Mapped[float] = mapped_column(Float, nullable=True)
    confidence_upper: Mapped[float] = mapped_column(Float, nullable=True)

    # How many days ahead this is from the forecast generation date
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id", "forecast_date", "scenario",
            name="uq_forecast_tenant_agent_date_scenario",
        ),
        Index("ix_forecast_tenant_agent_date", "tenant_id", "agent_id", "forecast_date"),
        Index("ix_forecast_generated", "generated_at"),
    )


class LLMBudgetRisk(Base):
    """
    Predicted budget exhaustion event for a tenant.

    Created when the 'likely' forecast scenario shows the tenant
    will exhaust their monthly token limit or cost budget within
    the forecast horizon.

    One row per tenant — upserted on each forecast run.
    If the tenant is not at risk, the row is deleted.

    risk_type: "token_limit" / "cost_budget"
    days_until_exhaustion: how many days from today until the limit is hit
    exhaustion_date: the specific calendar date
    forecasted_value_at_exhaustion: the predicted value on that date
    governance_limit: the limit being approached
    """

    __tablename__ = "llm_budget_risk"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    risk_type: Mapped[str] = mapped_column(String(30), nullable=False)
    days_until_exhaustion: Mapped[int] = mapped_column(Integer, nullable=False)
    exhaustion_date: Mapped[date] = mapped_column(Date, nullable=False)
    forecasted_value_at_exhaustion: Mapped[float] = mapped_column(
        Float, nullable=False
    )
    governance_limit: Mapped[float] = mapped_column(Float, nullable=False)
    pct_of_limit_today: Mapped[float] = mapped_column(Float, nullable=False)
    # what % of the limit the tenant is at right now

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "risk_type",
            name="uq_budget_risk_tenant_type",
        ),
    )