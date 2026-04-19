"""
Split the monolithic llm_token_log_6months.csv into daily partitioned files.

Target structure:
    datasets/raw/
    ├── 2025-10/
    │   ├── 2025-10-13.csv
    │   ├── 2025-10-14.csv
    │   └── ...
    ├── 2025-11/
    ...

Run once:
    python scripts/partition_csv.py

The original file is NOT deleted — do that manually after verification.
"""

import csv
import sys
from pathlib import Path
from collections import defaultdict

INPUT = Path("datasets/llm_token_log_6months.csv")
OUTPUT_DIR = Path("datasets/raw")


def partition():
    if not INPUT.exists():
        print(f"ERROR: {INPUT} not found")
        sys.exit(1)

    daily_rows: dict[str, list[dict]] = defaultdict(list)
    header = None

    print(f"Reading {INPUT}...")
    with open(INPUT, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        for row in reader:
            # Timestamp column is in YYYY-MM-DD or full ISO format
            date_str = row.get("timestamp", "")[:10]
            if date_str and len(date_str) == 10:
                daily_rows[date_str].append(row)
            else:
                # Fallback: try created_at
                date_str = row.get("created_at", "")[:10]
                if date_str and len(date_str) == 10:
                    daily_rows[date_str].append(row)

    if not daily_rows:
        print("ERROR: No rows with valid dates found")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_rows = 0
    for date_str, rows in sorted(daily_rows.items()):
        # Extract YYYY-MM for directory
        month_dir = OUTPUT_DIR / date_str[:7]
        month_dir.mkdir(parents=True, exist_ok=True)

        file_path = month_dir / f"{date_str}.csv"
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)

        total_rows += len(rows)

    print(
        f"Done: {total_rows:,} rows partitioned into {len(daily_rows)} daily files "
        f"in {OUTPUT_DIR}"
    )
    print(
        f"Date range: {min(daily_rows.keys())} to {max(daily_rows.keys())}"
    )


if __name__ == "__main__":
    partition()
