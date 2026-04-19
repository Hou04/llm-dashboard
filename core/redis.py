import json
import structlog
from datetime import date
from typing import Optional, Any
from redis.asyncio import Redis
from redis.exceptions import RedisError

from core.settings import settings

log = structlog.get_logger()


def get_redis_sync():
    """
    Returns a synchronous Redis client (used for JTI blacklisting in auth service).
    The auth service blacklist_token / is_blacklisted methods are called from sync context.
    """
    import redis as _redis_sync
    return _redis_sync.from_url(settings.redis_url, decode_responses=True)



# ============================================================
# CLIENT FACTORIES
#
# Each call creates a fresh Redis client.
# In production, redis-py manages its own internal pool.
# No module-level singletons means no event loop conflicts.
# ============================================================

def get_quota_redis() -> Redis:
    """
    Returns a Redis client for quota operations (database 0).
    Always call await redis.aclose() when done.
    """
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )


def get_cache_redis() -> Redis:
    """
    Returns a Redis client for cache operations (database 1).
    Always call await redis.aclose() when done.
    """
    return Redis.from_url(
        settings.redis_cache_url,
        decode_responses=True,
    )


# ============================================================
# TTL CONSTANTS
# ============================================================
DAILY_TTL_SECONDS = 25 * 3600
MONTHLY_TTL_SECONDS = 35 * 24 * 3600
RULE_CACHE_TTL = 5 * 60


# ============================================================
# KEY BUILDERS
# ============================================================

def _daily_key(tenant_id: str, metric: str) -> str:
    today = date.today().isoformat()
    return f"quota:{tenant_id}:{metric}:daily:{today}"


def _monthly_key(tenant_id: str, metric: str) -> str:
    month = date.today().strftime("%Y-%m")
    return f"quota:{tenant_id}:{metric}:monthly:{month}"


# ============================================================
# QUOTA COUNTER OPERATIONS
# ============================================================

async def increment_token_usage(
    tenant_id: str,
    tokens: int,
) -> dict[str, int]:
    """Atomically increments token usage. Returns new daily and monthly totals."""
    daily_key = _daily_key(tenant_id, "tokens")
    monthly_key = _monthly_key(tenant_id, "tokens")

    redis = get_quota_redis()
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incrby(daily_key, tokens)
            pipe.expire(daily_key, DAILY_TTL_SECONDS)
            pipe.incrby(monthly_key, tokens)
            pipe.expire(monthly_key, MONTHLY_TTL_SECONDS)
            results = await pipe.execute()
        return {"daily": int(results[0]), "monthly": int(results[2])}
    except RedisError as e:
        log.error("redis.quota.increment_tokens_failed",
                  tenant_id=tenant_id, tokens=tokens, error=str(e))
        raise
    finally:
        await redis.aclose()


async def increment_cost_usage(
    tenant_id: str,
    cost_usd: float,
) -> dict[str, float]:
    """Atomically increments cost usage. Returns new daily and monthly totals."""
    daily_key = _daily_key(tenant_id, "cost")
    monthly_key = _monthly_key(tenant_id, "cost")

    redis = get_quota_redis()
    try:
        async with redis.pipeline(transaction=False) as pipe:
            pipe.incrbyfloat(daily_key, cost_usd)
            pipe.expire(daily_key, DAILY_TTL_SECONDS)
            pipe.incrbyfloat(monthly_key, cost_usd)
            pipe.expire(monthly_key, MONTHLY_TTL_SECONDS)
            results = await pipe.execute()
        return {"daily": float(results[0]), "monthly": float(results[2])}
    except RedisError as e:
        log.error("redis.quota.increment_cost_failed",
                  tenant_id=tenant_id, cost_usd=cost_usd, error=str(e))
        raise
    finally:
        await redis.aclose()


async def get_current_usage(tenant_id: str) -> dict[str, Any]:
    """Returns all current quota usage for a tenant. Returns zeros if none exists."""
    keys = [
        _daily_key(tenant_id, "tokens"),
        _monthly_key(tenant_id, "tokens"),
        _daily_key(tenant_id, "cost"),
        _monthly_key(tenant_id, "cost"),
    ]

    redis = get_quota_redis()
    try:
        async with redis.pipeline(transaction=False) as pipe:
            for key in keys:
                pipe.get(key)
            results = await pipe.execute()
        return {
            "daily_tokens": int(results[0] or 0),
            "monthly_tokens": int(results[1] or 0),
            "daily_cost_usd": float(results[2] or 0.0),
            "monthly_cost_usd": float(results[3] or 0.0),
        }
    except RedisError as e:
        log.error("redis.quota.get_usage_failed", tenant_id=tenant_id, error=str(e))
        raise
    finally:
        await redis.aclose()


async def reset_tenant_quota(tenant_id: str) -> None:
    """Deletes all quota keys for a tenant."""
    keys = [
        _daily_key(tenant_id, "tokens"),
        _monthly_key(tenant_id, "tokens"),
        _daily_key(tenant_id, "cost"),
        _monthly_key(tenant_id, "cost"),
    ]
    redis = get_quota_redis()
    try:
        await redis.delete(*keys)
        log.info("redis.quota.reset", tenant_id=tenant_id)
    except RedisError as e:
        log.error("redis.quota.reset_failed", tenant_id=tenant_id, error=str(e))
        raise
    finally:
        await redis.aclose()


# ============================================================
# RULE CACHE OPERATIONS
# ============================================================

async def get_cached_rule(rule_key: str) -> Optional[dict]:
    """Fetches a rule from cache. Returns None on miss."""
    redis = get_cache_redis()
    try:
        value = await redis.get(rule_key)
        if value is None:
            log.debug("redis.cache.miss", key=rule_key)
            return None
        log.debug("redis.cache.hit", key=rule_key)
        return json.loads(value)
    except RedisError as e:
        log.error("redis.cache.get_failed", key=rule_key, error=str(e))
        return None
    finally:
        await redis.aclose()


async def set_cached_rule(
    rule_key: str,
    rule_data: dict,
    ttl: int = RULE_CACHE_TTL,
) -> bool:
    """Stores a rule in cache with TTL. Returns True on success."""
    redis = get_cache_redis()
    try:
        serialized = json.dumps(rule_data)
        await redis.set(rule_key, serialized, ex=ttl)
        log.debug("redis.cache.set", key=rule_key, ttl=ttl)
        return True
    except RedisError as e:
        log.error("redis.cache.set_failed", key=rule_key, error=str(e))
        return False
    finally:
        await redis.aclose()


async def invalidate_cached_rule(rule_key: str) -> None:
    """Deletes a rule from cache immediately."""
    redis = get_cache_redis()
    try:
        await redis.delete(rule_key)
        log.info("redis.cache.invalidated", key=rule_key)
    except RedisError as e:
        log.error("redis.cache.invalidate_failed", key=rule_key, error=str(e))
    finally:
        await redis.aclose()


async def invalidate_tenant_rules(tenant_id: str) -> None:
    """Deletes all cached rules for a tenant using SCAN (non-blocking)."""
    pattern = f"rules:tenant:{tenant_id}:*"
    redis = get_cache_redis()
    try:
        deleted_count = 0
        async for key in redis.scan_iter(match=pattern):
            await redis.delete(key)
            deleted_count += 1
        log.info("redis.cache.tenant_invalidated",
                 tenant_id=tenant_id, keys_deleted=deleted_count)
    except RedisError as e:
        log.error("redis.cache.tenant_invalidate_failed",
                  tenant_id=tenant_id, error=str(e))
    finally:
        await redis.aclose()


# ============================================================
# HEALTH CHECK AND STARTUP/SHUTDOWN
# ============================================================

async def init_redis() -> None:
    """Verifies both Redis databases are reachable. Called on app startup."""
    errors = []

    redis_quota = get_quota_redis()
    try:
        await redis_quota.ping()
        log.info("redis.quota.connected", url=settings.redis_url)
    except RedisError as e:
        errors.append(f"Quota Redis: {e}")
    finally:
        await redis_quota.aclose()

    redis_cache = get_cache_redis()
    try:
        await redis_cache.ping()
        log.info("redis.cache.connected", url=settings.redis_cache_url)
    except RedisError as e:
        errors.append(f"Cache Redis: {e}")
    finally:
        await redis_cache.aclose()

    if errors:
        raise ConnectionError(f"Redis connection failed: {', '.join(errors)}")


async def close_redis() -> None:
    """No-op — no persistent pools to close in this design."""
    log.info("redis.disconnected")