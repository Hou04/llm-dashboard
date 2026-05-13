import logging
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from modules.tenants.models import LLMTenant, LLMTenantCredential
from modules.tenants.schemas import TenantCreate, TenantUpdate

logger = logging.getLogger(__name__)

class TenantService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_tenant(self, request: TenantCreate) -> LLMTenant:
        # Check if exists
        result = await self.session.execute(
            select(LLMTenant).where(LLMTenant.tenant_id == request.tenant_id)
        )
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Tenant ID '{request.tenant_id}' already exists."
            )

        tenant = LLMTenant(
            tenant_id=request.tenant_id,
            name=request.name,
            tier=request.tier,
            monthly_budget_usd=request.monthly_budget_usd,
            contact_email=request.contact_email,
        )
        self.session.add(tenant)
        await self.session.commit()
        await self.session.refresh(tenant)
        logger.info(f"Created tenant {tenant.tenant_id} ({tenant.name})")
        return tenant

    async def get_tenant(self, tenant_id: str) -> Optional[LLMTenant]:
        result = await self.session.execute(
            select(LLMTenant).where(LLMTenant.tenant_id == tenant_id)
        )
        return result.scalar_one_or_none()

    async def list_tenants(self) -> List[LLMTenant]:
        result = await self.session.execute(
            select(LLMTenant).order_by(LLMTenant.created_at.desc())
        )
        return list(result.scalars().all())

    async def update_tenant(self, tenant_id: str, request: TenantUpdate) -> LLMTenant:
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")

        if request.name is not None:
            tenant.name = request.name
        if request.tier is not None:
            tenant.tier = request.tier
        if request.monthly_budget_usd is not None:
            tenant.monthly_budget_usd = request.monthly_budget_usd
        if request.contact_email is not None:
            tenant.contact_email = request.contact_email

        await self.session.commit()
        await self.session.refresh(tenant)
        logger.info(f"Updated tenant {tenant_id}")
        return tenant

    async def delete_tenant(self, tenant_id: str) -> None:
        # We might want to just set is_active=False instead of hard delete,
        # but for now, we'll do hard delete or raise error if they have users.
        # Hard deleting can violate foreign key constraints in analytics/auth.
        # It's better to implement an active/inactive flag if needed.
        # For simplicity in this demo:
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found.")
            
        await self.session.delete(tenant)
        await self.session.commit()
        logger.info(f"Deleted tenant {tenant_id}")

    # ================================================================
    # CREDENTIALS
    # ================================================================
    def _get_fernet(self):
        import base64
        import hashlib
        from cryptography.fernet import Fernet
        from core.settings import settings
        
        # Derive a 32-byte key from the app secret_key
        key = hashlib.sha256(settings.secret_key.encode()).digest()
        encoded_key = base64.urlsafe_b64encode(key)
        return Fernet(encoded_key)

    async def set_credential(self, tenant_id: str, provider: str, api_key: str) -> LLMTenantCredential:
        import uuid
        
        fernet = self._get_fernet()
        encrypted = fernet.encrypt(api_key.encode()).decode('utf-8')
        
        # Check if exists
        result = await self.session.execute(
            select(LLMTenantCredential).where(
                LLMTenantCredential.tenant_id == tenant_id,
                LLMTenantCredential.provider == provider
            )
        )
        cred = result.scalar_one_or_none()
        
        if cred:
            cred.encrypted_key = encrypted
        else:
            cred = LLMTenantCredential(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                provider=provider,
                encrypted_key=encrypted
            )
            self.session.add(cred)
            
        await self.session.commit()
        await self.session.refresh(cred)
        return cred

    async def list_credentials(self, tenant_id: str) -> List[LLMTenantCredential]:
        result = await self.session.execute(
            select(LLMTenantCredential).where(LLMTenantCredential.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

    async def delete_credential(self, tenant_id: str, provider: str) -> None:
        result = await self.session.execute(
            select(LLMTenantCredential).where(
                LLMTenantCredential.tenant_id == tenant_id,
                LLMTenantCredential.provider == provider
            )
        )
        cred = result.scalar_one_or_none()
        if cred:
            await self.session.delete(cred)
            await self.session.commit()

    async def get_decrypted_credential(self, tenant_id: str, provider: str) -> Optional[str]:
        """Fetch and decrypt an API key for a specific provider."""
        result = await self.session.execute(
            select(LLMTenantCredential).where(
                LLMTenantCredential.tenant_id == tenant_id,
                LLMTenantCredential.provider == provider
            )
        )
        cred = result.scalar_one_or_none()
        if not cred:
            return None
            
        fernet = self._get_fernet()
        try:
            return fernet.decrypt(cred.encrypted_key.encode()).decode('utf-8')
        except Exception as e:
            logger.error(f"Failed to decrypt credential for {tenant_id}/{provider}: {e}")
            return None

