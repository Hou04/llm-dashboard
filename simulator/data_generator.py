"""
Statistical Data Generator
============================
Generates realistic LLM call data using statistical distributions.
No external API calls — everything is computed locally.
"""

import math
import random
import uuid
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from typing import Optional

from simulator.config import (
    MODELS,
    TENANTS,
    AGENT_PROFILES,
    ANOMALY_DAYS,
    RANDOM_SEED,
    HISTORY_DAYS,
    WEEKEND_FACTOR,
)
from simulator.prompts import get_random_prompt, get_random_completion


class DataGenerator:
    """Generates realistic LLM call data with proper statistical distributions."""

    def __init__(self, seed: int = RANDOM_SEED):
        self.rng = random.Random(seed)

    def _lognormal(self, mean: float, std: float) -> int:
        """Generate a log-normal distributed integer (always positive)."""
        if std <= 0:
            return max(1, int(mean))
        # Convert mean/std to log-space parameters
        variance = std ** 2
        mu = math.log(mean ** 2 / math.sqrt(variance + mean ** 2))
        sigma = math.sqrt(math.log(1 + variance / mean ** 2))
        return max(1, int(self.rng.lognormvariate(mu, sigma)))

    def _work_hour_timestamp(self, day: date) -> datetime:
        """Generate a timestamp during work hours (8AM-8PM) with concentration around midday."""
        # Beta distribution peaks around 0.5 → shifted to 8AM-8PM
        hour_fraction = self.rng.betavariate(2.2, 2.2)  # Peak around 0.5
        hour = 8 + hour_fraction * 12  # 8AM to 8PM
        minute = self.rng.randint(0, 59)
        second = self.rng.randint(0, 59)

        h = int(hour)
        m = minute
        s = second

        return datetime(
            day.year, day.month, day.day,
            h, m, s,
            tzinfo=timezone.utc,
        )

    def is_weekend(self, d: date) -> bool:
        return d.weekday() >= 5  # Saturday=5, Sunday=6

    def get_daily_call_count(
        self,
        tenant_id: str,
        day: date,
        days_from_start: int,
    ) -> int:
        """Calculate number of calls for a tenant on a specific day."""
        cfg = TENANTS[tenant_id]
        base = cfg["daily_calls_mean"]
        std = cfg["daily_calls_std"]

        # Apply growth trend
        weekly_growth = cfg.get("growth_pct_per_week", 2.0) / 100.0
        growth_factor = 1.0 + (days_from_start / 7.0) * weekly_growth

        # Apply weekend reduction
        if self.is_weekend(day):
            base *= WEEKEND_FACTOR

        # Apply growth
        adjusted_mean = base * growth_factor

        # Random variation
        count = max(1, int(self.rng.gauss(adjusted_mean, std)))

        # Check if this is an anomaly day
        for anomaly in ANOMALY_DAYS:
            anomaly_date = (datetime.now(timezone.utc) - timedelta(days=anomaly["days_ago"])).date()
            if day == anomaly_date and tenant_id == anomaly["tenant_id"]:
                count = int(count * anomaly["multiplier"])

        return count

    def generate_call(
        self,
        tenant_id: str,
        agent_id: str,
        model: str,
        timestamp: datetime,
        is_anomalous: bool = False,
        force_error: bool = False,
        force_blocked: bool = False,
    ) -> dict:
        """Generate a single realistic LLM call record."""
        cfg = TENANTS[tenant_id]
        model_cfg = MODELS.get(model, MODELS["llama-3.3-70b-versatile"])
        agent_cfg = AGENT_PROFILES.get(agent_id, AGENT_PROFILES["nc_classifier"])

        # Token generation
        multiplier = self.rng.uniform(2.5, 4.0) if is_anomalous else 1.0
        input_tokens = self._lognormal(
            agent_cfg["input_tokens_mean"] * multiplier,
            agent_cfg["input_tokens_std"],
        )
        output_tokens = self._lognormal(
            agent_cfg["output_tokens_mean"] * multiplier,
            agent_cfg["output_tokens_std"],
        )
        total_tokens = input_tokens + output_tokens

        # Cost calculation
        cost = (
            input_tokens * model_cfg["input_price_per_1k"]
            + output_tokens * model_cfg["output_price_per_1k"]
        ) / 1000.0

        # Latency
        latency = self._lognormal(
            model_cfg["avg_latency_ms"] * (1.5 if is_anomalous else 1.0),
            model_cfg["latency_std"],
        )

        # Status determination
        if force_blocked:
            call_status = "blocked"
            error_msg = "Governance rule: budget cap exceeded"
            cost = 0.0
        elif force_error:
            call_status = "error"
            error_msg = self.rng.choice([
                "Provider timeout after 30000ms",
                "Rate limit exceeded (429)",
                "Internal server error from provider",
                "Context length exceeded for model",
                "Invalid API key or authentication failure",
            ])
        elif self.rng.random() < (0.15 if is_anomalous else 0.03):
            call_status = self.rng.choice(["error", "timeout"])
            error_msg = self.rng.choice([
                "Provider timeout after 30000ms",
                "Rate limit exceeded (429)",
                "Internal server error from provider",
            ])
        else:
            call_status = "success"
            error_msg = None

        # Session ID (group calls within a work session)
        session_id = f"sess_{tenant_id}_{timestamp.strftime('%Y%m%d')}_{self.rng.randint(1, 5)}"

        # Prompt & completion text
        prompt_text = get_random_prompt(agent_id)
        completion_text = get_random_completion(agent_id) if call_status == "success" else None

        call_id = uuid.uuid4()
        trace_id = uuid.uuid4().hex[:16]

        return {
            "id": call_id,
            "request_id": f"req_{call_id.hex[:12]}",
            "trace_id": trace_id,
            "tenant_id": tenant_id,
            "agent_id": agent_id,
            "module": agent_cfg["module"],
            "model": model,
            "provider": model_cfg["provider"],
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cost_usd": Decimal(str(round(cost, 8))),
            "duration_ms": latency,
            "status": call_status,
            "error_message": error_msg,
            "prompt_text": prompt_text,
            "completion_text": completion_text,
            "pii_redacted": False,
            "metadata_": {"source": "simulator", "agent": agent_id, "session": session_id},
            "created_at": timestamp,
            "session_id": session_id,
        }

    def generate_day(
        self,
        tenant_id: str,
        day: date,
        days_from_start: int,
    ) -> list[dict]:
        """Generate all calls for one tenant for one day."""
        cfg = TENANTS[tenant_id]
        agents = cfg["agents"]
        models = cfg["models"]

        num_calls = self.get_daily_call_count(tenant_id, day, days_from_start)

        # Check if this day has an anomaly
        is_anomaly_day = False
        anomaly_info = None
        for anomaly in ANOMALY_DAYS:
            anomaly_date = (datetime.now(timezone.utc) - timedelta(days=anomaly["days_ago"])).date()
            if day == anomaly_date and tenant_id == anomaly["tenant_id"]:
                is_anomaly_day = True
                anomaly_info = anomaly
                break

        calls = []
        for _ in range(num_calls):
            agent_id = self.rng.choice(agents)
            model = self.rng.choice(models)
            timestamp = self._work_hour_timestamp(day)

            call = self.generate_call(
                tenant_id=tenant_id,
                agent_id=agent_id,
                model=model,
                timestamp=timestamp,
                is_anomalous=is_anomaly_day,
            )
            calls.append(call)

        # Sort by timestamp
        calls.sort(key=lambda c: c["created_at"])
        return calls

    def generate_governance_decision(
        self,
        call: dict,
        decision: str = "allow",
        rule_id: Optional[uuid.UUID] = None,
        reason: Optional[str] = None,
        tokens_today: int = 0,
        budget_today: float = 0.0,
    ) -> dict:
        """Generate a governance decision record for a call."""
        return {
            "id": uuid.uuid4(),
            "request_id": call.get("request_id"),
            "tenant_id": call["tenant_id"],
            "model_requested": call["model"],
            "model_used": call["model"],
            "decision": decision,
            "reason": reason or f"Governance evaluation: {decision}",
            "rule_id": rule_id,
            "tokens_used_today": tokens_today,
            "budget_used_today": Decimal(str(round(budget_today, 4))),
            "evaluated_at": call["created_at"],
        }

    def generate_baseline(
        self,
        tenant_id: str,
        daily_totals: list[float],
        daily_costs: list[float],
        daily_counts: list[int],
    ) -> dict:
        """Compute a statistical baseline from historical data."""
        import statistics

        mean_tokens = statistics.mean(daily_totals) if daily_totals else 0
        std_tokens = statistics.stdev(daily_totals) if len(daily_totals) > 1 else 0
        min_tokens = min(daily_totals) if daily_totals else 0
        max_tokens = max(daily_totals) if daily_totals else 0

        mean_cost = statistics.mean(daily_costs) if daily_costs else 0
        std_cost = statistics.stdev(daily_costs) if len(daily_costs) > 1 else 0

        mean_calls = statistics.mean(daily_counts) if daily_counts else 0
        std_calls = statistics.stdev(daily_counts) if len(daily_counts) > 1 else 0

        return {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "agent_id": "*",
            "model": "*",
            "daily_mean": mean_tokens,
            "daily_std_dev": std_tokens,
            "daily_min": min_tokens,
            "daily_max": max_tokens,
            "call_count_mean": mean_calls,
            "call_count_std_dev": std_calls,
            "daily_cost_mean": mean_cost,
            "daily_cost_std_dev": std_cost,
            "error_rate_mean": 0.03,
            "cusum_pos": 0.0,
            "cusum_neg": 0.0,
            "cusum_k": std_tokens * 0.5 if std_tokens > 0 else 1000.0,
            "isolation_forest_threshold": -0.1,
            "sample_days": len(daily_totals),
            "computed_at": datetime.now(timezone.utc),
            "baseline_from": datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS),
            "baseline_to": datetime.now(timezone.utc),
        }

    def generate_forecasts(
        self,
        tenant_id: str,
        baseline_mean: float,
        baseline_cost_mean: float,
        growth_pct: float,
        horizon_days: int = 30,
    ) -> tuple[list[dict], Optional[dict]]:
        """Generate forecast records and budget risk for a tenant."""
        today = datetime.now(timezone.utc).date()
        forecasts = []
        budget_risk = None

        tenant_cfg = TENANTS[tenant_id]
        budget_limit = tenant_cfg["budget"]

        # Day-of-week seasonal multipliers
        dow_multipliers = {
            0: 1.05,  # Monday
            1: 1.10,  # Tuesday
            2: 1.08,  # Wednesday
            3: 1.03,  # Thursday
            4: 0.95,  # Friday
            5: 0.35,  # Saturday
            6: 0.30,  # Sunday
        }

        cumulative_cost = baseline_cost_mean * 15  # Assume we're mid-month

        for day_offset in range(1, horizon_days + 1):
            forecast_date = today + timedelta(days=day_offset)
            dow = forecast_date.weekday()
            seasonal = dow_multipliers.get(dow, 1.0)

            # Trend: linear growth
            daily_growth = growth_pct / 100.0 / 7.0
            trend_factor = 1.0 + daily_growth * day_offset
            trend_value = baseline_mean * trend_factor

            # Three scenarios
            for scenario, factor in [("pessimistic", 1.3), ("likely", 1.0), ("optimistic", 0.75)]:
                predicted_tokens = trend_value * seasonal * factor
                predicted_cost = (predicted_tokens / baseline_mean) * baseline_cost_mean * factor if baseline_mean > 0 else 0

                # Confidence interval grows with horizon
                half_width = baseline_mean * 0.05 * math.sqrt(day_offset)

                forecasts.append({
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "agent_id": "*",
                    "forecast_date": forecast_date,
                    "scenario": scenario,
                    "predicted_tokens": predicted_tokens,
                    "predicted_cost_usd": Decimal(str(round(predicted_cost, 8))),
                    "trend_value": trend_value,
                    "seasonal_multiplier": seasonal,
                    "confidence_lower": max(0, predicted_tokens - half_width),
                    "confidence_upper": predicted_tokens + half_width,
                    "horizon_days": day_offset,
                    "generated_at": datetime.now(timezone.utc),
                })

            # Track cumulative cost for budget risk (likely scenario)
            likely_daily_cost = (trend_value * seasonal / baseline_mean) * baseline_cost_mean if baseline_mean > 0 else 0
            cumulative_cost += likely_daily_cost

            # Check if budget will be exhausted
            if cumulative_cost >= budget_limit and budget_risk is None:
                budget_risk = {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "risk_type": "cost_budget",
                    "days_until_exhaustion": day_offset,
                    "exhaustion_date": forecast_date,
                    "forecasted_value_at_exhaustion": cumulative_cost,
                    "governance_limit": budget_limit,
                    "pct_of_limit_today": min(99.0, (cumulative_cost - likely_daily_cost) / budget_limit * 100),
                    "generated_at": datetime.now(timezone.utc),
                }

        return forecasts, budget_risk

    def generate_billing(
        self,
        tenant_id: str,
        year_month: int,
        calls: list[dict],
    ) -> tuple[dict, list[dict], dict]:
        """Generate a billing invoice, line items, and client report from call data."""
        cfg = TENANTS[tenant_id]
        contract = cfg["contract"]

        # Aggregate call data
        total_calls = len(calls)
        total_input = sum(c["input_tokens"] for c in calls)
        total_output = sum(c["output_tokens"] for c in calls)
        total_tokens = sum(c["total_tokens"] for c in calls)
        raw_cost = sum(float(c["cost_usd"]) for c in calls)

        # Apply contract rules
        if contract["type"] == "forfait":
            forfait_included = contract["tokens"]
            forfait_used = min(total_tokens, forfait_included)
            overage = max(0, total_tokens - forfait_included)
            base_fee = contract["base_fee"]
            overage_charge = (overage / 1000) * contract["overage"]
            total_billed = base_fee + overage_charge
        else:  # pay_as_you_go
            forfait_included = 0
            forfait_used = 0
            overage = 0
            base_fee = 0
            overage_charge = 0
            total_billed = raw_cost

        billing_id = uuid.uuid4()

        # Determine status: older months are finalized
        now_ym = int(datetime.now(timezone.utc).strftime("%Y%m"))
        is_current = year_month >= now_ym
        status = "draft" if is_current else "finalized"

        invoice = {
            "id": billing_id,
            "tenant_id": tenant_id,
            "year_month": year_month,
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "raw_cost_usd": Decimal(str(round(raw_cost, 4))),
            "forfait_tokens_included": forfait_included,
            "forfait_tokens_used": forfait_used,
            "overage_tokens": overage,
            "base_fee_usd": Decimal(str(round(base_fee, 4))),
            "overage_charge_usd": Decimal(str(round(overage_charge, 4))),
            "credits_usd": Decimal("0"),
            "total_billed_usd": Decimal(str(round(total_billed, 4))),
            "status": status,
            "notes": None,
            "snapshot_taken_at": datetime.now(timezone.utc),
            "finalized_at": datetime.now(timezone.utc) if status == "finalized" else None,
            "created_at": datetime.now(timezone.utc),
        }

        # Line items
        line_items = []

        # Base fee line
        if base_fee > 0:
            line_items.append({
                "id": uuid.uuid4(),
                "billing_id": billing_id,
                "tenant_id": tenant_id,
                "year_month": year_month,
                "line_type": "base_fee",
                "description": f"Forfait mensuel — {cfg['name']}",
                "model": None,
                "provider": None,
                "agent_id": None,
                "quantity_tokens": 0,
                "quantity_calls": 0,
                "unit_price_usd": Decimal(str(round(base_fee, 8))),
                "amount_usd": Decimal(str(round(base_fee, 4))),
                "credit_type": None,
                "created_at": datetime.now(timezone.utc),
            })

        # Usage cost per model
        model_usage = {}
        for c in calls:
            m = c["model"]
            if m not in model_usage:
                model_usage[m] = {"tokens": 0, "calls": 0, "cost": 0.0}
            model_usage[m]["tokens"] += c["total_tokens"]
            model_usage[m]["calls"] += 1
            model_usage[m]["cost"] += float(c["cost_usd"])

        for model_name, usage in model_usage.items():
            model_cfg = MODELS.get(model_name, {})
            line_items.append({
                "id": uuid.uuid4(),
                "billing_id": billing_id,
                "tenant_id": tenant_id,
                "year_month": year_month,
                "line_type": "usage_cost",
                "description": f"Utilisation {model_name} — {usage['calls']} appels",
                "model": model_name,
                "provider": model_cfg.get("provider", "unknown"),
                "agent_id": None,
                "quantity_tokens": usage["tokens"],
                "quantity_calls": usage["calls"],
                "unit_price_usd": Decimal(str(round(usage["cost"] / max(usage["tokens"], 1) * 1000, 8))),
                "amount_usd": Decimal(str(round(usage["cost"], 4))),
                "credit_type": None,
                "created_at": datetime.now(timezone.utc),
            })

        # Overage line
        if overage > 0:
            line_items.append({
                "id": uuid.uuid4(),
                "billing_id": billing_id,
                "tenant_id": tenant_id,
                "year_month": year_month,
                "line_type": "overage",
                "description": f"Dépassement forfait — {overage:,} tokens au-delà de {forfait_included:,}",
                "model": None,
                "provider": None,
                "agent_id": None,
                "quantity_tokens": overage,
                "quantity_calls": 0,
                "unit_price_usd": Decimal(str(contract["overage"])),
                "amount_usd": Decimal(str(round(overage_charge, 4))),
                "credit_type": None,
                "created_at": datetime.now(timezone.utc),
            })

        # Client report
        ym_str = f"{str(year_month)[:4]}-{str(year_month)[4:]}"
        report = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "year_month": year_month,
            "billing_id": billing_id,
            "report_title": f"Rapport Client — {cfg['name']} — {ym_str}",
            "executive_summary": (
                f"Ce mois, {cfg['name']} a effectué {total_calls:,} appels IA "
                f"consommant {total_tokens:,} tokens pour un coût total de "
                f"${total_billed:.2f}. "
                f"{'Le forfait a été utilisé à ' + str(int(forfait_used / forfait_included * 100)) + '%.' if forfait_included > 0 else 'Facturation au réel.'} "
                f"{'⚠️ Dépassement de ' + str(overage) + ' tokens.' if overage > 0 else '✅ Aucun dépassement.'}"
            ),
            "report_data": None,
            "status": "sent" if status == "finalized" else "draft",
            "generated_at": datetime.now(timezone.utc),
        }

        return invoice, line_items, report
