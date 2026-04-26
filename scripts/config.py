"""
Market configuration for the stock-analyst skill.
Defines per-market constants used by scanners and agents.
"""

# ── US Market ──────────────────────────────────────────────────────────────────
US = {
    "name":            "United States",
    "code":            "US",
    "currency":        "USD",
    "currency_symbol": "$",
    "ticker_suffix":   "",              # plain tickers: AAPL, MSFT
    "exchange":        "NYSE/NASDAQ",
    "scan_interval":   "weekly",
    "hold_period":     "2-3 weeks",
    "market_cap_min":  1_000_000_000,   # $1B
    "avg_volume_min":  500_000,
    "liquidity_note":  "market cap > $1B, avg volume > 500k",
}

# ── India Market ───────────────────────────────────────────────────────────────
IN = {
    "name":            "India",
    "code":            "IN",
    "currency":        "INR",
    "currency_symbol": "₹",
    "ticker_suffix":   ".NS",           # NSE tickers via yfinance: RELIANCE.NS
    "exchange":        "NSE",
    "scan_interval":   "biweekly",      # every 2 weeks
    "hold_period":     "3-4 weeks",
    "market_cap_min":  10_000_000_000,  # ₹1000 Cr (1000 × 10^7)
    "avg_volume_min":  500_000,
    "liquidity_note":  "market cap > ₹1000 Cr, avg volume > 500k",

    # India-specific fundamental thresholds
    "de_ratio_max":       1.0,          # Debt/Equity < 1
    "rev_growth_min_pct": 15.0,         # revenue growth YoY > 15%

    # Technical thresholds
    "rsi_low":   45,
    "rsi_high":  65,

    # Universe
    "universe":       "NIFTY500",
    "universe_file":  "ind_nifty500list (1).csv",   # relative to skill data/
}

# ── Convenience lookup ─────────────────────────────────────────────────────────
MARKETS = {"US": US, "IN": IN}


def get_market(code: str) -> dict:
    """Return market config dict for the given market code (case-insensitive)."""
    cfg = MARKETS.get(code.upper())
    if cfg is None:
        raise ValueError(f"Unknown market '{code}'. Available: {list(MARKETS)}")
    return cfg
