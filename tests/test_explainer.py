"""
M4 Explainer tests.

Tests cover:
1. Context building (no LLM needed)
2. Response parsing (no LLM needed)
3. Fallback behavior (no LLM needed)
4. Severity gating (no LLM needed)
5. API endpoints (mocked LLM call)
"""

import json
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.detection.services.explainer_service import ExplainerService
from modules.detection.models import LLMAnomalyRecord
from main import app


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture(scope="session")
async def db_session():
    engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    sf = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with sf() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


def make_anomaly(**kwargs) -> LLMAnomalyRecord:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": "enterprise_corp",
        "anomaly_type": "token_spike",
        "severity": "high",
        "detector_votes": "stl,cusum",
        "vote_count": 2,
        "stl_residual_zscore": 3.5,
        "isolation_score": -0.15,
        "cusum_value": 4.2,
        "observed_value": 2_500_000.0,
        "baseline_mean": 1_000_000.0,
        "baseline_std_dev": 150_000.0,
        "description": "HIGH anomaly: today's usage is 150% above baseline. Z-score: 3.50.",
        "resolved": False,
        "detected_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    return LLMAnomalyRecord(**defaults)


# ============================================================
# UNIT TESTS — no database, no LLM
# ============================================================

class TestContextBuilding:

    def test_context_contains_tenant_id(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(tenant_id="startup_ai")
        context = service._build_context(anomaly)
        assert "startup_ai" in context

    def test_context_contains_severity(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(severity="critical")
        context = service._build_context(anomaly)
        assert "critical" in context

    def test_context_contains_metrics(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(
            observed_value=2_500_000.0,
            baseline_mean=1_000_000.0,
        )
        context = service._build_context(anomaly)
        assert "2,500,000" in context
        assert "1,000,000" in context

    def test_context_computes_deviation_pct(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(
            observed_value=1_500_000.0,
            baseline_mean=1_000_000.0,
        )
        context = service._build_context(anomaly)
        assert "50.0%" in context

    def test_context_direction_above(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(
            observed_value=2_000_000.0,
            baseline_mean=1_000_000.0,
        )
        context = service._build_context(anomaly)
        assert "above" in context

    def test_context_direction_below(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(
            observed_value=500_000.0,
            baseline_mean=1_000_000.0,
        )
        context = service._build_context(anomaly)
        assert "below" in context


class TestResponseParsing:

    def test_valid_json_parsed_correctly(self, db_session):
        service = ExplainerService(db_session)
        raw = json.dumps({
            "explanation": "Usage spiked due to a new agent deployment.",
            "confidence": 0.9,
            "recommendations": [
                {
                    "action_type": "technical",
                    "action_title": "Reduce prompt size",
                    "action_description": "Shorten system prompts by 30%.",
                    "expected_token_saving": 15000,
                    "expected_cost_saving_usd": 0.045,
                    "risk_level": "low",
                    "priority": 1,
                }
            ]
        })
        result = service._parse_llm_response(raw)
        assert result["explanation"] == "Usage spiked due to a new agent deployment."
        assert len(result["recommendations"]) == 1
        assert result["recommendations"][0]["action_type"] == "technical"

    def test_markdown_fences_stripped(self, db_session):
        service = ExplainerService(db_session)
        raw = '```json\n{"explanation": "Test explanation", "recommendations": []}\n```'
        result = service._parse_llm_response(raw)
        assert result["explanation"] == "Test explanation"

    def test_invalid_json_returns_fallback(self, db_session):
        service = ExplainerService(db_session)
        result = service._parse_llm_response("This is not JSON at all")
        assert "explanation" in result
        assert len(result["explanation"]) > 0
        assert isinstance(result["recommendations"], list)

    def test_invalid_action_type_corrected(self, db_session):
        service = ExplainerService(db_session)
        raw = json.dumps({
            "explanation": "Test",
            "recommendations": [
                {
                    "action_type": "INVALID_TYPE",
                    "action_title": "Do something",
                    "priority": 1,
                }
            ]
        })
        result = service._parse_llm_response(raw)
        assert result["recommendations"][0]["action_type"] == "technical"

    def test_recommendations_capped_at_four(self, db_session):
        service = ExplainerService(db_session)
        recs = [
            {"action_type": "technical", "action_title": f"Action {i}", "priority": i}
            for i in range(1, 8)
        ]
        raw = json.dumps({"explanation": "Test", "recommendations": recs})
        result = service._parse_llm_response(raw)
        assert len(result["recommendations"]) <= 4


class TestFallback:

    def test_fallback_always_returns_explanation(self, db_session):
        service = ExplainerService(db_session)
        result = service._fallback_explanation("")
        assert "explanation" in result
        assert len(result["explanation"]) > 20

    def test_fallback_has_recommendations(self, db_session):
        service = ExplainerService(db_session)
        result = service._fallback_explanation("")
        assert len(result["recommendations"]) >= 1

    def test_fallback_tokens_used_is_zero(self, db_session):
        service = ExplainerService(db_session)
        result = service._fallback_explanation("")
        assert result["tokens_used"] == 0


class TestSeverityGating:

    @pytest.mark.asyncio
    async def test_normal_severity_returns_none(self, db_session):
        service = ExplainerService(db_session)
        anomaly = make_anomaly(severity="normal")
        result = await service.explain_anomaly(anomaly)
        assert result is None

    @pytest.mark.asyncio
    async def test_warning_severity_triggers_explanation(self, db_session):
        """Warning severity passes the gate. LLM is mocked."""
        service = ExplainerService(db_session)
        anomaly = make_anomaly(severity="warning")

        mock_result = {
            "explanation": "Mocked explanation for test.",
            "confidence": 0.8,
            "recommendations": [],
            "tokens_used": 50,
        }

        with patch.object(service, "_call_llm", return_value=mock_result):
            with patch.object(service, "_get_cached_explanation", return_value=None):
                with patch.object(service, "_set_cached_explanation", return_value=None):
                    result = await service.explain_anomaly(anomaly)

        assert result is not None
        assert result.explanation_text == "Mocked explanation for test."

    @pytest.mark.asyncio
    async def test_cache_hit_skips_llm(self, db_session):
        """If cache returns a result, LLM should not be called."""
        service = ExplainerService(db_session)
        anomaly = make_anomaly(severity="high", id=uuid.uuid4())

        cached = {
            "explanation": "Cached explanation.",
            "recommendations": [],
        }

        llm_called = []

        async def mock_llm(context):
            llm_called.append(True)
            return {"explanation": "LLM result", "recommendations": [], "tokens_used": 100}

        with patch.object(service, "_get_cached_explanation", return_value=cached):
            with patch.object(service, "_call_llm", side_effect=mock_llm):
                result = await service.explain_anomaly(anomaly)

        assert len(llm_called) == 0  # LLM was never called
        assert result.cache_hit is True


# ============================================================
# API TESTS
# ============================================================

@pytest.mark.asyncio
async def test_api_get_explanation_not_found(client):
    """Non-existent anomaly ID returns 404."""
    fake_id = str(uuid.uuid4())
    response = await client.get(f"/v1/detection/explain/{fake_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_get_explanation_invalid_uuid(client):
    """Invalid UUID format returns 400."""
    response = await client.get("/v1/detection/explain/not-a-uuid")
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_api_get_tenant_explanations(client):
    """GET /v1/detection/explanations/{tenant_id} returns valid structure."""
    response = await client.get("/v1/detection/explanations/enterprise_corp")
    assert response.status_code == 200
    body = response.json()
    assert "tenant_id" in body
    assert "explanations" in body
    assert "total" in body
    assert body["tenant_id"] == "enterprise_corp"


@pytest.mark.asyncio
async def test_api_cache_key_format(db_session):
    """Cache key has the right format for deduplication."""
    service = ExplainerService(db_session)
    anomaly = make_anomaly(
        tenant_id="startup_ai",
        anomaly_type="token_spike",
        severity="high",
    )
    key = service._cache_key(anomaly)
    assert key == "m4:explanation:startup_ai:token_spike:high"
    assert "m4:" in key

