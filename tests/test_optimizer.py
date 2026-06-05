"""
M5 Optimizer tests.

Tests cover:
1. Model classifier unit tests (pure logic, no database)
2. ROI computation accuracy
3. Prompt pattern detection
4. Integration tests using seeded data
5. API tests
"""

import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.forecasting.services.optimizer_service import (
    OptimizerService,
)
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


@pytest.fixture
async def service(db_session):
    return OptimizerService(db_session)


def make_profile(**kwargs) -> dict:
    defaults = {
        "agent_id": "test-agent",
        "model": "gpt-4o",
        "call_count": 1000,
        "avg_input_tokens": 300.0,
        "avg_output_tokens": 200.0,
        "avg_total_tokens": 500.0,
        "avg_cost_per_call": 0.0028,
        "error_rate_pct": 1.5,
        "total_cost_usd": 2.80,
        "total_tokens": 500_000,
    }
    defaults.update(kwargs)
    return defaults


# ============================================================
# UNIT TESTS — MODEL CLASSIFIER
# ============================================================

class TestModelClassifier:

    def test_downgrade_recommended_for_simple_agent(self, service):
        """gpt-4o agent with simple profile should get downgrade to mini."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=150.0,
            avg_output_tokens=200.0,
            error_rate_pct=1.0,
            call_count=1000,
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is not None
        assert rec["recommended_model"] == "gpt-4o-mini"
        assert rec["justification_type"] == "low_complexity"

    def test_no_downgrade_for_complex_agent(self, service):
        """High token usage agent should not be downgraded."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=1200.0,
            avg_output_tokens=800.0,
            error_rate_pct=2.0,
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is None

    def test_no_downgrade_for_high_error_rate(self, service):
        """Agent with high error rate should not be downgraded."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=100.0,
            avg_output_tokens=150.0,
            error_rate_pct=8.0,  # above threshold
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is None

    def test_upgrade_recommended_for_high_error_rate(self, service):
        """mini agent with high error rate should get upgrade recommendation."""
        profile = make_profile(
            model="gpt-4o-mini",
            avg_input_tokens=200.0,
            avg_output_tokens=300.0,
            error_rate_pct=15.0,  # above UPGRADE_MIN_ERROR_RATE
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is not None
        assert rec["recommended_model"] == "gpt-4o"
        assert rec["justification_type"] == "high_error_rate"

    def test_no_recommendation_for_already_optimal_model(self, service):
        """mini agent with normal usage needs no change."""
        profile = make_profile(
            model="gpt-4o-mini",
            avg_input_tokens=200.0,
            avg_output_tokens=300.0,
            error_rate_pct=2.0,
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is None

    def test_recommendation_includes_roi(self, service):
        """Recommendation always includes ROI fields."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=150.0,
            avg_output_tokens=200.0,
            error_rate_pct=1.0,
            call_count=5000,
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is not None
        assert "expected_monthly_saving_usd" in rec
        assert "current_monthly_cost_usd" in rec
        assert "projected_monthly_cost_usd" in rec
        assert float(rec["expected_monthly_saving_usd"]) > 0

    def test_confidence_high_for_clearly_simple_agent(self, service):
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=100.0,
            avg_output_tokens=150.0,
            error_rate_pct=0.5,
        )
        rec = service._classify_model("test_tenant", profile)
        if rec:
            assert rec["confidence"] in ("high", "medium")

    def test_minimum_saving_threshold_respected(self, service):
        """Very few calls means saving is below threshold — no recommendation."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=100.0,
            avg_output_tokens=100.0,
            error_rate_pct=1.0,
            call_count=2,  # almost no calls
        )
        rec = service._classify_model("test_tenant", profile)
        assert rec is None


# ============================================================
# UNIT TESTS — ROI COMPUTATION
# ============================================================

class TestROIComputation:

    def test_downgrade_always_saves_money(self, service):
        """Switching from gpt-4o to gpt-4o-mini always reduces cost."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=300.0,
            avg_output_tokens=200.0,
            call_count=1000,
        )
        result = service._compute_saving(profile, "gpt-4o", "gpt-4o-mini", 1000)
        assert result["monthly_saving"] > 0
        assert result["saving_pct"] > 0

    def test_upgrade_costs_more(self, service):
        """Switching from mini to gpt-4o increases cost."""
        profile = make_profile(
            model="gpt-4o-mini",
            avg_input_tokens=300.0,
            avg_output_tokens=200.0,
            call_count=1000,
        )
        result = service._compute_saving(profile, "gpt-4o-mini", "gpt-4o", 1000)
        assert result["monthly_saving"] < 0  # negative saving = cost increase

    def test_saving_scales_with_call_volume(self, service):
        """More calls means more saving."""
        profile_low = make_profile(
            model="gpt-4o", avg_input_tokens=200.0, avg_output_tokens=200.0
        )
        profile_high = make_profile(
            model="gpt-4o", avg_input_tokens=200.0, avg_output_tokens=200.0
        )
        low = service._compute_saving(profile_low, "gpt-4o", "gpt-4o-mini", 100)
        high = service._compute_saving(profile_high, "gpt-4o", "gpt-4o-mini", 10000)
        assert high["monthly_saving"] > low["monthly_saving"]

    def test_gpt4o_to_mini_saving_pct_reasonable(self, service):
        """gpt-4o to mini saving should be roughly 80-95% given pricing."""
        profile = make_profile(
            model="gpt-4o",
            avg_input_tokens=300.0,
            avg_output_tokens=200.0,
            call_count=1000,
        )
        result = service._compute_saving(profile, "gpt-4o", "gpt-4o-mini", 1000)
        # gpt-4o-mini is ~10-15x cheaper than gpt-4o
        assert 70.0 < result["saving_pct"] < 99.0


# ============================================================
# UNIT TESTS — PROMPT PATTERN DETECTION
# ============================================================

class TestPromptPatternDetection:

    def test_verbose_prompt_detected(self, service):
        """Agent with high avg_input tokens gets verbose prompt recommendation."""
        profile = make_profile(avg_input_tokens=1200.0, avg_output_tokens=200.0)
        opts = service._analyze_prompt_patterns("test_tenant", profile)
        types = [o["optimization_type"] for o in opts]
        assert "verbose_system_prompt" in types

    def test_normal_input_no_verbose_flag(self, service):
        """Agent with normal input tokens does not get verbose prompt flag."""
        profile = make_profile(avg_input_tokens=200.0, avg_output_tokens=200.0)
        opts = service._analyze_prompt_patterns("test_tenant", profile)
        types = [o["optimization_type"] for o in opts]
        assert "verbose_system_prompt" not in types

    def test_high_output_gets_format_recommendation(self, service):
        """Agent with high avg_output gets missing output format recommendation."""
        profile = make_profile(avg_input_tokens=200.0, avg_output_tokens=900.0)
        opts = service._analyze_prompt_patterns("test_tenant", profile)
        types = [o["optimization_type"] for o in opts]
        assert "missing_output_format" in types

    def test_optimization_includes_roi(self, service):
        """Every optimization record has ROI fields."""
        profile = make_profile(avg_input_tokens=1500.0, avg_output_tokens=200.0)
        opts = service._analyze_prompt_patterns("test_tenant", profile)
        assert len(opts) > 0
        for opt in opts:
            assert "expected_token_saving_monthly" in opt
            assert "expected_cost_saving_monthly_usd" in opt
            assert opt["expected_token_saving_monthly"] > 0

    def test_priority_high_for_very_verbose(self, service):
        """Very verbose agents (>1200 tokens) get priority 1."""
        profile = make_profile(avg_input_tokens=1500.0, avg_output_tokens=200.0)
        opts = service._analyze_prompt_patterns("test_tenant", profile)
        verbose = [o for o in opts if o["optimization_type"] == "verbose_system_prompt"]
        assert len(verbose) > 0
        assert verbose[0]["priority"] == 1


# ============================================================
# INTEGRATION TESTS — uses seeded database
# ============================================================

@pytest.mark.asyncio
async def test_run_optimization_enterprise_corp(db_session):
    """enterprise_corp has multiple agents — optimization should find candidates."""
    service = OptimizerService(db_session)
    result = await service.run_optimization_for_tenant("enterprise_corp", 30)
    assert result["agents_analyzed"] >= 1
    assert result["tenant_id"] == "enterprise_corp"


@pytest.mark.asyncio
async def test_optimization_result_structure(db_session):
    """Optimization result has all required fields."""
    service = OptimizerService(db_session)
    result = await service.run_optimization_for_tenant("startup_ai", 30)
    assert "agents_analyzed" in result
    assert "model_recommendations" in result
    assert "prompt_optimizations" in result
    assert "total_projected_monthly_saving_usd" in result


@pytest.mark.asyncio
async def test_unknown_tenant_returns_empty(db_session):
    """Tenant with no agent data returns zero recommendations."""
    service = OptimizerService(db_session)
    result = await service.run_optimization_for_tenant("nobody_xyz", 30)
    assert result["agents_analyzed"] == 0
    assert result["model_recommendations"] == []


# ============================================================
# API TESTS
# ============================================================

@pytest.mark.asyncio
async def test_api_run_optimization(client):
    """POST /v1/forecasting/optimize/{tenant_id} returns 200."""
    response = await client.post(
        "/v1/forecasting/optimize/enterprise_corp",
        params={"lookback_days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert "agents_analyzed" in body
    assert "model_recommendations_count" in body
    assert "total_projected_monthly_saving_usd" in body


@pytest.mark.asyncio
async def test_api_get_model_recommendations(client):
    """GET model recommendations returns valid structure."""
    await client.post("/v1/forecasting/optimize/enterprise_corp")
    response = await client.get(
        "/v1/forecasting/optimize/enterprise_corp/models"
    )
    assert response.status_code == 200
    body = response.json()
    assert "recommendations" in body
    assert "total" in body
    assert "total_monthly_saving_usd" in body


@pytest.mark.asyncio
async def test_api_get_all_recommendations(client):
    """GET all model recommendations across tenants returns valid structure."""
    response = await client.get("/v1/forecasting/optimize/all/models")
    assert response.status_code == 200
    body = response.json()
    assert "recommendations" in body
    assert "total" in body


@pytest.mark.asyncio
async def test_api_optimization_roi_is_positive(client):
    """Downgrade recommendations must show positive savings."""
    await client.post("/v1/forecasting/optimize/enterprise_corp")
    response = await client.get(
        "/v1/forecasting/optimize/enterprise_corp/models"
    )
    body = response.json()
    for rec in body["recommendations"]:
        if rec["justification_type"] == "low_complexity":
            assert float(rec["expected_monthly_saving_usd"]) > 0

# ============================================================
# M5 STEP 4 — LLM-ASSISTED PROMPT REWRITING TESTS
# ============================================================

class TestLLMPromptRewriting:
    """
    Tests for generate_optimized_prompt().
    All tests mock the LLM call — no real API keys needed.
    """

    @pytest.mark.asyncio
    async def test_rewrite_returns_suggestions(self, service):
        """generate_optimized_prompt returns a non-empty suggestion."""
        from unittest.mock import AsyncMock, patch

        mock_llm_result = {
            "text": '{"optimization_suggestions": "Remove repeated context. Add JSON format.", "estimated_reduction_pct": 30, "rewrite_template": "Role: [ROLE]. Task: [TASK]. Output: JSON."}',
            "input_tokens": 150,
            "output_tokens": 80,
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
        }

        with patch.object(service.llm, "complete", new_callable=AsyncMock, return_value=mock_llm_result):
            result = await service.generate_optimized_prompt(
                agent_id="crm-bot",
                original_prompt_description="Long system prompt with repeated role description and verbose instructions",
                avg_input_tokens=900,
                use_case="summarization",
            )

        assert result["agent_id"] == "crm-bot"
        assert len(result["optimization_suggestions"]) > 0
        assert result["estimated_reduction_pct"] == 30
        assert result["tokens_used"] == 230
        assert result["provider_used"] == "groq"

    @pytest.mark.asyncio
    async def test_rewrite_uses_fallback_when_llm_fails(self, service):
        """When LLM returns empty text, rule-based fallback activates."""
        from unittest.mock import AsyncMock, patch

        mock_llm_result = {
            "text": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "provider": "none",
            "model": "none",
            "error": "All providers failed",
        }

        with patch.object(service.llm, "complete", new_callable=AsyncMock, return_value=mock_llm_result):
            result = await service.generate_optimized_prompt(
                agent_id="audit-agent",
                original_prompt_description="Verbose audit prompt",
                avg_input_tokens=1200,
            )

        assert result["agent_id"] == "audit-agent"
        assert "LLM unavailable" in result["optimization_suggestions"]
        assert result["tokens_used"] == 0
        assert result["provider_used"] == "fallback"

    @pytest.mark.asyncio
    async def test_rewrite_parses_markdown_fenced_json(self, service):
        """Parser handles LLM responses wrapped in ```json fences."""
        from unittest.mock import AsyncMock, patch

        fenced_response = '```json\n{"optimization_suggestions": "Add output format.", "estimated_reduction_pct": 25, "rewrite_template": "Task: [T]. Output: JSON."}\n```'

        mock_llm_result = {
            "text": fenced_response,
            "input_tokens": 100,
            "output_tokens": 60,
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
        }

        with patch.object(service.llm, "complete", new_callable=AsyncMock, return_value=mock_llm_result):
            result = await service.generate_optimized_prompt(
                agent_id="test-agent",
                original_prompt_description="Verbose prompt",
                avg_input_tokens=700,
            )

        assert result["optimization_suggestions"] == "Add output format."
        assert result["estimated_reduction_pct"] == 25

    @pytest.mark.asyncio
    async def test_rewrite_clamps_reduction_pct(self, service):
        """estimated_reduction_pct is clamped between 5 and 50."""
        from unittest.mock import AsyncMock, patch

        # LLM returns 99% — must be clamped to 50
        mock_llm_result = {
            "text": '{"optimization_suggestions": "Remove everything.", "estimated_reduction_pct": 99, "rewrite_template": ""}',
            "input_tokens": 50,
            "output_tokens": 30,
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
        }

        with patch.object(service.llm, "complete", new_callable=AsyncMock, return_value=mock_llm_result):
            result = await service.generate_optimized_prompt(
                agent_id="test-agent",
                original_prompt_description="Test",
                avg_input_tokens=500,
            )

        assert result["estimated_reduction_pct"] == 50

    @pytest.mark.asyncio
    async def test_rewrite_api_endpoint(self, client):
        """POST /v1/forecasting/optimize/{tenant}/rewrite-prompt returns 200."""
        from unittest.mock import AsyncMock, patch
        from modules.detection.services.llm_provider import LLMProvider

        mock_result = {
            "text": '{"optimization_suggestions": "Shorten role.", "estimated_reduction_pct": 25, "rewrite_template": "Role: [R]. Task: [T]."}',
            "input_tokens": 100,
            "output_tokens": 60,
            "provider": "groq",
            "model": "llama-3.3-70b-versatile",
        }

        with patch.object(LLMProvider, "complete", new_callable=AsyncMock, return_value=mock_result):
            response = await client.post(
                "/v1/forecasting/optimize/enterprise_corp/rewrite-prompt",
                params={
                    "agent_id": "crm-bot",
                    "prompt_description": "Long verbose system prompt with repeated context",
                    "avg_input_tokens": 900,
                    "use_case": "summarization",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["tenant_id"] == "enterprise_corp"
        assert body["agent_id"] == "crm-bot"
        assert "optimization_suggestions" in body
        assert "estimated_reduction_pct" in body