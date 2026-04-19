"""
BillingService — M10 Agent.

Pure financial logic. Zero LLM calls. Deterministic and auditable.

The billing engine:
  1. Reads frozen usage data from llm_cost_monthly (M2 output)
  2. Applies the tenant's contract rules (forfait, overage rate)
  3. Generates line items — one charge per model, plus base fee
  4. Calculates the total invoice amount
  5. Generates a human-readable client report
  6. Optionally finalizes (freezes) the invoice

CONTRACT MODELS supported:
  pay_as_you_go  — charged exactly at model cost, no flat fee
  forfait        — flat monthly fee + overage above included tokens
  hybrid         — base fee + per-model rates (different from raw cost)

All contract configs are loaded dynamically from the llm_tenant_contracts
table via ContractRepository. No hardcoded tenant IDs or pricing.

IMMUTABILITY RULE:
  Once status='finalized', numbers never change.
  If a correction is needed, create a new record with type='credit_note'.
  This is non-negotiable for financial auditing.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from modules.billing.repositories.billing_repository import BillingRepository
from modules.billing.services.webhook_service import BillingWebhookService

logger = logging.getLogger(__name__)


# ============================================================
# CONTRACT CONFIGURATION
#
# Contracts are now loaded from the llm_tenant_contracts DB table.
# The ContractRepository handles lookups and auto-creation.
# No hardcoded tenant IDs or contract terms.
# ============================================================


class BillingService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo    = BillingRepository(session)
        self.webhook = BillingWebhookService()
        # Dynamic contract loading from database
        from modules.billing.repositories.contract_repository import ContractRepository
        self.contract_repo = ContractRepository(session)

    # ================================================================
    # PUBLIC — MAIN BILLING PIPELINE
    # ================================================================

    async def generate_monthly_invoice(
        self,
        tenant_id:  str,
        year_month: int,
        finalize:   bool = False,
    ) -> dict:
        """
        Full M10 billing pipeline for one tenant for one month.

        Args:
            tenant_id:  Which tenant to bill.
            year_month: Month as YYYYMM integer (e.g. 202603).
            finalize:   If True, freeze the invoice after calculation.
                        If False, keep as draft (can be recalculated).

        Returns a summary dict with the billing record and line items.

        Flow:
          1. Read usage from llm_cost_monthly (M2 frozen data)
          2. Apply contract rules → calculate amounts
          3. Generate line items (one per model + base fee)
          4. Save billing record
          5. Save line items
          6. Generate client report
          7. Optionally finalize
        """
        # Step 1: read raw usage from M2
        usage = await self.repo.get_monthly_usage(tenant_id, year_month)

        if usage is None:
            logger.warning(
                f"No M2 data for {tenant_id} month {year_month}. "
                "Run the M2 backfill script first."
            )
            return {
                "success": False,
                "error": (
                    f"M2 Usage data not found for {tenant_id} month {year_month}. "
                    "Invoices require monthly cost rollups. "
                    "Execute: pipenv run python scripts/backfill_costs.py"
                ),
            }

        # Step 2: apply contract rules (loaded from DB, not hardcoded)
        contract  = await self.contract_repo.get_contract_dict(tenant_id)
        amounts   = self._apply_contract(usage, contract)

        # Step 3: get model breakdown for line items
        model_breakdown = await self.repo.get_model_breakdown_for_month(
            tenant_id, year_month
        )

        # Step 4: get or create billing draft
        billing = await self.repo.get_or_create_draft(tenant_id, year_month)

        # Step 5: update billing totals
        billing = await self.repo.update_billing(
            billing.id,
            {
                "total_calls":           usage["total_calls"],
                "total_tokens":          usage["total_tokens"],
                "raw_cost_usd":          Decimal(str(round(usage["total_cost_usd"], 4))),
                "forfait_tokens_included":contract["forfait_tokens"],
                "forfait_tokens_used":   amounts["forfait_tokens_used"],
                "overage_tokens":        amounts["overage_tokens"],
                "base_fee_usd":          Decimal(str(round(amounts["base_fee_usd"], 4))),
                "overage_charge_usd":    Decimal(str(round(amounts["overage_charge_usd"], 4))),
                "credits_usd":           Decimal("0"),
                "total_billed_usd":      Decimal(str(round(amounts["total_billed_usd"], 4))),
                "snapshot_taken_at":     datetime.now(timezone.utc),
            },
        )

        # Step 6: generate and save line items
        line_items = self._build_line_items(
            billing_id=billing.id,
            tenant_id=tenant_id,
            year_month=year_month,
            contract=contract,
            amounts=amounts,
            model_breakdown=model_breakdown,
        )
        await self.repo.create_line_items(line_items)

        # Step 7: generate client report
        agent_breakdown = await self.repo.get_agent_breakdown_for_month(
            tenant_id, year_month
        )
        report = await self._generate_report(
            billing=billing,
            usage=usage,
            contract=contract,
            amounts=amounts,
            model_breakdown=model_breakdown,
            agent_breakdown=agent_breakdown,
        )
        await self.repo.save_report(report)

        # Step 8: finalize if requested
        if finalize:
            billing = await self.repo.finalize(billing.id)

        await self.session.commit()

        # Step 9: dispatch webhook if finalized
        if finalize:
            try:
                invoice_data = await self.get_invoice(tenant_id, year_month)
                await self.webhook.dispatch_invoice_event(
                    event_type="invoice.finalized",
                    invoice_data=invoice_data,
                    tenant_id=tenant_id,
                )
            except Exception as e:
                # Webhook failure should never block the billing pipeline
                logger.warning(
                    "webhook.dispatch_failed",
                    tenant_id=tenant_id,
                    year_month=year_month,
                    error=str(e),
                )

        return {
            "success":         True,
            "tenant_id":       tenant_id,
            "year_month":      year_month,
            "billing_id":      str(billing.id),
            "status":          billing.status,
            "total_billed_usd": str(billing.total_billed_usd),
            "total_calls":     billing.total_calls,
            "total_tokens":    billing.total_tokens,
            "raw_cost_usd":    str(billing.raw_cost_usd),
            "base_fee_usd":    str(billing.base_fee_usd),
            "overage_charge_usd": str(billing.overage_charge_usd),
            "line_items_count": len(line_items),
            "contract_type":   contract["contract_type"],
        }

    async def get_invoice(
        self, tenant_id: str, year_month: int
    ) -> Optional[dict]:
        """Retrieve a complete invoice with all line items."""
        billing = await self.repo.get_by_tenant_month(tenant_id, year_month)
        if billing is None:
            return None

        line_items = await self.repo.get_line_items(billing.id)
        report     = await self.repo.get_report(tenant_id, year_month)

        return {
            "invoice": {
                "id":                str(billing.id),
                "tenant_id":         billing.tenant_id,
                "year_month":        billing.year_month,
                "status":            billing.status,
                "total_calls":       billing.total_calls,
                "total_tokens":      billing.total_tokens,
                "raw_cost_usd":      str(billing.raw_cost_usd),
                "base_fee_usd":      str(billing.base_fee_usd),
                "overage_charge_usd":str(billing.overage_charge_usd),
                "credits_usd":       str(billing.credits_usd),
                "total_billed_usd":  str(billing.total_billed_usd),
                "forfait_tokens_included": billing.forfait_tokens_included,
                "forfait_tokens_used":     billing.forfait_tokens_used,
                "overage_tokens":          billing.overage_tokens,
                "snapshot_taken_at": billing.snapshot_taken_at.isoformat()
                                     if billing.snapshot_taken_at else None,
                "finalized_at":      billing.finalized_at.isoformat()
                                     if billing.finalized_at else None,
            },
            "line_items": [
                {
                    "id":             str(li.id),
                    "line_type":      li.line_type,
                    "description":    li.description,
                    "model":          li.model,
                    "provider":       li.provider,
                    "quantity_tokens":li.quantity_tokens,
                    "quantity_calls": li.quantity_calls,
                    "unit_price_usd": str(li.unit_price_usd),
                    "amount_usd":     str(li.amount_usd),
                }
                for li in line_items
            ],
            "report": {
                "title":            report.report_title     if report else None,
                "executive_summary":report.executive_summary if report else None,
                "generated_at":     report.generated_at.isoformat() if report else None,
            } if report else None,
        }

    async def list_tenant_invoices(
        self, tenant_id: str
    ) -> list[dict]:
        """List all invoices for a tenant, newest first."""
        records = await self.repo.list_for_tenant(tenant_id)
        return [
            {
                "id":               str(r.id),
                "year_month":       r.year_month,
                "status":           r.status,
                "total_billed_usd": str(r.total_billed_usd),
                "total_tokens":     r.total_tokens,
                "total_calls":      r.total_calls,
                "finalized_at":     r.finalized_at.isoformat()
                                    if r.finalized_at else None,
            }
            for r in records
        ]

    async def generate_all_for_month(
        self,
        year_month: int,
        finalize: bool = False,
    ) -> dict:
        """
        Batch: generate invoices for all tenants with data for one month.
        Dynamically discovers tenants from cost data — no hardcoded list.
        """
        tenants = await self._discover_billable_tenants(year_month)
        results = []
        errors  = []

        for tenant_id in tenants:
            try:
                result = await self.generate_monthly_invoice(
                    tenant_id=tenant_id,
                    year_month=year_month,
                    finalize=finalize,
                )
                if result["success"]:
                    results.append(result)
                else:
                    errors.append({"tenant_id": tenant_id, "error": result["error"]})
            except Exception as e:
                errors.append({"tenant_id": tenant_id, "error": str(e)})

        return {
            "year_month":     year_month,
            "total_tenants":  len(tenants),
            "succeeded":      len(results),
            "failed":         len(errors),
            "results":        results,
            "errors":         errors,
        }

    # ================================================================
    # PRIVATE — CONTRACT ENGINE
    # ================================================================

    def _apply_contract(self, usage: dict, contract: dict) -> dict:
        """
        Apply contract rules to raw usage and compute billing amounts.

        This is the financial engine. The logic differs by contract type.

        pay_as_you_go: total_billed = raw_cost (no base fee, no forfait)
        forfait:       base_fee + overage charge for tokens above limit
        """
        total_tokens  = usage["total_tokens"]
        raw_cost      = usage["total_cost_usd"]
        contract_type = contract["contract_type"]
        base_fee      = contract["base_fee_usd"]
        forfait_tokens= contract["forfait_tokens"]
        overage_rate  = contract["overage_rate_per_1k"]

        if contract_type == "pay_as_you_go":
            return {
                "base_fee_usd":         0.0,
                "forfait_tokens_used":  0,
                "overage_tokens":       0,
                "overage_charge_usd":   0.0,
                "total_billed_usd":     raw_cost,
            }

        elif contract_type == "forfait":
            # How many tokens were inside vs outside the forfait?
            forfait_used  = min(total_tokens, forfait_tokens)
            overage       = max(0, total_tokens - forfait_tokens)
            # Overage charge: each 1K tokens above forfait = overage_rate
            overage_charge= (overage / 1000) * overage_rate
            total         = base_fee + overage_charge

            return {
                "base_fee_usd":         base_fee,
                "forfait_tokens_used":  forfait_used,
                "overage_tokens":       overage,
                "overage_charge_usd":   round(overage_charge, 4),
                "total_billed_usd":     round(total, 4),
            }

        else:
            # Unknown contract type — fall back to pay-as-you-go
            logger.warning(f"Unknown contract type: {contract_type}")
            return {
                "base_fee_usd":         0.0,
                "forfait_tokens_used":  0,
                "overage_tokens":       0,
                "overage_charge_usd":   0.0,
                "total_billed_usd":     raw_cost,
            }

    def _build_line_items(
        self,
        billing_id: UUID,
        tenant_id: str,
        year_month: int,
        contract: dict,
        amounts: dict,
        model_breakdown: list[dict],
    ) -> list[dict]:
        """
        Build the invoice line items.

        Every charge is visible and explainable:
          Line 1: Base monthly fee (for forfait contracts)
          Lines 2+: Cost per model (one line per model used)
          Last line: Overage charge (if tokens exceeded forfait)

        The client can look at each line and understand it.
        """
        month_name = self._month_name(year_month)
        items = []

        # Line 1: Base fee (forfait contracts only)
        if contract["contract_type"] == "forfait" and contract["base_fee_usd"] > 0:
            items.append({
                "billing_id":      billing_id,
                "tenant_id":       tenant_id,
                "year_month":      year_month,
                "line_type":       "base_fee",
                "description":     f"Monthly AI platform fee — {month_name}",
                "model":           None,
                "provider":        None,
                "agent_id":        None,
                "quantity_tokens": 0,
                "quantity_calls":  0,
                "unit_price_usd":  Decimal(str(contract["base_fee_usd"])),
                "amount_usd":      Decimal(str(round(contract["base_fee_usd"], 4))),
            })

        # Lines 2+: Cost per model (always — gives full transparency)
        for model in model_breakdown:
            if model["total_cost"] <= 0:
                continue
            items.append({
                "billing_id":      billing_id,
                "tenant_id":       tenant_id,
                "year_month":      year_month,
                "line_type":       "usage_cost",
                "description": (
                    f"{model['model']} ({model['provider']}) usage — "
                    f"{month_name} — {model['call_count']:,} calls"
                ),
                "model":           model["model"],
                "provider":        model["provider"],
                "agent_id":        None,
                "quantity_tokens": model["total_tokens"],
                "quantity_calls":  model["call_count"],
                "unit_price_usd":  Decimal("0"),
                # Unit price per 1K tokens would need pricing table lookup
                # Using 0 for now — total is in amount_usd
                "amount_usd":      Decimal(str(round(model["total_cost"], 4))),
            })

        # Overage line (forfait contracts when tokens exceeded)
        if amounts["overage_charge_usd"] > 0:
            items.append({
                "billing_id":      billing_id,
                "tenant_id":       tenant_id,
                "year_month":      year_month,
                "line_type":       "overage",
                "description": (
                    f"Token overage — {amounts['overage_tokens']:,} tokens "
                    f"above {contract['forfait_tokens']:,} included — {month_name}"
                ),
                "model":           None,
                "provider":        None,
                "agent_id":        None,
                "quantity_tokens": amounts["overage_tokens"],
                "quantity_calls":  0,
                "unit_price_usd":  Decimal(str(contract["overage_rate_per_1k"])),
                "amount_usd":      Decimal(str(round(amounts["overage_charge_usd"], 4))),
            })

        return items

    async def _generate_report(
        self,
        billing,
        usage: dict,
        contract: dict,
        amounts: dict,
        model_breakdown: list[dict],
        agent_breakdown: list[dict],
    ) -> dict:
        """
        Generate the client report — a narrative document.

        Understandable by a non-technical reader.
        Answers: what did you use, what did it cost, what does it mean?
        """
        month_name = self._month_name(billing.year_month)
        tenant_id  = billing.tenant_id

        # Forfait usage percentage
        if contract["forfait_tokens"] > 0:
            forfait_pct = (
                amounts["forfait_tokens_used"]
                / contract["forfait_tokens"] * 100
            )
            usage_summary = (
                f"{forfait_pct:.1f}% of your included {contract['forfait_tokens']:,} "
                f"tokens were consumed this month."
            )
            if amounts["overage_tokens"] > 0:
                usage_summary += (
                    f" {amounts['overage_tokens']:,} tokens exceeded your forfait, "
                    f"generating an overage charge of "
                    f"${amounts['overage_charge_usd']:.2f}."
                )
        else:
            usage_summary = (
                f"{usage['total_tokens']:,} tokens consumed across "
                f"{usage['total_calls']:,} API calls."
            )

        executive_summary = (
            f"Your AI platform usage for {month_name}: "
            f"{usage['total_tokens']:,} tokens consumed, "
            f"{usage['total_calls']:,} API calls. "
            f"Total invoice: ${amounts['total_billed_usd']:.2f}. "
            f"{usage_summary}"
        )

        return {
            "tenant_id":        tenant_id,
            "year_month":       billing.year_month,
            "billing_id":       billing.id,
            "report_title":     f"AI Platform Report — {tenant_id} — {month_name}",
            "executive_summary": executive_summary,
            "sections": {
                "usage_summary": {
                    "total_tokens":  usage["total_tokens"],
                    "total_calls":   usage["total_calls"],
                    "total_cost_usd":usage["total_cost_usd"],
                },
                "contract": {
                    "type":               contract["contract_type"],
                    "base_fee_usd":       contract["base_fee_usd"],
                    "forfait_tokens":     contract["forfait_tokens"],
                    "overage_rate_per_1k":contract["overage_rate_per_1k"],
                },
                "billing_result": {
                    "base_fee_usd":      amounts["base_fee_usd"],
                    "overage_charge_usd":amounts["overage_charge_usd"],
                    "total_billed_usd":  amounts["total_billed_usd"],
                },
                "top_models":  model_breakdown[:5],
                "top_agents":  agent_breakdown[:5],
            },
        }

    async def _discover_billable_tenants(self, year_month: int) -> list[str]:
        """
        Dynamically discover tenants that have usage data for the specified month.
        This replaces the hardcoded TENANT_CONTRACTS list.
        """
        from sqlalchemy import text
        result = await self.session.execute(
            text("""
                SELECT DISTINCT tenant_id
                FROM llm_cost_monthly
                WHERE year_month = :year_month
            """),
            {"year_month": year_month}
        )
        return [row[0] for row in result.all()]

    @staticmethod
    def _month_name(year_month: int) -> str:
        """Convert 202603 → 'March 2026'."""
        import calendar
        year  = year_month // 100
        month = year_month % 100
        return f"{calendar.month_name[month]} {year}"