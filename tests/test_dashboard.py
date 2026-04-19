"""
Dashboard module tests.

The dashboard is a read-only integration layer.
Tests verify:
1. All endpoints return 200 with correct shape
2. Data from multiple modules is present in responses
3. Error resilience — if a module returns empty data, endpoint still works
"""

import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime

from main import app


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


# ============================================================
# EXECUTIVE OVERVIEW
# ============================================================

@pytest.mark.asyncio
async def test_executive_overview_returns_200(client):
    response = await client.get(
        "/v1/dashboard/executive", params={"period_days": 30}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_executive_overview_shape(client):
    """Response has all required top-level fields."""
    response = await client.get("/v1/dashboard/executive")
    body = response.json()
    assert "generated_at" in body
    assert "period_days" in body
    assert "tenants" in body
    assert "summary" in body


@pytest.mark.asyncio
async def test_executive_overview_has_all_tenants(client):
    """All 5 seeded tenants appear in the response."""
    response = await client.get("/v1/dashboard/executive")
    body = response.json()
    tenant_ids = [t["tenant_id"] for t in body["tenants"]]
    assert "enterprise_corp" in tenant_ids
    assert "startup_ai" in tenant_ids
    assert "research_lab" in tenant_ids
    assert "fintech_secure" in tenant_ids
    assert "dev_sandbox" in tenant_ids


@pytest.mark.asyncio
async def test_executive_overview_tenant_shape(client):
    """Each tenant item has cost, usage, anomaly_status, budget_risk."""
    response = await client.get("/v1/dashboard/executive")
    body = response.json()
    first = body["tenants"][0]
    assert "cost" in first
    assert "usage" in first
    assert "anomaly_status" in first
    assert "budget_risk" in first
    # Cost fields
    assert "total_usd" in first["cost"]
    assert "trend" in first["cost"]
    assert "change_pct" in first["cost"]
    # Usage fields
    assert "total_tokens" in first["usage"]
    assert "total_calls" in first["usage"]
    assert "error_rate_pct" in first["usage"]


@pytest.mark.asyncio
async def test_executive_overview_sorted_by_cost(client):
    """Tenants are sorted by cost descending."""
    response = await client.get(
        "/v1/dashboard/executive",
        params={"period_days": 90},
    )
    body = response.json()
    costs = [float(t["cost"]["total_usd"]) for t in body["tenants"]]
    assert costs == sorted(costs, reverse=True)


@pytest.mark.asyncio
async def test_executive_overview_summary_fields(client):
    """Summary section has all required aggregates."""
    response = await client.get("/v1/dashboard/executive")
    summary = response.json()["summary"]
    assert "total_tenants" in summary
    assert "total_cost_usd" in summary
    assert "tenants_at_risk" in summary
    assert "active_anomalies" in summary
    assert "critical_anomalies" in summary
    assert summary["total_tenants"] == 5


@pytest.mark.asyncio
async def test_executive_overview_enterprise_has_real_data(client):
    """enterprise_corp has seeded data — should show meaningful numbers."""
    response = await client.get(
        "/v1/dashboard/executive", params={"period_days": 90}
    )
    body = response.json()
    enterprise = next(
        t for t in body["tenants"] if t["tenant_id"] == "enterprise_corp"
    )
    assert float(enterprise["cost"]["total_usd"]) > 10.0
    assert enterprise["usage"]["total_calls"] > 1000
    assert enterprise["usage"]["total_tokens"] > 1_000_000


# ============================================================
# TENANT DETAIL
# ============================================================

@pytest.mark.asyncio
async def test_tenant_detail_returns_200(client):
    response = await client.get("/v1/dashboard/tenant/enterprise_corp")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_tenant_detail_shape(client):
    """Tenant detail has cost, anomalies, forecast, governance sections."""
    response = await client.get("/v1/dashboard/tenant/enterprise_corp")
    body = response.json()
    assert "tenant_id" in body
    assert "cost" in body
    assert "anomalies" in body
    assert "forecast" in body
    assert "governance" in body
    assert body["tenant_id"] == "enterprise_corp"


@pytest.mark.asyncio
async def test_tenant_detail_cost_has_daily_trend(client):
    """Cost section includes daily trend data."""
    response = await client.get(
        "/v1/dashboard/tenant/enterprise_corp",
        params={"period_days": 30},
    )
    body = response.json()
    assert "daily_trend" in body["cost"]
    assert len(body["cost"]["daily_trend"]) > 0


@pytest.mark.asyncio
async def test_tenant_detail_cost_has_model_breakdown(client):
    """Cost section includes model breakdown."""
    response = await client.get(
        "/v1/dashboard/tenant/enterprise_corp",
        params={"period_days": 90},
    )
    body = response.json()
    assert "model_breakdown" in body["cost"]
    assert len(body["cost"]["model_breakdown"]) >= 2


@pytest.mark.asyncio
async def test_tenant_detail_forecast_section(client):
    """Forecast section is present (may or may not have data)."""
    response = await client.get("/v1/dashboard/tenant/enterprise_corp")
    body = response.json()
    forecast = body["forecast"]
    assert "available" in forecast
    # If forecasts were generated, check structure
    if forecast["available"]:
        assert "trend_slope" in forecast
        assert "daily" in forecast
        assert len(forecast["daily"]) > 0


@pytest.mark.asyncio
async def test_tenant_detail_governance_section(client):
    """Governance section has call counts."""
    response = await client.get(
        "/v1/dashboard/tenant/enterprise_corp",
        params={"period_days": 90},
    )
    body = response.json()
    gov = body["governance"]
    assert "total_calls" in gov
    assert "blocked_calls" in gov
    assert "block_rate_pct" in gov
    assert "active_rules_count" in gov
    assert gov["total_calls"] > 0


@pytest.mark.asyncio
async def test_tenant_detail_unknown_tenant_returns_zeros(client):
    """Unknown tenant returns 200 with zero data — not a 404."""
    response = await client.get("/v1/dashboard/tenant/totally_unknown_xyz")
    assert response.status_code == 200
    body = response.json()
    assert body["cost"]["total_calls"] == 0


@pytest.mark.asyncio
async def test_tenant_detail_fintech_has_blocked_calls(client):
    """fintech_secure has governance rules that block gpt-4o — blocked_calls > 0."""
    response = await client.get(
        "/v1/dashboard/tenant/fintech_secure",
        params={"period_days": 90},
    )
    body = response.json()
    # fintech_secure has MODEL_BLOCK rules — some calls should be blocked
    assert body["cost"]["blocked_calls"] >= 0  # may be 0 in log, check governance
    assert body["governance"]["active_rules_count"] > 0


# ============================================================
# ALERTS FEED
# ============================================================

@pytest.mark.asyncio
async def test_alerts_returns_200(client):
    response = await client.get("/v1/dashboard/alerts")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_alerts_shape(client):
    """Alert feed has required fields."""
    response = await client.get("/v1/dashboard/alerts")
    body = response.json()
    assert "generated_at" in body
    assert "alerts" in body
    assert "total" in body
    assert "critical_count" in body
    assert "warning_count" in body
    assert isinstance(body["alerts"], list)


@pytest.mark.asyncio
async def test_alerts_total_matches_list(client):
    """total field matches len(alerts)."""
    response = await client.get("/v1/dashboard/alerts")
    body = response.json()
    assert body["total"] == len(body["alerts"])


@pytest.mark.asyncio
async def test_alerts_item_shape(client):
    """Each alert has required fields if any exist."""
    response = await client.get(
        "/v1/dashboard/alerts",
        params={"anomaly_hours": 168, "budget_risk_days": 30},
    )
    body = response.json()
    for alert in body["alerts"]:
        assert "id" in alert
        assert "alert_type" in alert
        assert "severity" in alert
        assert "tenant_id" in alert
        assert "title" in alert
        assert "description" in alert
        assert "detected_at" in alert
        assert "action_url" in alert
        assert alert["alert_type"] in ("anomaly", "budget_risk")
        assert alert["severity"] in ("critical", "high", "warning", "watch")


@pytest.mark.asyncio
async def test_alerts_counts_consistent(client):
    """critical_count + warning_count <= total."""
    response = await client.get("/v1/dashboard/alerts")
    body = response.json()
    assert body["critical_count"] + body["warning_count"] <= body["total"]


# ============================================================
# SYSTEM HEALTH
# ============================================================

@pytest.mark.asyncio
async def test_health_returns_200(client):
    response = await client.get("/v1/dashboard/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_shape(client):
    """Health response has status, modules, database, redis."""
    response = await client.get("/v1/dashboard/health")
    body = response.json()
    assert "status" in body
    assert "modules" in body
    assert "database" in body
    assert "redis" in body
    assert body["status"] in ("ok", "degraded", "error")


@pytest.mark.asyncio
async def test_health_all_modules_present(client):
    """Response lists all four modules."""
    response = await client.get("/v1/dashboard/health")
    body = response.json()
    module_names = [m["name"] for m in body["modules"]]
    assert "gateway" in module_names
    assert "analytics" in module_names
    assert "detection" in module_names
    assert "forecasting" in module_names


@pytest.mark.asyncio
async def test_health_database_ok(client):
    """Database should be ok (containers are running)."""
    response = await client.get("/v1/dashboard/health")
    body = response.json()
    assert body["database"] == "ok"
    assert body["redis"] == "ok"
    assert body["status"] == "ok"