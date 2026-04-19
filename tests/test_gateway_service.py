"""
GatewayService tests.

These tests use the real database and real Redis.
They test the full business logic pipeline:
- governance evaluation
- call logging
- usage tracking
- event publishing

Each test creates its own unique tenant_id to avoid state pollution
between tests. Since we use a session-scoped DB connection, all
data written in tests persists for the test session but is not
committed permanently (we roll back at session teardown... actually
we do commit in log_call, so data IS written. Tests use unique
tenant IDs to stay isolated from each other).
"""

import uuid
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.gateway.services import GatewayService, CallRequest, GovernanceResult
from modules.gateway.models import (
    CallStatus,
    GovernanceDecision,
    RuleType,
    LLMGovernanceRule,
)
from modules.gateway.repositories import RuleRepository


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Fresh session per test — service commits inside log_call."""
    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session


def make_request(**kwargs) -> CallRequest:
    """Build a minimal valid CallRequest."""
    defaults = {
        "tenant_id": f"svc_tenant_{uuid.uuid4().hex[:8]}",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": Decimal("0.00007500"),
        "duration_ms": 300,
        "status": CallStatus.SUCCESS.value,
    }
    defaults.update(kwargs)
    return CallRequest(**defaults)


# ============================================================
# GOVERNANCE EVALUATION TESTS
# (no database writes — pure rule logic)
# ============================================================

@pytest.mark.asyncio
async def test_evaluate_governance_no_rules(db_session):
    """With no rules configured, all calls are allowed by default."""
    tenant = f"no_rules_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    result = await service.evaluate_governance(
        tenant_id=tenant,
        model="gpt-4o",
        tokens_requested=1000,
    )

    assert result.decision == GovernanceDecision.ALLOW.value
    assert result.is_allowed is True
    assert result.is_blocked is False


@pytest.mark.asyncio
async def test_evaluate_governance_model_block(db_session):
    """MODEL_BLOCK rule blocks the specified model."""
    tenant = f"block_tenant_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    # Create a rule that blocks gpt-4o for this tenant
    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.MODEL_BLOCK.value,
        tenant_id=tenant,
        model_name="gpt-4o",
        priority=200,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    result = await service.evaluate_governance(
        tenant_id=tenant,
        model="gpt-4o",
        tokens_requested=100,
    )

    assert result.is_blocked is True
    assert "blocked" in result.reason.lower()


@pytest.mark.asyncio
async def test_evaluate_governance_model_block_other_model_allowed(db_session):
    """MODEL_BLOCK rule does not block other models."""
    tenant = f"block_other_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.MODEL_BLOCK.value,
        tenant_id=tenant,
        model_name="gpt-4o",
        priority=200,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    result = await service.evaluate_governance(
        tenant_id=tenant,
        model="gpt-4o-mini",  # different model
        tokens_requested=100,
    )

    assert result.is_allowed is True


@pytest.mark.asyncio
async def test_evaluate_governance_rate_limit(db_session):
    """RATE_LIMIT rule blocks requests exceeding per-request token max."""
    tenant = f"rate_tenant_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.RATE_LIMIT.value,
        tenant_id=tenant,
        max_tokens_per_request=500,
        priority=100,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    # 600 tokens > 500 limit — should block
    blocked = await service.evaluate_governance(
        tenant_id=tenant,
        model="gpt-4o-mini",
        tokens_requested=600,
    )
    assert blocked.is_blocked is True

    # 400 tokens < 500 limit — should allow
    allowed = await service.evaluate_governance(
        tenant_id=tenant,
        model="gpt-4o-mini",
        tokens_requested=400,
    )
    assert allowed.is_allowed is True


@pytest.mark.asyncio
async def test_evaluate_governance_model_downgrade(db_session):
    """MODEL_DOWNGRADE rule substitutes the requested model."""
    tenant = f"downgrade_tenant_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.MODEL_DOWNGRADE.value,
        tenant_id=tenant,
        model_name="gpt-4o",
        downgrade_to_model="gpt-4o-mini",
        priority=150,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    result = await service.evaluate_governance(
        tenant_id=tenant,
        model="gpt-4o",
        tokens_requested=100,
    )

    assert result.decision == GovernanceDecision.ALLOW_DOWNGRADE.value
    assert result.model_to_use == "gpt-4o-mini"
    assert result.was_downgraded is True


# ============================================================
# LOG CALL TESTS
# (full pipeline — writes to database)
# ============================================================

@pytest.mark.asyncio
async def test_log_call_success(db_session):
    """log_call() succeeds and returns a log_id."""
    service = GatewayService(db_session)
    request = make_request()

    result = await service.log_call(request)

    assert result.success is True
    assert result.log_id is not None
    assert result.decision == GovernanceDecision.ALLOW.value
    assert result.error is None


@pytest.mark.asyncio
async def test_log_call_returns_correct_model(db_session):
    """log_call() returns the model that was actually used."""
    service = GatewayService(db_session)
    request = make_request(model="gpt-4o-mini")

    result = await service.log_call(request)

    assert result.model_used == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_log_call_blocked_by_rule(db_session):
    """log_call() returns failure when governance blocks the call."""
    tenant = f"blocked_svc_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    # Create a block rule for this tenant
    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.MODEL_BLOCK.value,
        tenant_id=tenant,
        model_name="gpt-4o",
        priority=200,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    request = make_request(tenant_id=tenant, model="gpt-4o")
    result = await service.log_call(request)

    assert result.success is False
    assert result.decision == GovernanceDecision.BLOCK.value
    assert result.error is not None


@pytest.mark.asyncio
async def test_log_call_with_downgrade(db_session):
    """log_call() logs with the downgraded model when rule applies."""
    tenant = f"downgrade_svc_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.MODEL_DOWNGRADE.value,
        tenant_id=tenant,
        model_name="gpt-4o",
        downgrade_to_model="gpt-4o-mini",
        priority=150,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    request = make_request(tenant_id=tenant, model="gpt-4o")
    result = await service.log_call(request)

    assert result.success is True
    assert result.was_downgraded is True
    assert result.model_used == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_log_call_publishes_event(db_session):
    """log_call() publishes a call.logged event after commit."""
    service = GatewayService(db_session)
    request = make_request()

    published_events = []

    async def capture_event(data):
        print(f"\nEVENT PAYLOAD KEYS: {list(data.keys())}")
        published_events.append({"type": "call.logged", "data": data})
    from core.event_bus import register_handler, unregister_handler
    register_handler("call.logged", capture_event)

    try:
        result = await service.log_call(request)
        assert result.success is True
        assert len(published_events) == 1
        event = published_events[0]
        assert event["type"] == "call.logged"
        # event bus wraps payload: handler receives {event_id, event_type, data, ...}
        payload = event["data"]
        assert payload.get("tenant_id") == request.tenant_id or \
               payload.get("data", {}).get("tenant_id") == request.tenant_id
    finally:
        unregister_handler("call.logged", capture_event)


@pytest.mark.asyncio
async def test_log_call_blocked_publishes_blocked_event(db_session):
    """log_call() publishes call.blocked event when governance blocks."""
    tenant = f"blocked_evt_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    rule_repo = RuleRepository(db_session)
    rule = LLMGovernanceRule(
        rule_type=RuleType.MODEL_BLOCK.value,
        tenant_id=tenant,
        model_name="gpt-4o",
        priority=200,
        is_active=True,
    )
    await rule_repo.create(rule)
    await db_session.commit()

    blocked_events = []

    async def capture_blocked(data):
        blocked_events.append(data)

    from core.event_bus import register_handler, unregister_handler
    register_handler("call.blocked", capture_blocked)

    try:
        request = make_request(tenant_id=tenant, model="gpt-4o")
        result = await service.log_call(request)
        assert result.success is False
        assert len(blocked_events) == 1
        payload = blocked_events[0]
        # event bus wraps payload: handler receives the envelope dict
        actual = payload.get("tenant_id") or payload.get("data", {}).get("tenant_id")
        assert actual == tenant
    finally:
        unregister_handler("call.blocked", capture_blocked)


# ============================================================
# USAGE TESTS
# ============================================================

@pytest.mark.asyncio
async def test_get_tenant_usage_returns_summary(db_session):
    """get_tenant_usage() returns aggregated usage data."""
    tenant = f"usage_svc_{uuid.uuid4().hex[:8]}"
    service = GatewayService(db_session)

    # Log two calls
    for _ in range(2):
        request = make_request(tenant_id=tenant, total_tokens=200)
        await service.log_call(request)

    now = datetime.now(timezone.utc)
    summary = await service.get_tenant_usage(
        tenant_id=tenant,
        from_dt=now - timedelta(minutes=5),
        to_dt=now + timedelta(minutes=1),
    )

    assert summary["total_calls"] == 2
    assert summary["total_tokens"] == 400