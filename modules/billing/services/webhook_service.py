"""
BillingWebhookService — dispatch billing events to external systems.

Sends finalized invoice data (and credit notes) to configured webhook
endpoints for each tenant. This allows integration with:
  - ERP systems (SAP, Oracle, Odoo)
  - CRM platforms (Salesforce, HubSpot)
  - Stripe / payment gateways
  - Slack / Teams notification channels
  - Custom internal accounting systems

Security:
  Each request is signed with HMAC-SHA256 using a per-tenant secret.
  The receiving endpoint can verify authenticity by computing the same
  signature over the raw request body with the shared secret.

  Header: X-Webhook-Signature: sha256=<hex_digest>

Reliability:
  Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s).
  All dispatch attempts are logged for auditing.

Configuration:
  Webhook URLs are stored per-tenant in TENANT_WEBHOOK_CONFIG.
  In production this would come from a database or admin panel.
"""

import hashlib
import hmac
import json
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


# ============================================================
# WEBHOOK CONFIGURATION
#
# Webhook configurations are now fetched dynamically via the
# ConfigRegistry or could be queried from the database.
# E.g. config.get(f"webhook.{tenant_id}.url")
# ============================================================
from core.config_registry import config
from core.settings import settings as _settings

# Retry configuration — loaded from ConfigRegistry at runtime
def _max_retries():    return config.get("webhook.max_retries", 3)
def _retry_base_sec(): return config.get("webhook.retry_base_sec", 1.0)
def _timeout_sec():    return config.get("webhook.timeout_sec", 10.0)


class BillingWebhookService:
    """Dispatch billing events to external webhook endpoints."""

    # ================================================================
    # PUBLIC API
    # ================================================================

    async def dispatch_invoice_event(
        self,
        event_type: str,
        invoice_data: dict,
        tenant_id: str,
    ) -> dict:
        """
        Dispatch a billing event for a tenant.

        Args:
            event_type:   e.g. "invoice.finalized", "credit_note.issued"
            invoice_data: full invoice payload (same as GET /invoice response)
            tenant_id:    which tenant's webhook config to use

        Returns:
            dict with dispatch result: success, status_code, attempts, error
        """
        # Retrieve webhook config dynamically
        enabled = config.get(f"webhook.{tenant_id}.enabled", False)
        webhook_url = config.get(f"webhook.{tenant_id}.url")
        secret = config.get(f"webhook.{tenant_id}.secret")
        subscribed_events = config.get(f"webhook.{tenant_id}.events", ["invoice.finalized"])
        headers = config.get(f"webhook.{tenant_id}.headers", {})

        if not enabled or not webhook_url or not secret:
            logger.info(
                "webhook.skipped",
                tenant_id=tenant_id,
                reason="not_configured_or_disabled",
            )
            return {
                "dispatched": False,
                "reason": "Webhook not configured or disabled for this tenant",
            }

        # Check if tenant subscribes to this event type
        if event_type not in subscribed_events:
            logger.info(
                "webhook.skipped",
                tenant_id=tenant_id,
                event_type=event_type,
                reason="event_not_subscribed",
            )
            return {
                "dispatched": False,
                "reason": f"Tenant not subscribed to event '{event_type}'",
            }

        # Build payload
        payload = self._build_payload(event_type, invoice_data, tenant_id)

        # Sign and dispatch
        return await self._dispatch_with_retry(
            url=webhook_url,
            payload=payload,
            secret=secret,
            extra_headers=headers if isinstance(headers, dict) else {},
            tenant_id=tenant_id,
            event_type=event_type,
        )

    async def test_webhook(self, tenant_id: str) -> dict:
        """
        Send a test ping to the tenant's webhook endpoint.

        Returns connectivity status without sending real data.
        """
        enabled = config.get(f"webhook.{tenant_id}.enabled", False)
        webhook_url = config.get(f"webhook.{tenant_id}.url")
        secret = config.get(f"webhook.{tenant_id}.secret")
        headers = config.get(f"webhook.{tenant_id}.headers", {})

        if not webhook_url or not secret:
            return {"success": False, "error": f"No webhook config for tenant {tenant_id}"}
        if not enabled:
            return {"success": False, "error": "Webhook is disabled for this tenant"}

        test_payload = {
            "event_type": "webhook.test",
            "tenant_id":  tenant_id,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "message":    "Test ping from LLM Dashboard billing system",
        }

        result = await self._dispatch_with_retry(
            url=webhook_url,
            payload=test_payload,
            secret=secret,
            extra_headers=headers,
            tenant_id=tenant_id,
            event_type="webhook.test",
        )
        return result

    def get_webhook_config(self, tenant_id: str) -> Optional[dict]:
        """Return the webhook configuration for a tenant (redacted secret)."""
        webhook_url = config.get(f"webhook.{tenant_id}.url")
        if not webhook_url:
            return None
        secret = config.get(f"webhook.{tenant_id}.secret", "")
        return {
            "tenant_id": tenant_id,
            "enabled":   config.get(f"webhook.{tenant_id}.enabled", False),
            "url":       webhook_url,
            "events":    config.get(f"webhook.{tenant_id}.events", []),
            "secret":    secret[:8] + "..." if len(secret) > 8 else "***",
        }

    # ================================================================
    # INTERNAL — DISPATCH ENGINE
    # ================================================================

    async def _dispatch_with_retry(
        self,
        url: str,
        payload: dict,
        secret: str,
        extra_headers: dict,
        tenant_id: str,
        event_type: str,
    ) -> dict:
        """Send HTTP POST with retry logic and HMAC signing."""
        body_bytes = json.dumps(payload, default=str).encode("utf-8")
        signature  = self._sign_payload(body_bytes, secret)

        max_retries = _max_retries()
        retry_base  = _retry_base_sec()
        timeout_sec = _timeout_sec()

        headers = {
            "Content-Type":        "application/json",
            "X-Webhook-Signature": f"sha256={signature}",
            "X-Event-Type":        event_type,
            "X-Tenant-Id":         tenant_id,
            "User-Agent":          "LLMDashboard-Billing/1.0",
            **extra_headers,
        }

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_sec) as client:
                    response = await client.post(
                        url, content=body_bytes, headers=headers,
                    )

                logger.info(
                    "webhook.dispatched",
                    tenant_id=tenant_id,
                    event_type=event_type,
                    url=url,
                    attempt=attempt,
                    status_code=response.status_code,
                )

                if 200 <= response.status_code < 300:
                    return {
                        "dispatched":  True,
                        "status_code": response.status_code,
                        "attempts":    attempt,
                        "url":         url,
                    }

                # Non-2xx — will retry
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"

            except httpx.TimeoutException:
                last_error = f"Timeout after {timeout_sec}s"
                logger.warning(
                    "webhook.timeout",
                    tenant_id=tenant_id,
                    attempt=attempt,
                    url=url,
                )
            except httpx.RequestError as e:
                last_error = f"Request error: {str(e)}"
                logger.warning(
                    "webhook.request_error",
                    tenant_id=tenant_id,
                    attempt=attempt,
                    error=str(e),
                )

            # Exponential backoff before retry
            if attempt < max_retries:
                delay = retry_base * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(
            "webhook.failed",
            tenant_id=tenant_id,
            event_type=event_type,
            url=url,
            attempts=max_retries,
            last_error=last_error,
        )
        return {
            "dispatched": False,
            "attempts":   max_retries,
            "url":        url,
            "error":      last_error,
        }

    # ================================================================
    # INTERNAL — PAYLOAD & SIGNING
    # ================================================================

    def _build_payload(
        self,
        event_type: str,
        invoice_data: dict,
        tenant_id: str,
    ) -> dict:
        """Build the webhook event payload."""
        return {
            "event_type":  event_type,
            "tenant_id":   tenant_id,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "api_version": _settings.api_version,
            "data":        invoice_data,
        }

    @staticmethod
    def _sign_payload(body_bytes: bytes, secret: str) -> str:
        """
        Compute HMAC-SHA256 signature.

        The receiving endpoint should:
          1. Read the raw request body
          2. Compute HMAC-SHA256 with the shared secret
          3. Compare hex digest to the X-Webhook-Signature header value
        """
        return hmac.new(
            secret.encode("utf-8"),
            body_bytes,
            hashlib.sha256,
        ).hexdigest()
