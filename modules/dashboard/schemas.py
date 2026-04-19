"""
Dashboard module — Pydantic schemas.

These schemas define exactly what the frontend receives.
They are deliberately frontend-shaped, not module-shaped.
The service layer translates between module data and these schemas.
"""

import uuid
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel


# ============================================================
# EXECUTIVE OVERVIEW
# ============================================================

class TenantCostSummary(BaseModel):
    total_usd: str
    trend: str              # "up" / "down" / "stable"
    change_pct: float       # % change vs prior period


class TenantUsageSummary(BaseModel):
    total_tokens: int
    total_calls: int
    error_rate_pct: float


class TenantAnomalyStatus(BaseModel):
    has_active_anomaly: bool
    severity: Optional[str] = None   # None / "warning" / "high" / "critical"
    count: int = 0


class TenantForecastSummary(BaseModel):
    monthly_likely_tokens: float
    trend_slope: float
    trend_direction: str    # "up" / "down" / "flat"


class TenantBudgetRisk(BaseModel):
    at_risk: bool
    days_until_exhaustion: Optional[int] = None
    risk_type: Optional[str] = None   # "token_limit" / "cost_budget"
    urgency: Optional[str] = None     # "critical" / "warning" / "watch"


class TenantOverviewItem(BaseModel):
    tenant_id: str
    cost: TenantCostSummary
    usage: TenantUsageSummary
    anomaly_status: TenantAnomalyStatus
    forecast: Optional[TenantForecastSummary] = None
    budget_risk: TenantBudgetRisk


class ExecutiveOverviewSummary(BaseModel):
    total_tenants: int
    total_cost_usd: str
    tenants_at_risk: int
    active_anomalies: int
    critical_anomalies: int


class ExecutiveOverviewResponse(BaseModel):
    generated_at: datetime
    period_days: int
    tenants: list[TenantOverviewItem]
    summary: ExecutiveOverviewSummary


# ============================================================
# TENANT DEEP-DIVE
# ============================================================

class DailyCostPoint(BaseModel):
    date: str
    total_cost_usd: str
    total_tokens: int
    total_calls: int


class ModelCostItem(BaseModel):
    model: str
    provider: str
    call_count: int
    total_cost_usd: str
    cost_share_pct: float


class TenantCostDetail(BaseModel):
    period_days: int
    total_calls: int
    successful_calls: int
    error_calls: int
    blocked_calls: int
    total_tokens: int
    total_cost_usd: str
    avg_duration_ms: Optional[float]
    daily_trend: list[DailyCostPoint]
    model_breakdown: list[ModelCostItem]


class AnomalyItem(BaseModel):
    id: uuid.UUID
    anomaly_type: str
    severity: str
    detector_votes: str
    vote_count: int
    observed_value: float
    baseline_mean: float
    description: Optional[str]
    detected_at: datetime


class TenantAnomalyDetail(BaseModel):
    active: list[AnomalyItem]
    total_active: int


class ForecastDayPoint(BaseModel):
    forecast_date: str
    horizon_days: int
    likely_tokens: float
    pessimistic_tokens: float
    optimistic_tokens: float


class TenantForecastDetail(BaseModel):
    available: bool
    trend_slope: Optional[float] = None
    trend_slope_description: Optional[str] = None
    monthly_likely_tokens: Optional[float] = None
    daily: list[ForecastDayPoint] = []


class TenantGovernanceSummary(BaseModel):
    total_calls: int
    blocked_calls: int
    block_rate_pct: float
    downgraded_calls: int
    active_rules_count: int


class TenantDetailResponse(BaseModel):
    tenant_id: str
    generated_at: datetime
    period_days: int
    cost: TenantCostDetail
    anomalies: TenantAnomalyDetail
    forecast: TenantForecastDetail
    governance: TenantGovernanceSummary


# ============================================================
# ALERTS FEED
# ============================================================

class AlertItem(BaseModel):
    id: uuid.UUID
    alert_type: str          # "anomaly" / "budget_risk"
    severity: str
    tenant_id: str
    title: str
    description: str
    detected_at: datetime
    action_url: str


class AlertFeedResponse(BaseModel):
    generated_at: datetime
    alerts: list[AlertItem]
    total: int
    critical_count: int
    warning_count: int


# ============================================================
# AGENT DRILL-DOWN
# ============================================================

class AgentDetailItem(BaseModel):
    agent_id: str
    total_calls: int
    successful_calls: int
    error_calls: int
    error_rate_pct: float
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: str
    cost_share_pct: float
    avg_duration_ms: Optional[float] = None
    anomaly_count: int = 0


class AgentBreakdownResponse(BaseModel):
    tenant_id: str
    period_days: int
    generated_at: datetime
    agents: list[AgentDetailItem]
    total_agents: int


# ============================================================
# SYSTEM HEALTH
# ============================================================

class ModuleHealth(BaseModel):
    name: str
    status: str             # "ok" / "degraded" / "error"
    details: Optional[str] = None


class SystemHealthResponse(BaseModel):
    status: str             # "ok" / "degraded" / "error"
    generated_at: datetime
    modules: list[ModuleHealth]
    database: str
    redis: str