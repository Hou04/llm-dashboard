"""
AnalyticsService — business logic for the analytics module.

Coordinates between the cost repository and the API layer.
No SQL here — that lives in the repository.
No HTTP here — that lives in the router.
"""

from datetime import datetime, timezone, timedelta, date
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from modules.analytics.repositories.cost_repository import CostRepository


class AnalyticsService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.cost_repo = CostRepository(session)

    async def get_cost_summary(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict:
        """Full cost and usage summary for a tenant over a period."""
        return await self.cost_repo.get_summary(tenant_id, from_dt, to_dt)

    async def get_daily_trend(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """
        Day-by-day breakdown for trend charts.

        Fills in zero-value days where there was no usage so that
        charts render correctly without gaps.
        """
        rows = await self.cost_repo.get_daily_breakdown(
            tenant_id, from_dt, to_dt
        )

        # Build a complete date map including days with no activity
        date_map = {}
        current = from_dt.date()
        end = to_dt.date()
        while current <= end:
            date_map[current] = {
                "date": current.isoformat(),
                "total_calls": 0,
                "successful_calls": 0,
                "total_tokens": 0,
                "total_cost_usd": "0.00000000",
                "avg_duration_ms": None,
            }
            current += timedelta(days=1)

        # Fill in actual values
        for row in rows:
            d = row["date"]
            date_map[d] = {
                "date": d.isoformat(),
                "total_calls": row["total_calls"],
                "successful_calls": row["successful_calls"],
                "total_tokens": row["total_tokens"],
                "total_cost_usd": str(row["total_cost_usd"]),
                "avg_duration_ms": row["avg_duration_ms"],
            }

        return list(date_map.values())

    async def get_model_breakdown(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """Model cost breakdown with percentage shares."""
        rows = await self.cost_repo.get_model_breakdown(
            tenant_id, from_dt, to_dt
        )
        return [
            {
                **row,
                "total_cost_usd": str(row["total_cost_usd"]),
            }
            for row in rows
        ]

    async def get_agent_breakdown(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """Agent cost breakdown."""
        rows = await self.cost_repo.get_agent_breakdown(
            tenant_id, from_dt, to_dt
        )
        return [
            {
                **row,
                "total_cost_usd": str(row["total_cost_usd"]),
            }
            for row in rows
        ]

    async def get_all_tenants_overview(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """All tenants ranked by cost — for executive dashboard."""
        rows = await self.cost_repo.get_all_tenants_overview(
            from_dt, to_dt
        )
        return [
            {
                **row,
                "total_cost_usd": str(row["total_cost_usd"]),
            }
            for row in rows
        ]