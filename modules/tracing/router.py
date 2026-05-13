"""
Session Tracing, A/B Testing & Alert Config — FastAPI router.

Feature 8:  /v1/tracing/sessions/*
Feature 9:  /v1/tracing/experiments/*
Feature 10: /v1/tracing/alerts/*
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.auth.dependencies import require_tenant_admin, require_tenant_viewer
from modules.auth.schemas import CurrentUser
from modules.tracing.schemas import (
    SessionCreateRequest, SessionResponse, SessionListResponse, SessionWaterfallResponse, SessionCallEntry,
    ExperimentCreateRequest, ExperimentResponse, ExperimentListResponse, ExperimentResultsResponse,
    AlertConfigCreateRequest, AlertConfigResponse, AlertConfigListResponse,
    AlertHistoryEntry, AlertHistoryResponse, AlertTestResponse,
)
from modules.tracing.service import SessionService, ExperimentService, AlertConfigService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/tracing", tags=["Tracing & Experiments"])


# ============================================================
# DEPENDENCIES
# ============================================================

async def get_session_service(session: AsyncSession = Depends(get_db)) -> SessionService:
    return SessionService(session)

async def get_experiment_service(session: AsyncSession = Depends(get_db)) -> ExperimentService:
    return ExperimentService(session)

async def get_alert_service(session: AsyncSession = Depends(get_db)) -> AlertConfigService:
    return AlertConfigService(session)


# ============================================================
# FEATURE 8: SESSION TRACING
# ============================================================

@router.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create or resume a session",
)
async def create_session(
    request: SessionCreateRequest,
    user: CurrentUser = Depends(require_tenant_viewer),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    tenant_id = request.tenant_id or user.tenant_id
    if not tenant_id:
        raise HTTPException(400, "tenant_id required")

    sess = await service.get_or_create_session(
        session_id=request.session_id,
        tenant_id=tenant_id,
        user_id=request.user_id,
        agent_id=request.agent_id,
        name=request.name,
        metadata=request.metadata,
        tags=request.tags,
    )
    await service.db.commit()
    return SessionResponse.model_validate(sess)


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    summary="List sessions with filters",
)
async def list_sessions(
    tenant_id: Optional[str] = Query(None),
    agent_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: SessionService = Depends(get_session_service),
) -> SessionListResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    sessions, total = await service.list_sessions(
        tenant_id=effective_tenant, agent_id=agent_id,
        status=status_filter, page=page, page_size=page_size,
    )
    return SessionListResponse(
        sessions=[SessionResponse.model_validate(s) for s in sessions],
        total=total, page=page, page_size=page_size,
    )


@router.get(
    "/sessions/{session_id}",
    response_model=SessionResponse,
    summary="Get session details",
)
async def get_session(
    session_id: str,
    user: CurrentUser = Depends(require_tenant_viewer),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    sess = await service.get_session(session_id)
    if sess is None:
        raise HTTPException(404, f"Session {session_id} not found")
    if not user.is_super_admin() and sess.tenant_id != user.tenant_id:
        raise HTTPException(403, "Access denied")
    return SessionResponse.model_validate(sess)


@router.get(
    "/sessions/{session_id}/waterfall",
    response_model=SessionWaterfallResponse,
    summary="Waterfall view — each step in the agent chain with latency and cost",
)
async def get_session_waterfall(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: SessionService = Depends(get_session_service),
) -> SessionWaterfallResponse:
    sess = await service.get_session(session_id)
    if sess is None:
        raise HTTPException(404, f"Session {session_id} not found")
    if not user.is_super_admin() and sess.tenant_id != user.tenant_id:
        raise HTTPException(403, "Access denied")

    # Fetch the actual log entries from the call chain
    from modules.gateway.models import LLMTokenLog
    from sqlalchemy import select

    calls = []
    for step, log_id in enumerate(sess.call_chain or [], start=1):
        result = await db.execute(select(LLMTokenLog).where(LLMTokenLog.id == log_id))
        log = result.scalar_one_or_none()
        if log:
            calls.append(SessionCallEntry(
                log_id=str(log.id),
                step=step,
                model=log.model,
                provider=log.provider,
                input_tokens=log.input_tokens,
                output_tokens=log.output_tokens,
                cost_usd=str(log.cost_usd),
                duration_ms=log.duration_ms,
                status=log.status,
                prompt_preview=(log.prompt_text[:100] + "...") if log.prompt_text and len(log.prompt_text) > 100 else log.prompt_text,
                created_at=log.created_at,
            ))

    return SessionWaterfallResponse(
        session=SessionResponse.model_validate(sess),
        calls=calls,
        total_cost_usd=sess.total_cost_usd,
        total_duration_ms=sess.total_duration_ms,
    )


@router.post(
    "/sessions/{session_id}/complete",
    response_model=SessionResponse,
    summary="Mark a session as completed",
)
async def complete_session(
    session_id: str,
    status_value: str = Query("completed", alias="status"),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    sess = await service.complete_session(session_id, status_value)
    if sess is None:
        raise HTTPException(404, f"Session {session_id} not found")
    return SessionResponse.model_validate(sess)


# ============================================================
# FEATURE 9: A/B EXPERIMENTS
# ============================================================

@router.post(
    "/experiments",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new A/B experiment",
)
async def create_experiment(
    request: ExperimentCreateRequest,
    user: CurrentUser = Depends(require_tenant_admin),
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentResponse:
    tenant_id = request.tenant_id or user.tenant_id
    if not tenant_id:
        raise HTTPException(400, "tenant_id required")

    exp = await service.create_experiment(
        tenant_id=tenant_id,
        name=request.name,
        prompt_name=request.prompt_name,
        variant_a_version=request.variant_a_version,
        variant_b_version=request.variant_b_version,
        created_by=user.id,
        traffic_split=request.traffic_split,
        primary_metric=request.primary_metric,
        min_samples=request.min_samples,
        description=request.description,
    )
    return ExperimentResponse.model_validate(exp)


@router.get(
    "/experiments",
    response_model=ExperimentListResponse,
    summary="List experiments",
)
async def list_experiments(
    tenant_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentListResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    experiments, total = await service.list_experiments(effective_tenant, status_filter)
    return ExperimentListResponse(
        experiments=[ExperimentResponse.model_validate(e) for e in experiments],
        total=total,
    )


@router.post(
    "/experiments/{experiment_id}/start",
    response_model=ExperimentResponse,
    summary="Start an experiment — begin routing traffic",
)
async def start_experiment(
    experiment_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentResponse:
    exp = await service.start_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    return ExperimentResponse.model_validate(exp)


@router.post(
    "/experiments/{experiment_id}/pause",
    response_model=ExperimentResponse,
    summary="Pause an experiment — stop routing traffic",
)
async def pause_experiment(
    experiment_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentResponse:
    exp = await service.pause_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    return ExperimentResponse.model_validate(exp)


@router.post(
    "/experiments/{experiment_id}/complete",
    response_model=ExperimentResponse,
    summary="Complete an experiment manually",
)
async def complete_experiment(
    experiment_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentResponse:
    exp = await service.complete_experiment(experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")
    return ExperimentResponse.model_validate(exp)


@router.get(
    "/experiments/{experiment_id}/results",
    response_model=ExperimentResultsResponse,
    summary="Get detailed A vs B comparison with statistical analysis",
)
async def get_experiment_results(
    experiment_id: str,
    user: CurrentUser = Depends(require_tenant_viewer),
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentResultsResponse:
    exp = await service._get(experiment_id)
    if exp is None:
        raise HTTPException(404, "Experiment not found")

    results = await service.get_results(experiment_id)
    if results is None:
        raise HTTPException(404, "Experiment not found")

    return ExperimentResultsResponse(
        experiment=ExperimentResponse.model_validate(exp),
        **results,
    )


# ============================================================
# FEATURE 10: ALERT CONFIGURATION
# ============================================================

@router.post(
    "/alerts/configs",
    response_model=AlertConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an alert webhook channel",
)
async def create_alert_config(
    request: AlertConfigCreateRequest,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AlertConfigService = Depends(get_alert_service),
) -> AlertConfigResponse:
    tenant_id = request.tenant_id or user.tenant_id
    if not tenant_id:
        raise HTTPException(400, "tenant_id required")

    config = await service.create_config(
        tenant_id=tenant_id,
        name=request.name,
        channel_type=request.channel_type,
        webhook_url=request.webhook_url,
        created_by=user.id,
        alert_types=request.alert_types,
        severity_filter=request.severity_filter,
        is_active=request.is_active,
    )
    return AlertConfigResponse.model_validate(config)


@router.get(
    "/alerts/configs",
    response_model=AlertConfigListResponse,
    summary="List alert channels for a tenant",
)
async def list_alert_configs(
    tenant_id: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: AlertConfigService = Depends(get_alert_service),
) -> AlertConfigListResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    configs = await service.list_configs(effective_tenant)
    return AlertConfigListResponse(
        configs=[AlertConfigResponse.model_validate(c) for c in configs],
        total=len(configs),
    )


@router.delete(
    "/alerts/configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an alert channel",
)
async def delete_alert_config(
    config_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AlertConfigService = Depends(get_alert_service),
) -> None:
    ok = await service.delete_config(config_id)
    if not ok:
        raise HTTPException(404, "Alert config not found")


@router.post(
    "/alerts/configs/{config_id}/test",
    response_model=AlertTestResponse,
    summary="Test webhook connectivity — sends a ping",
)
async def test_alert_config(
    config_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AlertConfigService = Depends(get_alert_service),
) -> AlertTestResponse:
    result = await service.test_webhook(config_id)
    return AlertTestResponse(**result)


@router.get(
    "/alerts/history",
    response_model=AlertHistoryResponse,
    summary="View dispatched alert history",
)
async def get_alert_history(
    tenant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(require_tenant_viewer),
    service: AlertConfigService = Depends(get_alert_service),
) -> AlertHistoryResponse:
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    alerts, total = await service.get_alert_history(effective_tenant, page, page_size)
    return AlertHistoryResponse(
        alerts=[AlertHistoryEntry.model_validate(a) for a in alerts],
        total=total, page=page, page_size=page_size,
    )
