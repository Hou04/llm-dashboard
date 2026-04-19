"""Analytics module — Pydantic schemas for HTTP contracts."""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field


class CostSummaryResponse(BaseModel):
    tenant_id: str
    from_date: date
    to_date: date
    total_calls: int
    successful_calls: int
    error_calls: int
    blocked_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: str
    avg_duration_ms: Optional[float] = None
    first_call_at: Optional[datetime] = None
    last_call_at: Optional[datetime] = None
    
    # Trend Analysis fields
    mom_cost_change_pct: Optional[float] = None
    mom_tokens_change_pct: Optional[float] = None
    yoy_cost_change_pct: Optional[float] = None
    yoy_tokens_change_pct: Optional[float] = None

    model_config = {"from_attributes": True}

class PricingRuleCreate(BaseModel):
    provider: str = Field(..., description="LLM provider name")
    model: str = Field(..., description="LLM model name")
    input_price: Decimal = Field(..., ge=0, description="Input price per 1k tokens")
    output_price: Decimal = Field(..., ge=0, description="Output price per 1k tokens")
    effective_from: date = Field(..., description="When this pricing rule becomes effective")

class PricingRuleResponse(BaseModel):
    id: str
    provider: str
    model: str
    input_price_per_1k: str
    output_price_per_1k: str
    effective_from: date
    effective_to: Optional[date] = None
    is_active: bool
    
    model_config = {"from_attributes": True}

class AnomalyResponse(BaseModel):
    provider: str
    model: str
    db_input_price: str
    db_output_price: str
    litellm_input_price: str
    litellm_output_price: str
    anomaly_detected: bool


class DailyDataPoint(BaseModel):
    date: str
    total_calls: int
    successful_calls: int
    total_tokens: int
    total_cost_usd: str
    avg_duration_ms: Optional[float] = None


class DailyTrendResponse(BaseModel):
    tenant_id: str
    from_date: date
    to_date: date
    data: list[DailyDataPoint]
    total_days: int


class ModelBreakdownItem(BaseModel):
    model: str
    provider: str
    call_count: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    total_cost_usd: str
    avg_duration_ms: Optional[float] = None
    cost_share_pct: float


class ModelBreakdownResponse(BaseModel):
    tenant_id: str
    from_date: date
    to_date: date
    models: list[ModelBreakdownItem]


class AgentBreakdownItem(BaseModel):
    agent_id: str
    call_count: int
    total_tokens: int
    total_cost_usd: str


class AgentBreakdownResponse(BaseModel):
    tenant_id: str
    from_date: date
    to_date: date
    agents: list[AgentBreakdownItem]


class TenantOverviewItem(BaseModel):
    tenant_id: str
    total_calls: int
    total_tokens: int
    total_cost_usd: str
    error_calls: int
    error_rate_pct: float
    cost_share_pct: float


class AllTenantsOverviewResponse(BaseModel):
    from_date: date
    to_date: date
    tenants: list[TenantOverviewItem]
    total_tenants: int