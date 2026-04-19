"""
WebSocket endpoint for real-time dashboard push.

Broadcasts events to connected dashboard clients:
  - anomaly_detected   → toast + badge update
  - health_changed     → sidebar health indicator
  - cost_update        → KPI card auto-refresh
  - budget_warning     → alert banner
  - governance_decision → governance view update

Connection:
    ws://localhost:8000/ws/dashboard?token=<jwt>

Architecture:
    The ConnectionManager is a singleton that tracks all active
    WebSocket connections. When a domain event fires on the
    event_bus, a handler pushes it to all relevant sockets.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from modules.auth.service import AuthService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["WebSocket"])


# ============================================================
# CONNECTION MANAGER
# ============================================================

class ConnectionManager:
    """
    Manages active WebSocket connections.
    Tracks connections per user and per tenant for scoped broadcasts.
    """

    def __init__(self):
        # {websocket: {"user_id": str, "tenant_id": str|None, "role": str}}
        self._connections: dict[WebSocket, dict] = {}

    @property
    def active_count(self) -> int:
        return len(self._connections)

    async def connect(self, websocket: WebSocket, user_info: dict) -> None:
        await websocket.accept()
        self._connections[websocket] = user_info
        logger.info(
            f"WS connected: user={user_info.get('username')} "
            f"tenant={user_info.get('tenant_id')} "
            f"(total={self.active_count})"
        )

    def disconnect(self, websocket: WebSocket) -> None:
        user_info = self._connections.pop(websocket, {})
        logger.info(
            f"WS disconnected: user={user_info.get('username')} "
            f"(total={self.active_count})"
        )

    async def broadcast(self, message: dict, tenant_id: Optional[str] = None) -> int:
        """
        Send a message to all connected clients.
        If tenant_id is provided, only send to that tenant's clients
        (plus super_admins who see everything).
        Returns the number of clients that received the message.
        """
        sent = 0
        stale: list[WebSocket] = []
        payload = json.dumps(message)

        for ws, info in self._connections.items():
            # Scope check: super_admins get everything, others only their tenant
            if tenant_id and info.get("role") != "super_admin":
                if info.get("tenant_id") != tenant_id:
                    continue
            try:
                await ws.send_text(payload)
                sent += 1
            except Exception:
                stale.append(ws)

        # Clean up dead connections
        for ws in stale:
            self.disconnect(ws)

        return sent

    async def send_personal(self, websocket: WebSocket, message: dict) -> None:
        try:
            await websocket.send_text(json.dumps(message))
        except Exception:
            self.disconnect(websocket)


# Singleton
manager = ConnectionManager()


# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

@router.websocket("/ws/dashboard")
async def dashboard_websocket(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
):
    """
    WebSocket endpoint for real-time dashboard updates.

    Authentication: pass JWT as query param ?token=<jwt>
    The token is validated on connect; invalid tokens are rejected.
    """
    # Authenticate
    user_info = None
    if token:
        payload = AuthService.decode_token(token)
        if payload and payload.get("type") == "access":
            # Check blacklist
            jti = payload.get("jti", "")
            if not AuthService.is_blacklisted(jti):
                user_info = {
                    "user_id": payload.get("sub"),
                    "username": payload.get("username"),
                    "role": payload.get("role"),
                    "tenant_id": payload.get("tenant_id"),
                }

    if not user_info:
        # Reject unauthenticated connections
        await websocket.close(code=4001, reason="Authentication required")
        return

    await manager.connect(websocket, user_info)

    try:
        # Send initial connected message
        await manager.send_personal(websocket, {
            "type": "connected",
            "message": "WebSocket connected to LLM Dashboard",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": user_info["username"],
            "active_connections": manager.active_count,
        })

        # Keep alive loop — also handles client messages
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Handle client messages (e.g., pong, subscribe)
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "pong":
                        pass  # heartbeat acknowledged
                    elif msg.get("type") == "subscribe":
                        # Could add topic-based subscriptions later
                        pass
                except json.JSONDecodeError:
                    pass
            except asyncio.TimeoutError:
                # Send ping to keep connection alive
                try:
                    await manager.send_personal(websocket, {
                        "type": "ping",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception:
                    break

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        manager.disconnect(websocket)


# ============================================================
# EVENT BUS INTEGRATION — push domain events to WS clients
# ============================================================

async def push_anomaly_detected(event: dict) -> None:
    """Push anomaly alerts to all relevant WS clients."""
    data = event.get("data", {})
    tenant_id = data.get("tenant_id")
    await manager.broadcast({
        "type": "anomaly_detected",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "tenant_id": tenant_id,
            "anomaly_type": data.get("anomaly_type", ""),
            "severity": data.get("severity", "warning"),
            "description": data.get("description", "Anomaly detected"),
            "observed_value": data.get("observed_value"),
            "baseline_mean": data.get("baseline_mean"),
        }
    }, tenant_id=tenant_id)


async def push_governance_decision(event: dict) -> None:
    """Push governance decisions (especially blocks) to WS clients."""
    data = event.get("data", {})
    decision = data.get("decision", "allow")
    # Only push interesting decisions (blocks, downgrades)
    if decision in ("block", "allow_downgrade"):
        tenant_id = data.get("tenant_id")
        await manager.broadcast({
            "type": "governance_decision",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": {
                "tenant_id": tenant_id,
                "decision": decision,
                "model": data.get("model", ""),
                "reason": data.get("reason", ""),
            }
        }, tenant_id=tenant_id)


async def push_health_changed(event: dict) -> None:
    """Push health status changes to all WS clients."""
    data = event.get("data", {})
    await manager.broadcast({
        "type": "health_changed",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    })


async def push_explanation_generated(event: dict) -> None:
    """Push M4 explanations to WS clients immediately."""
    data = event.get("data", {})
    tenant_id = data.get("tenant_id")
    await manager.broadcast({
        "type": "explanation_generated",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "tenant_id": tenant_id,
            "anomaly_id": data.get("anomaly_id"),
            "explanation": data.get("explanation_text", "Explanation ready"),
        }
    }, tenant_id=tenant_id)


def register_ws_handlers() -> None:
    """Register WebSocket push handlers on the event bus."""
    from core.event_bus import register_handler
    register_handler("anomaly.detected", push_anomaly_detected)
    register_handler("governance.decision", push_governance_decision)
    register_handler("health.changed", push_health_changed)
    register_handler("explanation.generated", push_explanation_generated)
    logger.info("WebSocket event handlers registered")
