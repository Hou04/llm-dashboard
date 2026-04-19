"""
Asynchronous tasks for the Gateway module (M1).

Offloads database inserts from the synchronous API path to ensure high throughput.
"""

import asyncio
import logging
from typing import Dict, Any

from celery_app import app
from core.database import AsyncSessionLocal
from modules.gateway.models import LLMTokenLog, LLMGovernanceDecision

logger = logging.getLogger(__name__)

async def _persist_log_async(log_data: Dict[str, Any], decision_data: Dict[str, Any]) -> None:
    """
    Asynchronously write the LLMTokenLog and LLMGovernanceDecision to the DB.
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Create Log Entry
            log_entry = LLMTokenLog(**log_data)
            session.add(log_entry)
            
            # 2. Create Decision Entry
            if decision_data:
                decision_record = LLMGovernanceDecision(**decision_data)
                session.add(decision_record)
            
            await session.commit()
    except Exception as e:
        logger.error(f"Async persistence failed: {e}", extra={"log_data": log_data})
        # Note: If this fails, the Redis quota is already updated (it's ahead of DB).
        # We accept this slight inconsistency for extreme throughput.

@app.task(name="modules.gateway.tasks.persist_log_async", ignore_result=True)
def persist_log_async(log_data: Dict[str, Any], decision_data: Dict[str, Any] = None):
    """
    Celery task wrapper to persist LLM calls into the database via background worker.
    """
    asyncio.run(_persist_log_async(log_data, decision_data))
