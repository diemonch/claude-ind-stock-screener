"""
Position Monitor — standalone price watcher for open India holdings.
Runs every 15 min during NSE market hours (9:15–15:30 IST, Mon–Fri).

Usage:
  python monitor/position_monitor.py              # live monitoring
  python monitor/position_monitor.py --once       # single check and exit
  python monitor/position_monitor.py --interval 5 # check every 5 min (testing)

SL / Target resolution (in priority order):
  1. portfolio_india.json: holding.stop_loss + holding.target
  2. picks_registry.json:  registry entry sell_zone + stop_loss
  3. No levels → position is tracked for price only, no alerts fired
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytz

ROOT_DIR       = Path(__file__).parent.parent
PORTFOLIO_FILE = ROOT_DIR / "portfolio_india.json"
REGISTRY_FILE  = ROOT_DIR / "data" / "results" / "picks_registry.json"

sys.path.insert(0, str(ROOT_DIR / "monitor"))
from alert_engine import run_checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("position_monitor")

IST            = pytz.timezone("Asia/Kolkata")
MARKET_OPEN    = (9, 15)
MARKET_CLOSE   = (15, 30)
DEFAULT_INTERVAL_MIN = 15


# ── Market hours ───────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t <= MARKET_CLOSE


def next_open_seconds() -> int:
    """Seconds until next market open (9:15 IST next weekday)."""
    now = datetime.now(IST)
    days_ahead = 0
    while True:
        days_ahead += 1
        candidate = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1],
                                 second=0, microsecond=0)
        candidate = candidate.__class__(
            candidate.year, candidate.month, candidate.day,
            MARKET_OPEN[0], MARKET_OPEN[1], 0, 0, IST,
        )
        from datetime import timedelta
        target = now + timedelta(days=days_ahead)
        target = target.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
        if target.weekday() < 5:
            return max(0, int((target - now).total_seconds()))


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_portfolio() -> Dict:
    if not PORTFOLIO_FILE.exists():
        return {"holdings": [], "watchlist": []}
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def load_registry() -> Dict:
    if not REGISTRY_FILE.exists():
        return {}
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def resolve_levels(
    holding: Dict,
    registry: Dict,
) -> Tuple[Optional[float], Optional[List[float]]]:
    """
    Return (stop_loss, sell_zone) for a holding.
    Priority: portfolio manual fields → registry entry.
    """
    # 1. Manual fields in portfolio_india.json
    manual_sl     = holding.get("stop_loss")
    manual_target = holding.get("target")
    if manual_sl or manual_target:
        sell_zone = [manual_target, manual_target] if manual_target else None
        return manual_sl, sell_zone

    # 2. Registry entry
    reg = registry.get(holding["ticker"])
    if reg and reg.get("status") == "active":
        return reg.get("stop_loss"), reg.get("sell_zone")

    return None, None


# ── Price fetch ────────────────────────────────────────────────────────────────

def fetch_prices(tickers: List[str]) -> Dict[str, float]:
    """Fetch current prices for a list of tickers via 2-day history. Returns {ticker: price}."""
    try:
        import yfinance as yf
        prices = {}
        for t in tickers:
            try:
                hist = yf.Ticker(t).history(period="2d")
                if not hist.empty:
                    px = round(float(hist["Close"].iloc[-1]), 2)
                else:
                    px = 0.0
                prices[t] = px
            except Exception as e:
                log.warning("Price fetch failed for %s: %s", t, e)
                prices[t] = 0.0
        return prices
    except ImportError:
        log.error("yfinance not installed — pip install yfinance")
        return {}


# ── Main check cycle ───────────────────────────────────────────────────────────

def run_once(verbose: bool = True) -> None:
    """Fetch prices and run all alert checks once."""
    portfolio = load_portfolio()
    registry  = load_registry()
    holdings  = [h for h in portfolio.get("holdings", []) if h.get("shares", 0) > 0]

    if not holdings:
        log.info("No open holdings — nothing to monitor.")
        return

    tickers = [h["ticker"] for h in holdings]
    log.info("Checking %d positions: %s", len(tickers), ", ".join(tickers))

    prices = fetch_prices(tickers)

    positions = []
    for h in holdings:
        t               = h["ticker"]
        stop_loss, sell_zone = resolve_levels(h, registry)
        current_price   = prices.get(t, 0.0)

        if not stop_loss and not sell_zone:
            log.warning(
                "%s — no SL or target set. Add stop_loss/target to portfolio_india.json "
                "or ensure it is an active registry pick.",
                t,
            )

        positions.append({
            "ticker":        t,
            "current_price": current_price,
            "avg_cost":      h.get("avg_cost", 0.0),
            "stop_loss":     stop_loss,
            "sell_zone":     sell_zone,
        })

        if verbose:
            sl_str = "SL ₹{:.2f}".format(stop_loss) if stop_loss else "no SL"
            sz_str = "Target ₹{:.0f}".format(sell_zone[0]) if sell_zone else "no target"
            pnl    = (current_price - h["avg_cost"]) / h["avg_cost"] * 100 if h["avg_cost"] else 0
            log.info(
                "%-20s  CMP ₹%.2f  P&L %+.1f%%  %s  %s",
                t, current_price, pnl, sl_str, sz_str,
            )

    run_checks(positions)
    log.info("Check complete at %s IST", datetime.now(IST).strftime("%H:%M:%S"))


# ── Monitor loop ───────────────────────────────────────────────────────────────

def run_monitor(interval_min: int = DEFAULT_INTERVAL_MIN) -> None:
    """Main loop — checks every interval_min minutes during market hours."""
    log.info("Position monitor started — interval=%dmin, market hours=%02d:%02d–%02d:%02d IST",
             interval_min, *MARKET_OPEN, *MARKET_CLOSE)

    while True:
        if is_market_open():
            try:
                run_once()
            except Exception as e:
                log.error("Check cycle failed: %s", e)
            sleep_sec = interval_min * 60
            log.info("Next check in %d min", interval_min)
        else:
            now = datetime.now(IST)
            # If it's a weekday and market hasn't opened yet, wait until open
            if now.weekday() < 5 and (now.hour, now.minute) < MARKET_OPEN:
                sleep_sec = (
                    (MARKET_OPEN[0] - now.hour) * 3600
                    + (MARKET_OPEN[1] - now.minute) * 60
                    - now.second
                )
                log.info(
                    "Market opens in %.0f min — sleeping until 09:15 IST", sleep_sec / 60
                )
            else:
                secs = next_open_seconds()
                log.info(
                    "Market closed. Next open in %.1f hours — sleeping.", secs / 3600
                )
                sleep_sec = secs

        time.sleep(max(60, sleep_sec))


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="India Position Monitor")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single check and exit (ignores market hours)",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_MIN,
        help="Check interval in minutes (default: 15)",
    )
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_monitor(interval_min=args.interval)


if __name__ == "__main__":
    main()
