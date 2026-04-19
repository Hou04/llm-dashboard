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
from modules.auth.models import LLMApiKey, LLMAuthUser
from modules.auth.schemas import ApiKeyCreateRequest, CurrentUser, UserCreateRequest

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
            pwd_context.hash("dummy_to_prevent_timing_attack")
            return None

        if not self.verify_password(password, user.hashed_password):
            return None

        user.last_login_at = datetime.now(timezone.utc)
        await self.session.commit()
        return user

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

    async def create_user(self, request: UserCreateRequest,
                          created_by_role: str = "super_admin") -> LLMAuthUser:
        if request.role == "super_admin" and created_by_role != "super_admin":
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only super_admin can create super_admin users.",
            )

        # Check duplicate username
        exists = await self.session.execute(
            select(LLMAuthUser).where(LLMAuthUser.username == request.username)
        )
        if exists.scalar_one_or_none() is not None:
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
    # INITIAL ADMIN BOOTSTRAP
    # ================================================================

    async def create_initial_admin(self) -> bool:
        """
        Create the default super_admin if no users exist.
        Called on startup. Password: 'Admin@1234' — MUST be changed.
        Returns True if new admin created, False if users already exist.
        """
        result = await self.session.execute(select(LLMAuthUser).limit(1))
        if result.scalar_one_or_none() is not None:
            return False

        admin = LLMAuthUser(
            id=str(uuid.uuid4()),
            username="admin",
            email="admin@llmdashboard.local",
            hashed_password=self.hash_password("Admin@1234"),
            role="super_admin",
            tenant_id=None,
        )
        self.session.add(admin)
        await self.session.commit()
        logger.warning(
            "Created initial super_admin  (username='admin', password='Admin@1234'). "
            "CHANGE THIS PASSWORD IMMEDIATELY via POST /v1/auth/change-password."
        )
        return True