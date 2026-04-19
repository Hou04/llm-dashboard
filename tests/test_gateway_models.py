import uuid
import pytest
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from core.settings import settings
from modules.gateway.models import (
    LLMTokenLog,
    LLMGovernanceRule,
    LLMGovernanceDecision,
    CallStatus,
    GovernanceDecision,
    RuleType,
)


# ============================================================
# TEST ENGINE — NullPool to avoid event loop issues on Windows
# ============================================================

@pytest.fixture(scope="session")
async def db_session():
    """
    Session-scoped fixture. One engine + session for all DB tests.
    Torn down once at the end — avoids per-test loop-closed errors on Windows.
    """
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
# MODEL STRUCTURE TESTS — no database needed
# ============================================================

def test_llm_token_log_tablename():
    """LLMTokenLog maps to the correct table name."""
    assert LLMTokenLog.__tablename__ == "llm_token_log"


def test_llm_governance_rules_tablename():
    """LLMGovernanceRule maps to the correct table name."""
    assert LLMGovernanceRule.__tablename__ == "llm_governance_rules"


def test_llm_governance_decisions_tablename():
    """LLMGovernanceDecision maps to the correct table name."""
    assert LLMGovernanceDecision.__tablename__ == "llm_governance_decisions"


def test_call_status_enum_values():
    """CallStatus enum has all expected values."""
    assert CallStatus.SUCCESS.value == "success"
    assert CallStatus.ERROR.value == "error"
    assert CallStatus.TIMEOUT.value == "timeout"
    assert CallStatus.BLOCKED.value == "blocked"


def test_governance_decision_enum_values():
    """GovernanceDecision enum has all expected values."""
    assert GovernanceDecision.ALLOW.value == "allow"
    assert GovernanceDecision.ALLOW_DOWNGRADE.value == "allow_downgrade"
    assert GovernanceDecision.BLOCK.value == "block"


def test_rule_type_enum_values():
    """RuleType enum has all expected values."""
    assert RuleType.TENANT_LIMIT.value == "tenant_limit"
    assert RuleType.MODEL_BLOCK.value == "model_block"
    assert RuleType.BUDGET_CAP.value == "budget_cap"


def test_token_log_instance_creation():
    """LLMTokenLog instance can be created with required fields."""
    log = LLMTokenLog(
        tenant_id="tenant_abc",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=320,
        output_tokens=180,
        total_tokens=500,
        cost_usd=Decimal("0.00025000"),
    )
    assert log.tenant_id == "tenant_abc"
    assert log.model == "gpt-4o-mini"
    assert log.total_tokens == 500


def test_token_log_default_status():
    """LLMTokenLog defaults to success status."""
    log = LLMTokenLog(
        tenant_id="t", model="m", provider="p",
        input_tokens=0, output_tokens=0, total_tokens=0,
        cost_usd=Decimal("0"),
    )
    assert log.status == CallStatus.SUCCESS.value


def test_token_log_is_successful_property():
    """is_successful property returns True for success status."""
    log = LLMTokenLog(
        tenant_id="t", model="m", provider="p",
        input_tokens=0, output_tokens=0, total_tokens=0,
        cost_usd=Decimal("0"),
        status=CallStatus.SUCCESS.value,
    )
    assert log.is_successful is True


def test_token_log_cost_float_property():
    """cost_float property converts Decimal to float."""
    log = LLMTokenLog(
        tenant_id="t", model="m", provider="p",
        input_tokens=0, output_tokens=0, total_tokens=0,
        cost_usd=Decimal("0.00025000"),
    )
    assert isinstance(log.cost_float, float)
    assert abs(log.cost_float - 0.00025) < 0.000001


def test_governance_rule_applies_to_all_tenants():
    """applies_to_all_tenants is True when tenant_id is None."""
    rule = LLMGovernanceRule(rule_type=RuleType.MODEL_BLOCK.value)
    assert rule.applies_to_all_tenants is True


def test_governance_rule_applies_to_specific_tenant():
    """applies_to_all_tenants is False when tenant_id is set."""
    rule = LLMGovernanceRule(
        rule_type=RuleType.TENANT_LIMIT.value,
        tenant_id="tenant_abc",
    )
    assert rule.applies_to_all_tenants is False


def test_governance_decision_was_blocked():
    """was_blocked property returns True for block decisions."""
    decision = LLMGovernanceDecision(
        tenant_id="t",
        model_requested="gpt-4o",
        decision=GovernanceDecision.BLOCK.value,
    )
    assert decision.was_blocked is True
    assert decision.was_downgraded is False


# ============================================================
# DATABASE INTEGRATION TESTS — requires running PostgreSQL
# ============================================================

@pytest.mark.asyncio
async def test_tables_exist_in_database(db_session):
    """All three gateway tables exist in the database."""
    for table_name in [
        "llm_token_log",
        "llm_governance_rules",
        "llm_governance_decisions",
    ]:
        result = await db_session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                "WHERE table_name = :name)"
            ),
            {"name": table_name},
        )
        exists = result.scalar()
        assert exists is True, f"Table '{table_name}' not found in database"


@pytest.mark.asyncio
async def test_llm_token_log_is_hypertable(db_session):
    """llm_token_log is a TimescaleDB hypertable."""
    result = await db_session.execute(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_name = 'llm_token_log'"
        )
    )
    row = result.fetchone()
    assert row is not None, "llm_token_log is not a TimescaleDB hypertable"


@pytest.mark.asyncio
async def test_insert_and_query_token_log(db_session):
    """Can insert a token log record and query it back."""
    log_id = uuid.uuid4()
    log = LLMTokenLog(
        id=log_id,
        tenant_id="test_tenant",
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        cost_usd=Decimal("0.00007500"),
        duration_ms=450,
        status=CallStatus.SUCCESS.value,
    )
    db_session.add(log)
    await db_session.flush()

    result = await db_session.execute(
        select(LLMTokenLog).where(LLMTokenLog.id == log_id)
    )
    fetched = result.scalar_one()

    assert fetched.tenant_id == "test_tenant"
    assert fetched.total_tokens == 150
    assert fetched.provider == "openai"


@pytest.mark.asyncio
async def test_insert_and_query_governance_rule(db_session):
    """Can insert a governance rule and query it back."""
    rule_id = uuid.uuid4()
    rule = LLMGovernanceRule(
        id=rule_id,
        rule_type=RuleType.TENANT_LIMIT.value,
        tenant_id="test_tenant",
        daily_token_limit=1_000_000,
        monthly_budget_usd=Decimal("100.00"),
        priority=200,
        is_active=True,
        description="Test limit rule",
    )
    db_session.add(rule)
    await db_session.flush()

    result = await db_session.execute(
        select(LLMGovernanceRule).where(LLMGovernanceRule.id == rule_id)
    )
    fetched = result.scalar_one()

    assert fetched.tenant_id == "test_tenant"
    assert fetched.daily_token_limit == 1_000_000
    assert fetched.priority == 200
    assert fetched.is_active is True


@pytest.mark.asyncio
async def test_insert_governance_decision_with_rule_fk(db_session):
    """Can insert a governance decision linked to a rule."""
    rule_id = uuid.uuid4()
    rule = LLMGovernanceRule(
        id=rule_id,
        rule_type=RuleType.BUDGET_CAP.value,
        tenant_id="test_tenant",
        monthly_budget_usd=Decimal("50.00"),
        is_active=True,
    )
    db_session.add(rule)
    await db_session.flush()

    decision = LLMGovernanceDecision(
        tenant_id="test_tenant",
        model_requested="gpt-4o",
        decision=GovernanceDecision.BLOCK.value,
        reason="Monthly budget exceeded",
        rule_id=rule_id,
        tokens_used_today=500_000,
    )
    db_session.add(decision)
    await db_session.flush()

    result = await db_session.execute(
        select(LLMGovernanceDecision).where(
            LLMGovernanceDecision.tenant_id == "test_tenant"
        )
    )
    fetched = result.scalar_one()

    assert fetched.decision == GovernanceDecision.BLOCK.value
    assert fetched.rule_id == rule_id
    assert fetched.was_blocked is True