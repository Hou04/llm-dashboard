"""Pydantic schemas for auth request/response validation."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
import re


# ============================================================
# LOGIN / TOKENS
# ============================================================

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=6, max_length=200)


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    role:          str
    tenant_id:     Optional[str] = None
    username:      str
    expires_in:    int  # seconds (access token)


class RefreshRequest(BaseModel):
    refresh_token: str


# ============================================================
# USER MANAGEMENT
# ============================================================

VALID_ROLES = {"super_admin", "tenant_admin", "tenant_viewer"}

class UserCreateRequest(BaseModel):
    username:  str = Field(..., min_length=3, max_length=100)
    password:  str = Field(..., min_length=8, max_length=200)
    email:     Optional[str] = None
    role:      str = Field(default="tenant_viewer")
    tenant_id: Optional[str] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserUpdateRequest(BaseModel):
    """Partial update for an existing user — super_admin only."""
    email:     Optional[str] = None
    role:      Optional[str] = None
    tenant_id: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(sorted(VALID_ROLES))}")
        return v


class AdminResetPasswordRequest(BaseModel):
    """Admin-initiated password reset — super_admin only."""
    new_password: str = Field(..., min_length=8, max_length=200)

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserListResponse(BaseModel):
    """Paginated list of users with metadata."""
    users: list['UserResponse']
    total: int
    page: int
    page_size: int
    total_pages: int


class TenantSignupRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8, max_length=200)
    tenant_name: str = Field(..., min_length=3, max_length=100)
    email: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one digit")
        return v


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=6)
    new_password:     str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if not re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not re.search(r"[0-9]", v):
            raise ValueError("Password must contain at least one digit")
        return v


class UserResponse(BaseModel):
    id:            str
    username:      str
    email:         Optional[str]
    role:          str
    tenant_id:     Optional[str]
    is_active:     bool
    created_at:    datetime
    last_login_at: Optional[datetime] = None
    last_activity_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ============================================================
# API KEYS
# ============================================================

class ApiKeyCreateRequest(BaseModel):
    name:      str = Field(..., min_length=3, max_length=100,
                           description="Human-readable label, e.g. 'CI Pipeline'")
    tenant_id: Optional[str] = Field(
        default=None,
        description="Scope to a specific tenant. super_admin may leave blank for cross-tenant."
    )
    expires_days: Optional[int] = Field(
        default=365, ge=1, le=730,
        description="Key validity in days. Default 365."
    )


class ApiKeyResponse(BaseModel):
    """Returned when listing keys — raw key is never included here."""
    id:           str
    name:         str
    key_prefix:   str
    tenant_id:    Optional[str]
    is_active:    bool
    created_at:   datetime
    last_used_at: Optional[datetime] = None
    expires_at:   Optional[datetime] = None

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Returned ONCE at creation — includes the raw key. Store it securely."""
    raw_key: str


# ============================================================
# CURRENT USER (injected into every protected endpoint)
# ============================================================

class CurrentUser(BaseModel):
    """
    Injected into every protected endpoint via Depends(get_current_user).

    Usage in a router:
        @router.get("/costs/{tenant_id}")
        async def get_costs(
            tenant_id: str,
            user: CurrentUser = Depends(require_tenant_viewer),
        ):
            user.require_tenant_access(tenant_id)
            ...
    """
    id:        str
    username:  str
    role:      str
    tenant_id: Optional[str]
    is_api_key: bool = False   # True when authenticated via X-API-Key header

    def is_super_admin(self) -> bool:
        return self.role == "super_admin"

    def is_admin(self) -> bool:
        return self.role in ("super_admin", "tenant_admin")

    def can_access_tenant(self, tenant_id: str) -> bool:
        """Super admin can access any tenant. Others only their own."""
        if self.role == "super_admin":
            return True
        return self.tenant_id == tenant_id

    def require_tenant_access(self, tenant_id: str) -> None:
        """Raise HTTP 403 if this user cannot access the given tenant."""
        from fastapi import HTTPException, status
        if not self.can_access_tenant(tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to tenant '{tenant_id}'. "
                       f"Your account is scoped to tenant '{self.tenant_id}'.",
            )

    def require_admin(self) -> None:
        """Raise HTTP 403 if this user is not at least tenant_admin."""
        from fastapi import HTTPException, status
        if not self.is_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="At least tenant_admin role is required for this action.",
            )

    def require_super_admin(self) -> None:
        """Raise HTTP 403 if this user is not super_admin."""
        from fastapi import HTTPException, status
        if not self.is_super_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="super_admin role is required for this action.",
            )


# ============================================================
# VIRTUAL KEYS (per-team API keys with budgets & permissions)
# ============================================================

class VirtualKeyCreateRequest(BaseModel):
    """Request body for POST /v1/auth/virtual-keys."""
    name: str = Field(
        ..., min_length=3, max_length=100,
        description="Human-readable label, e.g. 'ML Pipeline - Production'",
    )
    tenant_id: Optional[str] = Field(
        default=None,
        description="Scope to a specific tenant. tenant_admin keys are auto-scoped.",
    )
    environment: str = Field(
        default="live",
        description="Key environment: 'live' or 'test'",
    )
    allowed_models: Optional[list[str]] = Field(
        default=None,
        description="Restrict to specific models. NULL = all models. e.g. ['gpt-4o-mini', 'claude-3-haiku']",
    )
    budget_usd: Optional[float] = Field(
        default=None, ge=0,
        description="Monthly budget cap in USD. NULL = unlimited.",
    )
    rate_limit_rpm: Optional[int] = Field(
        default=None, ge=1, le=10000,
        description="Max requests per minute. NULL = no rate limit.",
    )
    expires_days: Optional[int] = Field(
        default=365, ge=1, le=730,
        description="Key validity in days. Default 365.",
    )

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        if v not in ("live", "test"):
            raise ValueError("environment must be 'live' or 'test'")
        return v


class VirtualKeyResponse(BaseModel):
    """Returned when listing virtual keys — raw key is never included."""
    id: str
    name: str
    key_prefix: str
    tenant_id: str
    environment: str
    allowed_models: Optional[list[str]] = None
    budget_usd: Optional[float] = None
    budget_used_usd: float = 0.0
    rate_limit_rpm: Optional[int] = None
    is_active: bool
    created_at: datetime
    last_used_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_validator("allowed_models", mode="before")
    @classmethod
    def parse_allowed_models(cls, v):
        """Convert comma-separated string from DB to list."""
        if v is None:
            return None
        if isinstance(v, str):
            models = [m.strip() for m in v.split(",") if m.strip()]
            return models if models else None
        return v


class VirtualKeyCreatedResponse(VirtualKeyResponse):
    """Returned ONCE at creation — includes the raw key. Store it securely."""
    raw_key: str


class VirtualKeyListResponse(BaseModel):
    """Paginated list of virtual keys."""
    keys: list[VirtualKeyResponse]
    total: int
    tenant_id: Optional[str] = None


class VirtualKeyRotateResponse(BaseModel):
    """Returned when rotating a virtual key."""
    old_key_id: str
    new_key: VirtualKeyCreatedResponse