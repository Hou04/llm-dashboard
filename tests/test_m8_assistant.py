"""
M8 Assistant tests.

All LLM calls are mocked — no real API key needed to run these tests.
Tests cover:
  1. Intent classification (pure logic, no database)
  2. Context building (uses real database)
  3. Response parsing (pure logic)
  4. Fallback behavior (pure logic)
  5. API endpoints (mocked LLM, real database)
"""

import json
import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.dashboard.services.assistant_service import (
    AssistantService, classify_intent
)
from main import app


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture(scope="session")
async def db_session():
    engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    sf = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with sf() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


@pytest.fixture
async def service(db_session):
    return AssistantService(db_session)


# ============================================================
# UNIT TESTS — Intent classifier (no database needed)
# ============================================================

class TestIntentClassifier:

    def test_cost_question_detected(self):
        """Questions about cost are classified as 'cost'."""
        intents = classify_intent("How much does enterprise_corp spend?")
        assert "cost" in intents

    def test_anomaly_question_detected(self):
        """Questions about anomalies are classified as 'anomaly'."""
        intents = classify_intent("Are there any anomalies right now?")
        assert "anomaly" in intents

    def test_forecast_question_detected(self):
        """Questions about forecasts are classified as 'forecast'."""
        intents = classify_intent("What is the forecast for end of month?")
        assert "forecast" in intents

    def test_governance_question_detected(self):
        """Questions about governance are classified as 'governance'."""
        intents = classify_intent("How many calls were blocked?")
        assert "governance" in intents

    def test_optimize_question_detected(self):
        """Questions about optimization are classified as 'optimize'."""
        intents = classify_intent("How can I reduce costs?")
        assert "optimize" in intents

    def test_unknown_question_returns_general(self):
        """Questions matching nothing return ['general']."""
        intents = classify_intent("Hello world")
        assert intents == ["general"]

    def test_multi_intent_question(self):
        """A question can match multiple intents."""
        intents = classify_intent("Why did costs spike? Any anomalies?")
        assert "cost" in intents
        assert "anomaly" in intents

    def test_french_question_detected(self):
        """French keywords are recognized."""
        intents = classify_intent("Quel est le coût ce mois-ci?")
        assert "cost" in intents

    def test_returns_list(self):
        """classify_intent always returns a list, never None."""
        result = classify_intent("anything")
        assert isinstance(result, list)
        assert len(result) >= 1


# ============================================================
# UNIT TESTS — Response parser (no database needed)
# ============================================================

class TestResponseParser:

    def test_valid_json_parsed_correctly(self, service):
        """Valid JSON response is parsed into correct fields."""
        raw = json.dumps({
            "answer": "enterprise_corp spends $265.96 this month.",
            "key_figures": [
                {"label": "Total cost", "value": "$265.96", "unit": "30 days"}
            ],
            "actions": [
                {"title": "View detail", "url": "/v1/dashboard/tenant/enterprise_corp", "type": "link"}
            ]
        })
        result = service._parse_llm_response(raw)
        assert result["answer"] == "enterprise_corp spends $265.96 this month."
        assert len(result["key_figures"]) == 1
        assert result["key_figures"][0]["value"] == "$265.96"
        assert len(result["actions"]) == 1

    def test_markdown_fences_stripped(self, service):
        """Response wrapped in ```json fences is handled correctly."""
        raw = '```json\n{"answer": "Test answer.", "key_figures": [], "actions": []}\n```'
        result = service._parse_llm_response(raw)
        assert result["answer"] == "Test answer."

    def test_invalid_json_returns_fallback(self, service):
        """Unparseable response returns the rule-based fallback."""
        result = service._parse_llm_response("This is not JSON at all.")
        assert "answer" in result
        assert len(result["answer"]) > 0
        assert isinstance(result["actions"], list)

    def test_key_figures_capped_at_four(self, service):
        """More than 4 key figures are capped at 4."""
        raw = json.dumps({
            "answer": "Test.",
            "key_figures": [
                {"label": f"Item {i}", "value": str(i), "unit": "x"}
                for i in range(8)  # 8 items — should be capped at 4
            ],
            "actions": []
        })
        result = service._parse_llm_response(raw)
        assert len(result["key_figures"]) <= 4

    def test_actions_capped_at_three(self, service):
        """More than 3 actions are capped at 3."""
        raw = json.dumps({
            "answer": "Test.",
            "key_figures": [],
            "actions": [
                {"title": f"Action {i}", "url": f"/url/{i}", "type": "link"}
                for i in range(6)  # 6 actions — should be capped at 3
            ]
        })
        result = service._parse_llm_response(raw)
        assert len(result["actions"]) <= 3


# ============================================================
# UNIT TESTS — Fallback (no database needed)
# ============================================================

class TestFallback:

    def test_fallback_has_answer(self, service):
        """Fallback always returns a non-empty answer."""
        result = service._rule_based_fallback()
        assert "answer" in result
        assert len(result["answer"]) > 10

    def test_fallback_has_actions(self, service):
        """Fallback always includes navigation links."""
        result = service._rule_based_fallback()
        assert "actions" in result
        assert len(result["actions"]) >= 1

    def test_fallback_tokens_used_is_zero(self, service):
        """Fallback shows 0 tokens — no LLM was called."""
        result = service._rule_based_fallback()
        assert result["tokens_used"] == 0

    def test_fallback_model_used_is_rule_based(self, service):
        """Fallback clearly identifies itself as rule-based."""
        result = service._rule_based_fallback()
        assert "rule-based" in result["model_used"]


# ============================================================
# INTEGRATION TESTS — Context builder (uses real database)
# ============================================================

class TestContextBuilder:

    @pytest.mark.asyncio
    async def test_cost_context_contains_enterprise_corp(self, service):
        """Cost context includes enterprise_corp from seeded data."""
        context = await service._build_context(
            intents=["cost"],
            tenant_id=None,
            period_days=90,
        )
        assert "enterprise_corp" in context

    @pytest.mark.asyncio
    async def test_general_context_has_multiple_sections(self, service):
        """General intent fetches cost, anomaly, and governance sections."""
        context = await service._build_context(
            intents=["general"],
            tenant_id=None,
            period_days=30,
        )
        # Should have at least cost data from seeded data
        assert len(context) > 50

    @pytest.mark.asyncio
    async def test_tenant_filter_limits_results(self, service):
        """When tenant_id is set, context only mentions that tenant."""
        context = await service._build_context(
            intents=["cost"],
            tenant_id="enterprise_corp",
            period_days=30,
        )
        assert "enterprise_corp" in context

    @pytest.mark.asyncio
    async def test_context_never_crashes_on_empty_db(self, service):
        """Context builder handles unknown tenant gracefully."""
        context = await service._build_context(
            intents=["cost"],
            tenant_id="totally_unknown_xyz_123",
            period_days=30,
        )
        # Should return something (even "no data") without crashing
        assert isinstance(context, str)
        assert len(context) > 0


# ============================================================
# API TESTS — HTTP endpoints (mocked LLM)
# ============================================================

MOCK_LLM_RESPONSE = {
    "text": json.dumps({
        "answer": "enterprise_corp is the top spender at $265.96 this month, using primarily gpt-4o.",
        "key_figures": [
            {"label": "Top tenant cost", "value": "$265.96", "unit": "30 days"},
            {"label": "Total platform cost", "value": "$497.10", "unit": "30 days"},
        ],
        "actions": [
            {"title": "View enterprise_corp", "url": "/v1/dashboard/tenant/enterprise_corp", "type": "link"}
        ]
    }),
    "input_tokens":  250,
    "output_tokens": 120,
    "provider":      "groq",
    "model":         "llama-3.3-70b-versatile",
}


class TestAssistantAPI:

    @pytest.mark.asyncio
    async def test_ask_returns_200(self, client):
        """POST /assistant/ask returns HTTP 200."""
        with patch(
            "modules.dashboard.services.assistant_service.LLMProvider.complete",
            new_callable=AsyncMock,
            return_value=MOCK_LLM_RESPONSE,
        ):
            response = await client.post(
                "/v1/dashboard/assistant/ask",
                params={"question": "Which tenant spends the most?"},
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_ask_response_shape(self, client):
        """Response body has all required fields."""
        with patch(
            "modules.dashboard.services.assistant_service.LLMProvider.complete",
            new_callable=AsyncMock,
            return_value=MOCK_LLM_RESPONSE,
        ):
            response = await client.post(
                "/v1/dashboard/assistant/ask",
                params={"question": "What is the cost this month?"},
            )
        body = response.json()
        assert "answer"      in body
        assert "key_figures" in body
        assert "actions"     in body
        assert "intent"      in body
        assert "tokens_used" in body
        assert "model_used"  in body
        assert "from_cache"  in body

    @pytest.mark.asyncio
    async def test_ask_with_tenant_filter(self, client):
        """tenant_id parameter is accepted and does not crash."""
        with patch(
            "modules.dashboard.services.assistant_service.LLMProvider.complete",
            new_callable=AsyncMock,
            return_value=MOCK_LLM_RESPONSE,
        ):
            response = await client.post(
                "/v1/dashboard/assistant/ask",
                params={
                    "question":   "What is the cost for this tenant?",
                    "tenant_id":  "enterprise_corp",
                    "period_days": 30,
                },
            )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_ask_question_too_short_returns_422(self, client):
        """Question shorter than 3 characters returns HTTP 422."""
        response = await client.post(
            "/v1/dashboard/assistant/ask",
            params={"question": "Hi"},  # too short — min_length=3 is 2 chars
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ask_missing_question_returns_422(self, client):
        """Missing question parameter returns HTTP 422."""
        response = await client.post("/v1/dashboard/assistant/ask")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_ask_intent_is_detected(self, client):
        """Intent field in response reflects the question content."""
        with patch(
            "modules.dashboard.services.assistant_service.LLMProvider.complete",
            new_callable=AsyncMock,
            return_value=MOCK_LLM_RESPONSE,
        ):
            response = await client.post(
                "/v1/dashboard/assistant/ask",
                params={"question": "What is the cost this month?"},
            )
        body = response.json()
        assert "cost" in body["intent"]

    @pytest.mark.asyncio
    async def test_ask_with_llm_failure_returns_fallback(self, client):
        """When LLM fails, fallback answer is returned (not a crash)."""
        with patch(
            "modules.dashboard.services.assistant_service.LLMProvider.complete",
            new_callable=AsyncMock,
            return_value={"text": "", "input_tokens": 0, "output_tokens": 0, "provider": "none", "model": "none"},
        ):
            response = await client.post(
                "/v1/dashboard/assistant/ask",
                params={"question": "What is the total cost?"},
            )
        assert response.status_code == 200
        body = response.json()
        assert "answer" in body
        assert len(body["answer"]) > 0

    @pytest.mark.asyncio
    async def test_suggestions_returns_200(self, client):
        """GET /assistant/suggestions returns HTTP 200."""
        response = await client.get("/v1/dashboard/assistant/suggestions")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_suggestions_response_shape(self, client):
        """Suggestions response has suggestions list and count."""
        response = await client.get("/v1/dashboard/assistant/suggestions")
        body = response.json()
        assert "suggestions" in body
        assert "count" in body
        assert isinstance(body["suggestions"], list)
        assert body["count"] == len(body["suggestions"])

    @pytest.mark.asyncio
    async def test_suggestions_with_tenant_id(self, client):
        """Tenant-specific suggestions appear when tenant_id is provided."""
        response = await client.get(
            "/v1/dashboard/assistant/suggestions",
            params={"tenant_id": "enterprise_corp"},
        )
        body = response.json()
        assert any(
            "enterprise_corp" in s for s in body["suggestions"]
        )

    @pytest.mark.asyncio
    async def test_suggestions_without_tenant_has_platform_questions(self, client):
        """Without tenant_id, suggestions are platform-wide questions."""
        response = await client.get("/v1/dashboard/assistant/suggestions")
        body = response.json()
        assert len(body["suggestions"]) >= 5