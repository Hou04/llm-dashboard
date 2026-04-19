"""
Gateway API tests — HTTP layer.

These tests use FastAPI's TestClient to make real HTTP requests
against the application without starting a live server.

We test:
- HTTP status codes
- Response body shapes
- Validation errors (Pydantic catches bad input)
- Integration through the full stack (HTTP → service → DB)
"""

import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport

from main import app


# ============================================================
# FIXTURE — async HTTP client
# ============================================================

@pytest.fixture
async def client():
    """Async HTTP client that sends requests to the FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ============================================================
# HEALTH CHECK
# ============================================================

@pytest.mark.asyncio
async def test_health_check(client):
    """GET /v1/gateway/health returns 200 with status=ok."""
    response = await client.get("/v1/gateway/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["module"] == "gateway"
    assert "timestamp" in body


# ============================================================
# ROOT ENDPOINT
# ============================================================

@pytest.mark.asyncio
async def test_root(client):
    """GET / returns service info."""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "LLM Dashboard API"


# ============================================================
# POST /v1/gateway/log
# ============================================================

@pytest.mark.asyncio
async def test_log_call_returns_201(client):
    """Valid log request returns HTTP 201."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "api_test_tenant",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": "0.00007500",
        "status": "success",
    })
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_log_call_response_shape(client):
    """Response body has all required fields."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "api_test_tenant",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": "0.00007500",
        "status": "success",
    })
    body = response.json()
    assert "success" in body
    assert "log_id" in body
    assert "decision" in body
    assert "model_used" in body
    assert "was_downgraded" in body


@pytest.mark.asyncio
async def test_log_call_success_response(client):
    """Successful call returns success=True and a log_id."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "api_test_tenant",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 200,
        "output_tokens": 100,
        "total_tokens": 300,
        "cost_usd": "0.00015000",
        "duration_ms": 500,
        "status": "success",
    })
    body = response.json()
    assert body["success"] is True
    assert body["log_id"] is not None
    assert body["decision"] == "allow"


@pytest.mark.asyncio
async def test_log_call_blocked_by_governance(client):
    """fintech_secure tenant has gpt-4o blocked — returns success=False."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "fintech_secure",
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": "0.00037500",
        "status": "success",
    })
    # HTTP 201 — request was valid, governance blocked it
    assert response.status_code == 201
    body = response.json()
    assert body["success"] is False
    assert body["decision"] == "block"
    assert body["log_id"] is None
    assert body["error"] is not None


@pytest.mark.asyncio
async def test_log_call_validation_total_tokens_mismatch(client):
    """total_tokens inconsistent with input+output returns 422."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "test",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 999,  # wrong — should be 150
        "cost_usd": "0.00001000",
        "status": "success",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_log_call_validation_invalid_status(client):
    """Unknown status value returns 422."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "test",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": "0.00001000",
        "status": "flying",   # not a valid status
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_log_call_validation_negative_tokens(client):
    """Negative token counts return 422."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "test",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": -10,
        "output_tokens": 50,
        "total_tokens": 40,
        "cost_usd": "0.00001000",
        "status": "success",
    })
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_log_call_missing_required_fields(client):
    """Missing required fields return 422."""
    response = await client.post("/v1/gateway/log", json={
        "tenant_id": "test",
        # missing model, provider, tokens, cost
    })
    assert response.status_code == 422


# ============================================================
# GET /v1/gateway/usage/{tenant_id}
# ============================================================

@pytest.mark.asyncio
async def test_get_usage_returns_200(client):
    """Valid usage request returns 200."""
    response = await client.get(
        "/v1/gateway/usage/enterprise_corp",
        params={"from_date": "2026-01-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_usage_response_shape(client):
    """Usage response has all required fields."""
    response = await client.get(
        "/v1/gateway/usage/enterprise_corp",
        params={"from_date": "2026-01-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    assert "tenant_id" in body
    assert "total_calls" in body
    assert "total_tokens" in body
    assert "total_cost_usd" in body
    assert "from_date" in body
    assert "to_date" in body


@pytest.mark.asyncio
async def test_get_usage_reflects_seeded_data(client):
    """enterprise_corp has seeded data — total_calls should be substantial."""
    response = await client.get(
        "/v1/gateway/usage/enterprise_corp",
        params={"from_date": "2025-12-01", "to_date": "2026-03-11"},
    )
    body = response.json()
    assert body["total_calls"] > 1000
    assert body["total_tokens"] > 1_000_000


@pytest.mark.asyncio
async def test_get_usage_unknown_tenant_returns_zeros(client):
    """Unknown tenant returns 200 with zero counts — not a 404."""
    response = await client.get(
        "/v1/gateway/usage/totally_unknown_tenant_xyz",
        params={"from_date": "2026-01-01", "to_date": "2026-03-11"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_calls"] == 0
    assert body["total_tokens"] == 0


@pytest.mark.asyncio
async def test_get_usage_defaults_work(client):
    """Usage endpoint works without date params — uses 30-day default."""
    response = await client.get("/v1/gateway/usage/enterprise_corp")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_usage_invalid_date_range(client):
    """from_date after to_date returns 400."""
    response = await client.get(
        "/v1/gateway/usage/enterprise_corp",
        params={"from_date": "2026-03-11", "to_date": "2026-01-01"},
    )
    assert response.status_code == 400