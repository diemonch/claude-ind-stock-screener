#!/usr/bin/env python3
"""
Agent 3 — Trade Monitor
Polls active trade positions at a configurable interval and fires alerts
when price hits targets or stop-loss levels.

Usage:
  python scripts/agents/trade_monitor.py --check              # one-shot check
  python scripts/agents/trade_monitor.py --interval 4         # poll every 4 hours
  python scripts/agents/trade_monitor.py --interval 0.5       # poll every 30 mins
  python scripts/agents/trade_monitor.py --set SMCI --t1 21.88 --t2 22.63 --stop 19.57
  python scripts/agents/trade_monitor.py --set SMCI --shares 4 --cost 21.55 --note "4H MACD entry"
  python scripts/agents/trade_monitor.py --close TICKER       # mark trade as inactive
  python scripts/agents/trade_monitor.py --list               # list all monitored trades
"""

import sys
import os
import json
import time
import argparse
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
AGENT_DIR    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR  = os.path.dirname(AGENT_DIR)
ROOT_DIR     = os.path.dirname(SCRIPTS_DIR)
MONITORS_FILE = os.path.join(ROOT_DIR, "data", "monitors.json")
ALERTS_FILE   = os.path.join(ROOT_DIR, "data", "alerts.json")

# ── Rich ──────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.layout import Layout
    from rich import box
    RICH = True
    CONSOLE = Console()
except ImportError:
    RICH = False
    CONSOLE = None

# ── yfinance ──────────────────────────────────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    print("Missing: pip install yfinance"); sys.exit(1)


# ── I/O helpers ───────────────────────────────────────────────────────────────

def load_monitors():
    if not os.path.exists(MONITORS_FILE):
        return {}
    with open(MONITORS_FILE) as f:
        return json.load(f)

def save_monitors(monitors):
    with open(MONITORS_FILE, "w") as f:
        json.dump(monitors, f, indent=2)

def load_alerts():
    if not os.path.exists(ALERTS_FILE):
        return []
    with open(ALERTS_FILE) as f:
        return json.load(f)

def save_alerts(alerts):
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts, f, indent=2)

def append_alert(ticker, alert_type, price, message, level="info"):
    alerts = load_alerts()
    entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "ticker":    ticker,
        "type":      alert_type,
        "price":     round(price, 2),
        "message":   message,
        "level":     level,   # info | warning | danger | success
    }
    alerts.insert(0, entry)   # newest first
    alerts = alerts[:100]     # keep last 100
    save_alerts(alerts)
    return entry


# ── Price fetcher ─────────────────────────────────────────────────────────────

def fetch_price(ticker):
    """Fetch latest price via yfinance. Fast — uses 1d period."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d", auto_adjust=True)
        if hist.empty:
            return None
        return round(float(hist["Close"].iloc[-1]), 2)
    except Exception:
        return None


# ── Alert evaluation ──────────────────────────────────────────────────────────

ALERT_COLORS = {
    "success": "[bold bright_green]",
    "info":    "[bold cyan]",
    "warning": "[bold yellow]",
    "danger":  "[bold red]",
}

def evaluate(ticker, cfg, price):
    """
    Check price against all levels.
    Returns list of (alert_type, message, level) tuples — may be multiple.
    """
    if price is None:
        return []

    alerts = []
    avg_cost  = cfg.get("avg_cost", 0)
    t1        = cfg.get("target1")
    t2        = cfg.get("target2")
    stop      = cfg.get("stop")
    hard_stop = cfg.get("hard_stop")
    shares    = cfg.get("shares", 0)

    pnl_pct = ((price - avg_cost) / avg_cost * 100) if avg_cost else 0
    pnl_abs = (price - avg_cost) * shares if avg_cost else 0

    # Hard stop breached
    if hard_stop and price <= hard_stop:
        alerts.append(("HARD_STOP",
                        f"🚨 {ticker} HARD STOP BREACHED @ ${price:.2f} "
                        f"(hard stop ${hard_stop:.2f}) — EXIT IMMEDIATELY",
                        "danger"))

    # Stop hit
    elif stop and price <= stop:
        alerts.append(("STOP",
                        f"🔴 {ticker} STOP HIT @ ${price:.2f} "
                        f"(stop ${stop:.2f}) | P&L: {pnl_pct:+.1f}% (${pnl_abs:+.0f})",
                        "danger"))

    # Stop warning — within 3%
    elif stop and price <= stop * 1.03:
        pct_to_stop = ((price - stop) / stop * 100)
        alerts.append(("STOP_WARNING",
                        f"⚠️  {ticker} approaching stop — "
                        f"${price:.2f} is {pct_to_stop:.1f}% above stop ${stop:.2f}",
                        "warning"))

    # Target 2 hit
    if t2 and price >= t2:
        alerts.append(("TARGET2",
                        f"🎯 {ticker} TARGET 2 HIT @ ${price:.2f} "
                        f"(T2 ${t2:.2f}) | {pnl_pct:+.1f}% — GOAL REACHED, consider full exit",
                        "success"))

    # Target 1 hit
    elif t1 and price >= t1:
        alerts.append(("TARGET1",
                        f"✅ {ticker} TARGET 1 HIT @ ${price:.2f} "
                        f"(T1 ${t1:.2f}) | {pnl_pct:+.1f}% — consider taking 50%, trail rest to T2",
                        "success"))

    return alerts


# ── Display helpers ───────────────────────────────────────────────────────────

def progress_bar(price, stop, avg_cost, target1, target2, width=30):
    """ASCII progress bar: stop ──●── entry ──── T1 ──── T2"""
    lo  = stop    or (price * 0.85)
    hi  = target2 or target1 or (price * 1.15)
    rng = hi - lo
    if rng <= 0:
        return "─" * width

    def pos(v):
        return max(0, min(width - 1, int((v - lo) / rng * width)))

    bar   = ["─"] * width
    marks = {
        pos(lo):       "S",   # stop
        pos(avg_cost): "E",   # entry
    }
    if target1: marks[pos(target1)] = "1"
    if target2: marks[pos(target2)] = "2"
    # current price marker
    cp = pos(price)
    for p, ch in marks.items():
        bar[p] = ch
    bar[cp] = "●"
    return "".join(bar)


def print_status_table(monitors, prices):
    if not RICH:
        for t, cfg in monitors.items():
            if not cfg.get("active"): continue
            p = prices.get(t)
            print(f"  {t}: ${p:.2f}" if p else f"  {t}: N/A")
        return

    tbl = Table(title="[bold]Active Trade Monitor[/bold]",
                box=box.ROUNDED, header_style="bold white",
                border_style="bright_black", show_lines=False)
    tbl.add_column("Ticker",   style="bold white", width=7)
    tbl.add_column("Price",    justify="right",    width=9)
    tbl.add_column("P&L %",    justify="right",    width=8)
    tbl.add_column("P&L $",    justify="right",    width=9)
    tbl.add_column("Stop",     justify="right",    width=8)
    tbl.add_column("T1",       justify="right",    width=8)
    tbl.add_column("T2",       justify="right",    width=8)
    tbl.add_column("Progress (S=stop E=entry 1=T1 2=T2 ●=price)", width=36)

    for ticker, cfg in monitors.items():
        if not cfg.get("active"):
            continue
        price    = prices.get(ticker)
        avg_cost = cfg.get("avg_cost", 0)
        shares   = cfg.get("shares", 0)
        stop     = cfg.get("stop")
        t1       = cfg.get("target1")
        t2       = cfg.get("target2")

        if price is None:
            tbl.add_row(ticker, "[dim]N/A[/dim]", *["—"]*5, "No data")
            continue

        pnl_pct = ((price - avg_cost) / avg_cost * 100) if avg_cost else 0
        pnl_abs = (price - avg_cost) * shares if avg_cost else 0
        pnl_style = "green" if pnl_pct >= 0 else "red"

        bar = progress_bar(price, stop, avg_cost, t1, t2)

        tbl.add_row(
            ticker,
            f"${price:.2f}",
            Text(f"{pnl_pct:+.1f}%", style=pnl_style),
            Text(f"${pnl_abs:+.0f}", style=pnl_style),
            f"${stop:.2f}"  if stop else "—",
            f"${t1:.2f}"   if t1   else "—",
            f"${t2:.2f}"   if t2   else "—",
            f"[dim]{bar}[/dim]",
        )

    CONSOLE.print(tbl)


def print_alert(alert):
    level = alert.get("level", "info")
    msg   = alert["message"]
    ts    = alert["timestamp"]
    if RICH:
        colors = {"success": "bright_green", "info": "cyan",
                  "warning": "yellow", "danger": "bold red"}
        style = colors.get(level, "white")
        CONSOLE.print(Panel(f"[{style}]{msg}[/{style}]",
                            subtitle=f"[dim]{ts}[/dim]",
                            border_style=style.split()[-1]))
    else:
        print(f"[{ts}] {msg}")


# ── Core poll ─────────────────────────────────────────────────────────────────

def run_check(monitors, verbose=True):
    """Fetch prices for all active monitors and evaluate alerts."""
    active = {t: c for t, c in monitors.items() if c.get("active")}
    if not active:
        if verbose:
            msg = "No active monitors. Add one with --set TICKER"
            CONSOLE.print(f"[dim]{msg}[/dim]") if RICH else print(msg)
        return {}, []

    prices = {}
    fired  = []

    if verbose and RICH:
        CONSOLE.print(f"\n[dim]Checking {len(active)} position(s) @ "
                      f"{datetime.now().strftime('%H:%M:%S')}...[/dim]")

    for ticker, cfg in active.items():
        price = fetch_price(ticker)
        prices[ticker] = price
        alerts = evaluate(ticker, cfg, price)
        for alert_type, message, level in alerts:
            entry = append_alert(ticker, alert_type, price, message, level)
            fired.append(entry)
            if verbose:
                print_alert(entry)

    if verbose:
        print_status_table(active, prices)
        if not fired:
            ts = datetime.now().strftime("%H:%M:%S")
            msg = f"No alerts triggered @ {ts} — all positions within bounds."
            CONSOLE.print(f"  [dim]{msg}[/dim]\n") if RICH else print(f"  {msg}")

    return prices, fired


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_set(monitors, args):
    ticker = args.set.upper()
    cfg    = monitors.get(ticker, {"active": True})
    if args.t1    is not None: cfg["target1"]  = args.t1
    if args.t2    is not None: cfg["target2"]  = args.t2
    if args.stop  is not None: cfg["stop"]     = args.stop
    if args.hstop is not None: cfg["hard_stop"]= args.hstop
    if args.shares is not None: cfg["shares"]  = args.shares
    if args.cost  is not None: cfg["avg_cost"] = args.cost
    if args.note  is not None: cfg["note"]     = args.note
    cfg["active"] = True
    monitors[ticker] = cfg
    save_monitors(monitors)
    print(f"Monitor set for {ticker}: {json.dumps(cfg, indent=2)}")


def cmd_close(monitors, ticker):
    ticker = ticker.upper()
    if ticker in monitors:
        monitors[ticker]["active"] = False
        save_monitors(monitors)
        print(f"{ticker} monitor closed.")
    else:
        print(f"{ticker} not found in monitors.")


def cmd_list(monitors):
    active   = {t: c for t, c in monitors.items() if c.get("active")}
    inactive = {t: c for t, c in monitors.items() if not c.get("active")}

    if not RICH:
        for t, c in active.items():
            print(f"  ACTIVE  {t}: entry=${c.get('avg_cost','?')} "
                  f"stop=${c.get('stop','?')} t1=${c.get('target1','?')} t2=${c.get('target2','?')}")
        for t, c in inactive.items():
            print(f"  CLOSED  {t}")
        return

    CONSOLE.print()
    if active:
        tbl = Table(title="[bold]Active Monitors[/bold]", box=box.SIMPLE_HEAD,
                    header_style="bold white", border_style="bright_black")
        for col in ["Ticker","Shares","Avg Cost","Stop","Hard Stop","T1","T2","Note"]:
            tbl.add_column(col)
        for t, c in active.items():
            tbl.add_row(
                t,
                str(c.get("shares","")),
                f"${c.get('avg_cost','')}" if c.get("avg_cost") else "—",
                f"${c.get('stop','')}"     if c.get("stop")     else "—",
                f"${c.get('hard_stop','')}"if c.get("hard_stop")else "—",
                f"${c.get('target1','')}"  if c.get("target1")  else "—",
                f"${c.get('target2','')}"  if c.get("target2")  else "—",
                c.get("note",""),
            )
        CONSOLE.print(tbl)
    else:
        CONSOLE.print("[dim]No active monitors.[/dim]")

    if inactive:
        CONSOLE.print(f"\n[dim]Closed: {', '.join(inactive.keys())}[/dim]")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent 3 — Trade Monitor")
    parser.add_argument("--check",    action="store_true", help="One-shot price check")
    parser.add_argument("--interval", type=float, default=4.0,
                        help="Poll interval in hours (default 4.0)")
    parser.add_argument("--set",      metavar="TICKER",    help="Add/update a trade monitor")
    parser.add_argument("--close",    metavar="TICKER",    help="Mark trade as closed/inactive")
    parser.add_argument("--list",     action="store_true", help="List all monitors")
    # --set options
    parser.add_argument("--t1",     type=float, metavar="PRICE", help="Target 1 price")
    parser.add_argument("--t2",     type=float, metavar="PRICE", help="Target 2 price")
    parser.add_argument("--stop",   type=float, metavar="PRICE", help="Stop-loss price")
    parser.add_argument("--hstop",  type=float, metavar="PRICE", help="Hard stop price")
    parser.add_argument("--shares", type=float, metavar="N",     help="Number of shares")
    parser.add_argument("--cost",   type=float, metavar="PRICE", help="Average cost per share")
    parser.add_argument("--note",   type=str,                    help="Trade note/rationale")
    args = parser.parse_args()

    monitors = load_monitors()

    # Mutations
    if args.set:
        cmd_set(monitors, args); return
    if args.close:
        cmd_close(monitors, args.close); return
    if args.list:
        cmd_list(monitors); return

    # One-shot check
    if args.check:
        run_check(monitors)
        return

    # ── Continuous polling loop ───────────────────────────────────────────────
    interval_secs = args.interval * 3600
    if RICH:
        CONSOLE.print()
        CONSOLE.rule(f"[bold cyan]Trade Monitor — polling every {args.interval}h[/bold cyan]")
        CONSOLE.print(f"[dim]Press Ctrl+C to stop[/dim]\n")
    else:
        print(f"Trade Monitor — polling every {args.interval}h. Ctrl+C to stop.")

    poll_count = 0
    try:
        while True:
            poll_count += 1
            if RICH:
                CONSOLE.rule(f"[dim]Poll #{poll_count}[/dim]")
            monitors = load_monitors()   # reload in case user updated
            run_check(monitors)

            next_poll = datetime.fromtimestamp(time.time() + interval_secs)
            next_str  = next_poll.strftime("%H:%M:%S")
            msg = f"Next check @ {next_str} ({args.interval}h)"
            if RICH:
                CONSOLE.print(f"\n[dim]{msg}[/dim]\n")
            else:
                print(f"\n  {msg}\n")

            # Sleep in small chunks so Ctrl+C is responsive
            end_time = time.time() + interval_secs
            while time.time() < end_time:
                remaining = end_time - time.time()
                time.sleep(min(30, remaining))

    except KeyboardInterrupt:
        msg = "Monitor stopped."
        CONSOLE.print(f"\n[dim]{msg}[/dim]") if RICH else print(f"\n{msg}")


if __name__ == "__main__":
    main()
