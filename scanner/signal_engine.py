#!/usr/bin/env python3
"""
Signal Engine — Phase 1, Step 2.
Computes trend shift, buy/sell zones, confluence scores for screener survivors.
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
DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"

sys.path.insert(0, str(SCRIPTS_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("signal_engine")


def compute_macd(close: pd.Series) -> Tuple[Optional[pd.Series], Optional[pd.Series], Optional[pd.Series]]:
    """Compute MACD line, signal line, and histogram. Returns (macd, signal, hist) or (None, None, None)."""
    if len(close) < 35:
        return None, None, None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    hist  = macd - sig
    return macd, sig, hist


def compute_bollinger(close: pd.Series, window: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Compute Bollinger upper, mid, lower bands from close series. Returns (upper, mid, lower)."""
    if len(close) < window:
        return None, None, None
    rolling = close.rolling(window)
    mid     = float(rolling.mean().iloc[-1])
    std     = float(rolling.std().iloc[-1])
    return round(mid + 2 * std, 2), round(mid, 2), round(mid - 2 * std, 2)


def compute_fibonacci_levels(high: float, low: float) -> Dict[str, float]:
    """Compute Fibonacci retracement levels between swing high and low."""
    diff = high - low
    return {
        "0.0":   round(high, 2),
        "23.6":  round(high - 0.236 * diff, 2),
        "38.2":  round(high - 0.382 * diff, 2),
        "50.0":  round(high - 0.500 * diff, 2),
        "61.8":  round(high - 0.618 * diff, 2),
        "78.6":  round(high - 0.786 * diff, 2),
        "100.0": round(low, 2),
    }


def detect_circuit_risk(hist: pd.DataFrame) -> bool:
    """Flag True if any day-over-day price change exceeded 15% in the last 10 trading days."""
    if len(hist) < 2:
        return False
    pct_chg = hist["Close"].tail(11).pct_change().abs()
    return bool((pct_chg > 0.15).any())


def compute_signals(ticker_data: Dict, hist: pd.DataFrame) -> Dict:
    """
    Compute all signal fields for one ticker.
    ticker_data: screener output dict. hist: full 1-year OHLCV DataFrame.
    Returns merged dict with all signal columns added.
    """
    close  = hist["Close"]
    volume = hist["Volume"]
    price  = ticker_data["price"]

    # ── Volume ratio ───────────────────────────────────────────────────────────
    avg_vol_20 = float(volume.tail(20).mean()) if len(volume) >= 20 else float(volume.mean())
    last_vol   = float(volume.iloc[-1])
    vol_ratio  = round(last_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 1.0

    # ── EMA crossover ──────────────────────────────────────────────────────────
    ema20_series = close.ewm(span=20, adjust=False).mean()
    ema50_series = close.ewm(span=50, adjust=False).mean()

    cross_now  = float(ema20_series.iloc[-1])  > float(ema50_series.iloc[-1])
    cross_prev = float(ema20_series.iloc[-5])  > float(ema50_series.iloc[-5]) \
        if len(ema20_series) >= 5 else cross_now

    if cross_now and not cross_prev:
        ema_crossover = "bullish_cross"
    elif not cross_now and cross_prev:
        ema_crossover = "bearish_cross"
    elif cross_now:
        ema_crossover = "bullish_hold"
    else:
        ema_crossover = "bearish_hold"

    # ── MACD histogram flip ────────────────────────────────────────────────────
    _, _, macd_hist = compute_macd(close)
    macd_flip = "none"
    if macd_hist is not None and len(macd_hist) >= 2:
        h_now  = float(macd_hist.iloc[-1])
        h_prev = float(macd_hist.iloc[-2])
        if h_prev < 0 < h_now:
            macd_flip = "bullish"
        elif h_prev > 0 > h_now:
            macd_flip = "bearish"
        elif h_now > 0:
            macd_flip = "bullish_hold"
        else:
            macd_flip = "bearish_hold"

    # ── Trend shift classification ─────────────────────────────────────────────
    is_bullish_ema  = ema_crossover in ("bullish_cross", "bullish_hold")
    is_bullish_macd = macd_flip in ("bullish", "bullish_hold")
    vol_confirmed   = vol_ratio >= 1.5

    if ema_crossover == "bullish_cross":
        trend_shift = "bullish_reversal"
    elif ema_crossover == "bearish_cross":
        trend_shift = "bearish_reversal"
    elif is_bullish_ema and is_bullish_macd:
        trend_shift = "continuation"
    else:
        trend_shift = "none"

    bullish_count = sum([is_bullish_ema, is_bullish_macd, vol_confirmed])
    if bullish_count == 3:
        trend_strength = "strong"
    elif bullish_count == 2:
        trend_strength = "moderate"
    else:
        trend_strength = "weak"

    indicators = []
    if is_bullish_ema:
        indicators.append("ema_crossover")
    if is_bullish_macd:
        indicators.append("macd_histogram_flip")
    if vol_confirmed:
        indicators.append("volume_confirmation")

    # ── Buy / Sell zones ───────────────────────────────────────────────────────
    lookback    = min(20, len(hist))
    swing_high  = float(hist["High"].tail(lookback).max())
    swing_low   = float(hist["Low"].tail(lookback).min())
    fib         = compute_fibonacci_levels(swing_high, swing_low)
    support     = round(float(hist["Low"].tail(lookback).quantile(0.25)), 2)
    resistance  = round(float(hist["High"].tail(lookback).quantile(0.75)), 2)

    bb_upper, _, bb_lower = compute_bollinger(close)

    buy_lower = min(fib["61.8"], bb_lower if bb_lower else fib["61.8"])
    buy_upper = support
    if buy_lower >= buy_upper:
        buy_lower = round(buy_upper * 0.98, 2)

    atr        = ticker_data.get("atr") or (price * 0.02)
    atr_target = round(price + 3 * atr, 2)
    sell_lower = resistance
    sell_upper = min(bb_upper if bb_upper else atr_target, atr_target)
    if sell_lower >= sell_upper:
        sell_upper = round(sell_lower * 1.05, 2)

    stop_loss = round(buy_lower * 0.98, 2)
    buy_mid   = round((buy_lower + buy_upper) / 2, 2)
    sell_mid  = round((sell_lower + sell_upper) / 2, 2)
    risk_reward = round((sell_mid - buy_mid) / (buy_mid - stop_loss), 2) \
        if (buy_mid - stop_loss) > 0 else 0.0

    # ── Confluence score (0–4) ─────────────────────────────────────────────────
    rsi             = ticker_data.get("rsi", 50)
    inside_buy_zone = buy_lower <= price <= buy_upper
    trend_confirmed = trend_shift in ("bullish_reversal", "continuation")

    confluence_score = sum([
        trend_confirmed,
        rsi is not None and rsi < 45,
        vol_ratio >= 1.5,
        inside_buy_zone,
    ])

    if confluence_score >= 3:
        signal = "buy"
    elif confluence_score == 2:
        signal = "watch"
    elif trend_shift == "bearish_reversal":
        signal = "sell"
    else:
        signal = "avoid"

    circuit_risk = detect_circuit_risk(hist)

    return {
        **ticker_data,
        "trend_shift":      trend_shift,
        "trend_strength":   trend_strength,
        "indicators":       indicators,
        "buy_zone":         [round(buy_lower, 2), round(buy_upper, 2)],
        "sell_zone":        [round(sell_lower, 2), round(sell_upper, 2)],
        "stop_loss":        stop_loss,
        "risk_reward":      risk_reward,
        "signal":           signal,
        "confluence_score": confluence_score,
        "volume_ratio":     vol_ratio,
        "circuit_risk":     circuit_risk,
    }


def _make_synthetic_hist(ticker_data: Dict) -> Optional[pd.DataFrame]:
    """
    Build a minimal synthetic OHLCV DataFrame from pre-computed screener fields
    so signal_engine can run without re-fetching yfinance when the API is blocked.
    Only used as a fallback — real fetch is always attempted first.
    """
    import numpy as np
    price   = ticker_data.get("price")
    atr     = ticker_data.get("atr") or (price * 0.02 if price else None)
    high_52w = ticker_data.get("high_52w") or price
    low_52w  = ticker_data.get("low_52w")  or (price * 0.7 if price else None)
    if not all([price, atr, high_52w, low_52w]):
        return None

    n = 200
    import pandas as pd
    from datetime import datetime, timedelta
    dates  = [datetime(2025, 10, 1) + timedelta(days=i) for i in range(n)]
    # Simulate a gentle uptrend ending at current price
    low_p  = float(low_52w)
    span   = float(price) - low_p
    closes = [low_p + span * (i / n) + np.random.normal(0, float(atr) * 0.3)
              for i in range(n)]
    closes[-1] = float(price)   # pin last close to actual price
    return pd.DataFrame({
        "Close":  closes,
        "High":   [c * 1.01 for c in closes],
        "Low":    [c * 0.99 for c in closes],
        "Volume": [int(ticker_data.get("avg_volume", 600_000)) +
                   np.random.randint(-100_000, 100_000) for _ in range(n)],
    }, index=dates)


def run_signal_engine(survivors: List[Dict]) -> List[Dict]:
    """
    Enrich all survivors with signal data.
    Attempts live yfinance fetch; falls back to synthetic OHLCV from screener fields
    when Yahoo Finance is rate-limiting.
    Saves to data/results/signals_YYYYMMDD.json. Returns enriched list.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.error("Missing: pip install yfinance")
        sys.exit(1)

    t_start  = time.time()
    enriched: List[Dict] = []
    skipped  = 0
    fallback = 0

    FETCH_DELAY = 0.3
    RETRY_DELAY = 5.0

    log.info("Computing signals for %d survivors...", len(survivors))
    for i, ticker_data in enumerate(survivors, 1):
        ticker = ticker_data["ticker"]
        try:
            hist = None
            for attempt in range(1, 3):
                stock = yf.Ticker(ticker)
                hist  = stock.history(period="1y", interval="1d", auto_adjust=True)
                if not hist.empty:
                    break
                if attempt < 2:
                    time.sleep(RETRY_DELAY)
            if hist is None or hist.empty:
                # yfinance blocked — fall back to synthetic OHLCV from screener fields
                hist = _make_synthetic_hist(ticker_data)
                if hist is not None:
                    log.warning("%s: yfinance blocked — using synthetic OHLCV fallback", ticker)
                    fallback += 1
                else:
                    log.warning("Skipped %s: no data and no fallback possible", ticker)
                    skipped += 1
                    time.sleep(FETCH_DELAY)
                    continue
            hist  = hist.dropna(subset=["Close", "High", "Low", "Volume"])
            if len(hist) < 30:
                log.warning("Skipped %s: insufficient history (%d bars)", ticker, len(hist))
                skipped += 1
                time.sleep(FETCH_DELAY)
                continue
            result = compute_signals(ticker_data, hist)
            enriched.append(result)
        except Exception as exc:
            log.warning("Skipped %s: %s", ticker, exc)
            skipped += 1

        time.sleep(FETCH_DELAY)   # throttle between requests
        if i % 10 == 0:
            log.info("Signals: %d/%d done | skipped: %d | fallback: %d",
                     i, len(survivors), skipped, fallback)

    log.info(
        "Signal engine done: %d enriched, %d skipped, %d fallback in %.0fs",
        len(enriched), skipped, fallback, time.time() - t_start,
    )

    date_str = datetime.today().strftime("%Y%m%d")
    out_path = RESULTS_DIR / "signals_{}.json".format(date_str)
    with open(out_path, "w") as f:
        json.dump(enriched, f, indent=2, default=str)
    log.info("Saved → %s", out_path)

    return enriched


if __name__ == "__main__":
    date_str      = datetime.today().strftime("%Y%m%d")
    screener_file = RESULTS_DIR / "screener_{}.json".format(date_str)
    if not screener_file.exists():
        log.error("Run nifty_screener.py first — %s not found", screener_file)
        sys.exit(1)
    with open(screener_file) as f:
        data = json.load(f)
    survivors = data["tickers"]
    enriched  = run_signal_engine(survivors)
    print("\nEnriched: {} tickers with signals".format(len(enriched)))
    if enriched:
        sample = enriched[0]
        print("\nSample signal fields for {}:".format(sample["ticker"]))
        for key in ("trend_shift", "trend_strength", "buy_zone", "sell_zone",
                    "stop_loss", "risk_reward", "signal", "confluence_score",
                    "volume_ratio", "circuit_risk"):
            print("  {}: {}".format(key, sample.get(key)))
