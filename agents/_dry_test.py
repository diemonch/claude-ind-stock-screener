#!/usr/bin/env python3
"""
Phase 2 dry test — validates 3 tickers through Haiku → Sonnet.
Usage: python agents/_dry_test.py
"""

import sys
import json
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR / "scanner"))
sys.path.insert(0, str(ROOT_DIR / "agents"))

from signal_engine import compute_signals
from sector_batcher import build_batches
from agent_utils import TokenTracker, validate_json, retry_with_backoff
from haiku_validator import run_haiku_validator
from sonnet_analyst import run_sonnet_analyst


def make_synthetic_ticker(ticker, company, sector, industry, price, rsi):
    """Build a synthetic enriched ticker dict using computed signals."""
    np.random.seed(hash(ticker) % 2**31)
    n = 200
    dates = [datetime(2025, 10, 1) + timedelta(days=i) for i in range(n)]
    base = price * 0.85
    prices = [base + (price - base) * (i / n) + np.random.normal(0, price * 0.008)
              for i in range(n)]
    hist = pd.DataFrame({
        "Close":  prices,
        "High":   [p * 1.012 for p in prices],
        "Low":    [p * 0.988 for p in prices],
        "Volume": np.random.randint(500_000, 1_200_000, n),
    }, index=dates)

    ticker_data = {
        "ticker": ticker, "company": company, "sector": sector, "industry": industry,
        "price": price, "rsi": rsi, "avg_volume": 700_000,
        "ema20": round(price * 0.99, 2), "ema50": round(price * 0.96, 2),
        "roe_pct": 15.0, "fwd_pe": 18.5, "net_margin_pct": 12.0,
        "rev_growth_pct": 22.0, "beta": 0.85,
        "high_52w": round(price * 1.12, 2), "low_52w": round(price * 0.72, 2),
        "atr": round(price * 0.018, 2),
    }
    return compute_signals(ticker_data, hist)


def main():
    print("\n── Phase 2 dry test — 3 tickers through full agent pipeline ────")

    print("\n── Building synthetic enriched tickers ─────────────────────────")
    enriched = [
        make_synthetic_ticker("GESHIP.NS",   "Great Eastern Shipping",     "Industrials",        "Services",    1424, 54),
        make_synthetic_ticker("UNIONBANK.NS", "Union Bank of India",        "Financial Services", "Banking",     185,  57),
        make_synthetic_ticker("LUPIN.NS",     "Lupin Ltd.",                 "Healthcare",         "Pharma",      2310, 55),
    ]
    for t in enriched:
        print("  {} | trend={} | confluence={} | signal={}".format(
            t["ticker"], t["trend_shift"], t["confluence_score"], t["signal"]
        ))

    print("\n── Building sector batches ──────────────────────────────────────")
    batches = build_batches(enriched, week_context="normal", market_condition="sideways")
    print("  Batches: {}".format(len(batches)))

    print("\n── Running Haiku validator ──────────────────────────────────────")
    validated = run_haiku_validator(batches)
    print("  Validated picks: {}".format(len(validated)))
    if validated:
        for v in validated:
            print("  {} | validated={} | confidence={} | reason={}".format(
                v.get("ticker"), v.get("validated"), v.get("confidence"), v.get("reason")
            ))

    if not validated:
        print("  No validated picks — using enriched directly for Sonnet test")
        validated = enriched

    print("\n── Running Sonnet analyst ───────────────────────────────────────")
    cards = run_sonnet_analyst(validated)
    print("  Thesis cards: {}".format(len(cards)))

    if cards:
        print("\n── Sample thesis card ───────────────────────────────────────────")
        print(json.dumps(cards[0], indent=2))

    print("\n── Token usage this week ────────────────────────────────────────")
    tracker = TokenTracker()
    for model, info in tracker.summary().items():
        print("  {}: used={} / budget={} ({}%)".format(
            model, info["used"], info["budget"], info["pct"]
        ))

    print("\n── Phase 2 dry test complete ────────────────────────────────────")


if __name__ == "__main__":
    main()
