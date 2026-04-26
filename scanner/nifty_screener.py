#!/usr/bin/env python3
"""
Nifty 500 Quantitative Screener — Phase 1, Step 1.
Two-pass approach:
  Pass 1 — batch OHLCV download (50 tickers/request, ~10 API calls total)
  Pass 2 — fundamentals fetched only for technical survivors (~50-100 calls)
"""

import sys
import json
import logging
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
AGENTS_DIR  = SCRIPTS_DIR / "agents"
DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"
CACHE_DIR   = DATA_DIR / "cache"

sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(AGENTS_DIR))

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nifty_screener")

# ── Import helpers from scanner_india ─────────────────────────────────────────
from scanner_india import load_universe, compute_rsi, compute_emas, r2, safe

# ── Tuning constants ───────────────────────────────────────────────────────────
BATCH_SIZE  = 50    # tickers per yf.download() call
BATCH_DELAY = 4.0   # seconds between OHLCV batch downloads
INFO_DELAY  = 0.5   # seconds between individual .info calls
INFO_RETRY  = 8.0   # seconds before retrying a failed .info call

FILTERS: Dict = {
    "rsi_range":        (35, 65),
    "trend":            "ema20_above_ema50",
    "volume_min":       500_000,
    "pe_sector_factor": 1.2,
    "roe_min":          12.0,
    "max_drawdown_52w": 40.0,
}


# ── Pass 1 — Batch OHLCV download ─────────────────────────────────────────────

def _extract_ticker_hist(data: pd.DataFrame, ticker: str) -> Optional[pd.DataFrame]:
    """Extract single-ticker DataFrame from a multi-ticker yf.download() result."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            hist = data.xs(ticker, axis=1, level=1)
        else:
            hist = data  # single-ticker download
        hist = hist.dropna(how="all")
        return hist if len(hist) >= 60 else None
    except Exception:
        return None


def batch_download_ohlcv(universe: List[Dict], yf) -> Dict[str, pd.DataFrame]:
    """
    Download 1-year daily OHLCV for all tickers using batched yf.download() calls.
    universe: list of ticker entry dicts. Returns dict of ticker → DataFrame.
    """
    tickers = [e["ticker"] for e in universe]
    result: Dict[str, pd.DataFrame] = {}

    for i in range(0, len(tickers), BATCH_SIZE):
        batch    = tickers[i:i + BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        total    = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
        log.info("OHLCV batch %d/%d — downloading %d tickers...", batch_no, total, len(batch))

        for attempt in range(1, 3):
            try:
                data = yf.download(
                    batch,
                    period="1y",
                    interval="1d",
                    auto_adjust=True,
                    group_by="ticker",
                    progress=False,
                    threads=False,
                )
                if data.empty:
                    raise ValueError("empty response")

                for ticker in batch:
                    hist = _extract_ticker_hist(data, ticker)
                    if hist is not None:
                        result[ticker] = hist

                fetched = sum(1 for t in batch if t in result)
                log.info("  batch %d: %d/%d tickers fetched", batch_no, fetched, len(batch))
                break

            except Exception as exc:
                if attempt < 2:
                    log.warning("  batch %d attempt %d failed (%s) — retrying in %.0fs",
                                batch_no, attempt, exc, INFO_RETRY)
                    time.sleep(INFO_RETRY)
                else:
                    log.error("  batch %d failed after 2 attempts — skipping", batch_no)

        time.sleep(BATCH_DELAY)

    log.info("OHLCV download complete: %d/%d tickers", len(result), len(tickers))
    return result


def compute_technical_fields(
    entry: Dict,
    hist: pd.DataFrame,
) -> Optional[Dict]:
    """
    Compute all technical indicators from OHLCV for one ticker.
    Returns partial ticker dict (no fundamentals yet) or None on bad data.
    """
    try:
        hist  = hist.dropna(subset=["Close", "High", "Low", "Volume"])
        close = hist["Close"]
        price = r2(float(close.iloc[-1]))
        if not price:
            return None

        avg_vol      = float(hist["Volume"].tail(20).mean())
        rsi          = compute_rsi(close)
        ema20, ema50, _ = compute_emas(close)
        high_52w     = r2(float(hist["High"].max()))
        low_52w      = r2(float(hist["Low"].min()))
        drawdown_52w = r2((high_52w - price) / high_52w * 100) if high_52w else None

        tr = pd.DataFrame({
            "hl":  hist["High"] - hist["Low"],
            "hpc": (hist["High"] - close.shift(1)).abs(),
            "lpc": (hist["Low"]  - close.shift(1)).abs(),
        }).max(axis=1)
        atr = r2(float(tr.ewm(span=14, adjust=False).mean().iloc[-1]))

        return {
            "ticker":        entry["ticker"],
            "symbol":        entry["symbol"],
            "company":       entry["company"],
            "industry":      entry["industry"],
            "sector":        entry["industry"],   # overwritten when fundamentals arrive
            "price":         price,
            "avg_volume":    int(avg_vol),
            "rsi":           rsi,
            "ema20":         ema20,
            "ema50":         ema50,
            "high_52w":      high_52w,
            "low_52w":       low_52w,
            "drawdown_52w":  drawdown_52w,
            "atr":           atr,
            # fundamentals filled in Pass 2
            "roe_pct":       None,
            "fwd_pe":        None,
            "market_cap":    None,
            "net_margin_pct":None,
            "de_ratio":      None,
            "rev_growth_pct":None,
            "fcf":           None,
            "beta":          None,
        }
    except Exception:
        return None


# ── Pass 2 — Fundamentals for technical survivors ──────────────────────────────

def enrich_fundamentals(ticker_dict: Dict, yf) -> Dict:
    """
    Fetch Ticker.info for one ticker and merge fundamental fields.
    Returns the same dict enriched in-place (fundamentals filled or kept None).
    """
    ticker = ticker_dict["ticker"]
    for attempt in range(1, 3):
        try:
            info = yf.Ticker(ticker).info
            if not info:
                raise ValueError("empty info")

            roe_raw = safe(info, "returnOnEquity")
            pe_raw  = safe(info, "forwardPE") or safe(info, "trailingPE")
            pm_raw  = safe(info, "profitMargins")
            rg_raw  = safe(info, "revenueGrowth")

            ticker_dict.update({
                "sector":         info.get("sector") or info.get("industry") or ticker_dict["industry"],
                "roe_pct":        r2(roe_raw * 100) if roe_raw is not None else None,
                "fwd_pe":         pe_raw,
                "market_cap":     info.get("marketCap"),
                "net_margin_pct": r2(pm_raw * 100) if pm_raw is not None else None,
                "de_ratio":       safe(info, "debtToEquity"),
                "rev_growth_pct": r2(rg_raw * 100) if rg_raw is not None else None,
                "fcf":            info.get("freeCashflow"),
                "beta":           safe(info, "beta"),
            })
            return ticker_dict
        except Exception:
            if attempt < 2:
                time.sleep(INFO_RETRY)

    return ticker_dict   # fundamentals stay None — still passed through filters


# ── Filters ────────────────────────────────────────────────────────────────────

def apply_technical_filters(candidates: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """Apply the 4 purely technical filters (RSI, EMA, Volume, Drawdown)."""
    summary: Dict[str, int] = {}
    pool = candidates[:]

    lo, hi = FILTERS["rsi_range"]
    pre  = len(pool)
    pool = [t for t in pool if t["rsi"] is not None and lo <= t["rsi"] <= hi]
    summary["rsi_35_65"] = pre - len(pool)

    pre  = len(pool)
    pool = [t for t in pool
            if t["ema20"] is not None and t["ema50"] is not None
            and t["ema20"] > t["ema50"]]
    summary["ema20_above_ema50"] = pre - len(pool)

    pre  = len(pool)
    pool = [t for t in pool if t["avg_volume"] >= FILTERS["volume_min"]]
    summary["volume_min_500k"] = pre - len(pool)

    def _drawdown(t: Dict) -> Optional[float]:
        if t.get("drawdown_52w") is not None:
            return t["drawdown_52w"]
        pfh = t.get("pct_from_high")
        return abs(pfh) if pfh is not None else None

    pre  = len(pool)
    pool = [t for t in pool
            if _drawdown(t) is None or _drawdown(t) < FILTERS["max_drawdown_52w"]]
    summary["drawdown_52w_below_40pct"] = pre - len(pool)

    return pool, summary


def apply_fundamental_filters(candidates: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """Apply P/E sector-median and ROE filters after fundamentals are loaded."""
    summary: Dict[str, int] = {}
    pool = candidates[:]

    # P/E < sector median × 1.2
    sector_pes: Dict[str, List[float]] = {}
    for t in pool:
        pe = t["fwd_pe"]
        if pe and pe > 0:
            sector_pes.setdefault(t["sector"], []).append(pe)
    sector_median: Dict[str, float] = {
        s: float(pd.Series(pes).median()) for s, pes in sector_pes.items() if pes
    }
    pre    = len(pool)
    factor = FILTERS["pe_sector_factor"]
    pool   = [
        t for t in pool
        if t["fwd_pe"] is None
        or sector_median.get(t["sector"]) is None
        or t["fwd_pe"] <= sector_median[t["sector"]] * factor
    ]
    summary["pe_below_sector_median_1.2x"] = pre - len(pool)

    # ROE > 12%
    pre  = len(pool)
    pool = [t for t in pool
            if t["roe_pct"] is None or t["roe_pct"] >= FILTERS["roe_min"]]
    summary["roe_above_12pct"] = pre - len(pool)

    return pool, summary


# ── Main pipeline ──────────────────────────────────────────────────────────────

def run_screener(tickers_csv: Optional[str] = None) -> Tuple[List[Dict], Dict[str, int]]:
    """
    Run the two-pass screener on the Nifty 500 universe.
    Saves results to data/results/screener_YYYYMMDD.json.
    Returns (survivors, filter_summary).
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("Missing: pip install yfinance")
        sys.exit(1)

    universe = load_universe()
    log.info("Universe: %d tickers loaded", len(universe))

    t_start = time.time()

    # ── Pass 1: Batch OHLCV + technical filters ────────────────────────────────
    log.info("Pass 1 — batch OHLCV download (%d tickers, batch=%d)...",
             len(universe), BATCH_SIZE)
    ohlcv_map = batch_download_ohlcv(universe, yf)

    technical_candidates: List[Dict] = []
    for entry in universe:
        hist = ohlcv_map.get(entry["ticker"])
        if hist is None:
            continue
        tech = compute_technical_fields(entry, hist)
        if tech:
            technical_candidates.append(tech)

    log.info("Pass 1 — computed technicals for %d tickers", len(technical_candidates))
    tech_survivors, tech_summary = apply_technical_filters(technical_candidates)
    log.info("Pass 1 — technical survivors: %d", len(tech_survivors))

    # ── Pass 2: Fundamentals for technical survivors only ──────────────────────
    log.info("Pass 2 — fetching fundamentals for %d survivors...", len(tech_survivors))
    for i, t in enumerate(tech_survivors, 1):
        enrich_fundamentals(t, yf)
        time.sleep(INFO_DELAY)
        if i % 10 == 0:
            log.info("  fundamentals: %d/%d done", i, len(tech_survivors))

    fund_survivors, fund_summary = apply_fundamental_filters(tech_survivors)

    all_summary = {**tech_summary, **fund_summary}
    log.info("Final survivors after all 6 filters: %d", len(fund_survivors))
    for name, eliminated in all_summary.items():
        log.info("  %-35s eliminated %d", name, eliminated)

    date_str = datetime.today().strftime("%Y%m%d")
    out_path = RESULTS_DIR / "screener_{}.json".format(date_str)
    payload  = {
        "scan_date":      date_str,
        "universe_size":  len(universe),
        "ohlcv_fetched":  len(ohlcv_map),
        "technical_pass": len(tech_survivors),
        "survivors":      len(fund_survivors),
        "filter_summary": all_summary,
        "filters":        FILTERS,
        "tickers":        fund_survivors,
    }
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    log.info("Saved → %s (elapsed %.0fs)", out_path, time.time() - t_start)

    return fund_survivors, all_summary


if __name__ == "__main__":
    survivors, summary = run_screener()
    print("\nSurvivors: {}".format(len(survivors)))
    print("Filter summary: {}".format(json.dumps(summary, indent=2)))
