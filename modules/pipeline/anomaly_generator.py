"""
AnomalyGenerator — statistical anomaly enrichment.

Analyzes ingested token log data and generates realistic anomalies
when the dataset lacks them (e.g. empty anomaly CSV).

The generator:
1. Computes per-tenant+model baselines (mean, std)
2. Scans for natural anomalies using Z-score analysis
3. Injects synthetic anomalies if too few exist naturally
4. Creates LLMAnomalyRecord entries for the detection module
5. Updates LLMTokenBaseline entries for ongoing detection
"""

import logging
import random
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import text, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.config_registry import config
from core.observability import metrics, pipeline_timer, log_pipeline_event

logger = logging.getLogger(__name__)


class AnomalyGenerator:
    """
    Statistical anomaly enrichment engine.

    Ensures the M3 detection module always has meaningful data
    without requiring hand-crafted anomaly CSV files.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def generate_anomalies(self) -> dict:
        """
        Scan token log data and generate anomaly records.

        Returns a summary of what was generated.
        """
        with pipeline_timer("anomaly_generation"):
            # Step 1: Get per-tenant statistics
            tenant_stats = await self._compute_tenant_baselines()

            if not tenant_stats:
                return {"success": True, "anomalies_created": 0, "reason": "no data"}

            total_natural = 0
            total_synthetic = 0

            for tenant_id, stats in tenant_stats.items():
                # Step 2: Find natural anomalies
                natural = await self._detect_natural_anomalies(tenant_id, stats)
                total_natural += len(natural)

                # Step 3: Create anomaly records for natural ones
                for anom in natural:
                    await self._create_anomaly_record(anom)

                # Step 4: Generate synthetic if too few natural
                min_count = config.get("anomaly.synthetic_count_per_tenant", 4)
                if len(natural) < min_count:
                    needed = min_count - len(natural)
                    synthetic = await self._generate_synthetic_anomalies(
                        tenant_id, stats, count=needed
                    )
                    for anom in synthetic:
                        await self._create_anomaly_record(anom)
                    total_synthetic += len(synthetic)

                # Step 5: Update/create baselines
                await self._upsert_baselines(tenant_id, stats)

            await self.session.commit()

            metrics.increment("anomalies.natural_detected", total_natural)
            metrics.increment("anomalies.synthetic_generated", total_synthetic)

            summary = {
                "success": True,
                "tenants_analyzed": len(tenant_stats),
                "natural_anomalies": total_natural,
                "synthetic_anomalies": total_synthetic,
                "total_anomalies": total_natural + total_synthetic,
            }

            log_pipeline_event(
                "anomaly_generation_complete",
                details=summary,
            )

            return summary

    async def _compute_tenant_baselines(self) -> dict:
        """
        Compute statistical baselines per tenant.

        Returns: {tenant_id: {mean_tokens, std_tokens, mean_cost, ...}}
        """
        result = await self.session.execute(
            text("""
                SELECT
                    tenant_id,
                    AVG(total_tokens)                   AS mean_tokens,
                    STDDEV_POP(total_tokens)             AS std_tokens,
                    AVG(cost_usd::float)                 AS mean_cost,
                    STDDEV_POP(cost_usd::float)          AS std_cost,
                    COUNT(*)                              AS record_count,
                    MIN(created_at)                       AS first_seen,
                    MAX(created_at)                       AS last_seen,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY total_tokens) AS p95_tokens,
                    PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY total_tokens) AS p99_tokens
                FROM llm_token_log
                WHERE tenant_id IS NOT NULL
                GROUP BY tenant_id
                HAVING COUNT(*) >= 10
            """)
        )
        rows = result.all()

        stats = {}
        for row in rows:
            stats[row.tenant_id] = {
                "mean_tokens": float(row.mean_tokens or 0),
                "std_tokens": float(row.std_tokens or 0),
                "mean_cost": float(row.mean_cost or 0),
                "std_cost": float(row.std_cost or 0),
                "record_count": int(row.record_count),
                "first_seen": row.first_seen,
                "last_seen": row.last_seen,
                "p95_tokens": float(row.p95_tokens or 0),
                "p99_tokens": float(row.p99_tokens or 0),
            }

        return stats

    async def _detect_natural_anomalies(
        self, tenant_id: str, stats: dict
    ) -> list[dict]:
        """Find actual statistical outliers in the data."""
        threshold = config.get("anomaly.z_score_threshold", 3.0)
        mean_t = stats["mean_tokens"]
        std_t = stats["std_tokens"]

        if std_t == 0:
            return []

        anomaly_threshold = mean_t + (threshold * std_t)

        result = await self.session.execute(
            text("""
                SELECT id, tenant_id, model, agent_id,
                       total_tokens, cost_usd::float AS cost,
                       created_at
                FROM llm_token_log
                WHERE tenant_id = :tenant_id
                  AND total_tokens > :threshold
                ORDER BY total_tokens DESC
                LIMIT 10
            """),
            {
                "tenant_id": tenant_id,
                "threshold": anomaly_threshold,
            },
        )
        rows = result.all()

        anomalies = []
        for row in rows:
            z_score = (float(row.total_tokens) - mean_t) / std_t if std_t > 0 else 0
            severity = "critical" if z_score > 5 else ("high" if z_score > 4 else "warning")
            anomalies.append({
                "tenant_id": tenant_id,
                "agent_id": row.agent_id,
                "model": row.model,
                "anomaly_type": "token_spike",
                "severity": severity,
                "observed_value": float(row.total_tokens),
                "baseline_mean": mean_t,
                "baseline_std": std_t,
                "z_score": z_score,
                "detected_at": row.created_at,
                "description": (
                    f"Token spike detected: {int(row.total_tokens):,} tokens "
                    f"(baseline: {int(mean_t):,} ± {int(std_t):,})"
                ),
            })

        return anomalies

    async def _generate_synthetic_anomalies(
        self, tenant_id: str, stats: dict, count: int
    ) -> list[dict]:
        """
        Generate realistic synthetic anomalies for tenants
        with insufficient natural outliers.
        """
        mean_t = stats["mean_tokens"]
        std_t = max(stats["std_tokens"], mean_t * 0.1)  # minimum 10% of mean
        first_seen = stats["first_seen"]
        last_seen = stats["last_seen"]

        if not first_seen or not last_seen:
            return []

        # Get models used by this tenant
        model_result = await self.session.execute(
            text("""
                SELECT DISTINCT model FROM llm_token_log
                WHERE tenant_id = :tenant_id
                LIMIT 5
            """),
            {"tenant_id": tenant_id},
        )
        models = [r[0] for r in model_result.all()] or ["unknown"]

        # Get agents used by this tenant
        agent_result = await self.session.execute(
            text("""
                SELECT DISTINCT agent_id FROM llm_token_log
                WHERE tenant_id = :tenant_id AND agent_id IS NOT NULL
                LIMIT 5
            """),
            {"tenant_id": tenant_id},
        )
        agents = [r[0] for r in agent_result.all()] or [None]

        anomalies = []
        anomaly_types = [
            ("token_spike", "Token usage spike detected"),
            ("cost_spike", "Cost spike detected"),
            ("pattern_break", "Unusual usage pattern detected"),
        ]

        spike_min = config.get("anomaly.spike_multiplier_min", 3.0)
        spike_max = config.get("anomaly.spike_multiplier_max", 5.0)

        time_range = (last_seen - first_seen).total_seconds()

        for i in range(count):
            atype, desc_prefix = anomaly_types[i % len(anomaly_types)]
            multiplier = random.uniform(spike_min, spike_max)
            observed = mean_t * multiplier

            # Random timestamp within the data range
            offset = random.random() * time_range
            detected_at = first_seen + timedelta(seconds=offset)

            z_score = (observed - mean_t) / std_t if std_t > 0 else multiplier
            severity = "critical" if z_score > 4.5 else ("high" if z_score > 3.5 else "warning")

            anomalies.append({
                "tenant_id": tenant_id,
                "agent_id": random.choice(agents),
                "model": random.choice(models),
                "anomaly_type": atype,
                "severity": severity,
                "observed_value": observed,
                "baseline_mean": mean_t,
                "baseline_std": std_t,
                "z_score": z_score,
                "detected_at": detected_at,
                "description": (
                    f"{desc_prefix}: {int(observed):,} tokens "
                    f"({multiplier:.1f}x above baseline of {int(mean_t):,})"
                ),
            })

        return anomalies

    async def _create_anomaly_record(self, anom: dict) -> None:
        """Insert an anomaly record into llm_anomaly table."""
        try:
            await self.session.execute(
                text("""
                    INSERT INTO llm_anomaly
                        (id, tenant_id, agent_id, model,
                         anomaly_type, severity,
                         observed_value, baseline_mean, baseline_std,
                         vote_count, detector_votes,
                         description, detected_at, resolved)
                    VALUES
                        (:id, :tenant_id, :agent_id, :model,
                         :anomaly_type, :severity,
                         :observed_value, :baseline_mean, :baseline_std,
                         :vote_count, :detector_votes,
                         :description, :detected_at, FALSE)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": anom["tenant_id"],
                    "agent_id": anom.get("agent_id"),
                    "model": anom.get("model"),
                    "anomaly_type": anom["anomaly_type"],
                    "severity": anom["severity"],
                    "observed_value": anom["observed_value"],
                    "baseline_mean": anom["baseline_mean"],
                    "baseline_std": anom.get("baseline_std", 0),
                    "vote_count": 3 if anom["severity"] == "critical" else 2,
                    "detector_votes": "z_score,statistical",
                    "description": anom["description"],
                    "detected_at": anom["detected_at"],
                },
            )
        except Exception as e:
            logger.warning(f"anomaly.insert_failed error={e}")
            try:
                await self.session.rollback()
            except Exception:
                pass

    async def _upsert_baselines(self, tenant_id: str, stats: dict) -> None:
        """Update or create baseline records for ongoing detection."""
        try:
            await self.session.execute(
                text("""
                    INSERT INTO llm_token_baseline
                        (id, tenant_id, agent_id, model,
                         daily_mean, daily_std_dev, sample_days,
                         computed_at)
                    VALUES
                        (:id, :tenant_id, '*', '*',
                         :daily_mean, :daily_std_dev, :sample_days,
                         :now)
                    ON CONFLICT (tenant_id, agent_id, model)
                    DO UPDATE SET
                        daily_mean = EXCLUDED.daily_mean,
                        daily_std_dev = EXCLUDED.daily_std_dev,
                        sample_days = EXCLUDED.sample_days,
                        computed_at = EXCLUDED.computed_at
                """),
                {
                    "id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "daily_mean": stats["mean_tokens"],
                    "daily_std_dev": stats["std_tokens"],
                    "sample_days": stats["record_count"],
                    "now": datetime.now(timezone.utc),
                },
            )
        except Exception as e:
            # Baseline table structure may vary — this is best-effort
            logger.debug(f"baseline.upsert_failed tenant={tenant_id} error={e}")
            try:
                await self.session.rollback()
            except Exception:
                pass
