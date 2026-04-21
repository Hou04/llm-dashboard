"""
Generate simulated daily LLM usage logs.

Reads tenant/model configuration from datasets/config/*.json (no hardcoded values).
Outputs to datasets/raw/YYYY-MM/YYYY-MM-DD.csv.

Each generated CSV matches the schema expected by IngestionService,
making the output directly ingestible via POST /v1/pipeline/ingest.

Usage:
    Single day:
        python scripts/generate_daily_logs.py --date 2026-04-15
        python scripts/generate_daily_logs.py --date today

    Date range:
        python scripts/generate_daily_logs.py --range 2025-10-13 2026-04-15

    Dry run (preview only):
        python scripts/generate_daily_logs.py --date today --dry-run

    Custom output directory:
        python scripts/generate_daily_logs.py --date today --output ./my_data
"""

import argparse
import csv
import json
import random
import sys
from datetime import datetime, timedelta, date
from pathlib import Path

CONFIG_DIR = Path("datasets/config")
OUTPUT_DIR = Path("datasets/raw")

# Realistic status distribution: 90% OK, 5% error, 3% blocked, 2% timeout
_STATUS_POOL = ["OK"] * 90 + ["ERROR"] * 5 + ["BLOCKED"] * 3 + ["TIMEOUT"] * 2

# CSV header matching the llm_token_log schema
_FIELDNAMES = [
    "timestamp", "tenant_id", "agent_id", "provider", "model",
    "input_tokens", "output_tokens", "total_tokens", "duration_ms",
    "status", "input_cost_usd", "output_cost_usd", "total_cost_usd",
    "anomaly_flag",
]


def load_config():
    """Load tenant and model configs from JSON files."""
    tenants_path = CONFIG_DIR / "tenants.json"
    models_path = CONFIG_DIR / "models.json"

    if not tenants_path.exists():
        print(f"ERROR: {tenants_path} not found")
        sys.exit(1)
    if not models_path.exists():
        print(f"ERROR: {models_path} not found")
        sys.exit(1)

    with open(tenants_path, "r", encoding="utf-8") as f:
        tenants = json.load(f)["tenants"]
    with open(models_path, "r", encoding="utf-8") as f:
        models = json.load(f)["models"]

    return tenants, models


def generate_day(day: date, tenants, models, dry_run=False, output_dir=None):
    """
    Generate all simulated LLM calls for one day across all tenants.

    Realistic patterns applied:
    - Weekend reduction (80% less volume)
    - Daily variance (±25%)
    - Time-of-day distribution (more calls during business hours)
    - Anomaly injection (~0.5%)
    """
    out_dir = output_dir or OUTPUT_DIR
    rows = []

    for tenant in tenants:
        tid = tenant["tenant_id"]
        base_calls = tenant.get("daily_call_volume", 200)
        agents = tenant.get("agents", ["default_agent"])

        # Weekend reduction
        if day.weekday() >= 5:
            base_calls = int(base_calls * 0.2)

        # Daily variance ±25%
        num_calls = max(1, int(base_calls * random.uniform(0.75, 1.25)))

        for _ in range(num_calls):
            model_def = random.choice(models)
            agent = random.choice(agents)
            status = random.choice(_STATUS_POOL)

            # Token distribution depends on status
            inp = random.randint(100, 2000)
            if status == "OK":
                out = random.randint(50, 1500)
                dur = random.randint(200, 3000)
            elif status == "TIMEOUT":
                out = random.randint(0, 100)
                dur = random.randint(10000, 30000)
            else:  # ERROR, BLOCKED
                out = random.randint(0, 50)
                dur = random.randint(50, 500)

            total = inp + out

            # Cost calculation from model pricing
            input_cost = (inp / 1000) * model_def["input_price_per_1k"]
            output_cost = (out / 1000) * model_def["output_price_per_1k"]
            total_cost = input_cost + output_cost

            # Anomaly injection: ~0.5% of calls
            anomaly = "True" if random.random() < 0.005 else "False"

            # Generate realistic timestamp within the day (business hours weighted)
            hour = _weighted_hour()
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            ts = datetime(day.year, day.month, day.day, hour, minute, second)

            rows.append({
                "timestamp": ts.isoformat(),
                "tenant_id": tid,
                "agent_id": agent,
                "provider": model_def["provider"],
                "model": model_def["model"],
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": total,
                "duration_ms": dur,
                "status": status,
                "input_cost_usd": round(input_cost, 6),
                "output_cost_usd": round(output_cost, 6),
                "total_cost_usd": round(total_cost, 6),
                "anomaly_flag": anomaly,
            })

    if dry_run:
        print(f"  {day}: {len(rows)} rows (dry run)")
        return rows

    # Write to partitioned file
    month_dir = out_dir / day.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    file_path = month_dir / f"{day.isoformat()}.csv"

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"  {day}: {len(rows)} rows -> {file_path}")
    return rows


def _weighted_hour() -> int:
    """
    Generate a weighted random hour.
    Higher probability during business hours (9-18),
    lower at night, minimal in early morning.
    """
    weights = [
        1, 1, 1, 1, 1, 2,     # 00-05
        3, 5, 8, 10, 10, 10,  # 06-11
        8, 10, 10, 10, 10, 8, # 12-17
        6, 4, 3, 2, 1, 1,     # 18-23
    ]
    hours = list(range(24))
    return random.choices(hours, weights=weights, k=1)[0]


def main():
    parser = argparse.ArgumentParser(
        description="Generate simulated daily LLM usage logs"
    )
    parser.add_argument(
        "--date",
        help="Single date to generate (YYYY-MM-DD or 'today')",
    )
    parser.add_argument(
        "--range",
        nargs=2,
        metavar=("FROM", "TO"),
        help="Date range to generate (FROM TO, inclusive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only — don't write files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Output directory (default: {OUTPUT_DIR})",
    )
    args = parser.parse_args()

    tenants, models = load_config()
    print(
        f"Loaded {len(tenants)} tenants, {len(models)} models "
        f"from {CONFIG_DIR}"
    )

    total_rows = 0

    if args.date:
        d = date.today() if args.date == "today" else date.fromisoformat(args.date)
        rows = generate_day(d, tenants, models, args.dry_run, args.output)
        total_rows = len(rows)

    elif args.range:
        start = date.fromisoformat(args.range[0])
        end = date.fromisoformat(args.range[1])
        current = start
        while current <= end:
            rows = generate_day(current, tenants, models, args.dry_run, args.output)
            total_rows += len(rows)
            current += timedelta(days=1)

    else:
        parser.print_help()
        sys.exit(1)

    print(f"\nTotal: {total_rows:,} rows generated")
    if args.dry_run:
        print("(Dry run — no files written)")


if __name__ == "__main__":
    main()
