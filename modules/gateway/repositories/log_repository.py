"""
LogRepository — all database operations for llm_token_log.

This is the ONLY place in the project that reads from or writes to
the llm_token_log table. Services and API handlers never write SQL —
they call methods on this class.

Design principles:
- Every method receives an AsyncSession — the caller controls transactions.
- Methods never commit. They flush (write to DB within transaction) but
  the caller decides when to commit or rollback.
- All time comparisons use timezone-aware UTC datetimes.
- Heavy queries use explicit column selection, never SELECT *.
"""

import uuid
from datetime import datetime, timezone, date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func, and_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from modules.gateway.models import LLMTokenLog, CallStatus


class LogRepository:
    """
    Repository for llm_token_log.

    Usage:
        repo = LogRepository(session)
        log = await repo.create(log_entry)
        summary = await repo.get_usage_summary("tenant_abc", from_dt, to_dt)
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ================================================================
    # WRITES
    # ================================================================

    async def create(self, log: LLMTokenLog) -> LLMTokenLog:
        """
        Insert a single token log entry.

        The caller must commit after calling this. This method only
        flushes — the row is written to the DB within the current
        transaction but not yet permanently saved.

        Args:
            log: LLMTokenLog instance with all required fields set.

        Returns:
            The same log instance, with any DB-generated values populated.
        """
        self.session.add(log)
        await self.session.flush()
        await self.session.refresh(log)
        return log

    async def bulk_create(self, logs: list[LLMTokenLog]) -> int:
        """
        Insert multiple token log entries efficiently.

        Uses PostgreSQL's INSERT ... VALUES (...), (...) syntax to
        insert all rows in a single query instead of N separate INSERTs.
        Significantly faster for batched API call logging.

        Args:
            logs: List of LLMTokenLog instances.

        Returns:
            Number of rows inserted.
        """
        if not logs:
            return 0

        rows = [
            {
                "id": log.id or uuid.uuid4(),
                "request_id": log.request_id,
                "tenant_id": log.tenant_id,
                "agent_id": log.agent_id,
                "user_id": log.user_id,
                "model": log.model,
                "provider": log.provider,
                "input_tokens": log.input_tokens,
                "output_tokens": log.output_tokens,
                "total_tokens": log.total_tokens,
                "cost_usd": log.cost_usd,
                "duration_ms": log.duration_ms,
                "status": log.status,
                "error_message": log.error_message,
                "metadata_": log.metadata_,
                "created_at": log.created_at or datetime.now(timezone.utc),
            }
            for log in logs
        ]

        stmt = pg_insert(LLMTokenLog).values(rows)
        await self.session.execute(stmt)
        return len(rows)

    # ================================================================
    # READS — single record
    # ================================================================

    async def get_by_id(
        self, log_id: uuid.UUID
    ) -> Optional[LLMTokenLog]:
        """
        Fetch a single log entry by its UUID.

        Returns None if not found.
        """
        result = await self.session.execute(
            select(LLMTokenLog).where(LLMTokenLog.id == log_id)
        )
        return result.scalar_one_or_none()

    # ================================================================
    # READS — collections
    # ================================================================

    async def get_by_tenant(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
        limit: int = 100,
        status: Optional[str] = None,
    ) -> list[LLMTokenLog]:
        """
        Fetch log entries for a tenant within a time window.

        Results are ordered newest-first. Use limit to control result size —
        this table can have millions of rows, never query without a limit.

        Args:
            tenant_id: The tenant to query.
            from_dt: Start of time window (inclusive), UTC.
            to_dt: End of time window (inclusive), UTC.
            limit: Maximum rows to return. Default 100, max recommended 1000.
            status: Optional filter by call status (success/error/blocked).

        Returns:
            List of LLMTokenLog instances, newest first.
        """
        conditions = [
            LLMTokenLog.tenant_id == tenant_id,
            LLMTokenLog.created_at >= from_dt,
            LLMTokenLog.created_at <= to_dt,
        ]

        if status is not None:
            conditions.append(LLMTokenLog.status == status)

        result = await self.session.execute(
            select(LLMTokenLog)
            .where(and_(*conditions))
            .order_by(LLMTokenLog.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ================================================================
    # READS — aggregates
    # ================================================================

    async def get_usage_summary(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict:
        """
        Aggregate token and cost totals for a tenant over a time period.

        This is the primary query for analytics dashboards and billing.
        Uses a single SQL aggregate query — does not load individual rows.

        Returns a dict with:
            total_calls: int — number of API calls
            successful_calls: int — calls with status=success
            total_input_tokens: int
            total_output_tokens: int
            total_tokens: int
            total_cost_usd: Decimal
            avg_duration_ms: float | None — average call duration
        """
        result = await self.session.execute(
            select(
                func.count(LLMTokenLog.id).label("total_calls"),
                func.count(LLMTokenLog.id).filter(
                    LLMTokenLog.status == CallStatus.SUCCESS.value
                ).label("successful_calls"),
                func.coalesce(
                    func.sum(LLMTokenLog.input_tokens), 0
                ).label("total_input_tokens"),
                func.coalesce(
                    func.sum(LLMTokenLog.output_tokens), 0
                ).label("total_output_tokens"),
                func.coalesce(
                    func.sum(LLMTokenLog.total_tokens), 0
                ).label("total_tokens"),
                func.coalesce(
                    func.sum(LLMTokenLog.cost_usd), Decimal("0")
                ).label("total_cost_usd"),
                func.avg(LLMTokenLog.duration_ms).label("avg_duration_ms"),
            ).where(
                and_(
                    LLMTokenLog.tenant_id == tenant_id,
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                )
            )
        )
        row = result.one()
        return {
            "total_calls": row.total_calls or 0,
            "successful_calls": row.successful_calls or 0,
            "total_input_tokens": int(row.total_input_tokens or 0),
            "total_output_tokens": int(row.total_output_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "total_cost_usd": row.total_cost_usd or Decimal("0"),
            "avg_duration_ms": (
                float(row.avg_duration_ms) if row.avg_duration_ms else None
            ),
        }

    async def get_daily_token_count(
        self,
        tenant_id: str,
        for_date: date,
    ) -> int:
        """
        Total tokens used by a tenant on a specific calendar date (UTC).

        Used for governance checks — "has this tenant hit their daily limit?"
        This is backed by Redis cache in production, but this method provides
        the authoritative count from the database.

        Args:
            tenant_id: The tenant to check.
            for_date: The calendar date (UTC) to sum.

        Returns:
            Total token count as integer. Returns 0 if no records found.
        """
        result = await self.session.execute(
            select(
                func.coalesce(func.sum(LLMTokenLog.total_tokens), 0)
            ).where(
                and_(
                    LLMTokenLog.tenant_id == tenant_id,
                    cast(LLMTokenLog.created_at, Date) == for_date,
                )
            )
        )
        return int(result.scalar() or 0)

    async def get_model_breakdown(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict]:
        """
        Token and cost usage grouped by model for a tenant.

        Used for analytics dashboards showing which models a tenant uses most.

        Returns a list of dicts, sorted by total_tokens descending:
            model: str
            provider: str
            call_count: int
            total_tokens: int
            total_cost_usd: Decimal
        """
        result = await self.session.execute(
            select(
                LLMTokenLog.model,
                LLMTokenLog.provider,
                func.count(LLMTokenLog.id).label("call_count"),
                func.coalesce(
                    func.sum(LLMTokenLog.total_tokens), 0
                ).label("total_tokens"),
                func.coalesce(
                    func.sum(LLMTokenLog.cost_usd), Decimal("0")
                ).label("total_cost_usd"),
            )
            .where(
                and_(
                    LLMTokenLog.tenant_id == tenant_id,
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                )
            )
            .group_by(LLMTokenLog.model, LLMTokenLog.provider)
            .order_by(func.sum(LLMTokenLog.total_tokens).desc())
        )
        return [
            {
                "model": row.model,
                "provider": row.provider,
                "call_count": row.call_count,
                "total_tokens": int(row.total_tokens or 0),
                "total_cost_usd": row.total_cost_usd or Decimal("0"),
            }
            for row in result.all()
        ]

    async def count_by_status(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict[str, int]:
        """
        Count of calls grouped by status for a tenant.

        Used for health monitoring — what percentage of calls are errors?

        Returns a dict mapping status string to count:
            {"success": 450, "error": 12, "timeout": 3, "blocked": 5}
        """
        result = await self.session.execute(
            select(
                LLMTokenLog.status,
                func.count(LLMTokenLog.id).label("count"),
            )
            .where(
                and_(
                    LLMTokenLog.tenant_id == tenant_id,
                    LLMTokenLog.created_at >= from_dt,
                    LLMTokenLog.created_at <= to_dt,
                )
            )
            .group_by(LLMTokenLog.status)
        )
        counts = {status.value: 0 for status in CallStatus}
        for row in result.all():
            counts[row.status] = row.count
        return counts