"""
Analytics module — FastAPI router.

Five endpoints:
  GET /v1/analytics/costs/summary/{tenant_id}
  GET /v1/analytics/costs/daily/{tenant_id}
  GET /v1/analytics/costs/models/{tenant_id}
  GET /v1/analytics/costs/agents/{tenant_id}
  GET /v1/analytics/overview
"""

import logging
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.analytics.schemas import (
    CostSummaryResponse,
    DailyTrendResponse,
    DailyDataPoint,
    ModelBreakdownResponse,
    ModelBreakdownItem,
    AgentBreakdownResponse,
    AgentBreakdownItem,
    AllTenantsOverviewResponse,
    TenantOverviewItem,
)
from modules.analytics.services import AnalyticsService
from modules.auth.dependencies import require_tenant_viewer, require_super_admin
from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/analytics", tags=["Analytics"])


def _resolve_dates(
    from_date: Optional[date],
    to_date: Optional[date],
    default_days: int = 30,
) -> tuple[datetime, datetime, date, date]:
    """Resolve optional date params to UTC datetimes."""
    now = datetime.now(timezone.utc)
    resolved_to = to_date or now.date()
    resolved_from = from_date or (now - timedelta(days=default_days)).date()

    if resolved_from > resolved_to:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"from_date ({resolved_from}) must be before to_date ({resolved_to})",
        )

    from_dt = datetime(
        resolved_from.year, resolved_from.month, resolved_from.day,
        0, 0, 0, tzinfo=timezone.utc,
    )
    to_dt = datetime(
        resolved_to.year, resolved_to.month, resolved_to.day,
        23, 59, 59, tzinfo=timezone.utc,
    )
    return from_dt, to_dt, resolved_from, resolved_to


async def get_analytics_service(
    session: AsyncSession = Depends(get_db),
) -> AnalyticsService:
    return AnalyticsService(session)


@router.get("/debug/ping")
async def ping_analytics(
    user: CurrentUser = Depends(require_tenant_viewer),
):
    return {"status": "ok", "user": user.username}


@router.get(
    "/costs/summary/{tenant_id}",
    summary="Get cost summary for a tenant",
)
async def get_cost_summary(
    tenant_id: str,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    service: AnalyticsService = Depends(get_analytics_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> CostSummaryResponse:
    user.require_tenant_access(tenant_id)
    from_dt, to_dt, resolved_from, resolved_to = _resolve_dates(
        from_date, to_date
    )
    try:
        summary = await service.get_cost_summary(tenant_id, from_dt, to_dt)
        
        # Manual serialization to avoid 500s in validation/JSON encoding
        import math
        def serialize(v):
            if isinstance(v, (datetime, date)): return v.isoformat()
            if isinstance(v, Decimal): return str(v)
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return None
            return v

        res = {
            "tenant_id": tenant_id,
            "from_date": resolved_from.isoformat(),
            "to_date": resolved_to.isoformat(),
            "total_calls": int(summary.get("total_calls", 0)),
            "successful_calls": int(summary.get("successful_calls", 0)),
            "error_calls": int(summary.get("error_calls", 0)),
            "blocked_calls": int(summary.get("blocked_calls", 0)),
            "total_input_tokens": int(summary.get("total_input_tokens", 0)),
            "total_output_tokens": int(summary.get("total_output_tokens", 0)),
            "total_tokens": int(summary.get("total_tokens", 0)),
            "total_cost_usd": str(summary.get("total_cost_usd", "0.00")),
            "avg_duration_ms": serialize(summary.get("avg_duration_ms")),
            "first_call_at": serialize(summary.get("first_call_at")),
            "last_call_at": serialize(summary.get("last_call_at")),
            "mom_cost_change_pct": serialize(summary.get("mom_cost_change_pct")),
            "mom_tokens_change_pct": serialize(summary.get("mom_tokens_change_pct")),
            "yoy_cost_change_pct": serialize(summary.get("yoy_cost_change_pct")),
            "yoy_tokens_change_pct": serialize(summary.get("yoy_tokens_change_pct")),
        }
        
        return JSONResponse(content=res)
    except Exception as e:
        logger.error(f"HARD FAIL in get_cost_summary({tenant_id}): {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analytics internal: {str(e)}")


@router.get(
    "/costs/daily/{tenant_id}",
    response_model=DailyTrendResponse,
    summary="Get daily cost trend for a tenant",
)
async def get_daily_trend(
    tenant_id: str,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    service: AnalyticsService = Depends(get_analytics_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> DailyTrendResponse:
    user.require_tenant_access(tenant_id)
    from_dt, to_dt, resolved_from, resolved_to = _resolve_dates(
        from_date, to_date
    )
    data = await service.get_daily_trend(tenant_id, from_dt, to_dt)
    return DailyTrendResponse(
        tenant_id=tenant_id,
        from_date=resolved_from,
        to_date=resolved_to,
        data=[DailyDataPoint(**d) for d in data],
        total_days=len(data),
    )


@router.get(
    "/costs/models/{tenant_id}",
    response_model=ModelBreakdownResponse,
    summary="Get cost breakdown by model for a tenant",
)
async def get_model_breakdown(
    tenant_id: str,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    service: AnalyticsService = Depends(get_analytics_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> ModelBreakdownResponse:
    user.require_tenant_access(tenant_id)
    from_dt, to_dt, resolved_from, resolved_to = _resolve_dates(
        from_date, to_date
    )
    models = await service.get_model_breakdown(tenant_id, from_dt, to_dt)
    return ModelBreakdownResponse(
        tenant_id=tenant_id,
        from_date=resolved_from,
        to_date=resolved_to,
        models=[ModelBreakdownItem(**m) for m in models],
    )


@router.get(
    "/costs/agents/{tenant_id}",
    response_model=AgentBreakdownResponse,
    summary="Get cost breakdown by agent for a tenant",
)
async def get_agent_breakdown(
    tenant_id: str,
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    service: AnalyticsService = Depends(get_analytics_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> AgentBreakdownResponse:
    user.require_tenant_access(tenant_id)
    from_dt, to_dt, resolved_from, resolved_to = _resolve_dates(
        from_date, to_date
    )
    agents = await service.get_agent_breakdown(tenant_id, from_dt, to_dt)
    return AgentBreakdownResponse(
        tenant_id=tenant_id,
        from_date=resolved_from,
        to_date=resolved_to,
        agents=[AgentBreakdownItem(**a) for a in agents],
    )


@router.get(
    "/overview",
    response_model=AllTenantsOverviewResponse,
    summary="Overview of all tenants ranked by cost",
)
async def get_overview(
    from_date: Optional[date] = Query(None),
    to_date: Optional[date] = Query(None),
    service: AnalyticsService = Depends(get_analytics_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> AllTenantsOverviewResponse:
    from_dt, to_dt, resolved_from, resolved_to = _resolve_dates(
        from_date, to_date
    )
    tenants = await service.get_all_tenants_overview(from_dt, to_dt)
    return AllTenantsOverviewResponse(
        from_date=resolved_from,
        to_date=resolved_to,
        tenants=[TenantOverviewItem(**t) for t in tenants],
        total_tenants=len(tenants),
    )


# ============================================================
# PRICING ROUTES
# ============================================================

from modules.analytics.schemas import PricingRuleCreate, PricingRuleResponse, AnomalyResponse
from modules.analytics.services.pricing_service import PricingService
from modules.analytics.services.anomaly_service import AnomalyService
from modules.analytics.tasks.pricing_tasks import apply_retroactive_pricing_task

async def get_pricing_service(session: AsyncSession = Depends(get_db)) -> PricingService:
    return PricingService(session)

async def get_anomaly_service(session: AsyncSession = Depends(get_db)) -> AnomalyService:
    return AnomalyService(session)

@router.get(
    "/pricing",
    response_model=list[PricingRuleResponse],
    summary="List all active pricing rules",
)
async def list_active_pricing(
    service: PricingService = Depends(get_pricing_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> list[PricingRuleResponse]:
    rules = await service.list_active_rules()
    return [PricingRuleResponse.model_validate(r) for r in rules]

@router.post(
    "/pricing",
    response_model=PricingRuleResponse,
    summary="Create a new versioned pricing rule",
)
async def create_pricing_rule(
    rule: PricingRuleCreate,
    service: PricingService = Depends(get_pricing_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> PricingRuleResponse:
    new_rule = await service.create_versioned_rule(
        provider=rule.provider,
        model=rule.model,
        input_price=rule.input_price,
        output_price=rule.output_price,
        effective_from=rule.effective_from,
    )
    return PricingRuleResponse.model_validate(new_rule)

@router.post(
    "/pricing/apply-retroactive/{rule_id}",
    summary="Retroactively fix cost anomalies for a specific DB rule",
)
async def apply_retroactive_rule(
    rule_id: str,
    _user: CurrentUser = Depends(require_super_admin),
):
    """Fires a Celery task to overwrite old pricing and re-aggregate data"""
    apply_retroactive_pricing_task.delay(rule_id)
    return {"status": "accepted", "task": "apply_retroactive_pricing_task"}

@router.get(
    "/pricing/anomalies",
    response_model=list[AnomalyResponse],
    summary="Auto-detect active pricing mismatches with global Litellm rates",
)
async def get_pricing_anomalies(
    service: AnomalyService = Depends(get_anomaly_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> list[AnomalyResponse]:
    anomalies = await service.detect_pricing_anomalies()
    return [AnomalyResponse(**a) for a in anomalies]