"""
M10 Billing tests.

Tests cover:
  1. Contract engine (pure logic — no database)
  2. Line item generation (pure logic)
  3. API endpoints (real database)
"""

import pytest
from decimal import Decimal
from httpx import AsyncClient, ASGITransport

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession
)
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.billing.services.billing_service import (
    BillingService
)
from main import app
from modules.auth.dependencies import require_super_admin, require_tenant_viewer
from modules.auth.schemas import CurrentUser


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
    return BillingService(db_session)


@pytest.fixture(autouse=True)
def override_auth():
    mock_user = CurrentUser(
        id="test_admin",
        username="admin",
        role="super_admin",
        tenant_id="enterprise_corp"
    )
    app.dependency_overrides[require_super_admin] = lambda: mock_user
    app.dependency_overrides[require_tenant_viewer] = lambda: mock_user
    yield
    app.dependency_overrides.clear()

# ============================================================
# CONTRACT ENGINE TESTS — pure logic, no database
# ============================================================

class TestContractEngine:

    def test_pay_as_you_go_total_equals_raw_cost(self, service):
        """Pay-as-you-go: total billed = raw cost, no base fee."""
        contract = {
            "contract_type":       "pay_as_you_go",
            "base_fee_usd":        0.0,
            "forfait_tokens":      0,
            "overage_rate_per_1k": 0.0,
        }
        usage = {
            "total_tokens":   5_000_000,
            "total_cost_usd": 23.18,
            "total_calls":    15000,
        }
        result = service._apply_contract(usage, contract)
        assert result["base_fee_usd"]       == 0.0
        assert result["overage_tokens"]     == 0
        assert result["total_billed_usd"]   == 23.18

    def test_forfait_within_limit_no_overage(self, service):
        """Forfait: if usage < forfait, no overage charge."""
        contract = {
            "contract_type":       "forfait",
            "base_fee_usd":        150.0,
            "forfait_tokens":      50_000_000,
            "overage_rate_per_1k": 0.003,
        }
        usage = {
            "total_tokens":   30_000_000,   # under the 50M limit
            "total_cost_usd": 75.0,
            "total_calls":    40000,
        }
        result = service._apply_contract(usage, contract)
        assert result["base_fee_usd"]      == 150.0
        assert result["overage_tokens"]    == 0
        assert result["overage_charge_usd"]== 0.0
        assert result["total_billed_usd"]  == 150.0

    def test_forfait_over_limit_generates_overage(self, service):
        """Forfait: tokens above limit generate overage charge."""
        contract = {
            "contract_type":       "forfait",
            "base_fee_usd":        150.0,
            "forfait_tokens":      50_000_000,
            "overage_rate_per_1k": 0.003,
        }
        usage = {
            "total_tokens":   60_000_000,   # 10M over the limit
            "total_cost_usd": 150.0,
            "total_calls":    60000,
        }
        result = service._apply_contract(usage, contract)
        assert result["overage_tokens"]     == 10_000_000
        # 10M tokens / 1K * 0.003 = $30 overage
        assert result["overage_charge_usd"] == 30.0
        assert result["total_billed_usd"]   == 180.0   # 150 + 30

    def test_total_never_negative(self, service):
        """Total billed amount is always >= 0."""
        contract = {
            "contract_type":       "forfait",
            "base_fee_usd":        0.0,
            "forfait_tokens":      100_000_000,
            "overage_rate_per_1k": 0.003,
        }
        usage = {
            "total_tokens":   0,
            "total_cost_usd": 0.0,
            "total_calls":    0,
        }
        result = service._apply_contract(usage, contract)
        assert result["total_billed_usd"] >= 0


# ============================================================
# LINE ITEM TESTS — pure logic
# ============================================================

class TestLineItems:

    def test_forfait_generates_base_fee_line(self, service):
        """Forfait contract always generates a base_fee line item."""
        from uuid import uuid4
        contract = {
            "contract_type":       "forfait",
            "base_fee_usd":        150.0,
            "forfait_tokens":      50_000_000,
            "overage_rate_per_1k": 0.003,
        }
        amounts = {
            "base_fee_usd":      150.0,
            "forfait_tokens_used": 30_000_000,
            "overage_tokens":    0,
            "overage_charge_usd":0.0,
            "total_billed_usd":  150.0,
        }
        model_breakdown = []
        items = service._build_line_items(
            billing_id=uuid4(),
            tenant_id="enterprise_corp",
            year_month=202603,
            contract=contract,
            amounts=amounts,
            model_breakdown=model_breakdown,
        )
        base_fees = [i for i in items if i["line_type"] == "base_fee"]
        assert len(base_fees) == 1
        assert float(base_fees[0]["amount_usd"]) == 150.0

    def test_model_lines_generated_per_model(self, service):
        """One usage_cost line item is generated per model used."""
        from uuid import uuid4
        contract = {
            "contract_type": "pay_as_you_go",
            "base_fee_usd": 0.0,
            "forfait_tokens": 0,
            "overage_rate_per_1k": 0.0,
        }
        amounts = {
            "base_fee_usd": 0.0,
            "forfait_tokens_used": 0,
            "overage_tokens": 0,
            "overage_charge_usd": 0.0,
            "total_billed_usd": 35.0,
        }
        model_breakdown = [
            {"model": "gpt-4o", "provider": "openai",
             "call_count": 1000, "total_tokens": 1_000_000,
             "input_tokens": 700_000, "output_tokens": 300_000,
             "total_cost": 25.0},
            {"model": "gpt-4o-mini", "provider": "openai",
             "call_count": 500, "total_tokens": 500_000,
             "input_tokens": 400_000, "output_tokens": 100_000,
             "total_cost": 10.0},
        ]
        items = service._build_line_items(
            billing_id=uuid4(),
            tenant_id="startup_ai",
            year_month=202603,
            contract=contract,
            amounts=amounts,
            model_breakdown=model_breakdown,
        )
        usage_lines = [i for i in items if i["line_type"] == "usage_cost"]
        assert len(usage_lines) == 2

    def test_overage_line_only_when_overage_exists(self, service):
        """Overage line only appears when overage_charge > 0."""
        from uuid import uuid4
        contract = {
            "contract_type": "forfait",
            "base_fee_usd": 150.0,
            "forfait_tokens": 50_000_000,
            "overage_rate_per_1k": 0.003,
        }
        amounts_no_overage = {
            "base_fee_usd": 150.0, "forfait_tokens_used": 30_000_000,
            "overage_tokens": 0, "overage_charge_usd": 0.0,
            "total_billed_usd": 150.0,
        }
        items = service._build_line_items(
            billing_id=uuid4(), tenant_id="enterprise_corp",
            year_month=202603, contract=contract,
            amounts=amounts_no_overage, model_breakdown=[],
        )
        overage_lines = [i for i in items if i["line_type"] == "overage"]
        assert len(overage_lines) == 0


# ============================================================
# API TESTS
# ============================================================

class TestBillingAPI:

    @pytest.mark.asyncio
    async def test_generate_invoice_startup_ai(self, client: AsyncClient, token_super_admin: str):
        # Trigger billing process manually
        response = await client.post(
            "/v1/billing/generate/startup_ai/202603",
            headers={"Authorization": f"Bearer {token_super_admin}"}
        )
        # Even if it succeeds, it may return 422 if there's no data.
        # So we just ensure it doesn't return 500.
        assert response.status_code in [200, 422]

    @pytest.mark.asyncio
    async def test_pdf_export_startup_ai(self, client: AsyncClient, token_super_admin: str):
        response = await client.get(
            "/v1/billing/invoice/startup_ai/202603/pdf",
            headers={"Authorization": f"Bearer {token_super_admin}"}
        )
        assert response.status_code in [200, 404]
        if response.status_code == 200:
            assert response.headers["content-type"] == "application/pdf"
            assert response.content.startswith(b"%PDF")

    @pytest.mark.asyncio
    async def test_generate_invoice_enterprise_corp(self, client):
        """Generate invoice for enterprise_corp (forfait contract)."""
        response = await client.post(
            "/v1/billing/generate/enterprise_corp/202603"
        )
        assert response.status_code in (200, 422)
        if response.status_code == 200:
            body = response.json()
            assert body["contract_type"] == "forfait"
            assert "base_fee_usd" in body
            assert "line_items_count" in body

    @pytest.mark.asyncio
    async def test_get_invoice_not_found(self, client):
        """Requesting non-existent invoice returns 404."""
        response = await client.get(
            "/v1/billing/invoice/unknown_tenant_xyz/209901"
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_invoices_returns_list(self, client):
        """List invoices endpoint returns correct structure."""
        response = await client.get("/v1/billing/invoices/enterprise_corp")
        assert response.status_code == 200
        body = response.json()
        assert "tenant_id" in body
        assert "invoices" in body
        assert isinstance(body["invoices"], list)

    @pytest.mark.asyncio
    async def test_generate_all_returns_summary(self, client):
        """Batch generate returns a summary with counts."""
        response = await client.post("/v1/billing/generate-all/202603")
        assert response.status_code == 200
        body = response.json()
        assert "year_month"    in body
        assert "total_tenants" in body
        assert "succeeded"     in body
        assert "failed"        in body
        assert body["total_tenants"] >= 0