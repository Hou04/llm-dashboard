"""
Session Tracing & A/B Testing — Pydantic schemas.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ============================================================
# FEATURE 8: SESSION TRACING
# ============================================================

class SessionCreateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100, description="Client-provided session identifier")
    tenant_id: Optional[str] = Field(None, description="Tenant scope (auto-scoped for tenant users)")
    name: Optional[str] = Field(None, max_length=200, description="Human-readable session name")
    agent_id: Optional[str] = Field(None, max_length=100)
    user_id: Optional[str] = Field(None, max_length=100)
    metadata: Optional[dict] = None
    tags: Optional[list[str]] = None


class SessionResponse(BaseModel):
    id: str
    session_id: str
    tenant_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    name: Optional[str] = None
    total_calls: int
    total_tokens: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    total_duration_ms: int
    status: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    metadata: Optional[dict] = Field(None, alias="metadata_")
    tags: Optional[list[str]] = None
    call_chain: Optional[list] = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
    total: int
    page: int
    page_size: int


class SessionCallEntry(BaseModel):
    """A single call within a session waterfall."""
    log_id: str
    step: int
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cost_usd: str
    duration_ms: Optional[int] = None
    status: str
    prompt_preview: Optional[str] = None
    created_at: datetime


class SessionWaterfallResponse(BaseModel):
    """Waterfall view of all calls in a session."""
    session: SessionResponse
    calls: list[SessionCallEntry]
    total_cost_usd: float
    total_duration_ms: int


# ============================================================
# FEATURE 9: PROMPT A/B TESTING
# ============================================================

class ExperimentCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = None
    tenant_id: Optional[str] = None
    prompt_name: str = Field(..., description="Prompt template name to A/B test")
    variant_a_version: int = Field(..., ge=1, description="Control version (A)")
    variant_b_version: int = Field(..., ge=1, description="Treatment version (B)")
    traffic_split: float = Field(0.5, ge=0.0, le=1.0, description="Fraction routed to B")
    primary_metric: str = Field("quality_score", description="quality_score | latency | cost | error_rate")
    min_samples: int = Field(100, ge=10, description="Min samples per variant before declaring winner")


class ExperimentResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    description: Optional[str] = None
    prompt_name: str
    variant_a_version: int
    variant_b_version: int
    traffic_split: float
    primary_metric: str
    a_requests: int
    a_avg_latency_ms: float
    a_avg_cost_usd: float
    a_avg_quality: float
    a_error_count: int
    a_total_tokens: int
    b_requests: int
    b_avg_latency_ms: float
    b_avg_cost_usd: float
    b_avg_quality: float
    b_error_count: int
    b_total_tokens: int
    status: str
    winner: Optional[str] = None
    p_value: Optional[float] = None
    confidence_level: Optional[float] = None
    min_samples: int
    created_by: str
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ExperimentListResponse(BaseModel):
    experiments: list[ExperimentResponse]
    total: int


class ExperimentResultsResponse(BaseModel):
    """Detailed comparison of A vs B."""
    experiment: ExperimentResponse
    a_error_rate: float
    b_error_rate: float
    latency_improvement_pct: Optional[float] = None
    cost_improvement_pct: Optional[float] = None
    quality_improvement_pct: Optional[float] = None
    is_significant: bool
    recommendation: str


# ============================================================
# FEATURE 10: ALERT CONFIGURATION
# ============================================================

class AlertConfigCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    channel_type: str = Field(..., description="slack | teams | pagerduty | generic | email")
    webhook_url: str = Field(..., min_length=10, max_length=500)
    tenant_id: Optional[str] = None
    alert_types: Optional[list[str]] = Field(None, description="['blocked', 'budget_exceeded', 'anomaly', 'fallback']")
    severity_filter: Optional[str] = Field(None, description="Minimum severity: info | warning | error | critical")
    is_active: bool = True


class AlertConfigResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    channel_type: str
    webhook_url: str
    is_active: bool
    alert_types: Optional[list[str]] = None
    severity_filter: Optional[str] = None
    created_by: str
    created_at: datetime
    last_tested_at: Optional[datetime] = None
    last_alert_at: Optional[datetime] = None
    total_alerts_sent: int

    model_config = {"from_attributes": True}


class AlertConfigListResponse(BaseModel):
    configs: list[AlertConfigResponse]
    total: int


class AlertHistoryEntry(BaseModel):
    id: str
    tenant_id: str
    config_id: str
    channel_type: str
    alert_type: str
    success: bool
    status_code: Optional[int] = None
    error_message: Optional[str] = None
    sent_at: datetime

    model_config = {"from_attributes": True}


class AlertHistoryResponse(BaseModel):
    alerts: list[AlertHistoryEntry]
    total: int
    page: int
    page_size: int


class AlertTestResponse(BaseModel):
    success: bool
    status_code: Optional[int] = None
    message: str
