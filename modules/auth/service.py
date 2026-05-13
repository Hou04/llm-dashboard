"""
AuthService — password hashing, JWT lifecycle, API key management.

Security architecture:
  Access token:   HS256 JWT, 15-minute expiry, carries user identity
  Refresh token:  HS256 JWT, 7-day expiry, single-use (rotation on exchange)
  Logout:         blacklists the access token's JTI in Redis (TTL = remaining lifetime)
  API keys:       llm_sk_<32-bytes-hex>, stored as BLAKE2b-256 hash
  Passwords:      pbkdf2_sha256 with 600000 rounds (NIST recommended)

Token payload:
  {
    "sub":        "user-uuid",
    "username":   "alice",
    "role":       "tenant_admin",
    "tenant_id":  "enterprise_corp",
    "type":       "access" | "refresh",
    "iat":        1234567890,
    "exp":        1234567890,
    "jti":        "unique-token-uuid"   ← used for blacklisting
  }
"""

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import settings
from modules.auth.models import LLMApiKey, LLMAuthUser, LLMVirtualKey
from modules.auth.schemas import ApiKeyCreateRequest, CurrentUser, UserCreateRequest, VirtualKeyCreateRequest

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------
# Password hashing
# pbkdf2_sha256 — avoids bcrypt C-library issues on some platforms
# ---------------------------------------------------------------
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# ---------------------------------------------------------------
# JWT configuration
# ---------------------------------------------------------------
ALGORITHM          = "HS256"
ACCESS_EXPIRE_MIN  = 15       # 15-minute access tokens
REFRESH_EXPIRE_DAYS = 7       # 7-day refresh tokens
EXPIRE_HOURS       = 24       # kept for backwards compatibility (unused post-migration)

_INSECURE_DEFAULT = "dev-secret-key-change-in-production"


def _check_secret_key() -> None:
    """Warn loudly if the secret key is still the default."""
    if settings.secret_key == _INSECURE_DEFAULT:
        logger.warning(
            "\n"
            "=" * 70 + "\n"
            "  ⚠  SECURITY WARNING: SECRET_KEY is set to the development default.\n"
            "  All JWTs can be forged by anyone who knows this key.\n"
            "  Set SECRET_KEY to a random 64-character string in your .env file.\n"
            "  Generate one with:  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "=" * 70
        )


def _redis():
    """Lazy import to avoid circular imports at module load time."""
    from core.redis import get_redis_sync
    try:
        return get_redis_sync()
    except Exception:
        return None


class AuthService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        _check_secret_key()

    # ================================================================
    # PASSWORD UTILITIES
    # ================================================================

    @staticmethod
    def hash_password(plain: str) -> str:
        return pwd_context.hash(plain)

    @staticmethod
    def verify_password(plain: str, hashed: str) -> bool:
        return pwd_context.verify(plain, hashed)

    # ================================================================
    # JWT UTILITIES
    # ================================================================

    @staticmethod
    def _make_token(user_id: str, username: str, role: str,
                    tenant_id: Optional[str], token_type: str,
                    expire_delta: timedelta) -> str:
        now    = datetime.now(timezone.utc)
        expire = now + expire_delta
        payload = {
            "sub":       user_id,
            "username":  username,
            "role":      role,
            "tenant_id": tenant_id,
            "type":      token_type,
            "iat":       int(now.timestamp()),
            "exp":       int(expire.timestamp()),
            "jti":       str(uuid.uuid4()),
        }
        return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)

    @classmethod
    def create_access_token(cls, user: LLMAuthUser) -> str:
        return cls._make_token(
            user_id=str(user.id), username=user.username,
            role=user.role, tenant_id=user.tenant_id,
            token_type="access",
            expire_delta=timedelta(minutes=ACCESS_EXPIRE_MIN),
        )

    @classmethod
    def create_refresh_token(cls, user: LLMAuthUser) -> str:
        return cls._make_token(
            user_id=str(user.id), username=user.username,
            role=user.role, tenant_id=user.tenant_id,
            token_type="refresh",
            expire_delta=timedelta(days=REFRESH_EXPIRE_DAYS),
        )

    @staticmethod
    def decode_token(token: str) -> Optional[dict]:
        """Decode & validate JWT. Returns payload or None."""
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
            return payload
        except JWTError as e:
            logger.debug(f"JWT validation failed: {e}")
            return None

    @staticmethod
    def blacklist_token(jti: str, exp: int) -> None:
        """
        Add a JTI to the Redis blacklist.
        TTL = remaining token lifetime so Redis auto-cleans expired entries.
        Silently no-ops if Redis is unavailable.
        """
        r = _redis()
        if r is None:
            return
        try:
            remaining = max(exp - int(datetime.now(timezone.utc).timestamp()), 1)
            r.setex(f"blacklist:jti:{jti}", remaining, "1")
        except Exception as e:
            logger.warning(f"Could not blacklist token JTI {jti}: {e}")

    @staticmethod
    def is_blacklisted(jti: str) -> bool:
        """Check if a JTI has been revoked."""
        r = _redis()
        if r is None:
            return False
        try:
            return r.exists(f"blacklist:jti:{jti}") == 1
        except Exception:
            return False

    # ================================================================
    # AUTHENTICATION
    # ================================================================

    async def authenticate(self, username: str, password: str) -> Optional[LLMAuthUser]:
        """
        Verify username + password.
        Timing-safe — always hashes even when user not found.
        """
        # Ensure a default admin exists on first run.
        result = await self.session.execute(select(LLMAuthUser).limit(1))
        if result.scalar_one_or_none() is None:
            await self.create_initial_admin()

        result = await self.session.execute(
            select(LLMAuthUser).where(
                LLMAuthUser.username == username,
                LLMAuthUser.is_active == True,
            )
        )
        user = result.scalar_one_or_none()

        if user is None:
            # Even if user not found, perform hash to prevent timing attacks
            pwd_context.hash("constant_hash_to_prevent_timing_attack")
            return None

        if not self.verify_password(password, user.hashed_password):
            return None

        user.last_login_at = datetime.now(timezone.utc)
        user.last_activity_at = user.last_login_at
        await self.session.commit()
        return user

    async def update_last_activity(self, user_id: str) -> None:
        """Update last_activity_at for a user (best effort)."""
        await self.session.execute(
            update(LLMAuthUser)
            .where(LLMAuthUser.id == user_id)
            .values(last_activity_at=datetime.now(timezone.utc))
        )
        await self.session.commit()

    async def rotate_refresh_token(self, refresh_token: str) -> Optional[dict]:
        """
        Validate an old refresh token, blacklist it, issue a new pair.
        Returns {access_token, refresh_token} or None if invalid.
        """
        payload = self.decode_token(refresh_token)
        if payload is None or payload.get("type") != "refresh":
            return None

        jti = payload.get("jti", "")
        exp = payload.get("exp", 0)

        if self.is_blacklisted(jti):
            return None

        # Blacklist the old refresh token (single-use enforcement)
        self.blacklist_token(jti, exp)

        # Fetch user to get fresh DB state
        result = await self.session.execute(
            select(LLMAuthUser).where(
                LLMAuthUser.id == payload["sub"],
                LLMAuthUser.is_active == True,
            )
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None

        return {
            "access_token":  self.create_access_token(user),
            "refresh_token": self.create_refresh_token(user),
        }

    async def change_password(self, user_id: str,
                               current_pw: str, new_pw: str) -> bool:
        """Returns True if password changed, False if current_pw wrong."""
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None or not self.verify_password(current_pw, user.hashed_password):
            return False
        user.hashed_password = self.hash_password(new_pw)
        await self.session.commit()
        return True

    # ================================================================
    # USER MANAGEMENT
    # ================================================================

    async def get_user_by_username(self, username: str) -> Optional[LLMAuthUser]:
        """Fetch a user by their username."""
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.username == username)
        )
        return result.scalar_one_or_none()

    async def create_user(self, request: UserCreateRequest,
                          created_by_role: str = "super_admin") -> LLMAuthUser:
        """
        Create a new user account. 
        Note: This method expects a UserCreateRequest schema object.
        """
        if request.role == "super_admin" and created_by_role != "super_admin":
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super_admin can create super_admin users.",
            )

        # Check duplicate username
        exists = await self.get_user_by_username(request.username)
        if exists is not None:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Username '{request.username}' is already taken.",
            )

        user = LLMAuthUser(
            id=str(uuid.uuid4()),
            username=request.username,
            email=request.email,
            hashed_password=self.hash_password(request.password),
            role=request.role,
            tenant_id=request.tenant_id,
        )
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info(f"Created user '{user.username}' role={user.role} tenant={user.tenant_id}")
        return user

    async def get_all_users(self) -> list[LLMAuthUser]:
        result = await self.session.execute(
            select(LLMAuthUser).order_by(LLMAuthUser.created_at.desc())
        )
        return list(result.scalars().all())

    async def deactivate_user(self, user_id: str) -> bool:
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return False
        user.is_active = False
        await self.session.commit()
        return True

    async def reactivate_user(self, user_id: str) -> bool:
        """Re-enable a previously deactivated user account."""
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return False
        user.is_active = True
        await self.session.commit()
        return True

    async def get_user_by_id(self, user_id: str) -> Optional[LLMAuthUser]:
        """Fetch a single user by their ID."""
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.id == user_id)
        )
        return result.scalar_one_or_none()

    async def update_user(self, user_id: str, updates: dict) -> Optional[LLMAuthUser]:
        """
        Partially update a user record.
        Only non-None fields in the updates dict are applied.
        """
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return None

        for field, value in updates.items():
            if value is not None and hasattr(user, field):
                setattr(user, field, value)

        await self.session.commit()
        await self.session.refresh(user)
        logger.info(f"Updated user '{user.username}' fields={list(updates.keys())}")
        return user

    async def admin_reset_password(self, user_id: str, new_password: str) -> bool:
        """Admin-initiated password reset (no old password required)."""
        result = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None:
            return False
        user.hashed_password = self.hash_password(new_password)
        await self.session.commit()
        logger.info(f"Admin reset password for user '{user.username}'")
        return True

    async def get_users_paginated(
        self,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
        role_filter: Optional[str] = None,
        tenant_filter: Optional[str] = None,
        status_filter: Optional[str] = None,
    ) -> tuple[list[LLMAuthUser], int]:
        """
        Paginated, filterable user listing.
        Returns (users, total_count).
        """
        from sqlalchemy import func as sqla_func

        q = select(LLMAuthUser)
        count_q = select(sqla_func.count(LLMAuthUser.id))

        # Apply filters
        if search:
            search_pattern = f"%{search}%"
            q = q.where(
                (LLMAuthUser.username.ilike(search_pattern)) |
                (LLMAuthUser.email.ilike(search_pattern))
            )
            count_q = count_q.where(
                (LLMAuthUser.username.ilike(search_pattern)) |
                (LLMAuthUser.email.ilike(search_pattern))
            )

        if role_filter and role_filter != "all":
            q = q.where(LLMAuthUser.role == role_filter)
            count_q = count_q.where(LLMAuthUser.role == role_filter)

        if tenant_filter and tenant_filter != "all":
            q = q.where(LLMAuthUser.tenant_id == tenant_filter)
            count_q = count_q.where(LLMAuthUser.tenant_id == tenant_filter)

        if status_filter == "active":
            q = q.where(LLMAuthUser.is_active == True)
            count_q = count_q.where(LLMAuthUser.is_active == True)
        elif status_filter == "inactive":
            q = q.where(LLMAuthUser.is_active == False)
            count_q = count_q.where(LLMAuthUser.is_active == False)

        # Get total count
        total_result = await self.session.execute(count_q)
        total = total_result.scalar() or 0

        # Apply pagination & ordering
        offset = (page - 1) * page_size
        q = q.order_by(LLMAuthUser.created_at.desc()).offset(offset).limit(page_size)

        result = await self.session.execute(q)
        users = list(result.scalars().all())

        return users, total

    # ================================================================
    # API KEY MANAGEMENT
    # ================================================================

    @staticmethod
    def _hash_api_key(raw_key: str) -> str:
        """BLAKE2b-256 hash of the raw API key."""
        return hashlib.blake2b(raw_key.encode(), digest_size=32).hexdigest()

    async def create_api_key(self, request: ApiKeyCreateRequest,
                             created_by_user: CurrentUser) -> tuple[LLMApiKey, str]:
        """
        Generate a new API key.
        Returns (LLMApiKey ORM object, raw_key_string).
        The raw key is only available at this moment — it is NOT stored.
        """
        raw_key   = "llm_sk_" + secrets.token_hex(24)  # 48 hex chars → 61 total
        key_prefix = raw_key[:12]                        # "llm_sk_XXXX" for display

        from datetime import timedelta
        expires_at = None
        if request.expires_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_days)

        # tenant_admin can only create keys for their own tenant
        tenant_scope = request.tenant_id
        if not created_by_user.is_super_admin():
            tenant_scope = created_by_user.tenant_id

        api_key = LLMApiKey(
            id=str(uuid.uuid4()),
            name=request.name,
            key_prefix=key_prefix,
            hashed_key=self._hash_api_key(raw_key),
            tenant_id=tenant_scope,
            created_by=created_by_user.id,
            expires_at=expires_at,
        )
        self.session.add(api_key)
        await self.session.commit()
        await self.session.refresh(api_key)
        logger.info(f"Created API key '{request.name}' for tenant={tenant_scope}")
        return api_key, raw_key

    async def verify_api_key(self, raw_key: str) -> Optional[LLMApiKey]:
        """
        Verify a raw API key and return the LLMApiKey record if valid.
        Updates last_used_at on success.
        """
        hashed = self._hash_api_key(raw_key)
        result = await self.session.execute(
            select(LLMApiKey).where(
                LLMApiKey.hashed_key == hashed,
                LLMApiKey.is_active  == True,
            )
        )
        key = result.scalar_one_or_none()
        if key is None:
            return None

        # Check expiry
        if key.expires_at and key.expires_at < datetime.now(timezone.utc):
            return None

        # Update last_used_at (best-effort)
        key.last_used_at = datetime.now(timezone.utc)
        await self.session.commit()
        return key

    async def list_api_keys(self, tenant_id: Optional[str] = None,
                            user_id: Optional[str] = None) -> list[LLMApiKey]:
        q = select(LLMApiKey).where(LLMApiKey.is_active == True)
        if tenant_id:
            q = q.where(LLMApiKey.tenant_id == tenant_id)
        if user_id:
            q = q.where(LLMApiKey.created_by == user_id)
        result = await self.session.execute(q.order_by(LLMApiKey.created_at.desc()))
        return list(result.scalars().all())

    async def revoke_api_key(self, key_id: str, requester: CurrentUser) -> bool:
        result = await self.session.execute(
            select(LLMApiKey).where(LLMApiKey.id == key_id)
        )
        key = result.scalar_one_or_none()
        if key is None:
            return False

        # Can only revoke own-tenant keys unless super_admin
        if not requester.is_super_admin() and key.tenant_id != requester.tenant_id:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only revoke keys scoped to your own tenant.",
            )

        key.is_active = False
        await self.session.commit()
        return True

    # ================================================================
    # VIRTUAL KEY MANAGEMENT
    # ================================================================

    async def create_virtual_key(
        self,
        request: VirtualKeyCreateRequest,
        created_by_user: CurrentUser,
    ) -> tuple[LLMVirtualKey, str]:
        """
        Generate a new virtual API key with per-team permissions.

        Key format:
          Live: llm_vk_live_<48 hex chars>
          Test: llm_vk_test_<48 hex chars>

        Returns (LLMVirtualKey ORM object, raw_key_string).
        The raw key is only available at this moment.
        """
        prefix = f"llm_vk_{request.environment}_"
        raw_key = prefix + secrets.token_hex(24)
        key_prefix = raw_key[:16]

        from datetime import timedelta
        expires_at = None
        if request.expires_days:
            expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_days)

        # tenant_admin can only create keys for their own tenant
        tenant_scope = request.tenant_id
        if not created_by_user.is_super_admin():
            tenant_scope = created_by_user.tenant_id

        if not tenant_scope:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id is required. Tenant admins are auto-scoped.",
            )

        # Convert allowed_models list to comma-separated string for storage
        allowed_models_str = None
        if request.allowed_models:
            allowed_models_str = ",".join(request.allowed_models)

        vk = LLMVirtualKey(
            id=str(uuid.uuid4()),
            name=request.name,
            key_prefix=key_prefix,
            key_hash=self._hash_api_key(raw_key),
            tenant_id=tenant_scope,
            created_by=created_by_user.id,
            environment=request.environment,
            allowed_models=allowed_models_str,
            budget_usd=request.budget_usd,
            rate_limit_rpm=request.rate_limit_rpm,
            expires_at=expires_at,
        )
        self.session.add(vk)
        await self.session.commit()
        await self.session.refresh(vk)
        logger.info(
            f"Created virtual key '{request.name}' env={request.environment} "
            f"tenant={tenant_scope} budget={request.budget_usd}"
        )
        return vk, raw_key

    async def verify_virtual_key(self, raw_key: str) -> Optional[LLMVirtualKey]:
        """
        Verify a raw virtual key and return the record if valid.

        Checks: active, not expired, budget not exceeded.
        Updates last_used_at on success.
        """
        hashed = self._hash_api_key(raw_key)
        result = await self.session.execute(
            select(LLMVirtualKey).where(
                LLMVirtualKey.key_hash == hashed,
                LLMVirtualKey.is_active == True,
            )
        )
        vk = result.scalar_one_or_none()
        if vk is None:
            return None

        # Check expiry
        if vk.is_expired:
            return None

        # Check budget (soft check — hard enforcement is in gateway)
        if vk.is_budget_exceeded:
            logger.warning(f"Virtual key {vk.key_prefix} budget exceeded")
            return None

        # Update last_used_at
        vk.last_used_at = datetime.now(timezone.utc)
        await self.session.commit()
        return vk

    async def list_virtual_keys(
        self,
        tenant_id: Optional[str] = None,
        include_revoked: bool = False,
    ) -> list[LLMVirtualKey]:
        """List virtual keys, optionally filtered by tenant."""
        q = select(LLMVirtualKey)
        if not include_revoked:
            q = q.where(LLMVirtualKey.is_active == True)
        if tenant_id:
            q = q.where(LLMVirtualKey.tenant_id == tenant_id)
        result = await self.session.execute(
            q.order_by(LLMVirtualKey.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_virtual_key(self, key_id: str) -> Optional[LLMVirtualKey]:
        """Fetch a single virtual key by ID."""
        result = await self.session.execute(
            select(LLMVirtualKey).where(LLMVirtualKey.id == key_id)
        )
        return result.scalar_one_or_none()

    async def revoke_virtual_key(
        self, key_id: str, requester: CurrentUser
    ) -> bool:
        """Revoke a virtual key (soft delete). Returns True if found."""
        vk = await self.get_virtual_key(key_id)
        if vk is None:
            return False

        if not requester.is_super_admin() and vk.tenant_id != requester.tenant_id:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only revoke keys scoped to your own tenant.",
            )

        vk.is_active = False
        vk.revoked_at = datetime.now(timezone.utc)
        await self.session.commit()
        logger.info(f"Revoked virtual key {vk.key_prefix} by user {requester.username}")
        return True

    async def rotate_virtual_key(
        self, key_id: str, requester: CurrentUser
    ) -> Optional[tuple[LLMVirtualKey, str]]:
        """
        Rotate a virtual key: revoke the old one and create a new one
        with the same permissions.
        """
        old_vk = await self.get_virtual_key(key_id)
        if old_vk is None:
            return None

        if not requester.is_super_admin() and old_vk.tenant_id != requester.tenant_id:
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only rotate keys scoped to your own tenant.",
            )

        # Revoke old key
        old_vk.is_active = False
        old_vk.revoked_at = datetime.now(timezone.utc)

        # Create new key with same permissions
        request = VirtualKeyCreateRequest(
            name=old_vk.name,
            tenant_id=old_vk.tenant_id,
            environment=old_vk.environment,
            allowed_models=old_vk.allowed_models_list or None,
            budget_usd=old_vk.budget_usd,
            rate_limit_rpm=old_vk.rate_limit_rpm,
            expires_days=365,
        )
        new_vk, raw_key = await self.create_virtual_key(request, requester)
        logger.info(
            f"Rotated virtual key {old_vk.key_prefix} → {new_vk.key_prefix} "
            f"by user {requester.username}"
        )
        return new_vk, raw_key

    async def update_virtual_key_budget(
        self, key_id: str, cost_usd: float
    ) -> None:
        """Increment the budget_used_usd on a virtual key after a call."""
        vk = await self.get_virtual_key(key_id)
        if vk is not None:
            vk.budget_used_usd = (vk.budget_used_usd or 0.0) + cost_usd
            await self.session.commit()

    # ================================================================
    # INITIAL ADMIN BOOTSTRAP
    # ================================================================

    async def create_initial_admin(self) -> bool:
        """
        Create the default super_admin if no users exist.
        Called on startup. Password uses ADMIN_PASSWORD from env (default: Admin@1234).
        Returns True if new admin created, False if users already exist.
        """
        result = await self.session.execute(select(LLMAuthUser).limit(1))
        if result.scalar_one_or_none() is not None:
            return False

        admin_pw = settings.admin_password
        admin = LLMAuthUser(
            id=str(uuid.uuid4()),
            username="admin",
            email="admin@llmdashboard.local",
            hashed_password=self.hash_password(admin_pw),
            role="super_admin",
            tenant_id=None,
        )
        self.session.add(admin)
        await self.session.commit()
        
        if admin_pw == "Admin@1234":
            logger.warning(
                "Created initial super_admin with DEFAULT password 'Admin@1234'. "
                "CHANGE THIS PASSWORD IMMEDIATELY or set ADMIN_PASSWORD in .env."
            )
        else:
            logger.info("Created initial super_admin using custom ADMIN_PASSWORD from env.")
            
        return True