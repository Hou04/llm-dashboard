"""
GatewayService — the core business logic for the LLM Gateway.

This service is the brain of Module 1. It coordinates:
- Governance evaluation (should this call be allowed?)
- Call logging (write the result to the database)
- Usage tracking (update Redis counters)
- Event publishing (notify other modules)

Design principles:
- No SQL. The service talks to repositories, not the database directly.
- No HTTP. The service knows nothing about requests or responses.
- No Redis keys. Redis operations go through core.redis functions.
- Every public method is async.
- Transactions are explicit: the service controls commit/rollback.
- Events are published AFTER commit — never before.
  (If the commit fails, the event should not fire.)
"""

import hashlib
import time
import uuid
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from core.redis import (
    get_quota_redis,
    increment_token_usage,
    increment_cost_usage,
    get_cached_rule,
    set_cached_rule,
    invalidate_tenant_rules,
)
from core.event_bus import publish
from core.config_registry import config
from modules.gateway.models import (
    LLMTokenLog,
    LLMGovernanceRule,
    LLMGovernanceDecision,
    CallStatus,
    GovernanceDecision,
    RuleType,
)
from modules.gateway.repositories import LogRepository, RuleRepository
from modules.gateway.services.smart_router import get_smart_router

logger = logging.getLogger(__name__)


# ============================================================
# DATA TRANSFER OBJECTS
#
# Typed containers for data flowing into and out of the service.
# These are NOT Pydantic models — they are internal only.
# ============================================================

@dataclass
class CallRequest:
    """
    Everything the service needs to process one LLM API call.

    The API handler builds this from the HTTP request and passes it
    to GatewayService.log_call(). The service never sees raw HTTP.
    """
    tenant_id: str
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: Decimal

    # Optional fields
    agent_id: Optional[str] = None
    user_id: Optional[str] = None
    site_id: Optional[str] = None
    module: Optional[str] = None
    pricing_profile: Optional[str] = None
    duration_ms: Optional[int] = None
    status: str = CallStatus.SUCCESS.value
    request_id: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Optional[dict] = field(default=None)
    prompt_text: Optional[str] = field(default=None)
    completion_text: Optional[str] = field(default=None)
    session_id: Optional[str] = field(default=None)
    prompt_name: Optional[str] = field(default=None)  # Added for A/B testing
    prompt_version: Optional[int] = field(default=None) # Added for A/B testing
    experiment_id: Optional[str] = field(default=None) # Added for A/B testing
    variant: Optional[str] = field(default=None)       # Added for A/B testing


@dataclass
class GovernanceResult:
    """
    The outcome of evaluating governance rules for a call request.

    Returned by GatewayService.evaluate_governance() and used
    internally by log_call() before writing anything to the database.
    """
    decision: str                        # allow / allow_downgrade / block
    model_to_use: str                    # may differ from requested if downgraded
    rule_id: Optional[uuid.UUID] = None  # which rule triggered this decision
    reason: Optional[str] = None        # human-readable explanation

    @property
    def is_allowed(self) -> bool:
        return self.decision in (
            GovernanceDecision.ALLOW.value,
            GovernanceDecision.ALLOW_DOWNGRADE.value,
        )

    @property
    def is_blocked(self) -> bool:
        return self.decision == GovernanceDecision.BLOCK.value

    @property
    def was_downgraded(self) -> bool:
        return self.decision == GovernanceDecision.ALLOW_DOWNGRADE.value


@dataclass
class CallResult:
    """
    The outcome of GatewayService.log_call().

    Returned to the API handler. The handler converts this to an
    HTTP response — the service never does that itself.
    """
    success: bool
    decision: str
    model_used: str

    log_id: Optional[uuid.UUID] = None   # UUID of created LLMTokenLog
    error: Optional[str] = None          # set if success=False
    was_downgraded: bool = False


# ============================================================
# TRACEABILITY
#
# Every call processed by M1 gets a gateway-generated trace_id.
# This ID follows the call through the entire M1→M10 chain,
# enabling end-to-end correlation in logs, events, and audits.
# ============================================================

def generate_trace_id(tenant_id: str, request_id: Optional[str] = None) -> str:
    """
    Generate a unique trace ID for end-to-end correlation.

    Format: tr-{timestamp_hex}-{tenant_hash[:8]}-{random_hex[:8]}
    Example: tr-19434a2b1f0-c1a2b3d4-e5f6a7b8

    The trace_id is:
    - Unique per call (uuid component)
    - Sortable by time (timestamp prefix)
    - Tenant-identifiable (hash component)
    """
    ts = hex(int(time.time() * 1000))[2:]
    tenant_hash = hashlib.sha256(tenant_id.encode()).hexdigest()[:8]
    rand = uuid.uuid4().hex[:8]
    return f"tr-{ts}-{tenant_hash}-{rand}"


# ============================================================
# RULE CACHE HELPERS
#
# Governance rules are read on every API call. Hitting the database
# on every call would be too slow. Rules are cached in Redis with
# a configurable TTL. The cache key is per-tenant.
#
# Cache structure: JSON list of rule dicts, keyed by tenant_id.
# ============================================================

RULE_CACHE_TTL = config.get("gateway.rule_cache_ttl", 300)


def _rule_to_dict(rule: LLMGovernanceRule) -> dict:
    """Serialize a rule to a JSON-safe dict for Redis caching."""
    return {
        "id": str(rule.id),
        "rule_type": rule.rule_type,
        "tenant_id": rule.tenant_id,
        "model_name": rule.model_name,
        "daily_token_limit": rule.daily_token_limit,
        "monthly_token_limit": rule.monthly_token_limit,
        "monthly_budget_usd": str(rule.monthly_budget_usd) if rule.monthly_budget_usd else None,
        "max_tokens_per_request": rule.max_tokens_per_request,
        "priority": rule.priority,
        "is_active": rule.is_active,
        "downgrade_to_model": rule.downgrade_to_model,
    }


def _dict_to_rule_proxy(data: dict) -> dict:
    """
    Convert a cached dict back to a dict we can evaluate against.

    We don't reconstruct full SQLAlchemy objects from cache —
    we evaluate the dict directly. This avoids session attachment issues.
    """
    return {
        "id": uuid.UUID(data["id"]) if data.get("id") else None,
        "rule_type": data.get("rule_type"),
        "tenant_id": data.get("tenant_id"),
        "model_name": data.get("model_name"),
        "daily_token_limit": data.get("daily_token_limit"),
        "monthly_token_limit": data.get("monthly_token_limit"),
        "monthly_budget_usd": (
            Decimal(data["monthly_budget_usd"])
            if data.get("monthly_budget_usd")
            else None
        ),
        "max_tokens_per_request": data.get("max_tokens_per_request"),
        "priority": data.get("priority", 100),
        "downgrade_to_model": data.get("downgrade_to_model"),
    }


# ============================================================
# THE SERVICE
# ============================================================

class GatewayService:
    """
    Core business logic for the LLM Gateway.

    Each instance is created per-request with the database session
    for that request. The session is injected — the service never
    creates its own sessions.

    Usage (in an API handler):
        async with get_db() as session:
            service = GatewayService(session)
            result = await service.log_call(request)
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.log_repo = LogRepository(session)
        self.rule_repo = RuleRepository(session)
        
        # New: Experiment support
        from modules.tracing.repositories.experiment_repository import ExperimentRepository
        self.experiment_repo = ExperimentRepository(session)
        
        self._security = None  # Lazy-loaded singleton

    @property
    def security(self):
        """Lazy-load SecurityService (heavy ML model init, singleton)."""
        if self._security is None and config.get("gateway.security_enabled", True):
            try:
                from modules.gateway.services.security_service import SecurityService
                self._security = SecurityService()
            except Exception as e:
                logger.warning(f"SecurityService unavailable: {e}")
                self._security = False  # Mark as failed, don't retry
        return self._security if self._security is not False else None

    # ================================================================
    # PUBLIC API
    # ================================================================

    async def log_call(self, request: CallRequest) -> CallResult:
        """
        Process one LLM API call through the complete gateway pipeline.

        This is the main entry point. It:
        1. Evaluates governance rules
        2. If blocked: records the decision and returns failure
        3. If allowed: writes the log entry and decision
        4. Updates Redis usage counters
        5. Commits the transaction
        6. Publishes events

        Events are published AFTER commit. If the commit fails,
        no events fire. This prevents other modules from reacting
        to calls that were never actually saved.

        Args:
            request: CallRequest with all call details.

        Returns:
            CallResult indicating success/failure and what was decided.
        """
        try:
            # Step 0a: Sanitize sensitive fields
            request = self._sanitize_request(request)

            # Step 0b: Generate trace_id for end-to-end correlation
            trace_id = generate_trace_id(request.tenant_id, request.request_id)

            # Step 0c: Semantic cache check (Feature 1)
            cache_hit = await self._check_semantic_cache(request)
            if cache_hit is not None:
                return cache_hit

            # Step 1: Evaluate governance & Experiments
            governance = await self.evaluate_governance(
                tenant_id=request.tenant_id,
                model=request.model,
                tokens_requested=request.total_tokens,
                prompt_name=request.prompt_name
            )

            # Step 2: Handle blocked calls
            if governance.is_blocked:
                await self._record_blocked_decision(request, governance)
                await self.session.commit()
                await self._publish_blocked_event(request, governance)

                # Emit llm.usage.blocked event
                await self._publish_event("llm.usage.blocked", {
                    "trace_id": trace_id,
                    "tenant_id": request.tenant_id,
                    "model": request.model,
                    "reason": governance.reason,
                })

                return CallResult(
                    success=False,
                    decision=governance.decision,
                    model_used=request.model,
                    error=governance.reason or "Call blocked by governance rule",
                )

            # Step 3: Determine actual model to use
            model_used = governance.model_to_use
            
            log_id_val = uuid.uuid4()
            now_dt = datetime.now(timezone.utc)

            # Step 4: Prepare data dictionaries for celery
            log_data = {
                "id": str(log_id_val),
                "trace_id": trace_id,
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "agent_id": request.agent_id,
                "user_id": request.user_id,
                "site_id": request.site_id,
                "module": request.module,
                "pricing_profile": request.pricing_profile,
                "model": model_used,
                "provider": request.provider,
                "input_tokens": request.input_tokens,
                "output_tokens": request.output_tokens,
                "total_tokens": request.total_tokens,
                "cost_usd": str(request.cost_usd), # Convert Decimal to string globally
                "duration_ms": request.duration_ms,
                "status": request.status,
                "error_message": request.error_message,
                "prompt_text": request.prompt_text,
                "completion_text": request.completion_text,
                "metadata_": {
                    **(request.metadata or {}),
                    "experiment_id": request.experiment_id,
                    "variant": request.variant,
                    "prompt_name": request.prompt_name,
                    "prompt_version": request.prompt_version,
                    "session_id": request.session_id
                },
                "created_at": now_dt.isoformat(),
            }
            
            decision_data = {
                "id": str(uuid.uuid4()),
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "model_requested": request.model,
                "model_used": model_used,
                "decision": governance.decision,
                "reason": governance.reason,
                "rule_id": str(governance.rule_id) if governance.rule_id else None,
                "created_at": now_dt.isoformat(),
            }

            # Step 5 & 6: Offload DB Insert Payload to Celery (Async Queue)
            # This completely removes PostgreSQL latency from the Gateway fast path
            # Direct DB Insert (Bypassing Celery for Demo stability)
            from modules.gateway.tasks import _persist_log_async
            await _persist_log_async(log_data, decision_data)

            # Step 7: Update Redis usage counters (after commit — best effort)
            await self._update_usage_counters(request)

            from collections import namedtuple
            LogRecord = namedtuple('LogRecord', ['id', 'model', 'provider', 'created_at'])
            saved_log = LogRecord(log_id_val, model_used, request.provider, now_dt)

            # Step 8: Publish success event
            await self._publish_call_logged_event(request, saved_log, governance)

            # Step 9: Store in semantic cache for future dedup (best-effort)
            await self._store_semantic_cache(request)

            return CallResult(
                success=True,
                decision=governance.decision,
                model_used=model_used,
                log_id=log_id_val,
                was_downgraded=governance.was_downgraded,
            )

        except Exception as exc:
            await self.session.rollback()
            logger.error(
                "GatewayService.log_call failed",
                extra={
                    "tenant_id": request.tenant_id,
                    "model": request.model,
                    "error": str(exc),
                },
            )
            return CallResult(
                success=False,
                decision=GovernanceDecision.ALLOW.value,
                model_used=request.model,
                error=f"Internal error: {exc}",
            )

    async def evaluate_governance(
        self,
        tenant_id: str,
        model: str,
        tokens_requested: int,
        prompt_name: Optional[str] = None,
    ) -> GovernanceResult:
        """
        Evaluate governance rules AND A/B Experiments.
        """
        # --- FEATURE 9: A/B TESTING INTEGRATION ---
        if prompt_name:
            exp = await self.experiment_repo.get_active_experiment_for_prompt(tenant_id, prompt_name)
            if exp:
                import random
                variant = "B" if random.random() < exp.traffic_split else "A"
                version = exp.variant_a_version if variant == "A" else exp.variant_b_version
                
                return GovernanceResult(
                    decision=f"ab_test_{variant}",
                    model_to_use=model,
                    reason=f"A/B Experiment '{exp.name}' active. Routing to Variant {variant} (v{version})",
                    rule_id=exp.id
                )

        rules = await self._load_rules_for_tenant(tenant_id)

        if not rules:
            return GovernanceResult(
                decision=GovernanceDecision.ALLOW.value,
                model_to_use=model,
                reason="No governance rules configured",
            )

        # Load current usage from Redis for limit checks
        usage = await self._get_current_usage(tenant_id)

        for rule in rules:
            result = self._apply_rule(rule, model, tokens_requested, usage)
            if result is not None:
                return result

        # No rule triggered — try ML smart routing for cost optimization
        router = get_smart_router()
        if router.is_ready:
            cheaper_model = router.route(
                model_requested=model,
                input_tokens=tokens_requested,
                output_tokens=tokens_requested // 2,  # estimate
            )
            if cheaper_model and cheaper_model != model:
                return GovernanceResult(
                    decision=GovernanceDecision.ALLOW_DOWNGRADE.value,
                    model_to_use=cheaper_model,
                    reason=(
                        f"Smart Router ML prediction: '{cheaper_model}' can handle "
                        f"this request with sufficient quality (cost optimization)"
                    ),
                )

        # Default allow with requested model
        return GovernanceResult(
            decision=GovernanceDecision.ALLOW.value,
            model_to_use=model,
            reason="No matching rule \u2014 default allow",
        )

    async def get_tenant_usage(
        self,
        tenant_id: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict:
        """
        Get usage summary for a tenant over a time period.

        Fast path: current day/month counters from Redis.
        For historical queries: falls back to database aggregation.

        Args:
            tenant_id: The tenant to query.
            from_dt: Start of period (UTC).
            to_dt: End of period (UTC).

        Returns:
            Usage summary dict from LogRepository.get_usage_summary().
        """
        return await self.log_repo.get_usage_summary(
            tenant_id=tenant_id,
            from_dt=from_dt,
            to_dt=to_dt,
        )

    # ================================================================
    # PUBLIC — FALLBACK RESOLUTION
    # ================================================================

    async def resolve_fallback(
        self,
        tenant_id: str,
        original_model: str,
        error_status_code: int,
        error_message: str = "",
    ) -> Optional[GovernanceResult]:
        """
        Resolve a fallback model when the primary provider fails.

        Called by the API proxy when a provider returns a retryable error
        (429, 500, 503, timeout). This method:

        1. Checks if the error is retryable
        2. Loads the fallback chain for the model
        3. Filters out unhealthy providers (circuit breaker)
        4. Returns the first viable fallback model
        5. Logs the fallback decision in governance audit

        Args:
            tenant_id: The tenant making the call.
            original_model: The model that failed.
            error_status_code: HTTP status code from the provider.
            error_message: Error details from the provider.

        Returns:
            GovernanceResult with allow_fallback decision and the fallback model,
            or None if no fallback is available.
        """
        from modules.gateway.services.mode_router import (
            GatewayModeRouter,
            ProviderHealthTracker,
        )

        # 1. Check if error is retryable
        if not GatewayModeRouter.is_retryable_error(error_status_code):
            logger.debug(
                f"fallback.not_retryable model={original_model} "
                f"status={error_status_code}"
            )
            return None

        # Record the failure for circuit breaker
        original_provider = GatewayModeRouter.get_provider_for_model(original_model)
        ProviderHealthTracker.record_failure(original_provider)

        # 2. Get the fallback chain
        fallback_chain = GatewayModeRouter.get_fallback_chain(
            original_model, tenant_id
        )

        if not fallback_chain:
            logger.warning(
                f"fallback.no_chain model={original_model} tenant={tenant_id}"
            )
            return None

        # 3. Filter out unhealthy providers
        viable_fallbacks = []
        for fb_model in fallback_chain:
            fb_provider = GatewayModeRouter.get_provider_for_model(fb_model)
            if ProviderHealthTracker.is_healthy(fb_provider):
                viable_fallbacks.append(fb_model)
            else:
                logger.debug(
                    f"fallback.skip_unhealthy model={fb_model} "
                    f"provider={fb_provider}"
                )

        if not viable_fallbacks:
            logger.warning(
                f"fallback.all_unhealthy model={original_model} "
                f"chain={fallback_chain}"
            )
            return None

        # 4. Return the first viable fallback
        fallback_model = viable_fallbacks[0]
        fallback_provider = GatewayModeRouter.get_provider_for_model(fallback_model)

        logger.info(
            f"fallback.resolved original={original_model}({original_provider}) "
            f"→ {fallback_model}({fallback_provider}) "
            f"reason=status_{error_status_code} tenant={tenant_id}"
        )

        # 5. Log the fallback decision in governance audit
        decision = LLMGovernanceDecision(
            request_id=None,
            tenant_id=tenant_id,
            model_requested=original_model,
            model_used=fallback_model,
            decision=GovernanceDecision.ALLOW_FALLBACK.value,
            reason=(
                f"Auto-fallback: {original_model} returned {error_status_code} "
                f"({error_message[:200]}). Retrying with {fallback_model}."
            ),
        )
        self.session.add(decision)
        await self.session.flush()

        # Publish fallback event for observability
        await self._publish_event("llm.usage.fallback", {
            "tenant_id": tenant_id,
            "original_model": original_model,
            "original_provider": original_provider,
            "fallback_model": fallback_model,
            "fallback_provider": fallback_provider,
            "error_status_code": error_status_code,
            "error_message": error_message[:200],
        })

        return GovernanceResult(
            decision=GovernanceDecision.ALLOW_FALLBACK.value,
            model_to_use=fallback_model,
            reason=(
                f"Fallback from {original_model} to {fallback_model} "
                f"due to provider error ({error_status_code})"
            ),
        )

    async def get_fallback_chain_info(
        self,
        model: str,
        tenant_id: Optional[str] = None,
    ) -> dict:
        """
        Return the fallback chain and provider health for a model.
        Used by the dashboard to show fallback configuration.
        """
        from modules.gateway.services.mode_router import (
            GatewayModeRouter,
            ProviderHealthTracker,
        )

        chain = GatewayModeRouter.get_fallback_chain(model, tenant_id)
        chain_info = []
        for fb_model in chain:
            provider = GatewayModeRouter.get_provider_for_model(fb_model)
            chain_info.append({
                "model": fb_model,
                "provider": provider,
                "healthy": ProviderHealthTracker.is_healthy(provider),
            })

        return {
            "primary_model": model,
            "primary_provider": GatewayModeRouter.get_provider_for_model(model),
            "fallback_chain": chain_info,
            "provider_health": ProviderHealthTracker.get_status(),
        }

    # ================================================================
    # PRIVATE — GOVERNANCE
    # ================================================================

    async def _load_rules_for_tenant(self, tenant_id: str) -> list[dict]:
        """
        Load governance rules for a tenant, using Redis cache.

        Cache key: f"rules:{tenant_id}"
        Cache TTL: 5 minutes

        Returns list of rule dicts (not SQLAlchemy objects — those
        are tied to a session and can't be cached across requests).
        """
        cache_key = f"rules:{tenant_id}"

        # Try cache first
        cached = await get_cached_rule(cache_key)
        if cached is not None:
            rules_data = cached.get("rules", [])
            return [_dict_to_rule_proxy(r) for r in rules_data]

        # Cache miss — load from database
        db_rules = await self.rule_repo.get_active_rules_for_tenant(tenant_id)
        rule_dicts = [_rule_to_dict(r) for r in db_rules]

        # Populate cache
        await set_cached_rule(cache_key, {"rules": rule_dicts}, ttl=RULE_CACHE_TTL)

        return [_dict_to_rule_proxy(r) for r in rule_dicts]

    async def _get_current_usage(self, tenant_id: str) -> dict:
        """
        Get current token and cost usage for a tenant from Redis.

        Returns dict with: daily_tokens, monthly_tokens,
                           daily_cost_usd, monthly_cost_usd
        All values are 0 if no usage recorded yet.
        """
        redis = get_quota_redis()
        try:
            from core.redis import get_current_usage
            return await get_current_usage(tenant_id)
        finally:
            await redis.aclose()

    def _apply_rule(
        self,
        rule: dict,
        model: str,
        tokens_requested: int,
        usage: dict,
    ) -> Optional[GovernanceResult]:
        """
        Apply a single rule to a call request.

        Returns a GovernanceResult if the rule triggers, None if it doesn't.
        None means "this rule doesn't apply to this call — try the next one."

        This method is synchronous — all the async work (loading rules,
        loading usage) is done before we call this.
        """
        rule_type = rule.get("rule_type")
        rule_model = rule.get("model_name")  # None means applies to all models
        rule_id = rule.get("id")

        # Check if this rule applies to the requested model
        model_matches = (rule_model is None) or (rule_model == model)

        if rule_type == RuleType.MODEL_BLOCK.value:
            if model_matches:
                return GovernanceResult(
                    decision=GovernanceDecision.BLOCK.value,
                    model_to_use=model,
                    rule_id=rule_id,
                    reason=f"Model '{model}' is blocked by governance rule",
                )

        elif rule_type == RuleType.RATE_LIMIT.value:
            max_per_request = rule.get("max_tokens_per_request")
            if max_per_request and tokens_requested > max_per_request:
                return GovernanceResult(
                    decision=GovernanceDecision.BLOCK.value,
                    model_to_use=model,
                    rule_id=rule_id,
                    reason=(
                        f"Request tokens ({tokens_requested}) exceed "
                        f"per-request limit ({max_per_request})"
                    ),
                )

        elif rule_type == RuleType.TENANT_LIMIT.value:
            daily_limit = rule.get("daily_token_limit")
            if daily_limit:
                daily_used = int(usage.get("daily_tokens", 0))
                if daily_used + tokens_requested > daily_limit:
                    return GovernanceResult(
                        decision=GovernanceDecision.BLOCK.value,
                        model_to_use=model,
                        rule_id=rule_id,
                        reason=(
                            f"Daily token limit ({daily_limit:,}) would be exceeded. "
                            f"Used: {daily_used:,}, Requested: {tokens_requested:,}"
                        ),
                    )

        elif rule_type == RuleType.BUDGET_CAP.value:
            monthly_budget = rule.get("monthly_budget_usd")
            if monthly_budget:
                monthly_cost = Decimal(str(usage.get("monthly_cost_usd", "0")))
                if monthly_cost >= monthly_budget:
                    return GovernanceResult(
                        decision=GovernanceDecision.BLOCK.value,
                        model_to_use=model,
                        rule_id=rule_id,
                        reason=(
                            f"Monthly budget (${monthly_budget}) reached. "
                            f"Current spend: ${monthly_cost}"
                        ),
                    )

        elif rule_type == RuleType.MODEL_DOWNGRADE.value:
            if model_matches:
                downgrade_to = rule.get("downgrade_to_model")
                if downgrade_to:
                    return GovernanceResult(
                        decision=GovernanceDecision.ALLOW_DOWNGRADE.value,
                        model_to_use=downgrade_to,
                        rule_id=rule_id,
                        reason=(
                            f"Model '{model}' downgraded to '{downgrade_to}' "
                            f"by governance rule"
                        ),
                    )

        return None  # Rule didn't trigger — try next rule

    # ================================================================
    # PRIVATE — DATABASE WRITES
    # ================================================================

    async def _record_blocked_decision(
        self,
        request: CallRequest,
        governance: GovernanceResult,
    ) -> None:
        """Write a governance decision record for a blocked call."""
        decision = LLMGovernanceDecision(
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            model_requested=request.model,
            model_used=None,
            decision=GovernanceDecision.BLOCK.value,
            reason=governance.reason,
            rule_id=governance.rule_id,
        )
        self.session.add(decision)
        await self.session.flush()

    # ================================================================
    # PRIVATE — SEMANTIC CACHE
    # ================================================================

    async def _check_semantic_cache(self, request: CallRequest) -> Optional[CallResult]:
        """
        Check the semantic cache for a similar prompt.

        If a match is found with cosine similarity >= 0.95, return the
        cached response without calling the LLM provider.

        Returns a CallResult if cache hit, None if cache miss.
        """
        if not request.prompt_text:
            return None  # No prompt text to cache against

        try:
            from core.response_cache import semantic_cache

            cached = await semantic_cache.lookup(
                prompt=request.prompt_text,
                tenant_id=request.tenant_id,
                model=request.model,
            )

            if cached is not None:
                logger.info(
                    f"semantic_cache.hit tenant={request.tenant_id} "
                    f"model={request.model} similarity={cached.get('similarity', 0)}"
                )
                return CallResult(
                    success=True,
                    decision=GovernanceDecision.ALLOW.value,
                    model_used=cached.get("model", request.model),
                    log_id=None,  # No log entry — served from cache
                    was_downgraded=False,
                    error=None,
                )
        except Exception as exc:
            logger.debug(f"semantic_cache.check_failed error={exc}")

        return None

    async def _store_semantic_cache(self, request: CallRequest) -> None:
        """
        Store a prompt-response pair in the semantic cache after a successful call.
        """
        if not request.prompt_text or not request.completion_text:
            return

        try:
            from core.response_cache import semantic_cache

            await semantic_cache.store(
                prompt=request.prompt_text,
                response=request.completion_text,
                tenant_id=request.tenant_id,
                model=request.model,
                metadata={
                    "input_tokens": request.input_tokens,
                    "output_tokens": request.output_tokens,
                    "cost_usd": str(request.cost_usd),
                },
            )
        except Exception as exc:
            logger.debug(f"semantic_cache.store_failed error={exc}")

    # ================================================================
    # PRIVATE — REDIS
    # ================================================================

    async def _update_usage_counters(self, request: CallRequest) -> None:
        """
        Update Redis token and cost counters for a tenant.

        Called AFTER database commit — if this fails, it's logged
        but doesn't fail the request. The database is the source of
        truth; Redis counters are a fast approximation.
        """
        try:
            await increment_token_usage(request.tenant_id, request.total_tokens)
            await increment_cost_usage(request.tenant_id, float(request.cost_usd))
        except Exception as exc:
            logger.warning(
                "Failed to update Redis usage counters",
                extra={"tenant_id": request.tenant_id, "error": str(exc)},
            )

    # ================================================================
    # PRIVATE — EVENTS
    # ================================================================

    async def _publish_call_logged_event(
        self,
        request: CallRequest,
        log: LLMTokenLog,
        governance: GovernanceResult,
    ) -> None:
        """
        Publish a call.logged event after a successful call is committed.

        Other modules subscribe to this event:
        - Module 2 (Analytics): updates cost aggregates
        - Module 3 (Detection): checks for anomalies
        - Module 4 (Forecasting): updates forecast models

        The event payload contains everything downstream modules need.
        They should NOT query the database for more data — everything
        they need is in the event.
        """
        try:
            await publish("call.logged", {
                "log_id": str(log.id),
                "trace_id": getattr(log, 'trace_id', None) or '',
                "tenant_id": request.tenant_id,
                "agent_id": request.agent_id,
                "model": log.model,
                "provider": log.provider,
                "input_tokens": request.input_tokens,
                "output_tokens": request.output_tokens,
                "total_tokens": request.total_tokens,
                "cost_usd": str(request.cost_usd),
                "duration_ms": request.duration_ms,
                "status": request.status,
                "was_downgraded": governance.was_downgraded,
                "rule_id": str(governance.rule_id) if governance.rule_id else None,
                "timestamp": log.created_at.isoformat(),
            })

            # Also emit the typed event for event-driven subscribers
            await self._publish_event("llm.usage.logged", {
                "trace_id": getattr(log, 'trace_id', None) or '',
                "log_id": str(log.id),
                "tenant_id": request.tenant_id,
                "model": log.model,
                "tokens": request.total_tokens,
            })
        except Exception as exc:
            logger.warning(
                "Failed to publish call.logged event",
                extra={"tenant_id": request.tenant_id, "error": str(exc)},
            )

    async def _publish_blocked_event(
        self,
        request: CallRequest,
        governance: GovernanceResult,
    ) -> None:
        """Publish a call.blocked event when governance blocks a call."""
        try:
            await publish("call.blocked", {
                "tenant_id": request.tenant_id,
                "agent_id": request.agent_id,
                "model_requested": request.model,
                "reason": governance.reason,
                "rule_id": str(governance.rule_id) if governance.rule_id else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as exc:
            logger.warning(
                "Failed to publish call.blocked event",
                extra={"tenant_id": request.tenant_id, "error": str(exc)},
            )

    # ================================================================
    # PRIVATE — SECURITY (Issue #6 Fix)
    # ================================================================

    def _sanitize_request(self, request: CallRequest) -> CallRequest:
        """
        Sanitize sensitive fields in the request before storage.

        Actions:
        1. Anonymize PII in error_message (emails, phone numbers, names)
        2. Strip sensitive keys from metadata
        3. Flag prompt injection attempts in metadata

        This wires the existing SecurityService (previously dead code)
        into the M1 hot path.
        """
        if not self.security:
            return request  # Security service unavailable; pass through

        # 1. Anonymize PII in error_message (may contain user data)
        if request.error_message:
            try:
                result = self.security.anonymize_text(request.error_message)
                request.error_message = result["text"]
            except Exception:
                pass  # Never let sanitization break the pipeline

        # 2. Strip sensitive keys from metadata
        if request.metadata:
            sensitive_keys = {
                "password", "token", "api_key", "secret",
                "authorization", "credential", "private_key",
            }
            request.metadata = {
                k: v for k, v in request.metadata.items()
                if k.lower() not in sensitive_keys
            }

            # 3. Check prompt field for PII and injection
            prompt_text = request.metadata.get("prompt", "")
            if prompt_text and isinstance(prompt_text, str):
                try:
                    anon = self.security.anonymize_text(prompt_text)
                    request.metadata["prompt"] = anon["text"]
                    request.metadata["_pii_detected"] = len(anon["items"]) > 0

                    injection = self.security.detect_prompt_injection(prompt_text)
                    if injection["is_injection"]:
                        request.metadata["_injection_flagged"] = True
                        request.metadata["_injection_score"] = injection["score"]
                except Exception:
                    pass  # Best-effort sanitization

        return request

    # ================================================================
    # PRIVATE — EVENT HELPERS
    # ================================================================

    async def _publish_event(self, event_type: str, data: dict) -> None:
        """
        Publish a typed domain event (llm.usage.logged, llm.usage.blocked, etc.).
        Failures are logged but never propagate.
        """
        try:
            await publish(event_type, data)
        except Exception as exc:
            logger.warning(
                f"Failed to publish {event_type} event",
                extra={"error": str(exc)},
            )

    # ================================================================
    # PUBLIC — BATCH PROCESSING
    # ================================================================

    async def process_batch(
        self,
        rows: list[dict],
        source: str = "csv_import",
    ) -> dict:
        """
        Process a batch of historical records through M1 traceability.

        Used by CSV ingestion to ensure all data flows through the gateway
        with proper trace_id tagging. Unlike log_call(), this does NOT
        block rows — historical data has already happened. It tags them
        with trace_ids for correlation.

        Args:
            rows: List of row dicts to process.
            source: Origin label (e.g., 'csv_import', 'api_backfill').

        Returns:
            Summary dict with processed count.
        """
        processed = 0
        for row in rows:
            trace_id = generate_trace_id(row.get("tenant_id", "unknown"))
            row["trace_id"] = trace_id
            row["metadata_"] = {
                **(row.get("metadata_") or {}),
                "_source": source,
                "_trace_id": trace_id,
            }
            processed += 1
        return {"processed": processed, "source": source}

    async def log_stream_chunk(self, request_id: str, tenant_id: str, delta_tokens: int) -> int:
        """
        Record a stream chunk of tokens to Redis and return the current total.
        When streaming generation is complete, the final usage is reported 
        via log_call using this accumulated total.
        """
        redis = get_quota_redis()
        try:
            key = f"stream:{tenant_id}:{request_id}:tokens"
            # Increment the tokens
            total = await redis.incrby(key, delta_tokens)
            # Set a 24-hour expiry to prevent abandoned streams from filling Redis
            await redis.expire(key, 86400)
            return total
        except Exception as exc:
            logger.error(
                "Failed to update streaming token metrics in Redis",
                extra={"tenant_id": tenant_id, "request_id": request_id, "error": str(exc)},
            )
            return delta_tokens
        finally:
            await redis.aclose()