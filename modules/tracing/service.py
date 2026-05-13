"""
Session Tracing, A/B Testing & Alert Config — Service layer.
"""

import logging
import math
import random
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import select, func, and_, update
from sqlalchemy.ext.asyncio import AsyncSession

from modules.tracing.models import (
    LLMSession, SessionStatus,
    LLMExperiment, ExperimentStatus,
    LLMAlertConfig, LLMAlertHistory,
)

logger = logging.getLogger(__name__)


# ============================================================
# FEATURE 8: SESSION SERVICE
# ============================================================

class SessionService:
    """Business logic for session tracing."""

    def __init__(self, session: AsyncSession) -> None:
        self.db = session

    async def get_or_create_session(
        self,
        session_id: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        name: Optional[str] = None,
        metadata: Optional[dict] = None,
        tags: Optional[list] = None,
    ) -> LLMSession:
        """Get existing session or create a new one."""
        result = await self.db.execute(
            select(LLMSession).where(LLMSession.session_id == session_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        sess = LLMSession(
            id=str(uuid.uuid4()),
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            agent_id=agent_id,
            name=name,
            metadata_=metadata,
            tags=tags,
        )
        self.db.add(sess)
        await self.db.flush()
        return sess

    async def record_call(
        self,
        session_id: str,
        log_id: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        cost_usd: float,
        duration_ms: int = 0,
    ) -> Optional[LLMSession]:
        """
        Record a new LLM call in a session.
        Updates aggregated metrics and appends to call chain.
        """
        result = await self.db.execute(
            select(LLMSession).where(LLMSession.session_id == session_id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            return None

        sess.total_calls += 1
        sess.total_input_tokens += input_tokens
        sess.total_output_tokens += output_tokens
        sess.total_tokens += total_tokens
        sess.total_cost_usd += cost_usd
        sess.total_duration_ms += duration_ms

        # Append to call chain
        chain = sess.call_chain or []
        chain.append(log_id)
        sess.call_chain = chain

        await self.db.flush()
        return sess

    async def complete_session(
        self, session_id: str, status: str = SessionStatus.COMPLETED.value
    ) -> Optional[LLMSession]:
        """Mark a session as completed."""
        result = await self.db.execute(
            select(LLMSession).where(LLMSession.session_id == session_id)
        )
        sess = result.scalar_one_or_none()
        if sess is None:
            return None

        sess.status = status
        sess.ended_at = datetime.now(timezone.utc)
        await self.db.flush()
        await self.db.commit()
        return sess

    async def get_session(self, session_id: str) -> Optional[LLMSession]:
        result = await self.db.execute(
            select(LLMSession).where(LLMSession.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def list_sessions(
        self,
        tenant_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[LLMSession], int]:
        conditions = []
        if tenant_id:
            conditions.append(LLMSession.tenant_id == tenant_id)
        if agent_id:
            conditions.append(LLMSession.agent_id == agent_id)
        if status:
            conditions.append(LLMSession.status == status)

        query = select(LLMSession)
        count_query = select(func.count(LLMSession.id))
        if conditions:
            query = query.where(and_(*conditions))
            count_query = count_query.where(and_(*conditions))

        query = query.order_by(LLMSession.started_at.desc()).offset((page - 1) * page_size).limit(page_size)

        results = await self.db.execute(query)
        sessions = list(results.scalars().all())
        total = int((await self.db.execute(count_query)).scalar() or 0)
        return sessions, total


# ============================================================
# FEATURE 9: EXPERIMENT SERVICE
# ============================================================

class ExperimentService:
    """Business logic for prompt A/B testing."""

    def __init__(self, session: AsyncSession) -> None:
        self.db = session

    async def create_experiment(
        self,
        tenant_id: str,
        name: str,
        prompt_name: str,
        variant_a_version: int,
        variant_b_version: int,
        created_by: str,
        traffic_split: float = 0.5,
        primary_metric: str = "quality_score",
        min_samples: int = 100,
        description: Optional[str] = None,
    ) -> LLMExperiment:
        exp = LLMExperiment(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            description=description,
            prompt_name=prompt_name,
            variant_a_version=variant_a_version,
            variant_b_version=variant_b_version,
            traffic_split=traffic_split,
            primary_metric=primary_metric,
            min_samples=min_samples,
            created_by=created_by,
        )
        self.db.add(exp)
        await self.db.flush()
        await self.db.commit()
        return exp

    async def start_experiment(self, experiment_id: str) -> Optional[LLMExperiment]:
        exp = await self._get(experiment_id)
        if exp is None:
            return None
        exp.status = ExperimentStatus.RUNNING.value
        exp.started_at = datetime.now(timezone.utc)
        await self.db.commit()
        return exp

    async def pause_experiment(self, experiment_id: str) -> Optional[LLMExperiment]:
        exp = await self._get(experiment_id)
        if exp is None:
            return None
        exp.status = ExperimentStatus.PAUSED.value
        await self.db.commit()
        return exp

    async def complete_experiment(self, experiment_id: str) -> Optional[LLMExperiment]:
        exp = await self._get(experiment_id)
        if exp is None:
            return None
        exp.status = ExperimentStatus.COMPLETED.value
        exp.ended_at = datetime.now(timezone.utc)
        await self.db.commit()
        return exp

    async def route_traffic(
        self, tenant_id: str, prompt_name: str
    ) -> Optional[tuple[str, int]]:
        """
        Route traffic for a running experiment.
        Returns (variant, version) tuple, or None if no active experiment.
        """
        result = await self.db.execute(
            select(LLMExperiment).where(
                LLMExperiment.tenant_id == tenant_id,
                LLMExperiment.prompt_name == prompt_name,
                LLMExperiment.status == ExperimentStatus.RUNNING.value,
            )
        )
        exp = result.scalar_one_or_none()
        if exp is None:
            return None

        variant = exp.select_variant()
        version = exp.variant_a_version if variant == "A" else exp.variant_b_version
        return variant, version

    async def record_result(
        self,
        experiment_id: str,
        variant: str,
        latency_ms: float = 0,
        cost_usd: float = 0,
        quality_score: float = 0,
        tokens: int = 0,
        is_error: bool = False,
    ) -> Optional[LLMExperiment]:
        """Record a single call result for a variant."""
        exp = await self._get(experiment_id)
        if exp is None:
            return None

        if variant == "A":
            exp.a_requests += 1
            exp.a_total_tokens += tokens
            if is_error:
                exp.a_error_count += 1
            # Running average
            n = exp.a_requests
            exp.a_avg_latency_ms = exp.a_avg_latency_ms + (latency_ms - exp.a_avg_latency_ms) / n
            exp.a_avg_cost_usd = exp.a_avg_cost_usd + (cost_usd - exp.a_avg_cost_usd) / n
            exp.a_avg_quality = exp.a_avg_quality + (quality_score - exp.a_avg_quality) / n
        else:
            exp.b_requests += 1
            exp.b_total_tokens += tokens
            if is_error:
                exp.b_error_count += 1
            n = exp.b_requests
            exp.b_avg_latency_ms = exp.b_avg_latency_ms + (latency_ms - exp.b_avg_latency_ms) / n
            exp.b_avg_cost_usd = exp.b_avg_cost_usd + (cost_usd - exp.b_avg_cost_usd) / n
            exp.b_avg_quality = exp.b_avg_quality + (quality_score - exp.b_avg_quality) / n

        # Check statistical significance
        if exp.a_requests >= exp.min_samples and exp.b_requests >= exp.min_samples:
            self._check_significance(exp)

        await self.db.flush()
        return exp

    def _check_significance(self, exp: LLMExperiment) -> None:
        """Simple z-test for proportions or means."""
        metric = exp.primary_metric
        if metric == "quality_score":
            a_val, b_val = exp.a_avg_quality, exp.b_avg_quality
        elif metric == "latency":
            a_val, b_val = exp.a_avg_latency_ms, exp.b_avg_latency_ms
        elif metric == "cost":
            a_val, b_val = exp.a_avg_cost_usd, exp.b_avg_cost_usd
        elif metric == "error_rate":
            a_val = exp.a_error_count / max(exp.a_requests, 1)
            b_val = exp.b_error_count / max(exp.b_requests, 1)
        else:
            return

        # Approximate z-test (simplified)
        n_a, n_b = exp.a_requests, exp.b_requests
        pooled = (a_val * n_a + b_val * n_b) / (n_a + n_b)
        if pooled == 0 or pooled == 1:
            return

        se = math.sqrt(pooled * (1 - min(pooled, 0.999)) * (1/n_a + 1/n_b)) or 0.001
        z = abs(a_val - b_val) / se

        # Two-tailed p-value approximation
        p_value = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
        exp.p_value = round(p_value, 6)
        exp.confidence_level = round(1 - p_value, 4)

        if p_value < 0.05:
            # Determine winner based on metric direction
            if metric in ("latency", "cost", "error_rate"):
                exp.winner = "A" if a_val < b_val else "B"  # Lower is better
            else:
                exp.winner = "A" if a_val > b_val else "B"  # Higher is better

            exp.status = ExperimentStatus.COMPLETED.value
            exp.ended_at = datetime.now(timezone.utc)
            logger.info(f"experiment.winner_declared name={exp.name} winner={exp.winner} p={p_value:.4f}")

    async def get_results(self, experiment_id: str) -> Optional[dict]:
        """Get detailed comparison results."""
        exp = await self._get(experiment_id)
        if exp is None:
            return None

        a_err = exp.a_error_count / max(exp.a_requests, 1)
        b_err = exp.b_error_count / max(exp.b_requests, 1)

        lat_imp = None
        if exp.a_avg_latency_ms > 0:
            lat_imp = round((exp.a_avg_latency_ms - exp.b_avg_latency_ms) / exp.a_avg_latency_ms * 100, 2)

        cost_imp = None
        if exp.a_avg_cost_usd > 0:
            cost_imp = round((exp.a_avg_cost_usd - exp.b_avg_cost_usd) / exp.a_avg_cost_usd * 100, 2)

        qual_imp = None
        if exp.a_avg_quality > 0:
            qual_imp = round((exp.b_avg_quality - exp.a_avg_quality) / exp.a_avg_quality * 100, 2)

        is_sig = exp.p_value is not None and exp.p_value < 0.05
        if exp.winner:
            rec = f"Variant {exp.winner} is the winner with {(exp.confidence_level or 0)*100:.1f}% confidence."
        elif exp.a_requests < exp.min_samples or exp.b_requests < exp.min_samples:
            remaining = max(exp.min_samples - min(exp.a_requests, exp.b_requests), 0)
            rec = f"Need {remaining} more samples before declaring a winner."
        else:
            rec = "No statistically significant difference found yet."

        return {
            "a_error_rate": round(a_err, 4),
            "b_error_rate": round(b_err, 4),
            "latency_improvement_pct": lat_imp,
            "cost_improvement_pct": cost_imp,
            "quality_improvement_pct": qual_imp,
            "is_significant": is_sig,
            "recommendation": rec,
        }

    async def list_experiments(
        self, tenant_id: Optional[str] = None, status: Optional[str] = None
    ) -> tuple[list[LLMExperiment], int]:
        conditions = []
        if tenant_id:
            conditions.append(LLMExperiment.tenant_id == tenant_id)
        if status:
            conditions.append(LLMExperiment.status == status)

        query = select(LLMExperiment)
        if conditions:
            query = query.where(and_(*conditions))
        query = query.order_by(LLMExperiment.created_at.desc())

        results = await self.db.execute(query)
        experiments = list(results.scalars().all())
        return experiments, len(experiments)

    async def _get(self, experiment_id: str) -> Optional[LLMExperiment]:
        result = await self.db.execute(
            select(LLMExperiment).where(LLMExperiment.id == experiment_id)
        )
        return result.scalar_one_or_none()


# ============================================================
# FEATURE 10: ALERT CONFIG SERVICE
# ============================================================

class AlertConfigService:
    """Business logic for per-tenant alert configuration."""

    def __init__(self, session: AsyncSession) -> None:
        self.db = session

    async def create_config(
        self,
        tenant_id: str,
        name: str,
        channel_type: str,
        webhook_url: str,
        created_by: str,
        alert_types: Optional[list[str]] = None,
        severity_filter: Optional[str] = None,
        is_active: bool = True,
    ) -> LLMAlertConfig:
        config = LLMAlertConfig(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            channel_type=channel_type,
            webhook_url=webhook_url,
            created_by=created_by,
            alert_types=alert_types,
            severity_filter=severity_filter,
            is_active=is_active,
        )
        self.db.add(config)
        await self.db.flush()
        await self.db.commit()
        return config

    async def update_config(
        self, config_id: str, **kwargs
    ) -> Optional[LLMAlertConfig]:
        result = await self.db.execute(
            select(LLMAlertConfig).where(LLMAlertConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            return None

        for key, value in kwargs.items():
            if hasattr(config, key) and value is not None:
                setattr(config, key, value)

        await self.db.commit()
        return config

    async def delete_config(self, config_id: str) -> bool:
        result = await self.db.execute(
            select(LLMAlertConfig).where(LLMAlertConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            return False
        await self.db.delete(config)
        await self.db.commit()
        return True

    async def test_webhook(self, config_id: str) -> dict:
        """Send a test ping to verify connectivity."""
        result = await self.db.execute(
            select(LLMAlertConfig).where(LLMAlertConfig.id == config_id)
        )
        config = result.scalar_one_or_none()
        if config is None:
            return {"success": False, "message": "Config not found", "status_code": None}

        test_payload = {
            "event": "test",
            "message": f"Test alert from LLM Dashboard — channel: {config.name}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(config.webhook_url, json=test_payload)
                success = response.status_code < 300

                # Update last_tested_at
                config.last_tested_at = datetime.now(timezone.utc)
                await self.db.commit()

                # Log to history
                await self._log_alert(config, "test", test_payload, success, response.status_code)

                return {
                    "success": success,
                    "status_code": response.status_code,
                    "message": "Test alert sent successfully" if success else f"Webhook returned {response.status_code}",
                }
        except Exception as e:
            await self._log_alert(config, "test", test_payload, False, None, str(e))
            return {"success": False, "status_code": None, "message": str(e)}

    async def list_configs(
        self, tenant_id: Optional[str] = None
    ) -> list[LLMAlertConfig]:
        query = select(LLMAlertConfig)
        if tenant_id:
            query = query.where(LLMAlertConfig.tenant_id == tenant_id)
        query = query.order_by(LLMAlertConfig.created_at.desc())
        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def get_alert_history(
        self,
        tenant_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[LLMAlertHistory], int]:
        conditions = []
        if tenant_id:
            conditions.append(LLMAlertHistory.tenant_id == tenant_id)

        query = select(LLMAlertHistory)
        count_q = select(func.count(LLMAlertHistory.id))
        if conditions:
            query = query.where(and_(*conditions))
            count_q = count_q.where(and_(*conditions))

        query = query.order_by(LLMAlertHistory.sent_at.desc()).offset((page - 1) * page_size).limit(page_size)
        results = await self.db.execute(query)
        total = int((await self.db.execute(count_q)).scalar() or 0)
        return list(results.scalars().all()), total

    async def _log_alert(
        self,
        config: LLMAlertConfig,
        alert_type: str,
        payload: dict,
        success: bool,
        status_code: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> None:
        entry = LLMAlertHistory(
            id=str(uuid.uuid4()),
            tenant_id=config.tenant_id,
            config_id=config.id,
            channel_type=config.channel_type,
            alert_type=alert_type,
            payload=payload,
            success=success,
            status_code=status_code,
            error_message=error_message,
        )
        self.db.add(entry)

        if success:
            config.total_alerts_sent += 1
            config.last_alert_at = datetime.now(timezone.utc)

        await self.db.flush()
        await self.db.commit()
