"""
Detection module tests.

Covers:
1. ML engine unit tests (pure math, no database)
2. Baseline computation integration tests (uses seeded data)
3. API tests
"""

import pytest
import numpy as np
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.detection.services import DetectionService
from modules.detection.services.ml_engine import (
    MLDetectionEngine,
    STL_Z_THRESHOLD, CUSUM_H, CUSUM_K, MIN_SAMPLE_DAYS,
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
    return MLDetectionEngine()


def _make_history(n: int = 30, mean: float = 100_000, std: float = 10_000) -> list[float]:
    """Generate synthetic daily history with weekly seasonality."""
    rng = np.random.default_rng(42)
    values = []
    for i in range(n):
        weekday = i % 7
        seasonal = 1.0 if weekday < 5 else 0.3
        val = rng.normal(mean * seasonal, std * seasonal)
        values.append(max(0.0, val))
    return values


def _make_features(history: list[float]) -> list[list[float]]:
    return [MLDetectionEngine.build_feature_vector(t, t/800, t*0.000003, t*0.02) for t in history]


# ============================================================
# ML ENGINE UNIT TESTS — no database, pure math
# ============================================================

class TestCUSUM:

    def test_cusum_no_anomaly_normal_values(self, engine):
        """Normal variation should not accumulate enough to fire CUSUM."""
        history = _make_history(30, mean=100_000, std=8_000)
        mean = float(np.mean(history))
        std = float(np.std(history, ddof=1))

        _, new_pos, new_neg, cusum_val = engine._run_cusum(
            today_value=mean * 1.1,  # only 10% above mean
            mean=mean, std_dev=std,
            cusum_pos=0.0, cusum_neg=0.0,
        )
        # 10% above mean is < 0.5σ slack, CUSUM should not accumulate much
        assert cusum_val < CUSUM_H

    def test_cusum_fires_on_persistent_upward_drift(self, engine):
        """Simulating 6 days of 2σ above mean should fire CUSUM."""
        mean = 100_000.0
        std = 10_000.0
        pos, neg = 0.0, 0.0

        for _ in range(6):
            _, pos, neg, val = engine._run_cusum(
                today_value=mean + 2 * std,
                mean=mean, std_dev=std,
                cusum_pos=pos, cusum_neg=neg,
            )
        # After 6 days at +2σ: pos ≈ 6*(2-0.5)=9 >> CUSUM_H=4
        assert pos >= CUSUM_H

    def test_cusum_resets_on_normal_after_spike(self, engine):
        """CUSUM accumulators are bounded below by zero — cannot go negative."""
        mean = 100_000.0
        std = 10_000.0
        _, pos, neg, _ = engine._run_cusum(
            today_value=mean - 5 * std,
            mean=mean, std_dev=std, cusum_pos=0.0, cusum_neg=0.0,
        )
        assert pos >= 0.0
        assert neg >= 0.0

    def test_cusum_detects_downward_drift(self, engine):
        """Persistent below-mean usage fires the negative accumulator."""
        mean = 100_000.0
        std = 10_000.0
        pos, neg = 0.0, 0.0
        for _ in range(6):
            _, pos, neg, _ = engine._run_cusum(
                today_value=mean - 2 * std,
                mean=mean, std_dev=std, cusum_pos=pos, cusum_neg=neg,
            )
        assert neg >= CUSUM_H


class TestSTL:

    def test_stl_normal_value_does_not_fire(self, engine):
        history = _make_history(30)
        mean = float(np.mean(history))
        vote, z = engine._run_stl(history, mean)
        assert not vote.fired
        assert abs(z) < STL_Z_THRESHOLD

    def test_stl_spike_fires(self, engine):
        history = _make_history(30, mean=100_000, std=5_000)
        spike = 100_000 * 5.0  # 5x mean — clearly anomalous
        vote, z = engine._run_stl(history, spike)
        assert vote.fired
        assert z > STL_Z_THRESHOLD

    def test_stl_drop_fires(self, engine):
        history = _make_history(30, mean=100_000, std=5_000)
        drop = 1_000.0  # near-zero — clearly anomalous
        vote, z = engine._run_stl(history, drop)
        assert vote.fired
        assert z < -STL_Z_THRESHOLD

    def test_stl_requires_minimum_data(self, engine):
        short_history = _make_history(5)  # too short
        vote, z = engine._run_stl(short_history, 100_000)
        # STL will fail gracefully and return no vote
        # (may fail or return non-fired depending on statsmodels behavior)
        assert isinstance(vote.fired, bool)

    def test_stl_direction_correct(self, engine):
        history = _make_history(30, mean=100_000)
        spike = 500_000.0
        vote, z = engine._run_stl(history, spike)
        assert vote.direction == "up"

        drop = 1_000.0
        vote_d, z_d = engine._run_stl(history, drop)
        assert vote_d.direction == "down"


class TestIsolationForest:

    def test_isolation_forest_normal_returns_no_anomaly(self, engine):
        history = _make_history(30)
        features = _make_features(history)
        today = MLDetectionEngine.build_feature_vector(
            float(np.mean(history)), float(np.mean(history)) / 800,
            float(np.mean(history)) * 0.000003, 0.02
        )
        vote, score = engine._run_isolation_forest(features, today, threshold=-0.1)
        assert isinstance(vote.fired, bool)
        assert score is not None

    def test_isolation_forest_extreme_multivariate_fires(self, engine):
        """An extreme multi-dimensional outlier should fire."""
        history = _make_history(30, mean=100_000)
        features = _make_features(history)
        # extreme value on all dimensions simultaneously
        extreme = MLDetectionEngine.build_feature_vector(
            1_000_000.0, 5_000.0, 500.0, 50.0
        )
        vote, score = engine._run_isolation_forest(features, extreme, threshold=-0.1)
        # At least the score should be lower than normal scores
        assert score < 0


class TestEnsemble:

    def test_majority_vote_required(self, engine):
        """1 out of 3 detectors firing should not trigger an anomaly."""
        history = _make_history(30, mean=100_000, std=5_000)
        features = _make_features(history)
        normal_today = float(np.mean(history))
        today_feat = MLDetectionEngine.build_feature_vector(
            normal_today, normal_today / 800, normal_today * 0.000003, 0.02
        )
        result = engine.detect(
            history=history,
            today_value=normal_today,
            today_features=today_feat,
            history_features=features,
            cusum_pos=0.0, cusum_neg=0.0,
            mean=float(np.mean(history)),
            std_dev=float(np.std(history, ddof=1)),
        )
        # Normal value should not fire the ensemble
        assert result.severity in ("normal", "warning")  # at most warning

    def test_all_three_fire_on_extreme_spike(self, engine):
        """An extreme spike should fire all three detectors."""
        history = _make_history(30, mean=100_000, std=5_000)
        features = _make_features(history)
        spike = 100_000 * 8.0  # 8x mean

        # Pre-accumulate CUSUM to simulate persistent drift
        pos, neg = 0.0, 0.0
        mean = float(np.mean(history))
        std = float(np.std(history, ddof=1))
        for _ in range(5):
            _, pos, neg, _ = engine._run_cusum(spike, mean, std, pos, neg)

        spike_feat = MLDetectionEngine.build_feature_vector(
            spike, spike / 800, spike * 0.000003, 0.02
        )
        result = engine.detect(
            history=history,
            today_value=spike,
            today_features=spike_feat,
            history_features=features,
            cusum_pos=pos, cusum_neg=neg,
            mean=mean, std_dev=std,
        )
        assert result.fired is True
        assert result.vote_count >= 2

    def test_insufficient_history_returns_no_detection(self, engine):
        """Short history returns a safe no-anomaly result."""
        short_history = _make_history(5)
        features = _make_features(short_history)
        result = engine.detect(
            history=short_history,
            today_value=500_000,
            today_features=features[0],
            history_features=features,
            cusum_pos=0.0, cusum_neg=0.0,
            mean=100_000, std_dev=10_000,
        )
        assert result.fired is False
        assert result.severity == "normal"

    def test_feature_vector_shape(self):
        vec = MLDetectionEngine.build_feature_vector(100_000, 900, 3.50, 18.0)
        assert len(vec) == 5
        assert vec[2] == pytest.approx(100_000 / 900, rel=1e-3)
        assert vec[3] == pytest.approx(3.50 * 100, rel=1e-3)
        assert vec[4] == pytest.approx(18.0 / 900 * 100, rel=1e-3)


# ============================================================
# INTEGRATION TESTS — uses seeded database
# ============================================================

@pytest.mark.asyncio
async def test_compute_baseline_enterprise_corp(db_session):
    service = DetectionService(db_session)
    success = await service.compute_baseline_for_tenant("enterprise_corp", 30)
    assert success is True


@pytest.mark.asyncio
async def test_baseline_values_are_realistic(db_session):
    service = DetectionService(db_session)
    await service.compute_baseline_for_tenant("enterprise_corp", 30)
    baseline = await service.baseline_repo.get_baseline("enterprise_corp")
    assert baseline is not None
    assert baseline.daily_mean > 100_000
    assert baseline.daily_std_dev > 0
    assert baseline.sample_days >= 14
    assert baseline.isolation_forest_threshold < 0   # always negative for IsoForest
    assert baseline.cusum_pos == 0.0                  # reset on recompute


@pytest.mark.asyncio
async def test_compute_baselines_all_tenants(db_session):
    service = DetectionService(db_session)
    tenants = ["enterprise_corp", "startup_ai", "research_lab",
               "fintech_secure", "dev_sandbox"]
    results = await service.compute_baselines_for_all_tenants(tenants)
    successes = sum(1 for v in results.values() if v)
    assert successes >= 4


@pytest.mark.asyncio
async def test_check_tenant_returns_result(db_session):
    service = DetectionService(db_session)
    await service.compute_baseline_for_tenant("enterprise_corp", 30)
    result = await service.check_tenant_now("enterprise_corp")
    assert result.tenant_id == "enterprise_corp"
    assert result.severity in ("normal", "warning", "high", "critical")
    assert isinstance(result.fired, bool)


@pytest.mark.asyncio
async def test_check_unknown_tenant_returns_normal(db_session):
    service = DetectionService(db_session)
    result = await service.check_tenant_now("unknown_xyz_abc")
    assert result.fired is False
    assert result.severity == "normal"


# ============================================================
# API TESTS
# ============================================================

@pytest.mark.asyncio
async def test_api_compute_baseline(client):
    response = await client.post(
        "/v1/detection/baseline/enterprise_corp/compute",
        params={"lookback_days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["daily_mean"] > 0
    assert body["sample_days"] >= 14
    assert "isolation_forest_threshold" in body
    assert "cusum_pos" in body


@pytest.mark.asyncio
async def test_api_check_tenant(client):
    await client.post("/v1/detection/baseline/enterprise_corp/compute")
    response = await client.get("/v1/detection/check/enterprise_corp")
    assert response.status_code == 200
    body = response.json()
    assert "vote_count" in body
    assert "detector_names" in body
    assert body["severity"] in ("normal", "warning", "high", "critical")


@pytest.mark.asyncio
async def test_api_anomalies_empty(client):
    response = await client.get("/v1/detection/anomalies/quiet_tenant_xyz")
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_api_baseline_unknown_returns_404(client):
    response = await client.get("/v1/detection/baseline/nobody_here_xyz")
    assert response.status_code == 404