"""
Tests for the M2 aggregation tasks.

These tests verify that the aggregation logic correctly reads
from llm_token_log and writes to llm_cost_daily / llm_cost_monthly.

We test the async core functions directly (_aggregate_date,
_rollup_month) rather than the Celery task wrappers, because
Celery tasks are just thin sync wrappers around those functions.
"""

import pytest
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select, func

from core.database import AsyncSessionLocal
from modules.analytics.models import LLMCostDaily, LLMCostMonthly
from modules.analytics.tasks.aggregation import _aggregate_date, _rollup_month


# ============================================================
# llm_cost_daily tests
# ============================================================

@pytest.mark.asyncio
async def test_cost_daily_table_is_populated():
    """llm_cost_daily has rows after backfill."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(LLMCostDaily.id))
        )
        count = result.scalar()
    assert count > 0, "llm_cost_daily is empty — run scripts/backfill_costs.py"


@pytest.mark.asyncio
async def test_cost_daily_has_all_tenants():
    """All 5 seeded tenants appear in the daily table."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LLMCostDaily.tenant_id.distinct())
        )
        tenants = [row[0] for row in result.all()]

    expected = {
        "enterprise_corp", "startup_ai", "research_lab",
        "fintech_secure", "dev_sandbox"
    }
    for tenant in expected:
        assert tenant in tenants, f"{tenant} missing from llm_cost_daily"


@pytest.mark.asyncio
async def test_cost_daily_enterprise_corp_totals_match_log():
    """
    enterprise_corp's total cost in llm_cost_daily should match
    the sum from llm_token_log directly.
    """
    from sqlalchemy import select, func, and_
    from modules.gateway.models import LLMTokenLog

    async with AsyncSessionLocal() as session:
        # Sum from raw log
        log_result = await session.execute(
            select(func.sum(LLMTokenLog.cost_usd)).where(
                LLMTokenLog.tenant_id == "enterprise_corp"
            )
        )
        log_total = float(log_result.scalar() or 0)

        # Sum from pre-aggregated daily table
        daily_result = await session.execute(
            select(func.sum(LLMCostDaily.total_cost_usd)).where(
                LLMCostDaily.tenant_id == "enterprise_corp"
            )
        )
        daily_total = float(daily_result.scalar() or 0)

    # Should match within 1 cent (floating point tolerance)
    assert abs(log_total - daily_total) < 0.01, (
        f"Cost mismatch: log={log_total:.4f}, daily={daily_total:.4f}"
    )


@pytest.mark.asyncio
async def test_aggregate_date_is_idempotent():
    """
    Running aggregate_date twice for the same date produces
    the same result (upsert, not insert).
    """
    target_date = date(2026, 2, 15)

    # Run twice
    await _aggregate_date(target_date)
    await _aggregate_date(target_date)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(LLMCostDaily.id)).where(
                LLMCostDaily.date == target_date
            )
        )
        count = result.scalar()

    # Should be exactly 5 rows (one per tenant), not 10
    assert count == 5, f"Expected 5 rows after idempotent upsert, got {count}"


@pytest.mark.asyncio
async def test_aggregate_date_returns_correct_tenant_count():
    """aggregate_date returns the number of tenants processed."""
    result = await _aggregate_date(date(2026, 2, 20))
    assert result["tenants_processed"] == 5
    assert result["date"] == "2026-02-20"


@pytest.mark.asyncio
async def test_aggregate_empty_date_returns_zero():
    """A date with no activity returns zero tenants processed."""
    # Use a date far in the future with no data
    result = await _aggregate_date(date(2030, 1, 1))
    assert result["tenants_processed"] == 0


# ============================================================
# llm_cost_monthly tests
# ============================================================

@pytest.mark.asyncio
async def test_cost_monthly_table_is_populated():
    """llm_cost_monthly has rows after backfill."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(LLMCostMonthly.id))
        )
        count = result.scalar()
    assert count > 0, "llm_cost_monthly is empty — run scripts/backfill_costs.py"


@pytest.mark.asyncio
async def test_cost_monthly_has_expected_months():
    """December 2025, January/February/March 2026 should all exist."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LLMCostMonthly.year_month.distinct())
        )
        months = {row[0] for row in result.all()}

    expected = {202512, 202601, 202602, 202603}
    for ym in expected:
        assert ym in months, f"year_month {ym} missing from llm_cost_monthly"


@pytest.mark.asyncio
async def test_rollup_month_is_idempotent():
    """Running rollup_month twice produces the same result."""
    await _rollup_month(2026, 2)
    await _rollup_month(2026, 2)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(LLMCostMonthly.id)).where(
                LLMCostMonthly.year_month == 202602
            )
        )
        count = result.scalar()

    assert count == 5, f"Expected 5 rows after idempotent rollup, got {count}"


@pytest.mark.asyncio
async def test_monthly_enterprise_corp_february():
    """enterprise_corp's February 2026 monthly total should be positive."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LLMCostMonthly).where(
                LLMCostMonthly.tenant_id == "enterprise_corp",
                LLMCostMonthly.year_month == 202602,
            )
        )
        row = result.scalar_one_or_none()

    assert row is not None, "enterprise_corp Feb 2026 monthly row missing"
    assert float(row.total_cost_usd) > 0
    assert row.total_calls > 0
    assert row.total_tokens > 0