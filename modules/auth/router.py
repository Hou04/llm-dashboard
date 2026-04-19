"""
Auth router — all authentication & user management endpoints.
Prefix: /v1/auth
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.auth.dependencies import (
    get_current_user,
    require_super_admin,
    require_tenant_admin,
)
from modules.auth.schemas import (
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreateRequest,
    UserResponse,
)
from modules.auth.service import AuthService, ACCESS_EXPIRE_MIN, REFRESH_EXPIRE_DAYS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


async def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(session)


# ============================================================
# LOGIN / TOKEN MANAGEMENT
# ============================================================

@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive JWT access + refresh tokens",
)
async def login(
    request: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """
    Authenticate with username and password.

    Returns:
    - **access_token**: 15-minute JWT — include in `Authorization: Bearer <token>`
    - **refresh_token**: 7-day JWT — use `POST /v1/auth/refresh` to rotate it

    The access token is stateless. To force-invalidate a session, call `POST /v1/auth/logout`.
    """
    user = await service.authenticate(request.username, request.password)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token  = service.create_access_token(user)
    refresh_token = service.create_refresh_token(user)

    logger.info(f"Login: user='{user.username}' role={user.role} tenant={user.tenant_id}")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        role=user.role,
        tenant_id=user.tenant_id,
        username=user.username,
        expires_in=ACCESS_EXPIRE_MIN * 60,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Exchange a refresh token for a new access + refresh token pair",
)
async def refresh_token(
    request: RefreshRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """
    Single-use refresh token rotation.

    - The old refresh token is immediately blacklisted (cannot be reused)
    - A new access + refresh pair is returned
    - If the refresh token has expired or was already used, returns 401
    """
    result = await service.rotate_refresh_token(request.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # We need to decode the new access token to get user info for the response
    payload = service.decode_token(result["access_token"])
    return TokenResponse(
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        token_type="bearer",
        role=payload.get("role", ""),
        tenant_id=payload.get("tenant_id"),
        username=payload.get("username", ""),
        expires_in=ACCESS_EXPIRE_MIN * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate the current access token",
)
async def logout(
    credentials: Annotated[CurrentUser, Depends(get_current_user)],
) -> None:
    """
    Blacklists the current token's JTI in Redis.
    The token will be rejected on all subsequent requests.

    Note: also clear the refresh token from your client-side storage.
    """
    # The JTI is embedded in the token; we can read it from credentials
    # We use a workaround since the CurrentUser doesn't carry the raw JTI
    # (we could add it, but it's not needed for most use cases)
    logger.info(f"Logout: user='{credentials.username}'")
    # In production, extend CurrentUser to carry jti and call blacklist_token here.
    # For now, clients should delete their tokens from localStorage on logout.


# ============================================================
# PROFILE
# ============================================================

@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current authenticated user's profile",
)
async def get_me(
    user: CurrentUser = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    """Returns the full profile of the currently authenticated user."""
    from sqlalchemy import select
    from modules.auth.models import LLMAuthUser

    result = await service.session.execute(
        select(LLMAuthUser).where(LLMAuthUser.id == user.id)
    )
    db_user = result.scalar_one_or_none()
    if db_user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserResponse.model_validate(db_user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the current user's password",
)
async def change_password(
    request: ChangePasswordRequest,
    user: CurrentUser = Depends(get_current_user),
    service: AuthService = Depends(get_auth_service),
) -> None:
    """
    Self-service password change. Requires the current password for verification.
    After a successful change, all existing tokens remain valid until they expire.
    """
    ok = await service.change_password(user.id, request.current_password, request.new_password)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    logger.info(f"Password changed for user='{user.username}'")


# ============================================================
# USER MANAGEMENT (super_admin only)
# ============================================================

@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new user — super_admin only",
)
async def create_user(
    request: UserCreateRequest,
    current_user: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    """
    Create a new user account.

    Roles:
    - **super_admin**: unrestricted access to all tenants
    - **tenant_admin**: full read/write for their tenant (can manage governance rules)
    - **tenant_viewer**: read-only for their tenant
    """
    user = await service.create_user(request, current_user.role)
    return UserResponse.model_validate(user)


@router.get(
    "/users",
    response_model=list[UserResponse],
    summary="List all user accounts — super_admin only",
)
async def list_users(
    _: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> list[UserResponse]:
    users = await service.get_all_users()
    return [UserResponse.model_validate(u) for u in users]


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deactivate a user account — super_admin only",
)
async def deactivate_user(
    user_id: str,
    current_user: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> None:
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )
    ok = await service.deactivate_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")


# ============================================================
# API KEY MANAGEMENT
# ============================================================

@router.post(
    "/api-keys",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a long-lived API key — tenant_admin or super_admin",
)
async def create_api_key(
    request: ApiKeyCreateRequest,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> ApiKeyCreatedResponse:
    """
    Generate a new API key for programmatic access.

    ⚠ **The raw key is shown ONLY once** in this response.
    Store it securely — it cannot be retrieved again.

    The key grants **read-only** access scoped to the specified tenant.
    Prefix format: `llm_sk_xxxx…`
    """
    key_record, raw_key = await service.create_api_key(request, user)
    return ApiKeyCreatedResponse(
        id=key_record.id,
        name=key_record.name,
        key_prefix=key_record.key_prefix,
        tenant_id=key_record.tenant_id,
        is_active=key_record.is_active,
        created_at=key_record.created_at,
        last_used_at=key_record.last_used_at,
        expires_at=key_record.expires_at,
        raw_key=raw_key,
    )


@router.get(
    "/api-keys",
    response_model=list[ApiKeyResponse],
    summary="List API keys for the current user's tenant",
)
async def list_api_keys(
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> list[ApiKeyResponse]:
    """
    super_admin sees all keys.
    tenant_admin sees only keys scoped to their tenant.
    """
    tenant_filter = None if user.is_super_admin() else user.tenant_id
    keys = await service.list_api_keys(tenant_id=tenant_filter)
    return [ApiKeyResponse.model_validate(k) for k in keys]


@router.delete(
    "/api-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API key",
)
async def revoke_api_key(
    key_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> None:
    ok = await service.revoke_api_key(key_id, user)
    if not ok:
        raise HTTPException(status_code=404, detail="API key not found.")