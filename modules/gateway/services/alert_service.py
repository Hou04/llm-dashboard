"""
M7 Alert Service — Webhook & notification dispatch for governance threshold breaches.

Subscribes to governance events via the event bus and dispatches
alerts to configured webhook endpoints (Slack, Teams, PagerDuty, generic HTTP).

Privacy: No prompt content is ever included in alert payloads.
Only metadata (tenant, model, decision, rule, usage counters) is sent.

Integration patterns:
- Slack: POST to incoming webhook URL with Slack Block Kit payload
- Teams: POST to incoming webhook URL with Adaptive Card payload
- PagerDuty: POST to Events API v2 with severity-based routing
- Generic: POST to any HTTP endpoint with JSON payload
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from core.event_bus import subscribe
from core.settings import settings

logger = logging.getLogger(__name__)

# Alert cooldown: don't spam the same tenant more than once per N seconds
_alert_cooldown: dict[str, datetime] = {}
ALERT_COOLDOWN_SECONDS = 300  # 5 minutes between alerts for the same tenant


def _is_on_cooldown(tenant_id: str) -> bool:
    """Check if we recently alerted about this tenant."""
    last_alert = _alert_cooldown.get(tenant_id)
    if last_alert is None:
        return False
    elapsed = (datetime.now(timezone.utc) - last_alert).total_seconds()
    return elapsed < ALERT_COOLDOWN_SECONDS


def _mark_alerted(tenant_id: str) -> None:
    """Record that we just alerted about this tenant."""
    _alert_cooldown[tenant_id] = datetime.now(timezone.utc)


# ================================================================
# PAYLOAD FORMATTERS
# ================================================================

def _build_slack_payload(tenant_id: str, reason: str, model: str, rule_id: Optional[str]) -> dict:
    """Format alert as a Slack Block Kit message."""
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 LLM Governance Alert",
                    "emoji": True,
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Tenant:*\n`{tenant_id}`"},
                    {"type": "mrkdwn", "text": f"*Model:*\n`{model}`"},
                    {"type": "mrkdwn", "text": f"*Decision:*\n🔴 BLOCKED"},
                    {"type": "mrkdwn", "text": f"*Rule:*\n`{rule_id or 'N/A'}`"},
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Reason:*\n{reason}",
                }
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    }
                ]
            },
        ]
    }


def _build_teams_payload(tenant_id: str, reason: str, model: str, rule_id: Optional[str]) -> dict:
    """Format alert as a Microsoft Teams Adaptive Card."""
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "FF0000",
        "summary": f"LLM Governance Alert: {tenant_id} blocked",
        "sections": [{
            "activityTitle": "🚨 LLM Governance Alert — Call Blocked",
            "facts": [
                {"name": "Tenant", "value": tenant_id},
                {"name": "Model", "value": model},
                {"name": "Decision", "value": "BLOCKED"},
                {"name": "Rule ID", "value": rule_id or "N/A"},
                {"name": "Reason", "value": reason},
                {"name": "Time", "value": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')},
            ],
            "markdown": True,
        }]
    }


def _build_pagerduty_payload(tenant_id: str, reason: str, model: str, rule_id: Optional[str]) -> dict:
    """Format alert as a PagerDuty Events API v2 trigger."""
    return {
        "routing_key": getattr(settings, 'pagerduty_routing_key', ''),
        "event_action": "trigger",
        "payload": {
            "summary": f"LLM Governance: {tenant_id} blocked on {model}",
            "severity": "error",
            "source": "llm-dashboard-m7",
            "component": "governance",
            "group": tenant_id,
            "custom_details": {
                "tenant_id": tenant_id,
                "model": model,
                "reason": reason,
                "rule_id": rule_id,
            },
        },
    }


def _build_generic_payload(tenant_id: str, reason: str, model: str, rule_id: Optional[str]) -> dict:
    """Generic JSON payload for any HTTP webhook."""
    return {
        "event": "governance.call_blocked",
        "tenant_id": tenant_id,
        "model_requested": model,
        "decision": "block",
        "reason": reason,
        "rule_id": rule_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ================================================================
# WEBHOOK DISPATCHER
# ================================================================

async def _dispatch_webhook(url: str, payload: dict, webhook_type: str = "generic") -> bool:
    """
    Send an HTTP POST to a webhook endpoint.
    Returns True on success, False on failure. Never raises.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code < 300:
                logger.info(
                    f"Webhook alert sent successfully",
                    extra={"url": url, "type": webhook_type, "status": response.status_code},
                )
                return True
            else:
                logger.warning(
                    f"Webhook returned non-success status",
                    extra={"url": url, "status": response.status_code, "body": response.text[:200]},
                )
                return False
    except Exception as e:
        logger.error(
            f"Webhook dispatch failed",
            extra={"url": url, "type": webhook_type, "error": str(e)},
        )
        return False


# ================================================================
# EVENT BUS HANDLER — AUTO-REGISTERED ON IMPORT
# ================================================================

@subscribe("call.blocked")
async def handle_blocked_call(event: dict) -> None:
    """
    Event handler triggered when governance blocks a call.

    Dispatches alerts to all configured webhook URLs.
    Respects cooldown to prevent alert flooding.
    """
    data = event.get("data", {})
    tenant_id = data.get("tenant_id", "unknown")
    model = data.get("model_requested", "unknown")
    reason = data.get("reason", "No reason provided")
    rule_id = data.get("rule_id")

    # Check cooldown
    if _is_on_cooldown(tenant_id):
        logger.debug(
            f"Alert suppressed (cooldown active)",
            extra={"tenant_id": tenant_id},
        )
        return

    # Get webhook URLs from settings
    webhook_urls = _get_webhook_configs()

    if not webhook_urls:
        logger.debug("No webhook URLs configured — skipping alert dispatch")
        return

    _mark_alerted(tenant_id)

    # Dispatch to all configured webhooks concurrently
    tasks = []
    for config in webhook_urls:
        url = config["url"]
        wtype = config.get("type", "generic")

        if wtype == "slack":
            payload = _build_slack_payload(tenant_id, reason, model, rule_id)
        elif wtype == "teams":
            payload = _build_teams_payload(tenant_id, reason, model, rule_id)
        elif wtype == "pagerduty":
            payload = _build_pagerduty_payload(tenant_id, reason, model, rule_id)
        else:
            payload = _build_generic_payload(tenant_id, reason, model, rule_id)

        tasks.append(_dispatch_webhook(url, payload, wtype))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    success_count = sum(1 for r in results if r is True)

    logger.info(
        f"Alert dispatch complete: {success_count}/{len(tasks)} webhooks succeeded",
        extra={"tenant_id": tenant_id},
    )


def _get_webhook_configs() -> list[dict]:
    """
    Load webhook configurations from settings.

    Expected format in .env or settings:
        ALERT_WEBHOOKS='[{"url": "https://hooks.slack.com/...", "type": "slack"}]'

    Returns list of {"url": str, "type": str} dicts.
    """
    import json

    raw = getattr(settings, 'alert_webhooks', None)
    if not raw:
        return []

    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Single URL as plain string
            return [{"url": raw, "type": "generic"}]

    if isinstance(raw, list):
        return raw

    return []
