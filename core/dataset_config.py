"""
DatasetConfigLoader — loads externalized JSON configs and registers
them into the ConfigRegistry.

This is NOT a replacement for ConfigRegistry. It is a DATA SOURCE
that feeds into the registry at startup.

Loaded configs:
  - datasets/config/tenants.json  → tenant profiles, contracts, agents
  - datasets/config/models.json   → model catalog + pricing
  - datasets/config/quotas.json   → governance quotas per tenant

Usage:
    from core.dataset_config import load_dataset_configs, get_tenants, get_model_pricing
    load_dataset_configs()           # call once at startup
    tenants = get_tenants()          # cached in memory
    pricing = get_model_pricing("gpt-4")
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DATASETS_DIR = Path("datasets/config")

# ============================================================
# IN-MEMORY CACHES
#
# Populated once at startup by load_dataset_configs().
# Accessed via getter functions throughout the application.
# ============================================================

_tenant_cache: list[dict] = []
_model_cache: list[dict] = []
_quota_cache: list[dict] = []
_loaded: bool = False


# ============================================================
# STARTUP LOADER
# ============================================================

def load_dataset_configs() -> dict:
    """
    Load all JSON config files and register key values into ConfigRegistry.
    Called once at application startup from main.py lifespan.

    Returns summary dict of what was loaded.
    """
    global _loaded
    summary = {}

    # Load tenants
    tenants = _load_json("tenants.json")
    if tenants:
        _tenant_cache.clear()
        _tenant_cache.extend(tenants.get("tenants", []))
        summary["tenants"] = len(_tenant_cache)

        # Register into ConfigRegistry for runtime access
        try:
            from core.config_registry import config
            config.set("tenants.count", len(_tenant_cache), source="dataset_config")
            config.set(
                "tenants.ids",
                [t["tenant_id"] for t in _tenant_cache],
                source="dataset_config",
            )
        except Exception:
            pass  # ConfigRegistry may not be initialized yet

    # Load models
    models = _load_json("models.json")
    if models:
        _model_cache.clear()
        _model_cache.extend(models.get("models", []))
        summary["models"] = len(_model_cache)

    # Load quotas
    quotas = _load_json("quotas.json")
    if quotas:
        _quota_cache.clear()
        _quota_cache.extend(quotas.get("quotas", []))
        summary["quotas"] = len(_quota_cache)

    _loaded = True
    logger.info(f"dataset_config.loaded {summary}")
    return summary


def _ensure_loaded():
    """Lazy-load configs if not yet loaded."""
    if not _loaded:
        load_dataset_configs()


# ============================================================
# TENANT ACCESSORS
# ============================================================

def get_tenants() -> list[dict]:
    """Return all tenant definitions."""
    _ensure_loaded()
    return list(_tenant_cache)


def get_tenant(tenant_id: str) -> Optional[dict]:
    """Get a specific tenant by ID."""
    _ensure_loaded()
    for t in _tenant_cache:
        if t["tenant_id"] == tenant_id:
            return dict(t)
    return None


def get_tenant_ids() -> list[str]:
    """Return all tenant IDs."""
    _ensure_loaded()
    return [t["tenant_id"] for t in _tenant_cache]


# ============================================================
# MODEL ACCESSORS
# ============================================================

def get_models() -> list[dict]:
    """Return all model definitions."""
    _ensure_loaded()
    return list(_model_cache)


def get_model_pricing(model_name: str) -> Optional[dict]:
    """
    Get pricing for a specific model.

    Returns:
        {"input_price_per_1k": float, "output_price_per_1k": float}
        or None if model not found.
    """
    _ensure_loaded()
    for m in _model_cache:
        if m["model"] == model_name:
            return {
                "input_price_per_1k": m.get("input_price_per_1k", 0),
                "output_price_per_1k": m.get("output_price_per_1k", 0),
            }
    return None


def get_provider_for_model(model_name: str) -> str:
    """
    Derive provider from model name using models.json.
    Falls back to heuristic matching if model not in catalog.
    """
    _ensure_loaded()
    for m in _model_cache:
        if m["model"] == model_name:
            return m.get("provider", "unknown")

    # Fallback heuristic (supplements the catalog for unknown models)
    name = model_name.lower()
    if any(p in name for p in ("gpt", "o1", "o3", "davinci", "chatgpt")):
        return "openai"
    if "claude" in name:
        return "anthropic"
    if any(p in name for p in ("gemini", "palm", "bard")):
        return "google"
    if any(p in name for p in ("mistral", "mixtral", "codestral")):
        return "mistral"
    if any(p in name for p in ("llama", "meta-llama")):
        return "meta"
    if any(p in name for p in ("command", "coral")):
        return "cohere"
    return "unknown"


# ============================================================
# QUOTA ACCESSORS
# ============================================================

def get_quotas() -> list[dict]:
    """Return all quota definitions."""
    _ensure_loaded()
    return list(_quota_cache)


def get_quota_for_tenant(tenant_id: str) -> Optional[dict]:
    """Get quota config for a specific tenant."""
    _ensure_loaded()
    for q in _quota_cache:
        if q["tenant_id"] == tenant_id:
            return dict(q)
    return None


# ============================================================
# PRIVATE
# ============================================================

def _load_json(filename: str) -> Optional[dict]:
    """Load a JSON file from the datasets/config/ directory."""
    path = _DATASETS_DIR / filename
    if not path.exists():
        logger.warning(f"dataset_config.file_not_found path={path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"dataset_config.load_failed path={path} error={e}")
        return None
