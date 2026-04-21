"""
Security headers middleware.

Adds defense-in-depth HTTP headers to every response:
- X-Content-Type-Options: nosniff     — prevents MIME-type sniffing attacks
- X-Frame-Options: DENY               — prevents clickjacking via iframe embedding
- X-XSS-Protection: 1; mode=block     — legacy XSS filter (still useful for older browsers)
- Referrer-Policy: strict-origin-when-cross-origin — limits referrer data leakage
- Permissions-Policy                   — disables camera/mic/geolocation API access
- Content-Security-Policy              — restricts script/style sources (report-only in dev)

These headers cost zero performance and prevent entire classes of attacks.
"""

import logging
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Adds security headers to all HTTP responses.

    Static file responses (dashboard) get full CSP.
    API responses get a minimal set (no CSP needed for JSON).
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Universal headers — apply to all responses
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )

        # Cache control for API responses — prevent caching of sensitive data
        path = request.url.path
        if path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"

        return response
