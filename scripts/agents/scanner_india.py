#!/usr/bin/env python3
"""
India Bi-Weekly Scanner — Nifty 500
Scans the Nifty 500 universe every two weeks, applies liquidity, fundamental,
and technical filters, scores top candidates, and calls Claude for a bull
thesis + entry/exit levels per stock.

Usage:
  python scripts/agents/scanner_india.py
  python scripts/agents/scanner_india.py --top 10
  python scripts/agents/scanner_india.py --dry-run      # filter + score only, no Claude call
"""

import sys
import os
import json
import argparse
import math
import warnings
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
from typing import Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

# ── Paths ──────────────────────────────────────────────────────────────────────
AGENT_DIR   = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(AGENT_DIR)
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
DATA_DIR    = os.path.join(ROOT_DIR, "data")
OUT_DIR     = os.path.join(SCRIPTS_DIR, "data", "scans", "india")
NIFTY_CSV   = os.path.join(DATA_DIR, "ind_nifty500list (1).csv")

sys.path.insert(0, SCRIPTS_DIR)

# ── Load .env ──────────────────────────────────────────────────────────────────
def _load_env():
    candidates = [
        os.path.expanduser("~/Projects/py/IITD/AnthropicAI/.env"),
        os.path.join(ROOT_DIR, "..", "..", ".env"),
    ]
    for p in candidates:
        p = os.path.normpath(p)
        if os.path.exists(p):
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return
_load_env()

# ── Optional rich console ──────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich import box
    from rich.text import Text
    RICH = True
    CONSOLE = Console()
except ImportError:
    RICH = False
    CONSOLE = None

def _log(msg, style="dim"):
    if RICH:
        CONSOLE.print(f"  [{style}]{msg}[/{style}]")
    else:
        print(f"  {msg}")

def _section(title):
    if RICH:
        CONSOLE.print()
        CONSOLE.rule(f"[bold cyan]{title}[/bold cyan]")
        CONSOLE.print()
    else:
        print(f"\n{'─'*60}")
        print(f"  {title}")
        print(f"{'─'*60}")

# ── Market config ──────────────────────────────────────────────────────────────
from config import IN as MARKET

# ── Load Nifty 500 universe ────────────────────────────────────────────────────
def load_universe() -> List[Dict]:
    """Return list of {symbol, company, industry} from the Nifty 500 CSV."""
    try:
        import csv
        rows = []
        with open(NIFTY_CSV, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("Symbol", "").strip()
                if sym:
                    rows.append({
                        "symbol":   sym,
                        "ticker":   sym + MARKET["ticker_suffix"],
                        "company":  row.get("Company Name", sym).strip(),
                        "industry": row.get("Industry", "").strip(),
                    })
        return rows
    except FileNotFoundError:
        print(f"ERROR: Nifty 500 CSV not found at {NIFTY_CSV}")
        sys.exit(1)

# ── Math helpers ───────────────────────────────────────────────────────────────
def r2(val):
    try:
        if val is None: return None
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 2)
    except Exception:
        return None

def safe(info, key):
    val = info.get(key)
    if val is None: return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except Exception:
        return None

def fmt_cr(val_inr):
    """Format INR value as Crores string."""
    if val_inr is None: return "N/A"
    cr = val_inr / 1e7
    if cr >= 1e5: return f"₹{cr/1e2:.0f}k Cr"
    return f"₹{cr:,.0f} Cr"

def fmt_inr(val):
    if val is None: return "N/A"
    return f"₹{val:,.2f}"

# ── Technical indicators ───────────────────────────────────────────────────────
def compute_rsi(close, period=14):
    """Compute 14-period RSI using Wilder's smoothing."""
    import numpy as np
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return r2(val) if not math.isnan(val) else None

def compute_emas(close):
    """Return (ema20, ema50, ema200) or Nones if insufficient history."""
    def ema(n):
        if len(close) >= n:
            return r2(float(close.ewm(span=n, adjust=False).mean().iloc[-1]))
        return None
    return ema(20), ema(50), ema(200)

def ema_aligned(price, ema20, ema50, ema200):
    """True when price > EMA20 > EMA50 > EMA200 (bullish stack)."""
    if None in (ema20, ema50, ema200): return False
    return price > ema20 > ema50 > ema200

def volume_breakout(hist, lookback=20, multiplier=1.5):
    """
    True when latest close is above the lookback-period high AND
    latest volume is at least `multiplier` × average volume.
    """
    if len(hist) < lookback + 1:
        return False
    recent = hist.tail(lookback + 1)
    prev_high = recent["High"].iloc[:-1].max()
    last_close = float(hist["Close"].iloc[-1])
    last_vol   = float(hist["Volume"].iloc[-1])
    avg_vol    = float(hist["Volume"].iloc[-lookback - 1:-1].mean())
    return (last_close > prev_high) and (last_vol >= multiplier * avg_vol)

# ── Per-ticker analysis ────────────────────────────────────────────────────────
def fetch_and_filter(entry: Dict, yf) -> Optional[Dict]:
    """
    Fetch yfinance data for one ticker, apply all filters.
    Returns enriched dict on pass, None on fail/filter-out.
    """
    ticker = entry["ticker"]
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y", interval="1d", auto_adjust=True)
        if hist.empty or len(hist) < 50:
            return None

        import pandas as pd
        hist = hist.dropna(subset=["Close", "High", "Low", "Volume"])
        close  = hist["Close"]
        price  = r2(float(close.iloc[-1]))
        if not price:
            return None

        # ── Liquidity filter ───────────────────────────────────────────────────
        avg_vol = float(hist["Volume"].tail(20).mean())
        if avg_vol < MARKET["avg_volume_min"]:
            return None

        info = {}
        try:
            info = stock.info
        except Exception:
            pass

        mktcap = info.get("marketCap")
        if not mktcap or mktcap < MARKET["market_cap_min"]:
            return None

        # ── Fundamental filter ─────────────────────────────────────────────────
        de_ratio = safe(info, "debtToEquity")
        # yfinance returns debtToEquity as a percentage (e.g. 45 = 0.45 ratio)
        de_actual = (de_ratio / 100) if de_ratio is not None else None
        if de_actual is not None and de_actual >= MARKET["de_ratio_max"]:
            return None

        rev_growth_raw = safe(info, "revenueGrowth")  # decimal, e.g. 0.22
        rev_growth_pct = r2(rev_growth_raw * 100) if rev_growth_raw is not None else None
        if rev_growth_pct is not None and rev_growth_pct < MARKET["rev_growth_min_pct"]:
            return None

        # ── Technical filter ───────────────────────────────────────────────────
        rsi = compute_rsi(close)
        if rsi is None or not (MARKET["rsi_low"] <= rsi <= MARKET["rsi_high"]):
            return None

        ema20, ema50, ema200 = compute_emas(close)
        if not ema_aligned(price, ema20, ema50, ema200):
            return None

        vol_breakout = volume_breakout(hist)

        # ── Additional metrics for scoring ─────────────────────────────────────
        net_margin   = safe(info, "profitMargins")
        roe          = safe(info, "returnOnEquity")
        fwd_pe       = safe(info, "forwardPE")
        trailing_pe  = safe(info, "trailingPE")
        fcf          = info.get("freeCashflow")
        beta         = safe(info, "beta")
        sector       = info.get("sector") or info.get("industry") or entry["industry"]

        # 52-week range position
        high_52w = r2(float(hist["High"].max()))
        low_52w  = r2(float(hist["Low"].min()))
        pct_from_high = r2((price - high_52w) / high_52w * 100) if high_52w else None

        # ATR for entry/exit estimates
        import pandas as pd
        tr = pd.DataFrame({
            "hl":  hist["High"] - hist["Low"],
            "hpc": (hist["High"] - close.shift(1)).abs(),
            "lpc": (hist["Low"]  - close.shift(1)).abs(),
        }).max(axis=1)
        atr = r2(float(tr.ewm(span=14, adjust=False).mean().iloc[-1]))

        return {
            "ticker":          ticker,
            "symbol":          entry["symbol"],
            "company":         entry["company"],
            "industry":        entry["industry"],
            "sector":          sector,
            "price":           price,
            "market_cap":      mktcap,
            "avg_volume":      int(avg_vol),
            "de_ratio":        de_actual,
            "rev_growth_pct":  rev_growth_pct,
            "net_margin_pct":  r2(net_margin * 100) if net_margin else None,
            "roe_pct":         r2(roe * 100)         if roe else None,
            "fwd_pe":          fwd_pe,
            "trailing_pe":     trailing_pe,
            "fcf":             fcf,
            "beta":            beta,
            "rsi":             rsi,
            "ema20":           ema20,
            "ema50":           ema50,
            "ema200":          ema200,
            "ema_aligned":     True,
            "volume_breakout": vol_breakout,
            "high_52w":        high_52w,
            "low_52w":         low_52w,
            "pct_from_high":   pct_from_high,
            "atr":             atr,
        }
    except Exception:
        return None

# ── Scoring ────────────────────────────────────────────────────────────────────
def score_candidate(c: Dict) -> Tuple[int, str, List[str]]:
    """Score a filtered candidate 0–100. Returns (score, signal, reasons)."""
    score   = 50
    reasons = []

    # Revenue growth
    rg = c.get("rev_growth_pct")
    if rg is not None:
        if rg > 30:
            score += 12; reasons.append(f"Rev growth {rg:.0f}%")
        elif rg > 20:
            score += 7;  reasons.append(f"Rev growth {rg:.0f}%")
        else:
            score += 3

    # RSI sweet spot (50–60 best, 45–65 acceptable)
    rsi = c.get("rsi")
    if rsi is not None:
        if 50 <= rsi <= 60:
            score += 10; reasons.append(f"RSI {rsi:.0f} (ideal)")
        elif 45 <= rsi < 50 or 60 < rsi <= 65:
            score += 5

    # Volume breakout confirmation
    if c.get("volume_breakout"):
        score += 10; reasons.append("Volume breakout confirmed")

    # Debt/Equity
    de = c.get("de_ratio")
    if de is not None:
        if de < 0.3:
            score += 8; reasons.append(f"D/E {de:.2f} (low debt)")
        elif de < 0.6:
            score += 4

    # Profitability
    nm = c.get("net_margin_pct")
    if nm is not None and nm > 15:
        score += 6; reasons.append(f"Net margin {nm:.0f}%")

    roe = c.get("roe_pct")
    if roe is not None and roe > 15:
        score += 5; reasons.append(f"ROE {roe:.0f}%")

    # FCF positive
    fcf = c.get("fcf")
    if fcf is not None and fcf > 0:
        score += 5

    # Valuation
    fwd_pe = c.get("fwd_pe")
    if fwd_pe is not None:
        if fwd_pe < 20:
            score += 6
        elif fwd_pe > 50:
            score -= 5

    # Distance from 52w high (not too extended)
    pfh = c.get("pct_from_high")
    if pfh is not None:
        if -15 < pfh <= -5:
            score += 5; reasons.append(f"{pfh:.0f}% from 52w high (healthy pullback)")
        elif pfh < -30:
            score -= 5

    score = max(0, min(100, score))
    if   score >= 75: signal = "STRONG BUY"
    elif score >= 60: signal = "BUY"
    elif score >= 50: signal = "WATCH"
    else:             signal = "HOLD"

    return score, signal, reasons

# ── Entry / exit levels ────────────────────────────────────────────────────────
def compute_levels(c: dict) -> dict:
    """Derive entry zone, stop-loss, and targets from ATR and EMAs."""
    price = c["price"]
    atr   = c.get("atr") or (price * 0.02)
    ema20 = c.get("ema20") or price

    entry_low  = r2(max(ema20, price - atr))
    entry_high = r2(price)
    stop_loss  = r2(price - 2 * atr)
    target_1   = r2(price + 2 * atr)
    target_2   = r2(price + 3.5 * atr)

    return {
        "entry_low":  entry_low,
        "entry_high": entry_high,
        "stop_loss":  stop_loss,
        "target_1":   target_1,
        "target_2":   target_2,
    }

# ── Claude: bull thesis + entry/exit ──────────────────────────────────────────
def claude_bull_thesis(candidates: List[Dict], client) -> Dict:
    """
    One Claude call for all top candidates.
    Returns {ticker: {thesis, entry_note, exit_note}}.
    """
    if not candidates:
        return {}

    summaries = []
    for c in candidates:
        summaries.append(
            f"{c['ticker']} ({c['company']}, {c['industry']}): "
            f"price=₹{c['price']}, mktcap={fmt_cr(c['market_cap'])}, "
            f"rev_growth={c.get('rev_growth_pct','?')}%, "
            f"D/E={c.get('de_ratio','?')}, RSI={c.get('rsi','?')}, "
            f"net_margin={c.get('net_margin_pct','?')}%, "
            f"fwd_PE={c.get('fwd_pe','?')}, "
            f"volume_breakout={c.get('volume_breakout',False)}, "
            f"score={c.get('score',0)}/100"
        )

    prompt = (
        "You are a senior Indian equity analyst specialising in NSE-listed stocks. "
        "For each stock below, provide:\n"
        "1. A 2-sentence bull thesis specific to India macro/sector tailwinds.\n"
        "2. A concise entry note (ideal entry range or condition).\n"
        "3. A concise exit note (primary target and stop-loss rationale).\n"
        "Hold period for all ideas: 3-4 weeks.\n\n"
        "Stocks:\n"
        + "\n".join(summaries)
        + "\n\nReturn ONLY a JSON object:\n"
        "{\n"
        '  "SYMBOL.NS": {\n'
        '    "thesis": "...",\n'
        '    "entry_note": "...",\n'
        '    "exit_note": "..."\n'
        "  },\n"
        "  ...\n"
        "}\n"
        "Be specific. No generic disclaimers."
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        import re
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        _log(f"Claude call failed: {e}", "red")
    return {}

# ── Display ────────────────────────────────────────────────────────────────────
SIGNAL_STYLE = {
    "STRONG BUY": "bold bright_green",
    "BUY":        "green",
    "WATCH":      "cyan",
    "HOLD":       "yellow",
}

def print_results(results: List[Dict]):
    if not RICH:
        print(f"\nIndia Scan — Top {len(results)} candidates\n{'─'*70}")
        for r in results:
            print(f"  {r['rank']}. {r['ticker']} ({r['company'][:24]}) "
                  f"₹{r['price']} | Score {r['score']}/100 | {r['signal']}")
            if r.get("bull_thesis"):
                print(f"     {r['bull_thesis']}")
        return

    CONSOLE.print()
    CONSOLE.rule("[bold cyan]India Bi-Weekly Scan — Nifty 500[/bold cyan]")
    CONSOLE.print()

    tbl = Table(box=box.ROUNDED, header_style="bold white",
                border_style="bright_black", show_lines=False)
    tbl.add_column("#",         width=3,  justify="right")
    tbl.add_column("Ticker",    width=14, style="bold white")
    tbl.add_column("Company",   width=24)
    tbl.add_column("Price ₹",   width=10, justify="right")
    tbl.add_column("Mkt Cap",   width=12, justify="right")
    tbl.add_column("Rev Gr%",   width=9,  justify="right")
    tbl.add_column("D/E",       width=6,  justify="right")
    tbl.add_column("RSI",       width=6,  justify="right")
    tbl.add_column("VBrk",      width=5,  justify="center")
    tbl.add_column("Signal",    width=12, justify="center")
    tbl.add_column("Score",     width=8,  justify="center")

    for r in results:
        sig_style = SIGNAL_STYLE.get(r["signal"], "white")
        sc_style  = ("bold bright_green" if r["score"] >= 75
                     else ("yellow" if r["score"] >= 60 else "cyan"))
        de_str  = f"{r['de_ratio']:.2f}" if r.get("de_ratio") is not None else "N/A"
        rg_str  = f"{r['rev_growth_pct']:.0f}%" if r.get("rev_growth_pct") is not None else "N/A"
        rsi_str = f"{r['rsi']:.0f}" if r.get("rsi") else "N/A"
        vb_str  = "✓" if r.get("volume_breakout") else "·"
        tbl.add_row(
            str(r["rank"]),
            r["ticker"],
            r["company"][:22],
            fmt_inr(r["price"]),
            fmt_cr(r["market_cap"]),
            rg_str,
            de_str,
            rsi_str,
            vb_str,
            Text(r["signal"], style=sig_style),
            Text(f"{r['score']}/100", style=sc_style),
        )
    CONSOLE.print(tbl)

    # Detail panels for top 3
    top3 = results[:3]
    if top3 and RICH:
        from rich.columns import Columns
        CONSOLE.print()
        CONSOLE.rule("[dim]Top 3 — Bull Thesis[/dim]")
        CONSOLE.print()
        cards = []
        for r in top3:
            sig_style = SIGNAL_STYLE.get(r["signal"], "white").split()[-1]
            lvl = r.get("levels", {})
            body = (
                f"[white]Entry:[/white] [cyan]{fmt_inr(lvl.get('entry_low'))}–{fmt_inr(lvl.get('entry_high'))}[/cyan]\n"
                f"[white]Stop:[/white]  [red]{fmt_inr(lvl.get('stop_loss'))}[/red]\n"
                f"[white]T1:[/white]    [green]{fmt_inr(lvl.get('target_1'))}[/green]  "
                f"[white]T2:[/white] [green]{fmt_inr(lvl.get('target_2'))}[/green]\n"
                f"[dim]Rev: {r.get('rev_growth_pct','?')}%  D/E: {r.get('de_ratio','?')}  RSI: {r.get('rsi','?')}[/dim]\n\n"
            )
            thesis = r.get("bull_thesis") or r.get("entry_note") or ""
            if thesis:
                body += f"[italic dim]{thesis[:180]}[/italic dim]"
            cards.append(Panel(
                body,
                title=f"[bold]{r['ticker']}[/bold]  [dim]{r['company'][:18]}[/dim]",
                subtitle=f"[{sig_style}]{r['signal']}[/{sig_style}] · [dim]{r['score']}/100[/dim]",
                width=38,
                border_style=sig_style,
            ))
        CONSOLE.print(Columns(cards, equal=False))
    CONSOLE.print()

# ── Save output ────────────────────────────────────────────────────────────────
def save_output(results: List[Dict]) -> str:
    os.makedirs(OUT_DIR, exist_ok=True)
    date_str  = datetime.today().strftime("%Y-%m-%d")
    out_path  = os.path.join(OUT_DIR, f"scan_{date_str}.json")

    payload = {
        "scan_date":       date_str,
        "market":          MARKET["code"],
        "universe":        MARKET["universe"],
        "scan_interval":   MARKET["scan_interval"],
        "hold_period":     MARKET["hold_period"],
        "filters": {
            "avg_volume_min":     MARKET["avg_volume_min"],
            "market_cap_min_inr": MARKET["market_cap_min"],
            "de_ratio_max":       MARKET["de_ratio_max"],
            "rev_growth_min_pct": MARKET["rev_growth_min_pct"],
            "rsi_range":          [MARKET["rsi_low"], MARKET["rsi_high"]],
            "ema_alignment":      "price > EMA20 > EMA50 > EMA200",
        },
        "candidates": results,
    }

    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)
    return out_path

# ── Main pipeline ──────────────────────────────────────────────────────────────
def run_scanner(top_n: int = 8, dry_run: bool = False) -> List[Dict]:
    try:
        import yfinance as yf
    except ImportError:
        print("Missing: pip install yfinance"); sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key and not dry_run:
        print("ANTHROPIC_API_KEY not set — running in dry-run mode (no Claude call).")
        dry_run = True

    client = None
    if not dry_run:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key, timeout=90.0)
        except ImportError:
            print("Missing: pip install anthropic"); sys.exit(1)

    _section("India Bi-Weekly Scanner — Nifty 500")

    universe = load_universe()
    _log(f"Universe: {len(universe)} stocks loaded from {MARKET['universe_file']}")
    _log(f"Filters: vol>{MARKET['avg_volume_min']:,} · mktcap>₹1000Cr · "
         f"D/E<{MARKET['de_ratio_max']} · rev_growth>{MARKET['rev_growth_min_pct']}% · "
         f"RSI {MARKET['rsi_low']}–{MARKET['rsi_high']} · EMA aligned")
    _log(f"Fetching data for {len(universe)} tickers (this takes a few minutes)...")

    passed = []
    for i, entry in enumerate(universe, 1):
        if i % 50 == 0:
            _log(f"  Progress: {i}/{len(universe)} | passed so far: {len(passed)}")
        result = fetch_and_filter(entry, yf)
        if result:
            passed.append(result)

    _log(f"Passed all filters: {len(passed)} stocks")

    if not passed:
        _log("No stocks passed filters. Try relaxing thresholds.", "yellow")
        return []

    # Score each candidate
    for c in passed:
        score, signal, reasons = score_candidate(c)
        c["score"]   = score
        c["signal"]  = signal
        c["reasons"] = reasons
        c["levels"]  = compute_levels(c)

    # Sort by score, take top N
    passed.sort(key=lambda x: x["score"], reverse=True)
    top = passed[:top_n]
    for i, c in enumerate(top, 1):
        c["rank"] = i

    _log(f"Top {len(top)} candidates selected for Claude analysis")

    # Claude bull thesis
    if not dry_run and client:
        _log("Calling Claude for bull thesis and entry/exit levels...")
        ai_data = claude_bull_thesis(top, client)
        for c in top:
            ai = ai_data.get(c["ticker"], {})
            c["bull_thesis"]  = ai.get("thesis", "")
            c["entry_note"]   = ai.get("entry_note", "")
            c["exit_note"]    = ai.get("exit_note", "")
    else:
        for c in top:
            c["bull_thesis"] = c["entry_note"] = c["exit_note"] = ""

    print_results(top)

    out_path = save_output(top)
    _log(f"Results saved → {out_path}", "green")

    return top


# ── CLI ────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="India Bi-Weekly Scanner — Nifty 500")
    parser.add_argument("--top",     type=int, default=8,
                        help="Number of top candidates to output (default 8)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run filters and scoring only — skip Claude API call")
    args = parser.parse_args()

    run_scanner(top_n=args.top, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
