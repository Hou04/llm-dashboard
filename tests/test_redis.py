import pytest
import asyncio
from core.redis import (
    init_redis,
    close_redis,
    get_quota_redis,
    get_cache_redis,
    increment_token_usage,
    increment_cost_usage,
    get_current_usage,
    reset_tenant_quota,
    get_cached_rule,
    set_cached_rule,
    invalidate_cached_rule,
    invalidate_tenant_rules,
    _daily_key,
    _monthly_key,
)

# Test tenant — isolated from any real data
TEST_TENANT = "test_tenant_day4"


@pytest.fixture(autouse=True)
async def cleanup():
    """Clean up test tenant quota before and after each test."""
    await reset_tenant_quota(TEST_TENANT)
    yield
    await reset_tenant_quota(TEST_TENANT)


# ============================================================
# CONNECTION TESTS
# ============================================================

@pytest.mark.asyncio
async def test_quota_redis_ping():
    """Quota Redis (db 0) is reachable."""
    redis = get_quota_redis()
    try:
        result = await redis.ping()
        assert result is True
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_cache_redis_ping():
    """Cache Redis (db 1) is reachable."""
    redis = get_cache_redis()
    try:
        result = await redis.ping()
        assert result is True
    finally:
        await redis.aclose()


@pytest.mark.asyncio
async def test_init_redis():
    """init_redis() connects to both databases without error."""
    await init_redis()


# ============================================================
# QUOTA COUNTER TESTS
# ============================================================

@pytest.mark.asyncio
async def test_increment_tokens_returns_correct_totals():
    """Incrementing tokens returns the new running total."""
    result = await increment_token_usage(TEST_TENANT, 1000)
    assert result["daily"] == 1000
    assert result["monthly"] == 1000


@pytest.mark.asyncio
async def test_increment_tokens_accumulates():
    """Multiple increments accumulate correctly."""
    await increment_token_usage(TEST_TENANT, 1000)
    await increment_token_usage(TEST_TENANT, 500)
    result = await increment_token_usage(TEST_TENANT, 250)
    assert result["daily"] == 1750
    assert result["monthly"] == 1750


@pytest.mark.asyncio
async def test_increment_cost_returns_correct_totals():
    """Incrementing cost returns the new running total."""
    result = await increment_cost_usage(TEST_TENANT, 1.50)
    assert abs(result["daily"] - 1.50) < 0.001
    assert abs(result["monthly"] - 1.50) < 0.001


@pytest.mark.asyncio
async def test_increment_cost_accumulates():
    """Multiple cost increments accumulate correctly."""
    await increment_cost_usage(TEST_TENANT, 1.00)
    await increment_cost_usage(TEST_TENANT, 2.50)
    result = await increment_cost_usage(TEST_TENANT, 0.75)
    assert abs(result["daily"] - 4.25) < 0.001
    assert abs(result["monthly"] - 4.25) < 0.001


@pytest.mark.asyncio
async def test_get_current_usage_returns_zeros_for_new_tenant():
    """A tenant with no usage returns all zeros."""
    result = await get_current_usage(TEST_TENANT)
    assert result["daily_tokens"] == 0
    assert result["monthly_tokens"] == 0
    assert result["daily_cost_usd"] == 0.0
    assert result["monthly_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_get_current_usage_reflects_increments():
    """get_current_usage returns values set by increment operations."""
    await increment_token_usage(TEST_TENANT, 5000)
    await increment_cost_usage(TEST_TENANT, 3.75)

    result = await get_current_usage(TEST_TENANT)
    assert result["daily_tokens"] == 5000
    assert result["monthly_tokens"] == 5000
    assert abs(result["daily_cost_usd"] - 3.75) < 0.001
    assert abs(result["monthly_cost_usd"] - 3.75) < 0.001


@pytest.mark.asyncio
async def test_reset_tenant_quota():
    """reset_tenant_quota clears all counters to zero."""
    await increment_token_usage(TEST_TENANT, 9999)
    await increment_cost_usage(TEST_TENANT, 99.99)
    await reset_tenant_quota(TEST_TENANT)

    result = await get_current_usage(TEST_TENANT)
    assert result["daily_tokens"] == 0
    assert result["monthly_tokens"] == 0
    assert result["daily_cost_usd"] == 0.0
    assert result["monthly_cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_quota_keys_have_ttl():
    """Quota keys are set with a TTL (they will expire automatically)."""
    await increment_token_usage(TEST_TENANT, 100)

    redis = get_quota_redis()
    try:
        daily_key = _daily_key(TEST_TENANT, "tokens")
        ttl = await redis.ttl(daily_key)
        assert ttl > 0, "Key has no TTL — it will never expire"
        assert ttl <= 25 * 3600, "TTL is longer than expected"
    finally:
        await redis.aclose()


# ============================================================
# RULE CACHE TESTS
# ============================================================

@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    """Getting a non-existent cache key returns None."""
    result = await get_cached_rule("rules:test:nonexistent_key_xyz")
    assert result is None


@pytest.mark.asyncio
async def test_set_and_get_cached_rule():
    """A rule stored in the cache can be retrieved."""
    rule_key = f"rules:tenant:{TEST_TENANT}:limits"
    rule_data = {
        "daily_token_limit": 1_000_000,
        "monthly_budget_usd": 100.0,
        "allowed_models": ["gpt-4o-mini", "gpt-4o"],
    }

    success = await set_cached_rule(rule_key, rule_data)
    assert success is True

    retrieved = await get_cached_rule(rule_key)
    assert retrieved is not None
    assert retrieved["daily_token_limit"] == 1_000_000
    assert retrieved["monthly_budget_usd"] == 100.0
    assert "gpt-4o-mini" in retrieved["allowed_models"]

    # Cleanup
    await invalidate_cached_rule(rule_key)


@pytest.mark.asyncio
async def test_invalidate_cached_rule():
    """Invalidating a rule removes it from the cache."""
    rule_key = f"rules:tenant:{TEST_TENANT}:model_config"
    await set_cached_rule(rule_key, {"model": "gpt-4o", "max_tokens": 4096})

    # Verify it exists
    assert await get_cached_rule(rule_key) is not None

    # Invalidate it
    await invalidate_cached_rule(rule_key)

    # Verify it's gone
    assert await get_cached_rule(rule_key) is None


@pytest.mark.asyncio
async def test_cached_rule_has_ttl():
    """Cached rules expire automatically via TTL."""
    rule_key = f"rules:tenant:{TEST_TENANT}:ttl_test"
    await set_cached_rule(rule_key, {"test": True}, ttl=60)

    redis = get_cache_redis()
    try:
        ttl = await redis.ttl(rule_key)
        assert ttl > 0, "Cached rule has no TTL"
        assert ttl <= 60
    finally:
        await redis.aclose()
        await invalidate_cached_rule(rule_key)


@pytest.mark.asyncio
async def test_invalidate_tenant_rules():
    """invalidate_tenant_rules removes all rules for a tenant."""
    keys = [
        f"rules:tenant:{TEST_TENANT}:limits",
        f"rules:tenant:{TEST_TENANT}:models",
        f"rules:tenant:{TEST_TENANT}:config",
    ]
    for key in keys:
        await set_cached_rule(key, {"data": key})

    # Verify they exist
    for key in keys:
        assert await get_cached_rule(key) is not None

    # Invalidate all
    await invalidate_tenant_rules(TEST_TENANT)

    # Verify all gone
    for key in keys:
        assert await get_cached_rule(key) is None