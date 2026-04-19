"""
Aggregation tasks for the Analytics module.

Two tasks:
1. aggregate_yesterday() — runs nightly, aggregates the previous
   day's logs into llm_cost_daily. Can also be run manually for
   any specific date.

2. rollup_last_month() — runs 1st of each month, rolls up all
   daily records for the previous month into llm_cost_monthly.

Both tasks are idempotent: running them twice for the same period
produces the same result (upsert, not insert).

These tasks use asyncio.run() inside Celery workers because
Celery tasks are synchronous by default. This is the correct
pattern for using async SQLAlchemy in Celery.
"""

import asyncio
import logging
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, text, delete
from sqlalchemy.dialects.postgresql import insert

from celery_app import app
from core.database import AsyncSessionLocal
from modules.gateway.models import LLMTokenLog, CallStatus
from modules.analytics.models import LLMCostDaily, LLMCostMonthly

logger = logging.getLogger(__name__)


# ============================================================
# CORE AGGREGATION LOGIC (async functions)
# These are the real business logic — the Celery tasks
# below just call them via asyncio.run()
# ============================================================

async def _aggregate_date(target_date: date) -> dict:
    """
    Aggregate all token log entries for a specific date into
    llm_cost_daily.

    For each tenant that had activity on target_date, computes:
    - total calls, successful calls, failed calls
    - input tokens, output tokens, total tokens
    - total cost in USD
    - the most-used model

    Uses PostgreSQL's INSERT ON CONFLICT UPDATE (upsert) so
    running this twice for the same date is safe.

    Returns a summary dict with counts of what was processed.
    """
    from_dt = datetime(
        target_date.year, target_date.month, target_date.day,
        0, 0, 0, tzinfo=timezone.utc
    )
    to_dt = datetime(
        target_date.year, target_date.month, target_date.day,
        23, 59, 59, 999999, tzinfo=timezone.utc
    )

    async with AsyncSessionLocal() as session:
        # Step 1: Get all tenants with activity on this date
        result = await session.execute(
            select(LLMTokenLog.tenant_id.distinct()).where(
                and_(
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                )
            )
        )
        tenant_ids = [row[0] for row in result.all()]

        if not tenant_ids:
            logger.info(
                "aggregation.no_activity",
                date=target_date.isoformat()
            )
            return {"date": target_date.isoformat(), "tenants_processed": 0}

        rows_upserted = 0

        for tenant_id in tenant_ids:
            # Step 2: Aggregate this tenant's activity for this date
            agg = await session.execute(
                select(
                    func.count(LLMTokenLog.id).label("total_calls"),
                    func.count(LLMTokenLog.id).filter(
                        LLMTokenLog.status == CallStatus.SUCCESS.value
                    ).label("successful_calls"),
                    func.count(LLMTokenLog.id).filter(
                        LLMTokenLog.status.in_([
                            CallStatus.ERROR.value,
                            CallStatus.TIMEOUT.value,
                        ])
                    ).label("failed_calls"),
                    func.coalesce(
                        func.sum(LLMTokenLog.input_tokens), 0
                    ).label("input_tokens"),
                    func.coalesce(
                        func.sum(LLMTokenLog.output_tokens), 0
                    ).label("output_tokens"),
                    func.coalesce(
                        func.sum(LLMTokenLog.total_tokens), 0
                    ).label("total_tokens"),
                    func.coalesce(
                        func.sum(LLMTokenLog.cost_usd), Decimal("0")
                    ).label("total_cost_usd"),
                ).where(
                    and_(
                        LLMTokenLog.tenant_id == tenant_id,
                        LLMTokenLog.created_at >= from_dt,
                        LLMTokenLog.created_at <= to_dt,
                    )
                )
            )
            row = agg.one()

            # Step 3: Find the top model for this tenant on this day
            top_model_result = await session.execute(
                select(LLMTokenLog.model)
                .where(
                    and_(
                        LLMTokenLog.tenant_id == tenant_id,
                        LLMTokenLog.created_at >= from_dt,
                        LLMTokenLog.created_at <= to_dt,
                    )
                )
                .group_by(LLMTokenLog.model)
                .order_by(func.count(LLMTokenLog.id).desc())
                .limit(1)
            )
            top_model_row = top_model_result.first()
            top_model = top_model_row[0] if top_model_row else None

            # Step 4: Upsert into llm_cost_daily
            # If a row already exists for (tenant_id, date), update it.
            # This makes the task idempotent.
            stmt = text("""
                INSERT INTO llm_cost_daily
                    (id, tenant_id, date, total_calls, successful_calls,
                     failed_calls, input_tokens, output_tokens, total_tokens,
                     total_cost_usd, top_model)
                VALUES
                    (gen_random_uuid(), :tenant_id, :date, :total_calls,
                     :successful_calls, :failed_calls, :input_tokens,
                     :output_tokens, :total_tokens, :total_cost_usd,
                     :top_model)
                ON CONFLICT (tenant_id, date)
                DO UPDATE SET
                    total_calls      = EXCLUDED.total_calls,
                    successful_calls = EXCLUDED.successful_calls,
                    failed_calls     = EXCLUDED.failed_calls,
                    input_tokens     = EXCLUDED.input_tokens,
                    output_tokens    = EXCLUDED.output_tokens,
                    total_tokens     = EXCLUDED.total_tokens,
                    total_cost_usd   = EXCLUDED.total_cost_usd,
                    top_model        = EXCLUDED.top_model
            """)

            await session.execute(stmt, {
                "tenant_id": tenant_id,
                "date": target_date,
                "total_calls": int(row.total_calls),
                "successful_calls": int(row.successful_calls),
                "failed_calls": int(row.failed_calls),
                "input_tokens": int(row.input_tokens),
                "output_tokens": int(row.output_tokens),
                "total_tokens": int(row.total_tokens),
                "total_cost_usd": row.total_cost_usd,
                "top_model": top_model,
            })
            rows_upserted += 1

        await session.commit()

        logger.info(
            "aggregation.complete",
            date=target_date.isoformat(),
            tenants=rows_upserted,
        )
        return {
            "date": target_date.isoformat(),
            "tenants_processed": rows_upserted,
        }


async def _rollup_month(year: int, month: int) -> dict:
    """
    Roll up all daily records for a given month into llm_cost_monthly.

    Called on the 1st of each month to consolidate the previous month.
    Uses upsert so running it twice is safe.
    """
    year_month = year * 100 + month  # e.g. 202603

    async with AsyncSessionLocal() as session:
        # Sum all daily rows for this month, grouped by tenant
        result = await session.execute(
            select(
                LLMCostDaily.tenant_id,
                func.sum(LLMCostDaily.total_calls).label("total_calls"),
                func.sum(LLMCostDaily.successful_calls).label("successful_calls"),
                func.sum(LLMCostDaily.failed_calls).label("failed_calls"),
                func.sum(LLMCostDaily.total_tokens).label("total_tokens"),
                func.sum(LLMCostDaily.total_cost_usd).label("total_cost_usd"),
            ).where(
                and_(
                    func.extract("year", LLMCostDaily.date) == year,
                    func.extract("month", LLMCostDaily.date) == month,
                )
            ).group_by(LLMCostDaily.tenant_id)
        )
        rows = result.all()

        if not rows:
            return {
                "year_month": year_month,
                "tenants_processed": 0
            }

        # Fetch previous month for MoM calculation
        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1
        prev_year_month = prev_year * 100 + prev_month

        prev_result = await session.execute(
            select(
                LLMCostMonthly.tenant_id,
                LLMCostMonthly.total_cost_usd,
                LLMCostMonthly.total_tokens,
            ).where(LLMCostMonthly.year_month == prev_year_month)
        )
        prev_by_tenant = {
            row.tenant_id: row for row in prev_result.all()
        }

        for row in rows:
            prev = prev_by_tenant.get(row.tenant_id)
            cost_change_pct = None
            tokens_change_pct = None

            if prev and prev.total_cost_usd and float(prev.total_cost_usd) > 0:
                cost_change_pct = float(
                    (row.total_cost_usd - prev.total_cost_usd)
                    / prev.total_cost_usd * 100
                )
            if prev and prev.total_tokens and int(prev.total_tokens) > 0:
                tokens_change_pct = float(
                    (row.total_tokens - prev.total_tokens)
                    / prev.total_tokens * 100
                )

            stmt = text("""
                INSERT INTO llm_cost_monthly
                    (id, tenant_id, year_month, total_calls, successful_calls,
                     failed_calls, total_tokens, total_cost_usd,
                     cost_change_pct, tokens_change_pct)
                VALUES
                    (gen_random_uuid(), :tenant_id, :year_month, :total_calls,
                     :successful_calls, :failed_calls, :total_tokens,
                     :total_cost_usd, :cost_change_pct, :tokens_change_pct)
                ON CONFLICT (tenant_id, year_month)
                DO UPDATE SET
                    total_calls       = EXCLUDED.total_calls,
                    successful_calls  = EXCLUDED.successful_calls,
                    failed_calls      = EXCLUDED.failed_calls,
                    total_tokens      = EXCLUDED.total_tokens,
                    total_cost_usd    = EXCLUDED.total_cost_usd,
                    cost_change_pct   = EXCLUDED.cost_change_pct,
                    tokens_change_pct = EXCLUDED.tokens_change_pct
            """)

            await session.execute(stmt, {
                "tenant_id": row.tenant_id,
                "year_month": year_month,
                "total_calls": int(row.total_calls or 0),
                "successful_calls": int(row.successful_calls or 0),
                "failed_calls": int(row.failed_calls or 0),
                "total_tokens": int(row.total_tokens or 0),
                "total_cost_usd": row.total_cost_usd,
                "cost_change_pct": cost_change_pct,
                "tokens_change_pct": tokens_change_pct,
            })

        await session.commit()

        return {
            "year_month": year_month,
            "tenants_processed": len(rows),
        }


# ============================================================
# CELERY TASKS (sync wrappers around async logic)
# ============================================================

@app.task(name="modules.analytics.tasks.aggregation.aggregate_yesterday")
def aggregate_yesterday():
    """Nightly task: aggregate yesterday's logs into llm_cost_daily."""
    yesterday = date.today() - timedelta(days=1)
    return asyncio.run(_aggregate_date(yesterday))


@app.task(name="modules.analytics.tasks.aggregation.aggregate_date")
def aggregate_date(date_str: str):
    """
    Manual trigger: aggregate a specific date.
    date_str format: YYYY-MM-DD

    Use this to backfill historical data.
    """
    target = date.fromisoformat(date_str)
    return asyncio.run(_aggregate_date(target))


@app.task(name="modules.analytics.tasks.aggregation.rollup_last_month")
def rollup_last_month():
    """Monthly task: roll up last month's dailies into llm_cost_monthly."""
    today = date.today()
    if today.month == 1:
        year, month = today.year - 1, 12
    else:
        year, month = today.year, today.month - 1
    return asyncio.run(_rollup_month(year, month))


@app.task(name="modules.analytics.tasks.aggregation.backfill_all")
def backfill_all(from_date_str: str, to_date_str: str):
    """
    Backfill aggregation for a date range.
    Useful after initial deployment to populate historical data.

    from_date_str, to_date_str: YYYY-MM-DD format
    """
    from_date = date.fromisoformat(from_date_str)
    to_date = date.fromisoformat(to_date_str)

    results = []
    current = from_date
    while current <= to_date:
        result = asyncio.run(_aggregate_date(current))
        results.append(result)
        current += timedelta(days=1)

    return {
        "dates_processed": len(results),
        "from": from_date_str,
        "to": to_date_str,
        "summary": results,
    }