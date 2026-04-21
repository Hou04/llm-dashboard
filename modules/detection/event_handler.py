"""
Detection module — event handler.

Subscribes to call.logged events from the Gateway module.
On each call, runs the anomaly check for the affected tenant.

This is the real-time detection path. Every LLM call triggers
a check. The check is fast because:
1. Baseline is a single-row lookup (llm_token_baseline)
2. Today's totals can be read from Redis (or a fast aggregate)
3. Z-score is pure arithmetic

Registration:
    Call register_detection_handlers() once at application startup.
    This wires the handler into the event bus.
"""

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from core.database import AsyncSessionLocal
from core.event_bus import subscribe
from modules.detection.services import DetectionService

logger = logging.getLogger(__name__)


async def handle_call_logged(event: dict) -> None:
    """
    Handle a call.logged event from the Gateway.

    Opens its own database session — event handlers are
    decoupled from the caller's session and transaction.

    The event envelope structure:
        {
            "event_id": str,
            "event_type": "call.logged",
            "published_at": str,
            "data": {
                "tenant_id": str,
                "log_id": str,
                ...
            }
        }
    """
    data = event.get("data", {})
    tenant_id = data.get("tenant_id")
    log_id_str = data.get("log_id")
    agent_id = data.get("agent_id")
    model = data.get("model")

    if not tenant_id:
        logger.warning("call.logged event missing tenant_id", extra={"event": event})
        return

    log_id: Optional[uuid.UUID] = None
    if log_id_str:
        try:
            log_id = uuid.UUID(log_id_str)
        except (ValueError, AttributeError):
            pass

    scopes = [(tenant_id, "*", "*")]
    if agent_id:
        scopes.append((tenant_id, agent_id, "*"))
    if model:
        scopes.append((tenant_id, "*", model))

    try:
        async with AsyncSessionLocal() as session:
            service = DetectionService(session)
            
            for tid, aid, m in scopes:
                result = await service.check_entity_now(
                    tenant_id=tid,
                    agent_id=aid,
                    model_name=m,
                    trigger_log_id=log_id,
                )

                if result.is_anomaly:
                    logger.info(
                        "Anomaly detected via event",
                        extra={
                            "tenant_id": tid,
                            "agent_id": aid,
                            "model": m,
                            "severity": result.severity,
                            "vote_count": result.ensemble.vote_count if result.ensemble else 0,
                            "detector_names": result.ensemble.detector_names if result.ensemble else [],
                            "anomaly_id": str(result.anomaly_id),
                        },
                    )
    except Exception as exc:
        # Never let a detection failure crash the event handler.
        # The call was already logged successfully — detection is
        # best-effort.
        logger.error(
            "Detection handler failed",
            extra={"tenant_id": tenant_id, "error": str(exc)},
        )


async def handle_anomaly_detected(event: dict) -> None:
    """
    Handle an anomaly.detected event from the Detection service.

    Triggers M4 to generate an explanation for anomalies
    of severity WARNING or above.
    """
    data = event.get("data", {})
    anomaly_id_str = data.get("anomaly_id")
    severity = data.get("severity", "normal")

    if severity not in {"warning", "high", "critical"}:
        return

    if not anomaly_id_str:
        return

    try:
        anomaly_id = uuid.UUID(anomaly_id_str)
    except (ValueError, AttributeError):
        return

    try:
        async with AsyncSessionLocal() as session:
            # Fetch the full anomaly record
            from sqlalchemy import select
            from modules.detection.models import LLMAnomalyRecord

            result = await session.execute(
                select(LLMAnomalyRecord).where(
                    LLMAnomalyRecord.id == anomaly_id
                )
            )
            anomaly = result.scalar_one_or_none()

            if anomaly is None:
                return

            from modules.detection.services.explainer_service import ExplainerService
            explainer = ExplainerService(session)
            explanation = await explainer.explain_anomaly(anomaly)

            if explanation:
                logger.info(
                    "M4 explanation generated",
                    extra={
                        "anomaly_id": str(anomaly_id),
                        "tenant_id": anomaly.tenant_id,
                        "cache_hit": explanation.cache_hit,
                        "tokens_used": explanation.tokens_used,
                    },
                )
                
                from core.event_bus import publish_background
                # Publish to M9 Websocket immediately
                publish_background("explanation.generated", {
                    "anomaly_id": str(anomaly_id),
                    "tenant_id": anomaly.tenant_id,
                    "explanation_text": explanation.explanation_text,
                    "generated_at": explanation.generated_at.isoformat()
                })

    except Exception as exc:
        logger.error(
            "M4 handler failed",
            extra={"anomaly_id": anomaly_id_str, "error": str(exc)},
        )


def register_detection_handlers() -> None:
    """Register all detection event handlers."""
    from core.event_bus import register_handler
    register_handler("call.logged", handle_call_logged)
    register_handler("anomaly.detected", handle_anomaly_detected)
    logger.info("Detection event handlers registered (M3 + M4)")