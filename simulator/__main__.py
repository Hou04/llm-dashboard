"""
TIM Tunisia — Full-Platform Demo Simulator
===========================================
CLI entry point.

Usage:
    python -m simulator --mode seed         # Phase 1: populate 60 days of historical data
    python -m simulator --mode live         # Phase 2: continuous live traffic via gateway API
    python -m simulator --mode demo         # Phase 3: guided 7-act demo for jury
    python -m simulator --mode seed+live    # Seed then immediately go live
    python -m simulator --mode reset        # Wipe all simulator data
    python -m simulator --mode verify       # Verify data integrity
"""

import argparse
import asyncio
import os
import sys

# Ensure project root is on path for imports
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Load .env
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from simulator import utils


def main():
    parser = argparse.ArgumentParser(
        description="TIM Tunisia — AI Governance Platform — Demo Simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m simulator --mode seed          Populate 60 days of historical data
  python -m simulator --mode live          Continuous live traffic (Ctrl+C to stop)
  python -m simulator --mode live --calls 20   Send exactly 20 calls then stop
  python -m simulator --mode demo          Guided demo for jury presentation
  python -m simulator --mode seed+live     Seed data then go live
  python -m simulator --mode reset         Wipe all data
  python -m simulator --mode verify        Check data integrity
        """,
    )

    parser.add_argument(
        "--mode",
        required=True,
        choices=["seed", "live", "demo", "seed+live", "reset", "verify"],
        help="Simulator mode",
    )
    parser.add_argument(
        "--calls",
        type=int,
        default=None,
        help="Number of calls for live mode (default: unlimited)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Seconds between calls in live mode (default: 2-5s random)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible data (default: 42 for seed, time-based for live)",
    )

    args = parser.parse_args()

    try:
        if args.mode == "seed":
            from simulator.seeder import run_seed
            asyncio.run(run_seed())

        elif args.mode == "live":
            from simulator.live import run_live
            interval_min = args.interval or 2.0
            interval_max = max(interval_min, args.interval + 1.0 if args.interval else 5.0)
            asyncio.run(run_live(
                num_calls=args.calls,
                interval_min=interval_min,
                interval_max=interval_max,
            ))

        elif args.mode == "demo":
            from simulator.demo import run_demo
            asyncio.run(run_demo())

        elif args.mode == "seed+live":
            async def seed_then_live():
                from simulator.seeder import run_seed
                from simulator.live import run_live
                await run_seed()
                print()
                utils.info("Historical data seeded. Starting live traffic in 3 seconds...")
                await asyncio.sleep(3)
                interval_min = args.interval or 2.0
                interval_max = max(interval_min, args.interval + 1.0 if args.interval else 5.0)
                await run_live(
                    num_calls=args.calls,
                    interval_min=interval_min,
                    interval_max=interval_max,
                )
            asyncio.run(seed_then_live())

        elif args.mode == "reset":
            from simulator.seeder import run_reset
            asyncio.run(run_reset())

        elif args.mode == "verify":
            from simulator.seeder import run_verify
            asyncio.run(run_verify())

    except KeyboardInterrupt:
        print(f"\n{utils.C.YELLOW}Interrupted by user{utils.C.RESET}")
        sys.exit(0)
    except Exception as e:
        utils.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
