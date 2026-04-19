"""
Gateway module — Pydantic schemas for HTTP request/response contracts.

These schemas define the public API interface. They are deliberately
separate from SQLAlchemy models:
- SQLAlchemy models = database shape
- Pydantic schemas = HTTP contract

Rules for schemas:
- Request schemas validate incoming data (strict types, clear errors)
- Response schemas control what we expose (never leak internal fields)
- Field descriptions become API documentation automatically
- All monetary values use strings in JSON to avoid float precision loss
"""

import uuid
from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ============================================================
# REQUEST SCHEMAS — what the API accepts
# ============================================================

class LogCallRequest(BaseModel):
    """
    Request body for POST /v1/gateway/log.

    Sent by the LLM proxy after completing (or failing) an API call.
    The proxy fills in token counts and cost; the gateway logs and
    applies governance decisions.
    """
    tenant_id: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique identifier for the tenant making this call",
        examples=["enterprise_corp"],
    )
    model: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="LLM model name as returned by the provider",
        examples=["gpt-4o-mini", "claude-3-haiku"],
    )
    provider: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="LLM provider name",
        examples=["openai", "anthropic"],
    )
    input_tokens: int = Field(
        ...,
        ge=0,
        description="Number of tokens in the input/prompt",
    )
    output_tokens: int = Field(
        ...,
        ge=0,
        description="Number of tokens in the model's response",
    )
    total_tokens: int = Field(
        ...,
        ge=0,
        description="Total tokens consumed (input + output)",
    )
    cost_usd: Decimal = Field(
        ...,
        ge=Decimal("0"),
        decimal_places=8,
        description="Cost in USD for this call",
        examples=["0.00007500"],
    )

    # Optional fields
    agent_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Identifier for the agent or application making the call",
    )
    user_id: Optional[str] = Field(
        None,
        max_length=100,
        description="End-user identifier, if applicable",
    )
    site_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Physical or logical site identifier",
    )
    module: Optional[str] = Field(
        None,
        max_length=100,
        description="Application module generating the call",
    )
    pricing_profile: Optional[str] = Field(
        None,
        max_length=100,
        description="Pricing tier or contract profile applied",
    )
    duration_ms: Optional[int] = Field(
        None,
        ge=0,
        description="Call duration in milliseconds",
    )
    status: str = Field(
        default="success",
        description="Call outcome: success / error / timeout / blocked",
        examples=["success", "error", "timeout"],
    )
    request_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Caller-provided request ID for correlation",
    )
    error_message: Optional[str] = Field(
        None,
        max_length=2000,
        description="Error details if status is error or timeout",
    )
    metadata: Optional[dict] = Field(
        None,
        description="Arbitrary key-value metadata for this call",
    )

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        valid = {"success", "error", "timeout", "blocked"}
        if v not in valid:
            raise ValueError(f"status must be one of: {', '.join(sorted(valid))}")
        return v

    @model_validator(mode="after")
    def total_tokens_must_be_consistent(self) -> "LogCallRequest":
        """total_tokens should equal input + output (with tolerance for rounding)."""
        expected = self.input_tokens + self.output_tokens
        if abs(self.total_tokens - expected) > 1:
            raise ValueError(
                f"total_tokens ({self.total_tokens}) must equal "
                f"input_tokens + output_tokens ({expected})"
            )
        return self

class StreamChunkRequest(BaseModel):
    """
    Request body for POST /v1/gateway/log/stream.
    Used to asynchronously report streaming tokens for consolidation.
    """
    request_id: str = Field(..., description="The unique stream correlation ID")
    tenant_id: str = Field(..., description="Unique identifier for the tenant")
    delta_tokens: int = Field(..., ge=1, description="Number of tokens generated in this chunk")


# ============================================================
# RESPONSE SCHEMAS — what the API returns
# ============================================================

class LogCallResponse(BaseModel):
    """
    Response body for POST /v1/gateway/log.

    Returns the outcome of logging the call and the governance decision.
    """
    success: bool = Field(
        description="Whether the call was logged successfully",
    )
    log_id: Optional[uuid.UUID] = Field(
        None,
        description="UUID of the created log entry. Null if blocked.",
    )
    decision: str = Field(
        description="Governance decision: allow / allow_downgrade / block",
    )
    model_used: str = Field(
        description="Model that was actually used (may differ if downgraded)",
    )
    was_downgraded: bool = Field(
        default=False,
        description="True if the model was substituted by a governance rule",
    )
    error: Optional[str] = Field(
        None,
        description="Error message if success=False",
    )

    model_config = {"from_attributes": True}


class UsageSummaryResponse(BaseModel):
    """
    Response body for GET /v1/gateway/usage/{tenant_id}.

    Aggregated usage statistics for a tenant over a time period.
    Cost values are returned as strings to preserve decimal precision.
    """
    tenant_id: str
    from_date: date
    to_date: date

    total_calls: int
    successful_calls: int
    failed_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_cost_usd: str = Field(
        description="Total cost as decimal string to preserve precision",
        examples=["12.34567890"],
    )
    avg_duration_ms: Optional[float] = Field(
        None,
        description="Average call duration in milliseconds",
    )

    model_config = {"from_attributes": True}


class HealthResponse(BaseModel):
    """Response for GET /v1/gateway/health."""
    status: str
    module: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """Standard error response shape for all 4xx and 5xx responses."""
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None

# ============================================================
# SECURITY — Pydantic schemas
# ============================================================

class PiiAnonymizeRequest(BaseModel):
    text: str = Field(..., description="Raw text that might contain PII")
    language: str = Field("en", description="Language code for the analyzer")

class PiiAnonymizeResponse(BaseModel):
    anonymized_text: str
    items_found: int
    
class PromptInjectionRequest(BaseModel):
    prompt: str = Field(..., description="LLM prompt to check for injection attempts")

class PromptInjectionResponse(BaseModel):
    is_injection: bool = Field(..., description="True if the prompt is classified as malicious")
    score: float = Field(..., description="Confidence score from the classifier")
    label: str = Field(..., description="Raw prediction label")


# ============================================================
# M7 GOVERNANCE — Pydantic schemas
# These models live here because M7 is part of M1 (Gateway)
# ============================================================

class GovernanceRuleCreate(BaseModel):
    """Request body for POST /v1/gateway/governance/rules."""
    rule_type:              str            = Field(..., description="tenant_limit | model_block | budget_cap | rate_limit | model_downgrade")
    tenant_id:              Optional[str]  = Field(None, description="Target tenant. NULL = global rule (applies to all tenants).")
    model_name:             Optional[str]  = None
    daily_token_limit:      Optional[int]  = None
    monthly_token_limit:    Optional[int]  = None
    monthly_budget_usd:     Optional[Decimal] = None
    max_tokens_per_request: Optional[int]  = None
    priority:               int            = Field(default=100, ge=1, le=1000)
    downgrade_to_model:     Optional[str]  = None
    description:            Optional[str]  = None
    created_by:             Optional[str]  = None


class GovernanceRuleUpdate(BaseModel):
    """Partial update — only provided fields are changed."""
    description:            Optional[str]     = None
    daily_token_limit:      Optional[int]     = None
    monthly_token_limit:    Optional[int]     = None
    monthly_budget_usd:     Optional[Decimal] = None
    max_tokens_per_request: Optional[int]     = None
    priority:               Optional[int]     = Field(None, ge=1, le=1000)
    is_active:              Optional[bool]    = None
    downgrade_to_model:     Optional[str]     = None


class GovernanceRuleResponse(BaseModel):
    """Single governance rule — full detail."""
    id:                     uuid.UUID
    rule_type:              str
    tenant_id:              Optional[str]     = None
    model_name:             Optional[str]     = None
    daily_token_limit:      Optional[int]     = None
    monthly_token_limit:    Optional[int]     = None
    monthly_budget_usd:     Optional[Decimal] = None
    max_tokens_per_request: Optional[int]     = None
    priority:               int
    is_active:              bool
    downgrade_to_model:     Optional[str]     = None
    description:            Optional[str]     = None
    created_by:             Optional[str]     = None
    created_at:             datetime
    updated_at:             datetime

    model_config = {"from_attributes": True}


class GovernanceRuleListResponse(BaseModel):
    rules:        list[GovernanceRuleResponse]
    total:        int
    active_count: int


class GovernanceDecisionResponse(BaseModel):
    """One governance decision from the audit log."""
    id:                uuid.UUID
    request_id:        Optional[str]     = None
    tenant_id:         str
    model_requested:   str
    model_used:        Optional[str]     = None
    decision:          str               # allow | allow_downgrade | block
    reason:            Optional[str]     = None
    rule_id:           Optional[uuid.UUID] = None
    tokens_used_today: Optional[int]     = None
    budget_used_today: Optional[Decimal] = None
    evaluated_at:      datetime

    model_config = {"from_attributes": True}


class GovernanceDecisionListResponse(BaseModel):
    decisions:    list[GovernanceDecisionResponse]
    total:        int
    tenant_id:    Optional[str] = None
    period_hours: int


class GovernanceQuotaStatus(BaseModel):
    """Live quota status for a tenant derived from today's decisions."""
    tenant_id:               str
    tokens_used_today:       int
    daily_token_limit:       Optional[int]   = None
    pct_daily_used:          Optional[float] = None
    budget_used_today_usd:   float
    monthly_budget_usd:      Optional[float] = None
    pct_monthly_budget_used: Optional[float] = None
    status:                  str             # ok | warning | critical | blocked


class GovernanceSummaryResponse(BaseModel):
    """Real-time governance KPIs for the dashboard — zero hardcoded values."""
    period_days:                int
    generated_at:               datetime
    total_decisions:            int
    allowed_count:              int
    blocked_count:              int
    downgraded_count:           int
    block_rate_pct:             float
    downgrade_rate_pct:         float
    active_rules_count:         int
    total_rules_count:          int
    estimated_cost_blocked_usd: float
    estimated_cost_saved_usd:   float
    tenant_stats:               list[dict]