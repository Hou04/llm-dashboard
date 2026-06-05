"""Forecasting module — FastAPI router."""

import logging
from datetime import date, timedelta
from typing import Optional
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.forecasting.schemas import (
    ForecastResponse,
    ForecastSummary,
    DailyForecastPoint,
    BudgetRiskResponse,
    BudgetRiskItem,
)
from modules.forecasting.services import ForecastingService
from modules.forecasting.services.optimizer_service import OptimizerService
from modules.auth.dependencies import require_tenant_viewer, require_tenant_admin, require_super_admin
from modules.auth.schemas import CurrentUser
import uuid as uuid_lib

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/forecasting", tags=["Forecasting"])


async def get_forecasting_service(
    session: AsyncSession = Depends(get_db),
) -> ForecastingService:
    return ForecastingService(session)


async def get_optimizer_service(
    session: AsyncSession = Depends(get_db),
) -> OptimizerService:
    return OptimizerService(session)


@router.post(
    "/forecast/{tenant_id}",
    response_model=ForecastResponse,
    summary="Generate a 30-day forecast for a tenant",
)
async def generate_forecast(
    tenant_id: str,
    horizon_days: int = Query(default=30, ge=7, le=60),
    lookback_days: int = Query(default=60, ge=21, le=90),
    service: ForecastingService = Depends(get_forecasting_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> ForecastResponse:
    user.require_tenant_access(tenant_id)
    result = await service.generate_forecast_for_tenant(
        tenant_id=tenant_id,
        horizon_days=horizon_days,
        lookback_days=lookback_days,
    )

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient data to forecast for '{tenant_id}'. "
                   f"Need at least 21 days of usage history.",
        )

    # Build summary
    slope_sign = "+" if result.trend_slope >= 0 else ""
    summary = ForecastSummary(
        tenant_id=tenant_id,
        horizon_days=horizon_days,
        trend_slope=result.trend_slope,
        trend_slope_description=(
            f"{slope_sign}{int(result.trend_slope):,} tokens/day growth"
        ),
        monthly_likely_tokens=result.monthly_likely_tokens,
        monthly_pessimistic_tokens=result.monthly_pessimistic_tokens,
        monthly_optimistic_tokens=result.monthly_optimistic_tokens,
        monthly_likely_cost_usd=str(round(
            result.monthly_likely_tokens * result.avg_cost_per_token, 4
        )),
        avg_cost_per_token=result.avg_cost_per_token,
        history_days_used=result.history_days_used,
    )

    # Build daily points (all three scenarios merged)
    daily_map: dict[date, dict] = {}
    for d in result.daily_forecasts:
        daily_map[d.forecast_date] = {
            "forecast_date": d.forecast_date,
            "horizon_days": d.horizon_days,
            "likely_tokens": d.likely_tokens,
            "pessimistic_tokens": d.pessimistic_tokens,
            "optimistic_tokens": d.optimistic_tokens,
            "predicted_cost_usd": str(round(
                d.likely_tokens * result.avg_cost_per_token, 8
            )),
            "trend_value": d.trend_value,
            "seasonal_multiplier": d.seasonal_multiplier,
            "confidence_lower": d.likely_tokens - d.confidence_half_width,
            "confidence_upper": d.likely_tokens + d.confidence_half_width,
        }

    daily_points = [DailyForecastPoint(**v) for v in daily_map.values()]

    return ForecastResponse(
        tenant_id=tenant_id,
        summary=summary,
        daily=daily_points,
        total_days=len(daily_points),
    )


@router.get(
    "/forecast/{tenant_id}",
    response_model=ForecastResponse,
    summary="Get the latest stored forecast for a tenant",
)
async def get_forecast(
    tenant_id: str,
    service: ForecastingService = Depends(get_forecasting_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> ForecastResponse:
    user.require_tenant_access(tenant_id)
    rows = await service.get_all_scenarios(tenant_id)

    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No forecast found for '{tenant_id}'. "
                   f"Call POST /v1/forecasting/forecast/{tenant_id} first.",
        )

    # Group by date for all three scenarios
    date_map: dict[date, dict] = {}
    for row in rows:
        fd = row.forecast_date
        if fd not in date_map:
            date_map[fd] = {
                "forecast_date": fd,
                "horizon_days": row.horizon_days,
                "likely_tokens": 0.0,
                "pessimistic_tokens": 0.0,
                "optimistic_tokens": 0.0,
                "predicted_cost_usd": "0",
                "trend_value": row.trend_value,
                "seasonal_multiplier": row.seasonal_multiplier,
                "confidence_lower": row.confidence_lower,
                "confidence_upper": row.confidence_upper,
            }
        if row.scenario == "likely":
            date_map[fd]["likely_tokens"] = row.predicted_tokens
            date_map[fd]["predicted_cost_usd"] = str(row.predicted_cost_usd)
        elif row.scenario == "pessimistic":
            date_map[fd]["pessimistic_tokens"] = row.predicted_tokens
        elif row.scenario == "optimistic":
            date_map[fd]["optimistic_tokens"] = row.predicted_tokens

    daily = sorted(
        [DailyForecastPoint(**v) for v in date_map.values()],
        key=lambda x: x.forecast_date,
    )

    return ForecastResponse(
        tenant_id=tenant_id,
        summary=None,  # not stored, only returned on generate
        daily=daily,
        total_days=len(daily),
    )


@router.get(
    "/budget-risk",
    response_model=BudgetRiskResponse,
    summary="Get all tenants at risk of hitting budget limits",
)
async def get_budget_risks(
    max_days: int = Query(default=30, ge=1, le=60),
    service: ForecastingService = Depends(get_forecasting_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> BudgetRiskResponse:
    risks = await service.get_budget_risks(max_days=max_days)



    def urgency(days: int) -> str:
        if days <= 7:
            return "critical"
        elif days <= 14:
            return "warning"
        return "watch"

    items = [
        BudgetRiskItem(
            tenant_id=r.tenant_id,
            risk_type=r.risk_type,
            days_until_exhaustion=r.days_until_exhaustion,
            exhaustion_date=r.exhaustion_date,
            forecasted_value_at_exhaustion=r.forecasted_value_at_exhaustion,
            governance_limit=r.governance_limit,
            pct_of_limit_today=r.pct_of_limit_today,
            urgency=urgency(r.days_until_exhaustion),
        )
        for r in risks
    ]

    return BudgetRiskResponse(
        risks=items,
        total=len(items),
        critical_count=sum(1 for i in items if i.urgency == "critical"),
        warning_count=sum(1 for i in items if i.urgency == "warning"),
    )


# ============================================================
# M5 OPTIMIZER ENDPOINTS
# ============================================================


@router.post(
    "/optimize/{tenant_id}",
    summary="Run M5 optimization analysis for a tenant",
)
async def run_optimization(
    tenant_id: str,
    lookback_days: int = Query(default=30, ge=7, le=90),
    service: OptimizerService = Depends(get_optimizer_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> dict:
    user.require_tenant_access(tenant_id)
    """
    Analyze all agents for a tenant and generate:
    - Model substitution recommendations with ROI
    - Prompt structure optimization suggestions

    Results are stored and retrievable via GET endpoints.
    """
    result = await service.run_optimization_for_tenant(
        tenant_id=tenant_id,
        lookback_days=lookback_days,
    )
    return {
        "tenant_id": result["tenant_id"],
        "agents_analyzed": result["agents_analyzed"],
        "model_recommendations_count": len(result["model_recommendations"]),
        "prompt_optimizations_count": len(result["prompt_optimizations"]),
        "total_projected_monthly_saving_usd": result["total_projected_monthly_saving_usd"],
        "model_recommendations": [
            {
                "id": str(r.id),
                "agent_id": r.agent_id,
                "current_model": r.current_model,
                "recommended_model": r.recommended_model,
                "justification_type": r.justification_type,
                "justification_text": r.justification_text,
                "expected_monthly_saving_usd": str(r.expected_monthly_saving_usd),
                "expected_saving_pct": r.expected_saving_pct,
                "confidence": r.confidence,
                "status": r.status,
            }
            for r in result["model_recommendations"]
        ],
        "prompt_optimizations": [
            {
                "id": str(o.id),
                "agent_id": o.agent_id,
                "optimization_type": o.optimization_type,
                "recommendation_title": o.recommendation_title,
                "expected_token_saving_monthly": o.expected_token_saving_monthly,
                "expected_cost_saving_monthly_usd": str(o.expected_cost_saving_monthly_usd),
                "priority": o.priority,
                "status": o.status,
            }
            for o in result["prompt_optimizations"]
        ],
    }


@router.get(
    "/optimize/{tenant_id}/models",
    summary="Get model recommendations for a tenant",
)
async def get_model_recommendations(
    tenant_id: str,
    service: OptimizerService = Depends(get_optimizer_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    user.require_tenant_access(tenant_id)
    recs = await service.get_model_recommendations(tenant_id)
    return {
        "tenant_id": tenant_id,
        "recommendations": [
            {
                "id": str(r.id),
                "agent_id": r.agent_id,
                "current_model": r.current_model,
                "recommended_model": r.recommended_model,
                "justification_type": r.justification_type,
                "justification_text": r.justification_text,
                "current_monthly_cost_usd": str(r.current_monthly_cost_usd),
                "projected_monthly_cost_usd": str(r.projected_monthly_cost_usd),
                "expected_monthly_saving_usd": str(r.expected_monthly_saving_usd),
                "expected_saving_pct": r.expected_saving_pct,
                "confidence": r.confidence,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in recs
        ],
        "total": len(recs),
        "total_monthly_saving_usd": str(round(
            sum(float(r.expected_monthly_saving_usd) for r in recs), 4
        )),
    }


@router.get(
    "/optimize/all/models",
    summary="Get all model recommendations across all tenants",
)
async def get_all_model_recommendations(
    status: Optional[str] = Query(default=None),
    service: OptimizerService = Depends(get_optimizer_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    recs = await service.get_all_model_recommendations(status=status)
    return {
        "recommendations": [
            {
                "id": str(r.id),
                "tenant_id": r.tenant_id,
                "agent_id": r.agent_id,
                "current_model": r.current_model,
                "recommended_model": r.recommended_model,
                "expected_monthly_saving_usd": str(r.expected_monthly_saving_usd),
                "expected_saving_pct": r.expected_saving_pct,
                "confidence": r.confidence,
                "status": r.status,
            }
            for r in recs
        ],
        "total": len(recs),
        "total_monthly_saving_usd": str(round(
            sum(float(r.expected_monthly_saving_usd) for r in recs), 4
        )),
    }


@router.get(
    "/optimize/all/prompts",
    summary="Get all prompt recommendations across all tenants",
)
async def get_all_prompt_recommendations(
    status: Optional[str] = Query(default=None),
    service: OptimizerService = Depends(get_optimizer_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    from sqlalchemy import select
    from modules.forecasting.models_optimizer import LLMPromptOptimization
    query = select(LLMPromptOptimization)
    if status:
        query = query.where(LLMPromptOptimization.status == status)
    result = await service.session.execute(query)
    opts = result.scalars().all()
    return {
        "optimizations": [
            {
                "id": str(o.id),
                "tenant_id": o.tenant_id,
                "agent_id": o.agent_id,
                "optimization_type": o.optimization_type,
                "recommendation_title": o.recommendation_title,
                "expected_token_saving_monthly": o.expected_token_saving_monthly,
                "expected_cost_saving_monthly_usd": str(o.expected_cost_saving_monthly_usd),
                "priority": o.priority,
                "status": o.status,
            }
            for o in opts
        ],
        "total": len(opts),
    }

@router.patch(
    "/optimize/recommendation/{recommendation_id}",
    summary="Update recommendation status (validated/deployed/rejected)",
)
async def update_recommendation_status(
    recommendation_id: str,
    rec_type: str = Query(..., description="'prompt' or 'model'"),
    status: str = Query(..., description="proposed/validated/deployed/rejected"),
    service: OptimizerService = Depends(get_optimizer_service),
    _user: CurrentUser = Depends(require_tenant_admin),
) -> dict:
    try:
        rid = uuid_lib.UUID(recommendation_id)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid recommendation_id")

    success = await service.update_status(rid, rec_type, status)
    if not success:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Recommendation not found")

    return {"updated": True, "id": recommendation_id, "new_status": status}
@router.post(
    "/optimize/{tenant_id}/rewrite-prompt",
    summary="Use LLM to generate an optimized prompt structure for an agent",
)
async def rewrite_agent_prompt(
    tenant_id: str,
    agent_id: str = Query(..., description="Agent ID to optimize"),
    prompt_description: str = Query(
        ...,
        description="Plain description of the prompt structure (NOT the actual prompt)"
    ),
    avg_input_tokens: int = Query(
        default=500, ge=100, le=10000,
        description="Current average input token count for this agent"
    ),
    use_case: str = Query(
        default="general",
        description="What the agent does: summarization, classification, generation..."
    ),
    service: OptimizerService = Depends(get_optimizer_service),
) -> dict:
    """
    M5 Step 4 — LLM-assisted prompt optimization.

    Calls a lightweight LLM (Groq/Llama by default — free) to analyze
    a verbose agent prompt's structure and generate specific rewrite
    suggestions with an estimated token reduction.

    PRIVACY: The actual prompt is never sent or stored.
    Only the structural description and token statistics are used.
    """
    result = await service.generate_optimized_prompt(
        tenant_id=tenant_id,
        agent_id=agent_id,
        original_prompt_description=prompt_description,
        avg_input_tokens=avg_input_tokens,
        use_case=use_case,
    )
    return {
        "tenant_id": tenant_id,
        **result,
    }