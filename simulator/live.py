"""
Phase 2: Live Traffic Simulator
=================================
Continuously generates traffic through the actual Gateway API.
Shows the platform working in real-time with WebSocket pushes.

Usage:
    python -m simulator --mode live
    python -m simulator --mode live --calls 20
    python -m simulator --mode live --interval 1.5
"""

import asyncio
import random
import time
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from simulator.config import (
    API_BASE_URL,
    ADMIN_USER,
    ADMIN_PASS,
    TENANTS,
    MODELS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    LIVE_INTERVAL_MIN,
    LIVE_INTERVAL_MAX,
)
from simulator.data_generator import DataGenerator
from simulator import utils


async def authenticate() -> str:
    """Authenticate and return JWT token."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{API_BASE_URL}/auth/login",
            json={"username": ADMIN_USER, "password": ADMIN_PASS},
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


async def api_post(token: str, path: str, data: dict) -> dict | None:
    """POST to the API with authentication."""
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                f"{API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
                json=data,
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            utils.error(f"API {path} → HTTP {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            utils.error(f"API {path} → {e}")
            return None


async def api_get(token: str, path: str) -> dict | None:
    """GET from the API with authentication."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None


async def send_call(
    token: str,
    gen: DataGenerator,
    tenant_id: str | None = None,
    force_anomaly: bool = False,
    force_error: bool = False,
    force_blocked: bool = False,
    model_override: str | None = None,
) -> dict | None:
    """Generate and send a single call through the gateway API."""
    # Pick tenant
    if tenant_id is None:
        weights = [TENANTS[t]["weight"] for t in TENANTS]
        tenant_id = random.choices(list(TENANTS.keys()), weights=weights, k=1)[0]

    cfg = TENANTS[tenant_id]
    agent_id = random.choice(cfg["agents"])
    model = model_override or random.choice(cfg["models"])

    # Generate call data
    call = gen.generate_call(
        tenant_id=tenant_id,
        agent_id=agent_id,
        model=model,
        timestamp=datetime.now(timezone.utc),
        is_anomalous=force_anomaly,
        force_error=force_error,
        force_blocked=force_blocked,
    )

    # Build API payload
    payload = {
        "tenant_id": call["tenant_id"],
        "model": call["model"],
        "provider": call["provider"],
        "input_tokens": call["input_tokens"],
        "output_tokens": call["output_tokens"],
        "total_tokens": call["total_tokens"],
        "cost_usd": str(call["cost_usd"]),
        "agent_id": call["agent_id"],
        "module": call["module"],
        "duration_ms": call["duration_ms"],
        "status": call["status"],
        "error_message": call["error_message"],
        "prompt_text": call["prompt_text"],
        "completion_text": call["completion_text"],
        "session_id": call["session_id"],
        "metadata": {"source": "simulator_live", "agent": call["agent_id"]},
    }

    # Send to gateway
    result = await api_post(token, "/gateway/log", payload)

    if result:
        call["_api_result"] = result

    return {**call, "_api_result": result}


async def run_live(
    num_calls: int | None = None,
    interval_min: float = LIVE_INTERVAL_MIN,
    interval_max: float = LIVE_INTERVAL_MAX,
):
    """Run the live traffic simulator."""
    mode = f"{num_calls} calls" if num_calls else "continuous (Ctrl+C to stop)"
    utils.banner("LIVE TRAFFIC SIMULATOR")
    utils.info(f"Mode: {mode}")
    utils.info(f"Interval: {interval_min}-{interval_max}s between calls")
    utils.info(f"Target: {API_BASE_URL}")
    utils.info(f"Tenants: {', '.join(TENANTS.keys())}\n")

    # Authenticate
    utils.section("Authenticating")
    try:
        token = await authenticate()
        utils.success(f"Authenticated as {ADMIN_USER}")
    except Exception as e:
        utils.error(f"Authentication failed: {e}")
        utils.error("Make sure the server is running: pipenv run uvicorn main:app --port 8000")
        return

    gen = DataGenerator(seed=int(time.time()))  # Non-deterministic for live

    utils.section("Starting Live Traffic")
    print(
        f"  {'#':>5s}  {'Time':8s}  {'Decision':15s}  {'Tenant':20s}  "
        f"{'Agent':20s}  {'Tokens':>8s}  {'Cost':>10s}  {'Latency':>7s}"
    )
    print(f"  {'─' * 100}")

    call_count = 0
    anomaly_every = 30  # Inject anomaly every N calls

    try:
        while num_calls is None or call_count < num_calls:
            call_count += 1

            # Determine if this should be anomalous
            is_anomaly = (call_count % anomaly_every == 0)

            result = await send_call(
                token=token,
                gen=gen,
                force_anomaly=is_anomaly,
            )

            if result and result.get("_api_result"):
                api_result = result["_api_result"]
                decision = api_result.get("decision", "?")

                utils.call_log(
                    index=call_count,
                    total=num_calls or 0,
                    tenant=result["tenant_id"],
                    agent=result["agent_id"],
                    decision=decision,
                    tokens=result["total_tokens"],
                    cost=float(result["cost_usd"]),
                    latency=result["duration_ms"],
                )

                # Show anomaly alert if anomalous
                if is_anomaly:
                    utils.anomaly_alert(
                        tenant=result["tenant_id"],
                        anomaly_type="token_spike",
                        severity="warning",
                        description=f"Elevated token usage: {result['total_tokens']:,} tokens (simulated anomaly)",
                    )
            else:
                utils.error(f"Call {call_count} failed to send")

            # Wait before next call
            wait = random.uniform(interval_min, interval_max)
            await asyncio.sleep(wait)

    except KeyboardInterrupt:
        print(f"\n")
        utils.info(f"Stopped after {call_count} calls")

    utils.success(f"\nLive simulation complete: {call_count} calls sent")


# ============================================================
# SCENARIO HELPERS — Used by both live and demo modes
# ============================================================

async def scenario_token_spike(token: str, gen: DataGenerator, tenant_id: str = "caveo_automotive", count: int = 5):
    """Inject a burst of high-token calls to trigger anomaly detection."""
    utils.info(f"Injecting token spike for {tenant_id} ({count} calls)...")
    for i in range(count):
        result = await send_call(token=token, gen=gen, tenant_id=tenant_id, force_anomaly=True)
        if result and result.get("_api_result"):
            utils.call_log(
                index=i + 1, total=count,
                tenant=result["tenant_id"], agent=result["agent_id"],
                decision=result["_api_result"].get("decision", "?"),
                tokens=result["total_tokens"],
                cost=float(result["cost_usd"]),
                latency=result["duration_ms"],
            )
        await asyncio.sleep(0.5)


async def scenario_budget_block(token: str, gen: DataGenerator, tenant_id: str = "general_beton"):
    """Send calls until a budget cap block occurs."""
    utils.info(f"Sending calls to trigger budget block for {tenant_id}...")

    for i in range(20):
        result = await send_call(token=token, gen=gen, tenant_id=tenant_id)
        if result and result.get("_api_result"):
            decision = result["_api_result"].get("decision", "?")
            utils.call_log(
                index=i + 1, total=20,
                tenant=result["tenant_id"], agent=result["agent_id"],
                decision=decision,
                tokens=result["total_tokens"],
                cost=float(result["cost_usd"]),
                latency=result["duration_ms"],
            )
            if decision == "block":
                utils.governance_alert(
                    tenant=tenant_id,
                    decision="block",
                    model=result["model"],
                    reason="Budget cap exceeded — call blocked by governance",
                )
                return
        await asyncio.sleep(0.3)

    utils.warn("Budget cap not triggered in 20 calls — governance may need stricter limits")


async def scenario_model_downgrade(token: str, gen: DataGenerator, tenant_id: str = "caveo_automotive"):
    """Request a blocked model to show downgrade behavior."""
    utils.info(f"Requesting blocked model (gpt-4) for {tenant_id}...")
    result = await send_call(
        token=token, gen=gen,
        tenant_id=tenant_id,
        model_override="gpt-4",
    )
    if result and result.get("_api_result"):
        api_result = result["_api_result"]
        decision = api_result.get("decision", "?")
        utils.governance_alert(
            tenant=tenant_id,
            decision=decision,
            model="gpt-4",
            reason=api_result.get("error", "Model blocked by governance — GPT-4 not allowed"),
        )


async def scenario_error_burst(token: str, gen: DataGenerator, tenant_id: str = "seamtech_lakatech", count: int = 5):
    """Inject a burst of error calls."""
    utils.info(f"Injecting error burst for {tenant_id} ({count} calls)...")
    for i in range(count):
        result = await send_call(token=token, gen=gen, tenant_id=tenant_id, force_error=True)
        if result and result.get("_api_result"):
            utils.call_log(
                index=i + 1, total=count,
                tenant=result["tenant_id"], agent=result["agent_id"],
                decision=result["_api_result"].get("decision", "?"),
                tokens=result["total_tokens"],
                cost=float(result["cost_usd"]),
                latency=result["duration_ms"],
            )
        await asyncio.sleep(0.3)


async def scenario_generate_invoice(token: str, tenant_id: str = "caveo_automotive"):
    """Trigger billing invoice generation for current month."""
    year_month = int(datetime.now(timezone.utc).strftime("%Y%m"))
    utils.info(f"Generating invoice for {tenant_id} month {year_month}...")

    result = await api_post(
        token, f"/billing/generate/{tenant_id}/{year_month}", {}
    )
    if result:
        utils.success(
            f"Invoice generated: ${result.get('total_billed_usd', '?')} "
            f"({result.get('total_calls', '?')} calls, status={result.get('status', '?')})"
        )
    else:
        utils.warn("Invoice generation did not return data (may need more traffic first)")
