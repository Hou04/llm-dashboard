"""Forecasting module — Pydantic schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


class DailyForecastPoint(BaseModel):
    forecast_date: date
    horizon_days: int
    likely_tokens: float
    pessimistic_tokens: float
    optimistic_tokens: float
    predicted_cost_usd: str
    trend_value: Optional[float] = None
    seasonal_multiplier: Optional[float] = None
    confidence_lower: Optional[float] = None
    confidence_upper: Optional[float] = None

    model_config = {"from_attributes": True}


class ForecastSummary(BaseModel):
    tenant_id: str
    generated_at: Optional[datetime] = None
    horizon_days: int
    trend_slope: float
    trend_slope_description: str     # human-readable e.g. "+18,000 tokens/day"
    monthly_likely_tokens: float
    monthly_pessimistic_tokens: float
    monthly_optimistic_tokens: float
    monthly_likely_cost_usd: str
    avg_cost_per_token: float
    history_days_used: int


class ForecastResponse(BaseModel):
    tenant_id: str
    summary: Optional[ForecastSummary] = None
    daily: list[DailyForecastPoint]
    total_days: int


class BudgetRiskItem(BaseModel):
    tenant_id: str
    risk_type: str
    days_until_exhaustion: int
    exhaustion_date: date
    forecasted_value_at_exhaustion: float
    governance_limit: float
    pct_of_limit_today: float
    urgency: str   # "critical" / "warning" / "watch"

    model_config = {"from_attributes": True}


class BudgetRiskResponse(BaseModel):
    risks: list[BudgetRiskItem]
    total: int
    critical_count: int    # <= 7 days
    warning_count: int     # 8-14 days