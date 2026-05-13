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

import asyncio
import logging
import sys

# Configure standard streams to use UTF-8 on Windows to prevent UnicodeEncodeError
# when structlog formats tracebacks containing non-ASCII characters.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi.staticfiles import StaticFiles
import os

from core.settings import settings
from core.database import init_db
from core.redis import init_redis
from core.rate_limit import RateLimitMiddleware
from core.security_headers import SecurityHeadersMiddleware
from modules.gateway.router import router as gateway_router
from modules.analytics.router import router as analytics_router
from modules.billing.router import router as billing_router

from modules.detection.router import router as detection_router
from modules.detection.event_handler import register_detection_handlers
from modules.forecasting.router import router as forecasting_router
from modules.dashboard.router import router as dashboard_router
from modules.dashboard.websocket import router as ws_router, register_ws_handlers
from modules.auth.router import router as auth_router
from modules.prompts.router import router as prompts_router
from modules.tracing.router import router as tracing_router
from modules.observability.router import router as observability_router
from modules.gateway.catalog_router import router as catalog_router
from modules.tenants.router import router as tenants_router

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

    # ── Security: reject default secret key in production ──
    if settings.app_env == "production" and settings.secret_key in (
        "CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING",
        "changeme",
        "secret",
        "",
        "dev-secret-key-change-in-production"
    ):
        raise RuntimeError(
            "FATAL: SECRET_KEY is set to a default/insecure value. "
            "Generate a secure key: python -c \"import secrets; print(secrets.token_hex(32))\" "
            "and set it in your .env file."
        )

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

# Rate limiting — must be added BEFORE other middleware (outermost layer)
app.add_middleware(RateLimitMiddleware)

# Security headers — X-Content-Type-Options, X-Frame-Options, etc.
app.add_middleware(SecurityHeadersMiddleware)

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
app.include_router(prompts_router)
app.include_router(tracing_router)
app.include_router(observability_router)
app.include_router(ws_router)
app.include_router(catalog_router)
app.include_router(tenants_router)


# ============================================================
# GLOBAL EXCEPTION HANDLER
# ============================================================

from fastapi import Request
from fastapi.responses import JSONResponse
from core.observability import metrics

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for unhandled exceptions.
    Records the error in the central observability metrics and returns
    a sanitized 500 response to prevent internal detail leakage.
    """
    logger.error(f"Unhandled exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    
    # Record error in metrics
    metrics.record_error(
        source="unhandled_exception",
        error=str(exc),
        details={
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else None
        }
    )
    
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error_id": getattr(request.state, "trace_id", "unknown")}
    )

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