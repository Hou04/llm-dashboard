"""
Pipeline Router — API endpoints for data ingestion and pipeline orchestration.

Replaces manual scripts (import_csv_data.py, backfill_costs.py, run_pipeline.py)
with API-driven pipeline execution.

Endpoints:
  POST /v1/pipeline/ingest          — ingest CSV data from datasets/ directory
  POST /v1/pipeline/run-all         — run M2→M3 pipeline for all tenants
  POST /v1/pipeline/generate-anomalies — generate/enrich anomaly data
  GET  /v1/pipeline/status          — pipeline status & metrics
  GET  /v1/pipeline/config          — current configuration
  PATCH /v1/pipeline/config         — update runtime configuration
  GET  /v1/metrics                  — public health metrics (no auth)
  GET  /v1/pipeline/datasets        — discover available dataset files
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query as FastQuery
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.config_registry import config
from core.observability import metrics
from modules.auth.dependencies import require_super_admin
from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/pipeline", tags=["Pipeline"])


# ── Public metrics endpoint (no auth required) ──────────────

metrics_router = APIRouter(prefix="/v1", tags=["Observability"])


@metrics_router.get(
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


@metrics_router.get(
    "/metrics/detailed",
    summary="Detailed metrics snapshot",
)
async def get_detailed_metrics() -> dict:
    """Returns the full metrics snapshot including timings and counters."""
    return metrics.get_snapshot()


@metrics_router.get(
    "/metrics/prometheus",
    summary="Prometheus text exposition format",
)
async def get_prometheus_metrics():
    """
    Returns metrics in Prometheus text exposition format.

    Configure Prometheus to scrape this endpoint:
    ```yaml
    scrape_configs:
      - job_name: 'llm-dashboard'
        metrics_path: '/v1/metrics/prometheus'
        static_configs:
          - targets: ['api:8000']
    ```
    """
    from starlette.responses import Response as StarletteResponse
    return StarletteResponse(
        content=metrics.prometheus_export(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@metrics_router.get(
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

    Returns chronologically ordered entries (newest first).
    Each entry records who did what, when, and the details.
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


# ── Pipeline endpoints (auth required) ──────────────────────

@router.post(
    "/ingest",
    summary="Ingest CSV data from datasets/ directory",
)
async def ingest_datasets(
    session: AsyncSession = Depends(get_db),
    date_from: str | None = FastQuery(default=None, description="Start date filter (YYYY-MM-DD)"),
    date_to: str | None = FastQuery(default=None, description="End date filter (YYYY-MM-DD)"),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Ingest CSV files from the datasets/ directory.

    Supports two layouts:
    1. **Partitioned** (preferred): datasets/raw/YYYY-MM/YYYY-MM-DD.csv
    2. **Legacy**: datasets/llm_token_log_6months.csv (single file)

    Optional date range filtering via query params:
    - ?date_from=2026-01-01&date_to=2026-01-31

    This endpoint validates the gateway mode — CSV imports are
    rejected if GATEWAY_MODE=real.
    """
    # Validate gateway mode
    from modules.gateway.services.mode_router import GatewayModeRouter
    if not GatewayModeRouter.validate_request_source("csv"):
        raise HTTPException(
            status_code=403,
            detail="Gateway is in real mode. CSV simulation imports disabled.",
        )

    from modules.pipeline.ingestion_service import IngestionService
    service = IngestionService(session)
    results = {}

    # Strategy 1: Check for partitioned files in datasets/raw/
    raw_dir = Path("datasets/raw")
    if raw_dir.exists() and any(raw_dir.iterdir()):
        csv_files = sorted(raw_dir.rglob("*.csv"))

        # Apply date range filter (file stem is YYYY-MM-DD)
        if date_from:
            csv_files = [f for f in csv_files if f.stem >= date_from]
        if date_to:
            csv_files = [f for f in csv_files if f.stem <= date_to]

        if not csv_files:
            return {
                "success": False,
                "error": "No CSV files match the date range",
                "date_from": date_from,
                "date_to": date_to,
            }

        for csv_file in csv_files:
            results[csv_file.name] = await service.ingest_csv_file(str(csv_file))

        return {
            "success": True,
            "mode": "partitioned",
            "results": results,
            "files_processed": len(csv_files),
            "gateway_mode": GatewayModeRouter.get_status(),
        }

    # Strategy 2: Fallback to legacy single-file layout
    datasets_dir = Path("datasets")
    if not datasets_dir.exists():
        return {"success": False, "error": "datasets/ directory not found"}

    csv_files = sorted(datasets_dir.glob("*.csv"))
    if not csv_files:
        return {"success": False, "error": "No CSV files found in datasets/"}

    # Identify the main token log file
    token_log_candidates = [
        f for f in csv_files if "token" in f.stem.lower() and "anomal" not in f.stem.lower()
    ]
    if not token_log_candidates:
        token_log_candidates = sorted(csv_files, key=lambda f: f.stat().st_size, reverse=True)

    for csv_file in token_log_candidates[:1]:
        results["token_log"] = await service.ingest_csv_file(str(csv_file))

    return {
        "success": True,
        "mode": "legacy",
        "results": results,
        "discovered_files": [f.name for f in csv_files],
        "gateway_mode": GatewayModeRouter.get_status(),
    }


@router.post(
    "/run-all",
    summary="Run full M2→M3 pipeline for all tenants",
)
async def run_full_pipeline(
    session: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Execute the full pipeline:
    1. M2: Cost aggregation (daily + monthly rollup)
    2. M3: Anomaly detection/generation
    """
    from modules.pipeline.pipeline_orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(session)
    result = await orchestrator.run_full_pipeline()
    return result


@router.post(
    "/generate-anomalies",
    summary="Generate/enrich anomaly data from token logs",
)
async def generate_anomalies(
    session: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Analyze token log data and generate anomaly records.

    If insufficient natural anomalies exist, creates
    realistic synthetic anomalies to ensure M3 module
    has meaningful data to display.
    """
    from modules.pipeline.anomaly_generator import AnomalyGenerator

    generator = AnomalyGenerator(session)
    result = await generator.generate_anomalies()
    return result


@router.get(
    "/status",
    summary="Pipeline status & metrics",
)
async def pipeline_status() -> dict:
    """
    Returns pipeline health metrics including:
    - Ingestion counts
    - Processing timings
    - Error counts
    - Last run timestamps
    """
    return {
        "metrics": metrics.get_snapshot(),
        "config_summary": {
            "auto_run_on_startup": config.get("pipeline.auto_run_on_startup"),
            "auto_ingest": config.get("pipeline.auto_ingest_datasets"),
            "auto_anomalies": config.get("ingestion.auto_generate_anomalies"),
        },
    }


@router.get(
    "/config",
    summary="Current pipeline configuration",
)
async def pipeline_config() -> dict:
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
    "/config",
    summary="Update runtime configuration",
)
async def update_config(
    body: ConfigUpdateRequest,
) -> dict:
    """
    Update one or more configuration values at runtime.

    Values are validated against registered rules (type, range).
    Changes are logged in the audit trail.

    Example:
    ```json
    {"updates": {"anomaly.z_score_threshold": 2.5, "ingestion.batch_size": 1000}}
    ```
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


@router.get(
    "/datasets",
    summary="Discover available dataset files",
)
async def discover_datasets() -> dict:
    """
    Scan the datasets/ directory and return information about available files.
    """
    datasets_dir = Path("datasets")
    if not datasets_dir.exists():
        return {"datasets": [], "directory": "datasets/", "exists": False}

    files = []
    for f in sorted(datasets_dir.glob("*.csv")):
        stat = f.stat()
        files.append({
            "name": f.name,
            "size_bytes": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "modified": stat.st_mtime,
        })

    return {
        "datasets": files,
        "directory": str(datasets_dir.absolute()),
        "total_files": len(files),
        "exists": True,
    }


def _format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
