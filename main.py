"""
LLM Dashboard — FastAPI application entry point.

This file creates the FastAPI app and registers all module routers.
It also configures startup/shutdown lifecycle events.

Each module contributes its own router with its own URL prefix.
main.py just assembles them — it contains no business logic.

Running:
    pipenv run uvicorn main:app --reload --port 8000

API docs (auto-generated):
    http://localhost:8000/docs      ← Swagger UI
    http://localhost:8000/redoc     ← ReDoc
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.staticfiles import StaticFiles
import os

from core.settings import settings
from core.database import init_db
from core.redis import init_redis
from modules.gateway.router import router as gateway_router
from modules.analytics.router import router as analytics_router
from modules.billing.router import router as billing_router

from modules.detection.router import router as detection_router
from modules.detection.event_handler import register_detection_handlers
from modules.forecasting.router import router as forecasting_router
from modules.dashboard.router import router as dashboard_router
from modules.dashboard.websocket import router as ws_router, register_ws_handlers
from modules.auth.router import router as auth_router
from modules.pipeline.router import router as pipeline_router
from modules.pipeline.router import metrics_router

from fastapi.staticfiles import StaticFiles

from modules.auth.service import AuthService
from core.database import async_session_factory

logger = structlog.get_logger(__name__)


# ============================================================
# LIFESPAN — startup and shutdown
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.

    Code before 'yield' runs on startup.
    Code after 'yield' runs on shutdown.

    This replaces the deprecated @app.on_event("startup") pattern.
    """
    # STARTUP
    logger.info("app.starting")
    await init_db()
    await init_redis()

    # Load externalized dataset configs into ConfigRegistry
    from core.dataset_config import load_dataset_configs
    load_dataset_configs()

    register_detection_handlers()
    from modules.analytics.event_handler import register_analytics_handlers
    register_analytics_handlers()
    register_ws_handlers()

    # Create initial admin user if none exist
    async with async_session_factory() as session:
        auth_service = AuthService(session)
        await auth_service.create_initial_admin()

    logger.info("app.ready")

    yield

    # SHUTDOWN
    logger.info("app.shutting_down")


# ============================================================
# APP CREATION
# ============================================================

__version__ = "1.0.0"

app = FastAPI(
    title="LLM Dashboard",
    description=(
        "AI Governance Platform. "
        "Monitoring, governance, and analytics platform for LLM API usage. "
        "Tracks token consumption, enforces spending limits, detects anomalies, "
        "and provides cost forecasting across tenants."
    ),
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ============================================================
# MIDDLEWARE
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request timing middleware — records duration/status for all API calls
from core.observability import RequestTimingMiddleware
app.add_middleware(RequestTimingMiddleware)


# ============================================================
# ROUTERS
# ============================================================

app.include_router(gateway_router)
app.include_router(analytics_router)
app.include_router(detection_router)
app.include_router(forecasting_router)
app.include_router(dashboard_router)
app.include_router(billing_router)
app.include_router(auth_router)
app.include_router(pipeline_router)
app.include_router(metrics_router)  # public /v1/metrics (no auth)
app.include_router(ws_router)
# ============================================================
# ROOT ENDPOINT
# ============================================================

@app.get("/", tags=["Root"])
async def root():
    """API root — confirms the service is running."""
    return {
        "service": "LLM Dashboard API",
        "version": __version__,
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health_probe():
    """Unauthenticated health probe for load balancers and k8s liveness checks."""
    return {"status": "ok", "version": __version__}


@app.get("/ready", tags=["Health"])
async def readiness_probe():
    """Readiness probe — checks database connectivity."""
    from core.database import async_session_factory as _sf
    from sqlalchemy import text as _text
    try:
        async with _sf() as _s:
            await _s.execute(_text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": str(e)},
        )


# Serve the frontend dashboard (falls back to raw HTML if no built dist)
_dist_path = os.path.join(os.path.dirname(__file__), "frontend_dist")
_frontend_dir = _dist_path if os.path.exists(_dist_path) else "frontend"
app.mount("/dashboard", StaticFiles(directory=_frontend_dir, html=True), name="frontend")