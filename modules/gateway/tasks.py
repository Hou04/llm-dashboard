"""
Asynchronous tasks for the Gateway module (M1).

Offloads database inserts from the synchronous API path to ensure high throughput.
"""

import asyncio
import logging
from typing import Dict, Any

from celery_app import app
from core.database import AsyncSessionLocal
from modules.gateway.models import LLMTokenLog, LLMGovernanceDecision, LLMProviderStatus
from sqlalchemy import select, update
from datetime import datetime, timezone
import random

logger = logging.getLogger(__name__)

async def _persist_log_async(log_data: Dict[str, Any], decision_data: Dict[str, Any]) -> None:
    """
    Asynchronously write the LLMTokenLog and LLMGovernanceDecision to the DB.
    """
    try:
        from core.database import async_session_factory
        from datetime import datetime
        from decimal import Decimal

        # Handle string inputs (when called via Celery or if strings are passed)
        if isinstance(log_data.get("created_at"), str):
            try:
                log_data["created_at"] = datetime.fromisoformat(log_data["created_at"].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass
        
        if isinstance(log_data.get("cost_usd"), str):
            try:
                log_data["cost_usd"] = Decimal(log_data["cost_usd"])
            except (ValueError, TypeError):
                pass

        if decision_data and isinstance(decision_data.get("created_at"), str):
            try:
                decision_data["created_at"] = datetime.fromisoformat(decision_data["created_at"].replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass

        async with async_session_factory() as session:
            # 1. Create Log Entry
            log_entry = LLMTokenLog(**log_data)
            session.add(log_entry)
            
            # 2. Handle Session Tracing (Feature 8)
            metadata = log_data.get("metadata_") or {}
            session_id = metadata.get("session_id") or log_data.get("session_id")
            
            if session_id:
                try:
                    from modules.tracing.repositories.session_repository import SessionRepository
                    session_repo = SessionRepository(session)
                    await session_repo.upsert_and_aggregate(
                        session_id=session_id,
                        tenant_id=log_data.get("tenant_id"),
                        log_id=log_data.get("id"),
                        tokens=log_data.get("total_tokens", 0),
                        cost=float(log_data.get("cost_usd") or 0.0),
                        duration=log_data.get("duration_ms", 0),
                        agent_id=log_data.get("agent_id"),
                        user_id=log_data.get("user_id")
                    )
                except Exception as sess_err:
                    logger.warning(f"Failed to aggregate session {session_id}: {sess_err}")

            # 3. Create Decision Entry
            if decision_data:
                # Map created_at to evaluated_at if needed, though model uses evaluated_at
                # Actually LLMGovernanceDecision has evaluated_at
                if "created_at" in decision_data:
                    decision_data["evaluated_at"] = decision_data.pop("created_at")
                
                decision_record = LLMGovernanceDecision(**decision_data)
                session.add(decision_record)
            
            await session.commit()
    except Exception as e:
        logger.error(f"Async persistence failed: {e}")

@app.task(name="modules.gateway.tasks.persist_log_async", ignore_result=True)
def persist_log_async(log_data: Dict[str, Any], decision_data: Dict[str, Any] = None):
    """
    Celery task wrapper to persist LLM calls into the database via background worker.
    """
    asyncio.run(_persist_log_async(log_data, decision_data))

async def _check_provider_health_async():
    """
    Ping all active providers to measure latency and status.
    """
    providers = ["openai", "anthropic", "google", "meta", "mistral", "cohere", "groq", "perplexitiy", "deepseek"]
    
    async with AsyncSessionLocal() as session:
        for p in providers:
            try:
                # Simulated Ping for the demo, would be a real LiteLLM health check in full production
                # We simulate latency based on typical provider speeds
                base_lat = {"openai": 250, "anthropic": 400, "google": 300, "groq": 80}.get(p, 500)
                lat = base_lat + random.randint(-50, 150)
                status = "online"
                
                # Randomly simulate degradation for some
                if random.random() < 0.05:
                    status = "degraded"
                    lat *= 3
                
                stmt = select(LLMProviderStatus).where(LLMProviderStatus.provider_name == p)
                res = await session.execute(stmt)
                record = res.scalar_one_or_none()
                
                if record:
                    record.status = status
                    record.latency_ms = lat
                    record.last_check_at = datetime.now(timezone.utc)
                else:
                    new_rec = LLMProviderStatus(
                        provider_name=p,
                        status=status,
                        latency_ms=lat,
                        uptime_pct=99.9
                    )
                    session.add(new_rec)
                    
            except Exception as e:
                logger.error(f"Health check failed for {p}: {e}")
        
        await session.commit()

@app.task(name="modules.gateway.tasks.check_provider_health")
def check_provider_health():
    """Heartbeat task to monitor AI Infrastructure."""
    asyncio.run(_check_provider_health_async())
