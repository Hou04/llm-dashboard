"""
Analytics API tests.
Tests the full stack: HTTP → service → repository → PostgreSQL (seeded data).
"""

import pytest
from httpx import AsyncClient, ASGITransport
from main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ============================================================
# COST SUMMARY
# ============================================================

@pytest.mark.asyncio
async def test_cost_summary_returns_200(client):
    response = await client.get(
        "/v1/analytics/costs/summary/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cost_summary_has_seeded_data(client):
    """enterprise_corp has seeded data — should show real numbers."""
    response = await client.get(
        "/v1/analytics/costs/summary/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    assert body["total_calls"] > 10000
    assert body["total_tokens"] > 1_000_000
    assert float(body["total_cost_usd"]) > 10.0


@pytest.mark.asyncio
async def test_cost_summary_shape(client):
    response = await client.get(
        "/v1/analytics/costs/summary/enterprise_corp",
        params={"from_date": "2026-01-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    required = [
        "tenant_id", "from_date", "to_date", "total_calls",
        "total_tokens", "total_cost_usd", "successful_calls",
        "error_calls", "blocked_calls",
    ]
    for field in required:
        assert field in body, f"Missing field: {field}"


@pytest.mark.asyncio
async def test_cost_summary_unknown_tenant_returns_zeros(client):
    response = await client.get(
        "/v1/analytics/costs/summary/completely_unknown_xyz",
        params={"from_date": "2026-01-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_calls"] == 0
    assert body["total_tokens"] == 0


@pytest.mark.asyncio
async def test_cost_summary_invalid_date_range(client):
    response = await client.get(
        "/v1/analytics/costs/summary/enterprise_corp",
        params={"from_date": "2026-03-11", "to_date": "2026-01-01"},
    )
    assert response.status_code == 400


# ============================================================
# DAILY TREND
# ============================================================

@pytest.mark.asyncio
async def test_daily_trend_returns_200(client):
    response = await client.get(
        "/v1/analytics/costs/daily/enterprise_corp",
        params={"from_date": "2026-02-01", "to_date": "2026-02-28"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_daily_trend_fills_all_days(client):
    """Daily trend returns a data point for every day in range, including zeros."""
    response = await client.get(
        "/v1/analytics/costs/daily/enterprise_corp",
        params={"from_date": "2026-02-01", "to_date": "2026-02-28"},
    )
    body = response.json()
    assert body["total_days"] == 28
    assert len(body["data"]) == 28


@pytest.mark.asyncio
async def test_daily_trend_data_has_correct_shape(client):
    response = await client.get(
        "/v1/analytics/costs/daily/enterprise_corp",
        params={"from_date": "2026-02-01", "to_date": "2026-02-07"},
    )
    body = response.json()
    assert len(body["data"]) == 7
    first = body["data"][0]
    assert "date" in first
    assert "total_calls" in first
    assert "total_tokens" in first
    assert "total_cost_usd" in first


# ============================================================
# MODEL BREAKDOWN
# ============================================================

@pytest.mark.asyncio
async def test_model_breakdown_returns_200(client):
    response = await client.get(
        "/v1/analytics/costs/models/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_model_breakdown_has_models(client):
    """enterprise_corp uses multiple models — should see at least 2."""
    response = await client.get(
        "/v1/analytics/costs/models/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    assert len(body["models"]) >= 2


@pytest.mark.asyncio
async def test_model_breakdown_cost_share_sums_to_100(client):
    """cost_share_pct across all models should sum to ~100%."""
    response = await client.get(
        "/v1/analytics/costs/models/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    total_share = sum(m["cost_share_pct"] for m in body["models"])
    assert abs(total_share - 100.0) < 0.5


# ============================================================
# AGENT BREAKDOWN
# ============================================================

@pytest.mark.asyncio
async def test_agent_breakdown_returns_200(client):
    response = await client.get(
        "/v1/analytics/costs/agents/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_agent_breakdown_has_agents(client):
    """enterprise_corp has multiple agents in seeded data."""
    response = await client.get(
        "/v1/analytics/costs/agents/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    assert len(body["agents"]) >= 3


# ============================================================
# OVERVIEW
# ============================================================

@pytest.mark.asyncio
async def test_overview_returns_200(client):
    response = await client.get(
        "/v1/analytics/overview",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_overview_has_all_seeded_tenants(client):
    """Overview should include all 5 seeded tenants."""
    response = await client.get(
        "/v1/analytics/overview",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    tenant_ids = [t["tenant_id"] for t in body["tenants"]]
    assert "enterprise_corp" in tenant_ids
    assert "startup_ai" in tenant_ids
    assert "research_lab" in tenant_ids


@pytest.mark.asyncio
async def test_overview_ordered_by_cost(client):
    """Overview is ordered by cost descending."""
    response = await client.get(
        "/v1/analytics/overview",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    costs = [float(t["total_cost_usd"]) for t in body["tenants"]]
    assert costs == sorted(costs, reverse=True)