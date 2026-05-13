"""
Observability Router — System metrics, configuration, and audit logs.
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.config_registry import config
from core.observability import metrics
from modules.auth.dependencies import require_super_admin
from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["Observability"])


# ── Public metrics endpoint (no auth required) ──────────────

@router.get(
    "/metrics",
    summary="System health metrics (public)",
)
async def get_metrics() -> dict:
    """
    Returns system health metrics including:
    - Uptime and version
    - Request counts and error rates
    - Pipeline execution stats
    - Recent error log

    This endpoint requires no authentication for monitoring tools.
    """
    return metrics.get_health_summary()


@router.get(
    "/metrics/detailed",
    summary="Detailed metrics snapshot",
)
async def get_detailed_metrics() -> dict:
    """Returns the full metrics snapshot including timings and counters."""
    return metrics.get_snapshot()


@router.get(
    "/metrics/prometheus",
    summary="Prometheus text exposition format",
)
async def get_prometheus_metrics():
    """
    Returns metrics in Prometheus text exposition format.
    """
    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(
        content=metrics.prometheus_export(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ── Audit Log ───────────────────────────────────────────────

from fastapi import Query as FastQuery

@router.get(
    "/audit-log",
    summary="Query the admin audit log",
)
async def query_audit_log(
    action: str | None = FastQuery(default=None, description="Filter by action: create, update, delete, login"),
    resource_type: str | None = FastQuery(default=None, description="Filter by resource: governance_rule, user, config"),
    user_id: str | None = FastQuery(default=None, description="Filter by user ID"),
    tenant_id: str | None = FastQuery(default=None, description="Filter by tenant ID"),
    skip: int = FastQuery(default=0, ge=0, description="Pagination offset"),
    limit: int = FastQuery(default=50, ge=1, le=200, description="Max items to return"),
    session: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Query the admin audit log with optional filters.
    """
    from core.audit import audit
    entries = await audit.query(
        session=session,
        action=action,
        resource_type=resource_type,
        user_id=user_id,
        tenant_id=tenant_id,
        limit=limit,
        skip=skip,
    )
    return {
        "entries": entries,
        "total": len(entries),
        "filters": {
            "action": action,
            "resource_type": resource_type,
            "user_id": user_id,
            "tenant_id": tenant_id,
        },
    }


# ── System Configuration ─────────────────────────────────────

@router.get(
    "/system/config",
    summary="Current system configuration",
)
async def system_config() -> dict:
    """Returns all current configuration values."""
    return {
        "config": config.get_all(),
        "overrides": config.get_overrides(),
        "change_log": config.get_change_log()[-20:],
    }


class ConfigUpdateRequest(BaseModel):
    """Request body for config updates."""
    updates: dict[str, object]


@router.patch(
    "/system/config",
    summary="Update runtime configuration",
)
async def update_config(
    body: ConfigUpdateRequest,
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Update one or more configuration values at runtime.

    Values are validated against registered rules (type, range).
    Changes are logged in the audit trail.
    """
    results = {}
    errors = []

    for key, value in body.updates.items():
        r = config.set(key, value, source="api")
        results[key] = r
        if not r["success"]:
            errors.append(f"{key}: {r.get('error', 'unknown')}")

    return {
        "success": len(errors) == 0,
        "results": results,
        "errors": errors,
    }
