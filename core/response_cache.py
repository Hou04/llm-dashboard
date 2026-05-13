"""
Response caching for expensive API endpoints.

Uses Redis (cache database) with configurable TTL per endpoint.
Cache keys include tenant_id and period to prevent cross-tenant leakage.

Includes SemanticCache for LLM prompt similarity caching:
- Computes prompt embeddings using sentence-transformers
- Stores embeddings + responses in Redis
- On new request: cosine similarity search → if > threshold, return cached
- Adds X-Cache: HIT header so the client knows

Usage:
    from core.response_cache import response_cache, semantic_cache

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
import hashlib
import logging
import time
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
    "semantic_llm": 3600,          # 1 hour — LLM responses are expensive
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


# ============================================================
# SEMANTIC CACHE — embedding-based prompt similarity
# ============================================================

class SemanticCache:
    """
    Embedding-based semantic cache for LLM prompts.

    Instead of exact key matching, this cache computes a vector embedding
    of the prompt text and searches for similar prompts in the cache.
    If cosine similarity > threshold (default 0.95), the cached response
    is returned — saving an expensive LLM API call.

    Architecture:
    - Embeddings: sentence-transformers/all-MiniLM-L6-v2 (384-dim)
    - Storage: Redis hash per tenant → {embedding_hash: {embedding, response, metadata}}
    - Search: brute-force cosine similarity (fast enough for <10K cached items)
    - Isolation: per-tenant cache (no cross-tenant leakage)

    Expected cost reduction: 30-60% on repetitive workloads.
    """

    _model = None  # Lazy-loaded singleton

    DEFAULT_THRESHOLD = 0.95
    DEFAULT_TTL = 3600  # 1 hour
    EMBEDDING_DIM = 384

    @classmethod
    def _get_model(cls):
        """Lazy-load the sentence transformer model (heavy, ~90MB)."""
        if cls._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                cls._model = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("semantic_cache.model_loaded model=all-MiniLM-L6-v2")
            except ImportError:
                logger.warning(
                    "semantic_cache.model_unavailable "
                    "reason='sentence-transformers not installed'"
                )
                cls._model = False  # Mark as unavailable
            except Exception as e:
                logger.warning(f"semantic_cache.model_load_failed error={e}")
                cls._model = False
        return cls._model if cls._model is not False else None

    @classmethod
    def _compute_embedding(cls, text: str) -> Optional[list[float]]:
        """Compute the embedding vector for a text string."""
        model = cls._get_model()
        if model is None:
            return None
        try:
            embedding = model.encode(text, normalize_embeddings=True)
            return embedding.tolist()
        except Exception as e:
            logger.warning(f"semantic_cache.embedding_failed error={e}")
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two normalized vectors."""
        try:
            import numpy as np
            a_arr = np.array(a, dtype=np.float32)
            b_arr = np.array(b, dtype=np.float32)
            return float(np.dot(a_arr, b_arr))
        except ImportError:
            # Fallback: pure Python dot product (normalized vectors)
            return sum(x * y for x, y in zip(a, b))

    @staticmethod
    def _embedding_hash(embedding: list[float]) -> str:
        """Generate a stable hash key from an embedding vector."""
        raw = json.dumps(embedding[:8], sort_keys=True)  # first 8 dims for key
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _cache_key(self, tenant_id: str) -> str:
        """Redis key for a tenant's semantic cache index."""
        return f"sem:index:{tenant_id}"

    def _entry_key(self, tenant_id: str, entry_id: str) -> str:
        """Redis key for a specific cache entry."""
        return f"sem:entry:{tenant_id}:{entry_id}"

    async def lookup(
        self,
        prompt: str,
        tenant_id: str,
        model: str = "",
        threshold: float = DEFAULT_THRESHOLD,
    ) -> Optional[dict]:
        """
        Search for a semantically similar cached prompt.

        Args:
            prompt: The new prompt text.
            tenant_id: Tenant isolation scope.
            model: Optional model filter (only match same-model cache entries).
            threshold: Cosine similarity threshold (default 0.95).

        Returns:
            Cached response dict if a match is found, None otherwise.
            The dict includes: {response, model, similarity, cached_at, entry_id}
        """
        embedding = self._compute_embedding(prompt)
        if embedding is None:
            return None

        redis = get_cache_redis()
        try:
            # Load the index of all cached entries for this tenant
            index_key = self._cache_key(tenant_id)
            entries_raw = await redis.hgetall(index_key)

            if not entries_raw:
                return None

            best_match = None
            best_similarity = 0.0

            for entry_id, entry_json in entries_raw.items():
                try:
                    entry = json.loads(entry_json)
                    cached_embedding = entry.get("embedding", [])
                    cached_model = entry.get("model", "")

                    # Optional model filter
                    if model and cached_model and cached_model != model:
                        continue

                    similarity = self._cosine_similarity(embedding, cached_embedding)
                    if similarity > best_similarity and similarity >= threshold:
                        best_similarity = similarity
                        best_match = entry_id
                except (json.JSONDecodeError, KeyError):
                    continue

            if best_match is None:
                return None

            # Fetch the full cached response
            entry_key = self._entry_key(tenant_id, best_match)
            response_raw = await redis.get(entry_key)
            if response_raw is None:
                # Index is stale — clean up
                await redis.hdel(index_key, best_match)
                return None

            response_data = json.loads(response_raw)
            response_data["similarity"] = round(best_similarity, 4)
            response_data["entry_id"] = best_match

            logger.info(
                f"semantic_cache.hit tenant={tenant_id} "
                f"similarity={best_similarity:.4f} entry={best_match}"
            )
            return response_data

        except RedisError as e:
            logger.warning(f"semantic_cache.lookup_failed error={e}")
            return None

    async def store(
        self,
        prompt: str,
        response: Any,
        tenant_id: str,
        model: str = "",
        ttl: int = DEFAULT_TTL,
        metadata: Optional[dict] = None,
    ) -> bool:
        """
        Store a prompt-response pair in the semantic cache.

        Args:
            prompt: The prompt text.
            response: The LLM response to cache (any JSON-serializable value).
            tenant_id: Tenant isolation scope.
            model: The model that generated the response.
            ttl: Time-to-live in seconds (default 1 hour).
            metadata: Optional metadata (tokens, cost, etc).

        Returns:
            True on success, False on error.
        """
        embedding = self._compute_embedding(prompt)
        if embedding is None:
            return False

        entry_id = self._embedding_hash(embedding)
        redis = get_cache_redis()

        try:
            # Store the index entry (embedding + metadata for fast lookup)
            index_key = self._cache_key(tenant_id)
            index_entry = {
                "embedding": embedding,
                "model": model,
                "cached_at": time.time(),
            }
            await redis.hset(index_key, entry_id, json.dumps(index_entry))
            # Set TTL on the index (extends with each new entry)
            await redis.expire(index_key, ttl * 2)

            # Store the full response separately
            entry_key = self._entry_key(tenant_id, entry_id)
            response_data = {
                "response": response,
                "model": model,
                "prompt_preview": prompt[:200],
                "cached_at": time.time(),
                "metadata": metadata or {},
            }
            await redis.set(entry_key, json.dumps(response_data, default=str), ex=ttl)

            logger.debug(
                f"semantic_cache.stored tenant={tenant_id} "
                f"model={model} entry={entry_id} ttl={ttl}"
            )
            return True

        except (RedisError, TypeError) as e:
            logger.warning(f"semantic_cache.store_failed error={e}")
            return False

    async def invalidate_tenant(self, tenant_id: str) -> int:
        """Invalidate all semantic cache entries for a tenant."""
        redis = get_cache_redis()
        try:
            index_key = self._cache_key(tenant_id)
            entries = await redis.hgetall(index_key)
            count = 0
            for entry_id in entries:
                entry_key = self._entry_key(tenant_id, entry_id)
                await redis.delete(entry_key)
                count += 1
            await redis.delete(index_key)
            logger.info(f"semantic_cache.invalidated tenant={tenant_id} entries={count}")
            return count
        except RedisError as e:
            logger.warning(f"semantic_cache.invalidate_failed error={e}")
            return 0

    async def get_stats(self, tenant_id: str) -> dict:
        """Return cache statistics for a tenant."""
        redis = get_cache_redis()
        try:
            index_key = self._cache_key(tenant_id)
            count = await redis.hlen(index_key)
            return {
                "tenant_id": tenant_id,
                "cached_entries": count,
                "model_loaded": self._get_model() is not None,
            }
        except RedisError:
            return {
                "tenant_id": tenant_id,
                "cached_entries": 0,
                "model_loaded": False,
            }


# Singletons
response_cache = ResponseCache()
semantic_cache = SemanticCache()
