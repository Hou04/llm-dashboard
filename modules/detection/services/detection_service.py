"""
DetectionService — orchestrates ML detection.

Calls MLDetectionEngine with data fetched from repositories,
persists results, publishes events.
"""

import uuid
import math
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from core.event_bus import publish
from modules.detection.models import LLMAnomalyRecord, LLMTokenBaseline
from modules.detection.repositories import BaselineRepository, AnomalyRepository
from modules.detection.services.ml_engine import MLDetectionEngine, EnsembleResult

logger = logging.getLogger(__name__)

DEDUP_WINDOW_MINUTES = 60
MIN_BASELINE_MEAN = 100.0


@dataclass
class DetectionResult:
    tenant_id: str
    agent_id: str
    model: str
    fired: bool
    severity: str
    anomaly_type: str
    ensemble: Optional[EnsembleResult]
    anomaly_id: Optional[uuid.UUID] = None

    @property
    def is_anomaly(self) -> bool:
        return self.fired


class DetectionService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.baseline_repo = BaselineRepository(session)
        self.anomaly_repo = AnomalyRepository(session)
        self.engine = MLDetectionEngine()

    # ================================================================
    # BASELINE COMPUTATION
    # ================================================================

    async def compute_baseline_for_tenant(
        self, tenant_id: str, lookback_days: int = 30
    ) -> bool:
        from sqlalchemy import text
        
        # 1. Tenant global baseline
        res_global = await self._compute_and_save_baseline(tenant_id, "*", "*", lookback_days)
        
        # 2. Active Agents
        result = await self.session.execute(
            text("SELECT DISTINCT agent_id FROM llm_token_log WHERE tenant_id = :tid AND agent_id IS NOT NULL"),
            {"tid": tenant_id}
        )
        agents = [row[0] for row in result.all()]
        for a in agents:
            await self._compute_and_save_baseline(tenant_id, a, "*", lookback_days)
            
        # 3. Active Models
        result = await self.session.execute(
            text("SELECT DISTINCT model FROM llm_token_log WHERE tenant_id = :tid AND model IS NOT NULL"),
            {"tid": tenant_id}
        )
        models = [row[0] for row in result.all()]
        for m in models:
            await self._compute_and_save_baseline(tenant_id, "*", m, lookback_days)
            
        return res_global

    async def _compute_and_save_baseline(
        self, tenant_id: str, agent_id: str, model_name: str, lookback_days: int
    ) -> bool:
        history = await self.baseline_repo.get_daily_history(
            tenant_id, lookback_days, agent_id, model_name
        )

        if len(history) < 14:
            logger.info(
                "Insufficient history for baseline",
                extra={"tenant_id": tenant_id, "agent_id": agent_id, "model": model_name, "days": len(history)},
            )
            return False

        tokens = [d["tokens"] for d in history]
        calls = [d["calls"] for d in history]
        costs = [d["cost_usd"] for d in history]
        errors = [d["error_count"] for d in history]
        latencies = [d.get("avg_duration_ms", 0.0) for d in history]

        # --- Classical stats ---
        token_arr = np.array(tokens)
        call_arr = np.array(calls)
        cost_arr = np.array(costs)

        daily_mean = float(np.mean(token_arr))
        daily_std = float(np.std(token_arr, ddof=1)) if len(token_arr) > 1 else 0.0
        call_mean = float(np.mean(call_arr))
        call_std = float(np.std(call_arr, ddof=1)) if len(call_arr) > 1 else 0.0
        cost_mean = float(np.mean(cost_arr))
        cost_std = float(np.std(cost_arr, ddof=1)) if len(cost_arr) > 1 else 0.0
        error_rate_mean = float(
            np.mean([e / c * 100 if c > 0 else 0 for e, c in zip(errors, calls)])
        )

        # --- CUSUM initial slack ---
        cusum_k = 0.5  # standard choice, absorbs ~0.5σ noise per step

        # --- IsolationForest threshold calibration ---
        # Train on history, find the threshold that gives ~5% contamination
        feature_vecs = [
            MLDetectionEngine.build_feature_vector(t, c, co, e, l)
            for t, c, co, e, l in zip(tokens, calls, costs, errors, latencies)
        ]

        iso_threshold = self._calibrate_isolation_threshold(feature_vecs)

        data = {
            "daily_mean": daily_mean,
            "daily_std_dev": daily_std,
            "daily_min": float(np.min(token_arr)),
            "daily_max": float(np.max(token_arr)),
            "call_count_mean": call_mean,
            "call_count_std_dev": call_std,
            "daily_cost_mean": cost_mean,
            "daily_cost_std_dev": cost_std,
            "error_rate_mean": error_rate_mean,
            "cusum_pos": 0.0,       # reset CUSUM on recompute
            "cusum_neg": 0.0,
            "cusum_k": cusum_k,
            "isolation_forest_threshold": iso_threshold,
            "sample_days": len(history),
            "computed_at": datetime.now(timezone.utc),
            "baseline_from": history[0]["day"] if history else None,
            "baseline_to": history[-1]["day"] if history else None,
        }

        await self.baseline_repo.upsert_baseline(tenant_id, agent_id, model_name, data)
        await self.session.commit()

        logger.info(
            "Baseline computed",
            extra={
                "tenant_id": tenant_id,
                "agent_id": agent_id,
                "model": model_name,
                "daily_mean": round(daily_mean),
                "daily_std": round(daily_std),
                "sample_days": len(history),
                "iso_threshold": round(iso_threshold, 4),
            },
        )
        return True

    async def compute_baselines_for_all_tenants(
        self, tenant_ids: list[str], lookback_days: int = 30
    ) -> dict[str, bool]:
        return {
            tid: await self.compute_baseline_for_tenant(tid, lookback_days)
            for tid in tenant_ids
        }

    # ================================================================
    # REAL-TIME CHECK
    # ================================================================

    async def check_entity_now(
        self, tenant_id: str, agent_id: str = "*", model_name: str = "*", trigger_log_id: Optional[uuid.UUID] = None
    ) -> DetectionResult:
        """
        Run the ML ensemble check for a tenant/agent/model against today's usage so far.
        """
        baseline = await self.baseline_repo.get_baseline(tenant_id, agent_id, model_name)
        if baseline is None or baseline.daily_mean < MIN_BASELINE_MEAN:
            return DetectionResult(
                tenant_id=tenant_id,
                agent_id=agent_id,
                model=model_name,
                fired=False,
                severity="normal",
                anomaly_type="none",
                ensemble=None,
            )

        # Fetch historical data for ML detectors
        history_rows = await self.baseline_repo.get_daily_history(tenant_id, 30, agent_id, model_name)
        today = await self.baseline_repo.get_today_totals(tenant_id, agent_id, model_name)

        if len(history_rows) < 14:
            return DetectionResult(
                tenant_id=tenant_id,
                agent_id=agent_id,
                model=model_name,
                fired=False,
                severity="normal",
                anomaly_type="none",
                ensemble=None,
            )

        # Build inputs for ML engine
        history_tokens = [r["tokens"] for r in history_rows]
        history_features = [
            MLDetectionEngine.build_feature_vector(
                r["tokens"], r["calls"], r["cost_usd"], r["error_count"], r.get("avg_duration_ms", 0.0)
            )
            for r in history_rows
        ]
        today_features = MLDetectionEngine.build_feature_vector(
            today["tokens"], today["calls"], today["cost_usd"], today["error_count"], today.get("avg_duration_ms", 0.0)
        )

        # Run ensemble
        ensemble = self.engine.detect(
            history=history_tokens,
            today_value=today["tokens"],
            today_features=today_features,
            history_features=history_features,
            cusum_pos=baseline.cusum_pos,
            cusum_neg=baseline.cusum_neg,
            mean=baseline.daily_mean,
            std_dev=baseline.daily_std_dev,
            isolation_threshold=baseline.isolation_forest_threshold,
        )

        # Persist updated CUSUM state
        await self.baseline_repo.upsert_baseline(tenant_id, agent_id, model_name, {
            **self._baseline_to_dict(baseline),
            "cusum_pos": ensemble.new_cusum_pos,
            "cusum_neg": ensemble.new_cusum_neg,
        })

        result = DetectionResult(
            tenant_id=tenant_id,
            agent_id=agent_id,
            model=model_name,
            fired=ensemble.fired,
            severity=ensemble.severity,
            anomaly_type=ensemble.anomaly_type,
            ensemble=ensemble,
        )

        if result.is_anomaly:
            anomaly_id = await self._record_if_new(
                result, today, baseline, ensemble, trigger_log_id
            )
            result.anomaly_id = anomaly_id

        await self.session.commit()
        return result

    # ================================================================
    # GETTERS
    # ================================================================

    async def get_tenant_anomalies(
        self, tenant_id: str, include_resolved: bool = False, limit: int = 20
    ) -> list[LLMAnomalyRecord]:
        return await self.anomaly_repo.get_recent_for_tenant(
            tenant_id, limit=limit, include_resolved=include_resolved
        )

    async def get_all_recent_anomalies(
        self, hours: int = 24, severity: Optional[str] = None
    ) -> list[LLMAnomalyRecord]:
        return await self.anomaly_repo.get_all_recent(hours=hours, severity=severity)

    # ================================================================
    # PRIVATE
    # ================================================================

    def _calibrate_isolation_threshold(
        self, feature_vecs: list[list[float]]
    ) -> float:
        """
        Train IsolationForest on history and return the 5th percentile score.

        This is the threshold below which we consider a point anomalous.
        Calibrating on the training data ensures the threshold adapts to
        each tenant's specific usage patterns.
        """
        try:
            from sklearn.ensemble import IsolationForest
            X = np.array(feature_vecs, dtype=float)
            X = np.nan_to_num(X)
            clf = IsolationForest(contamination=0.05, n_estimators=100, random_state=42)
            clf.fit(X)
            scores = clf.score_samples(X)
            # 5th percentile of training scores = threshold
            return float(np.percentile(scores, 5))
        except Exception as exc:
            logger.warning(f"Threshold calibration failed: {exc}")
            return ISO_SCORE_THRESHOLD if "ISO_SCORE_THRESHOLD" in dir() else -0.1

    async def _record_if_new(
        self,
        result: DetectionResult,
        today: dict,
        baseline: LLMTokenBaseline,
        ensemble: EnsembleResult,
        trigger_log_id: Optional[uuid.UUID],
    ) -> Optional[uuid.UUID]:
        already = await self.anomaly_repo.has_recent_anomaly(
            result.tenant_id, result.anomaly_type, DEDUP_WINDOW_MINUTES
        )
        if already:
            return None

        detectors_str = ",".join(ensemble.detector_names)
        description = self._build_description(result, today, baseline, ensemble)

        anomaly = LLMAnomalyRecord(
            tenant_id=result.tenant_id,
            agent_id=result.agent_id,
            model=result.model,
            anomaly_type=result.anomaly_type,
            severity=result.severity,
            detector_votes=detectors_str,
            vote_count=ensemble.vote_count,
            stl_residual_zscore=ensemble.stl_residual_zscore,
            isolation_score=ensemble.isolation_score,
            cusum_value=ensemble.cusum_value,
            observed_value=today["tokens"],
            baseline_mean=baseline.daily_mean,
            baseline_std_dev=baseline.daily_std_dev,
            description=description,
            trigger_log_id=trigger_log_id,
            detected_at=datetime.now(timezone.utc),
        )

        saved = await self.anomaly_repo.create(anomaly)
        await self._publish_event(saved, result)

        logger.warning(
            "Anomaly recorded",
            extra={
                "tenant_id": result.tenant_id,
                "severity": result.severity,
                "detectors": detectors_str,
                "vote_count": ensemble.vote_count,
            },
        )
        return saved.id

    def _build_description(
        self,
        result: DetectionResult,
        today: dict,
        baseline: LLMTokenBaseline,
        ensemble: EnsembleResult,
    ) -> str:
        direction = "above" if (today["tokens"] - baseline.daily_mean) > 0 else "below"
        pct = abs(today["tokens"] - baseline.daily_mean) / max(baseline.daily_mean, 1) * 100
        detectors = ", ".join(ensemble.detector_names) or "none"
        return (
            f"{result.severity.upper()}: today's usage ({int(today['tokens']):,} tokens) "
            f"is {pct:.0f}% {direction} baseline ({int(baseline.daily_mean):,}). "
            f"Detectors: [{detectors}]. Votes: {ensemble.vote_count}/3."
        )

    async def _publish_event(
        self, anomaly: LLMAnomalyRecord, result: DetectionResult
    ) -> None:
        try:
            await publish("anomaly.detected", {
                "anomaly_id": str(anomaly.id),
                "tenant_id": anomaly.tenant_id,
                "agent_id": anomaly.agent_id,
                "model": anomaly.model,
                "anomaly_type": anomaly.anomaly_type,
                "severity": anomaly.severity,
                "detector_votes": anomaly.detector_votes,
                "vote_count": anomaly.vote_count,
                "stl_residual_zscore": anomaly.stl_residual_zscore,
                "isolation_score": anomaly.isolation_score,
                "cusum_value": anomaly.cusum_value,
                "observed_value": anomaly.observed_value,
                "baseline_mean": anomaly.baseline_mean,
                "description": anomaly.description,
                "detected_at": anomaly.detected_at.isoformat(),
            })
        except Exception as exc:
            logger.warning(f"Failed to publish anomaly event: {exc}")

    @staticmethod
    def _baseline_to_dict(b: LLMTokenBaseline) -> dict:
        return {
            "daily_mean": b.daily_mean,
            "daily_std_dev": b.daily_std_dev,
            "daily_min": b.daily_min,
            "daily_max": b.daily_max,
            "call_count_mean": b.call_count_mean,
            "call_count_std_dev": b.call_count_std_dev,
            "daily_cost_mean": b.daily_cost_mean,
            "daily_cost_std_dev": b.daily_cost_std_dev,
            "error_rate_mean": b.error_rate_mean,
            "cusum_pos": b.cusum_pos,
            "cusum_neg": b.cusum_neg,
            "cusum_k": b.cusum_k,
            "isolation_forest_threshold": b.isolation_forest_threshold,
            "sample_days": b.sample_days,
            "computed_at": b.computed_at,
            "baseline_from": b.baseline_from,
            "baseline_to": b.baseline_to,
        }


# re-export for ml_engine module reference
from modules.detection.services.ml_engine import ISO_SCORE_THRESHOLD  # noqa