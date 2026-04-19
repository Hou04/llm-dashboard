"""ForecastRepository — reads and writes forecast and budget risk data."""

import uuid
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, delete, and_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.forecasting.models import LLMForecast, LLMBudgetRisk


class ForecastRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_daily_forecasts(
        self, tenant_id: str, rows: list[dict]
    ) -> int:
        """
        Bulk upsert forecast rows for a tenant.

        Each row: {forecast_date, scenario, predicted_tokens,
                   predicted_cost_usd, trend_value, seasonal_multiplier,
                   confidence_lower, confidence_upper, horizon_days,
                   generated_at}

        Uses ON CONFLICT UPDATE to replace stale predictions.
        Returns count of rows written.
        """
        if not rows:
            return 0

        for row in rows:
            row["id"] = uuid.uuid4()
            row["tenant_id"] = tenant_id

        stmt = (
            pg_insert(LLMForecast)
            .values(rows)
            .on_conflict_do_update(
                constraint="uq_forecast_tenant_agent_date_scenario",
                set_={
                    "predicted_tokens": pg_insert(LLMForecast).excluded.predicted_tokens,
                    "predicted_cost_usd": pg_insert(LLMForecast).excluded.predicted_cost_usd,
                    "trend_value": pg_insert(LLMForecast).excluded.trend_value,
                    "seasonal_multiplier": pg_insert(LLMForecast).excluded.seasonal_multiplier,
                    "confidence_lower": pg_insert(LLMForecast).excluded.confidence_lower,
                    "confidence_upper": pg_insert(LLMForecast).excluded.confidence_upper,
                    "horizon_days": pg_insert(LLMForecast).excluded.horizon_days,
                    "generated_at": pg_insert(LLMForecast).excluded.generated_at,
                },
            )
        )
        await self.session.execute(stmt)
        return len(rows)

    async def get_latest_forecast(
        self,
        tenant_id: str,
        scenario: str = "likely",
        from_date: Optional[date] = None,
        agent_id: str = "*",
    ) -> list[LLMForecast]:
        """
        Fetch forecast rows for a tenant/agent and scenario.

        Returns rows ordered by forecast_date ascending (nearest first).
        If from_date not specified, returns all future dates.
        """
        from_dt = from_date or date.today()
        result = await self.session.execute(
            select(LLMForecast)
            .where(
                and_(
                    LLMForecast.tenant_id == tenant_id,
                    LLMForecast.agent_id == agent_id,
                    LLMForecast.scenario == scenario,
                    LLMForecast.forecast_date >= from_dt,
                )
            )
            .order_by(LLMForecast.forecast_date.asc())
        )
        return list(result.scalars().all())

    async def get_all_scenarios_for_date_range(
        self,
        tenant_id: str,
        from_date: date,
        to_date: date,
        agent_id: str = "*",
    ) -> list[LLMForecast]:
        """Fetch all three scenarios for a date range and agent."""
        result = await self.session.execute(
            select(LLMForecast)
            .where(
                and_(
                    LLMForecast.tenant_id == tenant_id,
                    LLMForecast.agent_id == agent_id,
                    LLMForecast.forecast_date >= from_date,
                    LLMForecast.forecast_date <= to_date,
                )
            )
            .order_by(LLMForecast.forecast_date.asc(), LLMForecast.scenario.asc())
        )
        return list(result.scalars().all())

    async def upsert_budget_risk(self, tenant_id: str, risk: dict) -> None:
        """Insert or update the budget risk record for a tenant."""
        stmt = (
            pg_insert(LLMBudgetRisk)
            .values(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                **risk,
                generated_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_update(
                constraint="uq_budget_risk_tenant_type",
                set_={k: risk[k] for k in risk},
            )
        )
        await self.session.execute(stmt)

    async def delete_budget_risk(self, tenant_id: str) -> None:
        """Remove budget risk records for a tenant (when no longer at risk)."""
        await self.session.execute(
            delete(LLMBudgetRisk).where(LLMBudgetRisk.tenant_id == tenant_id)
        )

    async def get_all_budget_risks(
        self, max_days: Optional[int] = None
    ) -> list[LLMBudgetRisk]:
        """
        Get all budget risk records, optionally filtered by urgency.

        max_days: only return risks where exhaustion is within N days.
        """
        conditions = []
        if max_days is not None:
            conditions.append(
                LLMBudgetRisk.days_until_exhaustion <= max_days
            )

        result = await self.session.execute(
            select(LLMBudgetRisk)
            .where(*conditions)
            .order_by(LLMBudgetRisk.days_until_exhaustion.asc())
        )
        return list(result.scalars().all())

    async def get_daily_history(
        self, tenant_id: str, lookback_days: int = 60
    ) -> list[dict]:
        """
        Fetch per-day aggregated token totals for history.

        Reused from detection module's query pattern.
        """
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=lookback_days)

        result = await self.session.execute(
            text("""
                SELECT
                    time_bucket('1 day', created_at)         AS day,
                    COALESCE(SUM(total_tokens), 0)           AS tokens,
                    COALESCE(SUM(cost_usd), 0)               AS cost_usd,
                    COUNT(*)                                  AS calls
                FROM llm_token_log
                WHERE
                    tenant_id   = :tenant_id
                    AND created_at >= :from_dt
                    AND created_at <  :to_dt
                    AND status != 'blocked'
                GROUP BY day
                ORDER BY day ASC
            """),
            {"tenant_id": tenant_id, "from_dt": from_dt, "to_dt": to_dt},
        )
        rows = result.fetchall()
        return [
            {
                "day": row.day,
                "tokens": float(row.tokens),
                "cost_usd": float(row.cost_usd),
                "calls": int(row.calls),
            }
            for row in rows
        ]

    async def get_daily_history_for_agent(
        self, tenant_id: str, agent_id: str, lookback_days: int = 60
    ) -> list[dict]:
        """
        Fetch per-day aggregated token totals for a specific agent.
        Used for per-agent Prophet forecasting.
        """
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=lookback_days)

        result = await self.session.execute(
            text("""
                SELECT
                    time_bucket('1 day', created_at)         AS day,
                    COALESCE(SUM(total_tokens), 0)           AS tokens,
                    COALESCE(SUM(cost_usd), 0)               AS cost_usd,
                    COUNT(*)                                  AS calls
                FROM llm_token_log
                WHERE
                    tenant_id   = :tenant_id
                    AND agent_id = :agent_id
                    AND created_at >= :from_dt
                    AND created_at <  :to_dt
                    AND status != 'blocked'
                GROUP BY day
                ORDER BY day ASC
            """),
            {"tenant_id": tenant_id, "agent_id": agent_id, "from_dt": from_dt, "to_dt": to_dt},
        )
        rows = result.fetchall()
        return [
            {
                "day": row.day,
                "tokens": float(row.tokens),
                "cost_usd": float(row.cost_usd),
                "calls": int(row.calls),
            }
            for row in rows
        ]

    async def get_current_month_totals(
        self, tenant_id: str
    ) -> dict:
        """Current month's running token and cost totals."""
        now = datetime.now(timezone.utc)
        from_dt = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = await self.session.execute(
            text("""
                SELECT
                    COALESCE(SUM(total_tokens), 0) AS tokens,
                    COALESCE(SUM(cost_usd), 0)     AS cost_usd
                FROM llm_token_log
                WHERE
                    tenant_id = :tenant_id
                    AND created_at >= :from_dt
                    AND status != 'blocked'
            """),
            {"tenant_id": tenant_id, "from_dt": from_dt},
        )
        row = result.one()
        return {
            "tokens": float(row.tokens),
            "cost_usd": float(row.cost_usd),
        }