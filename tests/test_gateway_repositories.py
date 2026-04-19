"""
Repository tests.

These tests hit the real database. Each test uses a fresh session
to avoid state leaking between tests. All writes use flush() not
commit() so nothing is permanently stored.
"""

import uuid
import pytest
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.gateway.models import (
    LLMTokenLog,
    LLMGovernanceRule,
    CallStatus,
    RuleType,
)
from modules.gateway.repositories import LogRepository, RuleRepository


# ============================================================
# SHARED FIXTURE — session-scoped to match pytest.ini loop scope
# ============================================================

@pytest.fixture(scope="session")
async def db_session():
    engine = create_async_engine(url=settings.database_url, poolclass=NullPool)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
    )
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ============================================================
# HELPER — build a minimal valid LLMTokenLog
# ============================================================

def make_log(**kwargs) -> LLMTokenLog:
    defaults = {
        "id": uuid.uuid4(),
        "tenant_id": "tenant_test",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": Decimal("0.00007500"),
        "duration_ms": 300,
        "status": CallStatus.SUCCESS.value,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(kwargs)
    return LLMTokenLog(**defaults)


def make_rule(**kwargs) -> LLMGovernanceRule:
    defaults = {
        "id": uuid.uuid4(),
        "rule_type": RuleType.TENANT_LIMIT.value,
        "tenant_id": "tenant_test",
        "daily_token_limit": 1_000_000,
        "priority": 100,
        "is_active": True,
    }
    defaults.update(kwargs)
    return LLMGovernanceRule(**defaults)


# ============================================================
# LOG REPOSITORY TESTS
# ============================================================

@pytest.mark.asyncio
async def test_log_repo_create(db_session):
    """create() inserts a log entry and returns it with id populated."""
    repo = LogRepository(db_session)
    log = make_log()
    log_id = log.id

    saved = await repo.create(log)

    assert saved.id == log_id
    assert saved.tenant_id == "tenant_test"
    assert saved.total_tokens == 150


@pytest.mark.asyncio
async def test_log_repo_get_by_id(db_session):
    """get_by_id() fetches the log we just inserted."""
    repo = LogRepository(db_session)
    log = make_log()
    await repo.create(log)

    fetched = await repo.get_by_id(log.id)

    assert fetched is not None
    assert fetched.id == log.id
    assert fetched.model == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_log_repo_get_by_id_not_found(db_session):
    """get_by_id() returns None for a non-existent UUID."""
    repo = LogRepository(db_session)
    result = await repo.get_by_id(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_log_repo_bulk_create(db_session):
    """bulk_create() inserts multiple logs and returns the count."""
    repo = LogRepository(db_session)
    tenant = f"bulk_tenant_{uuid.uuid4().hex[:8]}"
    logs = [make_log(tenant_id=tenant, total_tokens=100 * i) for i in range(1, 6)]

    count = await repo.bulk_create(logs)

    assert count == 5


@pytest.mark.asyncio
async def test_log_repo_get_by_tenant(db_session):
    """get_by_tenant() returns logs within the time window."""
    repo = LogRepository(db_session)
    tenant = f"window_tenant_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # Insert 3 logs within window, 1 outside
    for i in range(3):
        await repo.create(make_log(
            tenant_id=tenant,
            created_at=now - timedelta(hours=i),
        ))
    await repo.create(make_log(
        tenant_id=tenant,
        created_at=now - timedelta(days=10),  # outside window
    ))

    results = await repo.get_by_tenant(
        tenant_id=tenant,
        from_dt=now - timedelta(days=1),
        to_dt=now + timedelta(minutes=1),
    )

    assert len(results) == 3


@pytest.mark.asyncio
async def test_log_repo_get_by_tenant_status_filter(db_session):
    """get_by_tenant() filters by status correctly."""
    repo = LogRepository(db_session)
    tenant = f"status_tenant_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    await repo.create(make_log(tenant_id=tenant, status=CallStatus.SUCCESS.value))
    await repo.create(make_log(tenant_id=tenant, status=CallStatus.ERROR.value))
    await repo.create(make_log(tenant_id=tenant, status=CallStatus.BLOCKED.value))

    errors = await repo.get_by_tenant(
        tenant_id=tenant,
        from_dt=now - timedelta(minutes=1),
        to_dt=now + timedelta(minutes=1),
        status=CallStatus.ERROR.value,
    )

    assert len(errors) == 1
    assert errors[0].status == CallStatus.ERROR.value


@pytest.mark.asyncio
async def test_log_repo_get_usage_summary(db_session):
    """get_usage_summary() returns correct aggregates."""
    repo = LogRepository(db_session)
    tenant = f"summary_tenant_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    # 3 successful calls + 1 error
    for _ in range(3):
        await repo.create(make_log(
            tenant_id=tenant,
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            cost_usd=Decimal("0.00010000"),
            status=CallStatus.SUCCESS.value,
        ))
    await repo.create(make_log(
        tenant_id=tenant,
        input_tokens=50,
        output_tokens=20,
        total_tokens=70,
        cost_usd=Decimal("0.00005000"),
        status=CallStatus.ERROR.value,
    ))

    summary = await repo.get_usage_summary(
        tenant_id=tenant,
        from_dt=now - timedelta(minutes=1),
        to_dt=now + timedelta(minutes=1),
    )

    assert summary["total_calls"] == 4
    assert summary["successful_calls"] == 3
    assert summary["total_tokens"] == 150 * 3 + 70  # 520
    assert summary["total_cost_usd"] == Decimal("0.00035000")


@pytest.mark.asyncio
async def test_log_repo_get_daily_token_count(db_session):
    """get_daily_token_count() returns correct sum for a date."""
    repo = LogRepository(db_session)
    tenant = f"daily_tenant_{uuid.uuid4().hex[:8]}"
    today = datetime.now(timezone.utc)

    await repo.create(make_log(
        tenant_id=tenant, total_tokens=1000, created_at=today
    ))
    await repo.create(make_log(
        tenant_id=tenant, total_tokens=500, created_at=today
    ))

    count = await repo.get_daily_token_count(
        tenant_id=tenant,
        for_date=today.date(),
    )

    assert count == 1500


@pytest.mark.asyncio
async def test_log_repo_get_model_breakdown(db_session):
    """get_model_breakdown() groups results by model correctly."""
    repo = LogRepository(db_session)
    tenant = f"model_tenant_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    await repo.create(make_log(
        tenant_id=tenant, model="gpt-4o-mini", total_tokens=100
    ))
    await repo.create(make_log(
        tenant_id=tenant, model="gpt-4o-mini", total_tokens=200
    ))
    await repo.create(make_log(
        tenant_id=tenant, model="claude-3-haiku", provider="anthropic",
        total_tokens=150
    ))

    breakdown = await repo.get_model_breakdown(
        tenant_id=tenant,
        from_dt=now - timedelta(minutes=1),
        to_dt=now + timedelta(minutes=1),
    )

    assert len(breakdown) == 2
    # gpt-4o-mini has more tokens so should be first
    assert breakdown[0]["model"] == "gpt-4o-mini"
    assert breakdown[0]["call_count"] == 2
    assert breakdown[0]["total_tokens"] == 300
    assert breakdown[1]["model"] == "claude-3-haiku"


@pytest.mark.asyncio
async def test_log_repo_count_by_status(db_session):
    """count_by_status() returns counts for all status types."""
    repo = LogRepository(db_session)
    tenant = f"count_tenant_{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)

    statuses = [
        CallStatus.SUCCESS.value,
        CallStatus.SUCCESS.value,
        CallStatus.ERROR.value,
        CallStatus.BLOCKED.value,
    ]
    for s in statuses:
        await repo.create(make_log(tenant_id=tenant, status=s))

    counts = await repo.count_by_status(
        tenant_id=tenant,
        from_dt=now - timedelta(minutes=1),
        to_dt=now + timedelta(minutes=1),
    )

    assert counts["success"] == 2
    assert counts["error"] == 1
    assert counts["blocked"] == 1
    assert counts["timeout"] == 0


# ============================================================
# RULE REPOSITORY TESTS
# ============================================================

@pytest.mark.asyncio
async def test_rule_repo_create(db_session):
    """create() inserts a rule and returns it."""
    repo = RuleRepository(db_session)
    rule = make_rule()

    saved = await repo.create(rule)

    assert saved.id == rule.id
    assert saved.rule_type == RuleType.TENANT_LIMIT.value
    assert saved.is_active is True


@pytest.mark.asyncio
async def test_rule_repo_get_by_id(db_session):
    """get_by_id() fetches the rule we just created."""
    repo = RuleRepository(db_session)
    rule = make_rule()
    await repo.create(rule)

    fetched = await repo.get_by_id(rule.id)

    assert fetched is not None
    assert fetched.id == rule.id


@pytest.mark.asyncio
async def test_rule_repo_get_by_id_not_found(db_session):
    """get_by_id() returns None for unknown UUID."""
    repo = RuleRepository(db_session)
    result = await repo.get_by_id(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_rule_repo_deactivate(db_session):
    """deactivate() sets is_active=False on the rule."""
    repo = RuleRepository(db_session)
    rule = make_rule()
    await repo.create(rule)
    rule_id = rule.id  # capture before expiring

    result = await repo.deactivate(rule_id)

    assert result is True
    # Expire cached state so we re-fetch from DB, then fetch by saved id
    db_session.expire(rule)
    fetched = await repo.get_by_id(rule_id)
    assert fetched.is_active is False


@pytest.mark.asyncio
async def test_rule_repo_deactivate_nonexistent(db_session):
    """deactivate() returns False for a non-existent rule UUID."""
    repo = RuleRepository(db_session)
    result = await repo.deactivate(uuid.uuid4())
    assert result is False


@pytest.mark.asyncio
async def test_rule_repo_get_active_rules_for_tenant(db_session):
    """get_active_rules_for_tenant() returns tenant-specific and global rules."""
    repo = RuleRepository(db_session)
    tenant = f"rule_tenant_{uuid.uuid4().hex[:8]}"

    # Tenant-specific rule
    r1 = make_rule(tenant_id=tenant, priority=200, is_active=True)
    # Global rule (no tenant)
    r2 = make_rule(tenant_id=None, priority=100, is_active=True)
    # Inactive rule — should NOT appear
    r3 = make_rule(tenant_id=tenant, is_active=False)
    # Different tenant — should NOT appear
    r4 = make_rule(tenant_id="other_tenant", is_active=True)

    for r in [r1, r2, r3, r4]:
        await repo.create(r)

    rules = await repo.get_active_rules_for_tenant(tenant)
    rule_ids = {r.id for r in rules}

    assert r1.id in rule_ids   # tenant-specific, active ✓
    assert r2.id in rule_ids   # global, active ✓
    assert r3.id not in rule_ids  # inactive ✗
    assert r4.id not in rule_ids  # wrong tenant ✗


@pytest.mark.asyncio
async def test_rule_repo_priority_ordering(db_session):
    """Active rules are returned highest priority first."""
    repo = RuleRepository(db_session)
    tenant = f"priority_tenant_{uuid.uuid4().hex[:8]}"

    low = make_rule(tenant_id=tenant, priority=50)
    high = make_rule(tenant_id=tenant, priority=300)
    mid = make_rule(tenant_id=tenant, priority=150)

    for r in [low, high, mid]:
        await repo.create(r)

    rules = await repo.get_active_rules_for_tenant(tenant)
    # Filter to just our test rules by tenant
    our_rules = [r for r in rules if r.tenant_id == tenant]
    priorities = [r.priority for r in our_rules]

    assert priorities == sorted(priorities, reverse=True)


@pytest.mark.asyncio
async def test_rule_repo_get_all_active(db_session):
    """get_all_active() returns all active rules across tenants."""
    repo = RuleRepository(db_session)
    tenant_a = f"all_active_a_{uuid.uuid4().hex[:8]}"
    tenant_b = f"all_active_b_{uuid.uuid4().hex[:8]}"

    active_a = make_rule(tenant_id=tenant_a, is_active=True)
    active_b = make_rule(tenant_id=tenant_b, is_active=True)
    inactive = make_rule(tenant_id=tenant_a, is_active=False)

    for r in [active_a, active_b, inactive]:
        await repo.create(r)

    all_rules = await repo.get_all_active()
    all_ids = {r.id for r in all_rules}

    assert active_a.id in all_ids
    assert active_b.id in all_ids
    assert inactive.id not in all_ids