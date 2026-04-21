"""
Response caching for expensive API endpoints.

Uses Redis (cache database) with configurable TTL per endpoint.
Cache keys include tenant_id and period to prevent cross-tenant leakage.

Usage:
    from core.response_cache import response_cache

    @router.get("/expensive-endpoint")
    async def get_data(tenant_id: str, period: int):
        cached = await response_cache.get("executive", tenant_id=tenant_id, period=period)
        if cached is not None:
            return cached
        result = await expensive_computation()
        await response_cache.set("executive", result, ttl=60, tenant_id=tenant_id, period=period)
        return result
"""

import json
import logging
from typing import Optional, Any

from core.redis import get_cache_redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Default TTL per cache namespace (seconds)
DEFAULT_TTLS = {
    "executive_overview": 60,      # 1 minute — high traffic, expensive query
    "tenant_detail": 30,           # 30 seconds — per-tenant, less expensive
    "governance_summary": 120,     # 2 minutes — rarely changes
    "agent_breakdown": 60,         # 1 minute — medium traffic
}


class ResponseCache:
    """Redis-backed response cache with automatic key namespacing."""

    def _build_key(self, namespace: str, **kwargs) -> str:
        """Build a deterministic cache key from namespace + parameters."""
        parts = [f"cache:{namespace}"]
        for k in sorted(kwargs.keys()):
            v = kwargs[k]
            if v is not None:
                parts.append(f"{k}={v}")
        return ":".join(parts)

    async def get(self, namespace: str, **kwargs) -> Optional[Any]:
        """
        Fetch a cached response. Returns None on miss or error.
        Never raises — cache failures are transparent to the caller.
        """
        key = self._build_key(namespace, **kwargs)
        redis = get_cache_redis()
        try:
            raw = await redis.get(key)
            if raw is None:
                return None
            logger.debug(f"cache.hit namespace={namespace} key={key}")
            return json.loads(raw)
        except (RedisError, json.JSONDecodeError) as e:
            logger.warning(f"cache.get_failed namespace={namespace} error={e}")
            return None

    async def set(
        self,
        namespace: str,
        data: Any,
        ttl: Optional[int] = None,
        **kwargs,
    ) -> bool:
        """
        Store a response in cache with TTL.
        Uses default TTL for the namespace if not specified.
        Returns True on success, False on error.
        """
        key = self._build_key(namespace, **kwargs)
        effective_ttl = ttl or DEFAULT_TTLS.get(namespace, 60)
        redis = get_cache_redis()
        try:
            serialized = json.dumps(data, default=str)
            await redis.set(key, serialized, ex=effective_ttl)
            logger.debug(f"cache.set namespace={namespace} key={key} ttl={effective_ttl}")
            return True
        except (RedisError, TypeError) as e:
            logger.warning(f"cache.set_failed namespace={namespace} error={e}")
            return False

    async def invalidate(self, namespace: str, **kwargs) -> None:
        """Invalidate a specific cache entry."""
        key = self._build_key(namespace, **kwargs)
        redis = get_cache_redis()
        try:
            await redis.delete(key)
            logger.debug(f"cache.invalidated key={key}")
        except RedisError as e:
            logger.warning(f"cache.invalidate_failed key={key} error={e}")

    async def invalidate_namespace(self, namespace: str) -> None:
        """Invalidate all entries in a namespace (e.g., after data mutation)."""
        pattern = f"cache:{namespace}:*"
        redis = get_cache_redis()
        try:
            count = 0
            async for key in redis.scan_iter(match=pattern, count=100):
                await redis.delete(key)
                count += 1
            if count > 0:
                logger.info(f"cache.namespace_invalidated namespace={namespace} keys={count}")
        except RedisError as e:
            logger.warning(f"cache.namespace_invalidate_failed namespace={namespace} error={e}")


# Singleton
response_cache = ResponseCache()
