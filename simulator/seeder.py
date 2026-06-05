"""
Phase 1: Historical Data Seeder
=================================
Populates the database with 60 days of realistic historical data.
Direct DB insertion for speed — bypasses the API.

Usage:
    python -m simulator --mode seed
"""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from collections import defaultdict

from simulator.config import (
    TENANTS,
    GOVERNANCE_RULES,
    PROVIDERS,
    MODELS,
    ANOMALY_DAYS,
    HISTORY_DAYS,
)
from simulator.data_generator import DataGenerator
from simulator import utils


async def run_seed():
    """Execute the full historical data seeding pipeline."""
    utils.banner("TIM TUNISIA — DATA SEEDER")
    utils.info(f"Generating {HISTORY_DAYS} days of historical data for {len(TENANTS)} tenants")
    utils.info("Direct DB insertion (no API calls) — this is fast\n")

    # Import DB dependencies (deferred to avoid import at module level)
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dotenv import load_dotenv
    load_dotenv()

    from core.database import AsyncSessionLocal
    from sqlalchemy import text

    gen = DataGenerator()

    async with AsyncSessionLocal() as session:
        # ── Step 1: Create Tenants ──
        await _seed_tenants(session)

        # ── Step 2: Governance Rules ──
        rule_ids = await _seed_governance_rules(session)

        # ── Step 3: Provider Health ──
        await _seed_provider_health(session)

        # ── Step 4: Model Pricing ──
        await _seed_model_pricing(session)

        # ── Step 5: Contracts ──
        await _seed_contracts(session)

        # ── Step 6: 60 Days of LLM Logs ──
        all_calls, daily_stats = await _seed_call_logs(session, gen, rule_ids)

        # ── Step 7: Detection Baselines ──
        await _seed_baselines(session, gen, daily_stats)

        # ── Step 8: Anomaly Records ──
        await _seed_anomalies(session, daily_stats)

        # ── Step 9: Forecasts & Budget Risks ──
        await _seed_forecasts(session, gen, daily_stats)

        # ── Step 10: Billing Invoices ──
        await _seed_billing(session, gen, all_calls)

        # ── Step 11: M5 Optimizer Analysis ──
        await _seed_optimizer(session)

        await session.commit()

    utils.banner("SEEDING COMPLETE", char="─")
    utils.success(f"Historical data for {HISTORY_DAYS} days successfully created")
    utils.info("Start the server and open the dashboard to see the data\n")


# ============================================================
# STEP 1: TENANTS
# ============================================================

async def _seed_tenants(session):
    utils.section("Step 1/10 — Creating Tenants")

    from sqlalchemy import text
    # Check existing
    result = await session.execute(text("SELECT tenant_id FROM llm_tenants"))
    existing = {row[0] for row in result.fetchall()}

    for tid, cfg in TENANTS.items():
        if tid in existing:
            utils.dim(f"{tid}: already exists, skipping")
            continue

        await session.execute(
            text("""
                INSERT INTO llm_tenants (tenant_id, name, tier, monthly_budget_usd, contact_email, created_at, updated_at)
                VALUES (:tid, :name, :tier, :budget, :email, :now, :now)
            """),
            {
                "tid": tid,
                "name": cfg["name"],
                "tier": cfg["tier"],
                "budget": cfg["budget"],
                "email": cfg["contact"],
                "now": datetime.now(timezone.utc),
            },
        )
        utils.success(f"{tid}: {cfg['name']} ({cfg['tier']})")

    await session.flush()


# ============================================================
# STEP 2: GOVERNANCE RULES
# ============================================================

async def _seed_governance_rules(session) -> dict:
    utils.section("Step 2/10 — Creating Governance Rules")

    from sqlalchemy import text
    rule_ids = {}

    for rule in GOVERNANCE_RULES:
        rid = uuid.uuid4()
        rule_ids[rule["description"][:30]] = rid

        await session.execute(
            text("""
                INSERT INTO llm_governance_rules
                (id, rule_type, tenant_id, model_name, daily_token_limit,
                 monthly_budget_usd, priority, is_active, description, created_at, updated_at)
                VALUES (:id, :rt, :tid, :mn, :dtl, :mbu, :pri, :active, :desc, :now, :now)
            """),
            {
                "id": str(rid),
                "rt": rule["rule_type"],
                "tid": rule.get("tenant_id"),
                "mn": rule.get("model_name"),
                "dtl": rule.get("daily_token_limit"),
                "mbu": rule.get("monthly_budget_usd"),
                "pri": rule["priority"],
                "active": True,
                "desc": rule["description"],
                "now": datetime.now(timezone.utc),
            },
        )
        utils.success(f"{rule['rule_type']}: {rule['description'][:60]}")

    await session.flush()
    return rule_ids


# ============================================================
# STEP 3: PROVIDER HEALTH
# ============================================================

async def _seed_provider_health(session):
    utils.section("Step 3/10 — Seeding Provider Health")

    from sqlalchemy import text
    # Clear existing
    await session.execute(text("DELETE FROM llm_provider_status"))

    for name, cfg in PROVIDERS.items():
        await session.execute(
            text("""
                INSERT INTO llm_provider_status
                (provider_name, status, latency_ms, uptime_pct, last_check_at)
                VALUES (:name, :status, :lat, :up, :now)
            """),
            {
                "name": name,
                "status": cfg["status"],
                "lat": cfg["latency_ms"],
                "up": cfg["uptime_pct"],
                "now": datetime.now(timezone.utc),
            },
        )
        icon = "🟢" if cfg["status"] == "online" else "🟡"
        utils.success(f"{icon} {name}: {cfg['status']} ({cfg['latency_ms']}ms, {cfg['uptime_pct']}%)")

    await session.flush()


# ============================================================
# STEP 4: MODEL PRICING
# ============================================================

async def _seed_model_pricing(session):
    utils.section("Step 4/10 — Seeding Model Pricing")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_model_pricing"))

    for model_name, cfg in MODELS.items():
        await session.execute(
            text("""
                INSERT INTO llm_model_pricing
                (id, model, provider, input_price_per_1k, output_price_per_1k, valid_from, created_at)
                VALUES (:id, :model, :provider, :inp, :out, :vf, :now)
            """),
            {
                "id": str(uuid.uuid4()),
                "model": model_name,
                "provider": cfg["provider"],
                "inp": cfg["input_price_per_1k"],
                "out": cfg["output_price_per_1k"],
                "vf": (datetime.now(timezone.utc) - timedelta(days=90)).date(),
                "now": datetime.now(timezone.utc),
            },
        )
        utils.success(f"{model_name}: in=${cfg['input_price_per_1k']}/1K, out=${cfg['output_price_per_1k']}/1K")

    await session.flush()


# ============================================================
# STEP 5: CONTRACTS
# ============================================================

async def _seed_contracts(session):
    utils.section("Step 5/10 — Creating Billing Contracts")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_tenant_contracts"))

    for tid, cfg in TENANTS.items():
        ct = cfg["contract"]
        await session.execute(
            text("""
                INSERT INTO llm_tenant_contracts
                (id, tenant_id, contract_type, base_fee_usd, forfait_tokens,
                 overage_rate_per_1k, currency, is_active, status, description, created_at, updated_at)
                VALUES (:id, :tid, :ct, :bf, :ft, :or1k, :cur, :active, :status, :desc, :now, :now)
            """),
            {
                "id": str(uuid.uuid4()),
                "tid": tid,
                "ct": ct["type"],
                "bf": ct["base_fee"],
                "ft": ct["tokens"],
                "or1k": ct["overage"],
                "cur": "USD",
                "active": True,
                "status": "active",
                "desc": f"Contrat {ct['type']} — {cfg['name']}",
                "now": datetime.now(timezone.utc),
            },
        )
        utils.success(f"{tid}: {ct['type']} (base=${ct['base_fee']}, forfait={ct['tokens']:,} tokens)")

    await session.flush()


# ============================================================
# STEP 6: LLM CALL LOGS (the big one)
# ============================================================

async def _seed_call_logs(session, gen: DataGenerator, rule_ids: dict):
    utils.section(f"Step 6/10 — Generating {HISTORY_DAYS} Days of LLM Call Logs")

    from sqlalchemy import text

    # Clear existing simulator data
    await session.execute(text(
        "DELETE FROM llm_governance_decisions WHERE request_id LIKE 'req_%'"
    ))
    await session.execute(text(
        "DELETE FROM llm_token_log WHERE trace_id IS NOT NULL"
    ))
    await session.flush()

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=HISTORY_DAYS)

    all_calls = defaultdict(list)  # {(tenant_id, year_month): [calls]}
    daily_stats = defaultdict(lambda: defaultdict(lambda: {"tokens": 0, "cost": 0.0, "count": 0}))
    total_inserted = 0

    # First rule id for governance decisions
    first_rule_id = next(iter(rule_ids.values()), None)

    for day_offset in range(HISTORY_DAYS + 1):
        current_day = start_date + timedelta(days=day_offset)
        day_calls = []

        for tid in TENANTS:
            calls = gen.generate_day(tid, current_day, day_offset)
            day_calls.extend(calls)

            # Track daily stats per tenant
            day_tokens = sum(c["total_tokens"] for c in calls)
            day_cost = sum(float(c["cost_usd"]) for c in calls)
            daily_stats[tid][current_day] = {
                "tokens": day_tokens,
                "cost": day_cost,
                "count": len(calls),
            }

            # Track for billing
            ym = int(current_day.strftime("%Y%m"))
            all_calls[(tid, ym)].extend(calls)

        # Batch insert calls for this day
        for call in day_calls:
            await session.execute(
                text("""
                    INSERT INTO llm_token_log
                    (id, request_id, trace_id, tenant_id, agent_id, module,
                     model, provider, input_tokens, output_tokens, total_tokens,
                     cost_usd, duration_ms, status, error_message,
                     prompt_text, completion_text, pii_redacted, metadata, created_at)
                    VALUES (:id, :rid, :trc, :tid, :aid, :mod,
                            :model, :prov, :inp, :out, :tot,
                            :cost, :dur, :st, :err,
                            :pt, :ct, :pii, :meta, :cat)
                """),
                {
                    "id": str(call["id"]),
                    "rid": call["request_id"],
                    "trc": call["trace_id"],
                    "tid": call["tenant_id"],
                    "aid": call["agent_id"],
                    "mod": call["module"],
                    "model": call["model"],
                    "prov": call["provider"],
                    "inp": call["input_tokens"],
                    "out": call["output_tokens"],
                    "tot": call["total_tokens"],
                    "cost": str(call["cost_usd"]),
                    "dur": call["duration_ms"],
                    "st": call["status"],
                    "err": call["error_message"],
                    "pt": call["prompt_text"],
                    "ct": call["completion_text"],
                    "pii": False,
                    "meta": '{"source": "simulator"}',
                    "cat": call["created_at"],
                },
            )

            # Insert governance decision (for allow/block/downgrade)
            decision = "allow"
            if call["status"] == "blocked":
                decision = "block"

            gov = gen.generate_governance_decision(
                call=call,
                decision=decision,
                rule_id=first_rule_id,
                tokens_today=daily_stats[call["tenant_id"]][current_day]["tokens"],
                budget_today=daily_stats[call["tenant_id"]][current_day]["cost"],
            )
            await session.execute(
                text("""
                    INSERT INTO llm_governance_decisions
                    (id, request_id, tenant_id, model_requested, model_used,
                     decision, reason, rule_id, tokens_used_today, budget_used_today, evaluated_at)
                    VALUES (:id, :rid, :tid, :mr, :mu, :dec, :reason, :rul, :tut, :but, :ea)
                """),
                {
                    "id": str(gov["id"]),
                    "rid": gov["request_id"],
                    "tid": gov["tenant_id"],
                    "mr": gov["model_requested"],
                    "mu": gov["model_used"],
                    "dec": gov["decision"],
                    "reason": gov["reason"],
                    "rul": str(gov["rule_id"]) if gov["rule_id"] else None,
                    "tut": gov["tokens_used_today"],
                    "but": str(gov["budget_used_today"]),
                    "ea": gov["evaluated_at"],
                },
            )

        total_inserted += len(day_calls)

        # Progress bar
        utils.progress_bar(day_offset + 1, HISTORY_DAYS + 1, f"Day {day_offset+1}/{HISTORY_DAYS+1} — {len(day_calls)} calls")

        # Flush every 10 days to avoid memory buildup
        if day_offset % 10 == 0:
            await session.flush()

    await session.flush()
    utils.success(f"Total: {total_inserted:,} call logs + governance decisions inserted")

    return all_calls, daily_stats


# ============================================================
# STEP 7: DETECTION BASELINES
# ============================================================

async def _seed_baselines(session, gen: DataGenerator, daily_stats: dict):
    utils.section("Step 7/10 — Computing Detection Baselines")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_token_baseline"))

    for tid in TENANTS:
        tenant_daily = daily_stats[tid]
        daily_totals = [v["tokens"] for v in tenant_daily.values()]
        daily_costs = [v["cost"] for v in tenant_daily.values()]
        daily_counts = [v["count"] for v in tenant_daily.values()]

        baseline = gen.generate_baseline(tid, daily_totals, daily_costs, daily_counts)

        await session.execute(
            text("""
                INSERT INTO llm_token_baseline
                (id, tenant_id, agent_id, model,
                 daily_mean, daily_std_dev, daily_min, daily_max,
                 call_count_mean, call_count_std_dev,
                 daily_cost_mean, daily_cost_std_dev,
                 error_rate_mean, cusum_pos, cusum_neg, cusum_k,
                 isolation_forest_threshold, sample_days, computed_at,
                 baseline_from, baseline_to)
                VALUES (:id, :tid, :aid, :model,
                        :dm, :dsd, :dmin, :dmax,
                        :ccm, :ccsd,
                        :dcm, :dcsd,
                        :erm, :cp, :cn, :ck,
                        :ift, :sd, :ca, :bf, :bt)
            """),
            {
                "id": str(baseline["id"]),
                "tid": baseline["tenant_id"],
                "aid": baseline["agent_id"],
                "model": baseline["model"],
                "dm": baseline["daily_mean"],
                "dsd": baseline["daily_std_dev"],
                "dmin": baseline["daily_min"],
                "dmax": baseline["daily_max"],
                "ccm": baseline["call_count_mean"],
                "ccsd": baseline["call_count_std_dev"],
                "dcm": baseline["daily_cost_mean"],
                "dcsd": baseline["daily_cost_std_dev"],
                "erm": baseline["error_rate_mean"],
                "cp": baseline["cusum_pos"],
                "cn": baseline["cusum_neg"],
                "ck": baseline["cusum_k"],
                "ift": baseline["isolation_forest_threshold"],
                "sd": baseline["sample_days"],
                "ca": baseline["computed_at"],
                "bf": baseline["baseline_from"],
                "bt": baseline["baseline_to"],
            },
        )
        utils.success(
            f"{tid}: mean={baseline['daily_mean']:,.0f} tokens/day, "
            f"σ={baseline['daily_std_dev']:,.0f}, "
            f"samples={baseline['sample_days']} days"
        )

    await session.flush()


# ============================================================
# STEP 8: ANOMALY RECORDS
# ============================================================

async def _seed_anomalies(session, daily_stats: dict):
    utils.section("Step 8/10 — Injecting Anomaly Records")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_anomaly"))

    for anomaly in ANOMALY_DAYS:
        tid = anomaly["tenant_id"]
        anomaly_date = (datetime.now(timezone.utc) - timedelta(days=anomaly["days_ago"])).date()

        # Get actual stats for that day
        day_data = daily_stats[tid].get(anomaly_date, {"tokens": 50000, "cost": 5.0})

        # Baseline stats for context
        all_daily = [v["tokens"] for v in daily_stats[tid].values()]
        import statistics
        baseline_mean = statistics.mean(all_daily) if all_daily else 50000
        baseline_std = statistics.stdev(all_daily) if len(all_daily) > 1 else 10000

        await session.execute(
            text("""
                INSERT INTO llm_anomaly
                (id, tenant_id, agent_id, model,
                 anomaly_type, severity, detector_votes, vote_count,
                 observed_value, baseline_mean, baseline_std_dev,
                 description, resolved, detected_at)
                VALUES (:id, :tid, :aid, :model,
                        :at, :sev, :dv, :vc,
                        :ov, :bm, :bsd,
                        :desc, :resolved, :det)
            """),
            {
                "id": str(uuid.uuid4()),
                "tid": tid,
                "aid": "*",
                "model": "*",
                "at": anomaly["type"],
                "sev": anomaly["severity"],
                "dv": anomaly["detectors"],
                "vc": len(anomaly["detectors"].split(",")),
                "ov": float(day_data["tokens"]),
                "bm": baseline_mean,
                "bsd": baseline_std,
                "desc": anomaly["description"],
                "resolved": anomaly["days_ago"] > 10,  # Older anomalies are resolved
                "det": datetime(anomaly_date.year, anomaly_date.month, anomaly_date.day, 14, 30, 0, tzinfo=timezone.utc),
            },
        )

        sev_icon = {"critical": "🔴", "high": "🟠", "warning": "🟡"}.get(anomaly["severity"], "⚪")
        utils.success(
            f"{sev_icon} {tid}: {anomaly['type']} ({anomaly['severity']}) — "
            f"{anomaly['days_ago']} days ago"
        )

    await session.flush()


# ============================================================
# STEP 9: FORECASTS & BUDGET RISKS
# ============================================================

async def _seed_forecasts(session, gen: DataGenerator, daily_stats: dict):
    utils.section("Step 9/11 — Generating Forecasts & Budget Risks")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_forecast"))
    await session.execute(text("DELETE FROM llm_budget_risk"))

    from modules.forecasting.services.forecasting_service import ForecastingService
    forecaster = ForecastingService(session)

    for tid in TENANTS:
        res = await forecaster.generate_forecast_for_tenant(tid, horizon_days=30, lookback_days=60)
        if res:
            utils.success(f"{tid}: Forecast generated (trend: {res.trend_slope}/day)")
        else:
            utils.warn(f"{tid}: Failed to generate forecast (insufficient history)")

    await session.flush()


# ============================================================
# STEP 11: M5 OPTIMIZER
# ============================================================

async def _seed_optimizer(session):
    utils.section("Step 11/11 — Running M5 Optimizer Analysis")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_prompt_optimization"))
    await session.execute(text("DELETE FROM llm_model_recommendation"))

    from modules.forecasting.services.optimizer_service import OptimizerService
    optimizer = OptimizerService(session)

    for tid in TENANTS:
        res = await optimizer.run_optimization_for_tenant(tid, lookback_days=30)
        recs = len(res.get("model_recommendations", []))
        opts = len(res.get("prompt_optimizations", []))
        savings = res.get("total_projected_monthly_saving_usd", "0")
        utils.success(f"{tid}: {recs} model recs, {opts} prompt opts -> ${savings} saved/mo")

    await session.flush()


# ============================================================
# STEP 10: BILLING INVOICES
# ============================================================

async def _seed_billing(session, gen: DataGenerator, all_calls: dict):
    utils.section("Step 10/10 — Generating Billing Invoices")

    from sqlalchemy import text
    await session.execute(text("DELETE FROM llm_client_reports"))
    await session.execute(text("DELETE FROM llm_billing_line_items"))
    await session.execute(text("DELETE FROM llm_billing_monthly"))

    # Group calls by (tenant, year_month)
    for (tid, ym), calls in all_calls.items():
        if not calls:
            continue

        invoice, line_items, report = gen.generate_billing(tid, ym, calls)

        # Insert invoice
        await session.execute(
            text("""
                INSERT INTO llm_billing_monthly
                (id, tenant_id, year_month,
                 total_calls, total_tokens, total_input_tokens, total_output_tokens,
                 raw_cost_usd, forfait_tokens_included, forfait_tokens_used,
                 overage_tokens, base_fee_usd, overage_charge_usd,
                 credits_usd, total_billed_usd, status, notes,
                 snapshot_taken_at, finalized_at, created_at)
                VALUES (:id, :tid, :ym,
                        :tc, :tt, :ti, :to,
                        :rc, :fti, :ftu,
                        :ot, :bf, :oc,
                        :cr, :tb, :st, :notes,
                        :snap, :fin, :ca)
            """),
            {
                "id": str(invoice["id"]),
                "tid": invoice["tenant_id"],
                "ym": invoice["year_month"],
                "tc": invoice["total_calls"],
                "tt": invoice["total_tokens"],
                "ti": invoice["total_input_tokens"],
                "to": invoice["total_output_tokens"],
                "rc": str(invoice["raw_cost_usd"]),
                "fti": invoice["forfait_tokens_included"],
                "ftu": invoice["forfait_tokens_used"],
                "ot": invoice["overage_tokens"],
                "bf": str(invoice["base_fee_usd"]),
                "oc": str(invoice["overage_charge_usd"]),
                "cr": str(invoice["credits_usd"]),
                "tb": str(invoice["total_billed_usd"]),
                "st": invoice["status"],
                "notes": invoice["notes"],
                "snap": invoice["snapshot_taken_at"],
                "fin": invoice["finalized_at"],
                "ca": invoice["created_at"],
            },
        )

        # Insert line items
        for li in line_items:
            await session.execute(
                text("""
                    INSERT INTO llm_billing_line_items
                    (id, billing_id, tenant_id, year_month,
                     line_type, description, model, provider, agent_id,
                     quantity_tokens, quantity_calls,
                     unit_price_usd, amount_usd, credit_type, created_at)
                    VALUES (:id, :bid, :tid, :ym,
                            :lt, :desc, :model, :prov, :aid,
                            :qt, :qc,
                            :up, :amt, :ct, :ca)
                """),
                {
                    "id": str(li["id"]),
                    "bid": str(li["billing_id"]),
                    "tid": li["tenant_id"],
                    "ym": li["year_month"],
                    "lt": li["line_type"],
                    "desc": li["description"],
                    "model": li["model"],
                    "prov": li["provider"],
                    "aid": li["agent_id"],
                    "qt": li["quantity_tokens"],
                    "qc": li["quantity_calls"],
                    "up": str(li["unit_price_usd"]),
                    "amt": str(li["amount_usd"]),
                    "ct": li["credit_type"],
                    "ca": li["created_at"],
                },
            )

        # Insert client report
        await session.execute(
            text("""
                INSERT INTO llm_client_reports
                (id, tenant_id, year_month, billing_id,
                 report_title, executive_summary, report_data, status, generated_at)
                VALUES (:id, :tid, :ym, :bid,
                        :rt, :es, :rd, :st, :ga)
            """),
            {
                "id": str(report["id"]),
                "tid": report["tenant_id"],
                "ym": report["year_month"],
                "bid": str(report["billing_id"]),
                "rt": report["report_title"],
                "es": report["executive_summary"],
                "rd": report["report_data"],
                "st": report["status"],
                "ga": report["generated_at"],
            },
        )

        ym_str = f"{str(ym)[:4]}-{str(ym)[4:]}"
        utils.success(
            f"{tid} [{ym_str}]: {invoice['total_calls']:,} calls, "
            f"${float(invoice['total_billed_usd']):.2f} billed ({invoice['status']})"
        )

    await session.flush()


# ============================================================
# RESET — Wipe all simulator data
# ============================================================

async def run_reset():
    """Wipe all data from the database for a fresh start."""
    utils.banner("RESETTING DATABASE")
    utils.warn("This will delete ALL data from the platform tables\n")

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dotenv import load_dotenv
    load_dotenv()

    from core.database import AsyncSessionLocal
    from sqlalchemy import text

    tables = [
        "llm_client_reports",
        "llm_billing_line_items",
        "llm_billing_monthly",
        "llm_budget_risk",
        "llm_forecast",
        "llm_anomaly",
        "llm_token_baseline",
        "llm_governance_decisions",
        "llm_token_log",
        "llm_provider_status",
        "llm_model_pricing",
        "llm_tenant_contracts",
        "llm_tenants",
    ]

    async with AsyncSessionLocal() as session:
        for table in tables:
            try:
                result = await session.execute(text(f"DELETE FROM {table}"))
                utils.success(f"Cleared {table}")
            except Exception as e:
                utils.warn(f"Could not clear {table}: {e}")
        await session.commit()

    utils.success("\nAll data cleared. Run --mode seed to repopulate.")


# ============================================================
# VERIFY — Check data integrity
# ============================================================

async def run_verify():
    """Verify that seeded data exists and is consistent."""
    utils.banner("VERIFYING DATA")

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dotenv import load_dotenv
    load_dotenv()

    from core.database import AsyncSessionLocal
    from sqlalchemy import text

    checks = [
        ("llm_tenants", "SELECT COUNT(*) FROM llm_tenants", 3),
        ("llm_governance_rules", "SELECT COUNT(*) FROM llm_governance_rules", 5),
        ("llm_provider_status", "SELECT COUNT(*) FROM llm_provider_status", 5),
        ("llm_token_log", "SELECT COUNT(*) FROM llm_token_log", 100),
        ("llm_governance_decisions", "SELECT COUNT(*) FROM llm_governance_decisions", 100),
        ("llm_token_baseline", "SELECT COUNT(*) FROM llm_token_baseline", 3),
        ("llm_anomaly", "SELECT COUNT(*) FROM llm_anomaly", 3),
        ("llm_forecast", "SELECT COUNT(*) FROM llm_forecast", 50),
        ("llm_billing_monthly", "SELECT COUNT(*) FROM llm_billing_monthly", 1),
        ("llm_billing_line_items", "SELECT COUNT(*) FROM llm_billing_line_items", 1),
        ("llm_client_reports", "SELECT COUNT(*) FROM llm_client_reports", 1),
        ("llm_tenant_contracts", "SELECT COUNT(*) FROM llm_tenant_contracts", 3),
        ("llm_model_pricing", "SELECT COUNT(*) FROM llm_model_pricing", 4),
    ]

    all_ok = True
    async with AsyncSessionLocal() as session:
        for name, query, minimum in checks:
            try:
                result = await session.execute(text(query))
                count = result.scalar()
                if count >= minimum:
                    utils.success(f"{name}: {count:,} rows ✓")
                else:
                    utils.error(f"{name}: {count} rows (expected ≥{minimum})")
                    all_ok = False
            except Exception as e:
                utils.error(f"{name}: ERROR — {e}")
                all_ok = False

    if all_ok:
        utils.success("\n✓ All checks passed — data is ready for demo")
    else:
        utils.error("\n✗ Some checks failed — run --mode seed to populate data")
