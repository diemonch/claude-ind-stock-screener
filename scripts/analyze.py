#!/usr/bin/env python3
"""
Stock technical analysis script for the stock-analyst skill.
Usage: python scripts/analyze.py <TICKER>
Output: Formatted tables to stdout (or JSON with --json flag).
"""

import sys
import json
import warnings
import math
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python scripts/analyze.py <TICKER>"}))
        sys.exit(1)

    use_json = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ticker = args[0].upper().strip()

    try:
        import yfinance as yf
        import numpy as np
        import pandas as pd
    except ImportError as e:
        print(json.dumps({
            "error": f"Missing dependency: {e}. Install with: pip install yfinance pandas numpy",
            "ticker": ticker
        }))
        sys.exit(1)

    try:
        result = analyze(ticker, yf, np, pd)
        if use_json:
            print(json.dumps(result, indent=2, default=str))
        else:
            try:
                print_tables(result)
            except ImportError:
                # rich not installed — fall back to plain text tables
                print_plain(result)
    except ValueError as e:
        print(json.dumps({"error": str(e), "ticker": ticker}))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"error": f"Failed to fetch data: {e}", "ticker": ticker}))
        sys.exit(1)


def print_tables(result):
    from rich.console import Console
    from rich.table import Table
    from rich import box
    from rich.text import Text

    console = Console()
    cp = result["current_price"]
    cur = result["currency"]

    console.print(f"\n[bold cyan]{result['ticker']}[/bold cyan] — {result['company_name']}", highlight=False)
    console.print(f"As of [yellow]{result['as_of']}[/yellow] | Current Price: [bold green]{cp} {cur}[/bold green]\n")

    # ── Key Price Levels ────────────────────────────────────────────────────
    levels = []
    pp = result["pivot_points"]
    ma = result["moving_averages"]
    atr = result["atr"]
    fib_d = result["fibonacci"]["daily"]["retracements"]
    fib_w = result["fibonacci"]["weekly"]["retracements"]
    fib_de = result["fibonacci"]["daily"]["extensions"]
    fib_we = result["fibonacci"]["weekly"]["extensions"]

    def add(price, label, basis):
        if price is not None:
            levels.append((float(price), label, basis))

    add(pp["r2"],   "R2 Pivot",          "Classic Pivot")
    add(pp["r1"],   "R1 Pivot",          "Classic Pivot")
    add(pp["pivot"],"Pivot",             "Classic Pivot")
    add(pp["s1"],   "S1 Pivot",          "Classic Pivot")
    add(pp["s2"],   "S2 Pivot",          "Classic Pivot")
    add(atr["daily_target_up"],   "ATR Daily Up",   "ATR ×1")
    add(atr["daily_target_down"], "ATR Daily Down", "ATR ×1")
    add(atr["weekly_target_up"],  "ATR Weekly Up",  "ATR ×2")
    add(atr["weekly_target_down"],"ATR Weekly Down","ATR ×2")
    add(ma["ema_20"],  "EMA 20",  "Moving Average")
    add(ma["ema_50"],  "EMA 50",  "Moving Average")
    add(ma["ema_200"], "EMA 200", "Moving Average")
    add(ma["sma_50"],  "SMA 50",  "Moving Average")
    add(ma["sma_200"], "SMA 200", "Moving Average")
    for k, v in fib_d.items():
        add(v, f"Fib {k} (daily)", "Fibonacci Daily")
    for k, v in fib_w.items():
        add(v, f"Fib {k} (weekly)", "Fibonacci Weekly")
    for k, v in fib_de.items():
        add(v, f"Fib Ext {k} (daily)", "Fib Extension Daily")
    for k, v in fib_we.items():
        add(v, f"Fib Ext {k} (weekly)", "Fib Extension Weekly")

    # filter to ±15% of current price
    levels = [(p, l, b) for p, l, b in levels if abs(p - cp) / cp <= 0.15]
    levels.sort(key=lambda x: x[0], reverse=True)

    t = Table(title="Key Price Levels", box=box.SIMPLE_HEAD, show_lines=False)
    t.add_column("Level", style="white")
    t.add_column("Price", justify="right", style="bold")
    t.add_column("Type", style="dim")
    t.add_column("Basis", style="dim")

    for price, label, basis in levels:
        if price > cp:
            ptype = "[red]Resistance[/red]"
            price_str = f"[red]{price:.2f}[/red]"
        elif price < cp:
            ptype = "[green]Support[/green]"
            price_str = f"[green]{price:.2f}[/green]"
        else:
            ptype = "Current"
            price_str = f"[bold yellow]{price:.2f}[/bold yellow]"
        t.add_row(label, price_str, ptype, basis)

    # insert current price marker
    console.print(t)

    # ── Daily & Weekly Targets ───────────────────────────────────────────────
    t2 = Table(title="Daily & Weekly Targets", box=box.SIMPLE_HEAD)
    t2.add_column("Scenario", style="white")
    t2.add_column("Daily Target", justify="right")
    t2.add_column("Weekly Target", justify="right")
    t2.add_column("Basis", style="dim")

    t2.add_row("Upside (base)",
               f"[red]{atr['daily_target_up']:.2f}[/red]",
               f"[red]{atr['weekly_target_up']:.2f}[/red]",
               "ATR ×1 / ×2 from close")
    t2.add_row("Upside (extended)",
               f"[red]{fib_de.get('1.272', 0):.2f}[/red]",
               f"[red]{fib_we.get('1.618', 0):.2f}[/red]",
               "Fibonacci Extension")
    t2.add_row("Downside (base)",
               f"[green]{atr['daily_target_down']:.2f}[/green]",
               f"[green]{atr['weekly_target_down']:.2f}[/green]",
               "ATR ×1 / ×2 from close")
    t2.add_row("Downside (extended)",
               f"[green]{float(fib_d.get('0.618', 0)):.2f}[/green]",
               f"[green]{float(fib_w.get('0.618', 0)):.2f}[/green]",
               "Fibonacci Retracement")
    console.print(t2)

    # ── Entry & Stop-Loss ───────────────────────────────────────────────────
    sl = result["suggested_levels"]
    sup = sl["strong_support"]
    res = sl["strong_resistance"]

    entry_long  = f"{sup[0]:.2f}–{sup[1]:.2f}"  if len(sup) >= 2 else (f"{sup[0]:.2f}" if sup else "—")
    entry_short = f"{res[0]:.2f}–{res[1]:.2f}"  if len(res) >= 2 else (f"{res[0]:.2f}" if res else "—")

    t3 = Table(title="Entry & Stop-Loss Reference", box=box.SIMPLE_HEAD)
    t3.add_column("Direction", style="white")
    t3.add_column("Entry Zone", justify="right")
    t3.add_column("Stop-Loss", justify="right", style="bold")
    t3.add_column("Target", justify="right")

    t3.add_row("Long",
               f"[green]{entry_long}[/green]",
               f"[red]{sl['stop_loss_long']:.2f}[/red]",
               f"[red]{pp['r1']:.2f}[/red] / {atr['daily_target_up']:.2f}")
    t3.add_row("Short",
               f"[red]{entry_short}[/red]",
               f"[green]{sl['stop_loss_short']:.2f}[/green]",
               f"[green]{pp['s1']:.2f}[/green] / {atr['daily_target_down']:.2f}")
    console.print(t3)

    # ── Indicator Summary ───────────────────────────────────────────────────
    h52 = result["fibonacci"]["weekly"]["high_52w"]
    l52 = result["fibonacci"]["weekly"]["low_52w"]
    pct_from_high = (cp - h52) / h52 * 100
    pct_from_low  = (cp - l52) / l52 * 100

    t4 = Table(title="Indicator Summary", box=box.SIMPLE_HEAD)
    t4.add_column("Indicator", style="white")
    t4.add_column("Value", justify="right", style="bold")
    t4.add_column("Signal", style="dim")

    def ma_signal(val, label):
        if val is None:
            return label, "—", "N/A"
        sig = "Resistance" if val > cp else "Support"
        color = "red" if val > cp else "green"
        return label, f"[{color}]{val:.2f}[/{color}]", sig

    t4.add_row("ATR (14)", f"{atr['atr_value']:.2f}", f"Expected daily move ±{atr['atr_value']:.2f}")
    _, v, s = ma_signal(ma["ema_20"],  "EMA 20");  t4.add_row("EMA 20",  v, s)
    _, v, s = ma_signal(ma["ema_50"],  "EMA 50");  t4.add_row("EMA 50",  v, s)
    _, v, s = ma_signal(ma["ema_200"], "EMA 200"); t4.add_row("EMA 200", v, s)
    _, v, s = ma_signal(ma["sma_200"], "SMA 200"); t4.add_row("SMA 200", v, s)

    pivot_sig = "Above price — bullish weekly bias" if pp["pivot"] < cp else "Below price — bearish weekly bias"
    t4.add_row("Pivot Point", f"{pp['pivot']:.2f}", pivot_sig)
    t4.add_row("52-week High", f"{h52:.2f}", f"{pct_from_high:+.1f}% from current price")
    t4.add_row("52-week Low",  f"{l52:.2f}", f"{pct_from_low:+.1f}% from current price")
    console.print(t4)

    # ── Fundamental Snapshot ─────────────────────────────────────────────────
    fund = result.get("fundamentals")
    if fund:
        def fmt_large(n):
            if n is None: return "—"
            if n >= 1e12: return f"${n/1e12:.2f}T"
            if n >= 1e9:  return f"${n/1e9:.2f}B"
            if n >= 1e6:  return f"${n/1e6:.2f}M"
            return f"${n:,.0f}"

        def fmt_pct(n):
            return f"{n:.1f}%" if n is not None else "—"

        def fmt_x(n):
            return f"{n:.2f}x" if n is not None else "—"

        v  = fund.get("valuation", {})
        g  = fund.get("growth", {})
        p  = fund.get("profitability", {})
        fh = fund.get("financial_health", {})
        ae = fund.get("analyst_estimates", {})
        rk = fund.get("risk", {})

        t5 = Table(title="Fundamental Snapshot", box=box.SIMPLE_HEAD)
        t5.add_column("Metric",  style="white")
        t5.add_column("Value",   justify="right", style="bold")
        t5.add_column("Context", style="dim")

        if fund.get("market_cap"):
            t5.add_row("Market Cap", fmt_large(fund["market_cap"]),
                       f"{fund.get('sector') or ''} · {fund.get('industry') or ''}".strip(" ·"))
        if fh.get("total_revenue"):
            rev_ctx = f"{fmt_pct(g.get('revenue_growth_yoy_pct'))} YoY growth" if g.get("revenue_growth_yoy_pct") else ""
            t5.add_row("Revenue (TTM)", fmt_large(fh["total_revenue"]), rev_ctx)
        if fh.get("free_cash_flow") is not None:
            t5.add_row("Free Cash Flow", fmt_large(fh["free_cash_flow"]), "")
        if p.get("gross_margin_pct"):
            t5.add_row("Gross Margin",
                       fmt_pct(p["gross_margin_pct"]),
                       f"Op margin: {fmt_pct(p.get('operating_margin_pct'))}")
        if p.get("net_margin_pct") is not None:
            t5.add_row("Net Margin", fmt_pct(p["net_margin_pct"]),
                       f"ROE: {fmt_pct(p.get('roe_pct'))}")
        if v.get("trailing_pe") is not None:
            t5.add_row("Trailing P/E", f"{v['trailing_pe']:.1f}x",
                       f"Forward P/E: {v['forward_pe']:.1f}x" if v.get("forward_pe") else "")
        if v.get("peg_ratio") is not None:
            peg_ctx = "undervalued for growth" if v["peg_ratio"] < 1 else ("fair" if v["peg_ratio"] < 2 else "expensive for growth")
            t5.add_row("PEG Ratio", f"{v['peg_ratio']:.2f}", peg_ctx)
        if v.get("ev_to_revenue") is not None:
            t5.add_row("EV/Revenue", fmt_x(v["ev_to_revenue"]),
                       f"EV/EBITDA: {fmt_x(v.get('ev_to_ebitda'))}")
        if fund.get("per_share", {}).get("trailing_eps") is not None:
            ps = fund["per_share"]
            t5.add_row("EPS (trailing)", f"${ps['trailing_eps']:.2f}",
                       f"Forward EPS: ${ps['forward_eps']:.2f}" if ps.get("forward_eps") else "")
        if fund.get("per_share", {}).get("dividend_yield_pct"):
            t5.add_row("Dividend Yield", fmt_pct(fund["per_share"]["dividend_yield_pct"]), "")
        if ae.get("mean_target"):
            upside_str = f"{ae['upside_to_mean_target_pct']:+.1f}% upside" if ae.get("upside_to_mean_target_pct") else ""
            rec = (ae.get("recommendation") or "").upper()
            t5.add_row("Analyst Target",
                       f"${ae['mean_target']:.2f}",
                       f"{rec} · {upside_str} · {ae.get('num_analysts', '?')} analysts")
        if rk.get("beta") is not None:
            beta_ctx = "high volatility" if rk["beta"] > 1.5 else ("moderate" if rk["beta"] > 0.8 else "low volatility")
            t5.add_row("Beta", f"{rk['beta']:.2f}", beta_ctx)

        console.print(t5)

    if result.get("warnings"):
        console.print("\n[yellow]Notes:[/yellow]")
        for w in result["warnings"]:
            console.print(f"  • {w}")

    console.print("\n[dim italic]Technical analysis for informational purposes only. Not financial advice.[/dim italic]\n")


def print_plain(result):
    """Fallback plain-text table output (no rich dependency)."""
    cp = result["current_price"]
    cur = result["currency"]
    pp = result["pivot_points"]
    ma = result["moving_averages"]
    atr = result["atr"]
    fib_d = result["fibonacci"]["daily"]["retracements"]
    fib_w = result["fibonacci"]["weekly"]["retracements"]
    fib_de = result["fibonacci"]["daily"]["extensions"]
    fib_we = result["fibonacci"]["weekly"]["extensions"]
    sl = result["suggested_levels"]

    sep = "-" * 60
    print(f"\n{result['ticker']} — {result['company_name']}")
    print(f"As of {result['as_of']} | Current Price: {cp} {cur}")
    print()

    # Key Price Levels
    levels = []
    def add(price, label, basis):
        if price is not None:
            levels.append((float(price), label, basis))

    add(pp["r2"],    "R2 Pivot",    "Classic Pivot")
    add(pp["r1"],    "R1 Pivot",    "Classic Pivot")
    add(pp["pivot"], "Pivot",       "Classic Pivot")
    add(pp["s1"],    "S1 Pivot",    "Classic Pivot")
    add(pp["s2"],    "S2 Pivot",    "Classic Pivot")
    add(atr["daily_target_up"],    "ATR Daily Up",    "ATR ×1")
    add(atr["daily_target_down"],  "ATR Daily Down",  "ATR ×1")
    add(atr["weekly_target_up"],   "ATR Weekly Up",   "ATR ×2")
    add(atr["weekly_target_down"], "ATR Weekly Down", "ATR ×2")
    add(ma["ema_20"],  "EMA 20",  "Moving Average")
    add(ma["ema_50"],  "EMA 50",  "Moving Average")
    add(ma["ema_200"], "EMA 200", "Moving Average")
    add(ma["sma_50"],  "SMA 50",  "Moving Average")
    add(ma["sma_200"], "SMA 200", "Moving Average")
    for k, v in fib_d.items():
        add(v, f"Fib {k} (daily)", "Fibonacci Daily")
    for k, v in fib_w.items():
        add(v, f"Fib {k} (weekly)", "Fibonacci Weekly")

    levels = [(p, l, b) for p, l, b in levels if abs(p - cp) / cp <= 0.15]
    levels.sort(key=lambda x: x[0], reverse=True)

    print("KEY PRICE LEVELS")
    print(sep)
    print(f"  {'Level':<28} {'Price':>8}  {'Type':<12}  Basis")
    print(sep)
    for price, label, basis in levels:
        ptype = "Resistance" if price > cp else "Support"
        print(f"  {label:<28} {price:>8.2f}  {ptype:<12}  {basis}")
    print()

    # Targets
    print("DAILY & WEEKLY TARGETS")
    print(sep)
    print(f"  {'Scenario':<22} {'Daily':>8}  {'Weekly':>8}  Basis")
    print(sep)
    print(f"  {'Upside (base)':<22} {atr['daily_target_up']:>8.2f}  {atr['weekly_target_up']:>8.2f}  ATR ×1 / ×2")
    print(f"  {'Upside (extended)':<22} {float(fib_de.get('1.272',0)):>8.2f}  {float(fib_we.get('1.618',0)):>8.2f}  Fib Extension")
    print(f"  {'Downside (base)':<22} {atr['daily_target_down']:>8.2f}  {atr['weekly_target_down']:>8.2f}  ATR ×1 / ×2")
    print(f"  {'Downside (extended)':<22} {float(fib_d.get('0.618',0)):>8.2f}  {float(fib_w.get('0.618',0)):>8.2f}  Fib Retracement")
    print()

    # Entry & Stop-Loss
    sup = sl["strong_support"]
    res = sl["strong_resistance"]
    entry_long  = f"{sup[0]:.2f}–{sup[1]:.2f}" if len(sup) >= 2 else (f"{sup[0]:.2f}" if sup else "—")
    entry_short = f"{res[0]:.2f}–{res[1]:.2f}" if len(res) >= 2 else (f"{res[0]:.2f}" if res else "—")
    print("ENTRY & STOP-LOSS REFERENCE")
    print(sep)
    print(f"  {'Direction':<10} {'Entry Zone':>14}  {'Stop-Loss':>10}  Target")
    print(sep)
    print(f"  {'Long':<10} {entry_long:>14}  {sl['stop_loss_long']:>10.2f}  {pp['r1']:.2f} / {atr['daily_target_up']:.2f}")
    print(f"  {'Short':<10} {entry_short:>14}  {sl['stop_loss_short']:>10.2f}  {pp['s1']:.2f} / {atr['daily_target_down']:.2f}")
    print()

    # Indicator Summary
    h52 = result["fibonacci"]["weekly"]["high_52w"]
    l52 = result["fibonacci"]["weekly"]["low_52w"]
    print("INDICATOR SUMMARY")
    print(sep)
    print(f"  {'Indicator':<14} {'Value':>8}  Signal")
    print(sep)
    print(f"  {'ATR (14)':<14} {atr['atr_value']:>8.2f}  Expected daily move ±{atr['atr_value']:.2f}")
    for label, val in [("EMA 20", ma["ema_20"]), ("EMA 50", ma["ema_50"]),
                       ("EMA 200", ma["ema_200"]), ("SMA 200", ma["sma_200"])]:
        if val:
            sig = "Resistance" if val > cp else "Support"
            print(f"  {label:<14} {val:>8.2f}  {sig}")
    pivot_sig = "bullish weekly bias" if pp["pivot"] < cp else "bearish weekly bias"
    print(f"  {'Pivot':<14} {pp['pivot']:>8.2f}  {pivot_sig}")
    pct_high = (cp - h52) / h52 * 100
    pct_low  = (cp - l52) / l52 * 100
    print(f"  {'52w High':<14} {h52:>8.2f}  {pct_high:+.1f}% from current price")
    print(f"  {'52w Low':<14} {l52:>8.2f}  {pct_low:+.1f}% from current price")
    print()

    # Fundamental Snapshot
    fund = result.get("fundamentals")
    if fund:
        def fmtl(n):
            if n is None: return "—"
            if n >= 1e12: return f"${n/1e12:.2f}T"
            if n >= 1e9:  return f"${n/1e9:.2f}B"
            if n >= 1e6:  return f"${n/1e6:.2f}M"
            return f"${n:,.0f}"
        def fpct(n): return f"{n:.1f}%" if n is not None else "—"

        v  = fund.get("valuation", {})
        g  = fund.get("growth", {})
        p  = fund.get("profitability", {})
        fh = fund.get("financial_health", {})
        ae = fund.get("analyst_estimates", {})
        rk = fund.get("risk", {})

        print("FUNDAMENTAL SNAPSHOT")
        print(sep)
        print(f"  {'Metric':<22} {'Value':>12}  Context")
        print(sep)
        if fund.get("market_cap"):
            print(f"  {'Market Cap':<22} {fmtl(fund['market_cap']):>12}  {fund.get('sector') or ''}")
        if fh.get("total_revenue"):
            rev_ctx = f"{fpct(g.get('revenue_growth_yoy_pct'))} YoY" if g.get("revenue_growth_yoy_pct") else ""
            print(f"  {'Revenue (TTM)':<22} {fmtl(fh['total_revenue']):>12}  {rev_ctx}")
        if fh.get("free_cash_flow") is not None:
            print(f"  {'Free Cash Flow':<22} {fmtl(fh['free_cash_flow']):>12}")
        if p.get("gross_margin_pct"):
            print(f"  {'Gross Margin':<22} {fpct(p['gross_margin_pct']):>12}  Op: {fpct(p.get('operating_margin_pct'))}")
        if p.get("net_margin_pct") is not None:
            print(f"  {'Net Margin':<22} {fpct(p['net_margin_pct']):>12}  ROE: {fpct(p.get('roe_pct'))}")
        if v.get("trailing_pe") is not None:
            fwd = f"  Fwd P/E: {v['forward_pe']:.1f}x" if v.get("forward_pe") else ""
            pe_str = f"{v['trailing_pe']:.1f}x"
            print(f"  {'Trailing P/E':<22} {pe_str:>12}{fwd}")
        if v.get("peg_ratio") is not None:
            peg_ctx = "undervalued for growth" if v["peg_ratio"] < 1 else ("fair" if v["peg_ratio"] < 2 else "expensive")
            peg_str = f"{v['peg_ratio']:.2f}"
            print(f"  {'PEG Ratio':<22} {peg_str:>12}  {peg_ctx}")
        if v.get("ev_to_revenue") is not None:
            ev_str = f"{v['ev_to_revenue']:.2f}x"
            ebitda_str = f"{v['ev_to_ebitda']:.2f}x" if v.get("ev_to_ebitda") else "—"
            print(f"  {'EV/Revenue':<22} {ev_str:>12}  EV/EBITDA: {ebitda_str}")
        if ae.get("mean_target"):
            upside_str = f"{ae['upside_to_mean_target_pct']:+.1f}% upside" if ae.get("upside_to_mean_target_pct") else ""
            rec = (ae.get("recommendation") or "").upper()
            tgt_str = f"${ae['mean_target']:.2f}"
            print(f"  {'Analyst Target':<22} {tgt_str:>12}  {rec} · {upside_str} · {ae.get('num_analysts', '?')} analysts")
        if rk.get("beta") is not None:
            beta_str = f"{rk['beta']:.2f}"
            print(f"  {'Beta':<22} {beta_str:>12}")
        print()

    if result.get("warnings"):
        print("NOTES")
        for w in result["warnings"]:
            print(f"  • {w}")
        print()

    print("Technical analysis for informational purposes only. Not financial advice.\n")


def r2(val):
    """Round to 2 decimal places; return None if val is None/NaN/Inf."""
    try:
        if val is None:
            return None
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, 2)
    except Exception:
        return None


def get_fundamentals(info, current_price):
    """Extract fundamental metrics from a yfinance info dict."""
    if not info:
        return None

    def safe(key):
        val = info.get(key)
        if val is None:
            return None
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return None
            return round(f, 2)
        except (TypeError, ValueError):
            return None

    def safe_str(key):
        return info.get(key) or None

    def safe_int(key):
        val = info.get(key)
        if val is None:
            return None
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    def as_pct(key):
        val = safe(key)
        return r2(val * 100) if val is not None else None

    mean_target = safe("targetMeanPrice")
    upside_pct = None
    if mean_target and current_price:
        upside_pct = r2((mean_target - current_price) / current_price * 100)

    return {
        "sector":           safe_str("sector"),
        "industry":         safe_str("industry"),
        "market_cap":       safe_int("marketCap"),
        "enterprise_value": safe_int("enterpriseValue"),
        "valuation": {
            "trailing_pe":   safe("trailingPE"),
            "forward_pe":    safe("forwardPE"),
            "peg_ratio":     safe("pegRatio"),
            "price_to_book": safe("priceToBook"),
            "price_to_sales": safe("priceToSalesTrailingTwelveMonths"),
            "ev_to_ebitda":  safe("enterpriseToEbitda"),
            "ev_to_revenue": safe("enterpriseToRevenue"),
        },
        "growth": {
            "revenue_growth_yoy_pct":          as_pct("revenueGrowth"),
            "earnings_growth_yoy_pct":         as_pct("earningsGrowth"),
            "earnings_quarterly_growth_pct":   as_pct("earningsQuarterlyGrowth"),
        },
        "profitability": {
            "gross_margin_pct":     as_pct("grossMargins"),
            "operating_margin_pct": as_pct("operatingMargins"),
            "net_margin_pct":       as_pct("profitMargins"),
            "roe_pct":              as_pct("returnOnEquity"),
            "roa_pct":              as_pct("returnOnAssets"),
        },
        "financial_health": {
            "total_revenue":  safe_int("totalRevenue"),
            "free_cash_flow": safe_int("freeCashflow"),
            "total_cash":     safe_int("totalCash"),
            "total_debt":     safe_int("totalDebt"),
            "debt_to_equity": safe("debtToEquity"),
            "current_ratio":  safe("currentRatio"),
        },
        "per_share": {
            "trailing_eps":       safe("trailingEps"),
            "forward_eps":        safe("forwardEps"),
            "book_value_per_share": safe("bookValue"),
            "dividend_yield_pct": as_pct("dividendYield"),
            "payout_ratio_pct":   as_pct("payoutRatio"),
        },
        "analyst_estimates": {
            "recommendation":            safe_str("recommendationKey"),
            "mean_target":               mean_target,
            "high_target":               safe("targetHighPrice"),
            "low_target":                safe("targetLowPrice"),
            "num_analysts":              safe_int("numberOfAnalystOpinions"),
            "upside_to_mean_target_pct": upside_pct,
        },
        "risk": {
            "beta":                        safe("beta"),
            "short_ratio":                 safe("shortRatio"),
            "short_percent_of_float_pct":  as_pct("shortPercentOfFloat"),
        },
    }


def analyze(ticker, yf, np, pd):
    script_warnings = []
    data_quality = "full"

    # --- Fetch price history (~13 months for full 52-week + cushion) ---
    stock = yf.Ticker(ticker)
    end_date = datetime.today()
    start_date = end_date - timedelta(days=400)

    hist = stock.history(
        start=start_date.strftime("%Y-%m-%d"),
        end=end_date.strftime("%Y-%m-%d"),
        interval="1d",
        auto_adjust=True
    )

    if hist.empty or len(hist) < 5:
        raise ValueError(
            f"Ticker '{ticker}' not found on Yahoo Finance or has insufficient price history."
        )

    hist = hist.dropna(subset=["Close", "High", "Low"])

    # --- Current price and metadata ---
    try:
        fast = stock.fast_info
        current_price = r2(fast.last_price)
        currency = getattr(fast, "currency", "USD") or "USD"
    except Exception:
        current_price = None
        currency = "USD"

    if current_price is None:
        current_price = r2(hist["Close"].iloc[-1])

    info = {}
    try:
        info = stock.info
        company_name = info.get("longName") or info.get("shortName") or ticker
    except Exception:
        company_name = ticker

    fundamentals = get_fundamentals(info, current_price)

    as_of_date = hist.index[-1].date().isoformat()

    close = hist["Close"]
    high  = hist["High"]
    low   = hist["Low"]

    # --- Pivot Points (Classic, from last completed week's OHLC) ---
    weekly = hist.resample("W-FRI").agg({
        "High": "max", "Low": "min", "Close": "last"
    }).dropna()

    if len(weekly) >= 2:
        lw = weekly.iloc[-2]
    elif len(weekly) == 1:
        lw = weekly.iloc[-1]
        script_warnings.append("Only one week of data — pivot points use the most recent week.")
    else:
        raise ValueError("Insufficient weekly data for pivot point calculation.")

    wH, wL, wC = float(lw["High"]), float(lw["Low"]), float(lw["Close"])
    P  = (wH + wL + wC) / 3.0
    R1 = 2 * P - wL
    R2 = P + (wH - wL)
    S1 = 2 * P - wH
    S2 = P - (wH - wL)

    pivot_points = {
        "period": "last_week_ohlc",
        "weekly_high":  r2(wH),
        "weekly_low":   r2(wL),
        "weekly_close": r2(wC),
        "pivot": r2(P),
        "r1": r2(R1), "r2": r2(R2),
        "s1": r2(S1), "s2": r2(S2),
    }

    # --- ATR (14-period, Wilder's smoothing) ---
    tr = pd.DataFrame({
        "hl":  high - low,
        "hpc": (high - close.shift(1)).abs(),
        "lpc": (low  - close.shift(1)).abs(),
    }).max(axis=1)

    atr_series = tr.ewm(span=14, adjust=False).mean()
    atr_val = r2(float(atr_series.iloc[-1]))

    atr_section = {
        "period": 14,
        "atr_value":          atr_val,
        "daily_target_up":    r2(current_price + atr_val)     if atr_val else None,
        "daily_target_down":  r2(current_price - atr_val)     if atr_val else None,
        "weekly_target_up":   r2(current_price + 2 * atr_val) if atr_val else None,
        "weekly_target_down": r2(current_price - 2 * atr_val) if atr_val else None,
    }

    # --- Moving Averages ---
    def ema(n):
        if len(close) >= n:
            return r2(float(close.ewm(span=n, adjust=False).mean().iloc[-1]))
        return None

    def sma(n):
        if len(close) >= n:
            return r2(float(close.rolling(window=n).mean().iloc[-1]))
        return None

    ma = {
        "ema_20":  ema(20),
        "ema_50":  ema(50),
        "ema_200": ema(200),
        "sma_50":  sma(50),
        "sma_200": sma(200),
    }

    if ma["ema_200"] is None or ma["sma_200"] is None:
        script_warnings.append(
            f"Fewer than 200 trading days of history — EMA/SMA 200 not computed."
        )
        data_quality = "partial"

    # --- Fibonacci Levels ---
    def fib_retracements(h, lo):
        rng = h - lo
        return {str(lvl): r2(h - lvl * rng) for lvl in [0.236, 0.382, 0.5, 0.618, 0.786]}

    def fib_extensions(h, lo):
        rng = h - lo
        return {str(lvl): r2(lo + lvl * rng) for lvl in [1.272, 1.618]}

    # Daily: 20-day swing
    recent = hist.tail(20)
    if len(recent) < 10:
        script_warnings.append(
            "Fewer than 10 days of recent history — 20-day Fibonacci uses available range."
        )
        data_quality = "partial"

    sh20 = r2(float(recent["High"].max()))
    sl20 = r2(float(recent["Low"].min()))

    fib_daily = {
        "basis":        "20-day swing",
        "swing_high":   sh20,
        "swing_low":    sl20,
        "retracements": fib_retracements(sh20, sl20),
        "extensions":   fib_extensions(sh20, sl20),
    }

    # Weekly: 52-week range
    hist_52w = hist.tail(252)
    if len(hist_52w) < 50:
        script_warnings.append(
            f"Only {len(hist_52w)} trading days available — using full available range for 52-week Fibonacci."
        )
        data_quality = "partial"

    h52 = r2(float(hist_52w["High"].max()))
    l52 = r2(float(hist_52w["Low"].min()))

    fib_weekly = {
        "basis":        "52-week range",
        "high_52w":     h52,
        "low_52w":      l52,
        "retracements": fib_retracements(h52, l52),
        "extensions":   fib_extensions(h52, l52),
    }

    fibonacci = {"daily": fib_daily, "weekly": fib_weekly}

    # --- Suggested Levels (top supports/resistances near current price) ---
    candidates = [
        (R1, "R1 Pivot"), (R2, "R2 Pivot"), (P, "Pivot"),
        (S1, "S1 Pivot"), (S2, "S2 Pivot"),
        (ma["ema_20"], "EMA 20"), (ma["ema_50"], "EMA 50"),
        (ma["ema_200"], "EMA 200"), (ma["sma_50"], "SMA 50"),
        (ma["sma_200"], "SMA 200"),
    ]

    supports    = sorted([v for v, _ in candidates if v and v < current_price], reverse=True)
    resistances = sorted([v for v, _ in candidates if v and v > current_price])

    # Stop-loss: below S2 for longs (with a small buffer = 10% of weekly range)
    weekly_range = wH - wL
    buffer = 0.10 * weekly_range if weekly_range > 0 else current_price * 0.01
    stop_long  = r2(S2 - buffer)
    stop_short = r2(R2 + buffer)

    suggested_levels = {
        "strong_support":    [r2(v) for v in supports[:3]],
        "strong_resistance": [r2(v) for v in resistances[:3]],
        "stop_loss_long":    stop_long,
        "stop_loss_short":   stop_short,
    }

    return {
        "ticker":          ticker,
        "company_name":    company_name,
        "as_of":           as_of_date,
        "current_price":   current_price,
        "currency":        currency,
        "data_quality":    data_quality,
        "pivot_points":    pivot_points,
        "fibonacci":       fibonacci,
        "atr":             atr_section,
        "moving_averages": ma,
        "suggested_levels": suggested_levels,
        "fundamentals":    fundamentals,
        "warnings":        script_warnings,
    }


if __name__ == "__main__":
    main()
