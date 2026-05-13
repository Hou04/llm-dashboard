"""
M10 Billing — FastAPI router.

Endpoints:
  POST /v1/billing/generate/{tenant_id}/{year_month}
       Generate invoice for one tenant for one month

  POST /v1/billing/generate-all/{year_month}
       Batch: generate invoices for all tenants

  GET  /v1/billing/invoice/{tenant_id}/{year_month}
       Retrieve invoice with line items and report

  GET  /v1/billing/invoice/{tenant_id}/{year_month}/pdf
       Download professional PDF invoice

  GET  /v1/billing/invoices/{tenant_id}
       List all invoices for a tenant

  POST /v1/billing/finalize/{tenant_id}/{year_month}
       Freeze an invoice (irreversible)

  --- Credit Adjustments ---

  POST /v1/billing/credit/{tenant_id}/{year_month}
       Apply credit to a draft invoice

  POST /v1/billing/credit-note/{tenant_id}/{year_month}
       Issue credit note against a finalized invoice

  GET  /v1/billing/credits/{tenant_id}/{year_month}
       List all credits for an invoice

  --- Webhook Export ---

  POST /v1/billing/webhook/test/{tenant_id}
       Test webhook connectivity

  POST /v1/billing/export/{tenant_id}/{year_month}
       Manual export trigger
"""

import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from modules.billing.services.billing_service import BillingService
from modules.billing.services.pdf_service import InvoicePDFService
from modules.billing.services.credit_service import CreditAdjustmentService
from modules.billing.services.webhook_service import BillingWebhookService
from modules.billing.schemas import ApplyCreditRequest, ContractUpdateReq, ContractResponse
from modules.auth.dependencies import require_tenant_viewer, require_super_admin
from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["Billing M10"])


async def get_billing_service(
    session: AsyncSession = Depends(get_db),
) -> BillingService:
    return BillingService(session)


async def get_credit_service(
    session: AsyncSession = Depends(get_db),
) -> CreditAdjustmentService:
    return CreditAdjustmentService(session)


# ============================================================
# INVOICE GENERATION
# ============================================================

@router.post(
    "/generate/{tenant_id}/{year_month}",
    summary="M10 — Generate invoice for one tenant for one month",
)
async def generate_invoice(
    tenant_id:  str,
    year_month: int,
    finalize:   bool = Query(default=False),
    service: BillingService = Depends(get_billing_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Run the full M10 billing pipeline for one tenant.

    year_month format: YYYYMM (e.g. 202603 for March 2026)

    Usage data is computed dynamically from llm_token_log.
    No pre-aggregation or backfill scripts needed.
    """
    result = await service.generate_monthly_invoice(
        tenant_id=tenant_id,
        year_month=year_month,
        finalize=finalize,
    )
    if not result.get("success"):
        raise HTTPException(status_code=422, detail=result.get("error"))
    return result


@router.post(
    "/generate-all/{year_month}",
    summary="M10 — Generate invoices for ALL tenants for one month",
)
async def generate_all_invoices(
    year_month: int,
    finalize:   bool = Query(default=False),
    service: BillingService = Depends(get_billing_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Batch end-of-month processing.
    Generates invoices for all active tenants in one call.
    """
    return await service.generate_all_for_month(
        year_month=year_month,
        finalize=finalize,
    )


# ============================================================
# INVOICE RETRIEVAL
# ============================================================

@router.get(
    "/invoice/{tenant_id}/{year_month}",
    summary="M10 — Get invoice with line items and client report",
)
async def get_invoice(
    tenant_id:  str,
    year_month: int,
    service: BillingService = Depends(get_billing_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    """
    Retrieve a complete invoice including all line items and the
    client report for a given tenant and month.
    """
    user.require_tenant_access(tenant_id)
    result = await service.get_invoice(tenant_id, year_month)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No invoice found for {tenant_id} month {year_month}. "
                "Run POST /v1/billing/generate/{tenant_id}/{year_month} first."
            ),
        )
    return result


@router.get(
    "/invoices/{tenant_id}",
    summary="M10 — List all invoices for a tenant",
)
async def list_invoices(
    tenant_id: str,
    service: BillingService = Depends(get_billing_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    user.require_tenant_access(tenant_id)
    """List all monthly invoices for a tenant, newest first."""
    invoices = await service.list_tenant_invoices(tenant_id)
    return {
        "tenant_id": tenant_id,
        "total":     len(invoices),
        "invoices":  invoices,
    }


# ============================================================
# PDF DOWNLOAD
# ============================================================

@router.get(
    "/invoice/{tenant_id}/{year_month}/pdf",
    summary="M10 — Download professional PDF invoice",
    response_class=StreamingResponse,
)
async def download_invoice_pdf(
    tenant_id:  str,
    year_month: int,
    service: BillingService = Depends(get_billing_service),
    user: CurrentUser = Depends(require_tenant_viewer),
):
    """
    Generate and download a professional PDF invoice.

    Returns a PDF file with:
    - Company branding and invoice metadata
    - Full line items table with transparent breakdown
    - Contract summary (forfait utilisation)
    - Financial totals block
    - Executive summary from client report
    - Payment terms and footer
    """
    user.require_tenant_access(tenant_id)

    invoice_data = await service.get_invoice(tenant_id, year_month)
    if invoice_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No invoice found for {tenant_id} month {year_month}.",
        )

    pdf_service = InvoicePDFService()
    pdf_bytes = pdf_service.generate(invoice_data)

    filename = f"invoice_{tenant_id}_{year_month}.pdf"

    import io
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ============================================================
# FINALIZATION
# ============================================================

@router.post(
    "/finalize/{tenant_id}/{year_month}",
    summary="M10 — Freeze an invoice (irreversible)",
)
async def finalize_invoice(
    tenant_id:  str,
    year_month: int,
    service: BillingService = Depends(get_billing_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Freeze an invoice permanently.

    WARNING: This is irreversible. Once finalized:
    - Numbers cannot be changed
    - To correct an error, issue a credit note (new billing record)
    - The finalized_at timestamp is set permanently
    """
    billing = await service.repo.get_by_tenant_month(tenant_id, year_month)
    if billing is None:
        raise HTTPException(
            status_code=404,
            detail=f"No invoice found for {tenant_id} month {year_month}."
        )
    if billing.status == "finalized":
        return {
            "message":    "Already finalized",
            "tenant_id":  tenant_id,
            "year_month": year_month,
            "finalized_at": billing.finalized_at.isoformat(),
        }

    billing = await service.repo.finalize(billing.id)
    await service.session.commit()

    # Dispatch webhook on finalize
    try:
        invoice_data = await service.get_invoice(tenant_id, year_month)
        await service.webhook.dispatch_invoice_event(
            event_type="invoice.finalized",
            invoice_data=invoice_data,
            tenant_id=tenant_id,
        )
    except Exception as e:
        logger.warning(
            "webhook.dispatch_failed_on_finalize",
            tenant_id=tenant_id,
            error=str(e),
        )

    return {
        "message":      "Invoice finalized successfully",
        "tenant_id":    tenant_id,
        "year_month":   year_month,
        "billing_id":   str(billing.id),
        "status":       billing.status,
        "finalized_at": billing.finalized_at.isoformat(),
        "total_billed_usd": str(billing.total_billed_usd),
    }


# ============================================================
# CREDIT ADJUSTMENTS
# ============================================================

@router.post(
    "/credit/{tenant_id}/{year_month}",
    summary="M10 — Apply credit to a draft invoice",
)
async def apply_credit(
    tenant_id:  str,
    year_month: int,
    body:       ApplyCreditRequest,
    credit_svc: CreditAdjustmentService = Depends(get_credit_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Apply a credit adjustment to a DRAFT invoice.

    Credit types:
    - geste_commercial: commercial gesture / goodwill discount
    - incident_refund: refund for a service outage or bug
    - volume_discount: retrospective volume-based discount
    - promo_credit: promotional or trial credit

    The credit creates a negative line item and reduces the invoice total.
    """
    try:
        return await credit_svc.apply_credit(
            tenant_id=tenant_id,
            year_month=year_month,
            amount=body.amount,
            reason=body.reason,
            credit_type=body.credit_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post(
    "/credit-note/{tenant_id}/{year_month}",
    summary="M10 — Issue credit note against a finalized invoice",
)
async def issue_credit_note(
    tenant_id:  str,
    year_month: int,
    body:       ApplyCreditRequest,
    credit_svc: CreditAdjustmentService = Depends(get_credit_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Issue a credit note against a FINALIZED invoice.

    This creates a new billing record with negative total —
    the original finalized invoice is NEVER modified.

    The original invoice status changes to 'corrected'.
    """
    try:
        result = await credit_svc.issue_credit_note(
            tenant_id=tenant_id,
            year_month=year_month,
            amount=body.amount,
            reason=body.reason,
            credit_type=body.credit_type,
        )

        # Dispatch webhook for credit note
        try:
            webhook_svc = BillingWebhookService()
            await webhook_svc.dispatch_invoice_event(
                event_type="credit_note.issued",
                invoice_data=result,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.warning("webhook.credit_note_dispatch_failed", error=str(e))

        return result
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get(
    "/credits/{tenant_id}/{year_month}",
    summary="M10 — List all credits for an invoice",
)
async def list_credits(
    tenant_id:  str,
    year_month: int,
    credit_svc: CreditAdjustmentService = Depends(get_credit_service),
    user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    """List all credit adjustments applied to a specific invoice."""
    user.require_tenant_access(tenant_id)
    credits = await credit_svc.list_credits(tenant_id, year_month)
    return {
        "tenant_id":  tenant_id,
        "year_month": year_month,
        "total":      len(credits),
        "credits":    credits,
    }


# ============================================================
# WEBHOOK EXPORT
# ============================================================

@router.post(
    "/webhook/test/{tenant_id}",
    summary="M10 — Test webhook connectivity for a tenant",
)
async def test_webhook(
    tenant_id: str,
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Send a test ping to the tenant's configured webhook endpoint.
    Returns connectivity status without sending real invoice data.
    """
    webhook_svc = BillingWebhookService()
    result = await webhook_svc.test_webhook(tenant_id)
    return {
        "success":   result.get("dispatched", result.get("success", False)),
        "tenant_id": tenant_id,
        "result":    result,
    }


@router.post(
    "/export/{tenant_id}/{year_month}",
    summary="M10 — Manual export invoice to ERP/CRM webhook",
)
async def export_invoice(
    tenant_id:  str,
    year_month: int,
    service: BillingService = Depends(get_billing_service),
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Manually trigger a webhook export for an existing invoice.

    Useful for re-sending invoices that failed webhook delivery,
    or for initial ERP sync when webhooks are first configured.
    """
    invoice_data = await service.get_invoice(tenant_id, year_month)
    if invoice_data is None:
        raise HTTPException(
            status_code=404,
            detail=f"No invoice found for {tenant_id} month {year_month}.",
        )

    webhook_svc = BillingWebhookService()
    result = await webhook_svc.dispatch_invoice_event(
        event_type="invoice.exported",
        invoice_data=invoice_data,
        tenant_id=tenant_id,
    )

    return {
        "success":    result.get("dispatched", False),
        "tenant_id":  tenant_id,
        "year_month": year_month,
        "event_type": "invoice.exported",
        "result":     result,
    }


@router.get(
    "/webhook/config/{tenant_id}",
    summary="M10 — Get webhook configuration for a tenant",
)
async def get_webhook_config(
    tenant_id: str,
    _user: CurrentUser = Depends(require_super_admin),
) -> dict:
    """
    Return the webhook configuration for a tenant.
    The signing secret is redacted for security.
    """
    webhook_svc = BillingWebhookService()
    config = webhook_svc.get_webhook_config(tenant_id)
    if config is None:
        raise HTTPException(
            status_code=404,
            detail=f"No webhook configuration found for {tenant_id}.",
        )
    return config


# ============================================================
# CONTRACT MANAGEMENT
# ============================================================

@router.get(
    "/contract/{tenant_id}",
    summary="M10 — Get billing contract for a tenant",
    response_model=ContractResponse,
)
async def get_contract(
    tenant_id: str,
    service: BillingService = Depends(get_billing_service),
    user: CurrentUser = Depends(require_tenant_viewer),
):
    user.require_tenant_access(tenant_id)
    from modules.billing.repositories.contract_repository import ContractRepository
    repo = ContractRepository(service.session)
    contract = await repo.get_contract(tenant_id)
    if not contract:
        raise HTTPException(status_code=404, detail="No contract found for this tenant")
    return contract


@router.post(
    "/contract/{tenant_id}",
    summary="M10 — Update billing contract for a tenant",
    response_model=ContractResponse,
)
async def update_contract(
    tenant_id: str,
    payload: ContractUpdateReq,
    service: BillingService = Depends(get_billing_service),
    _user: CurrentUser = Depends(require_super_admin),
):
    from modules.billing.repositories.contract_repository import ContractRepository
    repo = ContractRepository(service.session)
    contract = await repo.upsert(
        tenant_id=tenant_id,
        contract_type=payload.contract_type,
        status=payload.status,
        base_fee_usd=payload.base_fee_usd,
        forfait_tokens=payload.forfait_tokens,
        overage_rate_per_1k=payload.overage_rate_per_1k,
        description=payload.description
    )
    await service.session.commit()
    return contract


@router.post(
    "/contract/{tenant_id}/accept",
    summary="M10 — Accept a proposed billing contract",
    response_model=ContractResponse,
)
async def accept_contract(
    tenant_id: str,
    service: BillingService = Depends(get_billing_service),
    user: CurrentUser = Depends(require_tenant_viewer),
):
    """
    Tenant Admin accepts a proposed contract.
    Changes status from 'proposed' to 'active'.
    """
    user.require_tenant_access(tenant_id)
    from modules.billing.repositories.contract_repository import ContractRepository
    repo = ContractRepository(service.session)
    
    contract = await repo.get_contract(tenant_id)
    if not contract:
        raise HTTPException(status_code=404, detail="No contract found for this tenant")
    
    if contract.status != "proposed":
        raise HTTPException(
            status_code=400, 
            detail=f"Only 'proposed' contracts can be accepted. Current status: {contract.status}"
        )
        
    contract = await repo.accept_contract(tenant_id)
    await service.session.commit()
    return contract


@router.post(
    "/contract/{tenant_id}/reject",
    summary="M10 — Reject a proposed billing contract",
    response_model=ContractResponse,
)
async def reject_contract(
    tenant_id: str,
    service: BillingService = Depends(get_billing_service),
    user: CurrentUser = Depends(require_tenant_viewer),
):
    """Tenant Admin rejects a proposed contract."""
    user.require_tenant_access(tenant_id)
    from modules.billing.repositories.contract_repository import ContractRepository
    repo = ContractRepository(service.session)
    
    contract = await repo.get_contract(tenant_id)
    if not contract:
        raise HTTPException(status_code=404, detail="No contract found for this tenant")
        
    contract = await repo.reject_contract(tenant_id)
    await service.session.commit()
    return contract