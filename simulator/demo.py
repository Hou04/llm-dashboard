"""
Phase 3: Demo Orchestrator
=============================
A guided, step-by-step demo designed for presenting to the jury.
Each act demonstrates a specific platform feature with narration.

Usage:
    python -m simulator --mode demo
"""

import asyncio
import time
from datetime import datetime, timezone

from simulator.config import API_BASE_URL, ADMIN_USER, ADMIN_PASS, TENANTS
from simulator.data_generator import DataGenerator
from simulator.live import (
    authenticate,
    send_call,
    api_post,
    api_get,
    scenario_token_spike,
    scenario_budget_block,
    scenario_model_downgrade,
    scenario_error_burst,
    scenario_generate_invoice,
)
from simulator import utils


async def run_demo():
    """Run the full 7-act guided demo."""
    utils.banner("TIM TUNISIA — AI GOVERNANCE PLATFORM\n             LIVE DEMO FOR JURY", char="═", width=64)

    print(f"  {utils.C.DIM}This guided demo will walk you through every feature of the")
    print(f"  AI Governance Platform. Each act demonstrates a key capability.")
    print(f"  Open the dashboard at: {utils.C.CYAN}http://localhost:8000/dashboard{utils.C.RESET}")
    print(f"  {utils.C.DIM}Press ENTER between acts to proceed at your own pace.{utils.C.RESET}\n")

    # Authenticate
    utils.section("Connecting to Platform")
    try:
        token = await authenticate()
        utils.success(f"Authenticated as {ADMIN_USER}")
        utils.success(f"API: {API_BASE_URL}")
    except Exception as e:
        utils.error(f"Cannot connect to the platform: {e}")
        utils.error("Make sure the server is running:")
        utils.info("  pipenv run uvicorn main:app --reload --port 8000")
        return

    gen = DataGenerator(seed=int(time.time()))

    # ── Verify data exists ──
    exec_data = await api_get(token, "/dashboard/executive")
    if not exec_data or not exec_data.get("tenants"):
        utils.warn("No historical data found. Running quick seed first...")
        utils.info("Tip: Run 'python -m simulator --mode seed' beforehand for full data\n")

    utils.wait_for_enter("Dashboard ready? Press ENTER to start the demo...")

    # ============================================================
    # ACT 1: THE GATEWAY — Normal Traffic
    # ============================================================
    utils.demo_act(
        1,
        "THE AI GATEWAY — Real-time Traffic",
        "Watch as AI calls flow through the gateway. Each call is logged,\n"
        "  governance rules are evaluated, and the dashboard updates live."
    )
    utils.info("👀 Watch the dashboard: KPI cards and activity feed should update in real-time\n")
    utils.wait_for_enter("Press ENTER to start sending traffic...")

    print(
        f"  {'#':>5s}  {'Time':8s}  {'Decision':15s}  {'Tenant':20s}  "
        f"{'Agent':20s}  {'Tokens':>8s}  {'Cost':>10s}  {'Latency':>7s}"
    )
    print(f"  {'─' * 100}")

    for i in range(10):
        result = await send_call(token=token, gen=gen)
        if result and result.get("_api_result"):
            utils.call_log(
                index=i + 1, total=10,
                tenant=result["tenant_id"], agent=result["agent_id"],
                decision=result["_api_result"].get("decision", "?"),
                tokens=result["total_tokens"],
                cost=float(result["cost_usd"]),
                latency=result["duration_ms"],
            )
        await asyncio.sleep(1.5)

    utils.success("\n✓ 10 calls processed through the AI Gateway")
    utils.info("📊 Dashboard should show: updated call count, token usage, cost metrics")

    # ============================================================
    # ACT 2: ANOMALY DETECTION — Token Spike
    # ============================================================
    utils.wait_for_enter()
    utils.demo_act(
        2,
        "ANOMALY DETECTION — Token Spike Alert",
        "We'll inject abnormally high token usage for CAVEO Automotive.\n"
        "  The platform's 3 ML detectors (STL, IsolationForest, CUSUM) will fire.\n"
        "  A CRITICAL alert will appear on the dashboard via WebSocket."
    )
    utils.info("👀 Watch: Alert bell icon, anomaly panel, and toast notification\n")
    utils.wait_for_enter("Press ENTER to trigger the anomaly...")

    print(
        f"  {'#':>5s}  {'Time':8s}  {'Decision':15s}  {'Tenant':20s}  "
        f"{'Agent':20s}  {'Tokens':>8s}  {'Cost':>10s}  {'Latency':>7s}"
    )
    print(f"  {'─' * 100}")

    await scenario_token_spike(token, gen, tenant_id="caveo_automotive", count=6)

    utils.anomaly_alert(
        tenant="caveo_automotive",
        anomaly_type="token_spike",
        severity="critical",
        description="Utilisation anormale détectée: 3.5x la consommation normale. "
                    "3 détecteurs ont voté: STL, IsolationForest, CUSUM."
    )
    utils.info("📊 Dashboard should show: red anomaly card, alert toast, updated anomaly feed")

    # ============================================================
    # ACT 3: GOVERNANCE — Budget Enforcement
    # ============================================================
    utils.wait_for_enter()
    utils.demo_act(
        3,
        "GOVERNANCE — Budget Cap Enforcement",
        "Général Béton (pay-as-you-go) has a $150/month budget cap.\n"
        "  We'll send traffic until the governance engine BLOCKS the call.\n"
        "  This shows the platform protecting against budget overruns."
    )
    utils.info("👀 Watch: Governance decisions panel — look for BLOCK decisions\n")
    utils.wait_for_enter("Press ENTER to trigger budget enforcement...")

    print(
        f"  {'#':>5s}  {'Time':8s}  {'Decision':15s}  {'Tenant':20s}  "
        f"{'Agent':20s}  {'Tokens':>8s}  {'Cost':>10s}  {'Latency':>7s}"
    )
    print(f"  {'─' * 100}")

    await scenario_budget_block(token, gen, tenant_id="general_beton")

    utils.info("📊 Dashboard should show: blocked call count increased, governance log updated")

    # ============================================================
    # ACT 4: GOVERNANCE — Model Downgrade
    # ============================================================
    utils.wait_for_enter()
    utils.demo_act(
        4,
        "GOVERNANCE — Model Blocking & Downgrade",
        "GPT-4 is blocked globally (too expensive). When a tenant requests it,\n"
        "  the governance engine blocks the call and logs the decision.\n"
        "  This demonstrates cost control without breaking applications."
    )
    utils.info("👀 Watch: Governance decisions — BLOCK decision for GPT-4\n")
    utils.wait_for_enter("Press ENTER to request the blocked model...")

    await scenario_model_downgrade(token, gen, tenant_id="caveo_automotive")

    utils.info("📊 Dashboard should show: model block decision in governance audit log")

    # ============================================================
    # ACT 5: BILLING — Invoice Generation
    # ============================================================
    utils.wait_for_enter()
    utils.demo_act(
        5,
        "BILLING — Invoice Generation",
        "The platform generates professional invoices with full transparency.\n"
        "  Each invoice has line items showing exactly what was consumed.\n"
        "  Supports forfait contracts with overage billing."
    )
    utils.info("👀 Watch: Billing section — invoice with line items and PDF download\n")
    utils.wait_for_enter("Press ENTER to generate the invoice...")

    # Generate for all tenants
    year_month = int(datetime.now(timezone.utc).strftime("%Y%m"))
    for tid in TENANTS:
        await scenario_generate_invoice(token, tenant_id=tid)
        await asyncio.sleep(0.5)

    utils.info("📊 Dashboard should show: invoices with breakdown by model, forfait usage, overage")

    # ============================================================
    # ACT 6: FORECASTING — Budget Prediction
    # ============================================================
    utils.wait_for_enter()
    utils.demo_act(
        6,
        "FORECASTING — 30-Day Budget Prediction",
        "The platform forecasts token usage and costs for the next 30 days.\n"
        "  Three scenarios: optimistic, likely, and pessimistic.\n"
        "  Budget risk alerts warn when a tenant will exceed their limit."
    )
    utils.info("👀 Watch: Forecast chart with 3 scenario lines and budget risk alerts\n")
    utils.wait_for_enter("Press ENTER to view forecast data...")

    for tid in TENANTS:
        forecast = await api_get(token, f"/forecasting/forecast/{tid}")
        if forecast and forecast.get("daily"):
            days = len(forecast["daily"])
            utils.success(f"{tid}: {days}-day forecast loaded")
        else:
            utils.dim(f"{tid}: forecast data available in dashboard (pre-seeded)")

    risk = await api_get(token, "/forecasting/budget-risk")
    if risk and risk.get("risks"):
        for r in risk["risks"]:
            utils.warn(
                f"⚠️ {r['tenant_id']}: budget exhaustion in {r['days_until_exhaustion']} days "
                f"({r.get('urgency', 'watch')})"
            )
    else:
        utils.info("Budget risk data available in dashboard (pre-seeded)")

    utils.info("📊 Dashboard should show: forecast charts, budget risk warnings")

    # ============================================================
    # ACT 7: ANALYTICS & AI ASSISTANT
    # ============================================================
    utils.wait_for_enter()
    utils.demo_act(
        7,
        "ANALYTICS & AI ASSISTANT",
        "The executive dashboard shows a complete overview of all tenants.\n"
        "  The AI Assistant (M8) answers natural language questions about costs,\n"
        "  anomalies, and governance — powered by Groq/Llama (free)."
    )
    utils.info("👀 Watch: Executive overview, cost breakdown charts, AI assistant panel\n")
    utils.wait_for_enter("Press ENTER to query the executive dashboard...")

    exec_overview = await api_get(token, "/dashboard/executive")
    if exec_overview and exec_overview.get("tenants"):
        utils.success(f"Executive overview: {len(exec_overview['tenants'])} tenants loaded")
        summary = exec_overview.get("summary", {})
        utils.info(f"  Total cost: ${summary.get('total_cost_usd', '?')}")
        total_calls = summary.get('total_calls', '?')
        total_calls_str = f"{total_calls:,}" if isinstance(total_calls, int) else str(total_calls)
        utils.info(f"  Total calls: {total_calls_str}")
        utils.info(f"  Active anomalies: {summary.get('active_anomalies', '?')}")

    # Query AI assistant
    utils.info("\nAsking AI Assistant: 'Quel tenant dépense le plus ce mois-ci ?'")
    assistant = await api_get(
        token,
        "/dashboard/assistant/ask?question=Quel%20tenant%20d%C3%A9pense%20le%20plus%20ce%20mois-ci%20%3F"
    )
    if assistant and assistant.get("answer"):
        print(f"\n  {utils.C.CYAN}🤖 AI Assistant:{utils.C.RESET}")
        # Print answer wrapped to ~70 chars
        answer = assistant["answer"]
        for line in answer.split("\n")[:8]:
            print(f"     {line}")
    else:
        utils.info("AI Assistant requires Groq API key for live answers (demo shows the UI)")

    # ============================================================
    # FINALE
    # ============================================================
    utils.wait_for_enter()
    utils.banner("DEMO COMPLETE", char="═", width=64)

    print(f"  {utils.C.BOLD}Features demonstrated:{utils.C.RESET}")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} AI Gateway — real-time call logging and routing")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Anomaly Detection — 3 ML detectors (STL, IF, CUSUM)")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Governance — budget caps, model blocking, decisions audit")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Billing — invoices, line items, contracts, PDF export")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Forecasting — 30-day predictions, budget risk alerts")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Analytics — cost breakdown, trends, agent analysis")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Real-time Dashboard — WebSocket push, live KPIs")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Multi-tenant — 3 clients with different contract types")
    print(f"  {utils.C.GREEN}✓{utils.C.RESET} Security — JWT auth, tenant isolation, audit logs")
    print()
    utils.info("Thank you for watching the demonstration! 🎓\n")
