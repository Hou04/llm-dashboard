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
"""

import logging
from core.config_registry import config

logger = logging.getLogger(__name__)


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
