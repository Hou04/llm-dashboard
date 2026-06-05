"""
BillingRepository — all SQL for the M10 billing module.

Follows the same pattern as every other repository in the project:
- SQL lives here and only here
- Service layer never writes SQL directly
- All methods are async
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, and_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.billing.models import (
    LLMBillingMonthly,
    LLMBillingLineItem,
    LLMClientReport,
)


class BillingRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================
    # BILLING MONTHLY — reads and writes
    # ================================================================

    async def get_or_create_draft(
        self, tenant_id: str, year_month: int
    ) -> LLMBillingMonthly:
        """
        Get existing billing record or create a new draft.

        Safe to call multiple times — idempotent.
        If a draft exists, returns it. If finalized exists, raises.
        """
        existing = await self.session.execute(
            select(LLMBillingMonthly).where(
                and_(
                    LLMBillingMonthly.tenant_id  == tenant_id,
                    LLMBillingMonthly.year_month == year_month,
                )
            )
        )
        record = existing.scalar_one_or_none()

        if record is not None:
            if record.status == "finalized":
                raise ValueError(
                    f"Billing for {tenant_id} month {year_month} is already "
                    f"finalized. Create a credit note to make corrections."
                )
            return record

        # Create new draft
        new_record = LLMBillingMonthly(
            tenant_id=tenant_id,
            year_month=year_month,
            status="draft",
        )
        self.session.add(new_record)
        await self.session.flush()
        await self.session.refresh(new_record)
        return new_record

    async def update_billing(
        self,
        billing_id: uuid.UUID,
        updates: dict,
    ) -> LLMBillingMonthly:
        """Update fields on a billing record."""
        record = await self.session.get(LLMBillingMonthly, billing_id)
        if record is None:
            raise ValueError(f"Billing record {billing_id} not found")
        for key, value in updates.items():
            setattr(record, key, value)
        await self.session.flush()
        return record

    async def finalize(
        self, billing_id: uuid.UUID
    ) -> LLMBillingMonthly:
        """
        Freeze a billing record. After this, numbers never change.
        This is a one-way operation.
        """
        record = await self.session.get(LLMBillingMonthly, billing_id)
        if record is None:
            raise ValueError(f"Billing record {billing_id} not found")
        if record.status == "finalized":
            return record  # already done — idempotent
        record.status       = "finalized"
        record.finalized_at = datetime.now(timezone.utc)
        await self.session.flush()
        return record

    async def get_by_tenant_month(
        self, tenant_id: str, year_month: int
    ) -> Optional[LLMBillingMonthly]:
        result = await self.session.execute(
            select(LLMBillingMonthly).where(
                and_(
                    LLMBillingMonthly.tenant_id  == tenant_id,
                    LLMBillingMonthly.year_month == year_month,
                )
            )
        )
        return result.scalar_one_or_none()

    async def list_for_tenant(
        self, tenant_id: str, limit: int = 12
    ) -> list[LLMBillingMonthly]:
        """Last N months of billing for a tenant."""
        result = await self.session.execute(
            select(LLMBillingMonthly)
            .where(LLMBillingMonthly.tenant_id == tenant_id)
            .order_by(LLMBillingMonthly.year_month.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_all_for_month(
        self, year_month: int
    ) -> list[LLMBillingMonthly]:
        """All tenant invoices for a given month — for batch processing."""
        result = await self.session.execute(
            select(LLMBillingMonthly)
            .where(LLMBillingMonthly.year_month == year_month)
            .order_by(LLMBillingMonthly.tenant_id)
        )
        return list(result.scalars().all())

    # ================================================================
    # LINE ITEMS
    # ================================================================

    async def create_line_items(
        self, line_items: list[dict]
    ) -> int:
        """
        Insert multiple line items for one billing record.

        First deletes any existing draft lines for this billing_id
        so this is safe to call again if recalculation is needed.
        """
        if not line_items:
            return 0

        billing_id = line_items[0]["billing_id"]

        # Delete existing draft lines (safe recalculation)
        await self.session.execute(
            text(
                "DELETE FROM llm_billing_line_items WHERE billing_id = :bid"
            ),
            {"bid": str(billing_id)},
        )

        for item in line_items:
            record = LLMBillingLineItem(**item)
            self.session.add(record)

        await self.session.flush()
        return len(line_items)

    async def get_line_items(
        self, billing_id: uuid.UUID
    ) -> list[LLMBillingLineItem]:
        result = await self.session.execute(
            select(LLMBillingLineItem)
            .where(LLMBillingLineItem.billing_id == billing_id)
            .order_by(LLMBillingLineItem.line_type, LLMBillingLineItem.amount_usd.desc())
        )
        return list(result.scalars().all())

    async def get_credit_line_items(
        self, billing_id: uuid.UUID
    ) -> list[LLMBillingLineItem]:
        """Fetch only credit-type line items for a billing record."""
        result = await self.session.execute(
            select(LLMBillingLineItem)
            .where(
                and_(
                    LLMBillingLineItem.billing_id == billing_id,
                    LLMBillingLineItem.line_type == "credit",
                )
            )
            .order_by(LLMBillingLineItem.created_at.desc())
        )
        return list(result.scalars().all())

    # ================================================================
    # CLIENT REPORTS
    # ================================================================

    async def save_report(
        self, report_data: dict
    ) -> LLMClientReport:
        """Upsert a client report — safe to regenerate."""
        import json

        # Check for existing report
        existing = await self.session.execute(
            select(LLMClientReport).where(
                and_(
                    LLMClientReport.tenant_id  == report_data["tenant_id"],
                    LLMClientReport.year_month == report_data["year_month"],
                )
            )
        )
        record = existing.scalar_one_or_none()

        if record is not None:
            # Update existing
            record.report_title      = report_data["report_title"]
            record.executive_summary = report_data["executive_summary"]
            record.report_data       = json.dumps(report_data["sections"])
            record.generated_at      = datetime.now(timezone.utc)
        else:
            record = LLMClientReport(
                tenant_id        = report_data["tenant_id"],
                year_month       = report_data["year_month"],
                billing_id       = report_data.get("billing_id"),
                report_title     = report_data["report_title"],
                executive_summary= report_data["executive_summary"],
                report_data      = json.dumps(report_data["sections"]),
                status           = "draft",
            )
            self.session.add(record)

        await self.session.flush()
        await self.session.refresh(record)
        return record

    async def get_report(
        self, tenant_id: str, year_month: int
    ) -> Optional[LLMClientReport]:
        result = await self.session.execute(
            select(LLMClientReport).where(
                and_(
                    LLMClientReport.tenant_id  == tenant_id,
                    LLMClientReport.year_month == year_month,
                )
            )
        )
        return result.scalar_one_or_none()

    # ================================================================
    # RAW USAGE — reads from M2 aggregated tables
    # ================================================================

    async def get_monthly_usage(
        self, tenant_id: str, year_month: int
    ) -> Optional[dict]:
        """
        Compute monthly usage dynamically from llm_token_log.

        100% dynamic — no pre-aggregation table needed.
        Aggregates directly from the raw telemetry logs for the given month.
        """
        year  = year_month // 100
        month = year_month % 100

        result = await self.session.execute(
            text("""
                SELECT
                    CAST(:tenant_id AS TEXT)                            AS tenant_id,
                    CAST(:year_month AS INTEGER)                        AS year_month,
                    COUNT(*)::int                                       AS total_calls,
                    COUNT(*) FILTER (WHERE status = 'success')::int     AS successful_calls,
                    COUNT(*) FILTER (WHERE status != 'success')::int    AS failed_calls,
                    COALESCE(SUM(total_tokens), 0)::bigint              AS total_tokens,
                    COALESCE(ROUND(SUM(cost_usd)::numeric, 6), 0)      AS total_cost_usd,
                    (
                        SELECT module 
                        FROM llm_token_log 
                        WHERE tenant_id = :tenant_id 
                          AND EXTRACT(YEAR FROM created_at) = :year 
                          AND EXTRACT(MONTH FROM created_at) = :month 
                          AND module IS NOT NULL
                        GROUP BY module 
                        ORDER BY COUNT(*) DESC 
                        LIMIT 1
                    ) AS primary_module
                FROM llm_token_log
                WHERE tenant_id = :tenant_id
                  AND EXTRACT(YEAR  FROM created_at) = :year
                  AND EXTRACT(MONTH FROM created_at) = :month
            """),
            {"tenant_id": tenant_id, "year_month": year_month, "year": year, "month": month},
        )
        row = result.fetchone()
        if row is None or int(row.total_calls or 0) == 0:
            return None
        return {
            "tenant_id":       row.tenant_id,
            "year_month":      row.year_month,
            "total_calls":     int(row.total_calls or 0),
            "successful_calls":int(row.successful_calls or 0),
            "failed_calls":    int(row.failed_calls or 0),
            "total_tokens":    int(row.total_tokens or 0),
            "total_cost_usd":  float(row.total_cost_usd or 0),
            "primary_module":  row.primary_module or "AI Platform",
        }

    async def get_model_breakdown_for_month(
        self, tenant_id: str, year_month: int
    ) -> list[dict]:
        """
        Cost and token usage by model for a given month.
        Used for line item generation — one line per model.
        """
        year  = year_month // 100
        month = year_month % 100

        result = await self.session.execute(
            text("""
                SELECT
                    model,
                    provider,
                    COUNT(*)::int                          AS call_count,
                    SUM(input_tokens)::bigint              AS input_tokens,
                    SUM(output_tokens)::bigint             AS output_tokens,
                    SUM(total_tokens)::bigint              AS total_tokens,
                    ROUND(SUM(cost_usd)::numeric, 6)       AS total_cost
                FROM llm_token_log
                WHERE tenant_id = :tenant_id
                  AND EXTRACT(YEAR  FROM created_at) = :year
                  AND EXTRACT(MONTH FROM created_at) = :month
                  AND status = 'success'
                GROUP BY model, provider
                ORDER BY SUM(cost_usd) DESC
            """),
            {"tenant_id": tenant_id, "year": year, "month": month},
        )
        return [
            {
                "model":        row.model,
                "provider":     row.provider,
                "call_count":   int(row.call_count or 0),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens":int(row.output_tokens or 0),
                "total_tokens": int(row.total_tokens or 0),
                "total_cost":   float(row.total_cost or 0),
            }
            for row in result.fetchall()
        ]

    async def get_agent_breakdown_for_month(
        self, tenant_id: str, year_month: int
    ) -> list[dict]:
        """Agent cost breakdown for the client report."""
        year  = year_month // 100
        month = year_month % 100

        result = await self.session.execute(
            text("""
                SELECT
                    COALESCE(agent_id, 'unknown') AS agent_id,
                    COUNT(*)::int                 AS call_count,
                    SUM(total_tokens)::bigint     AS total_tokens,
                    ROUND(SUM(cost_usd)::numeric, 6) AS total_cost
                FROM llm_token_log
                WHERE tenant_id = :tenant_id
                  AND EXTRACT(YEAR  FROM created_at) = :year
                  AND EXTRACT(MONTH FROM created_at) = :month
                  AND status = 'success'
                GROUP BY agent_id
                ORDER BY SUM(cost_usd) DESC
                LIMIT 10
            """),
            {"tenant_id": tenant_id, "year": year, "month": month},
        )
        return [
            {
                "agent_id":    row.agent_id,
                "call_count":  int(row.call_count or 0),
                "total_tokens":int(row.total_tokens or 0),
                "total_cost":  float(row.total_cost or 0),
            }
            for row in result.fetchall()
        ]