#!/usr/bin/env python3
"""
Sector Batcher — Phase 1, Step 3.
Groups signal-enriched survivors into sector batches for Haiku validation.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR    = Path(__file__).parent.parent
DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sector_batcher")

# Maps yfinance sector strings to canonical Nifty sector names
SECTOR_MAP: Dict[str, str] = {
    "Technology":               "IT",
    "Information Technology":   "IT",
    "Financial Services":       "BFSI",
    "Financial":                "BFSI",
    "Banking":                  "BFSI",
    "Consumer Defensive":       "FMCG",
    "Consumer Staples":         "FMCG",
    "Consumer Cyclical":        "Auto",
    "Automobile":               "Auto",
    "Healthcare":               "Pharma",
    "Health Care":              "Pharma",
    "Energy":                   "Energy",
    "Basic Materials":          "Metal",
    "Materials":                "Metal",
    "Industrials":              "Infra",
    "Real Estate":              "Realty",
    "Communication Services":   "Media",
    "Utilities":                "Infra",
}

MAX_BATCH_SIZE = 8

# Only these fields are sent to Haiku — no raw OHLCV ever reaches the agents
SIGNAL_FIELDS = [
    "ticker", "company", "sector",
    "trend_shift", "trend_strength", "confluence_score",
    "rsi", "volume_ratio", "circuit_risk",
    "buy_zone", "sell_zone", "stop_loss", "risk_reward", "signal",
]


def normalize_sector(raw_sector: Optional[str]) -> str:
    """Map a raw yfinance sector string to a canonical Nifty sector label."""
    if not raw_sector:
        return "Others"
    return SECTOR_MAP.get(raw_sector, "Others")


def build_batches(
    enriched_tickers: List[Dict],
    week_context: str = "normal",
    market_condition: str = "sideways",
) -> List[Dict]:
    """
    Group enriched tickers into sector batches for Haiku validation.
    enriched_tickers: output from signal_engine.run_signal_engine.
    week_context: normal | results_week | budget_week | expiry_week.
    market_condition: fii_buying | fii_selling | sideways.
    Returns list of batch dicts, each ready to send to haiku_validator.
    """
    sector_groups: Dict[str, List[Dict]] = {}
    for t in enriched_tickers:
        sector = normalize_sector(t.get("sector"))
        sector_groups.setdefault(sector, []).append(t)

    batches: List[Dict] = []
    for sector, tickers in sorted(sector_groups.items()):
        # Strip to signal fields only — agents never see raw OHLCV
        payloads = [{k: t.get(k) for k in SIGNAL_FIELDS} for t in tickers]

        for i in range(0, len(payloads), MAX_BATCH_SIZE):
            chunk = payloads[i:i + MAX_BATCH_SIZE]
            batches.append({
                "sector":           sector,
                "week_context":     week_context,
                "market_condition": market_condition,
                "tickers":          chunk,
            })
            log.info(
                "Batch: sector=%-8s tickers=%d  (week=%s, market=%s)",
                sector, len(chunk), week_context, market_condition,
            )

    log.info("Total batches: %d across %d sectors", len(batches), len(sector_groups))
    return batches


def run_batcher(
    enriched_tickers: List[Dict],
    week_context: str = "normal",
    market_condition: str = "sideways",
) -> List[Dict]:
    """
    Build and save sector batches to data/results/batches_YYYYMMDD.json.
    Returns list of batch dicts.
    """
    batches  = build_batches(enriched_tickers, week_context, market_condition)
    date_str = datetime.today().strftime("%Y%m%d")
    out_path = RESULTS_DIR / "batches_{}.json".format(date_str)
    with open(out_path, "w") as f:
        json.dump(batches, f, indent=2, default=str)
    log.info("Saved %d batches → %s", len(batches), out_path)
    return batches


if __name__ == "__main__":
    import sys
    date_str     = datetime.today().strftime("%Y%m%d")
    signals_file = RESULTS_DIR / "signals_{}.json".format(date_str)
    if not signals_file.exists():
        log.error("Run signal_engine.py first — %s not found", signals_file)
        sys.exit(1)
    with open(signals_file) as f:
        enriched = json.load(f)
    batches = run_batcher(enriched)
    print("\nBatches created: {}".format(len(batches)))
    if batches:
        print("\nSample batch (first):")
        print(json.dumps(batches[0], indent=2))
