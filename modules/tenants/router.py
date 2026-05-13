import logging
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.auth.dependencies import require_super_admin
from modules.auth.schemas import CurrentUser
from modules.tenants.schemas import TenantCreate, TenantUpdate, TenantResponse
from modules.tenants.service import TenantService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/tenants", tags=["Tenants"])

async def get_tenant_service(session: AsyncSession = Depends(get_db)) -> TenantService:
    return TenantService(session)

@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tenant (super_admin only)"
)
async def create_tenant(
    request: TenantCreate,
    service: TenantService = Depends(get_tenant_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> TenantResponse:
    tenant = await service.create_tenant(request)
    return TenantResponse.model_validate(tenant)

@router.get(
    "",
    response_model=List[TenantResponse],
    summary="List all tenants (super_admin only)"
)
async def list_tenants(
    service: TenantService = Depends(get_tenant_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> List[TenantResponse]:
    tenants = await service.list_tenants()
    return [TenantResponse.model_validate(t) for t in tenants]

@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get tenant details (super_admin only)"
)
async def get_tenant(
    tenant_id: str,
    service: TenantService = Depends(get_tenant_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> TenantResponse:
    from fastapi import HTTPException
    tenant = await service.get_tenant(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return TenantResponse.model_validate(tenant)

@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update tenant details (super_admin only)"
)
async def update_tenant(
    tenant_id: str,
    request: TenantUpdate,
    service: TenantService = Depends(get_tenant_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> TenantResponse:
    tenant = await service.update_tenant(tenant_id, request)
    return TenantResponse.model_validate(tenant)

@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tenant (super_admin only)"
)
async def delete_tenant(
    tenant_id: str,
    service: TenantService = Depends(get_tenant_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> None:
    await service.delete_tenant(tenant_id)


# ============================================================
# CREDENTIALS
# ============================================================

from modules.auth.dependencies import require_tenant_admin
from modules.tenants.schemas import TenantCredentialCreate, TenantCredentialResponse

@router.post(
    "/{tenant_id}/credentials",
    response_model=TenantCredentialResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Set LLM provider credential for a tenant (tenant_admin only)"
)
async def set_tenant_credential(
    tenant_id: str,
    request: TenantCredentialCreate,
    service: TenantService = Depends(get_tenant_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> TenantCredentialResponse:
    user.require_tenant_access(tenant_id)
    cred = await service.set_credential(tenant_id, request.provider, request.api_key)
    return TenantCredentialResponse.model_validate(cred)

@router.get(
    "/{tenant_id}/credentials",
    response_model=List[TenantCredentialResponse],
    summary="List configured credentials for a tenant (tenant_admin only)"
)
async def list_tenant_credentials(
    tenant_id: str,
    service: TenantService = Depends(get_tenant_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> List[TenantCredentialResponse]:
    user.require_tenant_access(tenant_id)
    creds = await service.list_credentials(tenant_id)
    return [TenantCredentialResponse.model_validate(c) for c in creds]

@router.delete(
    "/{tenant_id}/credentials/{provider}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a provider credential (tenant_admin only)"
)
async def delete_tenant_credential(
    tenant_id: str,
    provider: str,
    service: TenantService = Depends(get_tenant_service),
    user: CurrentUser = Depends(require_tenant_admin),
) -> None:
    user.require_tenant_access(tenant_id)
    await service.delete_credential(tenant_id, provider)
