"""Dashboard module — FastAPI router. The BFF (Backend for Frontend)."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.dashboard.schemas import (
    ExecutiveOverviewResponse,
    TenantOverviewItem,
    TenantCostSummary,
    TenantUsageSummary,
    TenantAnomalyStatus,
    TenantForecastSummary,
    TenantBudgetRisk,
    ExecutiveOverviewSummary,
    TenantDetailResponse,
    TenantCostDetail,
    DailyCostPoint,
    ModelCostItem,
    TenantAnomalyDetail,
    AnomalyItem,
    TenantForecastDetail,
    ForecastDayPoint,
    TenantGovernanceSummary,
    AlertFeedResponse,
    AlertItem,
    SystemHealthResponse,
    ModuleHealth,
    AgentBreakdownResponse,
    AgentDetailItem,
)
from modules.dashboard.services import DashboardService
from datetime import datetime, timezone
from modules.dashboard.services.assistant_service import AssistantService
from modules.auth.dependencies import get_current_user, require_tenant_viewer
from modules.auth.schemas import CurrentUser
from core.sanitize import sanitize_text
from core.response_cache import response_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/dashboard", tags=["Dashboard"])


async def get_dashboard_service(
    session: AsyncSession = Depends(get_db),
) -> DashboardService:
    return DashboardService(session)

async def get_assistant_service(
    session: AsyncSession = Depends(get_db),
) -> AssistantService:
    return AssistantService(session)


@router.get(
    "/tenants",
    summary="Dynamic tenant list — discovered from actual data",
)
async def list_tenants(
    service: DashboardService = Depends(get_dashboard_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    """Returns the list of tenants that have data in the system.
    Used by the frontend to dynamically populate all tenant selectors."""
    tenants = await service._discover_tenants()
    
    if not user.is_super_admin():
        if user.tenant_id in tenants:
            tenants = [user.tenant_id]
        else:
            tenants = []
            
    return {"tenants": tenants, "count": len(tenants)}

@router.get(
    "/executive",
    response_model=ExecutiveOverviewResponse,
    summary="Executive overview — all tenants, costs, anomalies, forecasts",
)
async def executive_overview(
    period_days: int = Query(default=30, ge=7, le=90),
    service: DashboardService = Depends(get_dashboard_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> ExecutiveOverviewResponse:
    # Check cache first (60s TTL)
    cache_key_params = dict(period=period_days)
    cached = await response_cache.get("executive_overview", **cache_key_params)
    if cached is not None:
        data = cached
    else:
        data = await service.get_executive_overview(period_days=period_days)
        # Cache the raw dict before tenant filtering (shared across users)
        await response_cache.set("executive_overview", data, **cache_key_params)

    # Tenant-scoped users only see their own tenant's data
    if not user.is_super_admin():
        data["tenants"] = [t for t in data["tenants"] if t["tenant_id"] == user.tenant_id]

    tenants = []
    for t in data["tenants"]:
        forecast_data = t.get("forecast")
        tenants.append(TenantOverviewItem(
            tenant_id=t["tenant_id"],
            cost=TenantCostSummary(**t["cost"]),
            usage=TenantUsageSummary(**t["usage"]),
            anomaly_status=TenantAnomalyStatus(**t["anomaly_status"]),
            forecast=TenantForecastSummary(**forecast_data)
            if forecast_data else None,
            budget_risk=TenantBudgetRisk(**t["budget_risk"]),
        ))

    return ExecutiveOverviewResponse(
        generated_at=data["generated_at"],
        period_days=data["period_days"],
        tenants=tenants,
        summary=ExecutiveOverviewSummary(**data["summary"]),
    )


@router.get(
    "/tenant/{tenant_id}/agents",
    response_model=AgentBreakdownResponse,
    summary="Agent-level drill-down for a tenant",
)
async def agent_breakdown(
    tenant_id: str,
    period_days: int = Query(default=30, ge=7, le=90),
    service: DashboardService = Depends(get_dashboard_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> AgentBreakdownResponse:
    user.require_tenant_access(tenant_id)
    data = await service.get_agent_breakdown(tenant_id, period_days)
    return AgentBreakdownResponse(
        tenant_id=data["tenant_id"],
        period_days=data["period_days"],
        generated_at=data["generated_at"],
        agents=[AgentDetailItem(**a) for a in data["agents"]],
        total_agents=data["total_agents"],
    )


@router.get(
    "/tenant/{tenant_id}",
    response_model=TenantDetailResponse,
    summary="Full detail view for one tenant",
)
async def tenant_detail(
    tenant_id: str,
    period_days: int = Query(default=30, ge=7, le=90),
    service: DashboardService = Depends(get_dashboard_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> TenantDetailResponse:
    user.require_tenant_access(tenant_id)
    # Check cache first (30s TTL)
    cache_key_params = dict(tenant_id=tenant_id, period=period_days)
    cached = await response_cache.get("tenant_detail", **cache_key_params)
    if cached is not None:
        data = cached
    else:
        data = await service.get_tenant_detail(tenant_id, period_days)
        await response_cache.set("tenant_detail", data, **cache_key_params)

    cost_data = data["cost"]
    daily_trend = [DailyCostPoint(**d) for d in cost_data["daily_trend"]]
    model_breakdown = [ModelCostItem(**m) for m in cost_data["model_breakdown"]]

    anomaly_data = data["anomalies"]
    active_anomalies = [AnomalyItem(**a) for a in anomaly_data["active"]]

    forecast_data = data["forecast"]
    daily_forecast = [ForecastDayPoint(**d) for d in forecast_data.get("daily", [])]

    return TenantDetailResponse(
        tenant_id=data["tenant_id"],
        generated_at=data["generated_at"],
        period_days=data["period_days"],
        cost=TenantCostDetail(
            period_days=cost_data["period_days"],
            total_calls=cost_data["total_calls"],
            successful_calls=cost_data["successful_calls"],
            error_calls=cost_data["error_calls"],
            blocked_calls=cost_data["blocked_calls"],
            total_tokens=cost_data["total_tokens"],
            total_cost_usd=cost_data["total_cost_usd"],
            avg_duration_ms=cost_data.get("avg_duration_ms"),
            daily_trend=daily_trend,
            model_breakdown=model_breakdown,
        ),
        anomalies=TenantAnomalyDetail(
            active=active_anomalies,
            total_active=anomaly_data["total_active"],
        ),
        forecast=TenantForecastDetail(
            available=forecast_data.get("available", False),
            trend_slope=forecast_data.get("trend_slope"),
            trend_slope_description=forecast_data.get("trend_slope_description"),
            monthly_likely_tokens=forecast_data.get("monthly_likely_tokens"),
            daily=daily_forecast,
        ),
        governance=TenantGovernanceSummary(**data["governance"]),
    )


@router.get(
    "/alerts",
    response_model=AlertFeedResponse,
    summary="Real-time alert feed — anomalies and budget risks",
)
async def alert_feed(
    anomaly_hours: int = Query(default=24, ge=1, le=168),
    budget_risk_days: int = Query(default=7, ge=1, le=30),
    service: DashboardService = Depends(get_dashboard_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> AlertFeedResponse:
    data = await service.get_alerts(
        hours=anomaly_hours,
        max_budget_risk_days=budget_risk_days,
    )

    alerts = [
        AlertItem(
            id=a["id"],
            alert_type=a["alert_type"],
            severity=a["severity"],
            tenant_id=a["tenant_id"],
            title=a["title"],
            description=a["description"],
            detected_at=a["detected_at"],
            action_url=a["action_url"],
        )
        for a in data["alerts"]
    ]

    return AlertFeedResponse(
        generated_at=data["generated_at"],
        alerts=alerts,
        total=data["total"],
        critical_count=data["critical_count"],
        warning_count=data["warning_count"],
    )


@router.get(
    "/health",
    response_model=SystemHealthResponse,
    summary="System health — all modules, database, Redis",
)
async def system_health(
    service: DashboardService = Depends(get_dashboard_service),
) -> SystemHealthResponse:
    data = await service.get_system_health()
    return SystemHealthResponse(
        status=data["status"],
        generated_at=data["generated_at"],
        modules=[ModuleHealth(**m) for m in data["modules"]],
        database=data["database"],
        redis=data["redis"],
    )
# ============================================================
# M8 — DASHBOARD ASSISTANT
# ============================================================

@router.get(
    "/assistant/ask",
    summary="M8 — Ask the AI assistant a question about costs, anomalies, or governance",
)
async def ask_assistant(
    question: str = Query(..., min_length=3, max_length=500),
    tenant_id: Optional[str] = Query(default=None),
    user_id: Optional[str] = Query(default=None),
    period_days: int = Query(default=30, ge=7, le=90),
    session_id: Optional[str] = Query(default=None),
    service: AssistantService = Depends(get_assistant_service),
    current_user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    # Tenant-scoped users can only query their own tenant
    if not current_user.is_super_admin() and tenant_id and tenant_id != current_user.tenant_id:
        tenant_id = current_user.tenant_id
    """
    Ask M8 a natural language question about your LLM platform.

    M8 will:
    1. Understand your intent (cost / anomaly / forecast / governance)
    2. Fetch relevant data from the database
    3. Call Groq/Llama (free) to generate an answer
    4. Return: plain-language answer + key figures + action links

    Example questions:
    - "Which tenant spends the most this month?"
    - "Why did costs go up this week?"
    - "Are there any critical anomalies?"
    - "Where can I reduce AI costs by 20%?"
    - "What is the projected budget for enterprise_corp?"
    - "How many calls were blocked by governance rules?"
    """
    return await service.ask(
        question=sanitize_text(question, max_length=500),
        tenant_id=tenant_id,
        user_id=user_id,
        period_days=period_days,
        session_id=session_id,
    )


@router.get(
    "/assistant/suggestions",
    summary="M8 — Get suggested questions for the assistant",
)
async def get_suggested_questions(
    tenant_id: Optional[str] = Query(
        default=None,
        description="If set, includes tenant-specific question suggestions.",
    ),
    service: AssistantService = Depends(get_assistant_service),
) -> dict:
    """
    Returns a list of suggested questions for the M8 assistant.

    Questions are contextual — if a tenant is selected, tenant-specific
    questions appear first.
    """
    suggestions = await service.get_suggestions(tenant_id=tenant_id)
    return {
        "suggestions": suggestions,
        "tenant_id": tenant_id,
        "count": len(suggestions),
    }