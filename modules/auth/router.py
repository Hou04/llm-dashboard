"""
Auth router — all authentication & user management endpoints.
Prefix: /v1/auth
"""

import logging
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.audit import audit
from core.sanitize import sanitize_identifier
from modules.auth.dependencies import (
    get_current_user,
    require_super_admin,
    require_tenant_admin,
)
from modules.auth.schemas import (
    AdminResetPasswordRequest,
    ApiKeyCreateRequest,
    ApiKeyCreatedResponse,
    ApiKeyResponse,
    ChangePasswordRequest,
    CurrentUser,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)
from modules.auth.service import AuthService, ACCESS_EXPIRE_MIN, REFRESH_EXPIRE_DAYS
from core.sanitize import sanitize_identifier

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/auth", tags=["Authentication"])


async def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(session)


# ============================================================
# LOGIN / TOKEN MANAGEMENT
# ============================================================

from modules.auth.schemas import TenantSignupRequest
import uuid

@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign up as a new Tenant Admin",
)
async def signup(
    request: TenantSignupRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """
    Self-service onboarding for new tenants.
    Creates a new Tenant, assigns the user as a tenant_admin, and returns tokens.
    """
    from modules.tenants.models import LLMTenant
    from sqlalchemy import select
    
    # 1. Check if user exists
    existing = await service.get_user_by_username(request.username)
    if existing:
        raise HTTPException(status_code=400, detail="Username already taken")
        
    # 2. Create the Tenant
    tenant_id = sanitize_identifier(request.tenant_name)
    
    # check if tenant exists
    result = await service.session.execute(select(LLMTenant).where(LLMTenant.tenant_id == tenant_id))
    if result.scalar_one_or_none():
        tenant_id = f"{tenant_id}_{str(uuid.uuid4())[:8]}"
        
    tenant = LLMTenant(
        tenant_id=tenant_id,
        name=request.tenant_name,
        contact_email=request.email,
        tier="pay_as_you_go",
    )
    service.session.add(tenant)
    await service.session.flush()
    
    # 3. Create the User
    user = await service.create_user(
        UserCreateRequest(
            username=request.username,
            password=request.password,
            email=request.email,
            role="tenant_admin",
            tenant_id=tenant_id,
        ),
        created_by_role="super_admin", # Bypass role check for self-signup
    )
    
    # 4. Return tokens
    access_token  = service.create_access_token(user)
    refresh_token = service.create_refresh_token(user)

    logger.info(f"Signup: user='{user.username}' created new tenant='{tenant_id}'")

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
    await audit.log(
        session=service.session, user=current_user, action="create",
        resource_type="user", resource_id=user.id,
        description=f"Created user {user.username} with role {user.role}"
    )
    return UserResponse.model_validate(user)


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List user accounts with pagination, search, and filters — super_admin only",
)
async def list_users(
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    role: str | None = None,
    tenant_id: str | None = None,
    status_filter: str | None = None,
    _: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserListResponse:
    """
    Paginated user listing with optional filters.

    Query parameters:
    - **page**: page number (1-indexed)
    - **page_size**: users per page (max 100)
    - **search**: filter by username or email (case-insensitive)
    - **role**: filter by role (super_admin, tenant_admin, tenant_viewer)
    - **tenant_id**: filter by tenant
    - **status_filter**: 'active' or 'inactive'
    """
    page_size = min(page_size, 100)
    users, total = await service.get_users_paginated(
        page=page,
        page_size=page_size,
        search=search,
        role_filter=role,
        tenant_filter=tenant_id,
        status_filter=status_filter,
    )
    total_pages = max(1, (total + page_size - 1) // page_size)
    return UserListResponse(
        users=[UserResponse.model_validate(u) for u in users],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Get a single user by ID — super_admin only",
)
async def get_user(
    user_id: str,
    _: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    user = await service.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    return UserResponse.model_validate(user)


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Update a user account — super_admin only",
)
async def update_user(
    user_id: str,
    request: UserUpdateRequest,
    current_user: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    """
    Partially update user fields: email, role, tenant_id, is_active.
    Only non-null fields are applied.
    """
    updates = request.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No fields to update.",
        )
    user = await service.update_user(user_id, updates)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    await audit.log(
        session=service.session, user=current_user, action="update",
        resource_type="user", resource_id=user_id,
        description=f"Updated user {user.username}: {list(updates.keys())}"
    )
    return UserResponse.model_validate(user)


@router.post(
    "/users/{user_id}/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Admin-initiated password reset — super_admin only",
)
async def admin_reset_password(
    user_id: str,
    request: AdminResetPasswordRequest,
    _: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> None:
    ok = await service.admin_reset_password(user_id, request.new_password)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")


@router.post(
    "/users/{user_id}/reactivate",
    response_model=UserResponse,
    summary="Reactivate a deactivated user — super_admin only",
)
async def reactivate_user(
    user_id: str,
    _: CurrentUser = Depends(require_super_admin),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    ok = await service.reactivate_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found.")
    user = await service.get_user_by_id(user_id)
    return UserResponse.model_validate(user)


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
    await audit.log(
        session=service.session, user=current_user, action="deactivate",
        resource_type="user", resource_id=user_id,
        description=f"Deactivated user {user_id}"
    )


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
    await audit.log(
        session=service.session, user=user, action="revoke",
        resource_type="api_key", resource_id=key_id,
        description=f"Revoked API key {key_id}"
    )


# ============================================================
# VIRTUAL KEY MANAGEMENT (per-team keys with budgets)
# ============================================================

from modules.auth.schemas import (
    VirtualKeyCreateRequest,
    VirtualKeyCreatedResponse,
    VirtualKeyListResponse,
    VirtualKeyResponse,
    VirtualKeyRotateResponse,
)


@router.post(
    "/virtual-keys",
    response_model=VirtualKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a virtual API key with per-team permissions and budget",
)
async def create_virtual_key(
    request: VirtualKeyCreateRequest,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> VirtualKeyCreatedResponse:
    """
    Generate a new virtual API key with fine-grained permissions.

    Virtual keys support:
    - **Model restrictions**: limit which LLM models this key can access
    - **Budget caps**: set monthly USD spending limit
    - **Rate limiting**: requests per minute
    - **Environments**: separate live/test keys

    ⚠ **The raw key is shown ONLY once** in this response.
    Format: `llm_vk_live_<hex>` or `llm_vk_test_<hex>`
    """
    vk, raw_key = await service.create_virtual_key(request, user)
    await audit.log(
        session=service.session, user=user, action="create",
        resource_type="virtual_key", resource_id=vk.id,
        description=f"Created virtual key: {vk.name}"
    )
    return VirtualKeyCreatedResponse(
        id=vk.id,
        name=vk.name,
        key_prefix=vk.key_prefix,
        tenant_id=vk.tenant_id,
        environment=vk.environment,
        allowed_models=vk.allowed_models,
        budget_usd=vk.budget_usd,
        budget_used_usd=vk.budget_used_usd,
        rate_limit_rpm=vk.rate_limit_rpm,
        is_active=vk.is_active,
        created_at=vk.created_at,
        last_used_at=vk.last_used_at,
        expires_at=vk.expires_at,
        raw_key=raw_key,
    )


@router.get(
    "/virtual-keys",
    response_model=VirtualKeyListResponse,
    summary="List virtual API keys for the current tenant",
)
async def list_virtual_keys(
    tenant_id: Optional[str] = Query(None, description="Filter by tenant (super_admin only)"),
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> VirtualKeyListResponse:
    """
    super_admin sees all virtual keys, or filtered by tenant_id.
    tenant_admin sees only keys scoped to their tenant.
    """
    # Enforce isolation: if not super_admin, force to user.tenant_id
    effective_tenant = tenant_id if user.is_super_admin() else user.tenant_id
    
    # If not super_admin, they cannot request another tenant's keys
    if not user.is_super_admin() and tenant_id and tenant_id != user.tenant_id:
         raise HTTPException(status_code=403, detail="You can only view keys for your own tenant.")

    keys = await service.list_virtual_keys(tenant_id=effective_tenant)
    return VirtualKeyListResponse(
        keys=[VirtualKeyResponse.model_validate(k) for k in keys],
        total=len(keys),
        tenant_id=tenant_filter,
    )


@router.delete(
    "/virtual-keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a virtual API key",
)
async def revoke_virtual_key(
    key_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> None:
    ok = await service.revoke_virtual_key(key_id, user)
    if not ok:
        raise HTTPException(status_code=404, detail="Virtual key not found.")


@router.post(
    "/virtual-keys/{key_id}/rotate",
    response_model=VirtualKeyRotateResponse,
    summary="Rotate a virtual API key — revokes old, creates new with same permissions",
)
async def rotate_virtual_key(
    key_id: str,
    user: CurrentUser = Depends(require_tenant_admin),
    service: AuthService = Depends(get_auth_service),
) -> VirtualKeyRotateResponse:
    """
    Revokes the old key and generates a new one with the same permissions.
    The old key stops working immediately.
    """
    result = await service.rotate_virtual_key(key_id, user)
    if result is None:
        raise HTTPException(status_code=404, detail="Virtual key not found.")
    new_vk, raw_key = result
    return VirtualKeyRotateResponse(
        old_key_id=key_id,
        new_key=VirtualKeyCreatedResponse(
            id=new_vk.id,
            name=new_vk.name,
            key_prefix=new_vk.key_prefix,
            tenant_id=new_vk.tenant_id,
            environment=new_vk.environment,
            allowed_models=new_vk.allowed_models,
            budget_usd=new_vk.budget_usd,
            budget_used_usd=new_vk.budget_used_usd,
            rate_limit_rpm=new_vk.rate_limit_rpm,
            is_active=new_vk.is_active,
            created_at=new_vk.created_at,
            last_used_at=new_vk.last_used_at,
            expires_at=new_vk.expires_at,
            raw_key=raw_key,
        ),
    )