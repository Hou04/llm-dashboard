"""
CostRepository — analytics queries against llm_token_log.

This repository reads from the raw log table and the pre-aggregated
tables. It never writes to llm_token_log — that's the gateway's job.

All queries are designed to use TimescaleDB's time-based indexes
efficiently. Queries always include a time range filter to enable
chunk pruning — TimescaleDB skips chunks outside the range entirely.
"""

from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from modules.gateway.models import LLMTokenLog, CallStatus


class CostRepository:
    """
    All analytics queries for cost and usage data.

    Reads from llm_token_log (raw) and llm_cost_daily (pre-aggregated).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_summary(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict:
        """
        Full usage summary for a tenant over a period with MoM and YoY trends.
        """
        delta = to_dt - from_dt
        mom_from_dt = from_dt - delta
        mom_to_dt = from_dt
        
        # We use strict 365 days for YoY
        from datetime import timedelta
        yoy_from_dt = from_dt - timedelta(days=365)
        yoy_to_dt = to_dt - timedelta(days=365)
        
        base_query = select(
            func.count(LLMTokenLog.id).label("total_calls"),
            func.count(LLMTokenLog.id).filter(LLMTokenLog.status == CallStatus.SUCCESS.value).label("successful_calls"),
            func.count(LLMTokenLog.id).filter(LLMTokenLog.status == CallStatus.ERROR.value).label("error_calls"),
            func.count(LLMTokenLog.id).filter(LLMTokenLog.status == CallStatus.BLOCKED.value).label("blocked_calls"),
            func.coalesce(func.sum(LLMTokenLog.input_tokens), 0).label("total_input_tokens"),
            func.coalesce(func.sum(LLMTokenLog.output_tokens), 0).label("total_output_tokens"),
            func.coalesce(func.sum(LLMTokenLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMTokenLog.cost_usd), Decimal("0")).label("total_cost_usd"),
            func.avg(LLMTokenLog.duration_ms).label("avg_duration_ms"),
            func.min(LLMTokenLog.created_at).label("first_call_at"),
            func.max(LLMTokenLog.created_at).label("last_call_at"),
        ).where(LLMTokenLog.tenant_id == tenant_id)

        cur_res = await self.session.execute(base_query.where(and_(LLMTokenLog.created_at >= from_dt, LLMTokenLog.created_at <= to_dt)))
        mom_res = await self.session.execute(base_query.where(and_(LLMTokenLog.created_at >= mom_from_dt, LLMTokenLog.created_at <= mom_to_dt)))
        yoy_res = await self.session.execute(base_query.where(and_(LLMTokenLog.created_at >= yoy_from_dt, LLMTokenLog.created_at <= yoy_to_dt)))
        
        row = cur_res.one()
        mom_row = mom_res.one()
        yoy_row = yoy_res.one()

        def calc_pct(cur: Decimal|int, old: Decimal|int):
            if not old or old == 0:
                return None
            return float(round(((cur - old) / old) * 100, 2))

        mom_cost_change_pct = calc_pct(row.total_cost_usd, mom_row.total_cost_usd)
        mom_token_change_pct = calc_pct(row.total_tokens, mom_row.total_tokens)
        yoy_cost_change_pct = calc_pct(row.total_cost_usd, yoy_row.total_cost_usd)
        yoy_token_change_pct = calc_pct(row.total_tokens, yoy_row.total_tokens)

        return {
            "total_calls": int(row.total_calls or 0),
            "successful_calls": int(row.successful_calls or 0),
            "error_calls": int(row.error_calls or 0),
            "blocked_calls": int(row.blocked_calls or 0),
            "total_input_tokens": int(row.total_input_tokens or 0),
            "total_output_tokens": int(row.total_output_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "total_cost_usd": row.total_cost_usd or Decimal("0"),
            "avg_duration_ms": float(row.avg_duration_ms) if row.avg_duration_ms else None,
            "first_call_at": row.first_call_at,
            "last_call_at": row.last_call_at,
            "mom_cost_change_pct": mom_cost_change_pct,
            "mom_tokens_change_pct": mom_token_change_pct,
            "yoy_cost_change_pct": yoy_cost_change_pct,
            "yoy_tokens_change_pct": yoy_token_change_pct,
        }

    async def get_daily_breakdown(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """
        Day-by-day cost and token breakdown for a tenant.

        Uses TimescaleDB's time_bucket() function to group by day.
        This is significantly faster than PostgreSQL's DATE_TRUNC
        on a hypertable because TimescaleDB knows exactly which
        chunks to read.

        Returns one dict per day, ordered oldest first (for charts).
        """
        result = await self.session.execute(
            text("""
                SELECT
                    time_bucket('1 day', created_at) AS day,
                    COUNT(*)                          AS total_calls,
                    COUNT(*) FILTER (WHERE status = 'success') AS successful_calls,
                    COALESCE(SUM(total_tokens), 0)    AS total_tokens,
                    COALESCE(SUM(cost_usd), 0)        AS total_cost_usd,
                    COALESCE(AVG(duration_ms), 0)     AS avg_duration_ms
                FROM llm_token_log
                WHERE
                    tenant_id = :tenant_id
                    AND created_at >= :from_dt
                    AND created_at <= :to_dt
                GROUP BY day
                ORDER BY day ASC
            """),
            {
                "tenant_id": tenant_id,
                "from_dt": from_dt,
                "to_dt": to_dt,
            }
        )
        return [
            {
                "date": row.day.date(),
                "total_calls": int(row.total_calls),
                "successful_calls": int(row.successful_calls),
                "total_tokens": int(row.total_tokens),
                "total_cost_usd": Decimal(str(row.total_cost_usd)),
                "avg_duration_ms": float(row.avg_duration_ms),
            }
            for row in result.all()
        ]

    async def get_model_breakdown(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """
        Cost and usage grouped by model for a tenant.

        Returns models ordered by total cost descending — the most
        expensive model first. Used for the "model cost breakdown"
        pie chart.
        """
        result = await self.session.execute(
            select(
                LLMTokenLog.model,
                LLMTokenLog.provider,
                func.count(LLMTokenLog.id).label("call_count"),
                func.coalesce(func.sum(LLMTokenLog.total_tokens), 0)
                    .label("total_tokens"),
                func.coalesce(func.sum(LLMTokenLog.input_tokens), 0)
                    .label("input_tokens"),
                func.coalesce(func.sum(LLMTokenLog.output_tokens), 0)
                    .label("output_tokens"),
                func.coalesce(func.sum(LLMTokenLog.cost_usd), Decimal("0"))
                    .label("total_cost_usd"),
                func.avg(LLMTokenLog.duration_ms).label("avg_duration_ms"),
            )
            .where(
                and_(
                    LLMTokenLog.tenant_id == tenant_id,
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                )
            )
            .group_by(LLMTokenLog.model, LLMTokenLog.provider)
            .order_by(func.sum(LLMTokenLog.cost_usd).desc())
        )
        rows = result.all()
        total_cost = sum(
            float(r.total_cost_usd or 0) for r in rows
        )
        return [
            {
                "model": row.model,
                "provider": row.provider,
                "call_count": int(row.call_count),
                "total_tokens": int(row.total_tokens or 0),
                "input_tokens": int(row.input_tokens or 0),
                "output_tokens": int(row.output_tokens or 0),
                "total_cost_usd": Decimal(str(row.total_cost_usd or 0)),
                "avg_duration_ms": (
                    float(row.avg_duration_ms) if row.avg_duration_ms else None
                ),
                "cost_share_pct": round(
                    float(row.total_cost_usd or 0) / total_cost * 100, 2
                ) if total_cost > 0 else 0.0,
            }
            for row in rows
        ]

    async def get_agent_breakdown(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """
        Cost and usage grouped by agent for a tenant.

        Shows which of the tenant's agents/applications costs the most.
        Used for the "cost by agent" chart.
        """
        result = await self.session.execute(
            select(
                LLMTokenLog.agent_id,
                func.count(LLMTokenLog.id).label("call_count"),
                func.coalesce(func.sum(LLMTokenLog.total_tokens), 0)
                    .label("total_tokens"),
                func.coalesce(func.sum(LLMTokenLog.cost_usd), Decimal("0"))
                    .label("total_cost_usd"),
            )
            .where(
                and_(
                    LLMTokenLog.tenant_id == tenant_id,
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                    LLMTokenLog.agent_id.isnot(None),
                )
            )
            .group_by(LLMTokenLog.agent_id)
            .order_by(func.sum(LLMTokenLog.cost_usd).desc())
        )
        return [
            {
                "agent_id": row.agent_id,
                "call_count": int(row.call_count),
                "total_tokens": int(row.total_tokens or 0),
                "total_cost_usd": Decimal(str(row.total_cost_usd or 0)),
            }
            for row in result.all()
        ]

    async def get_all_tenants_overview(
        self,
        from_dt: datetime,
        to_dt: datetime,
        limit: int = 20,
    ) -> list[dict]:
        """
        All tenants ranked by total cost over a period.

        Used for the executive overview dashboard — "which tenant
        costs the most this month?"

        Only returns tenants that have actual usage in the period.
        Returns up to `limit` tenants, most expensive first.
        """
        result = await self.session.execute(
            select(
                LLMTokenLog.tenant_id,
                func.count(LLMTokenLog.id).label("total_calls"),
                func.coalesce(func.sum(LLMTokenLog.total_tokens), 0)
                    .label("total_tokens"),
                func.coalesce(func.sum(LLMTokenLog.cost_usd), Decimal("0"))
                    .label("total_cost_usd"),
                func.count(LLMTokenLog.id).filter(
                    LLMTokenLog.status == CallStatus.ERROR.value
                ).label("error_calls"),
            )
            .where(
                and_(
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                )
            )
            .group_by(LLMTokenLog.tenant_id)
            .order_by(func.sum(LLMTokenLog.cost_usd).desc())
            .limit(limit)
        )
        rows = result.all()
        total_cost = sum(float(r.total_cost_usd or 0) for r in rows)
        return [
            {
                "tenant_id": row.tenant_id,
                "total_calls": int(row.total_calls),
                "total_tokens": int(row.total_tokens or 0),
                "total_cost_usd": Decimal(str(row.total_cost_usd or 0)),
                "error_calls": int(row.error_calls or 0),
                "error_rate_pct": round(
                    int(row.error_calls or 0) / int(row.total_calls) * 100, 2
                ) if row.total_calls > 0 else 0.0,
                "cost_share_pct": round(
                    float(row.total_cost_usd or 0) / total_cost * 100, 2
                ) if total_cost > 0 else 0.0,
            }
            for row in rows
        ]