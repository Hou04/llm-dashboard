"""
PipelineOrchestrator — runs the full M2→M3 processing pipeline.

Replaces manual scripts with a unified, automated pipeline:
1. M2: Aggregate daily costs from llm_token_log → llm_cost_daily
2. M2: Roll up daily costs → llm_cost_monthly
3. M3: Generate/detect anomalies

Designed to be called from:
- API endpoint (POST /v1/pipeline/run-all)
- Startup hook (if configured)
- Celery task (for scheduled execution)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from core.config_registry import config
from core.observability import metrics, pipeline_timer, log_pipeline_event

logger = logging.getLogger(__name__)


class PipelineOrchestrator:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run_full_pipeline(self) -> dict:
        """
        Execute the complete M2→M3 pipeline.

        Returns a summary of what was processed.
        """
        log_pipeline_event("full_pipeline_start")
        results = {}

        # Step 1: M2 — Aggregate daily costs
        with pipeline_timer("m2_daily_aggregation"):
            results["m2_daily"] = await self._run_daily_aggregation()

        # Step 2: M2 — Roll up monthly costs
        with pipeline_timer("m2_monthly_rollup"):
            results["m2_monthly"] = await self._run_monthly_rollup()

        # Step 3: M3 — Generate anomalies
        if config.get("ingestion.auto_generate_anomalies"):
            with pipeline_timer("m3_anomaly_generation"):
                from modules.pipeline.anomaly_generator import AnomalyGenerator
                generator = AnomalyGenerator(self.session)
                results["m3_anomalies"] = await generator.generate_anomalies()

        await self.session.commit()

        log_pipeline_event("full_pipeline_complete", details={"steps": len(results)})
        return {"success": True, "pipeline_results": results}

    async def _run_daily_aggregation(self) -> dict:
        """
        Aggregate token logs into llm_cost_daily.

        Processes all dates that have token log data but no
        corresponding daily aggregate — no hardcoded date range needed.
        """
        # Find date range of data
        result = await self.session.execute(
            text("""
                SELECT
                    MIN(DATE(created_at)) AS first_date,
                    MAX(DATE(created_at)) AS last_date
                FROM llm_token_log
            """)
        )
        row = result.one()
        if not row.first_date or not row.last_date:
            return {"dates_processed": 0, "reason": "no data"}

        first_date = row.first_date
        last_date = row.last_date

        dates_processed = 0
        current = first_date

        errors = []
        while current <= last_date:
            try:
                await self._aggregate_single_date(current)
                # Commit after each date so a failure on day 5 doesn't revert days 1-4
                await self.session.commit()
                dates_processed += 1
            except Exception as e:
                logger.warning(f"aggregation.date_failed date={current} error={e}")
                await self.session.rollback()
                errors.append(str(current))
                # Do NOT raise here, continue to the next date
            
            # Use strict start transaction if needed, but session usually handes it
            current += timedelta(days=1)

        await self.session.flush()
        metrics.set_gauge("pipeline.daily_aggregation.last_date", str(last_date))

        return {
            "dates_processed": dates_processed,
            "date_range": f"{first_date} → {last_date}",
            "errors": errors if errors else None
        }

    async def _aggregate_single_date(self, target_date: date) -> None:
        """Aggregate one day's data into llm_cost_daily."""
        from_dt = datetime(
            target_date.year, target_date.month, target_date.day,
            0, 0, 0, tzinfo=timezone.utc,
        )
        to_dt = datetime(
            target_date.year, target_date.month, target_date.day,
            23, 59, 59, 999999, tzinfo=timezone.utc,
        )

        # Get all tenants with activity on this date
        result = await self.session.execute(
            text("""
                SELECT DISTINCT tenant_id
                FROM llm_token_log
                WHERE created_at >= :from_dt AND created_at <= :to_dt
                  AND tenant_id IS NOT NULL
            """),
            {"from_dt": from_dt, "to_dt": to_dt},
        )
        tenant_ids = [r[0] for r in result.all()]

        for tenant_id in tenant_ids:
            agg = await self.session.execute(
                text("""
                    SELECT
                        COUNT(*)                                          AS total_calls,
                        COUNT(*) FILTER (WHERE status = 'success')        AS successful_calls,
                        COUNT(*) FILTER (WHERE status IN ('error','timeout')) AS failed_calls,
                        COALESCE(SUM(input_tokens), 0)                    AS input_tokens,
                        COALESCE(SUM(output_tokens), 0)                   AS output_tokens,
                        COALESCE(SUM(total_tokens), 0)                    AS total_tokens,
                        COALESCE(SUM(cost_usd), 0)                        AS total_cost_usd
                    FROM llm_token_log
                    WHERE tenant_id = :tenant_id
                      AND created_at >= :from_dt
                      AND created_at <= :to_dt
                """),
                {"tenant_id": tenant_id, "from_dt": from_dt, "to_dt": to_dt},
            )
            r = agg.one()

            # Top model
            top_result = await self.session.execute(
                text("""
                    SELECT model FROM llm_token_log
                    WHERE tenant_id = :tenant_id
                      AND created_at >= :from_dt AND created_at <= :to_dt
                    GROUP BY model ORDER BY COUNT(*) DESC LIMIT 1
                """),
                {"tenant_id": tenant_id, "from_dt": from_dt, "to_dt": to_dt},
            )
            top_row = top_result.first()
            top_model = top_row[0] if top_row else None

            # Upsert
            await self.session.execute(
                text("""
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
                """),
                {
                    "tenant_id": tenant_id,
                    "date": target_date,
                    "total_calls": int(r.total_calls),
                    "successful_calls": int(r.successful_calls),
                    "failed_calls": int(r.failed_calls),
                    "input_tokens": int(r.input_tokens),
                    "output_tokens": int(r.output_tokens),
                    "total_tokens": int(r.total_tokens),
                    "total_cost_usd": r.total_cost_usd,
                    "top_model": top_model,
                },
            )

    async def _run_monthly_rollup(self) -> dict:
        """Roll up daily aggregates into monthly summaries."""
        # Find all months that have daily data
        result = await self.session.execute(
            text("""
                SELECT DISTINCT
                    EXTRACT(YEAR FROM date)::int AS year,
                    EXTRACT(MONTH FROM date)::int AS month
                FROM llm_cost_daily
                ORDER BY year, month
            """)
        )
        months = result.all()

        processed = 0
        for row in months:
            year, month = int(row.year), int(row.month)
            year_month = year * 100 + month

            try:
                await self._rollup_single_month(year, month, year_month)
                processed += 1
            except Exception as e:
                logger.warning(f"monthly_rollup.failed year_month={year_month} error={e}")
                await self.session.rollback()
                raise

        await self.session.flush()
        return {"months_processed": processed}

    async def _rollup_single_month(
        self, year: int, month: int, year_month: int
    ) -> None:
        """Roll up one month's daily data into llm_cost_monthly."""
        result = await self.session.execute(
            text("""
                SELECT
                    tenant_id,
                    SUM(total_calls)      AS total_calls,
                    SUM(successful_calls) AS successful_calls,
                    SUM(failed_calls)     AS failed_calls,
                    SUM(total_tokens)     AS total_tokens,
                    SUM(total_cost_usd)   AS total_cost_usd
                FROM llm_cost_daily
                WHERE EXTRACT(YEAR FROM date) = :year
                  AND EXTRACT(MONTH FROM date) = :month
                GROUP BY tenant_id
            """),
            {"year": year, "month": month},
        )
        rows = result.all()

        for row in rows:
            await self.session.execute(
                text("""
                    INSERT INTO llm_cost_monthly
                        (id, tenant_id, year_month, total_calls, successful_calls,
                         failed_calls, total_tokens, total_cost_usd)
                    VALUES
                        (gen_random_uuid(), :tenant_id, :year_month, :total_calls,
                         :successful_calls, :failed_calls, :total_tokens,
                         :total_cost_usd)
                    ON CONFLICT (tenant_id, year_month)
                    DO UPDATE SET
                        total_calls      = EXCLUDED.total_calls,
                        successful_calls = EXCLUDED.successful_calls,
                        failed_calls     = EXCLUDED.failed_calls,
                        total_tokens     = EXCLUDED.total_tokens,
                        total_cost_usd   = EXCLUDED.total_cost_usd
                """),
                {
                    "tenant_id": row.tenant_id,
                    "year_month": year_month,
                    "total_calls": int(row.total_calls or 0),
                    "successful_calls": int(row.successful_calls or 0),
                    "failed_calls": int(row.failed_calls or 0),
                    "total_tokens": int(row.total_tokens or 0),
                    "total_cost_usd": row.total_cost_usd,
                },
            )
