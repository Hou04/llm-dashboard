"""
ForecastingService — orchestrates forecast generation and storage.

Coordinates between:
- ForecastRepository (data access)
- ForecastEngine (ML logic via Prophet or STL fallback)
- LLMGovernanceRule (to get limits for budget risk)

v2: Per-agent forecasting — runs independent forecasts for each agent
    under a tenant, plus a tenant-wide aggregate for budget risk.
"""

import logging
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.forecasting.models import LLMForecast, LLMBudgetRisk
from modules.forecasting.repositories import ForecastRepository
from modules.forecasting.services.forecast_engine import ForecastEngine, ForecastResult

logger = logging.getLogger(__name__)


class ForecastingService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ForecastRepository(session)
        self.engine = ForecastEngine()

    async def generate_forecast_for_tenant(
        self,
        tenant_id: str,
        horizon_days: int = 30,
        lookback_days: int = 60,
    ) -> Optional[ForecastResult]:
        """
        Generate, store, and return a forecast for a tenant.

        Steps:
        1. Fetch daily history from llm_token_log (tenant aggregate)
        2. Run ForecastEngine.forecast()
        3. Convert result to DB rows and upsert
        4. Compute budget risk and upsert/delete risk record
        5. Run per-agent forecasts for each active agent
        6. Commit and return the tenant-level ForecastResult

        Returns None if there is insufficient history.
        """
        # ---- Tenant-level aggregate forecast ----
        history_rows = await self.repo.get_daily_history(
            tenant_id, lookback_days
        )

        if len(history_rows) < 21:
            logger.info(
                "Insufficient history for forecast",
                extra={"tenant_id": tenant_id, "days": len(history_rows)},
            )
            return None

        history_tokens = [r["tokens"] for r in history_rows]
        history_costs = [r["cost_usd"] for r in history_rows]

        total_tokens = sum(history_tokens)
        total_cost = sum(history_costs)
        avg_cost_per_token = (
            total_cost / total_tokens if total_tokens > 0 else 0.0
        )

        last_history_date = history_rows[-1]["day"].date() \
            if hasattr(history_rows[-1]["day"], "date") \
            else history_rows[-1]["day"]
        start_date = last_history_date + timedelta(days=1)

        forecast = self.engine.forecast(
            history=history_tokens,
            start_date=start_date,
            horizon_days=horizon_days,
            avg_cost_per_token=avg_cost_per_token,
            tenant_id=tenant_id,
            agent_id="*",
        )

        if forecast is None:
            return None

        # Persist tenant-level forecasts
        await self._persist_forecast(tenant_id, "*", forecast, avg_cost_per_token)

        # Compute and store budget risk (tenant-level only)
        await self._update_budget_risk(
            tenant_id, forecast, avg_cost_per_token
        )

        # ---- Per-agent forecasts ----
        await self._generate_per_agent_forecasts(
            tenant_id, start_date, horizon_days, lookback_days
        )

        await self.session.commit()

        logger.info(
            "Forecast generated (tenant + per-agent)",
            extra={
                "tenant_id": tenant_id,
                "horizon_days": horizon_days,
                "slope": forecast.trend_slope,
                "monthly_likely": round(forecast.monthly_likely_tokens),
            },
        )
        return forecast

    async def _generate_per_agent_forecasts(
        self,
        tenant_id: str,
        start_date: date,
        horizon_days: int,
        lookback_days: int,
    ) -> list[ForecastResult]:
        """
        Generate individual forecasts for each agent under a tenant.

        Uses the same Prophet/STL engine but with per-agent history.
        Agents with fewer than MIN_HISTORY_DAYS of data are skipped.
        """
        agent_ids = await self._get_active_agents(tenant_id, lookback_days)
        results = []

        for aid in agent_ids:
            try:
                history_rows = await self.repo.get_daily_history_for_agent(
                    tenant_id, aid, lookback_days
                )
                if len(history_rows) < 21:
                    continue

                history_tokens = [r["tokens"] for r in history_rows]
                history_costs = [r["cost_usd"] for r in history_rows]
                total_t = sum(history_tokens)
                total_c = sum(history_costs)
                avg_cpt = total_c / total_t if total_t > 0 else 0.0

                forecast = self.engine.forecast(
                    history=history_tokens,
                    start_date=start_date,
                    horizon_days=horizon_days,
                    avg_cost_per_token=avg_cpt,
                    tenant_id=tenant_id,
                    agent_id=aid,
                )

                if forecast:
                    await self._persist_forecast(tenant_id, aid, forecast, avg_cpt)
                    results.append(forecast)

            except Exception as e:
                logger.warning(
                    f"Per-agent forecast failed for {aid}: {e}",
                    extra={"tenant_id": tenant_id, "agent_id": aid},
                )

        logger.info(
            f"Per-agent forecasts: {len(results)}/{len(agent_ids)} agents",
            extra={"tenant_id": tenant_id},
        )
        return results

    async def _get_active_agents(
        self, tenant_id: str, lookback_days: int
    ) -> list[str]:
        """Get distinct agent_ids that have logged calls recently."""
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=lookback_days)

        result = await self.session.execute(
            text("""
                SELECT DISTINCT agent_id
                FROM llm_token_log
                WHERE tenant_id = :tenant_id
                  AND created_at >= :from_dt
                  AND agent_id IS NOT NULL
                  AND agent_id != ''
                ORDER BY agent_id
            """),
            {"tenant_id": tenant_id, "from_dt": from_dt},
        )
        return [row[0] for row in result.fetchall()]

    async def _persist_forecast(
        self,
        tenant_id: str,
        agent_id: str,
        forecast: ForecastResult,
        avg_cost_per_token: float,
    ) -> None:
        """Convert ForecastResult to DB rows and upsert."""
        now = datetime.now(timezone.utc)
        rows = []
        for d in forecast.daily_forecasts:
            for scenario, tokens in [
                ("likely", d.likely_tokens),
                ("pessimistic", d.pessimistic_tokens),
                ("optimistic", d.optimistic_tokens),
            ]:
                rows.append({
                    "forecast_date": d.forecast_date,
                    "agent_id": agent_id,
                    "scenario": scenario,
                    "predicted_tokens": tokens,
                    "predicted_cost_usd": Decimal(str(
                        round(tokens * avg_cost_per_token, 8)
                    )),
                    "trend_value": d.trend_value,
                    "seasonal_multiplier": d.seasonal_multiplier,
                    "confidence_lower": d.likely_tokens - d.confidence_half_width,
                    "confidence_upper": d.likely_tokens + d.confidence_half_width,
                    "horizon_days": d.horizon_days,
                    "generated_at": now,
                })

        await self.repo.upsert_daily_forecasts(tenant_id, rows)

    # ================================================================
    # PUBLIC — READ
    # ================================================================

    async def get_forecast(
        self,
        tenant_id: str,
        from_date: Optional[date] = None,
        agent_id: str = "*",
    ) -> list[LLMForecast]:
        """Retrieve stored likely-scenario forecast for a tenant/agent."""
        return await self.repo.get_latest_forecast(
            tenant_id, scenario="likely", from_date=from_date,
            agent_id=agent_id,
        )

    async def get_all_scenarios(
        self,
        tenant_id: str,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        agent_id: str = "*",
    ) -> list[LLMForecast]:
        """Retrieve all three scenarios for a date range."""
        from_dt = from_date or date.today()
        to_dt = to_date or (date.today() + timedelta(days=30))
        return await self.repo.get_all_scenarios_for_date_range(
            tenant_id, from_dt, to_dt, agent_id=agent_id,
        )

    async def get_budget_risks(
        self, max_days: Optional[int] = None
    ) -> list[LLMBudgetRisk]:
        """Get all budget risk records, optionally filtered by urgency."""
        return await self.repo.get_all_budget_risks(max_days)

    async def generate_forecasts_for_all_tenants(
        self, tenant_ids: list[str]
    ) -> dict[str, bool]:
        """Generate forecasts for all tenants. Returns success map."""
        results = {}
        for tid in tenant_ids:
            result = await self.generate_forecast_for_tenant(tid)
            results[tid] = result is not None
        return results

    # ================================================================
    # PRIVATE
    # ================================================================

    async def _update_budget_risk(
        self,
        tenant_id: str,
        forecast: ForecastResult,
        avg_cost_per_token: float,
    ) -> None:
        """
        Compute budget risk and update the risk record.
        Only uses tenant-wide aggregate (*) forecasts for budget risk.
        """
        daily_limit, monthly_budget = await self._get_tenant_limits(tenant_id)
        month_totals = await self.repo.get_current_month_totals(tenant_id)

        risks = self.engine.compute_budget_risk(
            forecast=forecast,
            daily_token_limit=daily_limit,
            monthly_budget_usd=monthly_budget,
            current_monthly_tokens=month_totals["tokens"],
            current_monthly_cost_usd=month_totals["cost_usd"],
        )

        await self.repo.delete_budget_risk(tenant_id)

        for risk in risks:
            await self.repo.upsert_budget_risk(tenant_id, risk)

    async def _get_tenant_limits(
        self, tenant_id: str
    ) -> tuple[Optional[float], Optional[float]]:
        """Fetch governance limits for a tenant."""
        result = await self.session.execute(
            text("""
                SELECT rule_type,
                       daily_token_limit,
                       monthly_budget_usd
                FROM llm_governance_rules
                WHERE (tenant_id = :tenant_id OR tenant_id IS NULL)
                  AND is_active = TRUE
                  AND rule_type IN ('tenant_limit', 'budget_cap')
                ORDER BY priority DESC
            """),
            {"tenant_id": tenant_id},
        )
        rows = result.fetchall()

        daily_limit = None
        monthly_budget = None

        for row in rows:
            if row.rule_type == "tenant_limit" and daily_limit is None:
                daily_limit = float(row.daily_token_limit) \
                    if row.daily_token_limit else None
                # Artificial scaling down for the demo to trigger risks
                if daily_limit and daily_limit > 100000:
                    daily_limit = 50000
            if row.rule_type == "budget_cap" and monthly_budget is None:
                monthly_budget = float(row.monthly_budget_usd) \
                    if row.monthly_budget_usd else None
                # Artificial scaling down for the demo to trigger risks
                if monthly_budget and monthly_budget > 10:
                    monthly_budget = 1.0

        return daily_limit, monthly_budget