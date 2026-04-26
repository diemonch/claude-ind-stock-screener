#!/usr/bin/env python3
"""
Phase 1 dry test — runs 5 Nifty tickers through the full pipeline.
Usage: python scanner/_dry_test.py
"""

import sys
import json
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR / "scripts"))
sys.path.insert(0, str(ROOT_DIR / "scripts" / "agents"))
sys.path.insert(0, str(ROOT_DIR / "scanner"))

from nifty_screener import fetch_ticker_data, apply_filters, RESULTS_DIR
from signal_engine  import run_signal_engine
from sector_batcher import run_batcher

TEST_TICKERS = [
    {"ticker": "RELIANCE.NS",  "symbol": "RELIANCE",  "company": "Reliance Industries",    "industry": "Energy"},
    {"ticker": "INFY.NS",      "symbol": "INFY",       "company": "Infosys Ltd.",            "industry": "IT"},
    {"ticker": "HDFCBANK.NS",  "symbol": "HDFCBANK",   "company": "HDFC Bank Ltd.",          "industry": "Banking"},
    {"ticker": "TATAMOTORS.NS","symbol": "TATAMOTORS", "company": "Tata Motors Ltd.",        "industry": "Automobile"},
    {"ticker": "SUNPHARMA.NS", "symbol": "SUNPHARMA",  "company": "Sun Pharmaceutical Ind.", "industry": "Healthcare"},
]

def main():
    import yfinance as yf

    print("\n── Step 1: Fetch ticker data ────────────────────────────────")
    raw = []
    for entry in TEST_TICKERS:
        print("  Fetching {}...".format(entry["ticker"]))
        result = fetch_ticker_data(entry, yf)
        if result:
            raw.append(result)
            print("    price={} rsi={} ema20={} ema50={} roe_pct={} fwd_pe={} drawdown_52w={}".format(
                result["price"], result["rsi"], result["ema20"], result["ema50"],
                result["roe_pct"], result["fwd_pe"], result["drawdown_52w"]
            ))
        else:
            print("    FAILED to fetch")

    print("\n── Step 2: Apply 6 filters ──────────────────────────────────")
    survivors, summary = apply_filters(raw)
    print("  Raw: {}  Survivors: {}".format(len(raw), len(survivors)))
    print("  Filter summary:")
    for k, v in summary.items():
        print("    {}: eliminated {}".format(k, v))

    if not survivors:
        print("\n  No survivors (expected with only 5 tickers — filters are strict)")
        print("  Using raw list for signal/batch test...")
        survivors = raw  # use raw for testing downstream stages

    print("\n── Step 3: Compute signals ──────────────────────────────────")
    enriched = run_signal_engine(survivors)
    print("  Enriched: {}".format(len(enriched)))
    if enriched:
        s = enriched[0]
        print("\n  Sample signals for {}:".format(s["ticker"]))
        for k in ("trend_shift", "trend_strength", "indicators", "buy_zone",
                  "sell_zone", "stop_loss", "risk_reward", "signal",
                  "confluence_score", "volume_ratio", "circuit_risk"):
            print("    {}: {}".format(k, s.get(k)))

    print("\n── Step 4: Build sector batches ─────────────────────────────")
    batches = run_batcher(enriched, week_context="normal", market_condition="sideways")
    print("  Batches: {}".format(len(batches)))
    if batches:
        print("\n  Sample batch (first) — this is what Haiku receives:")
        print(json.dumps(batches[0], indent=2))

    print("\n── Phase 1 dry test complete ────────────────────────────────")

if __name__ == "__main__":
    main()
