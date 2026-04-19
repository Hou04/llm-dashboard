"""
Analytics module (M2) — event handler.

Subscribes to call.logged events from M1 Gateway.
Incrementally updates llm_cost_daily aggregates in real-time
instead of waiting for batch pipeline runs.

This is the real-time M2 path. The batch pipeline (PipelineOrchestrator)
is kept for backfill and reconciliation — it fills gaps and corrects
drift between real-time increments and actual totals.

Event chain:
    M1 publishes "call.logged" → this handler fires →
    llm_cost_daily row upserted (INSERT ... ON CONFLICT UPDATE)

Registration:
    Call register_analytics_handlers() once at application startup.
"""

import logging
from datetime import datetime, timezone
from decimal import Decimal

from core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)


async def handle_call_for_aggregation(event: dict) -> None:
    """
    Handle a call.logged event from M1.

    Incrementally updates the daily cost aggregate row for
    this tenant+model+date combination using atomic upsert.

    This means dashboard cost widgets update within seconds
    of a gateway call, instead of waiting for the batch pipeline.

    The event envelope structure:
        {
            "event_id": str,
            "event_type": "call.logged",
            "published_at": str,
            "data": {
                "tenant_id": str,
                "model": str,
                "provider": str,
                "total_tokens": int,
                "cost_usd": str,
                "status": str,
                ...
            }
        }
    """
    data = event.get("data", {})
    tenant_id = data.get("tenant_id")
    model = data.get("model")
    provider = data.get("provider")
    cost_usd = data.get("cost_usd", "0")
    total_tokens = data.get("total_tokens", 0)
    status = data.get("status", "success")

    if not tenant_id:
        logger.warning("M2 handler: call.logged event missing tenant_id")
        return

    # Blocked calls don't count in cost aggregation
    if status == "blocked":
        return

    try:
        async with AsyncSessionLocal() as session:
            from sqlalchemy import text

            today = datetime.now(timezone.utc).date()

            # Atomic upsert: insert or increment existing daily aggregate
            await session.execute(
                text("""
                    INSERT INTO llm_cost_daily
                        (tenant_id, model, provider, cost_date,
                         total_tokens, total_cost_usd, call_count)
                    VALUES
                        (:tenant_id, :model, :provider, :cost_date,
                         :tokens, :cost, 1)
                    ON CONFLICT (tenant_id, model, provider, cost_date)
                    DO UPDATE SET
                        total_tokens = llm_cost_daily.total_tokens + EXCLUDED.total_tokens,
                        total_cost_usd = llm_cost_daily.total_cost_usd + EXCLUDED.total_cost_usd,
                        call_count = llm_cost_daily.call_count + 1
                """),
                {
                    "tenant_id": tenant_id,
                    "model": model or "unknown",
                    "provider": provider or "unknown",
                    "cost_date": today,
                    "tokens": total_tokens,
                    "cost": Decimal(str(cost_usd)),
                },
            )
            await session.commit()

            logger.debug(
                "M2 real-time aggregation updated",
                extra={
                    "tenant_id": tenant_id,
                    "model": model,
                    "cost_date": str(today),
                    "tokens": total_tokens,
                },
            )

    except Exception as exc:
        # M2 aggregation failure never blocks the call pipeline.
        # The batch pipeline will reconcile on next run.
        logger.error(
            "M2 aggregation handler failed",
            extra={"tenant_id": tenant_id, "error": str(exc)},
        )


def register_analytics_handlers() -> None:
    """
    Register M2 event handlers on the event bus.
    Call once at app startup, after the database is initialized.
    """
    from core.event_bus import register_handler
    register_handler("call.logged", handle_call_for_aggregation)
    logger.info("M2 Analytics event handlers registered")
