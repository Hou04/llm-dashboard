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
virtual_key_scheme = APIKeyHeader(name="X-Virtual-Key", auto_error=False)


# ---------------------------------------------------------------
# Core resolver — accepts JWT OR API key OR Virtual key
# ---------------------------------------------------------------
async def _resolve_user(
    credentials: Annotated[
        Optional[HTTPAuthorizationCredentials],
        Depends(bearer_scheme),
    ],
    api_key_header: Annotated[Optional[str], Security(api_key_scheme)] = None,
    virtual_key_header: Annotated[Optional[str], Security(virtual_key_scheme)] = None,
    session: AsyncSession = Depends(get_db),
) -> Optional[CurrentUser]:
    """
    Try JWT first, then X-API-Key, then X-Virtual-Key header.
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
            
            user_id = payload.get("sub", "")
            # Update last activity in background/best-effort
            try:
                await service.update_last_activity(user_id)
            except Exception:
                pass

            return CurrentUser(
                id=user_id,
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

    # ── 3. Try X-Virtual-Key header ─────────────────────────────
    if virtual_key_header is not None:
        vk = await service.verify_virtual_key(virtual_key_header)
        if vk is not None:
            return CurrentUser(
                id=vk.id,
                username=f"vk:{vk.name}",
                role="tenant_viewer",        # Virtual keys get viewer role
                tenant_id=vk.tenant_id,
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


# ---------------------------------------------------------------
# Tenant scope resolution — Two-Personality enforcement
# ---------------------------------------------------------------

class TenantScope:
    """
    Resolved tenant scope for data queries.

    Attributes:
        tenant_id:  Effective tenant to query. None means "all tenants"
                    (only possible for super_admin).
        is_scoped:  True if the result is locked to a single tenant.
        user:       The authenticated user who owns this scope.
    """
    __slots__ = ("tenant_id", "is_scoped", "user")

    def __init__(self, tenant_id: Optional[str], is_scoped: bool, user: CurrentUser):
        self.tenant_id = tenant_id
        self.is_scoped = is_scoped
        self.user = user


async def get_tenant_scope(
    tenant_id: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
) -> TenantScope:
    """
    Two-Personality tenant scoping dependency.

    Usage in a router:
        @router.get("/data")
        async def get_data(scope: TenantScope = Depends(get_tenant_scope)):
            # scope.tenant_id is guaranteed safe
            data = await repo.query(tenant_id=scope.tenant_id)

    Behavior:
      - super_admin:   Uses the requested tenant_id (or None for all tenants).
      - tenant_admin/viewer: ALWAYS forced to user.tenant_id, regardless
        of what the caller passes. This prevents URL-guessing attacks.
    """
    if user.is_super_admin():
        return TenantScope(
            tenant_id=tenant_id,
            is_scoped=tenant_id is not None,
            user=user,
        )

    # Non-super-admin: force to their own tenant, always scoped
    forced_id = user.tenant_id
    if not forced_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has no tenant_id assigned. Contact your administrator.",
        )
    return TenantScope(
        tenant_id=forced_id,
        is_scoped=True,
        user=user,
    )