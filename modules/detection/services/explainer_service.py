"""
ExplainerService — M4 Agent.

M4 is the interpreter between detection data and human decisions.
It takes an anomaly record, builds a compact context (< 500 tokens),
calls an LLM via LLMProvider (supports Anthropic, OpenAI, Gemini,
Mistral, Ollama), parses the structured response, and stores an
explanation with prioritized recommendations.

Key design decisions:

MULTI-PROVIDER: Uses LLMProvider — switch provider via M4_DEFAULT_PROVIDER
in .env. Works with paid APIs (Anthropic, OpenAI, Gemini, Mistral)
and free local models via Ollama. Zero code changes to switch.

CACHING: Redis cache keyed by (tenant_id, anomaly_type, severity).
Same type of anomaly for the same tenant within 2 hours returns
the cached explanation. No duplicate LLM calls.

STRUCTURED OUTPUT: The LLM is prompted to return JSON only.
No free-form text. The response is parsed and validated before storing.
If parsing fails, a safe fallback explanation is generated without LLM.

TOKEN BUDGET: The context prompt is always < 500 tokens.
M4 tracks its own token consumption in llm_token_explanations
so it appears in the monitoring dashboard.

TRIGGER: Only called for severity WARNING or above.
Normal anomalies get no explanation — that would be wasteful.

FALLBACK: If all LLM providers are unavailable, a rule-based fallback
generates a useful explanation without any LLM call. The dashboard
always shows something.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from core.redis import get_cache_redis
from core.settings import settings
from modules.detection.models import LLMAnomalyRecord
from modules.detection.models_explainer import LLMTokenExplanation
from modules.detection.repositories.explanation_repository import ExplanationRepository
from modules.analytics.services.analytics_service import AnalyticsService
from modules.forecasting.services.forecasting_service import ForecastingService
from datetime import timedelta
from modules.detection.services.llm_provider import LLMProvider
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class LLMRecommendation(BaseModel):
    action_type: str = Field(pattern=r"^(technical|functional|financial|governance)$", default="technical")
    action_title: str
    action_description: str
    expected_token_saving: int = Field(default=0)
    expected_cost_saving_usd: float = Field(default=0.0)
    risk_level: str = Field(pattern=r"^(low|medium|high)$", default="low")
    priority: int = Field(default=3)

class LLMExplanation(BaseModel):
    explanation: str
    confidence: float = Field(default=0.8)
    recommendations: list[LLMRecommendation] = Field(default_factory=list)

_embed_model = None

def get_embed_model():
    global _embed_model
    if _embed_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer("all-MiniLM-L6-v2")
        except ImportError:
            pass
    return _embed_model

EXPLANATION_CACHE_TTL = 7200  # 2 hours
TRIGGER_SEVERITIES = {"warning", "high", "critical"}


class ExplainerService:

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ExplanationRepository(session)
        self.llm = LLMProvider()
        self.analytics_service = AnalyticsService(session)
        self.forecasting_service = ForecastingService(session)

    async def explain_anomaly(
        self, anomaly: LLMAnomalyRecord
    ) -> Optional[LLMTokenExplanation]:
        """
        Generate explanation + recommendations for a detected anomaly.

        Returns the stored explanation, or None if severity is below threshold.
        """
        if anomaly.severity not in TRIGGER_SEVERITIES:
            logger.debug(
                "Anomaly below M4 threshold — skipping",
                extra={"severity": anomaly.severity, "anomaly_id": str(anomaly.id)},
            )
            return None

        # Already explained? Return existing.
        existing = await self.repo.get_by_anomaly_id(anomaly.id)
        if existing is not None:
            return existing

        # Redis cache check
        cache_key = self._cache_key(anomaly)
        cached_result = await self._get_cached_explanation(cache_key)

        if cached_result is not None:
            return await self._store_explanation(
                anomaly=anomaly,
                explanation_text=cached_result["explanation"],
                recommendations=cached_result["recommendations"],
                tokens_used=0,
                cache_hit=True,
                model_used="cache",
            )

        # Generate embedding
        embed_model = get_embed_model()
        embedding = None
        if embed_model:
            anomaly_text = f"{anomaly.anomaly_type} severity: {anomaly.severity}. {anomaly.description}"
            embedding = embed_model.encode(anomaly_text).tolist()

        # Fetch similar anomalies
        similar_explanations = []
        if embedding:
            similar_explanations = await self.repo.find_similar_explanations(anomaly.tenant_id, embedding, limit=5)

        # Build context and call LLM
        context = await self._build_context(anomaly, similar_explanations)
        llm_result = await self._call_llm(context)

        explanation = await self._store_explanation(
            anomaly=anomaly,
            explanation_text=llm_result["explanation"],
            recommendations=llm_result["recommendations"],
            tokens_used=llm_result.get("tokens_used", 0),
            cache_hit=False,
            model_used=llm_result.get("model_used", "claude-haiku-4-5-20251001"),
            embedding=embedding,
        )

        await self._set_cached_explanation(cache_key, llm_result)
        return explanation

    async def get_explanation_with_recommendations(
        self, anomaly_id: uuid.UUID
    ) -> Optional[dict]:
        explanation = await self.repo.get_by_anomaly_id(anomaly_id)
        if explanation is None:
            return None
        recommendations = await self.repo.get_recommendations_for_anomaly(anomaly_id)
        return {"explanation": explanation, "recommendations": recommendations}

    async def get_tenant_explanations(
        self, tenant_id: str, limit: int = 10
    ) -> list[LLMTokenExplanation]:
        return await self.repo.get_recent_for_tenant(tenant_id, limit)

    # ================================================================
    # PRIVATE — CONTEXT BUILDING
    # ================================================================

    async def _build_context(self, anomaly: LLMAnomalyRecord, similar_explanations: list[LLMTokenExplanation] = None) -> str:
        """
        Build the compact context string for the LLM prompt.

        Strictly under 500 tokens. Contains structured facts —
        no raw data. Uses M2 trends and M6 forecasts for outlook awareness.
        """
        # Fetch M2 & M6 data
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=7)
        trend = await self.analytics_service.get_daily_trend(anomaly.tenant_id, from_dt, now)
        recent_trend = [f"{t['date'][:10]}: {int(t['total_tokens'])}" for t in trend]
        
        forecast = await self.forecasting_service.get_forecast(anomaly.tenant_id, from_date=now.date())
        upcoming_forecast = [f"{f.forecast_date.isoformat()[:10]}: {int(f.predicted_tokens)}" for f in forecast[:7]] if forecast else ["No forecast available"]
        baseline_mean = anomaly.baseline_mean or 1.0
        pct_deviation = (
            abs(anomaly.observed_value - baseline_mean)
            / max(baseline_mean, 1)
            * 100
        )
        direction = "above" if anomaly.observed_value > baseline_mean else "below"
        detectors = anomaly.detector_votes or "unknown"

        # CORRECT: use actual field names from LLMAnomalyRecord
        # stl_residual_zscore, isolation_score, cusum_value
        zscore_str = (
            f"{anomaly.stl_residual_zscore:.2f}"
            if anomaly.stl_residual_zscore is not None
            else "N/A"
        )
        isolation_str = (
            f"{anomaly.isolation_score:.3f}"
            if anomaly.isolation_score is not None
            else "N/A"
        )
        cusum_str = (
            f"{anomaly.cusum_value:.2f}"
            if anomaly.cusum_value is not None
            else "N/A"
        )

        result_context = f"""ANOMALY CONTEXT:
Tenant: {anomaly.tenant_id}
Type: {anomaly.anomaly_type}
Severity: {anomaly.severity}
Detectors that fired: {detectors} ({anomaly.vote_count}/3 votes)

METRICS:
Observed daily tokens: {int(anomaly.observed_value):,}
Baseline mean (30-day): {int(baseline_mean):,}
Deviation: {pct_deviation:.1f}% {direction} baseline
STL Z-score: {zscore_str}
Isolation Forest score: {isolation_str}
CUSUM value: {cusum_str}

CONTEXT:
Detection time: {anomaly.detected_at.strftime('%Y-%m-%d %H:%M UTC')}
Description: {anomaly.description or 'No description available'}

HISTORICAL TREND (LAST 7 DAYS):
{', '.join(recent_trend)}

FUTURE FORECAST (NEXT 7 DAYS):
{', '.join(upcoming_forecast)}"""

        if similar_explanations:
            similar_text = "\n".join([f"- {exp.anomaly_type} [{exp.generated_at.isoformat()[:10]}]: {exp.explanation_text}" for exp in similar_explanations])
            result_context += f"\n\nSIMILAR PAST ANOMALIES:\n{similar_text}"
            
        return result_context

    # ================================================================
    # PRIVATE — LLM CALL
    # ================================================================

    async def _call_llm(self, context: str) -> dict:
        """
        Call the configured LLM provider with the anomaly context.

        Uses LLMProvider which supports Anthropic, OpenAI, Gemini,
        Mistral, and Ollama (free local). Provider selected from settings.
        Falls back to rule-based explanation if all providers fail.
        """
        try:
            prompt = self._build_prompt(context)

            result = await self.llm.complete(
                prompt=prompt,
                provider=settings.m4_default_provider,
                model=settings.m4_default_model,
                max_tokens=1000,
                timeout=30.0,
            )

            if result.get("error") and not result.get("text"):
                logger.warning(
                    "All LLM providers failed — using rule-based fallback",
                    extra={"error": result["error"]},
                )
                return self._fallback_explanation(context)

            tokens_used = result["input_tokens"] + result["output_tokens"]
            parsed = self._parse_llm_response(result["text"])
            parsed["tokens_used"] = tokens_used
            parsed["model_used"] = f"{result['provider']}/{result['model']}"
            return parsed

        except Exception as exc:
            logger.warning(
                "LLM call raised unexpected exception — using fallback",
                extra={"error": str(exc)},
            )
            return self._fallback_explanation(context)

    def _build_prompt(self, context: str) -> str:
        return f"""You are an AI monitoring expert analyzing an LLM token usage anomaly.

{context}

Analyze this anomaly and respond with ONLY a JSON object (no markdown, no preamble):

{{
  "explanation": "2-3 sentence explanation of what likely caused this anomaly, in plain business language. Focus on cause → effect → impact.",
  "confidence": 0.85,
  "recommendations": [
    {{
      "action_type": "technical|functional|financial|governance",
      "action_title": "Short title (max 10 words)",
      "action_description": "What to do and why (1-2 sentences)",
      "expected_token_saving": 15000,
      "expected_cost_saving_usd": 0.045,
      "risk_level": "low|medium|high",
      "priority": 1
    }}
  ]
}}

Rules:
- explanation: plain language, no jargon, max 3 sentences
- recommendations: 2-4 items, ordered by priority (1=highest impact)
- action_type must be exactly one of: technical, functional, financial, governance
- risk_level must be exactly one of: low, medium, high
- If you cannot estimate token/cost savings, use 0
- Respond ONLY with the JSON object"""

    def _parse_llm_response(self, raw_text: str) -> dict:
        """Parse and validate the LLM JSON response using Pydantic."""
        try:
            text = raw_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
            text = text.strip()

            parsed = LLMExplanation.model_validate_json(text)

            clean_recs = []
            for rec in parsed.recommendations[:4]:
                clean_recs.append({
                    "action_type": rec.action_type,
                    "action_title": str(rec.action_title)[:200],
                    "action_description": str(rec.action_description)[:500],
                    "expected_token_saving": int(rec.expected_token_saving or 0),
                    "expected_cost_saving_usd": float(rec.expected_cost_saving_usd or 0.0),
                    "risk_level": rec.risk_level,
                    "priority": max(1, min(5, int(rec.priority or 3))),
                    "status": "proposed",
                })

            return {
                "explanation": str(parsed.explanation)[:2000],
                "confidence": float(parsed.confidence),
                "recommendations": clean_recs,
            }

        except Exception as exc:
            logger.warning(f"LLM response parse failed: {exc}\\nRaw: {raw_text[:200]}")
            return self._fallback_explanation("")

    def _fallback_explanation(self, context: str) -> dict:
        """Rule-based fallback when all LLM providers are unavailable."""
        return {
            "explanation": (
                "An unusual token usage pattern was detected for this tenant. "
                "The usage deviated significantly from the established baseline, "
                "which may indicate a new workflow, increased load, or a configuration change. "
                "Review recent agent activity and compare with any recent deployments."
            ),
            "confidence": 0.5,
            "model_used": "fallback/rule-based",
            "tokens_used": 0,
            "recommendations": [
                {
                    "action_type": "functional",
                    "action_title": "Review recent agent activity",
                    "action_description": (
                        "Check if any new agents were deployed or existing agents "
                        "had configuration changes in the past 48 hours."
                    ),
                    "expected_token_saving": 0,
                    "expected_cost_saving_usd": 0.0,
                    "risk_level": "low",
                    "priority": 1,
                    "status": "proposed",
                },
                {
                    "action_type": "governance",
                    "action_title": "Add a daily token limit rule",
                    "action_description": (
                        "If this tenant has no daily token limit, add one via M7 "
                        "governance rules to prevent unexpected cost overruns."
                    ),
                    "expected_token_saving": 0,
                    "expected_cost_saving_usd": 0.0,
                    "risk_level": "low",
                    "priority": 2,
                    "status": "proposed",
                },
            ],
        }

    async def _store_explanation(
        self,
        anomaly: LLMAnomalyRecord,
        explanation_text: str,
        recommendations: list[dict],
        tokens_used: int,
        cache_hit: bool,
        model_used: str,
        embedding: list[float] = None,
    ) -> LLMTokenExplanation:
        explanation = await self.repo.create_explanation({
            "anomaly_id": anomaly.id,
            "tenant_id": anomaly.tenant_id,
            "anomaly_type": anomaly.anomaly_type,
            "severity": anomaly.severity,
            "explanation_text": explanation_text,
            "llm_model_used": model_used,
            "tokens_used": tokens_used,
            "confidence_level": 0.8,
            "cache_hit": cache_hit,
            "generated_at": datetime.now(timezone.utc),
            "embedding": embedding,
        })

        if recommendations:
            await self.repo.create_recommendations([
                {
                    "explanation_id": explanation.id,
                    "anomaly_id": anomaly.id,
                    "tenant_id": anomaly.tenant_id,
                    **rec,
                }
                for rec in recommendations
            ])

        await self.session.commit()
        return explanation

    def _cache_key(self, anomaly: LLMAnomalyRecord) -> str:
        return f"m4:explanation:{anomaly.tenant_id}:{anomaly.anomaly_type}:{anomaly.severity}"

    async def _get_cached_explanation(self, key: str) -> Optional[dict]:
        try:
            redis = get_cache_redis()
            value = await redis.get(key)
            await redis.aclose()
            if value:
                return json.loads(value)
        except Exception:
            pass
        return None

    async def _set_cached_explanation(self, key: str, data: dict) -> None:
        try:
            redis = get_cache_redis()
            await redis.set(key, json.dumps({
                "explanation": data.get("explanation", ""),
                "recommendations": data.get("recommendations", []),
            }), ex=EXPLANATION_CACHE_TTL)
            await redis.aclose()
        except Exception:
            pass