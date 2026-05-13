from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime

class TenantBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    tier: str = Field(default="pay_as_you_go", description="E.g., free, pay_as_you_go, enterprise")
    monthly_budget_usd: float = Field(default=100.0, ge=0.0)
    contact_email: Optional[EmailStr] = None

class TenantCreate(TenantBase):
    tenant_id: str = Field(..., min_length=3, max_length=50, description="Unique identifier like 'acme_corp'")

class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=100)
    tier: Optional[str] = None
    monthly_budget_usd: Optional[float] = Field(None, ge=0.0)
    contact_email: Optional[EmailStr] = None

class TenantResponse(TenantBase):
    tenant_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class TenantCredentialCreate(BaseModel):
    provider: str = Field(..., description="e.g. openai, anthropic, google")
    api_key: str = Field(..., description="The raw API key from the provider")

class TenantCredentialResponse(BaseModel):
    id: str
    tenant_id: str
    provider: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
