#!/usr/bin/env python3
"""
Agent 2 — Stock Screener
Discovers and ranks stocks matching a theme using:
  (A) Claude's knowledge for initial candidates
  (B) Web search to validate and expand the list
  (C) Optional manual tickers added by the user

Usage:
  python scripts/agents/screener.py "AI inference plays under $50"
  python scripts/agents/screener.py "AI networking stocks" --tickers ANET,CSCO,INFN
  python scripts/agents/screener.py "AI datacenter REITs" --top 5
  python scripts/agents/screener.py --interactive
"""

import sys
import os
import json
import subprocess
import argparse
from dotenv import load_dotenv

load_dotenv()
import re
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
AGENT_DIR   = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(AGENT_DIR)
ROOT_DIR    = os.path.dirname(SCRIPTS_DIR)
ANALYZE_PY  = os.path.join(SCRIPTS_DIR, "analyze.py")
PYTHON      = "/opt/homebrew/bin/python3.9"

sys.path.insert(0, SCRIPTS_DIR)

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
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
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

# ── Anthropic ─────────────────────────────────────────────────────────────────
try:
    import anthropic
except ImportError:
    print("Missing: pip install anthropic"); sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _status(msg):
    if RICH:
        CONSOLE.print(f"  [dim]{msg}[/dim]")
    else:
        print(f"  {msg}")

def _extract_tickers_from_text(text):
    """Pull uppercase ticker-like tokens (2-5 chars) from free text."""
    raw = re.findall(r'\b([A-Z]{2,5})\b', text)
    # Filter out common non-ticker words
    noise = {"AI", "US", "GPU", "CEO", "CFO", "IPO", "ETF", "THE", "AND",
             "FOR", "WITH", "NEW", "TOP", "BEST", "STOCK", "CLOUD", "DATA",
             "API", "EPS", "TTM", "ROE", "FCF", "YOY", "ATR", "EMA", "SMA"}
    return list(dict.fromkeys(t for t in raw if t not in noise))


def parse_price_filter(theme):
    """Extract a price constraint from the theme string.
    Returns (operator, value) e.g. ('<', 30.0) or None."""
    theme_lower = theme.lower()
    # Patterns: "under $30", "below 30$", "under 30", "< $30", "above $50", "over $100"
    pattern = r'(under|below|above|over|<|>)\s*\$?\s*(\d+\.?\d*)\$?'
    match = re.search(pattern, theme_lower)
    if match:
        op_word, val = match.group(1), float(match.group(2))
        op = '<' if op_word in ('under', 'below', '<') else '>'
        return op, val
    return None


def apply_price_filter(rows, price_filter):
    """Remove rows that don't satisfy the price constraint."""
    if not price_filter:
        return rows
    op, limit = price_filter
    filtered = []
    for r in rows:
        price = r.get("price")
        if price is None:
            continue
        if op == '<' and price < limit:
            filtered.append(r)
        elif op == '>' and price > limit:
            filtered.append(r)
    return filtered


# ── Step A: Claude initial candidates ─────────────────────────────────────────

def claude_initial_candidates(theme, client, n=8):
    """Ask Claude to suggest tickers matching the theme from its knowledge.
    Returns (tickers_list, ai_layers_dict, rationale)."""
    _status(f"Claude generating initial candidates for: '{theme}'...")
    price_filter = parse_price_filter(theme)
    price_constraint = ""
    if price_filter:
        op, val = price_filter
        word = "under" if op == '<' else "above"
        price_constraint = (
            f"\nCRITICAL: The theme requires stocks trading {word} ${val:.0f}. "
            f"ONLY include tickers whose current stock price is {word} ${val:.0f}. "
            f"Do NOT include any stock priced {'above' if op == '<' else 'below'} ${val:.0f}."
        )
    prompt = f"""You are a stock research assistant. For the investment theme: "{theme}"{price_constraint}

List the {n} most relevant publicly traded US stock tickers (NYSE/NASDAQ).
Consider market cap, liquidity, and direct relevance to the theme.
For each ticker, classify its AI layer (e.g. "AI Silicon", "AI Infrastructure",
"AI Networking", "AI Cloud", "AI Applications", "AI Edge", "AI Data", "AI Security").

Return ONLY a JSON object in this exact format:
{{
  "tickers": [
    {{"ticker": "TICK1", "layer": "AI Silicon"}},
    {{"ticker": "TICK2", "layer": "AI Cloud"}}
  ],
  "rationale": "one sentence on why these fit the theme"
}}"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            raw = data.get("tickers", [])
            # Support both old list-of-strings and new list-of-dicts format
            if raw and isinstance(raw[0], dict):
                tickers = [item["ticker"] for item in raw]
                layers  = {item["ticker"]: item.get("layer", "") for item in raw}
            else:
                tickers = raw
                layers  = {}
            return tickers, layers, data.get("rationale", "")
    except Exception:
        pass
    # Fallback: extract tickers from raw text, no layer info
    return _extract_tickers_from_text(text)[:n], {}, ""


# ── Tooltip generation ────────────────────────────────────────────────────────

def generate_tooltips(top_rows, analyses, client):
    """One Claude call to generate a 1-2 sentence AI thesis per ticker.
    Returns {ticker: "thesis string"}."""
    if not top_rows:
        return {}

    summaries = []
    for r in top_rows:
        t = r["ticker"]
        d = analyses.get(t, {})
        f = d.get("fundamentals") or {}
        growth = f.get("growth") or {}
        val    = f.get("valuation") or {}
        analyst= f.get("analyst_estimates") or {}
        company = r.get('company', '')[:24]
        summaries.append(
            f"{t} ({company}): "
            f"sector={f.get('sector','?')}, industry={f.get('industry','?')}, "
            f"rev_growth={growth.get('revenue_growth_yoy_pct','?')}%, "
            f"fwd_pe={val.get('forward_pe','?')}x, "
            f"analyst={analyst.get('recommendation','?')} target={analyst.get('mean_target','?')}"
        )

    prompt = (
        "You are a concise equity analyst. For each stock below, write exactly ONE sentence "
        "(max 20 words) explaining its specific AI angle — what it does in the AI stack and why it matters. "
        "Be specific, not generic. No disclaimers.\n\n"
        + "\n".join(summaries)
        + "\n\nReturn ONLY a JSON object: {\"TICK1\": \"sentence\", \"TICK2\": \"sentence\", ...}"
    )

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        text = resp.content[0].text.strip()
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return {}


# ── Step B: Web search expansion ──────────────────────────────────────────────

def web_search_expansion(theme, client, max_results=5):
    """Search web for theme, extract additional tickers via Claude."""
    if not DDGS:
        _status("DuckDuckGo not available — skipping web search.")
        return [], ""

    _status(f"Searching web for: '{theme}'...")
    year = datetime.today().strftime("%Y")
    query = f"best stocks {theme} {year} ticker symbol"

    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        if not results:
            return [], ""

        snippets = "\n".join(
            f"[{r.get('title','')}] {r.get('body','')[:200]}"
            for r in results
        )
    except Exception as e:
        _status(f"Web search failed: {e}")
        return [], ""

    # Claude extracts tickers from search results
    _status("Claude extracting tickers from search results...")
    prompt = f"""From these web search results about "{theme}", extract any stock ticker symbols mentioned.

Search results:
{snippets}

Return ONLY a JSON object:
{{
  "tickers": ["TICK1", "TICK2", ...],
  "sources": "brief note on what sources mentioned"
}}

Only include real, actively traded US tickers. Max 8."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    try:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("tickers", []), data.get("sources", "")
    except Exception:
        pass
    return _extract_tickers_from_text(text)[:8], ""


# ── Step C: Merge + validate ───────────────────────────────────────────────────

def merge_tickers(claude_tickers, web_tickers, manual_tickers):
    """Merge and deduplicate all ticker sources."""
    seen = {}
    for t in (claude_tickers + web_tickers + manual_tickers):
        t = t.upper().strip()
        if t and t not in seen:
            source = []
            if t in [x.upper() for x in claude_tickers]: source.append("Claude")
            if t in [x.upper() for x in web_tickers]:    source.append("Web")
            if t in [x.upper() for x in manual_tickers]: source.append("Manual")
            seen[t] = "+".join(source)
    return seen  # {ticker: source_label}


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_ticker(ticker):
    """Run analyze.py --json on a ticker."""
    try:
        r = subprocess.run(
            [PYTHON, ANALYZE_PY, ticker, "--json"],
            capture_output=True, text=True, timeout=30
        )
        output = r.stdout.strip()
        if not output:
            return {"error": "No output", "ticker": ticker}
        return json.loads(output)
    except subprocess.TimeoutExpired:
        return {"error": "Timeout", "ticker": ticker}
    except Exception as e:
        return {"error": str(e), "ticker": ticker}


def score_ticker(data):
    """Reuse portfolio.py score_stock."""
    from portfolio import score_stock
    if "error" in data:
        return 0, "ERR", []
    return score_stock(data)


def fmt_price(v):
    return f"${v:,.2f}" if v is not None else "N/A"

def fmt_pct(v):
    if v is None: return "N/A"
    return f"+{v:.1f}%" if v > 0 else f"{v:.1f}%"

def fmt_large(v):
    if v is None: return "N/A"
    if abs(v) >= 1e12: return f"${v/1e12:.2f}T"
    if abs(v) >= 1e9:  return f"${v/1e9:.2f}B"
    if abs(v) >= 1e6:  return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"


# ── Display ───────────────────────────────────────────────────────────────────

SIGNAL_STYLE = {
    "STRONG BUY": "bold bright_green",
    "BUY":        "green",
    "HOLD":       "yellow",
    "WATCH":      "cyan",
    "AVOID":      "red",
    "ERR":        "dim red",
}

def print_results(theme, results, sources):
    if not RICH:
        print(f"\nScreener results for: {theme}\n{'─'*60}")
        for r in results:
            t = r["ticker"]
            print(f"  {r['rank']}. {t} ({r['signal']}, {r['score']}/100) "
                  f"${r['price']:.2f} | {fmt_pct(r.get('upside_raw'))} upside | {sources.get(t,'')}")
        return

    CONSOLE.print()
    CONSOLE.rule(f"[bold cyan]Screener Results — {theme}[/bold cyan]")
    CONSOLE.print()

    tbl = Table(box=box.ROUNDED, header_style="bold white",
                border_style="bright_black", show_lines=False)
    tbl.add_column("#",        width=3,  justify="right")
    tbl.add_column("Ticker",   width=7,  style="bold white")
    tbl.add_column("Company",  width=22)
    tbl.add_column("Price",    width=9,  justify="right")
    tbl.add_column("Mkt Cap",  width=10, justify="right")
    tbl.add_column("Rev Gr%",  width=9,  justify="right")
    tbl.add_column("Fwd P/E",  width=9,  justify="right")
    tbl.add_column("Upside",   width=9,  justify="right")
    tbl.add_column("Signal",   width=12, justify="center")
    tbl.add_column("Score",    width=8,  justify="center")
    tbl.add_column("Source",   width=14, style="dim")

    for r in results:
        sig_style = SIGNAL_STYLE.get(r["signal"], "white")
        up_style  = "green" if r.get("upside_raw", 0) > 0 else "red"
        sc        = r["score"]
        sc_style  = "bold bright_green" if sc >= 70 else ("yellow" if sc >= 55 else "red")

        tbl.add_row(
            str(r["rank"]),
            r["ticker"],
            r["company"][:20],
            fmt_price(r["price"]),
            fmt_large(r["mktcap"]),
            fmt_pct(r["rev_growth"]),
            f"{r['fwd_pe']:.1f}x" if r.get("fwd_pe") else "N/A",
            Text(fmt_pct(r.get("upside_raw")), style=up_style),
            Text(r["signal"], style=sig_style),
            Text(f"{sc}/100", style=sc_style),
            sources.get(r["ticker"], ""),
        )
    CONSOLE.print(tbl)

    # Top picks detail
    top = [r for r in results if r["score"] >= 55][:3]
    if top:
        CONSOLE.print()
        CONSOLE.rule("[dim]Top Picks[/dim]")
        CONSOLE.print()
        from rich.columns import Columns
        cards = []
        for r in top:
            sig_style = SIGNAL_STYLE.get(r["signal"], "white").split()[-1]
            up_style  = "green" if r.get("upside_raw", 0) > 0 else "red"
            pe_str  = f"{r['fwd_pe']:.1f}x" if r.get("fwd_pe") else "N/A"
            src_str = sources.get(r["ticker"], "")
            body = (f"[white]{fmt_price(r['price'])}[/white]  [{up_style}]{fmt_pct(r.get('upside_raw'))} upside[/{up_style}]\n"
                    f"Entry: [cyan]{r.get('entry','N/A')}[/cyan]\n"
                    f"Stop:  [red]{r.get('stop','N/A')}[/red]\n"
                    f"[dim]Rev growth: {fmt_pct(r['rev_growth'])}  Fwd P/E: {pe_str}[/dim]\n"
                    f"[dim]Source: {src_str}[/dim]")
            cards.append(Panel(body, title=f"[bold]{r['ticker']}[/bold]  [dim]{r['company'][:18]}[/dim]",
                               subtitle=f"[{sig_style}]{r['signal']}[/{sig_style}] · [dim]{r['score']}/100[/dim]",
                               width=34, border_style=sig_style))
        CONSOLE.print(Columns(cards, equal=False))

    CONSOLE.print()
    CONSOLE.print("[dim]Use --watch to add any ticker to your watchlist:[/dim]")
    CONSOLE.print(f"[dim]  python scripts/portfolio.py --watch TICKER[/dim]\n")


# ── Core screener ─────────────────────────────────────────────────────────────

def run_screener(theme, manual_tickers=None, top_n=6, verbose=True):
    """
    Full screening pipeline. Returns list of result dicts.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set."); return []

    client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    manual = [t.strip().upper() for t in (manual_tickers or []) if t.strip()]

    if verbose and RICH:
        CONSOLE.print()
        CONSOLE.rule(f"[bold yellow]Agent 2 — Stock Screener[/bold yellow]")
        CONSOLE.print(f"  Theme: [bold]{theme}[/bold]")
        if manual:
            CONSOLE.print(f"  Manual tickers: [bold]{', '.join(manual)}[/bold]")
        CONSOLE.print()

    # Step A: Claude candidates
    claude_tickers, ai_layers, claude_rationale = claude_initial_candidates(theme, client)
    if verbose and claude_rationale:
        _status(f"Claude: {claude_rationale}")
        _status(f"Claude suggested: {', '.join(claude_tickers)}")

    # Step B: Web search expansion
    web_tickers, web_sources = web_search_expansion(theme, client)
    if verbose and web_tickers:
        _status(f"Web found: {', '.join(web_tickers)}")

    # Step C: Merge
    all_sources = merge_tickers(claude_tickers, web_tickers, manual)
    all_tickers = list(all_sources.keys())
    if verbose:
        _status(f"Total unique tickers to analyse: {len(all_tickers)} — {', '.join(all_tickers)}")
        CONSOLE.print() if RICH else print()

    # Analyze each
    analyses = {}
    for t in all_tickers:
        if verbose: _status(f"  Analyzing {t}...")
        analyses[t] = analyze_ticker(t)

    # Score and build result rows
    rows = []
    for t, data in analyses.items():
        score, signal, reasons = score_ticker(data)
        if "error" in data:
            rows.append({
                "ticker": t, "company": t, "rank": 99,
                "price": None, "mktcap": None, "rev_growth": None,
                "fwd_pe": None, "upside_raw": None,
                "signal": "ERR", "score": 0,
                "entry": "N/A", "stop": "N/A",
                "reasons": [],
            })
            continue

        price   = data.get("current_price")
        f       = data.get("fundamentals") or {}
        val     = f.get("valuation") or {}
        growth  = f.get("growth") or {}
        analyst = f.get("analyst_estimates") or {}
        sl      = data.get("suggested_levels") or {}
        supports    = sl.get("strong_support") or []
        stop        = sl.get("stop_loss_long")
        mktcap      = f.get("market_cap")
        rev_growth  = growth.get("revenue_growth_yoy_pct")
        fwd_pe      = val.get("forward_pe")
        upside      = analyst.get("upside_to_mean_target_pct")
        entry       = (f"{fmt_price(supports[-1])}–{fmt_price(supports[0])}"
                       if len(supports) >= 2 else fmt_price(price))

        rows.append({
            "ticker":     t,
            "company":    data.get("company_name", t),
            "rank":       0,
            "price":      price,
            "mktcap":     mktcap,
            "rev_growth": rev_growth,
            "fwd_pe":     fwd_pe,
            "upside_raw": upside,
            "signal":     signal,
            "score":      score or 0,
            "entry":      entry,
            "stop":       fmt_price(stop),
            "reasons":    reasons,
            "ai_layer":   ai_layers.get(t, ""),
        })

    # Apply price filter if theme contains a price constraint
    price_filter = parse_price_filter(theme)
    if price_filter:
        before = len(rows)
        rows = apply_price_filter(rows, price_filter)
        dropped = before - len(rows)
        if verbose and dropped:
            op, val = price_filter
            _status(f"Price filter (${val:.0f} {'max' if op == '<' else 'min'}): dropped {dropped} tickers outside range")

    # Sort by score, assign rank
    rows.sort(key=lambda x: x["score"], reverse=True)
    for i, r in enumerate(rows[:top_n], 1):
        r["rank"] = i
    rows = rows[:top_n]

    # Generate one-sentence AI thesis per top ticker
    if verbose:
        _status("Generating AI thesis tooltips...")
    tooltips = generate_tooltips(rows, analyses, client)
    for r in rows:
        r["tooltip"] = tooltips.get(r["ticker"], "")

    if verbose:
        print_results(theme, rows, all_sources)

    return rows, all_sources


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Agent 2 — Stock Screener")
    parser.add_argument("theme",       nargs="?", default=None,
                        help="Investment theme (e.g. 'AI inference plays under $50')")
    parser.add_argument("--tickers",   default="",
                        help="Comma-separated manual tickers to include (e.g. ANET,PLTR)")
    parser.add_argument("--top",       type=int, default=6,
                        help="Number of top results to show (default 6)")
    parser.add_argument("--watch",     action="store_true",
                        help="Add top result to watchlist automatically")
    parser.add_argument("--interactive", action="store_true",
                        help="Interactive mode — prompts for theme and tickers")
    args = parser.parse_args()

    theme   = args.theme
    manual  = [t.strip() for t in args.tickers.split(",") if t.strip()]

    if args.interactive or not theme:
        if RICH:
            CONSOLE.print("\n[bold cyan]Agent 2 — Stock Screener[/bold cyan]  (interactive)\n")
        theme  = input("  Enter investment theme: ").strip()
        extra  = input("  Additional tickers to include (comma-separated, or Enter to skip): ").strip()
        if extra:
            manual += [t.strip().upper() for t in extra.split(",") if t.strip()]

    if not theme:
        print("No theme provided."); sys.exit(1)

    results, sources = run_screener(theme, manual_tickers=manual, top_n=args.top)

    if args.watch and results:
        top = results[0]
        t   = top["ticker"]
        subprocess.run(
            [PYTHON, os.path.join(SCRIPTS_DIR, "portfolio.py"), "--watch", t],
            capture_output=True
        )
        print(f"\n  Added {t} to watchlist.")


if __name__ == "__main__":
    main()
