"""
Gateway module — FastAPI router.

Exposes M1 (log/usage/health) AND M7 (governance) endpoints,
because M7 is architecturally part of M1:

  POST /v1/gateway/log                                — log an LLM call
  GET  /v1/gateway/usage/{tid}                        — tenant usage summary
  GET  /v1/gateway/health                             — module health check

  --- M7 Governance (sub-routes under /v1/gateway/governance) ---
  GET    /v1/gateway/governance/rules                 — list rules
  POST   /v1/gateway/governance/rules                 — create rule
  GET    /v1/gateway/governance/rules/{id}            — get rule
  PATCH  /v1/gateway/governance/rules/{id}            — update rule
  DELETE /v1/gateway/governance/rules/{id}            — deactivate rule
  GET    /v1/gateway/governance/decisions             — audit log
  GET    /v1/gateway/governance/decisions/{tenant}    — tenant decisions
  GET    /v1/gateway/governance/summary               — KPI dashboard
  GET    /v1/gateway/governance/quota/{tenant_id}     — live quota status
"""

import logging
from datetime import datetime, timezone, date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.gateway.schemas import (
    LogCallRequest,
    LogCallResponse,
    UsageSummaryResponse,
    HealthResponse,
    StreamChunkRequest,
    # M7 governance schemas
    GovernanceRuleCreate,
    GovernanceRuleUpdate,
    GovernanceRuleResponse,
    GovernanceRuleListResponse,
    GovernanceDecisionResponse,
    GovernanceDecisionListResponse,
    GovernanceQuotaStatus,
    GovernanceSummaryResponse,
)
from modules.gateway.services import GatewayService, CallRequest
from modules.gateway.services.governance_service import GovernanceService
from modules.auth.dependencies import (
    get_current_user,
    require_tenant_admin,
    require_tenant_viewer,
)
from modules.auth.schemas import CurrentUser
from core.sanitize import sanitize_text, sanitize_identifier
from core.audit import audit
from core.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/gateway", tags=["Gateway"])



# ============================================================
# DEPENDENCY — build the service for this request
# ============================================================

async def get_gateway_service(
    session: AsyncSession = Depends(get_db),
) -> GatewayService:
    """
    FastAPI dependency that creates a GatewayService for the current request.

    Each HTTP request gets its own service instance with its own database
    session. When the request ends, the session is automatically closed
    by the get_db() context manager.
    """
    return GatewayService(session)


# ============================================================
# ENDPOINTS
# ============================================================

@router.post(
    "/log",
    response_model=LogCallResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Log an LLM API call",
    description=(
        "Records a completed LLM API call. Evaluates governance rules "
        "and returns the decision. Blocked calls return 201 with "
        "success=False — they are logged as blocked, not as HTTP errors."
    ),
)
async def log_call(
    request: LogCallRequest,
    service: GatewayService = Depends(get_gateway_service),
    _user: CurrentUser = Depends(require_tenant_viewer),
) -> LogCallResponse:
    """
    Log one LLM API call and evaluate governance.

    Returns 201 in all cases where the request is valid — even if the
    call was blocked by governance. HTTP errors (4xx/5xx) are reserved
    for invalid requests or server failures, not governance decisions.

    A blocked call is a valid, expected outcome. The caller learns about
    it via success=False and decision="block" in the response body.
    """
    call_request = CallRequest(
        tenant_id=request.tenant_id,
        model=request.model,
        provider=request.provider,
        input_tokens=request.input_tokens,
        output_tokens=request.output_tokens,
        total_tokens=request.total_tokens,
        cost_usd=request.cost_usd,
        agent_id=request.agent_id,
        user_id=request.user_id,
        site_id=request.site_id,
        module=request.module,
        pricing_profile=request.pricing_profile,
        duration_ms=request.duration_ms,
        status=request.status,
        request_id=request.request_id,
        error_message=request.error_message,
        metadata=request.metadata,
    )

    result = await service.log_call(call_request)

    return LogCallResponse(
        success=result.success,
        log_id=result.log_id,
        decision=result.decision,
        model_used=result.model_used,
        was_downgraded=result.was_downgraded,
        error=result.error,
    )


@router.post(
    "/log/stream",
    response_model=dict,
    status_code=status.HTTP_200_OK,
    summary="Log a streaming token chunk",
    description="Accumulates streaming token generation per request_id in Redis.",
)
async def log_stream_chunk_endpoint(
    request: StreamChunkRequest,
    service: GatewayService = Depends(get_gateway_service),
    _user: CurrentUser = Depends(require_tenant_viewer),
):
    """
    Record an incremental chunk of total tokens generated in a streaming response.
    When generation is complete, the client must call /log to finalize it.
    """
    total = await service.log_stream_chunk(
        request_id=request.request_id,
        tenant_id=request.tenant_id,
        delta_tokens=request.delta_tokens,
    )
    return {"status": "ok", "total_accumulated_tokens": total}


# ============================================================
# SECURITY ENDPOINTS
# ============================================================

from modules.gateway.schemas import (
    PiiAnonymizeRequest, PiiAnonymizeResponse,
    PromptInjectionRequest, PromptInjectionResponse
)
from modules.gateway.services.security_service import SecurityService

@router.post(
    "/security/pii/pseudonymize",
    response_model=PiiAnonymizeResponse,
    summary="Anonymize PII in text",
)
async def anonymize_pii(request: PiiAnonymizeRequest):
    service = SecurityService()
    result = service.anonymize_text(request.text, request.language)
    return PiiAnonymizeResponse(
        anonymized_text=result["text"],
        items_found=len(result["items"])
    )

@router.post(
    "/security/prompt-injection/detect",
    response_model=PromptInjectionResponse,
    summary="Detect prompt injection using DistilBERT",
)
async def detect_injection(request: PromptInjectionRequest):
    service = SecurityService()
    result = service.detect_prompt_injection(request.prompt)
    return PromptInjectionResponse(**result)


@router.get(
    "/usage/{tenant_id}",
    response_model=UsageSummaryResponse,
    summary="Get tenant usage summary",
    description=(
        "Returns aggregated token usage and cost for a tenant over a "
        "specified time period. Defaults to the last 30 days if no "
        "dates are provided."
    ),
)
async def get_usage(
    tenant_id: str,
    from_date: Optional[date] = Query(default=None, description="Start date (UTC). Defaults to 30 days ago.", examples=["2026-01-01"]),
    to_date: Optional[date] = Query(default=None, description="End date (UTC). Defaults to today.", examples=["2026-03-11"]),
    service: GatewayService = Depends(get_gateway_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> UsageSummaryResponse:
    """
    Get usage summary for a tenant.

    Date range defaults to the last 30 days if not specified.
    Dates are interpreted as UTC calendar days (full day inclusive).
    """
    user.require_tenant_access(tenant_id)
    # Apply defaults
    now = datetime.now(timezone.utc)
    resolved_to = to_date or now.date()
    resolved_from = from_date or (now - timedelta(days=30)).date()

    if resolved_from > resolved_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"from_date ({resolved_from}) must be before to_date ({resolved_to})",
        )

    # Convert dates to UTC datetimes (full day inclusive)
    from_dt = datetime(
        resolved_from.year, resolved_from.month, resolved_from.day,
        0, 0, 0, tzinfo=timezone.utc
    )
    to_dt = datetime(
        resolved_to.year, resolved_to.month, resolved_to.day,
        23, 59, 59, tzinfo=timezone.utc
    )

    summary = await service.get_tenant_usage(
        tenant_id=tenant_id,
        from_dt=from_dt,
        to_dt=to_dt,
    )

    return UsageSummaryResponse(
        tenant_id=tenant_id,
        from_date=resolved_from,
        to_date=resolved_to,
        total_calls=summary["total_calls"],
        successful_calls=summary["successful_calls"],
        failed_calls=summary["total_calls"] - summary["successful_calls"],
        total_input_tokens=summary["total_input_tokens"],
        total_output_tokens=summary["total_output_tokens"],
        total_tokens=summary["total_tokens"],
        total_cost_usd=str(summary["total_cost_usd"]),
        avg_duration_ms=summary["avg_duration_ms"],
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Gateway health check",
)
async def health_check() -> HealthResponse:
    """Returns 200 if the gateway module is running."""
    return HealthResponse(
        status="ok",
        module="gateway",
        timestamp=datetime.now(timezone.utc),
    )


# ============================================================
# M7 GOVERNANCE — dependency
# ============================================================

async def get_governance_service(
    session: AsyncSession = Depends(get_db),
) -> GovernanceService:
    """FastAPI dependency that creates a GovernanceService for the current request."""
    return GovernanceService(session)


# ============================================================
# M7 GOVERNANCE — Rules CRUD
# ============================================================

@router.get(
    "/governance/rules",
    response_model=GovernanceRuleListResponse,
    summary="List governance rules",
    description=(
        "Returns all governance rules. "
        "Filter by tenant_id or rule_type to narrow the results. "
        "By default only active rules are returned."
    ),
    tags=["Gateway", "Governance M7"],
)
async def list_governance_rules(
    tenant_id: Optional[str] = Query(default=None, description="Filter by tenant."),
    active_only: bool = Query(default=True),
    rule_type: Optional[str] = Query(default=None),
    service: GovernanceService = Depends(get_governance_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> GovernanceRuleListResponse:
    # Non-super-admins can only see their own tenant's rules
    if not user.is_super_admin() and tenant_id and tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Access denied to this tenant's rules.")
    rules = await service.list_rules(
        tenant_id=tenant_id,
        active_only=active_only,
        rule_type=rule_type,
    )
    active_count = sum(1 for r in rules if r.is_active)
    return GovernanceRuleListResponse(
        rules=rules,
        total=len(rules),
        active_count=active_count,
    )


@router.post(
    "/governance/rules",
    response_model=GovernanceRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a governance rule",
    description=(
        "Creates a new governance rule. "
        "Rules with NULL tenant_id apply globally to all tenants. "
        "Higher priority values (max 1000) win when multiple rules match."
    ),
    tags=["Gateway", "Governance M7"],
)
async def create_governance_rule(
    request: GovernanceRuleCreate,
    service: GovernanceService = Depends(get_governance_service),
    user: CurrentUser = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_db),
) -> GovernanceRuleResponse:
    # Sanitize user-supplied text fields
    data = request.model_dump()
    if data.get("description"):
        data["description"] = sanitize_text(data["description"], max_length=500)
    if data.get("tenant_id"):
        data["tenant_id"] = sanitize_identifier(data["tenant_id"])
    if data.get("model_name"):
        data["model_name"] = sanitize_identifier(data["model_name"])
    rule = await service.create_rule(data)
    # Audit log
    await audit.log(
        session=session, user=user, action="create",
        resource_type="governance_rule",
        resource_id=str(rule.get("id", "")),
        description=f"Created governance rule: {data.get('rule_type', 'unknown')}",
        details=data,
    )
    return GovernanceRuleResponse.model_validate(rule)


@router.get(
    "/governance/rules/{rule_id}",
    response_model=GovernanceRuleResponse,
    summary="Get a governance rule by ID",
    tags=["Gateway", "Governance M7"],
)
async def get_governance_rule(
    rule_id: UUID,
    service: GovernanceService = Depends(get_governance_service),
    _user: CurrentUser = Depends(require_tenant_viewer),
) -> GovernanceRuleResponse:
    rule = await service.get_rule(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Governance rule {rule_id} not found.",
        )
    return GovernanceRuleResponse.model_validate(rule)


@router.patch(
    "/governance/rules/{rule_id}",
    response_model=GovernanceRuleResponse,
    summary="Update a governance rule",
    description=(
        "Partially updates a governance rule. "
        "Only provided fields are changed. "
        "To deactivate a rule without deleting it, set is_active=false."
    ),
    tags=["Gateway", "Governance M7"],
)
async def update_governance_rule(
    rule_id: UUID,
    request: GovernanceRuleUpdate,
    service: GovernanceService = Depends(get_governance_service),
    _user: CurrentUser = Depends(require_tenant_admin),
) -> GovernanceRuleResponse:
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    # Sanitize user-supplied text fields
    if "description" in updates:
        updates["description"] = sanitize_text(updates["description"], max_length=500)
    if "tenant_id" in updates:
        updates["tenant_id"] = sanitize_identifier(updates["tenant_id"])
    if "model_name" in updates:
        updates["model_name"] = sanitize_identifier(updates["model_name"])
    rule = await service.update_rule(rule_id, updates)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Governance rule {rule_id} not found.",
        )
    # Audit log
    await audit.log(
        session=service.session, user=_user, action="update",
        resource_type="governance_rule",
        resource_id=str(rule_id),
        description=f"Updated governance rule fields: {list(updates.keys())}",
        details={"updates": updates},
    )
    return GovernanceRuleResponse.model_validate(rule)


@router.delete(
    "/governance/rules/{rule_id}",
    response_model=GovernanceRuleResponse,
    summary="Deactivate a governance rule (soft delete)",
    description=(
        "Marks a rule as inactive without deleting it from the database. "
        "Inactive rules are never deleted — they are kept for the audit trail. "
        "To re-enable a rule, PATCH it with is_active=true."
    ),
    tags=["Gateway", "Governance M7"],
)
async def deactivate_governance_rule(
    rule_id: UUID,
    service: GovernanceService = Depends(get_governance_service),
    _user: CurrentUser = Depends(require_tenant_admin),
) -> GovernanceRuleResponse:
    rule = await service.deactivate_rule(rule_id)
    if rule is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Governance rule {rule_id} not found.",
        )
    # Audit log
    await audit.log(
        session=service.session, user=_user, action="deactivate",
        resource_type="governance_rule",
        resource_id=str(rule_id),
        description=f"Deactivated governance rule {rule_id}",
    )
    return GovernanceRuleResponse.model_validate(rule)


# ============================================================
# M7 GOVERNANCE — Decisions audit log
# ============================================================

@router.get(
    "/governance/decisions",
    response_model=GovernanceDecisionListResponse,
    summary="Get governance decision audit log",
    description=(
        "Returns the audit log of governance decisions. "
        "Each row corresponds to one LLM call that triggered a governance evaluation. "
        "Decisions: allow | allow_downgrade | block."
    ),
    tags=["Gateway", "Governance M7"],
)
async def list_governance_decisions(
    hours: int = Query(
        default=24,
        ge=1,
        le=168,
        description="Lookback window in hours (max 168 = 7 days).",
    ),
    decision_type: Optional[str] = Query(
        default=None,
        description="Filter by decision type: allow | allow_downgrade | block",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description="Maximum number of decisions to return.",
    ),
    service: GovernanceService = Depends(get_governance_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> GovernanceDecisionListResponse:
    # Super admins see all; others see only their own if filtrable
    # BUT this endpoint is typically for the global/super admin view.
    # We should still restrict non-super-admins to their own tenant if no tenant_id provided.
    tenant_filter = None
    if not user.is_super_admin():
        tenant_filter = user.tenant_id

    decisions = await service.list_decisions(
        tenant_id=tenant_filter,
        hours=hours,
        decision_type=decision_type,
        limit=limit,
    )
    return GovernanceDecisionListResponse(
        decisions=decisions,
        total=len(decisions),
        tenant_id=None,
        period_hours=hours,
    )


@router.get(
    "/governance/decisions/{tenant_id}",
    response_model=GovernanceDecisionListResponse,
    summary="Get governance decisions for a specific tenant",
    tags=["Gateway", "Governance M7"],
)
async def list_tenant_governance_decisions(
    tenant_id: str,
    hours: int = Query(default=24, ge=1, le=168),
    decision_type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    service: GovernanceService = Depends(get_governance_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> GovernanceDecisionListResponse:
    user.require_tenant_access(tenant_id)
    decisions = await service.list_decisions(
        tenant_id=tenant_id,
        hours=hours,
        decision_type=decision_type,
        limit=limit,
    )
    return GovernanceDecisionListResponse(
        decisions=decisions,
        total=len(decisions),
        tenant_id=tenant_id,
        period_hours=hours,
    )


# ============================================================
# M7 GOVERNANCE — KPI summary & quota status
# ============================================================

@router.get(
    "/governance/summary",
    response_model=GovernanceSummaryResponse,
    summary="Governance KPI summary — dashboard data",
    description=(
        "Returns real-time governance KPIs: total decisions, block rate, "
        "downgrade rate, active rules count, and per-tenant breakdown. "
        "All values come from the database — zero hardcoded data."
    ),
    tags=["Gateway", "Governance M7"],
)
async def get_governance_summary(
    period_days: int = Query(default=30, ge=1, le=90),
    service: GovernanceService = Depends(get_governance_service),
    _user: CurrentUser = Depends(require_tenant_viewer),
) -> GovernanceSummaryResponse:
    summary = await service.get_summary(period_days=period_days)
    return GovernanceSummaryResponse(**summary)


@router.get(
    "/governance/quota/{tenant_id}",
    response_model=GovernanceQuotaStatus,
    summary="Get live quota status for a tenant",
    description=(
        "Returns the current quota consumption for a tenant. "
        "Reads today's decision log to compute tokens used and compares "
        "against the tightest active governance rules. "
        "Status: ok | warning (≥75%) | critical (≥90%) | blocked (≥100%)."
    ),
    tags=["Gateway", "Governance M7"],
)
async def get_quota_status(
    tenant_id: str,
    service: GovernanceService = Depends(get_governance_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> GovernanceQuotaStatus:
    user.require_tenant_access(tenant_id)
    quota = await service.get_quota_status(tenant_id)
    return GovernanceQuotaStatus(**quota)