"""Detection module — FastAPI router."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.detection.schemas import (
    BaselineResponse,
    AnomalyResponse,
    AnomalyListResponse,
    DetectionCheckResponse,
)
from modules.detection.services import DetectionService
from modules.detection.services.explainer_service import ExplainerService
from modules.auth.dependencies import require_tenant_viewer, require_tenant_admin
from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/detection", tags=["Detection"])


async def get_detection_service(
    session: AsyncSession = Depends(get_db),
) -> DetectionService:
    return DetectionService(session)


@router.get(
    "/baseline/{tenant_id}",
    response_model=BaselineResponse,
    summary="Get statistical baseline for a tenant",
)
async def get_baseline(
    tenant_id: str,
    service: DetectionService = Depends(get_detection_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> BaselineResponse:
    user.require_tenant_access(tenant_id)
    baseline = await service.baseline_repo.get_baseline(tenant_id)
    if baseline is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"No baseline found for tenant '{tenant_id}'. "
                   f"Run baseline computation first."
        )
    return BaselineResponse.model_validate(baseline)


@router.post(
    "/baseline/{tenant_id}/compute",
    response_model=BaselineResponse,
    summary="Compute or refresh the baseline for a tenant",
)
async def compute_baseline(
    tenant_id: str,
    lookback_days: int = Query(default=30, ge=7, le=90),
    service: DetectionService = Depends(get_detection_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> BaselineResponse:
    user.require_tenant_access(tenant_id)
    success = await service.compute_baseline_for_tenant(
        tenant_id=tenant_id,
        lookback_days=lookback_days,
    )
    if not success:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient data to compute baseline for '{tenant_id}'. "
                   f"Need at least 7 days of usage data."
        )
    baseline = await service.baseline_repo.get_baseline(tenant_id)
    return BaselineResponse.model_validate(baseline)


@router.get(
    "/check/{tenant_id}",
    response_model=DetectionCheckResponse,
    summary="Run anomaly check for a tenant right now",
)
async def check_tenant(
    tenant_id: str,
    service: DetectionService = Depends(get_detection_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> DetectionCheckResponse:
    user.require_tenant_access(tenant_id)
    result = await service.check_entity_now(tenant_id=tenant_id)
    ensemble = result.ensemble
    return DetectionCheckResponse(
        tenant_id=result.tenant_id,
        fired=result.fired,
        severity=result.severity,
        anomaly_type=result.anomaly_type,
        vote_count=ensemble.vote_count if ensemble else 0,
        detector_names=ensemble.detector_names if ensemble else [],
        stl_z=ensemble.stl_residual_zscore if ensemble else None,
        isolation_score=ensemble.isolation_score if ensemble else None,
        cusum_value=ensemble.cusum_value if ensemble else None,
        anomaly_id=str(result.anomaly_id) if result.anomaly_id else None,
    )


@router.patch(
    "/anomalies/{anomaly_id}/resolve",
    response_model=dict,
    summary="Mark an anomaly as resolved",
)
async def resolve_anomaly(
    anomaly_id: str,
    service: DetectionService = Depends(get_detection_service),
    _user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    try:
        aid = uuid.UUID(anomaly_id)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid anomaly_id format")

    success = await service.anomaly_repo.resolve(aid)
    if not success:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Anomaly not found")

    await service.session.commit()
    return {"status": "ok", "anomaly_id": anomaly_id}


@router.get(
    "/anomalies",
    response_model=AnomalyListResponse,
    summary="Get all recent anomalies across all tenants",
)
async def get_all_anomalies(
    hours: int = Query(default=24, ge=1, le=168),
    severity: Optional[str] = Query(default=None),
    skip: int = Query(default=0, ge=0, description="Number of items to skip (pagination offset)"),
    limit: int = Query(default=50, ge=1, le=200, description="Max items to return"),
    service: DetectionService = Depends(get_detection_service),
    _user: CurrentUser = Depends(require_tenant_viewer),
) -> AnomalyListResponse:
    anomalies = await service.get_all_recent_anomalies(
        hours=hours, severity=severity
    )
    total = len(anomalies)
    paginated = anomalies[skip:skip + limit]
    return AnomalyListResponse(
        anomalies=[AnomalyResponse.model_validate(a) for a in paginated],
        total=total,
    )


@router.get(
    "/anomalies/{tenant_id}",
    response_model=AnomalyListResponse,
    summary="Get anomaly history for a tenant",
)
async def get_anomalies(
    tenant_id: str,
    include_resolved: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    service: DetectionService = Depends(get_detection_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> AnomalyListResponse:
    user.require_tenant_access(tenant_id)
    anomalies = await service.get_tenant_anomalies(
        tenant_id=tenant_id,
        include_resolved=include_resolved,
        limit=limit,
    )
    return AnomalyListResponse(
        tenant_id=tenant_id,
        anomalies=[AnomalyResponse.model_validate(a) for a in anomalies],
        total=len(anomalies),
    )
# ============================================================
# M4 EXPLAINER ENDPOINTS
# ============================================================

@router.get(
    "/explain/{anomaly_id}",
    summary="Get M4 explanation for an anomaly",
)
async def get_explanation(
    anomaly_id: str,
    service: DetectionService = Depends(get_detection_service),
) -> dict:
    """
    Return the stored M4 explanation and recommendations for an anomaly.

    If no explanation exists yet, triggers generation on demand.
    """
    try:
        aid = uuid.UUID(anomaly_id)
    except ValueError:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid anomaly_id format")

    explainer = ExplainerService(service.session)

    result = await explainer.get_explanation_with_recommendations(aid)

    if result is None:
        # Try to generate on-demand — direct lookup by ID
        from sqlalchemy import select
        from modules.detection.models import LLMAnomalyRecord as AnomalyModel
        db_result = await service.session.execute(
            select(AnomalyModel).where(AnomalyModel.id == aid)
        )
        anomaly = db_result.scalar_one_or_none()

        if anomaly is None:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=404,
                detail=f"Anomaly {anomaly_id} not found"
            )

        explanation = await explainer.explain_anomaly(anomaly)
        if explanation is None:
            return {"available": False, "reason": "Anomaly severity below M4 threshold"}

        result = await explainer.get_explanation_with_recommendations(aid)

    exp = result["explanation"]
    recs = result["recommendations"]

    return {
        "anomaly_id": anomaly_id,
        "explanation": {
            "id": str(exp.id),
            "explanation_text": exp.explanation_text,
            "llm_model_used": exp.llm_model_used,
            "tokens_used": exp.tokens_used,
            "confidence_level": exp.confidence_level,
            "cache_hit": exp.cache_hit,
            "generated_at": exp.generated_at.isoformat(),
        },
        "recommendations": [
            {
                "id": str(r.id),
                "action_type": r.action_type,
                "action_title": r.action_title,
                "action_description": r.action_description,
                "expected_token_saving": r.expected_token_saving,
                "expected_cost_saving_usd": str(r.expected_cost_saving_usd),
                "risk_level": r.risk_level,
                "priority": r.priority,
                "status": r.status,
            }
            for r in recs
        ],
    }


@router.get(
    "/explanations/{tenant_id}",
    summary="Get recent M4 explanations for a tenant",
)
async def get_tenant_explanations(
    tenant_id: str,
    limit: int = Query(default=10, ge=1, le=50),
    service: DetectionService = Depends(get_detection_service),
) -> dict:
    """Return recent M4 explanations for a tenant."""
    explainer = ExplainerService(service.session)
    explanations = await explainer.get_tenant_explanations(tenant_id, limit)
    return {
        "tenant_id": tenant_id,
        "explanations": [
            {
                "id": str(e.id),
                "anomaly_id": str(e.anomaly_id),
                "anomaly_type": e.anomaly_type,
                "severity": e.severity,
                "explanation_text": e.explanation_text,
                "tokens_used": e.tokens_used,
                "cache_hit": e.cache_hit,
                "generated_at": e.generated_at.isoformat(),
            }
            for e in explanations
        ],
        "total": len(explanations),
    }