"""
Model Catalog — exposes LiteLLM model_cost data as a browsable API.

GET /v1/models/catalog  — all models with pricing, capabilities, status
"""

import logging
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timezone, timedelta

from core.database import get_db
import litellm
from modules.auth.dependencies import require_tenant_viewer
from modules.auth.schemas import CurrentUser

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/models", tags=["Model Catalog"])

@router.get(
    "/catalog",
    summary="Model Catalog — browsable registry with pricing and health",
)
async def get_model_catalog(
    provider: Optional[str] = Query(None, description="Filter by provider"),
    category: Optional[str] = Query(None, description="Filter: flagship|efficient|open-source"),
    capability: Optional[str] = Query(None, description="Filter by capability"),
    session: AsyncSession = Depends(get_db),
    _user: CurrentUser = Depends(require_tenant_viewer),
) -> dict:
    """
    Returns all models with pricing, capabilities, and real-time health
    status derived from recent log error rates.
    """
    from modules.gateway.models import LLMTokenLog

    # Get models from LiteLLM model_cost registry
    raw_registry = litellm.model_cost
    
    models = []
    # Curated list of high-value providers for the TIM demo
    target_providers = ["openai", "anthropic", "google", "groq", "mistral"]
    
    # List of "noisy" prefixes to exclude (like legacy azure models)
    exclude_prefixes = ["azure/", "ft:", "davinci", "babbage", "curie", "ada"]
    
    for model_id, info in raw_registry.items():
        # 1. Check if model ID starts with an excluded prefix
        if any(model_id.lower().startswith(p) for p in exclude_prefixes):
            continue

        provider_name = info.get("model_info", {}).get("provider", "").lower()
        if not provider_name:
            if ":" in model_id: provider_name = model_id.split(":")[0]
            elif "/" in model_id: provider_name = model_id.split("/")[0]
            else: provider_name = "unknown"

        # 2. Strict provider check
        if provider_name not in target_providers:
            continue
        
        # 3. Only keep modern models (exclude versions from 2022/2023 for cleaner list)
        if "-0613" in model_id or "-0314" in model_id:
            continue

        # Determine category based on cost/name
        input_cost = info.get("input_cost_per_token", 0)
        cat = "efficient"
        if input_cost > 0.000002: cat = "flagship"
        if provider_name in ["groq", "mistral"] or "llama" in model_id.lower(): cat = "open-source"

        caps = ["chat"]
        if "vision" in model_id.lower() or info.get("model_info", {}).get("supports_vision"): caps.append("vision")
        if info.get("model_info", {}).get("supports_function_calling"): caps.append("function_calling")
        
        models.append({
            "model": model_id,
            "provider": provider_name,
            "input_cost_per_token": input_cost,
            "output_cost_per_token": info.get("output_cost_per_token", 0),
            "max_tokens": info.get("max_tokens", 4096),
            "capabilities": caps,
            "category": cat,
        })

    # Sort and take top 100 to avoid overwhelming the UI
    models.sort(key=lambda x: x["model"])
    
    # Apply filters
    if provider:
        models = [m for m in models if m["provider"] == provider.lower()]
    if category:
        models = [m for m in models if m["category"] == category.lower()]
    if capability:
        models = [m for m in models if capability.lower() in m["capabilities"]]

    # Limit results for performance
    models = models[:100]

    # Compute health from recent logs (last 1 hour)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    try:
        health_query = (
            select(
                LLMTokenLog.model,
                func.count().label("total"),
                func.count().filter(LLMTokenLog.status != "success").label("errors"),
            )
            .where(LLMTokenLog.created_at >= cutoff)
            .group_by(LLMTokenLog.model)
        )
        result = await session.execute(health_query)
        health_map = {}
        for row in result.all():
            error_rate = row.errors / max(row.total, 1)
            health_map[row.model] = {
                "status": "down" if error_rate > 0.5 else "degraded" if error_rate > 0.1 else "healthy",
                "requests_1h": row.total,
                "error_rate": round(error_rate * 100, 1),
            }
    except Exception:
        health_map = {}

    # Enrich models with health data
    enriched = []
    for m in models:
        health = health_map.get(m["model"], {
            "status": "unknown",
            "requests_1h": 0,
            "error_rate": 0,
        })
        enriched.append({
            **m,
            "health": health,
            "input_cost_per_1k": round(m["input_cost_per_token"] * 1000, 6),
            "output_cost_per_1k": round(m["output_cost_per_token"] * 1000, 6),
        })

    # Extract unique providers and capabilities for filters
    all_providers = sorted(set(m["provider"] for m in models))
    all_capabilities = sorted(set(c for m in models for c in m["capabilities"]))

    return {
        "models": enriched,
        "total": len(enriched),
        "providers": all_providers,
        "capabilities": all_capabilities,
        "categories": ["flagship", "efficient", "open-source"],
    }
