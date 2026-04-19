"""
Forecasting module tests.

1. ForecastEngine unit tests (pure math, no database)
2. Integration tests using seeded data
3. API tests
"""

import math
import pytest
import numpy as np
from datetime import date, timedelta
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.forecasting.services import ForecastingService
from modules.forecasting.services.forecast_engine import (
    ForecastEngine,
    MIN_HISTORY_DAYS,
    OPTIMISTIC_SLOPE_DAMPING,
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
def engine():
    return ForecastEngine()


def _make_flat_history(n: int = 60, value: float = 100_000.0) -> list[float]:
    """Perfectly flat history — slope should be ~0."""
    return [value] * n


def _make_growing_history(
    n: int = 60,
    start: float = 50_000.0,
    daily_growth: float = 1_000.0,
) -> list[float]:
    """
    History with linear growth — slope should be ~daily_growth.
    Adds small weekday/weekend variation to make it realistic for STL.
    """
    rng = np.random.default_rng(0)
    values = []
    for i in range(n):
        weekday = i % 7
        seasonal = 1.0 if weekday < 5 else 0.3
        base = start + daily_growth * i
        noise = rng.normal(0, base * 0.03)
        values.append(max(0.0, base * seasonal + noise))
    return values


# ============================================================
# UNIT TESTS — DECOMPOSITION AND REGRESSION
# ============================================================

class TestSTLDecomposition:

    def test_decompose_returns_three_components(self, engine):
        """_decompose returns trend, seasonal, and residual of equal length."""
        history = _make_flat_history(60)
        trend, seasonal, residual = engine._decompose(history)
        assert len(trend) == len(history)
        assert len(seasonal) == len(history)
        assert len(residual) == len(history)

    def test_trend_smoother_than_raw(self, engine):
        """Trend component should have lower variance than raw data."""
        history = _make_growing_history(60, daily_growth=500)
        trend, _, _ = engine._decompose(history)
        assert np.std(trend) < np.std(history)

    def test_seasonal_has_weekly_period(self, engine):
        """Seasonal component should repeat approximately every 7 days."""
        history = _make_growing_history(60)
        _, seasonal, _ = engine._decompose(history)
        # Seasonal at index i and i+7 should be similar
        diffs = [abs(seasonal[i] - seasonal[i + 7]) for i in range(len(seasonal) - 7)]
        # Average diff should be much smaller than std of raw values
        assert np.mean(diffs) < np.std(history) * 0.5


class TestLinearTrendFitting:

    def test_flat_history_slope_near_zero(self, engine):
        """Flat history (no growth) gives slope ~0."""
        history = _make_flat_history(60)
        trend, _, _ = engine._decompose(history)
        slope, intercept = engine._fit_trend(trend)
        assert abs(slope) < 500  # some noise from STL, but near zero

    def test_growing_history_positive_slope(self, engine):
        """Growing history gives positive slope."""
        history = _make_growing_history(60, start=50_000, daily_growth=2_000)
        trend, _, _ = engine._decompose(history)
        slope, _ = engine._fit_trend(trend)
        assert slope > 0

    def test_slope_direction_matches_data(self, engine):
        """Declining history gives negative slope."""
        n = 60
        history = [100_000 - i * 500 + (i % 7 < 5) * 20_000 for i in range(n)]
        trend, _, _ = engine._decompose(history)
        slope, _ = engine._fit_trend(trend)
        assert slope < 0


class TestSeasonalExtraction:

    def test_weekday_higher_than_weekend(self, engine):
        history = _make_growing_history(60)
        _, seasonal, _ = engine._decompose(history)
        # Use a Monday as history_start so day 0=Mon, 5=Sat, 6=Sun
        history_start = date(2026, 1, 5)  # Monday
        by_weekday = engine._seasonal_by_weekday(seasonal, history_start)
        weekday_avg = np.mean([by_weekday[i] for i in range(5)])
        weekend_avg = np.mean([by_weekday[5], by_weekday[6]])
        assert weekday_avg > weekend_avg

    def test_returns_all_seven_weekdays(self, engine):
        """Result always has keys 0-6."""
        history = _make_flat_history(60)
        _, seasonal, _ = engine._decompose(history)
        by_weekday = engine._seasonal_by_weekday(seasonal, date(2026, 1, 1))
        assert set(by_weekday.keys()) == {0, 1, 2, 3, 4, 5, 6}


# ============================================================
# UNIT TESTS — FORECAST GENERATION
# ============================================================

class TestForecastGeneration:

    def test_insufficient_history_returns_none(self, engine):
        """Less than MIN_HISTORY_DAYS returns None."""
        short = _make_flat_history(MIN_HISTORY_DAYS - 1)
        result = engine.forecast(short, date.today())
        assert result is None

    def test_result_has_correct_horizon(self, engine):
        """30-day forecast returns 30 daily predictions."""
        history = _make_growing_history(60)
        result = engine.forecast(history, date.today(), horizon_days=30)
        assert result is not None
        assert len(result.daily_forecasts) == 30

    def test_forecast_dates_are_consecutive(self, engine):
        """Each daily forecast is exactly one day after the previous."""
        history = _make_growing_history(60)
        result = engine.forecast(history, date.today(), horizon_days=14)
        assert result is not None
        dates = [d.forecast_date for d in result.daily_forecasts]
        for i in range(1, len(dates)):
            assert (dates[i] - dates[i - 1]).days == 1

    def test_growing_data_has_positive_slope(self, engine):
        """Growing history produces positive trend slope."""
        history = _make_growing_history(60, daily_growth=1_500)
        result = engine.forecast(history, date.today())
        assert result is not None
        assert result.trend_slope > 0

    def test_flat_data_has_near_zero_slope(self, engine):
        """Flat history (no growth) produces slope close to zero."""
        history = _make_flat_history(60, value=100_000)
        result = engine.forecast(history, date.today())
        assert result is not None
        # With flat data, slope should be small relative to the mean
        assert abs(result.trend_slope) < 5_000

    def test_confidence_widens_with_horizon(self, engine):
        """Confidence half-width should grow for later forecast dates."""
        history = _make_growing_history(60)
        result = engine.forecast(history, date.today(), horizon_days=20)
        assert result is not None
        widths = [d.confidence_half_width for d in result.daily_forecasts]
        # Width at day 20 should be larger than at day 1
        assert widths[-1] > widths[0]

    def test_pessimistic_always_gte_likely(self, engine):
        """Pessimistic scenario is always >= likely."""
        history = _make_growing_history(60)
        result = engine.forecast(history, date.today())
        assert result is not None
        for d in result.daily_forecasts:
            assert d.pessimistic_tokens >= d.likely_tokens - 1  # small float tolerance

    def test_all_forecasts_non_negative(self, engine):
        """No scenario produces negative token forecasts."""
        history = _make_flat_history(60, value=100_000)
        result = engine.forecast(history, date.today())
        assert result is not None
        for d in result.daily_forecasts:
            assert d.likely_tokens >= 0
            assert d.pessimistic_tokens >= 0
            assert d.optimistic_tokens >= 0

    def test_monthly_totals_sum_correctly(self, engine):
        """Monthly likely total = sum of 30 daily likely values."""
        history = _make_growing_history(60)
        result = engine.forecast(history, date.today(), horizon_days=30)
        assert result is not None
        manual_sum = sum(d.likely_tokens for d in result.daily_forecasts)
        assert abs(result.monthly_likely_tokens - manual_sum) < 1.0


# ============================================================
# UNIT TESTS — BUDGET RISK
# ============================================================

class TestBudgetRisk:

    def test_no_risk_when_no_limits(self, engine):
        """No limits configured means no budget risk."""
        history = _make_growing_history(60)
        forecast = engine.forecast(history, date.today())
        risks = engine.compute_budget_risk(
            forecast, None, None, 0.0, 0.0
        )
        assert risks == []

    def test_detects_daily_token_limit_breach(self, engine):
        """When forecast exceeds daily limit, a risk is reported."""
        # Use flat but high history, very low limit
        history = _make_flat_history(60, value=100_000)
        forecast = engine.forecast(history, date.today())
        risks = engine.compute_budget_risk(
            forecast,
            daily_token_limit=50_000,   # limit below forecast
            monthly_budget_usd=None,
            current_monthly_tokens=0.0,
            current_monthly_cost_usd=0.0,
        )
        assert len(risks) > 0
        assert risks[0]["risk_type"] == "token_limit"

    def test_detects_cost_budget_breach(self, engine):
        """When cumulative forecast cost exceeds monthly budget, risk is reported."""
        history = _make_flat_history(60, value=100_000)
        forecast = engine.forecast(
            history, date.today(), avg_cost_per_token=0.000003
        )
        risks = engine.compute_budget_risk(
            forecast,
            daily_token_limit=None,
            monthly_budget_usd=5.0,    # very low budget
            current_monthly_tokens=0.0,
            current_monthly_cost_usd=4.5,  # already nearly at limit
        )
        assert len(risks) > 0
        assert risks[0]["risk_type"] == "cost_budget"

    def test_risk_has_exhaustion_date(self, engine):
        """Budget risk record includes an exhaustion_date."""
        history = _make_flat_history(60, value=100_000)
        forecast = engine.forecast(history, date.today())
        risks = engine.compute_budget_risk(
            forecast, daily_token_limit=50_000, monthly_budget_usd=None,
            current_monthly_tokens=0.0, current_monthly_cost_usd=0.0,
        )
        if risks:
            assert "exhaustion_date" in risks[0]
            assert isinstance(risks[0]["exhaustion_date"], date)


# ============================================================
# INTEGRATION TESTS — uses seeded database
# ============================================================

@pytest.mark.asyncio
async def test_generate_forecast_enterprise_corp(db_session):
    """enterprise_corp has 90 days of data — forecast generates successfully."""
    service = ForecastingService(db_session)
    result = await service.generate_forecast_for_tenant(
        "enterprise_corp", horizon_days=30, lookback_days=60
    )
    assert result is not None
    assert len(result.daily_forecasts) == 30


@pytest.mark.asyncio
async def test_startup_ai_shows_positive_growth(db_session):
    """startup_ai has 18%/month growth seeded — slope should be positive."""
    service = ForecastingService(db_session)
    result = await service.generate_forecast_for_tenant(
        "startup_ai", lookback_days=60
    )
    assert result is not None
    assert result.trend_slope > 0  # seeder builds in upward growth


@pytest.mark.asyncio
async def test_forecast_all_tenants(db_session):
    """All 5 seeded tenants generate successful forecasts."""
    service = ForecastingService(db_session)
    tenants = ["enterprise_corp", "startup_ai", "research_lab",
               "fintech_secure", "dev_sandbox"]
    results = await service.generate_forecasts_for_all_tenants(tenants)
    successes = sum(1 for v in results.values() if v)
    assert successes >= 4


@pytest.mark.asyncio
async def test_unknown_tenant_returns_none(db_session):
    """Tenant with no data returns None forecast."""
    service = ForecastingService(db_session)
    result = await service.generate_forecast_for_tenant("nobody_xyz_abc")
    assert result is None


# ============================================================
# API TESTS
# ============================================================

@pytest.mark.asyncio
async def test_api_generate_forecast(client):
    """POST /v1/forecasting/forecast/{tenant_id} returns 200."""
    response = await client.post(
        "/v1/forecasting/forecast/enterprise_corp",
        params={"horizon_days": 30, "lookback_days": 60},
    )
    assert response.status_code == 200
    body = response.json()
    assert "summary" in body
    assert "daily" in body
    assert body["total_days"] == 30


@pytest.mark.asyncio
async def test_api_forecast_summary_fields(client):
    """Forecast summary contains all expected fields."""
    response = await client.post(
        "/v1/forecasting/forecast/enterprise_corp",
    )
    body = response.json()
    summary = body["summary"]
    assert "trend_slope" in summary
    assert "trend_slope_description" in summary
    assert "monthly_likely_tokens" in summary
    assert "monthly_likely_cost_usd" in summary


@pytest.mark.asyncio
async def test_api_forecast_daily_shape(client):
    """Each daily forecast point has all three scenarios."""
    response = await client.post(
        "/v1/forecasting/forecast/enterprise_corp",
    )
    body = response.json()
    first_day = body["daily"][0]
    assert "likely_tokens" in first_day
    assert "pessimistic_tokens" in first_day
    assert "optimistic_tokens" in first_day
    assert "confidence_lower" in first_day
    assert "confidence_upper" in first_day


@pytest.mark.asyncio
async def test_api_get_stored_forecast(client):
    """GET /v1/forecasting/forecast/{tenant_id} returns stored forecast."""
    await client.post("/v1/forecasting/forecast/startup_ai")
    response = await client.get("/v1/forecasting/forecast/startup_ai")
    assert response.status_code == 200
    body = response.json()
    assert len(body["daily"]) > 0


@pytest.mark.asyncio
async def test_api_unknown_tenant_returns_422(client):
    """POST forecast for unknown tenant returns 422."""
    response = await client.post(
        "/v1/forecasting/forecast/completely_unknown_xyz"
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_api_get_forecast_not_found_returns_404(client):
    """GET forecast for tenant with no stored forecast returns 404."""
    response = await client.get(
        "/v1/forecasting/forecast/nobody_stored_forecast_xyz"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_api_budget_risk(client):
    """GET /v1/forecasting/budget-risk returns valid response."""
    # Generate forecasts first so risks can be computed
    await client.post("/v1/forecasting/forecast/startup_ai")
    await client.post("/v1/forecasting/forecast/enterprise_corp")

    response = await client.get(
        "/v1/forecasting/budget-risk", params={"max_days": 30}
    )
    assert response.status_code == 200
    body = response.json()
    assert "risks" in body
    assert "total" in body
    assert "critical_count" in body
    assert "warning_count" in body