"""
BaselineRepository — reads/writes baselines and queries historical data
needed by the ML engine.
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from modules.detection.models import LLMTokenBaseline


class BaselineRepository:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_baseline(self, tenant_id: str, agent_id: str = "*", model: str = "*") -> Optional[LLMTokenBaseline]:
        result = await self.session.execute(
            select(LLMTokenBaseline).where(
                LLMTokenBaseline.tenant_id == tenant_id,
                LLMTokenBaseline.agent_id == agent_id,
                LLMTokenBaseline.model == model
            )
        )
        return result.scalar_one_or_none()

    async def upsert_baseline(self, tenant_id: str, agent_id: str, model: str, data: dict) -> LLMTokenBaseline:
        """
        Insert or update baseline for a specific entity scope.
        data: all columns except id, tenant_id, agent_id, model.
        """
        stmt = (
            pg_insert(LLMTokenBaseline)
            .values(id=uuid.uuid4(), tenant_id=tenant_id, agent_id=agent_id, model=model, **data)
            .on_conflict_do_update(
                index_elements=["tenant_id", "agent_id", "model"],
                set_={k: data[k] for k in data},
            )
        )
        await self.session.execute(stmt)
        await self.session.flush()
        return await self.get_baseline(tenant_id, agent_id, model)

    async def get_daily_history(
        self, tenant_id: str, lookback_days: int = 30, agent_id: str = "*", model: str = "*"
    ) -> list[dict]:
        """
        Fetch per-day aggregated stats for the last N days.
        """
        to_dt = datetime.now(timezone.utc)
        from_dt = to_dt - timedelta(days=lookback_days)

        agent_filter = "AND agent_id = :agent_id" if agent_id != "*" else ""
        model_filter = "AND model = :model_name" if model != "*" else ""

        query = f"""
            SELECT
                time_bucket('1 day', created_at)          AS day,
                COALESCE(SUM(total_tokens), 0)            AS tokens,
                COUNT(*)                                   AS calls,
                COALESCE(SUM(cost_usd), 0)                AS cost_usd,
                COUNT(*) FILTER (WHERE status = 'error')  AS error_count,
                COALESCE(AVG(duration_ms), 0)             AS avg_duration_ms
            FROM llm_token_log
            WHERE
                tenant_id   = :tenant_id
                AND created_at >= :from_dt
                AND created_at <  :to_dt
                AND status != 'blocked'
                {agent_filter}
                {model_filter}
            GROUP BY day
            ORDER BY day ASC
        """

        result = await self.session.execute(
            text(query),
            {"tenant_id": tenant_id, "from_dt": from_dt, "to_dt": to_dt, "agent_id": agent_id, "model_name": model},
        )
        rows = result.fetchall()
        return [
            {
                "day": row.day,
                "tokens": float(row.tokens),
                "calls": float(row.calls),
                "cost_usd": float(row.cost_usd),
                "error_count": float(row.error_count),
                "avg_duration_ms": float(row.avg_duration_ms),
            }
            for row in rows
        ]

    async def get_today_totals(self, tenant_id: str, agent_id: str = "*", model: str = "*") -> dict:
        """Today's running totals (UTC day boundary)."""
        now = datetime.now(timezone.utc)
        from_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)

        agent_filter = "AND agent_id = :agent_id" if agent_id != "*" else ""
        model_filter = "AND model = :model_name" if model != "*" else ""

        query = f"""
            SELECT
                COALESCE(SUM(total_tokens), 0)          AS tokens,
                COUNT(*)                                 AS calls,
                COALESCE(SUM(cost_usd), 0)              AS cost_usd,
                COUNT(*) FILTER (WHERE status='error')  AS error_count,
                COALESCE(AVG(duration_ms), 0)             AS avg_duration_ms
            FROM llm_token_log
            WHERE
                tenant_id = :tenant_id
                AND created_at >= :from_dt
                AND status != 'blocked'
                {agent_filter}
                {model_filter}
        """

        result = await self.session.execute(
            text(query),
            {"tenant_id": tenant_id, "from_dt": from_dt, "agent_id": agent_id, "model_name": model},
        )
        row = result.one()
        return {
            "tokens": float(row.tokens),
            "calls": float(row.calls),
            "cost_usd": float(row.cost_usd),
            "error_count": float(row.error_count),
            "avg_duration_ms": float(row.avg_duration_ms)
        }