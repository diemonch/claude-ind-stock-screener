#!/usr/bin/env python3
"""
AI Portfolio Manager
Usage:
  python scripts/portfolio.py              # full dashboard (holdings + watchlist)
  python scripts/portfolio.py --holdings   # holdings only
  python scripts/portfolio.py --watchlist  # watchlist only
  python scripts/portfolio.py --add TICKER SHARES AVG_COST [DATE]
  python scripts/portfolio.py --remove TICKER
  python scripts/portfolio.py --watch TICKER   # add to watchlist
  python scripts/portfolio.py --unwatch TICKER # remove from watchlist
  python scripts/portfolio.py --json           # machine-readable output
"""

import sys
import os
import json
import subprocess
import argparse
from datetime import datetime

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.columns import Columns
    from rich import box
    RICH = True
except ImportError:
    RICH = False

CONSOLE = Console() if RICH else None

PORTFOLIO_FILE = os.path.join(os.path.dirname(__file__), "..", "portfolio.json")
ANALYZE_SCRIPT = os.path.join(os.path.dirname(__file__), "analyze.py")


def _find_python_with_yfinance():
    """Return the Python executable that has a working yfinance (can fetch live data)."""
    # Probe script: fetch a minimal SPY quote to validate the yfinance version works
    probe = (
        "import yfinance as yf, sys; "
        "t = yf.Ticker('SPY'); "
        "h = t.history(period='1d', auto_adjust=True); "
        "sys.exit(0 if not h.empty else 1)"
    )
    # Prefer known-good paths first, then fall back to PATH-resolved ones
    candidates = [
        "/opt/homebrew/bin/python3.9",
        "/opt/homebrew/bin/python3",
        sys.executable,
        "/usr/local/bin/python3",
        "python3",
        "python",
    ]
    for py in candidates:
        try:
            r = subprocess.run(
                [py, "-c", probe],
                capture_output=True, timeout=15
            )
            if r.returncode == 0:
                return py
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return sys.executable  # fallback — will fail at runtime with a clear error


PYTHON = _find_python_with_yfinance()


# ─── Portfolio I/O ────────────────────────────────────────────────────────────

def load_portfolio():
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)
    print(f"Portfolio saved to {PORTFOLIO_FILE}")


# ─── Analysis runner ──────────────────────────────────────────────────────────

def run_analysis(ticker):
    """Run analyze.py --json for a ticker and return parsed result or error dict."""
    try:
        result = subprocess.run(
            [PYTHON, ANALYZE_SCRIPT, ticker, "--json"],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout.strip()
        if not output:
            return {"error": f"No output from analyze.py for {ticker}", "ticker": ticker}
        return json.loads(output)
    except subprocess.TimeoutExpired:
        return {"error": f"Timeout fetching data for {ticker}", "ticker": ticker}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse error for {ticker}: {e}", "ticker": ticker}
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


# ─── Scoring & signals ────────────────────────────────────────────────────────

def score_stock(data):
    """
    Score a stock 0–100 for portfolio attractiveness.
    Considers: analyst upside, valuation (PEG, P/E), growth, FCF, technical position.
    Returns (score, signal_label, key_reasons).
    """
    if "error" in data:
        return None, "N/A", ["Data unavailable"]

    score = 50  # neutral start
    reasons = []

    price = data.get("current_price", 0)
    f = data.get("fundamentals") or {}
    atr_data = data.get("atr") or {}
    ma = data.get("moving_averages") or {}
    analyst = f.get("analyst_estimates") or {}
    valuation = f.get("valuation") or {}
    growth = f.get("growth") or {}
    profitability = f.get("profitability") or {}
    health = f.get("financial_health") or {}

    # --- Analyst upside ---
    upside = analyst.get("upside_to_mean_target_pct")
    rec = (analyst.get("recommendation") or "").lower()
    if upside is not None:
        if upside > 40:
            score += 15
            reasons.append(f"+{upside:.0f}% analyst upside")
        elif upside > 20:
            score += 8
            reasons.append(f"+{upside:.0f}% analyst upside")
        elif upside < 0:
            score -= 10
            reasons.append(f"{upside:.0f}% analyst downside")
    if "buy" in rec or "strong_buy" in rec:
        score += 5
    elif "sell" in rec or "underperform" in rec:
        score -= 10

    # --- Valuation ---
    peg = valuation.get("peg_ratio")
    fwd_pe = valuation.get("forward_pe")
    if peg is not None:
        if peg < 1.0:
            score += 12
            reasons.append(f"PEG {peg:.2f} (undervalued for growth)")
        elif peg < 2.0:
            score += 4
            reasons.append(f"PEG {peg:.2f} (fair)")
        else:
            score -= 8
            reasons.append(f"PEG {peg:.2f} (expensive)")
    if fwd_pe is not None:
        if fwd_pe < 20:
            score += 8
        elif fwd_pe > 60:
            score -= 8

    # --- Revenue growth ---
    rev_growth = growth.get("revenue_growth_yoy_pct")
    if rev_growth is not None:
        if rev_growth > 30:
            score += 12
            reasons.append(f"{rev_growth:.0f}% revenue growth")
        elif rev_growth > 15:
            score += 6
        elif rev_growth < 0:
            score -= 8

    # --- Profitability ---
    fcf = health.get("free_cash_flow")
    net_margin = profitability.get("net_margin_pct")
    if fcf is not None:
        if fcf > 0:
            score += 6
        else:
            score -= 8
            reasons.append("Negative FCF")
    if net_margin is not None and net_margin > 20:
        score += 5

    # --- Technical: price vs EMAs ---
    ema200 = ma.get("ema_200")
    ema50 = ma.get("ema_50")
    ema20 = ma.get("ema_20")
    if price and ema200:
        if price > ema200:
            score += 5
            reasons.append("Above EMA 200 (uptrend)")
        else:
            score -= 5
            reasons.append("Below EMA 200 (downtrend)")
    if price and ema50 and price > ema50:
        score += 3
    if price and ema20 and price > ema20:
        score += 2

    # --- Clamp ---
    score = max(0, min(100, score))

    if score >= 70:
        label = "STRONG BUY"
    elif score >= 55:
        label = "BUY"
    elif score >= 45:
        label = "HOLD"
    elif score >= 30:
        label = "WATCH"
    else:
        label = "AVOID"

    return score, label, reasons[:3]


# ─── P&L calculation ─────────────────────────────────────────────────────────

def calc_pnl(holding, current_price):
    shares = holding.get("shares", 0)
    avg_cost = holding.get("avg_cost", 0)
    if shares == 0 or avg_cost == 0:
        return None
    cost_basis = shares * avg_cost
    market_value = shares * current_price
    gain = market_value - cost_basis
    gain_pct = (gain / cost_basis) * 100
    return {
        "shares": shares,
        "avg_cost": avg_cost,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "gain": gain,
        "gain_pct": gain_pct
    }


# ─── Formatters ───────────────────────────────────────────────────────────────

def fmt_price(v):
    if v is None:
        return "N/A"
    return f"${v:,.2f}"


def fmt_pct(v, plus=True):
    if v is None:
        return "N/A"
    prefix = "+" if (plus and v > 0) else ""
    return f"{prefix}{v:.1f}%"


def fmt_large(v):
    if v is None:
        return "N/A"
    if abs(v) >= 1e12:
        return f"${v/1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"


def col(text, width):
    return str(text)[:width].ljust(width)


def sep(widths, char="─"):
    return "┼".join(char * w for w in widths)


def row(*cells_widths):
    parts = []
    for cell, width in cells_widths:
        parts.append(str(cell)[:width].ljust(width))
    return "│".join(parts)


# ─── Rich color helpers ───────────────────────────────────────────────────────

SIGNAL_STYLE = {
    "STRONG BUY":      "bold bright_green",
    "BUY":             "green",
    "HOLD":            "yellow",
    "HOLD WITH CAUTION": "dark_orange",
    "WATCH":           "cyan",
    "CONSIDER CUTTING": "red",
    "AVOID":           "bold red",
    "DANGER":          "bold white on red",
    "ERR":             "dim red",
    "N/A":             "dim",
}

ACTION_STYLE = {
    "ADD":    "bold bright_green",
    "HOLD":   "yellow",
    "TRIM":   "dark_orange",
    "WATCH":  "cyan",
    "DANGER": "bold white on red",
    "AVOID":  "bold red",
    "N/A":    "dim",
}


def rsignal(signal):
    """Return a rich-styled Text for a signal label."""
    style = SIGNAL_STYLE.get(signal, "white")
    return Text(signal, style=style)


def raction(action):
    style = ACTION_STYLE.get(action, "white")
    return Text(action, style=style)


def rpnl(value, fmt):
    """Green if positive, red if negative."""
    if value is None or fmt == "N/A":
        return Text("N/A", style="dim")
    style = "green" if value >= 0 else "red"
    return Text(fmt, style=style)


def rscore(score):
    if score is None:
        return Text("N/A", style="dim")
    if score >= 70:
        style = "bold bright_green"
    elif score >= 55:
        style = "green"
    elif score >= 40:
        style = "yellow"
    else:
        style = "red"
    return Text(f"{score}/100", style=style)


# ─── Display: Holdings Dashboard ─────────────────────────────────────────────

def print_holdings_dashboard(portfolio, analyses):
    holdings = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
    if not holdings:
        if RICH:
            CONSOLE.print(Panel("No holdings with shares > 0.\nAdd positions with [bold]--add TICKER SHARES AVG_COST[/bold]", style="yellow"))
        else:
            print("No holdings. Use --add TICKER SHARES AVG_COST")
        return

    total_cost = total_value = 0

    if not RICH:
        # Plain fallback
        print(f"\n{'═'*80}")
        print(f"  {portfolio['name']} — Holdings Dashboard  {datetime.today().strftime('%Y-%m-%d')}")
        print(f"{'═'*80}\n")
        W = [7, 10, 8, 8, 10, 9, 10, 14, 10]
        print("  " + row(("TICKER",W[0]),("PRICE",W[1]),("SHARES",W[2]),("AVG COST",W[3]),
                          ("MKT VALUE",W[4]),("GAIN $",W[5]),("GAIN %",W[6]),("SIGNAL",W[7]),("SCORE",W[8])))
        print(f"  {'─'*sum(W)}")
        for h in holdings:
            t = h["ticker"]
            data = analyses.get(t, {})
            price = data.get("current_price") if "error" not in data else None
            score, signal, _ = score_stock(data) if "error" not in data else (None, "ERR", [])
            pnl = calc_pnl(h, price) if price else None
            if pnl:
                total_cost += pnl["cost_basis"]; total_value += pnl["market_value"]
            print("  " + row((t,W[0]),(fmt_price(price),W[1]),(str(h["shares"]),W[2]),
                              (fmt_price(h["avg_cost"]),W[3]),
                              (fmt_large(pnl["market_value"]) if pnl else "N/A",W[4]),
                              (fmt_large(pnl["gain"]) if pnl else "N/A",W[5]),
                              (fmt_pct(pnl["gain_pct"]) if pnl else "N/A",W[6]),
                              (signal,W[7]),(f"{score}/100" if score else "N/A",W[8])))
        if total_cost > 0:
            g = total_value - total_cost
            print("  " + row(("TOTAL",W[0]),("",W[1]),("",W[2]),("",W[3]),
                              (fmt_large(total_value),W[4]),(fmt_large(g),W[5]),
                              (fmt_pct(g/total_cost*100),W[6]),("",W[7]),("",W[8])))
        return

    # ── Rich version ──────────────────────────────────────────────────────────
    today = datetime.today().strftime("%Y-%m-%d")
    CONSOLE.print()
    CONSOLE.rule(f"[bold cyan]{portfolio['name']} — Holdings Dashboard[/bold cyan]  [dim]{today}[/dim]")
    CONSOLE.print()

    tbl = Table(box=box.ROUNDED, show_footer=True, footer_style="bold",
                header_style="bold white", border_style="bright_black")
    tbl.add_column("Ticker",    style="bold white",  footer="TOTAL")
    tbl.add_column("Price",     justify="right",     footer="")
    tbl.add_column("Shares",    justify="right",     footer="")
    tbl.add_column("Avg Cost",  justify="right",     footer="")
    tbl.add_column("Mkt Value", justify="right",     footer="")
    tbl.add_column("Gain $",    justify="right",     footer="")
    tbl.add_column("Gain %",    justify="right",     footer="")
    tbl.add_column("Signal",    justify="center",    footer="")
    tbl.add_column("Score",     justify="center",    footer="")

    for h in holdings:
        t = h["ticker"]
        data = analyses.get(t, {})
        if "error" in data:
            tbl.add_row(t, "[dim red]ERR[/]", str(h["shares"]), fmt_price(h["avg_cost"]),
                        "N/A", "N/A", "N/A", Text("ERR", style="dim red"), Text("N/A", style="dim"))
            continue

        price = data.get("current_price")
        score, signal, _ = score_stock(data)
        pnl = calc_pnl(h, price) if price else None

        if pnl:
            total_cost  += pnl["cost_basis"]
            total_value += pnl["market_value"]
            gain_t  = rpnl(pnl["gain"],     fmt_large(pnl["gain"]))
            pct_t   = rpnl(pnl["gain_pct"], fmt_pct(pnl["gain_pct"]))
            mval_t  = Text(fmt_large(pnl["market_value"]), style="white")
        else:
            gain_t = pct_t = mval_t = Text("N/A", style="dim")

        tbl.add_row(t, fmt_price(price), str(h["shares"]), fmt_price(h["avg_cost"]),
                    mval_t, gain_t, pct_t, rsignal(signal), rscore(score))

    # Footer totals
    if total_cost > 0:
        total_gain = total_value - total_cost
        total_pct  = (total_gain / total_cost) * 100
        tbl.columns[4].footer = Text(fmt_large(total_value), style="bold white")
        tbl.columns[5].footer = rpnl(total_gain, fmt_large(total_gain))
        tbl.columns[6].footer = rpnl(total_pct,  fmt_pct(total_pct))

    CONSOLE.print(tbl)

    # ── Key signals cards ─────────────────────────────────────────────────────
    CONSOLE.print()
    CONSOLE.rule("[dim]Key Signals[/dim]")
    CONSOLE.print()

    cards = []
    for h in holdings:
        t = h["ticker"]
        data = analyses.get(t, {})
        if "error" in data:
            cards.append(Panel(f"[dim red]{data['error'][:60]}[/dim red]", title=f"[bold]{t}[/bold]", width=28))
            continue
        score, signal, reasons = score_stock(data)
        f = data.get("fundamentals") or {}
        analyst = f.get("analyst_estimates") or {}
        price = data.get("current_price")
        target = analyst.get("mean_target")
        upside = analyst.get("upside_to_mean_target_pct")

        up_style = "green" if upside and upside > 0 else "red"
        body  = f"{rsignal(signal).markup if hasattr(rsignal(signal),'markup') else signal}\n"
        body  = ""
        body += f"[white]{fmt_price(price)}[/white] → [cyan]{fmt_price(target)}[/cyan]\n"
        body += f"[{up_style}]{fmt_pct(upside)} analyst upside[/{up_style}]\n"
        for r in reasons:
            body += f"[dim]• {r}[/dim]\n"

        sig_style = SIGNAL_STYLE.get(signal, "white")
        cards.append(Panel(body.strip(), title=f"[bold]{t}[/bold]  [{sig_style}]{signal}[/{sig_style}]",
                           subtitle=f"[dim]{score}/100[/dim]", width=32, border_style=sig_style.split()[-1]))

    CONSOLE.print(Columns(cards, equal=False, expand=False))
    CONSOLE.print()


# ─── Display: Watchlist Opportunities ────────────────────────────────────────

def print_watchlist_dashboard(portfolio, analyses):
    watchlist = portfolio.get("watchlist", [])
    if not watchlist:
        msg = "Watchlist is empty. Add tickers with --watch TICKER"
        CONSOLE.print(Panel(msg, style="yellow")) if RICH else print(msg)
        return

    scored = []
    for t in watchlist:
        data = analyses.get(t, {})
        if "error" in data:
            scored.append((t, None, "ERR", ["Data unavailable"], data))
        else:
            score, signal, reasons = score_stock(data)
            scored.append((t, score, signal, reasons, data))
    scored.sort(key=lambda x: x[1] if x[1] is not None else -1, reverse=True)

    if not RICH:
        print(f"\n{'═'*80}\n  WATCHLIST — New Entry Opportunities\n{'═'*80}\n")
        W = [7, 10, 10, 9, 9, 8, 8, 14, 10]
        print("  " + row(("TICKER",W[0]),("PRICE",W[1]),("MKT CAP",W[2]),("REV GR%",W[3]),
                          ("FWD P/E",W[4]),("PEG",W[5]),("UPSIDE%",W[6]),("SIGNAL",W[7]),("SCORE",W[8])))
        print(f"  {'─'*sum(W)}")
        for t, score, signal, reasons, data in scored:
            f = data.get("fundamentals") or {}
            val = f.get("valuation") or {}
            growth = f.get("growth") or {}
            analyst = f.get("analyst_estimates") or {}
            mktcap_val = f.get("market_cap")
            price = data.get("current_price") if "error" not in data else None
            print("  " + row((t,W[0]),(fmt_price(price),W[1]),(fmt_large(mktcap_val),W[2]),
                              (fmt_pct(growth.get("revenue_growth_yoy_pct"),plus=False),W[3]),
                              (f"{val.get('forward_pe'):.1f}x" if val.get("forward_pe") else "N/A",W[4]),
                              (f"{val.get('peg_ratio'):.2f}" if val.get("peg_ratio") else "N/A",W[5]),
                              (fmt_pct(analyst.get("upside_to_mean_target_pct")),W[6]),
                              (signal,W[7]),(f"{score}/100" if score else "N/A",W[8])))
        return

    # ── Rich version ──────────────────────────────────────────────────────────
    CONSOLE.print()
    CONSOLE.rule("[bold cyan]Watchlist — New Entry Opportunities[/bold cyan]")
    CONSOLE.print()

    tbl = Table(box=box.ROUNDED, header_style="bold white", border_style="bright_black", show_lines=False)
    tbl.add_column("Ticker",   style="bold white")
    tbl.add_column("Price",    justify="right")
    tbl.add_column("Mkt Cap",  justify="right")
    tbl.add_column("Rev Gr%",  justify="right")
    tbl.add_column("Fwd P/E",  justify="right")
    tbl.add_column("PEG",      justify="right")
    tbl.add_column("Upside",   justify="right")
    tbl.add_column("Signal",   justify="center")
    tbl.add_column("Score",    justify="center")

    for t, score, signal, reasons, data in scored:
        if "error" in data:
            tbl.add_row(t, *["[dim]N/A[/dim]"]*7, Text("ERR", style="dim red"), Text("N/A", style="dim"))
            continue
        price = data.get("current_price")
        f     = data.get("fundamentals") or {}
        val   = f.get("valuation") or {}
        growth= f.get("growth") or {}
        analyst = f.get("analyst_estimates") or {}
        mktcap = f.get("market_cap")
        upside = analyst.get("upside_to_mean_target_pct")
        rev_gr = growth.get("revenue_growth_yoy_pct")
        fwd_pe = val.get("forward_pe")
        peg    = val.get("peg_ratio")

        up_style = "green" if upside and upside > 0 else "red"
        rg_style = "green" if rev_gr and rev_gr > 15 else ("yellow" if rev_gr and rev_gr > 0 else "red")

        tbl.add_row(
            t,
            fmt_price(price),
            fmt_large(mktcap),
            Text(fmt_pct(rev_gr, plus=False), style=rg_style),
            f"{fwd_pe:.1f}x" if fwd_pe else "[dim]N/A[/dim]",
            f"{peg:.2f}" if peg else "[dim]N/A[/dim]",
            Text(fmt_pct(upside), style=up_style),
            rsignal(signal),
            rscore(score),
        )
    CONSOLE.print(tbl)

    # ── Top picks cards ───────────────────────────────────────────────────────
    top = [s for s in scored if s[1] is not None and s[1] >= 55][:5]
    if not top:
        CONSOLE.print("[dim]No strong picks right now — consider expanding the watchlist.[/dim]\n")
        return

    CONSOLE.print()
    CONSOLE.rule("[dim]Top Picks — Entry Opportunities[/dim]")
    CONSOLE.print()

    cards = []
    for rank, (t, score, signal, reasons, data) in enumerate(top, 1):
        f = data.get("fundamentals") or {}
        analyst = f.get("analyst_estimates") or {}
        sl = data.get("suggested_levels") or {}
        supports = sl.get("strong_support") or []
        stop = sl.get("stop_loss_long")
        target = analyst.get("mean_target")
        upside = analyst.get("upside_to_mean_target_pct")
        price = data.get("current_price")
        entry = f"{fmt_price(supports[-1])}–{fmt_price(supports[0])}" if len(supports) >= 2 else fmt_price(price)
        up_style = "green" if upside and upside > 0 else "red"
        sig_style = SIGNAL_STYLE.get(signal, "white").split()[-1]

        body = f"[white]{fmt_price(price)}[/white]\n"
        body += f"Entry: [cyan]{entry}[/cyan]  Stop: [red]{fmt_price(stop)}[/red]\n"
        body += f"Target: [cyan]{fmt_price(target)}[/cyan]  [{up_style}]{fmt_pct(upside)}[/{up_style}]\n"
        for r in reasons:
            body += f"[dim]• {r}[/dim]\n"
        cards.append(Panel(body.strip(),
                           title=f"[bold]#{rank} {t}[/bold]",
                           subtitle=f"[{sig_style}]{signal}[/{sig_style}] · [dim]{score}/100[/dim]",
                           width=34, border_style=sig_style))

    CONSOLE.print(Columns(cards, equal=False))
    CONSOLE.print()


# ─── Recommendations ─────────────────────────────────────────────────────────

def _pct_from(price, level):
    """% distance of level from price (positive = above price)."""
    if not price or not level:
        return None
    return ((level - price) / price) * 100


def _technical_action(price, data, holding=None):
    """
    Return (action, reason) based on technical position.
    action: ADD | HOLD | TRIM | AVOID | WATCH | DANGER
    """
    if not price or "error" in data:
        return "N/A", "No data"

    sl = data.get("suggested_levels") or {}
    ma = data.get("moving_averages") or {}
    atr_data = data.get("atr") or {}
    pp = data.get("pivot_points") or {}

    supports = sl.get("strong_support") or []
    resistances = sl.get("strong_resistance") or []
    stop_long = sl.get("stop_loss_long")

    ema20 = ma.get("ema_20")
    ema50 = ma.get("ema_50")
    ema200 = ma.get("ema_200")
    atr = atr_data.get("atr_value")
    pivot = pp.get("pivot")

    # Distance to nearest support / resistance
    nearest_sup = min(supports, key=lambda s: abs(price - s)) if supports else None
    nearest_res = min(resistances, key=lambda r: abs(price - r)) if resistances else None
    dist_to_sup = _pct_from(price, nearest_sup)   # negative = support is below
    dist_to_res = _pct_from(price, nearest_res)   # positive = resistance is above

    # Stop-loss proximity
    if stop_long and _pct_from(price, stop_long) is not None:
        dist_to_stop = _pct_from(price, stop_long)  # negative = stop below price
        if dist_to_stop > -3:
            return "DANGER", f"Price within 3% of stop-loss {fmt_price(stop_long)}"

    # Near support (within 3%) → buy/add zone
    if dist_to_sup is not None and -3 <= dist_to_sup <= 0:
        return "ADD", f"At support {fmt_price(nearest_sup)} (entry zone)"

    # Price has bounced — within 3% above support
    if dist_to_sup is not None and 0 < dist_to_sup <= 3:
        return "ADD", f"Just above support {fmt_price(nearest_sup)} (early entry)"

    # Near resistance (within 3%) → trim zone
    if dist_to_res is not None and 0 <= dist_to_res <= 3:
        return "TRIM", f"Approaching resistance {fmt_price(nearest_res)}"

    # Above resistance → overbought short-term
    if dist_to_res is not None and dist_to_res < 0:
        return "HOLD", f"Above resistance, wait for pullback to {fmt_price(nearest_sup)}"

    # Below all EMAs → downtrend caution
    below_all = all([
        ema20 and price < ema20,
        ema50 and price < ema50,
        ema200 and price < ema200,
    ])
    if below_all:
        return "WATCH", f"Below EMA 20/50/200 — wait for base near {fmt_price(nearest_sup)}"

    # Default: in no-man's land
    return "HOLD", f"No immediate trigger — next support {fmt_price(nearest_sup)}"


def generate_daily_recommendations(portfolio, analyses):
    """Daily report: entry zones, expected ranges, stop-loss alerts, quick actions."""
    today = datetime.today().strftime("%Y-%m-%d")
    holdings = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
    watchlist = portfolio.get("watchlist", [])

    if not RICH:
        # Plain-text fallback (original logic)
        lines = [f"\n{'═'*80}", f"  DAILY RECOMMENDATION REPORT — {today}", f"  {portfolio['name']}", f"{'═'*80}"]
        for h in holdings:
            t = h["ticker"]; data = analyses.get(t, {})
            if "error" in data: continue
            price = data.get("current_price")
            action, reason = _technical_action(price, data, h)
            pnl = calc_pnl(h, price)
            if action == "DANGER": lines.append(f"  ⚠  {t} — {reason}")
            if pnl and pnl["gain_pct"] < -20: lines.append(f"  ⚠  {t} — Down {pnl['gain_pct']:.1f}%")
        W = [7,10,8,12,30]
        lines += ["\n  HOLDINGS — TODAY'S ACTION", f"  {'─'*60}",
                  "  "+row(("TICKER",W[0]),("PRICE",W[1]),("ATR±",W[2]),("ACTION",W[3]),("REASON",W[4]))]
        for h in holdings:
            t = h["ticker"]; data = analyses.get(t, {})
            if "error" in data: continue
            price = data.get("current_price"); atr = (data.get("atr") or {}).get("atr_value")
            action, reason = _technical_action(price, data, h)
            lines.append("  "+row((t,W[0]),(fmt_price(price),W[1]),(f"±{atr:.2f}" if atr else "N/A",W[2]),(action,W[3]),(reason[:28],W[4])))
        print("\n".join(lines))
        print(f"{'─'*80}\n  * Not financial advice.")
        return

    # ── Rich version ──────────────────────────────────────────────────────────
    CONSOLE.print()
    CONSOLE.rule(f"[bold yellow]Daily Recommendation Report[/bold yellow]  [dim]{today}[/dim]")
    CONSOLE.print()

    # Alerts
    alert_lines = []
    for h in holdings:
        t = h["ticker"]; data = analyses.get(t, {})
        if "error" in data: continue
        price = data.get("current_price")
        action, reason = _technical_action(price, data, h)
        pnl = calc_pnl(h, price)
        if action == "DANGER":
            alert_lines.append(f"[bold red]⚠ {t}[/bold red] — {reason}")
        if pnl and pnl["gain_pct"] < -20:
            alert_lines.append(f"[bold red]⚠ {t}[/bold red] — Down [red]{pnl['gain_pct']:.1f}%[/red] from cost {fmt_price(h['avg_cost'])}")

    if alert_lines:
        CONSOLE.print(Panel("\n".join(alert_lines), title="[bold red]⚠ Alerts[/bold red]",
                            border_style="red"))
        CONSOLE.print()

    # Holdings action table
    tbl = Table(title="[bold]Holdings — Today's Action[/bold]", box=box.ROUNDED,
                header_style="bold white", border_style="bright_black")
    tbl.add_column("Ticker",  style="bold white")
    tbl.add_column("Price",   justify="right")
    tbl.add_column("ATR ±",   justify="right", style="dim")
    tbl.add_column("Action",  justify="center")
    tbl.add_column("Reason")

    for h in holdings:
        t = h["ticker"]; data = analyses.get(t, {})
        if "error" in data:
            tbl.add_row(t, "[dim]ERR[/dim]", "—", Text("ERR","dim red"), data.get("error","")[:40])
            continue
        price = data.get("current_price")
        atr = (data.get("atr") or {}).get("atr_value")
        action, reason = _technical_action(price, data, h)
        tbl.add_row(t, fmt_price(price), f"±{atr:.2f}" if atr else "N/A",
                    raction(action), f"[dim]{reason}[/dim]")
    CONSOLE.print(tbl)
    CONSOLE.print()

    # Expected daily ranges table
    tbl2 = Table(title="[bold]Expected Daily Ranges[/bold]  [dim](ATR ×1)[/dim]",
                 box=box.SIMPLE_HEAD, header_style="bold white", border_style="bright_black")
    tbl2.add_column("Ticker", style="bold white")
    tbl2.add_column("Price",  justify="right")
    tbl2.add_column("Low −ATR", justify="right", style="red")
    tbl2.add_column("High +ATR", justify="right", style="green")
    tbl2.add_column("Key Support", justify="right", style="cyan")

    for h in holdings:
        t = h["ticker"]; data = analyses.get(t, {})
        if "error" in data: continue
        price = data.get("current_price")
        atr_d = data.get("atr") or {}
        sl = data.get("suggested_levels") or {}
        supports = sl.get("strong_support") or []
        tbl2.add_row(t, fmt_price(price),
                     fmt_price(atr_d.get("daily_target_down")),
                     fmt_price(atr_d.get("daily_target_up")),
                     fmt_price(supports[0]) if supports else "N/A")
    CONSOLE.print(tbl2)
    CONSOLE.print()

    # Watchlist entry opportunities
    CONSOLE.rule("[dim]Watchlist — Daily Entry Opportunities[/dim]")
    CONSOLE.print()
    watch_hits = []
    for t in watchlist:
        data = analyses.get(t, {})
        if "error" in data: continue
        price = data.get("current_price")
        action, reason = _technical_action(price, data)
        score, signal, _ = score_stock(data)
        if action in ("ADD", "WATCH") and score and score >= 55:
            sl = data.get("suggested_levels") or {}
            supports = sl.get("strong_support") or []
            stop = sl.get("stop_loss_long")
            f = data.get("fundamentals") or {}
            analyst = f.get("analyst_estimates") or {}
            watch_hits.append((score, t, price, action, reason, supports, stop, signal,
                               analyst.get("mean_target"), analyst.get("upside_to_mean_target_pct")))
    watch_hits.sort(reverse=True)

    if not watch_hits:
        CONSOLE.print("[dim]  No watchlist stocks at a daily entry zone today.[/dim]\n")
    else:
        cards = []
        for score, t, price, action, reason, supports, stop, signal, target, upside in watch_hits[:5]:
            entry = f"{fmt_price(supports[-1])}–{fmt_price(supports[0])}" if len(supports) >= 2 else fmt_price(price)
            up_style = "green" if upside and upside > 0 else "red"
            sig_style = SIGNAL_STYLE.get(signal, "white").split()[-1]
            body = (f"[white]{fmt_price(price)}[/white]\n"
                    f"Entry: [cyan]{entry}[/cyan]\n"
                    f"Stop:  [red]{fmt_price(stop)}[/red]\n"
                    f"Target: [cyan]{fmt_price(target)}[/cyan]  [{up_style}]{fmt_pct(upside)}[/{up_style}]\n"
                    f"[dim]{reason}[/dim]")
            cards.append(Panel(body, title=f"[bold]{t}[/bold]",
                               subtitle=f"[{sig_style}]{raction(action).plain}[/{sig_style}] · [dim]{score}/100[/dim]",
                               width=32, border_style=sig_style))
        CONSOLE.print(Columns(cards, equal=False))
        CONSOLE.print()

    CONSOLE.print("[dim]* Not financial advice. Levels based on ATR, pivot points, Fibonacci.[/dim]\n")


def generate_weekly_recommendations(portfolio, analyses):
    """Weekly report: trend health, rebalancing, strategic new entries, hold/cut decisions."""
    today = datetime.today().strftime("%Y-%m-%d")
    holdings = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
    watchlist = portfolio.get("watchlist", [])

    # ── Compute totals ────────────────────────────────────────────────────────
    total_cost = total_value = 0
    holding_values = {}
    for h in holdings:
        t = h["ticker"]; data = analyses.get(t, {})
        price = data.get("current_price") if "error" not in data else None
        pnl = calc_pnl(h, price) if price else None
        if pnl:
            total_cost += pnl["cost_basis"]; total_value += pnl["market_value"]
            holding_values[t] = pnl["market_value"]

    if not RICH:
        lines = [f"\n{'═'*80}", f"  WEEKLY RECOMMENDATION REPORT — {today}", f"{'═'*80}"]
        if total_cost > 0:
            g = total_value - total_cost
            lines += [f"  Value: {fmt_large(total_value)}  Cost: {fmt_large(total_cost)}  P&L: {fmt_large(g)} ({fmt_pct(g/total_cost*100)})"]
            for t, val in sorted(holding_values.items(), key=lambda x: -x[1]):
                alloc = val/total_value*100
                lines.append(f"    {t:<6} {alloc:5.1f}%  {'█'*int(alloc/5)}")
        print("\n".join(lines))
        return

    # ── Rich version ──────────────────────────────────────────────────────────
    CONSOLE.print()
    CONSOLE.rule(f"[bold cyan]Weekly Recommendation Report[/bold cyan]  [dim]{today}[/dim]")
    CONSOLE.print()

    # Portfolio health panel
    if total_cost > 0:
        total_gain = total_value - total_cost
        total_pct  = (total_gain / total_cost) * 100
        pnl_style  = "green" if total_gain >= 0 else "red"
        health_text  = f"[white]Total Value[/white]   [bold]{fmt_large(total_value)}[/bold]\n"
        health_text += f"[white]Total Cost[/white]    {fmt_large(total_cost)}\n"
        health_text += f"[white]Unrealized P&L[/white]  [{pnl_style}]{fmt_large(total_gain)}  {fmt_pct(total_pct)}[/{pnl_style}]\n\n"
        health_text += "[bold]Allocation[/bold]\n"
        for t, val in sorted(holding_values.items(), key=lambda x: -x[1]):
            alloc = (val / total_value) * 100
            filled = int(alloc / 3)
            bar_style = "bright_red" if alloc > 40 else ("yellow" if alloc > 25 else "cyan")
            bar = f"[{bar_style}]{'█' * filled}[/{bar_style}][dim]{'░' * (20 - filled)}[/dim]"
            flag = "  [bright_red]← concentrated[/bright_red]" if alloc > 40 else ""
            health_text += f"  [bold]{t:<6}[/bold]  {alloc:5.1f}%  {bar}{flag}\n"
        CONSOLE.print(Panel(health_text.strip(), title="[bold]Portfolio Health[/bold]", border_style="cyan"))
        CONSOLE.print()

    # Holdings weekly action table
    tbl = Table(title="[bold]Holdings — Weekly Action[/bold]", box=box.ROUNDED,
                header_style="bold white", border_style="bright_black", show_lines=True)
    tbl.add_column("Ticker",   style="bold white")
    tbl.add_column("Verdict",  justify="center")
    tbl.add_column("P&L",      justify="right")
    tbl.add_column("Trend",    justify="center")
    tbl.add_column("Wkly Range")
    tbl.add_column("Target",   justify="right")
    tbl.add_column("Stop",     justify="right", style="red")

    for h in holdings:
        t = h["ticker"]; data = analyses.get(t, {})
        if "error" in data:
            tbl.add_row(t, Text("ERR","dim red"), *["[dim]—[/dim]"]*5); continue
        price = data.get("current_price")
        pnl = calc_pnl(h, price)
        score, signal, reasons = score_stock(data)
        ma = data.get("moving_averages") or {}
        atr_d = data.get("atr") or {}
        f = data.get("fundamentals") or {}
        analyst = f.get("analyst_estimates") or {}
        health_f = f.get("financial_health") or {}
        pp = data.get("pivot_points") or {}
        sl = data.get("suggested_levels") or {}

        ema200 = ma.get("ema_200"); pivot = pp.get("pivot")
        upside = analyst.get("upside_to_mean_target_pct"); target = analyst.get("mean_target")
        stop_long = sl.get("stop_loss_long")
        weekly_lo = atr_d.get("weekly_target_down"); weekly_hi = atr_d.get("weekly_target_up")

        if score and score >= 70 and upside and upside > 30:  verdict = "HOLD / ADD ON DIPS"
        elif score and score >= 55:                            verdict = "HOLD"
        elif score and score < 40:                             verdict = "CONSIDER CUTTING"
        else:                                                  verdict = "HOLD WITH CAUTION"

        in_uptrend = price and ema200 and price > ema200
        pivot_bull = price and pivot and price > pivot
        trend_t = Text("↑ Uptrend" if in_uptrend else "↓ Downtrend",
                       style="green" if in_uptrend else "red")
        pivot_t = Text(" Bull" if pivot_bull else " Bear",
                       style="green" if pivot_bull else "red")
        trend_cell = Text.assemble(trend_t, "[dim] /[/dim]", pivot_t)

        pnl_t = rpnl(pnl["gain"], f"{fmt_large(pnl['gain'])} ({fmt_pct(pnl['gain_pct'])})") if pnl else Text("N/A","dim")
        wkly = f"{fmt_price(weekly_lo)} – {fmt_price(weekly_hi)}"
        up_style = "green" if upside and upside > 0 else "red"
        target_t = Text(f"{fmt_price(target)} ({fmt_pct(upside)})", style=up_style)

        verdict_style = SIGNAL_STYLE.get(verdict, "yellow")
        tbl.add_row(t, Text(verdict, style=verdict_style), pnl_t,
                    trend_cell, f"[dim]{wkly}[/dim]", target_t, fmt_price(stop_long))

    CONSOLE.print(tbl)
    CONSOLE.print()

    # Watchlist new entries
    CONSOLE.rule("[bold cyan]Watchlist — Best New Entries This Week[/bold cyan]")
    CONSOLE.print()

    scored_watch = []
    for t in watchlist:
        data = analyses.get(t, {})
        if "error" in data: continue
        score, signal, reasons = score_stock(data)
        price = data.get("current_price")
        ma = data.get("moving_averages") or {}
        f = data.get("fundamentals") or {}
        analyst = f.get("analyst_estimates") or {}
        atr_d = data.get("atr") or {}
        sl = data.get("suggested_levels") or {}
        pp = data.get("pivot_points") or {}
        health_f = f.get("financial_health") or {}
        growth = f.get("growth") or {}
        val = f.get("valuation") or {}
        ema200 = ma.get("ema_200"); pivot = pp.get("pivot")
        in_uptrend = price and ema200 and price > ema200
        pivot_bull = price and pivot and price > pivot
        scored_watch.append((score or 0, t, price, signal, reasons,
                             sl.get("strong_support") or [], sl.get("stop_loss_long"),
                             analyst.get("mean_target"), analyst.get("upside_to_mean_target_pct"),
                             in_uptrend, pivot_bull, atr_d.get("weekly_target_up"),
                             health_f.get("free_cash_flow"), growth.get("revenue_growth_yoy_pct"),
                             val.get("forward_pe")))

    scored_watch.sort(reverse=True)
    top_watch = [x for x in scored_watch if x[0] >= 55][:5]

    if not top_watch:
        CONSOLE.print("[dim]No strong weekly entries from watchlist.[/dim]\n")
    else:
        cards = []
        for rank, (score, t, price, signal, reasons, supports, stop, target, upside,
                   in_uptrend, pivot_bull, weekly_up, fcf, rev_growth, fwd_pe) in enumerate(top_watch, 1):
            trend = "[green]↑ Uptrend[/green]" if in_uptrend else "[red]↓ Downtrend[/red]"
            pb    = "[green]Bull[/green]" if pivot_bull else "[red]Bear[/red]"
            entry = f"{fmt_price(supports[-1])}–{fmt_price(supports[0])}" if len(supports) >= 2 else fmt_price(price)
            up_style = "green" if upside and upside > 0 else "red"
            sig_style = SIGNAL_STYLE.get(signal, "white").split()[-1]
            body = (f"{trend} · {pb}\n"
                    f"[white]{fmt_price(price)}[/white] → [cyan]{fmt_price(weekly_up)}[/cyan] weekly\n"
                    f"Entry: [cyan]{entry}[/cyan]  Stop: [red]{fmt_price(stop)}[/red]\n"
                    f"Target: [cyan]{fmt_price(target)}[/cyan]  [{up_style}]{fmt_pct(upside)}[/{up_style}]\n"
                    f"[dim]Fwd P/E: {f'{fwd_pe:.1f}x' if fwd_pe else 'N/A'}  FCF: {fmt_large(fcf)}[/dim]\n")
            for r in reasons:
                body += f"[dim]• {r}[/dim]\n"
            cards.append(Panel(body.strip(), title=f"[bold]#{rank} {t}[/bold]",
                               subtitle=f"[{sig_style}]{signal}[/{sig_style}] · [dim]{score}/100[/dim]",
                               width=36, border_style=sig_style))
        CONSOLE.print(Columns(cards, equal=False))
        CONSOLE.print()

    CONSOLE.print("[dim]* Not financial advice. Levels based on ATR, pivot points, Fibonacci.[/dim]\n")


# ─── Portfolio mutations ──────────────────────────────────────────────────────

def cmd_add(portfolio, ticker, shares, avg_cost, date=None):
    ticker = ticker.upper()
    date = date or datetime.today().strftime("%Y-%m-%d")
    for h in portfolio["holdings"]:
        if h["ticker"] == ticker:
            print(f"Updating existing holding: {ticker}")
            h["shares"] = shares
            h["avg_cost"] = avg_cost
            h["date_added"] = date
            save_portfolio(portfolio)
            return
    portfolio["holdings"].append({
        "ticker": ticker,
        "shares": shares,
        "avg_cost": avg_cost,
        "date_added": date,
        "notes": ""
    })
    save_portfolio(portfolio)
    print(f"Added {shares} shares of {ticker} @ ${avg_cost:.2f}")


def cmd_remove(portfolio, ticker):
    ticker = ticker.upper()
    before = len(portfolio["holdings"])
    portfolio["holdings"] = [h for h in portfolio["holdings"] if h["ticker"] != ticker]
    if len(portfolio["holdings"]) < before:
        save_portfolio(portfolio)
        print(f"Removed {ticker} from holdings.")
    else:
        print(f"{ticker} not found in holdings.")


def cmd_watch(portfolio, ticker):
    ticker = ticker.upper()
    if ticker not in portfolio["watchlist"]:
        portfolio["watchlist"].append(ticker)
        save_portfolio(portfolio)
        print(f"Added {ticker} to watchlist.")
    else:
        print(f"{ticker} already on watchlist.")


def cmd_unwatch(portfolio, ticker):
    ticker = ticker.upper()
    if ticker in portfolio["watchlist"]:
        portfolio["watchlist"].remove(ticker)
        save_portfolio(portfolio)
        print(f"Removed {ticker} from watchlist.")
    else:
        print(f"{ticker} not on watchlist.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Portfolio Manager")
    parser.add_argument("--holdings", action="store_true", help="Show holdings dashboard only")
    parser.add_argument("--watchlist", action="store_true", help="Show watchlist only")
    parser.add_argument("--add", nargs="+", metavar=("TICKER", "SHARES"),
                        help="Add/update holding: --add TICKER SHARES AVG_COST [DATE]")
    parser.add_argument("--remove", metavar="TICKER", help="Remove holding")
    parser.add_argument("--watch", metavar="TICKER", help="Add to watchlist")
    parser.add_argument("--unwatch", metavar="TICKER", help="Remove from watchlist")
    parser.add_argument("--recommend", action="store_true", help="Generate recommendation report")
    parser.add_argument("--daily", action="store_true", help="Daily recommendation (use with --recommend)")
    parser.add_argument("--weekly", action="store_true", help="Weekly recommendation (use with --recommend)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    portfolio = load_portfolio()

    # Mutations
    if args.add:
        if len(args.add) < 3:
            print("Usage: --add TICKER SHARES AVG_COST [DATE]")
            sys.exit(1)
        cmd_add(portfolio, args.add[0], float(args.add[1]), float(args.add[2]),
                args.add[3] if len(args.add) > 3 else None)
        return

    if args.remove:
        cmd_remove(portfolio, args.remove)
        return

    if args.watch:
        cmd_watch(portfolio, args.watch)
        return

    if args.unwatch:
        cmd_unwatch(portfolio, args.unwatch)
        return

    # Recommendation mode — needs all tickers
    if args.recommend or args.daily or args.weekly:
        tickers_needed = set()
        for h in portfolio["holdings"]:
            if h.get("shares", 0) > 0:
                tickers_needed.add(h["ticker"])
        tickers_needed.update(portfolio.get("watchlist", []))
        print(f"Fetching data for {len(tickers_needed)} ticker(s)...", file=sys.stderr)
        analyses = {}
        for t in sorted(tickers_needed):
            print(f"  {t}...", file=sys.stderr, end=" ", flush=True)
            analyses[t] = run_analysis(t)
            status = "ok" if "error" not in analyses[t] else f"ERR: {analyses[t]['error'][:40]}"
            print(status, file=sys.stderr)
        # Default: show both daily and weekly unless one is specified
        if args.weekly and not args.daily:
            generate_weekly_recommendations(portfolio, analyses)
        elif args.daily and not args.weekly:
            generate_daily_recommendations(portfolio, analyses)
        else:
            generate_daily_recommendations(portfolio, analyses)
            generate_weekly_recommendations(portfolio, analyses)
        return

    # Determine tickers to fetch
    show_holdings = args.holdings or not args.watchlist
    show_watchlist = args.watchlist or not args.holdings

    tickers_needed = set()
    if show_holdings:
        for h in portfolio["holdings"]:
            if h.get("shares", 0) > 0:
                tickers_needed.add(h["ticker"])
    if show_watchlist:
        tickers_needed.update(portfolio.get("watchlist", []))

    # Fetch all analyses (sequentially — yfinance rate limits parallel calls)
    print(f"Fetching data for {len(tickers_needed)} ticker(s)...", file=sys.stderr)
    analyses = {}
    for t in sorted(tickers_needed):
        print(f"  {t}...", file=sys.stderr, end=" ", flush=True)
        analyses[t] = run_analysis(t)
        status = "ok" if "error" not in analyses[t] else f"ERR: {analyses[t]['error'][:40]}"
        print(status, file=sys.stderr)

    if args.json:
        print(json.dumps({
            "portfolio": portfolio,
            "analyses": analyses,
            "as_of": datetime.today().strftime("%Y-%m-%d")
        }, indent=2, default=str))
        return

    if show_holdings:
        print_holdings_dashboard(portfolio, analyses)
    if show_watchlist:
        print_watchlist_dashboard(portfolio, analyses)

    print(f"\n{'─'*80}")
    print("  Commands:")
    print("    Add holding : python scripts/portfolio.py --add TICKER SHARES AVG_COST")
    print("    Remove      : python scripts/portfolio.py --remove TICKER")
    print("    Watch       : python scripts/portfolio.py --watch TICKER")
    print("    Unwatch     : python scripts/portfolio.py --unwatch TICKER")
    print("    Daily report: python scripts/portfolio.py --recommend --daily")
    print("    Weekly rpt  : python scripts/portfolio.py --recommend --weekly")
    print(f"{'─'*80}\n")


if __name__ == "__main__":
    main()
