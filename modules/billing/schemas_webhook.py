"""
Webhook schemas — Pydantic models for billing webhook payloads.
"""

from typing import Optional
from pydantic import BaseModel, Field


class WebhookConfigResponse(BaseModel):
    """Webhook configuration for a tenant (secret redacted)."""
    tenant_id: str
    enabled: bool
    url: str
    events: list[str]
    secret: Optional[str] = Field(
        description="Redacted webhook signing secret"
    )


class WebhookDispatchResult(BaseModel):
    """Result of a webhook dispatch attempt."""
    dispatched: bool
    status_code: Optional[int] = None
    attempts: Optional[int] = None
    url: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None


class WebhookTestResponse(BaseModel):
    """Response from a webhook connectivity test."""
    success: bool
    tenant_id: str
    result: WebhookDispatchResult


class ManualExportResponse(BaseModel):
    """Response from a manual invoice export trigger."""
    success: bool
    tenant_id: str
    year_month: int
    event_type: str
    result: WebhookDispatchResult
