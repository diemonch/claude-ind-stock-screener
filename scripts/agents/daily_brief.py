#!/usr/bin/env python3
"""
Agent 1 — Daily Market Brief
Fetches portfolio data + live news per ticker, then calls Claude to
generate a structured pre-market brief.

Usage:
  python scripts/agents/daily_brief.py
  python scripts/agents/daily_brief.py --holdings-only   # skip watchlist news
  python scripts/agents/daily_brief.py --top N           # watchlist top N (default 3)

Requires:
  ANTHROPIC_API_KEY env var
  pip install anthropic duckduckgo-search
"""

import sys
import os
import json
import subprocess
from dotenv import load_dotenv

load_dotenv()
import argparse
from datetime import datetime, timedelta

# ── Path setup ────────────────────────────────────────────────────────────────
AGENT_DIR    = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR  = os.path.dirname(AGENT_DIR)
ROOT_DIR     = os.path.dirname(SCRIPTS_DIR)
PORTFOLIO_PY = os.path.join(SCRIPTS_DIR, "portfolio.py")
PYTHON       = "/opt/homebrew/bin/python3.9"

sys.path.insert(0, SCRIPTS_DIR)


def load_env_file():
    """Load .env file from the AnthropicAI project root."""
    candidates = [
        os.path.join(ROOT_DIR, "..", "..", ".env"),          # skills/skills/stock-analyst → AnthropicAI
        os.path.join(ROOT_DIR, "..", "..", "..", ".env"),
        os.path.expanduser("~/Projects/py/IITD/AnthropicAI/.env"),
    ]
    for path in candidates:
        path = os.path.normpath(path)
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, _, val = line.partition("=")
                        val = val.strip().strip('"').strip("'")
                        os.environ.setdefault(key.strip(), val)
            return path
    return None


_env_path = load_env_file()

# ── Deps ──────────────────────────────────────────────────────────────────────
try:
    import anthropic
except ImportError:
    print("Missing: pip install anthropic"); sys.exit(1)

try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("Missing: pip install ddgs"); sys.exit(1)

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.markdown import Markdown
    from rich.spinner import Spinner
    from rich.live import Live
    from rich.text import Text
    RICH = True
    CONSOLE = Console()
except ImportError:
    RICH = False
    CONSOLE = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def status(msg):
    if RICH:
        CONSOLE.print(f"  [dim]{msg}[/dim]")
    else:
        print(f"  {msg}")


def fetch_portfolio_data():
    """Run portfolio.py --json and return parsed result."""
    status("Fetching portfolio data...")
    result = subprocess.run(
        [PYTHON, PORTFOLIO_PY, "--json"],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"portfolio.py failed: {result.stderr[:200]}")
    return json.loads(result.stdout)


def search_news(ticker, company_name, max_results=4):
    """Search DuckDuckGo for recent news on a ticker."""
    today = datetime.today().strftime("%B %Y")
    query = f"{company_name} {ticker} stock news earnings {today}"
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        headlines = []
        for r in results:
            title = r.get("title", "")
            source = r.get("source", "")
            date = r.get("date", "")
            body = r.get("body", "")[:120]
            headlines.append(f"- [{source}] {title}. {body}")
        return "\n".join(headlines) if headlines else "No recent news found."
    except Exception as e:
        return f"News fetch failed: {e}"


def build_holding_context(holding, analysis, news):
    """Build a text block summarising one holding for the prompt."""
    t = holding["ticker"]
    shares = holding["shares"]
    avg_cost = holding["avg_cost"]

    if "error" in analysis:
        return f"### {t}\nData unavailable: {analysis['error']}\nNews:\n{news}\n"

    price = analysis.get("current_price", 0)
    pnl_pct = ((price - avg_cost) / avg_cost * 100) if avg_cost else 0
    pnl_abs = (price - avg_cost) * shares if avg_cost else 0

    f = analysis.get("fundamentals") or {}
    analyst = f.get("analyst_estimates") or {}
    ma = analysis.get("moving_averages") or {}
    sl = analysis.get("suggested_levels") or {}
    atr_d = analysis.get("atr") or {}
    pp = analysis.get("pivot_points") or {}

    ema200 = ma.get("ema_200")
    trend = "uptrend" if (price and ema200 and price > ema200) else "downtrend"
    pivot = pp.get("pivot")
    pivot_bias = "bullish" if (price and pivot and price > pivot) else "bearish"
    supports = sl.get("strong_support") or []
    resistances = sl.get("strong_resistance") or []
    stop = sl.get("stop_loss_long")
    daily_hi = atr_d.get("daily_target_up")
    daily_lo = atr_d.get("daily_target_down")
    target = analyst.get("mean_target")
    upside = analyst.get("upside_to_mean_target_pct")
    rec = analyst.get("recommendation", "N/A")
    health = f.get("financial_health") or {}
    growth = f.get("growth") or {}
    fcf = health.get("free_cash_flow")
    rev_gr = growth.get("revenue_growth_yoy_pct")

    return f"""### {t} — {analysis.get('company_name', t)}
Position: {shares} shares @ ${avg_cost:.2f} avg cost | Current: ${price:.2f} | P&L: {pnl_pct:+.1f}% (${pnl_abs:+.0f})
Trend: {trend} | Weekly pivot bias: {pivot_bias}
Daily range: ${daily_lo:.2f}–${daily_hi:.2f} (ATR ±${atr_d.get('atr_value', 0):.2f})
Support: {', '.join(f'${s:.2f}' for s in supports[:2])} | Resistance: {', '.join(f'${r:.2f}' for r in resistances[:2])}
Stop-loss: ${stop:.2f}
Analyst: {rec.upper()} | Target ${target:.2f} ({upside:+.1f}% upside)
FCF: {'${:,.0f}'.format(fcf) if fcf else 'N/A'} | Rev growth: {f'{rev_gr:.0f}%' if rev_gr else 'N/A'} YoY

Recent News:
{news}
"""


def build_watchlist_context(ticker, analysis, news):
    """Build a text block for a watchlist stock."""
    if "error" in analysis:
        return f"### {ticker}\nData unavailable.\nNews:\n{news}\n"

    price = analysis.get("current_price", 0)
    f = analysis.get("fundamentals") or {}
    analyst = f.get("analyst_estimates") or {}
    val = f.get("valuation") or {}
    growth = f.get("growth") or {}
    sl = analysis.get("suggested_levels") or {}
    ma = analysis.get("moving_averages") or {}
    atr_d = analysis.get("atr") or {}

    ema200 = ma.get("ema_200")
    trend = "uptrend" if (price and ema200 and price > ema200) else "downtrend"
    supports = sl.get("strong_support") or []
    stop = sl.get("stop_loss_long")
    target = analyst.get("mean_target")
    upside = analyst.get("upside_to_mean_target_pct")
    fwd_pe = val.get("forward_pe")
    rev_gr = growth.get("revenue_growth_yoy_pct")
    daily_hi = atr_d.get("daily_target_up")

    return f"""### {ticker} — {analysis.get('company_name', ticker)}
Price: ${price:.2f} | Trend: {trend}
Entry zone: {', '.join(f'${s:.2f}' for s in supports[:2])} | Stop: ${stop:.2f} | Daily target: ${daily_hi:.2f}
Analyst: Target ${target:.2f} ({upside:+.1f}%) | Fwd P/E: {f'{fwd_pe:.1f}x' if fwd_pe else 'N/A'} | Rev growth: {f'{rev_gr:.0f}%' if rev_gr else 'N/A'}

Recent News:
{news}
"""


BRIEF_SYSTEM_PROMPT = """You are a professional equity analyst and portfolio manager producing a concise pre-market daily brief for a retail investor with an AI-theme portfolio. You have access to live technical data, fundamentals, and breaking news for each position.

Your brief must be actionable, honest, and structured. Do not pad with generic disclaimers. Be direct — if something looks dangerous, say so. If there's a clear opportunity, name it.

Format rules:
- Use markdown headers and bullets
- Keep the total brief under 600 words
- Numbers must match the data provided — do not invent prices or percentages
- Always end with a single "One thing to do today" action item
"""

BRIEF_USER_TEMPLATE = """Today is {date}. Produce the Daily Market Brief for this AI portfolio.

---
PORTFOLIO SUMMARY
Total holdings: {num_holdings} positions | Portfolio P&L: {total_pnl_pct:+.1f}% (${total_pnl_abs:+.0f})

---
HOLDINGS DATA & NEWS
{holdings_context}

---
TOP WATCHLIST OPPORTUNITIES
{watchlist_context}

---
Produce the brief in this exact structure:

## Daily Market Brief — {date}

### Market Pulse
2-3 sentences on the overall AI/tech market tone today based on the news.

### Portfolio Status
For each holding, one line:
`[🟢/🟡/🔴] TICKER — verdict + one key fact from news or technicals`

### Pre-Market Alerts
Bullet any positions requiring action today (stop approaching, catalyst event, earnings, major news).
If none, write "No urgent alerts."

### Top Opportunity Today
Name the single best entry from the watchlist right now and why — entry zone, catalyst, target.

### Key Risk Today
The one thing that could hurt this portfolio today.

### One thing to do today
A single, specific action sentence.
"""


def call_claude(prompt_text, api_key):
    """Stream Claude's response and print it live."""
    client = anthropic.Anthropic(api_key=api_key, timeout=120.0)

    if RICH:
        CONSOLE.print()
        CONSOLE.rule("[bold cyan]Daily Market Brief[/bold cyan]")
        CONSOLE.print()

    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=BRIEF_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt_text}]
    ) as stream:
        full_text = ""
        for text in stream.text_stream:
            print(text, end="", flush=True)
            full_text += text

    print()  # final newline
    return full_text


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent 1 — Daily Market Brief")
    parser.add_argument("--holdings-only", action="store_true",
                        help="Skip watchlist news (faster)")
    parser.add_argument("--top", type=int, default=3,
                        help="Number of watchlist stocks to include (default 3)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        print("  export ANTHROPIC_API_KEY=your_key_here")
        sys.exit(1)

    today = datetime.today().strftime("%Y-%m-%d")

    if RICH:
        CONSOLE.print()
        CONSOLE.rule(f"[bold yellow]AI Portfolio — Daily Brief Agent[/bold yellow]  [dim]{today}[/dim]")
        CONSOLE.print()

    # ── Step 1: Portfolio data ────────────────────────────────────────────────
    try:
        pf_data = fetch_portfolio_data()
    except Exception as e:
        print(f"Failed to fetch portfolio data: {e}"); sys.exit(1)

    portfolio  = pf_data["portfolio"]
    analyses   = pf_data["analyses"]
    holdings   = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
    watchlist  = portfolio.get("watchlist", [])

    # ── Step 2: Compute portfolio totals ─────────────────────────────────────
    total_cost = total_value = 0
    for h in holdings:
        t = h["ticker"]
        data = analyses.get(t, {})
        price = data.get("current_price") if "error" not in data else None
        if price:
            total_cost  += h["shares"] * h["avg_cost"]
            total_value += h["shares"] * price
    total_pnl_abs = total_value - total_cost
    total_pnl_pct = (total_pnl_abs / total_cost * 100) if total_cost else 0

    # ── Step 3: News for holdings ─────────────────────────────────────────────
    status(f"Searching news for {len(holdings)} holdings...")
    holdings_context = ""
    for h in holdings:
        t = h["ticker"]
        data = analyses.get(t, {})
        company = data.get("company_name", t) if "error" not in data else t
        status(f"  News: {t}...")
        news = search_news(t, company)
        holdings_context += build_holding_context(h, data, news) + "\n"

    # ── Step 4: News for top watchlist picks ─────────────────────────────────
    watchlist_context = ""
    if not args.holdings_only and watchlist:
        # Score and pick top N
        from portfolio import score_stock
        scored = []
        for t in watchlist:
            data = analyses.get(t, {})
            if "error" not in data:
                score, signal, _ = score_stock(data)
                scored.append((score or 0, t, data))
        scored.sort(reverse=True)
        top_tickers = scored[:args.top]

        status(f"Searching news for top {len(top_tickers)} watchlist picks...")
        for score, t, data in top_tickers:
            company = data.get("company_name", t)
            status(f"  News: {t}...")
            news = search_news(t, company)
            watchlist_context += build_watchlist_context(t, data, news) + "\n"
    else:
        watchlist_context = "Watchlist news skipped (--holdings-only mode)."

    # ── Step 5: Build prompt ──────────────────────────────────────────────────
    prompt = BRIEF_USER_TEMPLATE.format(
        date=today,
        num_holdings=len(holdings),
        total_pnl_pct=total_pnl_pct,
        total_pnl_abs=total_pnl_abs,
        holdings_context=holdings_context.strip(),
        watchlist_context=watchlist_context.strip(),
    )

    # ── Step 6: Call Claude ───────────────────────────────────────────────────
    status("Calling Claude for brief synthesis...")
    if RICH:
        CONSOLE.print()
    call_claude(prompt, api_key)

    if RICH:
        CONSOLE.print()
        CONSOLE.rule("[dim]* Not financial advice.[/dim]")
        CONSOLE.print()


if __name__ == "__main__":
    main()
