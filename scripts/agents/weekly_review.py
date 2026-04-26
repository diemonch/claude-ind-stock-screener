#!/usr/bin/env python3
"""
Agent 4 — Weekly Review
Runs on a configurable interval (default daily). Each run:
  1. Saves a timestamped portfolio snapshot
  2. Compares current vs last snapshot  (daily delta)
  3. Compares current vs Monday snapshot (weekly delta)
  4. Fetches brief news per holding
  5. Calls Claude for a structured narrative review

Usage:
  python scripts/agents/weekly_review.py              # one-shot review
  python scripts/agents/weekly_review.py --interval 1 # run every 1 day (default)
  python scripts/agents/weekly_review.py --interval 0.5 # every 12 hours
  python scripts/agents/weekly_review.py --snapshots  # list saved snapshots
"""

import sys
import os
import json
from dotenv import load_dotenv

load_dotenv()
import time
import subprocess
import argparse
from datetime import datetime, timedelta

# ── Paths ─────────────────────────────────────────────────────────────────────
AGENT_DIR     = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR   = os.path.dirname(AGENT_DIR)
ROOT_DIR      = os.path.dirname(SCRIPTS_DIR)
PORTFOLIO_PY  = os.path.join(SCRIPTS_DIR, "portfolio.py")
SNAPSHOTS_DIR = os.path.join(ROOT_DIR, "data", "snapshots")
REVIEWS_FILE  = os.path.join(ROOT_DIR, "data", "reviews.json")
PYTHON        = "/opt/homebrew/bin/python3.9"

sys.path.insert(0, SCRIPTS_DIR)
os.makedirs(SNAPSHOTS_DIR, exist_ok=True)

# ── Load .env ─────────────────────────────────────────────────────────────────
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

# ── Rich ──────────────────────────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.rule import Rule
    from rich.panel import Panel
    RICH = True
    CONSOLE = Console()
except ImportError:
    RICH = False
    CONSOLE = None

# ── DuckDuckGo ────────────────────────────────────────────────────────────────
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        DDGS = None


# ── Snapshot I/O ──────────────────────────────────────────────────────────────

def snapshot_path(ts: datetime) -> str:
    return os.path.join(SNAPSHOTS_DIR, ts.strftime("%Y-%m-%d_%H-%M.json"))

def monday_path(ts: datetime) -> str:
    # Monday of the current week
    monday = ts - timedelta(days=ts.weekday())
    return os.path.join(SNAPSHOTS_DIR, f"monday_{monday.strftime('%Y-%m-%d')}.json")

def save_snapshot(pf_data: dict, ts: datetime = None):
    ts = ts or datetime.now()
    portfolio = pf_data["portfolio"]
    analyses  = pf_data["analyses"]
    holdings  = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]

    prices = {}
    total_cost = total_value = 0
    for h in holdings:
        t = h["ticker"]
        d = analyses.get(t, {})
        price = d.get("current_price") if "error" not in d else None
        prices[t] = price
        if price:
            total_cost  += h["shares"] * h["avg_cost"]
            total_value += h["shares"] * price

    snap = {
        "timestamp":      ts.isoformat(),
        "prices":         prices,
        "total_cost":     round(total_cost, 2),
        "total_value":    round(total_value, 2),
        "total_pnl_abs":  round(total_value - total_cost, 2),
        "total_pnl_pct":  round((total_value - total_cost) / total_cost * 100, 2) if total_cost else 0,
        "holdings":       [{
            "ticker":   h["ticker"],
            "shares":   h["shares"],
            "avg_cost": h["avg_cost"],
            "price":    prices.get(h["ticker"]),
        } for h in holdings],
        "watchlist":      portfolio.get("watchlist", []),
    }

    # Save timestamped snapshot
    path = snapshot_path(ts)
    with open(path, "w") as f:
        json.dump(snap, f, indent=2)

    # Pin as Monday snapshot if today is Monday and it doesn't exist yet
    mon_path = monday_path(ts)
    if ts.weekday() == 0 and not os.path.exists(mon_path):
        with open(mon_path, "w") as f:
            json.dump(snap, f, indent=2)

    return snap, path


def load_last_snapshot(exclude_path=None):
    """Load the most recent snapshot (other than the one just saved)."""
    files = sorted([
        f for f in os.listdir(SNAPSHOTS_DIR)
        if f.endswith(".json") and not f.startswith("monday_")
    ])
    # Filter out the just-saved one
    if exclude_path:
        exclude_name = os.path.basename(exclude_path)
        files = [f for f in files if f != exclude_name]
    if not files:
        return None
    with open(os.path.join(SNAPSHOTS_DIR, files[-1])) as f:
        return json.load(f)


def load_monday_snapshot(ts: datetime = None):
    """Load Monday's snapshot for the current week."""
    ts = ts or datetime.now()
    mon_path = monday_path(ts)
    if os.path.exists(mon_path):
        with open(mon_path) as f:
            return json.load(f)
    # Fallback: oldest available snapshot
    files = sorted([
        f for f in os.listdir(SNAPSHOTS_DIR)
        if f.endswith(".json") and not f.startswith("monday_")
    ])
    if files:
        with open(os.path.join(SNAPSHOTS_DIR, files[0])) as f:
            return json.load(f)
    return None


# ── Delta computation ─────────────────────────────────────────────────────────

def compute_delta(current_snap: dict, prev_snap: dict, label: str) -> dict:
    """Compute price and P&L deltas between two snapshots."""
    if not prev_snap:
        return {"label": label, "available": False}

    prev_ts   = prev_snap.get("timestamp", "unknown")
    curr_val  = current_snap["total_value"]
    prev_val  = prev_snap.get("total_value", curr_val)
    val_delta = curr_val - prev_val
    val_pct   = (val_delta / prev_val * 100) if prev_val else 0

    per_ticker = {}
    for h in current_snap["holdings"]:
        t          = h["ticker"]
        curr_price = h.get("price")
        prev_price = prev_snap.get("prices", {}).get(t)
        if curr_price and prev_price:
            chg     = curr_price - prev_price
            chg_pct = (chg / prev_price * 100)
            per_ticker[t] = {
                "prev":    round(prev_price, 2),
                "current": round(curr_price, 2),
                "change":  round(chg, 2),
                "pct":     round(chg_pct, 2),
            }

    return {
        "label":       label,
        "available":   True,
        "prev_ts":     prev_ts,
        "val_delta":   round(val_delta, 2),
        "val_pct":     round(val_pct, 2),
        "per_ticker":  per_ticker,
    }


# ── News fetcher ──────────────────────────────────────────────────────────────

def fetch_news_brief(ticker, company_name, max_results=3):
    if not DDGS:
        return "News unavailable (ddgs not installed)."
    today = datetime.today().strftime("%B %Y")
    query = f"{company_name} {ticker} stock news {today}"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        lines = [f"- [{r.get('source','')}] {r.get('title','')}. {r.get('body','')[:100]}"
                 for r in results]
        return "\n".join(lines) if lines else "No recent news."
    except Exception as e:
        return f"News fetch failed: {e}"


# ── Claude prompt ─────────────────────────────────────────────────────────────

REVIEW_SYSTEM = """You are a portfolio manager producing a structured periodic review for a retail AI-theme investor.
Your review covers two timeframes: since the last daily check AND since Monday (weekly view).
Be direct, quantitative, and actionable. Highlight what changed, why it likely moved, and what to do next.
Keep the total review under 700 words. Use markdown."""

REVIEW_TEMPLATE = """Today: {date}

=== PORTFOLIO SNAPSHOT ===
Current Value: ${curr_value:,.2f} | Cost Basis: ${cost:,.2f} | Total P&L: {total_pnl_pct:+.1f}% (${total_pnl_abs:+,.0f})

=== SINCE LAST CHECK ({daily_label}) ===
Portfolio Value Change: {daily_val_pct:+.1f}% (${daily_val_delta:+,.0f})
{daily_ticker_deltas}

=== SINCE MONDAY ({weekly_label}) ===
Portfolio Value Change: {weekly_val_pct:+.1f}% (${weekly_val_delta:+,.0f})
{weekly_ticker_deltas}

=== HOLDINGS & NEWS ===
{holdings_context}

=== WATCHLIST TOP 3 ===
{watchlist_context}

---
Write the review in this structure:

## Weekly Review — {date}

### Performance Recap
Table: Ticker | Since Last Check | Since Monday | Verdict (↑Hold / ↓Watch / 🚨Act)

### What Moved & Why
2-3 sentences on the biggest movers, linking to news where relevant.

### Portfolio Health
One line each: allocation balance, biggest risk concentration, stop-loss exposure.

### Watchlist Pulse
Which watchlist stocks improved/worsened in opportunity score this period.

### Week-Ahead Strategy
3 bullet points: specific levels to watch, actions to consider, catalyst dates.

### One Priority Action
Single sentence — the most important thing to do before next review.
"""


def fmt_delta_block(delta: dict) -> str:
    if not delta.get("available"):
        return "No previous snapshot available for comparison."
    lines = []
    for t, d in delta.get("per_ticker", {}).items():
        arrow = "↑" if d["pct"] >= 0 else "↓"
        lines.append(f"  {t}: ${d['prev']:.2f} → ${d['current']:.2f}  {arrow}{abs(d['pct']):.1f}%")
    return "\n".join(lines) if lines else "No price data."


# ── Save review ───────────────────────────────────────────────────────────────

def save_review(text: str, ts: datetime):
    reviews = []
    if os.path.exists(REVIEWS_FILE):
        with open(REVIEWS_FILE) as f:
            reviews = json.load(f)
    reviews.insert(0, {
        "timestamp": ts.isoformat(),
        "date":      ts.strftime("%Y-%m-%d %H:%M"),
        "text":      text,
    })
    reviews = reviews[:30]  # keep last 30 reviews
    with open(REVIEWS_FILE, "w") as f:
        json.dump(reviews, f, indent=2)


# ── Core: one review run ──────────────────────────────────────────────────────

def run_review(verbose=True):
    import anthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set."); return None

    ts = datetime.now()

    if verbose:
        msg = f"Running review @ {ts.strftime('%Y-%m-%d %H:%M')}"
        CONSOLE.rule(f"[bold cyan]{msg}[/bold cyan]") if RICH else print(f"\n{'='*60}\n{msg}")

    # ── Step 1: Portfolio data ────────────────────────────────────────────────
    if verbose:
        _status("Fetching portfolio data...")
    result = subprocess.run(
        [PYTHON, PORTFOLIO_PY, "--json"],
        capture_output=True, text=True, timeout=300
    )
    if not result.stdout.strip():
        print(f"Failed: {result.stderr[:200]}"); return None
    pf_data   = json.loads(result.stdout)
    portfolio = pf_data["portfolio"]
    analyses  = pf_data["analyses"]
    holdings  = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
    watchlist = portfolio.get("watchlist", [])

    # ── Step 2: Save snapshot + load comparisons ──────────────────────────────
    if verbose: _status("Saving snapshot...")
    curr_snap, snap_path = save_snapshot(pf_data, ts)
    last_snap   = load_last_snapshot(exclude_path=snap_path)
    monday_snap = load_monday_snapshot(ts)

    daily_delta  = compute_delta(curr_snap, last_snap,   _snap_label(last_snap))
    weekly_delta = compute_delta(curr_snap, monday_snap, _snap_label(monday_snap))

    # ── Step 3: News ──────────────────────────────────────────────────────────
    if verbose: _status(f"Fetching news for {len(holdings)} holdings...")
    holdings_ctx = ""
    for h in holdings:
        t    = h["ticker"]
        d    = analyses.get(t, {})
        name = d.get("company_name", t) if "error" not in d else t
        if verbose: _status(f"  {t}...")
        news = fetch_news_brief(t, name)
        price = d.get("current_price") if "error" not in d else None
        pnl   = ((price - h["avg_cost"]) / h["avg_cost"] * 100) if price and h["avg_cost"] else None
        holdings_ctx += (f"### {t} ({name})\n"
                         f"Price: ${price:.2f} | P&L: {pnl:+.1f}% from ${h['avg_cost']:.2f}\n"
                         f"{news}\n\n")

    # ── Step 4: Top watchlist ─────────────────────────────────────────────────
    from portfolio import score_stock
    scored_w = []
    for t in watchlist:
        d = analyses.get(t, {})
        if "error" not in d:
            score, signal, _ = score_stock(d)
            price = d.get("current_price")
            scored_w.append((score or 0, t, price, signal))
    scored_w.sort(reverse=True)
    watchlist_ctx = ""
    for score, t, price, signal in scored_w[:3]:
        d  = analyses.get(t, {})
        f  = d.get("fundamentals") or {}
        an = f.get("analyst_estimates") or {}
        watchlist_ctx += (f"- {t}: ${price:.2f} | {signal} ({score}/100) | "
                          f"Target ${an.get('mean_target','?')} "
                          f"({an.get('upside_to_mean_target_pct','?'):+.1f}% upside)\n")

    # ── Step 5: Build prompt ──────────────────────────────────────────────────
    prompt = REVIEW_TEMPLATE.format(
        date              = ts.strftime("%Y-%m-%d"),
        curr_value        = curr_snap["total_value"],
        cost              = curr_snap["total_cost"],
        total_pnl_pct     = curr_snap["total_pnl_pct"],
        total_pnl_abs     = curr_snap["total_pnl_abs"],
        daily_label       = daily_delta.get("label", "N/A"),
        daily_val_pct     = daily_delta.get("val_pct", 0),
        daily_val_delta   = daily_delta.get("val_delta", 0),
        daily_ticker_deltas = fmt_delta_block(daily_delta),
        weekly_label      = weekly_delta.get("label", "N/A"),
        weekly_val_pct    = weekly_delta.get("val_pct", 0),
        weekly_val_delta  = weekly_delta.get("val_delta", 0),
        weekly_ticker_deltas = fmt_delta_block(weekly_delta),
        holdings_context  = holdings_ctx.strip(),
        watchlist_context = watchlist_ctx.strip(),
    )

    # ── Step 6: Stream Claude ─────────────────────────────────────────────────
    if verbose:
        _status("Calling Claude...")
        CONSOLE.print() if RICH else print()

    client   = anthropic.Anthropic(api_key=api_key, timeout=240.0)
    full_text = ""
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        system=REVIEW_SYSTEM,
        messages=[{"role": "user", "content": prompt}]
    ) as stream:
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text
    print()

    save_review(full_text, ts)
    return full_text


def _status(msg):
    CONSOLE.print(f"  [dim]{msg}[/dim]") if RICH else print(f"  {msg}")


def _snap_label(snap):
    if not snap:
        return "no prior snapshot"
    ts = snap.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ts[:16]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent 4 — Weekly Review")
    parser.add_argument("--interval",  type=float, default=1.0,
                        help="Run interval in days (default 1.0 = daily)")
    parser.add_argument("--snapshots", action="store_true",
                        help="List saved snapshots and exit")
    args = parser.parse_args()

    if args.snapshots:
        files = sorted(os.listdir(SNAPSHOTS_DIR))
        if not files:
            print("No snapshots saved yet.")
        else:
            print(f"\n{len(files)} snapshot(s) in {SNAPSHOTS_DIR}:\n")
            for f in files:
                print(f"  {f}")
        return

    interval_secs = args.interval * 86400

    if args.interval == 1.0:
        if RICH:
            CONSOLE.rule("[bold cyan]Agent 4 — Weekly Review (daily interval)[/bold cyan]")
            CONSOLE.print("[dim]Press Ctrl+C to stop[/dim]\n")
        else:
            print("Agent 4 — Weekly Review. Running daily. Ctrl+C to stop.")

    run_count = 0
    try:
        while True:
            run_count += 1
            run_review(verbose=True)
            if interval_secs <= 0:
                break   # one-shot if interval is 0

            next_run = datetime.fromtimestamp(time.time() + interval_secs)
            msg = f"Next review @ {next_run.strftime('%Y-%m-%d %H:%M')} ({args.interval}d)"
            CONSOLE.print(f"\n[dim]{msg}[/dim]\n") if RICH else print(f"\n  {msg}\n")

            end_time = time.time() + interval_secs
            while time.time() < end_time:
                time.sleep(min(60, end_time - time.time()))

    except KeyboardInterrupt:
        CONSOLE.print("\n[dim]Review agent stopped.[/dim]") if RICH else print("\nStopped.")


if __name__ == "__main__":
    main()
