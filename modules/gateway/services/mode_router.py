"""
Gateway Mode Router — routes data flow based on operating mode.

Three modes control what types of data ingestion M1 accepts:

  simulation: CSV datasets → M1 validation → DB
              (default for development — no real LLM API calls)

  real:       API calls → M1 governance → LLM provider → DB
              (production mode — only accepts real-time API calls)

  hybrid:     Both paths active simultaneously
              (testing mode — allows mixing simulated and real data)

The mode is controlled by:
  1. GATEWAY_MODE in .env (startup default)
  2. config.set("gateway.mode", ...) at runtime via API

Fallback chains define the retry order when a provider fails.
Each model maps to a list of fallback models from different providers.
"""

import logging
import time
from typing import Optional
from core.config_registry import config

logger = logging.getLogger(__name__)


# ============================================================
# DEFAULT FALLBACK CHAINS
#
# If OpenAI returns 503, try Anthropic, then Groq.
# These can be overridden per-tenant via config registry.
# ============================================================

DEFAULT_FALLBACK_CHAINS: dict[str, list[str]] = {
    "gpt-4o":           ["claude-3-5-sonnet-20241022", "llama-3.3-70b-versatile"],
    "gpt-4o-mini":      ["claude-3-haiku-20240307", "llama-3.3-70b-versatile"],
    "claude-3-5-sonnet-20241022": ["gpt-4o", "llama-3.3-70b-versatile"],
    "claude-3-haiku-20240307":    ["gpt-4o-mini", "llama-3.3-70b-versatile"],
    "llama-3.3-70b-versatile":    ["gpt-4o-mini", "claude-3-haiku-20240307"],
}

# Model → provider mapping for automatic provider resolution
MODEL_TO_PROVIDER: dict[str, str] = {
    "gpt-4o":                     "openai",
    "gpt-4o-mini":                "openai",
    "gpt-4-turbo":                "openai",
    "gpt-3.5-turbo":              "openai",
    "claude-3-5-sonnet-20241022": "anthropic",
    "claude-3-haiku-20240307":    "anthropic",
    "claude-3-opus-20240229":     "anthropic",
    "llama-3.3-70b-versatile":    "groq",
    "llama-3.1-8b-instant":       "groq",
    "mixtral-8x7b-32768":         "groq",
    "gemini-1.5-pro":             "google",
    "gemini-1.5-flash":           "google",
}

# Retryable HTTP status codes from providers
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# Maximum retries before giving up
MAX_FALLBACK_RETRIES = 3


class GatewayModeRouter:
    """Routes incoming data through the appropriate M1 pipeline based on mode."""

    @staticmethod
    def get_mode() -> str:
        """Return current gateway operating mode."""
        return config.get("gateway.mode", "simulation")

    @staticmethod
    def is_simulation_enabled() -> bool:
        """True if CSV/dataset ingestion is allowed."""
        mode = GatewayModeRouter.get_mode()
        return mode in ("simulation", "hybrid")

    @staticmethod
    def is_real_enabled() -> bool:
        """True if real-time API calls are allowed."""
        mode = GatewayModeRouter.get_mode()
        return mode in ("real", "hybrid")

    @staticmethod
    def validate_request_source(source: str) -> bool:
        """
        Validate if a request source is allowed in the current mode.

        Args:
            source: 'api' for real-time calls, 'csv' for simulation imports

        Returns:
            True if allowed, False if rejected.
        """
        mode = GatewayModeRouter.get_mode()

        if source == "api" and mode == "simulation":
            logger.warning(
                "gateway.mode_rejected source=api mode=simulation "
                "reason='Real-time API calls disabled in simulation mode'"
            )
            return False

        if source == "csv" and mode == "real":
            logger.warning(
                "gateway.mode_rejected source=csv mode=real "
                "reason='CSV imports disabled in real mode'"
            )
            return False

        return True

    @staticmethod
    def get_status() -> dict:
        """Return current mode status for API responses."""
        mode = GatewayModeRouter.get_mode()
        return {
            "mode": mode,
            "simulation_enabled": mode in ("simulation", "hybrid"),
            "real_enabled": mode in ("real", "hybrid"),
        }

    # ================================================================
    # FALLBACK CHAIN MANAGEMENT
    # ================================================================

    @staticmethod
    def get_fallback_chain(
        model: str,
        tenant_id: Optional[str] = None,
    ) -> list[str]:
        """
        Get the fallback chain for a model, optionally per-tenant.

        Lookup order:
        1. Tenant-specific override: config "gateway.fallback.{tenant_id}.{model}"
        2. Global override: config "gateway.fallback.{model}"
        3. Default chains defined above

        Returns a list of fallback model names (may be empty).
        """
        # 1. Tenant-specific
        if tenant_id:
            tenant_chain = config.get(f"gateway.fallback.{tenant_id}.{model}")
            if tenant_chain:
                return tenant_chain if isinstance(tenant_chain, list) else [tenant_chain]

        # 2. Global override
        global_chain = config.get(f"gateway.fallback.{model}")
        if global_chain:
            return global_chain if isinstance(global_chain, list) else [global_chain]

        # 3. Default
        return list(DEFAULT_FALLBACK_CHAINS.get(model, []))

    @staticmethod
    def get_provider_for_model(model: str) -> str:
        """Resolve the provider name for a given model."""
        return MODEL_TO_PROVIDER.get(model, "unknown")

    @staticmethod
    def is_retryable_error(status_code: int) -> bool:
        """Check if an HTTP status code is retryable (provider transient error)."""
        return status_code in RETRYABLE_STATUS_CODES


class ProviderHealthTracker:
    """
    Tracks provider health to skip known-unhealthy providers in fallback chains.

    Uses a simple circuit-breaker pattern:
    - After N consecutive failures, mark provider as unhealthy for M seconds
    - During cooldown, skip this provider in fallback chains
    - After cooldown, try again (half-open state)
    """

    _failures: dict[str, int] = {}
    _cooldown_until: dict[str, float] = {}

    FAILURE_THRESHOLD = 3          # Mark unhealthy after 3 consecutive failures
    COOLDOWN_SECONDS = 60          # Skip unhealthy providers for 60 seconds

    @classmethod
    def record_failure(cls, provider: str) -> None:
        """Record a provider failure."""
        cls._failures[provider] = cls._failures.get(provider, 0) + 1
        if cls._failures[provider] >= cls.FAILURE_THRESHOLD:
            cls._cooldown_until[provider] = time.time() + cls.COOLDOWN_SECONDS
            logger.warning(
                f"provider.circuit_breaker.open provider={provider} "
                f"failures={cls._failures[provider]} "
                f"cooldown_seconds={cls.COOLDOWN_SECONDS}"
            )

    @classmethod
    def record_success(cls, provider: str) -> None:
        """Reset failure count on success."""
        cls._failures.pop(provider, None)
        cls._cooldown_until.pop(provider, None)

    @classmethod
    def is_healthy(cls, provider: str) -> bool:
        """Check if a provider is currently healthy (not in cooldown)."""
        cooldown = cls._cooldown_until.get(provider)
        if cooldown is None:
            return True
        if time.time() > cooldown:
            # Cooldown expired — half-open state, allow retry
            cls._cooldown_until.pop(provider, None)
            cls._failures.pop(provider, None)
            return True
        return False

    @classmethod
    def get_status(cls) -> dict:
        """Return current health status of all tracked providers."""
        now = time.time()
        return {
            provider: {
                "failures": cls._failures.get(provider, 0),
                "healthy": cls.is_healthy(provider),
                "cooldown_remaining": max(0, cls._cooldown_until.get(provider, 0) - now),
            }
            for provider in set(list(cls._failures.keys()) + list(cls._cooldown_until.keys()))
        }
