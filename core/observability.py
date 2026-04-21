"""
Observability & Monitoring — structured logging + metrics + middleware.

Provides:
1. Structured JSON logging for all pipeline events
2. In-memory metrics counters for monitoring
3. Pipeline execution timing (sync + async)
4. FastAPI request timing middleware
5. System info tracking (uptime, version, request counts)

Usage:
    from core.observability import metrics, pipeline_timer, log_pipeline_event

    metrics.increment("ingestion.rows_processed", 500)
    metrics.set_gauge("pipeline.last_run_duration_ms", 1234)

    with pipeline_timer("m2_aggregation"):
        await run_aggregation()
"""

import logging
import time
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# ── System startup time ──────────────────────────────────
_STARTUP_TIME = datetime.now(timezone.utc)
_APP_VERSION = "1.0.0"


class MetricsCollector:
    """
    In-memory metrics for monitoring pipeline health.

    In production, this would emit to Prometheus/StatsD.
    For now, stores counters in memory and exposes via API.
    """

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}
        self._gauges: dict[str, float] = {}
        self._timings: dict[str, list[float]] = {}
        self._last_updated: dict[str, datetime] = {}
        self._errors: list[dict] = []  # Last N errors for diagnostics

    def increment(self, name: str, value: int = 1) -> None:
        """Increment a counter."""
        self._counters[name] = self._counters.get(name, 0) + value
        self._last_updated[name] = datetime.now(timezone.utc)

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge to a specific value."""
        self._gauges[name] = value
        self._last_updated[name] = datetime.now(timezone.utc)

    def record_timing(self, name: str, duration_ms: float) -> None:
        """Record a timing measurement."""
        if name not in self._timings:
            self._timings[name] = []
        self._timings[name].append(duration_ms)
        # Keep only last 100 timings to prevent memory growth
        if len(self._timings[name]) > 100:
            self._timings[name] = self._timings[name][-100:]
        self._last_updated[name] = datetime.now(timezone.utc)

    def record_error(self, source: str, error: str, details: Optional[dict] = None) -> None:
        """Record an error event for diagnostics."""
        entry = {
            "source": source,
            "error": error,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._errors.append(entry)
        # Keep only last 50 errors
        if len(self._errors) > 50:
            self._errors = self._errors[-50:]
        self.increment(f"errors.{source}")

    def get_snapshot(self) -> dict:
        """Get a snapshot of all metrics."""
        timing_stats = {}
        for name, values in self._timings.items():
            if values:
                timing_stats[name] = {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values), 2),
                    "min_ms": round(min(values), 2),
                    "max_ms": round(max(values), 2),
                    "last_ms": round(values[-1], 2),
                }

        now = datetime.now(timezone.utc)
        uptime_seconds = (now - _STARTUP_TIME).total_seconds()

        return {
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "timings": timing_stats,
            "system": {
                "version": _APP_VERSION,
                "started_at": _STARTUP_TIME.isoformat(),
                "uptime_seconds": round(uptime_seconds, 1),
                "uptime_human": _format_uptime(uptime_seconds),
            },
            "recent_errors": self._errors[-10:],
            "collected_at": now.isoformat(),
        }

    def get_health_summary(self) -> dict:
        """Get a compact health summary for the /v1/metrics endpoint."""
        now = datetime.now(timezone.utc)
        uptime = (now - _STARTUP_TIME).total_seconds()

        # Compute request stats
        total_requests = self._counters.get("http.requests.total", 0)
        error_requests = self._counters.get("http.requests.errors", 0)
        error_rate = round(error_requests / max(total_requests, 1) * 100, 2)

        return {
            "status": "healthy",
            "version": _APP_VERSION,
            "uptime": _format_uptime(uptime),
            "uptime_seconds": round(uptime, 1),
            "requests": {
                "total": total_requests,
                "errors": error_requests,
                "error_rate_pct": error_rate,
            },
            "pipeline": {
                "ingestion_rows": self._counters.get("ingestion.rows_inserted", 0),
                "anomalies_generated": self._counters.get("anomalies.synthetic_generated", 0)
                    + self._counters.get("anomalies.natural_detected", 0),
                "pipeline_runs": self._counters.get("pipeline.full_pipeline.success", 0),
            },
            "recent_errors_count": len(self._errors),
            "collected_at": now.isoformat(),
        }

    def prometheus_export(self) -> str:
        """
        Export metrics in Prometheus text exposition format.

        This format is compatible with Prometheus, Grafana Agent,
        and any monitoring tool that speaks the Prometheus protocol.
        """
        now = datetime.now(timezone.utc)
        uptime = (now - _STARTUP_TIME).total_seconds()
        lines = []

        # App info
        lines.append('# HELP llm_app_info Application version and metadata')
        lines.append('# TYPE llm_app_info gauge')
        lines.append(f'llm_app_info{{version="{_APP_VERSION}"}} 1')

        # Uptime
        lines.append('# HELP llm_uptime_seconds Application uptime in seconds')
        lines.append('# TYPE llm_uptime_seconds gauge')
        lines.append(f'llm_uptime_seconds {uptime:.1f}')

        # Counters
        for name, value in sorted(self._counters.items()):
            prom_name = f"llm_{name.replace('.', '_').replace('-', '_')}"
            lines.append(f'# TYPE {prom_name} counter')
            lines.append(f'{prom_name} {value}')

        # Gauges
        for name, value in sorted(self._gauges.items()):
            prom_name = f"llm_{name.replace('.', '_').replace('-', '_')}"
            lines.append(f'# TYPE {prom_name} gauge')
            lines.append(f'{prom_name} {value}')

        # Timing summaries
        for name, values in sorted(self._timings.items()):
            if values:
                prom_name = f"llm_{name.replace('.', '_').replace('-', '_')}_ms"
                avg_val = sum(values) / len(values)
                lines.append(f'# TYPE {prom_name} summary')
                lines.append(f'{prom_name}_count {len(values)}')
                lines.append(f'{prom_name}_avg {avg_val:.2f}')
                lines.append(f'{prom_name}_max {max(values):.2f}')

        lines.append('')  # Trailing newline
        return '\n'.join(lines)

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        self._counters.clear()
        self._gauges.clear()
        self._timings.clear()
        self._last_updated.clear()
        self._errors.clear()


# Singleton instance
metrics = MetricsCollector()


def _format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


# ── Pipeline Timer (sync context manager) ──────────────────────

@contextmanager
def pipeline_timer(step_name: str):
    """
    Context manager that times a pipeline step and logs it.

    Usage:
        with pipeline_timer("m2_aggregation"):
            await run_aggregation()
    """
    start = time.monotonic()
    logger.info(f"pipeline.step.start step={step_name}")
    try:
        yield
    except Exception as e:
        duration = (time.monotonic() - start) * 1000
        metrics.record_timing(f"pipeline.{step_name}", duration)
        metrics.increment(f"pipeline.{step_name}.errors")
        metrics.record_error(f"pipeline.{step_name}", str(e))
        logger.error(
            f"pipeline.step.error step={step_name} "
            f"duration_ms={duration:.1f} error={e}"
        )
        raise
    else:
        duration = (time.monotonic() - start) * 1000
        metrics.record_timing(f"pipeline.{step_name}", duration)
        metrics.increment(f"pipeline.{step_name}.success")
        logger.info(
            f"pipeline.step.complete step={step_name} "
            f"duration_ms={duration:.1f}"
        )


def log_pipeline_event(
    event: str,
    *,
    tenant_id: Optional[str] = None,
    module: Optional[str] = None,
    details: Optional[dict] = None,
    level: str = "info",
) -> None:
    """
    Emit a structured pipeline event log.

    All pipeline events go through this function so
    they have a consistent format for log aggregation.
    """
    parts = [f"pipeline.event={event}"]
    if tenant_id:
        parts.append(f"tenant={tenant_id}")
    if module:
        parts.append(f"module={module}")
    if details:
        for k, v in details.items():
            parts.append(f"{k}={v}")

    message = " ".join(parts)
    getattr(logger, level, logger.info)(message)
    metrics.increment(f"pipeline.events.{event}")


# ── Request Timing Middleware ───────────────────────────────────

class RequestTimingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that times every HTTP request and records
    the duration in the metrics collector.

    Also counts total requests, error responses, and per-path stats.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.monotonic()
        path = request.url.path
        method = request.method

        try:
            response = await call_next(request)
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            metrics.increment("http.requests.total")
            metrics.increment("http.requests.errors")
            metrics.record_timing("http.request_duration_ms", duration)
            metrics.record_error("http.unhandled", str(e), {"path": path, "method": method})
            raise

        duration = (time.monotonic() - start) * 1000
        status = response.status_code

        # Record metrics
        metrics.increment("http.requests.total")
        metrics.record_timing("http.request_duration_ms", duration)

        if status >= 400:
            metrics.increment("http.requests.errors")

        # Per-path metrics for API routes (not static files)
        if path.startswith("/v1/"):
            clean_path = path.split("?")[0]
            metrics.record_timing(f"http.path.{clean_path}", duration)

        # Log slow requests
        if duration > 2000:
            logger.warning(
                f"http.slow_request path={path} method={method} "
                f"status={status} duration_ms={duration:.1f}"
            )

        return response
