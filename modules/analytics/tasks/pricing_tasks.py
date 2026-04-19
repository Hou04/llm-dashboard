import asyncio
import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import text, select
from celery_app import app
from core.database import AsyncSessionLocal
from modules.analytics.models import LLMPricingRule
from modules.analytics.tasks.aggregation import backfill_all

logger = logging.getLogger(__name__)

async def _apply_retroactive_pricing(rule_id: str) -> dict:
    """
    Applies retroactive pricing changes to all matching llm_token_log entries
    for a specific versioned PricingRule window.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(LLMPricingRule).where(LLMPricingRule.id == rule_id)
        )
        rule = result.scalar_one_or_none()
        if not rule:
            logger.error(f"Cannot apply retroactive pricing. Rule {rule_id} not found.")
            return {"status": "error", "message": "Rule not found"}

        from_str = rule.effective_from.isoformat()
        to_str = rule.effective_to.isoformat() if rule.effective_to else date.today().isoformat()
        
        # 1. Update the hypertable log rows
        stmt = text("""
            UPDATE llm_token_log
            SET cost_usd = (ROUND(CAST(input_tokens AS numeric) / 1000.0 * :in_price, 8)) +
                           (ROUND(CAST(output_tokens AS numeric) / 1000.0 * :out_price, 8))
            WHERE model = :model
              AND provider = :provider
              AND created_at >= :from_str::timestamp
              AND created_at <= :to_str::timestamp + interval '1 day'
        """)
        
        # Execute the retroactive SQL
        await session.execute(stmt, {
            "in_price": rule.input_price_per_1k,
            "out_price": rule.output_price_per_1k,
            "model": rule.model,
            "provider": rule.provider,
            "from_str": from_str,
            "to_str": to_str
        })
        await session.commit()
        
        logger.info(f"Retroactive DB prices synced for {rule.model} within {from_str} - {to_str}.")
        return {"status": "ok", "from_date": from_str, "to_date": to_str}

@app.task(name="modules.analytics.tasks.pricing_tasks.apply_retroactive_pricing_task")
def apply_retroactive_pricing_task(rule_id: str):
    """
    Triggers retroactive correction block + subsequent aggregation backfill logic.
    """
    # 1. Update logs
    res = asyncio.run(_apply_retroactive_pricing(rule_id))
    
    # 2. Trigger backfill on impacted date range
    if res["status"] == "ok":
        backfill_all(res["from_date"], res["to_date"])
        logger.info(f"Successfully backfilled token analytics for updated rule: {rule_id}")
    
    return res
