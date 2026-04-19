"""
CreditAdjustmentService — geste commercial, refunds, and credit notes.

Two modes of operation:

  1. APPLY CREDIT to a DRAFT invoice:
     Adds a negative line item and recalculates the invoice total.
     The invoice must still be in "draft" status.

  2. ISSUE CREDIT NOTE against a FINALIZED invoice:
     Creates a brand-new billing record with:
       - status = "credit_note"
       - negative total_billed_usd
       - links to the original invoice via notes
     The original finalized invoice is NEVER modified
     (required for financial auditing).

Credit types:
  geste_commercial — commercial gesture, goodwill discount
  incident_refund  — refund for a service incident or outage
  volume_discount  — retrospective volume-based discount
  promo_credit     — promotional credit / trial credit

All credit amounts are stored as POSITIVE Decimals in the database.
The line_item.amount_usd is NEGATIVE to indicate a credit.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from modules.billing.repositories.billing_repository import BillingRepository

logger = logging.getLogger(__name__)

VALID_CREDIT_TYPES = {
    "geste_commercial",
    "incident_refund",
    "volume_discount",
    "promo_credit",
}


class CreditAdjustmentService:
    """Apply credits and issue credit notes for billing records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo    = BillingRepository(session)

    # ================================================================
    # PUBLIC — APPLY CREDIT TO DRAFT
    # ================================================================

    async def apply_credit(
        self,
        tenant_id:   str,
        year_month:  int,
        amount:      Decimal,
        reason:      str,
        credit_type: str = "geste_commercial",
    ) -> dict:
        """
        Apply a credit adjustment to a DRAFT invoice.

        Creates a credit line item with negative amount and recalculates
        the invoice totals.

        Args:
            tenant_id:   Target tenant
            year_month:  Invoice month (YYYYMM)
            amount:      Credit amount as POSITIVE Decimal
            reason:      Human-readable reason (required)
            credit_type: One of VALID_CREDIT_TYPES

        Returns:
            dict with updated invoice summary

        Raises:
            ValueError on validation failure
        """
        # ── Validation ────────────────────────────────────────────
        self._validate_credit_input(amount, reason, credit_type)

        # ── Load invoice ──────────────────────────────────────────
        billing = await self.repo.get_by_tenant_month(tenant_id, year_month)
        if billing is None:
            raise ValueError(
                f"No invoice found for {tenant_id} month {year_month}. "
                "Generate the invoice first."
            )
        if billing.status == "finalized":
            raise ValueError(
                f"Invoice for {tenant_id} month {year_month} is already finalized. "
                "Use issue_credit_note() to create a credit note instead."
            )
        if billing.status == "credit_note":
            raise ValueError("Cannot apply credits to a credit note record.")

        # ── Check credit does not exceed invoice total ────────────
        current_total = billing.total_billed_usd
        if amount > current_total:
            raise ValueError(
                f"Credit amount ${amount} exceeds invoice total "
                f"${current_total}. Maximum allowed: ${current_total}."
            )

        # ── Create credit line item ──────────────────────────────
        month_name = self._month_name(year_month)
        credit_line = {
            "billing_id":      billing.id,
            "tenant_id":       tenant_id,
            "year_month":      year_month,
            "line_type":       "credit",
            "description":     f"Credit: {reason} — {month_name}",
            "model":           None,
            "provider":        None,
            "agent_id":        None,
            "quantity_tokens": 0,
            "quantity_calls":  0,
            "unit_price_usd":  amount,
            "amount_usd":      -amount,   # NEGATIVE — it's a credit
            "credit_type":     credit_type,
        }

        # Add the line item (don't use create_line_items which deletes first)
        from modules.billing.models import LLMBillingLineItem
        record = LLMBillingLineItem(**credit_line)
        self.session.add(record)
        await self.session.flush()

        # ── Recalculate invoice totals ────────────────────────────
        new_credits   = billing.credits_usd + amount
        new_total     = billing.base_fee_usd + billing.overage_charge_usd - new_credits

        # Ensure total doesn't go negative
        if new_total < 0:
            new_total = Decimal("0")

        await self.repo.update_billing(billing.id, {
            "credits_usd":     new_credits,
            "total_billed_usd": new_total,
        })

        await self.session.commit()

        logger.info(
            "credit.applied",
            tenant_id=tenant_id,
            year_month=year_month,
            amount=str(amount),
            credit_type=credit_type,
            reason=reason,
            new_total=str(new_total),
        )

        return {
            "success":         True,
            "action":          "credit_applied",
            "tenant_id":       tenant_id,
            "year_month":      year_month,
            "billing_id":      str(billing.id),
            "credit_amount":   str(amount),
            "credit_type":     credit_type,
            "reason":          reason,
            "previous_total":  str(current_total),
            "new_credits":     str(new_credits),
            "new_total":       str(new_total),
        }

    # ================================================================
    # PUBLIC — ISSUE CREDIT NOTE AGAINST FINALIZED INVOICE
    # ================================================================

    async def issue_credit_note(
        self,
        tenant_id:   str,
        year_month:  int,
        amount:      Decimal,
        reason:      str,
        credit_type: str = "geste_commercial",
    ) -> dict:
        """
        Issue a credit note against a FINALIZED invoice.

        Creates a new billing record with:
          - status = "credit_note"
          - negative total_billed_usd
          - reference to the original invoice in notes

        The original finalized invoice is NEVER modified.

        Args:
            tenant_id:   Target tenant
            year_month:  Original invoice month (YYYYMM)
            amount:      Credit amount as POSITIVE Decimal
            reason:      Human-readable reason (required)
            credit_type: One of VALID_CREDIT_TYPES

        Returns:
            dict with credit note details
        """
        # ── Validation ────────────────────────────────────────────
        self._validate_credit_input(amount, reason, credit_type)

        # ── Load original invoice ─────────────────────────────────
        original = await self.repo.get_by_tenant_month(tenant_id, year_month)
        if original is None:
            raise ValueError(
                f"No invoice found for {tenant_id} month {year_month}."
            )
        if original.status not in ("finalized", "corrected"):
            raise ValueError(
                f"Credit notes can only be issued against finalized invoices. "
                f"Current status: {original.status}. "
                f"For draft invoices, use apply_credit() instead."
            )
        if amount > original.total_billed_usd:
            raise ValueError(
                f"Credit note amount ${amount} exceeds original invoice total "
                f"${original.total_billed_usd}."
            )

        # ── Create credit note billing record ─────────────────────
        # Use a slightly different year_month to avoid unique constraint
        # Format: original year_month * 100 + sequence (e.g. 20260300 + 1)
        cn_year_month = await self._next_credit_note_ym(tenant_id, year_month)

        from modules.billing.models import LLMBillingMonthly, LLMBillingLineItem
        credit_note = LLMBillingMonthly(
            tenant_id=tenant_id,
            year_month=cn_year_month,
            total_calls=0,
            total_tokens=0,
            total_input_tokens=0,
            total_output_tokens=0,
            raw_cost_usd=Decimal("0"),
            forfait_tokens_included=0,
            forfait_tokens_used=0,
            overage_tokens=0,
            base_fee_usd=Decimal("0"),
            overage_charge_usd=Decimal("0"),
            credits_usd=amount,
            total_billed_usd=-amount,   # NEGATIVE — credit note
            status="credit_note",
            notes=(
                f"Credit note for invoice {year_month} — "
                f"Original billing ID: {original.id} — "
                f"Reason: {reason}"
            ),
            snapshot_taken_at=datetime.now(timezone.utc),
        )
        self.session.add(credit_note)
        await self.session.flush()
        await self.session.refresh(credit_note)

        # ── Create credit line item ──────────────────────────────
        month_name = self._month_name(year_month)
        credit_line = LLMBillingLineItem(
            billing_id=credit_note.id,
            tenant_id=tenant_id,
            year_month=cn_year_month,
            line_type="credit",
            description=(
                f"Credit Note: {reason} — against {month_name} invoice"
            ),
            model=None,
            provider=None,
            agent_id=None,
            quantity_tokens=0,
            quantity_calls=0,
            unit_price_usd=amount,
            amount_usd=-amount,
            credit_type=credit_type,
        )
        self.session.add(credit_line)

        # ── Mark original as corrected ────────────────────────────
        if original.status == "finalized":
            original.status = "corrected"

        await self.session.flush()
        await self.session.commit()

        logger.info(
            "credit_note.issued",
            tenant_id=tenant_id,
            year_month=year_month,
            cn_year_month=cn_year_month,
            amount=str(amount),
            credit_type=credit_type,
            reason=reason,
            original_billing_id=str(original.id),
            credit_note_id=str(credit_note.id),
        )

        return {
            "success":                True,
            "action":                 "credit_note_issued",
            "tenant_id":             tenant_id,
            "original_year_month":   year_month,
            "original_billing_id":   str(original.id),
            "credit_note_id":        str(credit_note.id),
            "credit_note_year_month":cn_year_month,
            "credit_amount":         str(amount),
            "credit_type":           credit_type,
            "reason":                reason,
            "original_status":       original.status,
        }

    # ================================================================
    # PUBLIC — LIST CREDITS
    # ================================================================

    async def list_credits(
        self, tenant_id: str, year_month: int
    ) -> list[dict]:
        """List all credit line items for a specific invoice."""
        billing = await self.repo.get_by_tenant_month(tenant_id, year_month)
        if billing is None:
            return []

        items = await self.repo.get_credit_line_items(billing.id)
        return [
            {
                "id":            str(li.id),
                "line_type":     li.line_type,
                "description":   li.description,
                "credit_type":   getattr(li, "credit_type", None),
                "amount_usd":    str(li.amount_usd),
                "created_at":    li.created_at.isoformat(),
            }
            for li in items
        ]

    # ================================================================
    # PRIVATE — VALIDATION
    # ================================================================

    @staticmethod
    def _validate_credit_input(
        amount: Decimal, reason: str, credit_type: str
    ) -> None:
        """Validate credit input parameters."""
        if amount <= 0:
            raise ValueError("Credit amount must be a positive value.")
        if not reason or len(reason.strip()) < 3:
            raise ValueError("A meaningful reason is required (min 3 characters).")
        if credit_type not in VALID_CREDIT_TYPES:
            raise ValueError(
                f"Invalid credit_type: '{credit_type}'. "
                f"Must be one of: {', '.join(sorted(VALID_CREDIT_TYPES))}"
            )

    async def _next_credit_note_ym(
        self, tenant_id: str, original_ym: int
    ) -> int:
        """
        Generate a unique year_month for a credit note.

        Credit notes use the pattern: original_ym * 100 + sequence.
        e.g. 202603 → 20260301, 20260302, ...

        This avoids clashing with the unique constraint on (tenant_id, year_month).
        """
        from sqlalchemy import text
        result = await self.session.execute(
            text("""
                SELECT MAX(year_month)
                FROM llm_billing_monthly
                WHERE tenant_id = :tenant_id
                  AND year_month >= :base
                  AND year_month < :ceiling
            """),
            {
                "tenant_id": tenant_id,
                "base":      original_ym * 100,
                "ceiling":   original_ym * 100 + 100,
            },
        )
        max_ym = result.scalar()
        if max_ym is None:
            return original_ym * 100 + 1
        return max_ym + 1

    @staticmethod
    def _month_name(year_month: int) -> str:
        """Convert 202603 → 'March 2026'."""
        import calendar
        year  = year_month // 100
        month = year_month % 100
        if 1 <= month <= 12:
            return f"{calendar.month_name[month]} {year}"
        return str(year_month)
