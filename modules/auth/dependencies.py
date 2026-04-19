"""
FastAPI dependency injection for authentication.

Available dependencies (import from here in any router):

  require_tenant_viewer  — any authenticated user (viewer, admin, super_admin, api_key_agent)
  require_tenant_admin   — tenant_admin or super_admin
  require_super_admin    — super_admin only
  get_current_user       — alias for require_tenant_viewer (use when you just need the user object)

Dual-mode auth:
  Each dependency accepts EITHER:
    Authorization: Bearer <jwt>   ← browser sessions
    X-API-Key: llm_sk_<key>       ← CI/CD, external services

Usage example:
    from modules.auth.dependencies import require_tenant_viewer, require_tenant_admin
    from modules.auth.schemas import CurrentUser

    @router.get("/costs/{tenant_id}")
    async def get_costs(
        tenant_id: str,
        user: CurrentUser = Depends(require_tenant_viewer),
    ):
        user.require_tenant_access(tenant_id)   # 403 if wrong tenant
        ...

    @router.post("/rules")
    async def create_rule(
        user: CurrentUser = Depends(require_tenant_admin),
    ):
        ...
"""

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.auth.schemas import CurrentUser
from modules.auth.service import AuthService

# ---------------------------------------------------------------
# Security scheme extractors
# ---------------------------------------------------------------
bearer_scheme  = HTTPBearer(auto_error=False)
api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------
# Core resolver — accepts JWT OR API key
# ---------------------------------------------------------------
async def _resolve_user(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(bearer_scheme),
    ],
    api_key_header: Annotated[Optional[str], Security(api_key_scheme)] = None,
    session: AsyncSession = Depends(get_db),
) -> Optional[CurrentUser]:
    """
    Try JWT first, then X-API-Key header.
    Returns a CurrentUser or None (raises handled upstream).
    """
    service = AuthService(session)

    # ── 1. Try Bearer JWT ───────────────────────────────────────
    if credentials is not None:
        payload = service.decode_token(credentials.credentials)
        if payload is not None and payload.get("type") == "access":
            # Check if the token JTI has been blacklisted (logout)
            jti = payload.get("jti", "")
            if jti and service.is_blacklisted(jti):
                return None  # will be caught as 401 below
            return CurrentUser(
                id=payload.get("sub", ""),
                username=payload.get("username", ""),
                role=payload.get("role", ""),
                tenant_id=payload.get("tenant_id"),
                is_api_key=False,
            )

    # ── 2. Try X-API-Key header ─────────────────────────────────
    if api_key_header is not None:
        key_record = await service.verify_api_key(api_key_header)
        if key_record is not None:
            return CurrentUser(
                id=key_record.id,
                username=f"api_key:{key_record.name}",
                role="tenant_viewer",        # API keys always get viewer role
                tenant_id=key_record.tenant_id,
                is_api_key=True,
            )

    return None


# ---------------------------------------------------------------
# Public dependency: require any authenticated user
# ---------------------------------------------------------------
async def get_current_user(
    user: Optional[CurrentUser] = Depends(_resolve_user),
) -> CurrentUser:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Provide a valid Bearer token or X-API-Key header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


# Alias — "viewer" is the lowest bar for protected endpoints
require_tenant_viewer = get_current_user


# ---------------------------------------------------------------
# Elevated roles
# ---------------------------------------------------------------
async def require_tenant_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Requires tenant_admin or super_admin. API keys are NOT allowed here."""
    if user.is_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires a user session, not an API key.",
        )
    user.require_admin()
    return user


async def require_super_admin(
    user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """Requires super_admin. API keys are NOT allowed here."""
    if user.is_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires a super_admin session, not an API key.",
        )
    user.require_super_admin()
    return user