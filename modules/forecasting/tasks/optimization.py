"""
Optimization tasks for the Forecasting/M5 module.

Task:
scan_for_optimizations() — runs globally (e.g. weekly) to scan
all tenants for verbose prompts or expensive models, generating recommendations.
"""

import asyncio
import logging
from celery_app import app
from sqlalchemy import select
from core.database import AsyncSessionLocal
from modules.gateway.models import LLMTokenLog
from modules.forecasting.services.optimizer_service import OptimizerService

logger = logging.getLogger(__name__)

async def _scan_optimizations_for_all() -> dict:
    """
    Finds all unique tenants that have LLM traffic and runs 
    the optimization engine (M5) for each.
    """
    results = []
    
    async with AsyncSessionLocal() as session:
        # Get all active tenants from target log tables
        # Distinct tenants who actually have logs
        result = await session.execute(
            select(LLMTokenLog.tenant_id.distinct())
        )
        tenant_ids = [row[0] for row in result.all() if row[0] is not None]

    for tenant_id in tenant_ids:
        # We spawn a fresh session per tenant to avoid massive transactions
        try:
            async with AsyncSessionLocal() as session:
                optimizer = OptimizerService(session)
                res = await optimizer.run_optimization_for_tenant(
                    tenant_id=tenant_id, 
                    lookback_days=30
                )
                results.append(res)
        except Exception as e:
            logger.error(
                "Optimization scan failed for tenant",
                extra={"tenant_id": tenant_id, "error": str(e)}
            )

    return {
        "tenants_scanned": len(tenant_ids),
        "successful_scans": len(results),
        "results_summary": results
    }

@app.task(name="modules.forecasting.tasks.optimization.scan_for_optimizations")
def scan_for_optimizations():
    """
    Weekly background task to run M5 optimization intelligence on all active tenants.
    """
    logger.info("Starting global optimization scan (M5)")
    return asyncio.run(_scan_optimizations_for_all())
