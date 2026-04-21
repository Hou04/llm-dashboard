"""
Rate limiting middleware using a sliding-window counter in memory.

Production consideration: in a multi-worker deployment, move the counter
storage to Redis so limits are shared across workers. This in-memory
version is correct for single-process (uvicorn --workers 1) or dev.

Configuration:
    DEFAULT_RATE   — requests per minute for general API endpoints
    AUTH_RATE      — requests per minute for /v1/auth/login (brute-force protection)
    BURST_RATE     — requests per minute for write endpoints (POST/PUT/PATCH/DELETE)

All rates are per-IP. X-Forwarded-For is respected if present (reverse proxy).
"""

import time
import logging
from collections import defaultdict
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────
DEFAULT_RATE = 200       # requests per minute for general endpoints
AUTH_RATE = 10           # requests per minute for login endpoint
BURST_RATE = 60          # requests per minute for write operations
WINDOW_SECONDS = 60      # sliding window size

# Paths with special rate limits
_AUTH_PATHS = {"/v1/auth/login", "/v1/auth/refresh"}

# Paths exempt from rate limiting (health checks, static files)
_EXEMPT_PREFIXES = ("/health", "/ready", "/docs", "/redoc", "/openapi.json", "/dashboard")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP sliding-window rate limiter.

    Returns 429 Too Many Requests when limit exceeded, with a
    Retry-After header indicating seconds until the window resets.
    """

    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)
        # {ip: [(timestamp, ...)] }
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._last_cleanup = time.monotonic()

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP, respecting X-Forwarded-For from reverse proxies."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Take the first IP in the chain (original client)
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "unknown"

    def _get_rate_limit(self, path: str, method: str) -> int:
        """Determine the rate limit for a given path and method."""
        if path in _AUTH_PATHS:
            return AUTH_RATE
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            return BURST_RATE
        return DEFAULT_RATE

    def _cleanup_old_entries(self, now: float) -> None:
        """Periodically remove expired entries to prevent memory growth."""
        if now - self._last_cleanup < 60:
            return
        self._last_cleanup = now
        cutoff = now - WINDOW_SECONDS
        expired_keys = []
        for ip, timestamps in self._requests.items():
            self._requests[ip] = [t for t in timestamps if t > cutoff]
            if not self._requests[ip]:
                expired_keys.append(ip)
        for key in expired_keys:
            del self._requests[key]

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method

        # Skip rate limiting for exempt paths
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await call_next(request)

        now = time.monotonic()
        self._cleanup_old_entries(now)

        client_ip = self._get_client_ip(request)
        # Use a composite key for auth endpoints (stricter per-path limit)
        rate_key = f"{client_ip}:{path}" if path in _AUTH_PATHS else client_ip

        rate_limit = self._get_rate_limit(path, method)
        cutoff = now - WINDOW_SECONDS

        # Filter to requests within the window
        recent = [t for t in self._requests[rate_key] if t > cutoff]
        self._requests[rate_key] = recent

        if len(recent) >= rate_limit:
            retry_after = int(WINDOW_SECONDS - (now - recent[0])) + 1
            logger.warning(
                f"rate_limit.exceeded ip={client_ip} path={path} "
                f"count={len(recent)} limit={rate_limit}"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down.",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(rate_limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        # Record this request
        self._requests[rate_key].append(now)
        remaining = rate_limit - len(self._requests[rate_key])

        response = await call_next(request)

        # Add rate limit headers to all responses
        response.headers["X-RateLimit-Limit"] = str(rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))

        return response
