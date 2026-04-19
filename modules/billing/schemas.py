"""
Billing module (M10) — Pydantic schemas for HTTP request/response contracts.

These match the dict structure returned by BillingService methods.
All monetary values are strings to avoid float precision loss.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# LINE ITEMS
# ============================================================

class BillingLineItemResponse(BaseModel):
    """One charge line within a monthly invoice."""
    id: uuid.UUID
    line_type: str = Field(
        description="base_fee | usage_cost | overage | credit | premium"
    )
    description: str
    model: Optional[str] = None
    provider: Optional[str] = None
    quantity_tokens: int
    quantity_calls: int
    unit_price_usd: str
    amount_usd: str

    model_config = {"from_attributes": True}


# ============================================================
# REPORT
# ============================================================

class ClientReportResponse(BaseModel):
    """Embedded client report within an invoice response."""
    title: Optional[str] = None
    executive_summary: Optional[str] = None
    generated_at: Optional[str] = None


# ============================================================
# INVOICE DETAIL (full)
# ============================================================

class InvoiceDetailResponse(BaseModel):
    """
    Full invoice response — returned by GET /v1/billing/invoice/{tenant_id}/{year_month}.

    Contains the invoice header, all line items, and the client report.
    """
    invoice: dict = Field(
        description="Invoice header with all financial totals"
    )
    line_items: list[BillingLineItemResponse]
    report: Optional[ClientReportResponse] = None

    model_config = {"from_attributes": True}


# ============================================================
# INVOICE LIST (compact rows)
# ============================================================

class InvoiceListItem(BaseModel):
    """One row in the invoice list — returned by GET /v1/billing/invoices/{tenant_id}."""
    id: uuid.UUID
    year_month: int = Field(description="Format YYYYMM e.g. 202603")
    status: str = Field(
        description="draft | finalized | disputed | corrected"
    )
    total_billed_usd: str
    total_tokens: int
    total_calls: int
    finalized_at: Optional[str] = None

    model_config = {"from_attributes": True}


class InvoiceListResponse(BaseModel):
    """Response for GET /v1/billing/invoices/{tenant_id}."""
    tenant_id: str
    total: int
    invoices: list[InvoiceListItem]


# ============================================================
# GENERATE INVOICE RESPONSE
# ============================================================

class GenerateInvoiceResponse(BaseModel):
    """
    Response for POST /v1/billing/generate/{tenant_id}/{year_month}.

    Summary returned after running the billing pipeline.
    """
    success: bool
    tenant_id: str
    year_month: int
    billing_id: uuid.UUID
    status: str
    total_billed_usd: str
    total_calls: int
    total_tokens: int
    raw_cost_usd: str
    base_fee_usd: str
    overage_charge_usd: str
    line_items_count: int
    contract_type: str

    model_config = {"from_attributes": True}


# ============================================================
# FINALIZE RESPONSE
# ============================================================

class FinalizeInvoiceResponse(BaseModel):
    """Response for POST /v1/billing/finalize/{tenant_id}/{year_month}."""
    message: str
    tenant_id: str
    year_month: int
    billing_id: uuid.UUID
    status: str
    finalized_at: str
    total_billed_usd: str

    model_config = {"from_attributes": True}


# ============================================================
# CREDIT ADJUSTMENTS
# ============================================================

class ApplyCreditRequest(BaseModel):
    """Request body for applying a credit or issuing a credit note."""
    amount: Decimal = Field(
        gt=0,
        description="Credit amount in USD (positive value)"
    )
    reason: str = Field(
        min_length=3,
        max_length=300,
        description="Human-readable reason for the credit"
    )
    credit_type: str = Field(
        default="geste_commercial",
        description="geste_commercial | incident_refund | volume_discount | promo_credit"
    )


class CreditNoteResponse(BaseModel):
    """Response after applying a credit or issuing a credit note."""
    success: bool
    action: str = Field(
        description="credit_applied | credit_note_issued"
    )
    tenant_id: str
    year_month: Optional[int] = None
    billing_id: Optional[str] = None
    credit_amount: str
    credit_type: str
    reason: str
    previous_total: Optional[str] = None
    new_credits: Optional[str] = None
    new_total: Optional[str] = None
    original_billing_id: Optional[str] = None
    credit_note_id: Optional[str] = None

    model_config = {"from_attributes": True}


class CreditLineItem(BaseModel):
    """One credit line item."""
    id: str
    line_type: str
    description: str
    credit_type: Optional[str] = None
    amount_usd: str
    created_at: str


class CreditListResponse(BaseModel):
    """Response for listing credits on an invoice."""
    tenant_id: str
    year_month: int
    total: int
    credits: list[CreditLineItem]
