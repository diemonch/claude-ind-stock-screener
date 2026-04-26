#!/usr/bin/env python3
"""
Weekly Runner — Phase 4. Full pipeline orchestrator + APScheduler.
Runs every Sunday 22:00 IST or on demand via CLI.

Usage:
  python scheduler/weekly_runner.py                         # one-shot, auto context
  python scheduler/weekly_runner.py --dry-run               # skip API calls
  python scheduler/weekly_runner.py --week-context results_week
  python scheduler/weekly_runner.py --schedule              # block and run on cron
"""

import argparse
import calendar
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
AGENTS_DIR  = SCRIPTS_DIR / "agents"
SCANNER_DIR = ROOT_DIR / "scanner"
NEW_AGENTS  = ROOT_DIR / "agents"
DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"
CACHE_DIR   = DATA_DIR / "cache"

for p in (RESULTS_DIR, CACHE_DIR):
    p.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(AGENTS_DIR))
sys.path.insert(0, str(SCANNER_DIR))
sys.path.insert(0, str(NEW_AGENTS))

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weekly_runner")


# ── Week context detection ─────────────────────────────────────────────────────

def _last_thursday(year: int, month: int) -> int:
    """Return the day-of-month of the last Thursday in the given month."""
    last_day = calendar.monthrange(year, month)[1]
    for d in range(last_day, last_day - 7, -1):
        if datetime(year, month, d).weekday() == 3:   # Thursday
            return d
    return last_day  # fallback


def _is_expiry_week(dt: datetime) -> bool:
    """True if dt falls in the same ISO week as the last Thursday of its month."""
    last_thu = _last_thursday(dt.year, dt.month)
    last_thu_dt = datetime(dt.year, dt.month, last_thu)
    return dt.isocalendar()[1] == last_thu_dt.isocalendar()[1]


def detect_week_context(dt: Optional[datetime] = None) -> str:
    """
    Auto-detect week context from the calendar date.
    Priority: budget_week > expiry_week > results_week > normal.
    """
    dt = dt or datetime.now()
    m, d = dt.month, dt.day

    # Budget week: Union Budget is typically presented in first week of February
    if m == 2 and d <= 7:
        return "budget_week"

    # NSE F&O monthly expiry week (last Thursday of each month)
    if _is_expiry_week(dt):
        return "expiry_week"

    # Results seasons: Q4 Apr–May, Q1 Jul–Aug, Q2 Oct–Nov, Q3 Jan–Feb
    if m in (4, 5, 7, 8, 10, 11, 1, 2):
        return "results_week"

    return "normal"


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cached_screener_path(date_str: str) -> Path:
    return RESULTS_DIR / "screener_{}.json".format(date_str)


def _load_screener_cache(date_str: str) -> Optional[Tuple[List[Dict], Dict]]:
    """Return (survivors, filter_summary) from today's screener cache, or None."""
    path = _cached_screener_path(date_str)
    if not path.exists():
        return None
    age_s = time.time() - path.stat().st_mtime
    if age_s > 86_400:   # older than 24 hours
        return None
    with open(path) as f:
        data = json.load(f)
    log.info("Loaded screener cache from %s (age %.0fmin)", path, age_s / 60)
    return data.get("tickers", []), data.get("filter_summary", {})


# ── Pipeline stages ────────────────────────────────────────────────────────────

def _stage_screener(
    date_str: str,
    tickers_csv: Optional[str],
    dry_run: bool,
) -> Tuple[List[Dict], Dict]:
    """Run or load screener. Returns (survivors, filter_summary)."""
    cached = _load_screener_cache(date_str)
    if cached:
        survivors, summary = cached
        log.info("Stage 1 — screener (cached): %d survivors", len(survivors))
        return survivors, summary

    if dry_run:
        log.info("Stage 1 — screener (dry-run): using empty survivor list")
        return [], {}

    from nifty_screener import run_screener
    log.info("Stage 1 — screener: fetching Nifty 500...")
    survivors, summary = run_screener(tickers_csv)
    log.info("Stage 1 done: %d survivors", len(survivors))
    return survivors, summary


def _stage_signals(survivors: List[Dict], dry_run: bool) -> List[Dict]:
    """Compute signals for survivors. Returns enriched list."""
    if not survivors:
        return []
    if dry_run:
        log.info("Stage 2 — signals (dry-run): returning survivors unchanged")
        return survivors
    from signal_engine import run_signal_engine
    log.info("Stage 2 — signal engine: %d tickers", len(survivors))
    enriched = run_signal_engine(survivors)
    log.info("Stage 2 done: %d enriched", len(enriched))
    return enriched


def _stage_batcher(
    enriched: List[Dict],
    week_context: str,
    market_condition: str,
) -> List[Dict]:
    """Group enriched tickers into sector batches. Returns batch list."""
    if not enriched:
        return []
    from sector_batcher import run_batcher
    log.info("Stage 3 — sector batcher: %d tickers", len(enriched))
    batches = run_batcher(enriched, week_context=week_context, market_condition=market_condition)
    log.info("Stage 3 done: %d batches", len(batches))
    return batches


def _stage_haiku(batches: List[Dict], dry_run: bool) -> List[Dict]:
    """Run Haiku validation on all batches. Returns validated list."""
    if not batches:
        return []
    if dry_run:
        log.info("Stage 4 — Haiku (dry-run): skipping API call")
        return []
    from haiku_validator import run_haiku_validator
    log.info("Stage 4 — Haiku validator: %d batches", len(batches))
    validated = run_haiku_validator(batches)
    log.info("Stage 4 done: %d validated picks", len(validated))
    return validated


def _stage_sonnet(
    validated: List[Dict],
    enriched: List[Dict],
    dry_run: bool,
) -> List[Dict]:
    """Run Sonnet thesis generation. Falls back to top enriched if no validated."""
    if dry_run:
        log.info("Stage 5 — Sonnet (dry-run): skipping API call")
        return []

    candidates = validated
    if not candidates:
        log.warning("Stage 5 — no validated picks; using top enriched by confluence")
        candidates = sorted(
            enriched,
            key=lambda t: t.get("confluence_score", 0),
            reverse=True,
        )[:20]

    if not candidates:
        log.warning("Stage 5 — no candidates at all; skipping Sonnet")
        return []

    from sonnet_analyst import run_sonnet_analyst
    log.info("Stage 5 — Sonnet analyst: %d candidates", len(candidates))
    cards = run_sonnet_analyst(candidates)
    log.info("Stage 5 done: %d thesis cards", len(cards))
    return cards


def _print_summary(
    date_str: str,
    week_context: str,
    market_condition: str,
    universe_size: int,
    survivors: List[Dict],
    enriched: List[Dict],
    validated: List[Dict],
    cards: List[Dict],
    elapsed: float,
    dry_run: bool,
) -> None:
    """Print the pipeline summary to stdout."""
    separator = "─" * 60
    print("\n" + separator)
    print("  India Weekly Runner — Pipeline Summary")
    print(separator)
    print("  Date:             {}".format(date_str))
    print("  Week context:     {}".format(week_context))
    print("  Market condition: {}".format(market_condition))
    print("  Dry run:          {}".format(dry_run))
    print(separator)
    print("  Stage 1 — Universe:    {:>5}".format(universe_size))
    print("  Stage 2 — Survivors:   {:>5}".format(len(survivors)))
    print("  Stage 3 — Enriched:    {:>5}".format(len(enriched)))
    print("  Stage 4 — Validated:   {:>5}".format(len(validated)))
    print("  Stage 5 — Thesis cards:{:>5}".format(len(cards)))
    print(separator)

    if not dry_run:
        try:
            from agent_utils import TokenTracker
            tracker = TokenTracker()
            for model, info in tracker.summary().items():
                print("  Tokens {:<30} {:>6}/{:<6}  ({:.0f}%)".format(
                    model + ":", info["used"], info["budget"], info["pct"]
                ))
            print(separator)
        except Exception:
            pass

    print("  Elapsed:          {:.0f}s".format(elapsed))
    thesis_path = RESULTS_DIR / "thesis_{}.json".format(date_str)
    print("  Output:           {}".format(thesis_path))
    print(separator + "\n")


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_weekly_pipeline(
    week_context: Optional[str] = None,
    market_condition: str = "sideways",
    dry_run: bool = False,
    tickers_csv: Optional[str] = None,
) -> List[Dict]:
    """
    Execute the full India scan pipeline end-to-end.
    week_context: auto-detected if None. Returns final thesis cards list.
    """
    t_start   = time.time()
    date_str  = datetime.today().strftime("%Y%m%d")
    ctx       = week_context or detect_week_context()

    log.info("Pipeline start — date=%s context=%s market=%s dry_run=%s",
             date_str, ctx, market_condition, dry_run)

    survivors, filter_summary = _stage_screener(date_str, tickers_csv, dry_run)
    enriched                  = _stage_signals(survivors, dry_run)
    batches                   = _stage_batcher(enriched, ctx, market_condition)
    validated                 = _stage_haiku(batches, dry_run)
    cards                     = _stage_sonnet(validated, enriched, dry_run)

    elapsed = time.time() - t_start
    _print_summary(
        date_str, ctx, market_condition,
        universe_size=500,
        survivors=survivors,
        enriched=enriched,
        validated=validated,
        cards=cards,
        elapsed=elapsed,
        dry_run=dry_run,
    )

    return cards


# ── APScheduler ────────────────────────────────────────────────────────────────

def _start_scheduler(market_condition: str) -> None:
    """Block and run the pipeline every Sunday at 22:00 IST."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
    except ImportError:
        log.error("Missing: pip install apscheduler pytz")
        sys.exit(1)

    tz        = pytz.timezone("Asia/Kolkata")
    scheduler = BlockingScheduler(timezone=tz)

    def _job():
        ctx = detect_week_context(datetime.now(tz))
        log.info("Scheduled job fired — week_context=%s", ctx)
        run_weekly_pipeline(week_context=ctx, market_condition=market_condition)

    scheduler.add_job(
        _job,
        CronTrigger(day_of_week="sun", hour=22, minute=0, timezone=tz),
    )
    log.info("Scheduler running — next fire: Sunday 22:00 IST. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="India Weekly Runner")
    parser.add_argument(
        "--week-context",
        choices=["normal", "results_week", "budget_week", "expiry_week"],
        default=None,
        help="Override week context (auto-detected if omitted)",
    )
    parser.add_argument(
        "--market-condition",
        choices=["sideways", "fii_buying", "fii_selling"],
        default="sideways",
        help="Current FII/market condition (default: sideways)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip Claude API calls — runs screener, signals, batcher only",
    )
    parser.add_argument(
        "--tickers-csv",
        default=None,
        help="Path to Nifty 500 tickers CSV (default: data/ind_nifty500list.csv)",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Block and run on cron schedule (Sunday 22:00 IST)",
    )
    args = parser.parse_args()

    if args.schedule:
        _start_scheduler(args.market_condition)
    else:
        run_weekly_pipeline(
            week_context=args.week_context,
            market_condition=args.market_condition,
            dry_run=args.dry_run,
            tickers_csv=args.tickers_csv,
        )


if __name__ == "__main__":
    main()
