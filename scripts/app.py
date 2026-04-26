"""
AI Portfolio Terminal — Streamlit App
Run: streamlit run scripts/app.py
"""

import sys
import os
import json
import subprocess
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPTS_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR     = os.path.dirname(SCRIPTS_DIR)
PORTFOLIO_PY = os.path.join(SCRIPTS_DIR, "portfolio.py")
ANALYZE_PY   = os.path.join(SCRIPTS_DIR, "analyze.py")
BRIEF_PY     = os.path.join(SCRIPTS_DIR, "agents", "daily_brief.py")
PYTHON       = "/opt/homebrew/bin/python3.9"

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

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AI Portfolio Terminal",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stTabs [data-baseweb="tab-list"] { gap: 8px; }
  .stTabs [data-baseweb="tab"] {
      padding: 6px 20px;
      font-weight: 600;
      border-radius: 6px 6px 0 0;
  }
  .metric-card {
      background: #1e1e2e;
      border-radius: 10px;
      padding: 16px 20px;
      border-left: 4px solid #4c9be8;
  }
  .alert-box {
      background: #2d1515;
      border-left: 4px solid #ff4b4b;
      border-radius: 6px;
      padding: 12px 16px;
      margin: 6px 0;
  }
  .signal-green  { color: #00c853; font-weight: 700; }
  .signal-yellow { color: #ffd600; font-weight: 700; }
  .signal-red    { color: #ff5252; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

# ── Data loading ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_data():
    result = subprocess.run(
        [PYTHON, PORTFOLIO_PY, "--json"],
        capture_output=True, text=True, timeout=300
    )
    if not result.stdout.strip():
        raise RuntimeError(result.stderr[:300])
    return json.loads(result.stdout)

# ── Helpers ───────────────────────────────────────────────────────────────────
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

SIGNAL_COLOR = {
    "STRONG BUY": "#00c853",
    "BUY":        "#69f0ae",
    "HOLD":       "#ffd600",
    "HOLD WITH CAUTION": "#ff9800",
    "WATCH":      "#40c4ff",
    "CONSIDER CUTTING": "#ff5252",
    "AVOID":      "#ff5252",
    "DANGER":     "#ff1744",
}

ACTION_COLOR = {
    "ADD":    "#00c853",
    "HOLD":   "#ffd600",
    "TRIM":   "#ff9800",
    "WATCH":  "#40c4ff",
    "DANGER": "#ff1744",
}

def signal_badge(signal):
    color = SIGNAL_COLOR.get(signal, "#888")
    return f'<span style="color:{color};font-weight:700">{signal}</span>'

def pnl_color(v):
    if v is None: return "#888"
    return "#00c853" if v >= 0 else "#ff5252"

def score_color(s):
    if s is None: return "#888"
    if s >= 70: return "#00c853"
    if s >= 55: return "#ffd600"

def _stream_chat(client, system_prompt, messages):
    """Generator for streaming Claude chat responses."""
    import anthropic as _ac
    with client.messages.stream(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system_prompt,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text
    return "#ff5252"

# ── Claude brief generator ────────────────────────────────────────────────────
def stream_brief(holdings_only=True):
    """Generator that yields Claude brief text for st.write_stream."""
    try:
        import anthropic
        from agents.daily_brief import (
            fetch_portfolio_data, search_news,
            build_holding_context, build_watchlist_context,
            BRIEF_SYSTEM_PROMPT, BRIEF_USER_TEMPLATE
        )
        from portfolio import score_stock

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            yield "⚠️ ANTHROPIC_API_KEY not set. Check your .env file."
            return

        pf_data  = fetch_portfolio_data()
        portfolio = pf_data["portfolio"]
        analyses  = pf_data["analyses"]
        holdings  = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
        watchlist = portfolio.get("watchlist", [])

        total_cost = total_value = 0
        for h in holdings:
            data = analyses.get(h["ticker"], {})
            price = data.get("current_price") if "error" not in data else None
            if price:
                total_cost  += h["shares"] * h["avg_cost"]
                total_value += h["shares"] * price
        total_pnl_abs = total_value - total_cost
        total_pnl_pct = (total_pnl_abs / total_cost * 100) if total_cost else 0

        holdings_context = ""
        for h in holdings:
            t = h["ticker"]; data = analyses.get(t, {})
            company = data.get("company_name", t) if "error" not in data else t
            news = search_news(t, company)
            holdings_context += build_holding_context(h, data, news) + "\n"

        watchlist_context = "Watchlist skipped."
        if not holdings_only and watchlist:
            scored = []
            for t in watchlist:
                data = analyses.get(t, {})
                if "error" not in data:
                    score, _, _ = score_stock(data)
                    scored.append((score or 0, t, data))
            scored.sort(reverse=True)
            for _, t, data in scored[:3]:
                company = data.get("company_name", t)
                news = search_news(t, company)
                watchlist_context += build_watchlist_context(t, data, news) + "\n"

        prompt = BRIEF_USER_TEMPLATE.format(
            date=datetime.today().strftime("%Y-%m-%d"),
            num_holdings=len(holdings),
            total_pnl_pct=total_pnl_pct,
            total_pnl_abs=total_pnl_abs,
            holdings_context=holdings_context.strip(),
            watchlist_context=watchlist_context.strip(),
        )

        client = anthropic.Anthropic(api_key=api_key)
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=BRIEF_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        ) as stream:
            for text in stream.text_stream:
                yield text

    except Exception as e:
        yield f"\n⚠️ Brief generation failed: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════════════════════════════════

# Header
col_title, col_time, col_refresh = st.columns([5, 2, 1])
with col_title:
    st.markdown("## 📈 AI Portfolio Terminal")
with col_time:
    st.markdown(f"<br><span style='color:#888'>{datetime.today().strftime('%A, %d %b %Y')}</span>",
                unsafe_allow_html=True)
with col_refresh:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("⟳ Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

st.divider()

# Load data
with st.spinner("Fetching live market data..."):
    try:
        data = load_data()
    except Exception as e:
        st.error(f"Failed to load portfolio data: {e}")
        st.stop()

portfolio  = data["portfolio"]
analyses   = data["analyses"]
holdings   = [h for h in portfolio["holdings"] if h.get("shares", 0) > 0]
watchlist  = portfolio.get("watchlist", [])

# Tabs
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊  Portfolio",
    "👁  Watchlist",
    "⚡  Trade Monitor",
    "📅  Weekly Review",
    "💬  Portfolio Chat",
])


# ── TAB 1: PORTFOLIO ──────────────────────────────────────────────────────────
with tab1:

    # ── Top metrics row ───────────────────────────────────────────────────────
    total_cost = total_value = 0
    for h in holdings:
        t = h["ticker"]; d = analyses.get(t, {})
        price = d.get("current_price") if "error" not in d else None
        if price:
            total_cost  += h["shares"] * h["avg_cost"]
            total_value += h["shares"] * price

    total_gain = total_value - total_cost
    total_pct  = (total_gain / total_cost * 100) if total_cost else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Portfolio Value",  fmt_large(total_value))
    m2.metric("Total Cost",       fmt_large(total_cost))
    m3.metric("Unrealized P&L",   fmt_large(total_gain),  f"{fmt_pct(total_pct)}")
    m4.metric("Positions",        len(holdings))

    st.markdown("")

    # ── Holdings table + Allocation chart ─────────────────────────────────────
    left, right = st.columns([3, 2])

    with left:
        st.markdown("#### Holdings")

        rows = []
        for h in holdings:
            t = h["ticker"]; d = analyses.get(t, {})
            if "error" in d:
                rows.append({"Ticker": t, "Price": "ERR", "Shares": h["shares"],
                             "Avg Cost": fmt_price(h["avg_cost"]),
                             "Mkt Value": "N/A", "Gain $": "N/A", "Gain %": "N/A",
                             "Signal": "ERR", "Score": "N/A"})
                continue
            price = d.get("current_price")
            score, signal, _ = __import__("portfolio").score_stock(d)
            pnl_abs = (price - h["avg_cost"]) * h["shares"] if price else None
            pnl_pct = ((price - h["avg_cost"]) / h["avg_cost"] * 100) if price and h["avg_cost"] else None
            rows.append({
                "Ticker":    t,
                "Price":     fmt_price(price),
                "Shares":    h["shares"],
                "Avg Cost":  fmt_price(h["avg_cost"]),
                "Mkt Value": fmt_large(price * h["shares"]) if price else "N/A",
                "Gain $":    fmt_large(pnl_abs),
                "Gain %":    fmt_pct(pnl_pct),
                "Signal":    signal,
                "Score":     f"{score}/100" if score else "N/A",
                "_gain_pct": pnl_pct or 0,
                "_score":    score or 0,
            })

        df = pd.DataFrame(rows)

        def color_gain(val):
            try:
                v = float(val.replace("%","").replace("+",""))
                return f"color: {'#00c853' if v >= 0 else '#ff5252'}"
            except: return ""

        def color_signal(val):
            return f"color: {SIGNAL_COLOR.get(val, '#888')}; font-weight: 700"

        def color_score(val):
            try:
                s = int(val.split("/")[0])
                return f"color: {score_color(s)}; font-weight: 700"
            except: return ""

        display_df = df.drop(columns=["_gain_pct", "_score"], errors="ignore")
        styled = (display_df.style
                  .applymap(color_gain,   subset=["Gain %"])
                  .applymap(color_gain,   subset=["Gain $"])
                  .applymap(color_signal, subset=["Signal"])
                  .applymap(color_score,  subset=["Score"]))
        st.dataframe(styled, use_container_width=True, hide_index=True, height=280)

    with right:
        st.markdown("#### Allocation")
        alloc_data = []
        for h in holdings:
            t = h["ticker"]; d = analyses.get(t, {})
            price = d.get("current_price") if "error" not in d else None
            if price:
                alloc_data.append({"ticker": t, "value": price * h["shares"]})

        if alloc_data:
            alloc_df = pd.DataFrame(alloc_data)
            fig = px.pie(alloc_df, values="value", names="ticker",
                         hole=0.5,
                         color_discrete_sequence=px.colors.qualitative.Set2)
            fig.update_layout(
                margin=dict(t=10, b=10, l=10, r=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                legend=dict(font=dict(color="white")),
                showlegend=True,
                height=260,
            )
            fig.update_traces(textinfo="label+percent", textfont_color="white")
            st.plotly_chart(fig, use_container_width=True)

    # ── P&L bar chart ─────────────────────────────────────────────────────────
    st.markdown("#### P&L per Position")
    pnl_rows = [r for r in rows if r.get("_gain_pct") is not None and r["_gain_pct"] != 0]
    if pnl_rows:
        pnl_df = pd.DataFrame([{"Ticker": r["Ticker"], "P&L %": r["_gain_pct"]} for r in rows])
        colors = ["#00c853" if v >= 0 else "#ff5252" for v in pnl_df["P&L %"]]
        fig2 = go.Figure(go.Bar(
            x=pnl_df["Ticker"], y=pnl_df["P&L %"],
            marker_color=colors,
            text=[fmt_pct(v) for v in pnl_df["P&L %"]],
            textposition="outside",
        ))
        fig2.update_layout(
            margin=dict(t=20, b=20, l=10, r=10),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(gridcolor="#333", color="white", ticksuffix="%"),
            xaxis=dict(color="white"),
            height=220,
            showlegend=False,
        )
        fig2.add_hline(y=0, line_color="#555")
        st.plotly_chart(fig2, use_container_width=True)

    # ── Daily Brief ───────────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🤖 Daily Market Brief  *(Agent 1)*")

    bcol1, bcol2 = st.columns([1, 4])
    with bcol1:
        run_brief   = st.button("▶ Generate Brief", use_container_width=True)
        full_brief  = st.toggle("Include watchlist", value=False)
    with bcol2:
        st.caption("Fetches live news per holding then asks Claude for a pre-market analysis. ~30–60s.")

    if run_brief:
        with st.container(border=True):
            st.write_stream(stream_brief(holdings_only=not full_brief))


# ── TAB 2: WATCHLIST ──────────────────────────────────────────────────────────
with tab2:
    st.markdown("#### Watchlist — Ranked Opportunities")

    sys.path.insert(0, SCRIPTS_DIR)
    from portfolio import score_stock

    scored_watch = []
    for t in watchlist:
        d = analyses.get(t, {})
        if "error" in d:
            scored_watch.append({"Ticker": t, "Price": "ERR", "Mkt Cap": "N/A",
                                 "Rev Gr%": "N/A", "Fwd P/E": "N/A", "PEG": "N/A",
                                 "Upside": "N/A", "Signal": "ERR", "Score": "N/A",
                                 "_score": -1, "_upside": 0})
            continue
        price  = d.get("current_price")
        score, signal, _ = score_stock(d)
        f      = d.get("fundamentals") or {}
        val    = f.get("valuation") or {}
        growth = f.get("growth") or {}
        analyst= f.get("analyst_estimates") or {}
        upside = analyst.get("upside_to_mean_target_pct")
        scored_watch.append({
            "Ticker":  t,
            "Price":   fmt_price(price),
            "Mkt Cap": fmt_large(f.get("market_cap")),
            "Rev Gr%": fmt_pct(growth.get("revenue_growth_yoy_pct")),
            "Fwd P/E": f"{val.get('forward_pe'):.1f}x" if val.get("forward_pe") else "N/A",
            "PEG":     f"{val.get('peg_ratio'):.2f}" if val.get("peg_ratio") else "N/A",
            "Upside":  fmt_pct(upside),
            "Signal":  signal,
            "Score":   f"{score}/100" if score else "N/A",
            "_score":  score or 0,
            "_upside": upside or 0,
        })

    scored_watch.sort(key=lambda x: x["_score"], reverse=True)
    wdf = pd.DataFrame(scored_watch).drop(columns=["_score", "_upside"])

    styled_w = (wdf.style
                .applymap(color_signal, subset=["Signal"])
                .applymap(color_score,  subset=["Score"])
                .applymap(lambda v: f"color: {'#00c853' if '+' in str(v) else '#ff5252'}",
                          subset=["Upside"]))
    st.dataframe(styled_w, use_container_width=True, hide_index=True)

    # ── Entry zone cards ──────────────────────────────────────────────────────
    st.markdown("#### Top Entry Opportunities")
    top_watch = [s for s in scored_watch if s.get("_score", -1) >= 55][:6]

    if not top_watch:
        st.info("No strong entry opportunities right now.")
    else:
        cols = st.columns(3)
        for i, item in enumerate(top_watch):
            t  = item["Ticker"]
            d  = analyses.get(t, {})
            sl = d.get("suggested_levels") or {}
            f  = d.get("fundamentals") or {}
            analyst = f.get("analyst_estimates") or {}
            supports    = sl.get("strong_support") or []
            stop        = sl.get("stop_loss_long")
            target      = analyst.get("mean_target")
            upside      = analyst.get("upside_to_mean_target_pct")
            price       = d.get("current_price")
            entry       = f"{fmt_price(supports[-1])} – {fmt_price(supports[0])}" if len(supports) >= 2 else fmt_price(price)
            signal      = item["Signal"]
            score_val   = item["Score"]
            sig_color   = SIGNAL_COLOR.get(signal, "#888")

            with cols[i % 3]:
                st.markdown(f"""
                <div style="background:#1e1e2e;border-radius:10px;padding:16px;
                            border-left:4px solid {sig_color};margin-bottom:12px">
                  <div style="font-size:1.2em;font-weight:700;color:white">{t}</div>
                  <div style="color:{sig_color};font-size:0.85em;font-weight:600">{signal} · {score_val}</div>
                  <hr style="border-color:#333;margin:8px 0">
                  <div style="color:#aaa;font-size:0.85em">Price</div>
                  <div style="color:white;font-weight:600">{fmt_price(price)}</div>
                  <div style="color:#aaa;font-size:0.85em;margin-top:6px">Entry Zone</div>
                  <div style="color:#40c4ff">{entry}</div>
                  <div style="color:#aaa;font-size:0.85em;margin-top:6px">Stop / Target</div>
                  <div><span style="color:#ff5252">{fmt_price(stop)}</span>
                       &nbsp;→&nbsp;
                       <span style="color:#00c853">{fmt_price(target)} ({fmt_pct(upside)})</span>
                  </div>
                </div>
                """, unsafe_allow_html=True)

    st.divider()
    st.markdown("#### 🤖 Stock Screener  *(Agent 2)*")

    with st.container(border=True):
        sc1, sc2 = st.columns([3, 1])
        theme_input  = sc1.text_input(
            "Investment theme",
            placeholder="e.g. AI inference plays under $50  |  AI networking stocks  |  AI datacenter REITs"
        )
        top_n        = sc2.number_input("Top N", min_value=3, max_value=10, value=6)
        manual_input = st.text_input(
            "Additional tickers to include (optional)",
            placeholder="e.g. PLTR, SMCI, ANET  — comma separated"
        )
        btn1, btn2   = st.columns([1, 4])
        run_screener_btn = btn1.button("▶ Run Screener", use_container_width=True)
        btn2.caption("Claude suggests candidates → web search validates → analyze.py scores each. ~60s.")

    if run_screener_btn and theme_input:
        manual_tickers = [t.strip().upper() for t in manual_input.split(",") if t.strip()]

        def stream_screener():
            sys.path.insert(0, SCRIPTS_DIR)
            from agents.screener import (
                claude_initial_candidates, web_search_expansion,
                merge_tickers, analyze_ticker, score_ticker,
                generate_tooltips, parse_price_filter, apply_price_filter,
                fmt_price, fmt_pct, fmt_large
            )
            import anthropic as _anthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                yield "⚠️ ANTHROPIC_API_KEY not set."; return

            client = _anthropic.Anthropic(api_key=api_key, timeout=60.0)

            yield f"**Theme:** {theme_input}\n\n"

            yield "🔍 **Step A** — Claude generating initial candidates...\n"
            claude_tickers, ai_layers, rationale = claude_initial_candidates(theme_input, client)
            yield f"Claude suggested: `{', '.join(claude_tickers)}`\n"
            if rationale:
                yield f"_{rationale}_\n\n"

            yield "🌐 **Step B** — Searching web for current picks...\n"
            web_tickers, web_src = web_search_expansion(theme_input, client)
            if web_tickers:
                yield f"Web found: `{', '.join(web_tickers)}`\n\n"
            else:
                yield "Web search returned no additional tickers.\n\n"

            all_sources = merge_tickers(claude_tickers, web_tickers, manual_tickers)
            all_tickers = list(all_sources.keys())
            yield f"📊 **Step C** — Analysing {len(all_tickers)} tickers: `{', '.join(all_tickers)}`\n\n"

            analyses = {}
            for t in all_tickers:
                yield f"  Analyzing `{t}`...\n"
                analyses[t] = analyze_ticker(t)

            # Build + sort rows
            rows = []
            for t, d in analyses.items():
                score, signal, reasons = score_ticker(d)
                if "error" in d:
                    continue
                price   = d.get("current_price")
                f       = d.get("fundamentals") or {}
                val     = f.get("valuation") or {}
                growth  = f.get("growth") or {}
                analyst = f.get("analyst_estimates") or {}
                sl      = d.get("suggested_levels") or {}
                supports= sl.get("strong_support") or []
                stop    = sl.get("stop_loss_long")
                entry   = (f"{fmt_price(supports[-1])}–{fmt_price(supports[0])}"
                           if len(supports) >= 2 else fmt_price(price))
                rows.append({
                    "Ticker":    t,
                    "Company":   (d.get("company_name") or t)[:20],
                    "Price":     fmt_price(price),
                    "Mkt Cap":   fmt_large(f.get("market_cap")),
                    "Rev Gr%":   fmt_pct(growth.get("revenue_growth_yoy_pct")),
                    "Fwd P/E":   f"{val.get('forward_pe'):.1f}x" if val.get("forward_pe") else "N/A",
                    "Upside":    fmt_pct(analyst.get("upside_to_mean_target_pct")),
                    "Signal":    signal,
                    "Score":     f"{score}/100" if score else "N/A",
                    "Entry":     entry,
                    "Stop":      fmt_price(stop),
                    "Source":    all_sources.get(t, ""),
                    "_score":    score or 0,
                    "_upside":   analyst.get("upside_to_mean_target_pct") or 0,
                    "_reasons":  reasons[:2] if reasons else [],
                    "_ai_layer": ai_layers.get(t, ""),
                    "_tooltip":  "",
                })
            # Apply price filter if theme contains a price constraint
            price_filter = parse_price_filter(theme_input)
            if price_filter:
                before = len(rows)
                rows = apply_price_filter(
                    [{**r, "price": float(r["Price"].replace("$","").replace(",","")) if r["Price"] != "N/A" else None}
                     for r in rows],
                    price_filter
                )
                # Restore original row format (apply_price_filter works on price key)
                dropped = before - len(rows)
                if dropped:
                    op, val = price_filter
                    yield f"\n⚙️ Price filter (${val:.0f} {'max' if op == '<' else 'min'}): removed {dropped} tickers outside range.\n"

            rows.sort(key=lambda x: x["_score"], reverse=True)
            rows = rows[:top_n]

            # Generate one-sentence AI thesis per top ticker
            yield "\n🧠 **Generating AI thesis...**\n"
            tooltips = generate_tooltips(
                [{"ticker": r["Ticker"], "company": r["Company"]} for r in rows],
                analyses, client
            )
            for r in rows:
                r["_tooltip"] = tooltips.get(r["Ticker"], "")

            # Store results in session state for the table below
            st.session_state["screener_results"] = rows
            st.session_state["screener_theme"]   = theme_input

            yield f"\n✅ **Done.** {len(rows)} tickers analysed. Top {min(top_n, len(rows))} shown below.\n"

        with st.container(border=True):
            st.write_stream(stream_screener())

    # ── Screener results table ────────────────────────────────────────────────
    if "screener_results" in st.session_state and st.session_state["screener_results"]:
        st.markdown(f"##### Results — *{st.session_state.get('screener_theme','')}*")
        rows = st.session_state["screener_results"]
        display_rows = [{k: v for k, v in r.items()
                         if not k.startswith("_") and k not in ("Entry","Stop")}
                        for r in rows]
        sdf = pd.DataFrame(display_rows)

        def _cg(val):
            try:
                v = float(str(val).replace("%","").replace("+",""))
                return f"color: {'#00c853' if v >= 0 else '#ff5252'}"
            except: return ""
        def _cs(val):
            return f"color: {SIGNAL_COLOR.get(val,'#888')}; font-weight:700"
        def _ck(val):
            try:
                s = int(str(val).split("/")[0])
                return f"color: {'#00c853' if s>=70 else ('#ffd600' if s>=55 else '#ff5252')}; font-weight:700"
            except: return ""

        styled_s = (sdf.style
                    .applymap(_cg, subset=["Rev Gr%","Upside"])
                    .applymap(_cs, subset=["Signal"])
                    .applymap(_ck, subset=["Score"]))
        st.dataframe(styled_s, use_container_width=True, hide_index=True)

        # Entry/stop + watchlist buttons
        st.markdown("##### Entry Zones & Actions")
        cols3 = st.columns(3)
        for i, r in enumerate(rows[:6]):
            with cols3[i % 3]:
                sig_color = SIGNAL_COLOR.get(r["Signal"], "#888")
                layer   = r.get("_ai_layer", "")
                tooltip = r.get("_tooltip", "")
                layer_html = (
                    f"<span style='background:#1a3a4a;color:#40c4ff;font-size:0.72em;"
                    f"border-radius:4px;padding:1px 6px;margin-left:6px'>{layer}</span>"
                    if layer else ""
                )
                tooltip_html = (
                    f"<div class='card-tip'>{tooltip}</div>"
                    if tooltip else ""
                )
                st.markdown(
                    f"<style>"
                    f".stock-card{{position:relative;}}"
                    f".card-tip{{display:none;position:absolute;z-index:999;top:0;left:105%;"
                    f"width:220px;background:#1a1a2e;border:1px solid #40c4ff;border-radius:8px;"
                    f"padding:10px;font-size:0.8em;color:#ccc;line-height:1.4;pointer-events:none;}}"
                    f".stock-card:hover .card-tip{{display:block;}}"
                    f"</style>"
                    f"<div class='stock-card' style='background:#1e1e2e;border-radius:8px;padding:12px;"
                    f"border-left:3px solid {sig_color};margin-bottom:10px'>"
                    f"<b style='color:white'>{r['Ticker']}</b> "
                    f"<span style='color:{sig_color};font-size:0.8em'>{r['Signal']}</span>"
                    f"{layer_html}<br>"
                    f"<span style='color:#aaa;font-size:0.82em'>Entry: <span style='color:#40c4ff'>{r['Entry']}</span>"
                    f"  Stop: <span style='color:#ff5252'>{r['Stop']}</span></span>"
                    f"{tooltip_html}"
                    f"</div>",
                    unsafe_allow_html=True
                )
                if st.button(f"+ Watch {r['Ticker']}", key=f"watch_{r['Ticker']}_{i}",
                             use_container_width=True):
                    subprocess.run(
                        [PYTHON, PORTFOLIO_PY, "--watch", r["Ticker"]],
                        capture_output=True
                    )
                    st.success(f"{r['Ticker']} added to watchlist!")
                    st.cache_data.clear()
                    st.rerun()

    # ── Stock Deep-Dive ───────────────────────────────────────────────────────
    st.divider()
    st.markdown("#### 🔬 Stock Deep-Dive")

    all_tickers = sorted(set(
        [h["ticker"] for h in portfolio["holdings"] if h.get("shares", 0) > 0]
        + portfolio.get("watchlist", [])
    ))
    da_col1, da_col2 = st.columns([3, 1])
    with da_col1:
        da_ticker = st.selectbox("Select ticker to analyse", all_tickers,
                                 key="deepdive_ticker")
    with da_col2:
        da_custom = st.text_input("Or enter any ticker", placeholder="e.g. TSLA",
                                  key="deepdive_custom")
    analyse_btn = st.button("▶ Analyse", key="deepdive_btn", use_container_width=False)

    if analyse_btn:
        target = da_custom.strip().upper() if da_custom.strip() else da_ticker
        with st.spinner(f"Running analysis on {target}..."):
            result = subprocess.run(
                [PYTHON, ANALYZE_PY, target, "--json"],
                capture_output=True, text=True, timeout=60
            )
        if result.returncode != 0 or not result.stdout.strip():
            st.error(f"Analysis failed for {target}: {result.stderr[:200]}")
        else:
            d = json.loads(result.stdout)
            if "error" in d:
                st.error(d["error"])
            else:
                price   = d.get("current_price", 0)
                f       = d.get("fundamentals") or {}
                analyst = f.get("analyst_estimates") or {}
                growth  = f.get("growth") or {}
                val     = f.get("valuation") or {}
                health  = f.get("financial_health") or {}
                ma      = d.get("moving_averages") or {}
                sl      = d.get("suggested_levels") or {}
                atr     = d.get("atr") or {}
                pp      = d.get("pivot_points") or {}

                rec        = analyst.get("recommendation", "N/A").upper().replace("_", " ")
                target_p   = analyst.get("mean_target")
                upside     = analyst.get("upside_to_mean_target_pct")
                ema200     = ma.get("ema_200")
                trend      = "▲ Uptrend" if (price and ema200 and price > ema200) else "▼ Downtrend"
                trend_col  = "#00c853" if "▲" in trend else "#ff5252"
                supports   = sl.get("strong_support") or []
                resistances= sl.get("strong_resistance") or []
                stop       = sl.get("stop_loss_long")

                # Header
                st.markdown(
                    f"<div style='background:#1e1e2e;border-radius:10px;padding:16px;margin-bottom:12px'>"
                    f"<span style='font-size:1.3em;font-weight:700;color:white'>{target}</span> "
                    f"<span style='color:#aaa;font-size:0.9em'>{d.get('company_name','')}</span><br>"
                    f"<span style='font-size:1.6em;font-weight:700;color:#40c4ff'>${price:.2f}</span> "
                    f"<span style='color:{trend_col};font-size:0.85em;margin-left:10px'>{trend}</span> "
                    f"<span style='color:#aaa;font-size:0.8em;margin-left:10px'>as of {d.get('as_of','')}</span>"
                    f"</div>",
                    unsafe_allow_html=True
                )

                # Key metrics row
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Analyst Target", f"${target_p:.2f}" if target_p else "N/A",
                          f"{upside:+.1f}%" if upside else None)
                m2.metric("Recommendation", rec)
                m3.metric("Fwd P/E", f"{val.get('forward_pe'):.1f}x" if val.get("forward_pe") else "N/A")
                m4.metric("Rev Growth", f"{growth.get('revenue_growth_yoy_pct'):.0f}%" if growth.get("revenue_growth_yoy_pct") else "N/A")
                m5.metric("FCF", f"${health.get('free_cash_flow')/1e9:.1f}B" if health.get("free_cash_flow") else "N/A")

                st.markdown("---")
                lc, rc = st.columns(2)

                with lc:
                    st.markdown("**Key Levels**")
                    levels = []
                    for r_lvl in resistances[:2]:
                        levels.append({"Level": f"${r_lvl:.2f}", "Type": "Resistance", "Basis": "Support/Resistance"})
                    if pp.get("pivot"):
                        levels.append({"Level": f"${pp['pivot']:.2f}", "Type": "Pivot", "Basis": "Weekly"})
                    levels.append({"Level": f"${price:.2f}", "Type": "◀ Current", "Basis": ""})
                    for s_lvl in supports[:2]:
                        levels.append({"Level": f"${s_lvl:.2f}", "Type": "Support", "Basis": "Support/Resistance"})
                    if stop:
                        levels.append({"Level": f"${stop:.2f}", "Type": "Stop Loss", "Basis": "Long"})
                    st.dataframe(levels, use_container_width=True, hide_index=True)

                with rc:
                    st.markdown("**Moving Averages**")
                    mas = []
                    for label, key in [("EMA 20","ema_20"),("EMA 50","ema_50"),("EMA 200","ema_200")]:
                        v = ma.get(key)
                        if v:
                            bias = "Support ▲" if price > v else "Resistance ▼"
                            mas.append({"MA": label, "Value": f"${v:.2f}", "Signal": bias})
                    st.dataframe(mas, use_container_width=True, hide_index=True)

                    st.markdown("**ATR Daily Range**")
                    st.markdown(
                        f"<div style='background:#1e1e2e;border-radius:8px;padding:10px;font-size:0.85em'>"
                        f"⬆ <span style='color:#00c853'>${atr.get('daily_target_up',0):.2f}</span> &nbsp;|&nbsp; "
                        f"Current <span style='color:#40c4ff'>${price:.2f}</span> &nbsp;|&nbsp; "
                        f"⬇ <span style='color:#ff5252'>${atr.get('daily_target_down',0):.2f}</span><br>"
                        f"<span style='color:#aaa'>ATR(14): ±${atr.get('atr_value',0):.2f}</span>"
                        f"</div>",
                        unsafe_allow_html=True
                    )

                if d.get("warnings"):
                    st.caption("⚠️ " + " | ".join(d["warnings"]))


# ── TAB 3: TRADE MONITOR ──────────────────────────────────────────────────────
with tab3:
    MONITORS_FILE = os.path.join(ROOT_DIR, "data", "monitors.json")
    ALERTS_FILE   = os.path.join(ROOT_DIR, "data", "alerts.json")
    MONITOR_PY    = os.path.join(SCRIPTS_DIR, "agents", "trade_monitor.py")

    def load_monitors_st():
        if not os.path.exists(MONITORS_FILE): return {}
        with open(MONITORS_FILE) as f: return json.load(f)

    def load_alerts_st():
        if not os.path.exists(ALERTS_FILE): return []
        with open(ALERTS_FILE) as f: return json.load(f)

    monitors = load_monitors_st()
    active   = {t: c for t, c in monitors.items() if c.get("active")}

    st.markdown("#### ⚡ Trade Monitor  *(Agent 3)*")

    # ── Top controls ──────────────────────────────────────────────────────────
    ctrl1, ctrl2, ctrl3 = st.columns([1, 1, 3])
    with ctrl1:
        check_now = st.button("⟳ Check Now", use_container_width=True)
    with ctrl2:
        interval_h = st.number_input("Interval (hrs)", min_value=0.25,
                                     max_value=24.0, value=4.0, step=0.25)
    with ctrl3:
        st.caption(f"Run in terminal: `python scripts/agents/trade_monitor.py --interval {interval_h}`")

    if check_now:
        with st.spinner("Fetching live prices..."):
            result = subprocess.run(
                [PYTHON, MONITOR_PY, "--check"],
                capture_output=True, text=True, timeout=30
            )
        monitors = load_monitors_st()
        active   = {t: c for t, c in monitors.items() if c.get("active")}
        st.rerun()

    st.divider()

    # ── Active positions ──────────────────────────────────────────────────────
    st.markdown("##### Active Positions")

    if not active:
        st.info("No active monitors. Add one below.")
    else:
        for ticker, cfg in active.items():
            avg_cost = cfg.get("avg_cost", 0)
            shares   = cfg.get("shares", 0)
            stop     = cfg.get("stop")
            hard_stop= cfg.get("hard_stop")
            t1       = cfg.get("target1")
            t2       = cfg.get("target2")
            note     = cfg.get("note", "")

            # Fetch latest price from analyses (already loaded) or monitors
            d     = analyses.get(ticker, {})
            price = d.get("current_price") if "error" not in d else None

            pnl_pct = ((price - avg_cost) / avg_cost * 100) if price and avg_cost else None
            pnl_abs = ((price - avg_cost) * shares)         if price and avg_cost else None

            with st.container(border=True):
                h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 2, 1])
                h1.metric("Ticker",   ticker)
                h2.metric("Price",    fmt_price(price),   fmt_pct(pnl_pct) if pnl_pct is not None else "")
                h3.metric("Entry",    fmt_price(avg_cost))
                h4.metric("P&L $",    fmt_large(pnl_abs) if pnl_abs is not None else "N/A")
                with h5:
                    if st.button("✕ Close", key=f"close_{ticker}"):
                        subprocess.run([PYTHON, MONITOR_PY, "--close", ticker])
                        st.rerun()

                # Levels row
                lc1, lc2, lc3, lc4 = st.columns(4)
                lc1.markdown(f"<small style='color:#888'>Hard Stop</small><br>"
                             f"<span style='color:#ff1744;font-weight:700'>{fmt_price(hard_stop)}</span>",
                             unsafe_allow_html=True)
                lc2.markdown(f"<small style='color:#888'>Stop</small><br>"
                             f"<span style='color:#ff5252;font-weight:700'>{fmt_price(stop)}</span>",
                             unsafe_allow_html=True)
                lc3.markdown(f"<small style='color:#888'>Target 1</small><br>"
                             f"<span style='color:#69f0ae;font-weight:700'>{fmt_price(t1)}</span>",
                             unsafe_allow_html=True)
                lc4.markdown(f"<small style='color:#888'>Target 2</small><br>"
                             f"<span style='color:#00c853;font-weight:700'>{fmt_price(t2)}</span>",
                             unsafe_allow_html=True)

                # Progress bar
                if price and stop and t1:
                    lo  = hard_stop or stop
                    hi  = t2 or t1
                    rng = hi - lo
                    pct = max(0.0, min(1.0, (price - lo) / rng)) if rng else 0.0
                    bar_color = "#ff5252" if pnl_pct and pnl_pct < 0 else "#00c853"
                    st.markdown(
                        f"<small style='color:#888'>Stop {fmt_price(stop)} "
                        f"→ Entry {fmt_price(avg_cost)} "
                        f"→ T1 {fmt_price(t1)} "
                        f"→ T2 {fmt_price(t2)}</small>",
                        unsafe_allow_html=True)
                    st.progress(pct)

                if note:
                    st.caption(f"📌 {note}")

    # ── Add / edit monitor ────────────────────────────────────────────────────
    st.divider()
    st.markdown("##### Add / Update Monitor")

    with st.expander("Configure a trade to monitor"):
        fc1, fc2 = st.columns(2)
        new_ticker = fc1.text_input("Ticker").upper()
        new_shares = fc2.number_input("Shares", min_value=0.0, value=0.0, step=0.5)
        fc3, fc4   = st.columns(2)
        new_cost   = fc3.number_input("Avg Cost $", min_value=0.0, value=0.0, step=0.01)
        new_stop   = fc4.number_input("Stop $",     min_value=0.0, value=0.0, step=0.01)
        fc5, fc6, fc7 = st.columns(3)
        new_hstop  = fc5.number_input("Hard Stop $", min_value=0.0, value=0.0, step=0.01)
        new_t1     = fc6.number_input("Target 1 $",  min_value=0.0, value=0.0, step=0.01)
        new_t2     = fc7.number_input("Target 2 $",  min_value=0.0, value=0.0, step=0.01)
        new_note   = st.text_input("Note / rationale")

        if st.button("💾 Save Monitor", use_container_width=True):
            if new_ticker:
                cmd = [PYTHON, MONITOR_PY, "--set", new_ticker]
                if new_shares: cmd += ["--shares", str(new_shares)]
                if new_cost:   cmd += ["--cost",   str(new_cost)]
                if new_stop:   cmd += ["--stop",   str(new_stop)]
                if new_hstop:  cmd += ["--hstop",  str(new_hstop)]
                if new_t1:     cmd += ["--t1",     str(new_t1)]
                if new_t2:     cmd += ["--t2",     str(new_t2)]
                if new_note:   cmd += ["--note",   new_note]
                subprocess.run(cmd, capture_output=True)
                st.success(f"Monitor saved for {new_ticker}")
                st.rerun()

    # ── Alert log ─────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("##### Alert Log")

    alerts = load_alerts_st()
    if not alerts:
        st.caption("No alerts yet. Alerts appear here when targets or stops are hit.")
    else:
        ALERT_STYLES = {
            "success": ("#00c853", "🟢"),
            "info":    ("#40c4ff", "🔵"),
            "warning": ("#ffd600", "🟡"),
            "danger":  ("#ff5252", "🔴"),
        }
        for alert in alerts[:20]:
            color, icon = ALERT_STYLES.get(alert.get("level","info"), ("#888","⚪"))
            st.markdown(
                f"<div style='background:#1e1e2e;border-left:3px solid {color};"
                f"border-radius:6px;padding:8px 14px;margin:4px 0'>"
                f"<span style='color:{color}'>{icon} {alert['message']}</span>"
                f"<span style='color:#555;font-size:0.8em;float:right'>{alert['timestamp']}</span>"
                f"</div>",
                unsafe_allow_html=True
            )
        if st.button("🗑 Clear Alert Log"):
            with open(ALERTS_FILE, "w") as f: json.dump([], f)
            st.rerun()


# ── TAB 4: WEEKLY REVIEW ─────────────────────────────────────────────────────
with tab4:
    REVIEWS_FILE  = os.path.join(ROOT_DIR, "data", "reviews.json")
    REVIEW_PY     = os.path.join(SCRIPTS_DIR, "agents", "weekly_review.py")
    SNAPSHOTS_DIR = os.path.join(ROOT_DIR, "data", "snapshots")

    def load_reviews():
        if not os.path.exists(REVIEWS_FILE): return []
        with open(REVIEWS_FILE) as f: return json.load(f)

    def list_snapshots():
        if not os.path.exists(SNAPSHOTS_DIR): return []
        return sorted(os.listdir(SNAPSHOTS_DIR), reverse=True)

    st.markdown("#### 📅 Weekly Review  *(Agent 4)*")

    # ── Controls ──────────────────────────────────────────────────────────────
    rc1, rc2, rc3 = st.columns([1, 1, 3])
    with rc1:
        run_review = st.button("▶ Run Review Now", use_container_width=True)
    with rc2:
        rev_interval = st.number_input("Interval (days)", min_value=0.25,
                                       max_value=7.0, value=1.0, step=0.25)
    with rc3:
        st.caption(f"Terminal: `python scripts/agents/weekly_review.py --interval {rev_interval}`")

    # ── Delta summary cards (from latest snapshots) ───────────────────────────
    snaps = list_snapshots()
    snap_files = [s for s in snaps if not s.startswith("monday_")]

    if len(snap_files) >= 2:
        def load_snap(fname):
            with open(os.path.join(SNAPSHOTS_DIR, fname)) as f:
                return json.load(f)

        curr = load_snap(snap_files[0])
        prev = load_snap(snap_files[1])

        # Monday snapshot
        monday_files = [s for s in snaps if s.startswith("monday_")]
        mon  = load_snap(monday_files[0]) if monday_files else prev

        st.divider()
        st.markdown("##### Period-over-Period Snapshot")

        d1, d2, d3, d4 = st.columns(4)
        d1.metric("Current Value",    f"${curr['total_value']:,.2f}")

        daily_val_delta = curr["total_value"] - prev["total_value"]
        daily_val_pct   = (daily_val_delta / prev["total_value"] * 100) if prev["total_value"] else 0
        d2.metric("Since Last Check", f"${daily_val_delta:+,.2f}", f"{daily_val_pct:+.1f}%")

        weekly_val_delta = curr["total_value"] - mon["total_value"]
        weekly_val_pct   = (weekly_val_delta / mon["total_value"] * 100) if mon["total_value"] else 0
        d3.metric("Since Monday",     f"${weekly_val_delta:+,.2f}", f"{weekly_val_pct:+.1f}%")

        d4.metric("Snapshots Saved",  len(snap_files))

        # Per-ticker delta table
        st.markdown("##### Price Changes")
        delta_rows = []
        for h in curr["holdings"]:
            t          = h["ticker"]
            curr_price = h.get("price")
            prev_price = prev.get("prices", {}).get(t)
            mon_price  = mon.get("prices", {}).get(t)
            if curr_price:
                delta_rows.append({
                    "Ticker":       t,
                    "Current":      f"${curr_price:.2f}",
                    "vs Last":      f"{((curr_price-prev_price)/prev_price*100):+.1f}%" if prev_price else "—",
                    "vs Monday":    f"{((curr_price-mon_price)/mon_price*100):+.1f}%"   if mon_price  else "—",
                    "_daily_pct":   ((curr_price-prev_price)/prev_price*100) if prev_price else 0,
                    "_weekly_pct":  ((curr_price-mon_price)/mon_price*100)   if mon_price  else 0,
                })

        if delta_rows:
            ddf = pd.DataFrame(delta_rows)

            def color_delta(val):
                try:
                    v = float(val.replace("%","").replace("+",""))
                    return f"color: {'#00c853' if v >= 0 else '#ff5252'}"
                except: return ""

            display_ddf = ddf.drop(columns=["_daily_pct","_weekly_pct"])
            styled_d = (display_ddf.style
                        .applymap(color_delta, subset=["vs Last","vs Monday"]))
            st.dataframe(styled_d, use_container_width=True, hide_index=True)

    elif snap_files:
        st.info("Run the review again to build up snapshot history for delta comparisons.")
    else:
        st.info("No snapshots yet. Click '▶ Run Review Now' to generate the first one.")

    # ── Run review ────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("##### 🤖 AI Narrative Review")

    if run_review:
        def stream_review():
            import anthropic
            sys.path.insert(0, SCRIPTS_DIR)
            from agents.weekly_review import (
                save_snapshot, load_last_snapshot, load_monday_snapshot,
                compute_delta, fetch_news_brief, fmt_delta_block,
                _snap_label, save_review, REVIEW_SYSTEM, REVIEW_TEMPLATE
            )
            from portfolio import score_stock

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                yield "⚠️ ANTHROPIC_API_KEY not set."; return

            # Always reload fresh data so exited positions / new entries are correct
            st.cache_data.clear()
            fresh     = load_data()
            fresh_pf  = fresh["portfolio"]
            fresh_an  = fresh["analyses"]

            ts        = datetime.now()
            holdings  = [h for h in fresh_pf["holdings"] if h.get("shares", 0) > 0]
            watchlist = fresh_pf.get("watchlist", [])

            curr_snap, snap_path = save_snapshot(fresh, ts)
            last_snap   = load_last_snapshot(exclude_path=snap_path)
            monday_snap = load_monday_snapshot(ts)
            daily_delta  = compute_delta(curr_snap, last_snap,   _snap_label(last_snap))
            weekly_delta = compute_delta(curr_snap, monday_snap, _snap_label(monday_snap))

            holdings_ctx = ""
            for h in holdings:
                t    = h["ticker"]; d = fresh_an.get(t, {})
                name = d.get("company_name", t) if "error" not in d else t
                news = fetch_news_brief(t, name)
                price = d.get("current_price") if "error" not in d else None
                pnl   = ((price-h["avg_cost"])/h["avg_cost"]*100) if price and h["avg_cost"] else None
                holdings_ctx += (f"### {t}\nPrice: ${price:.2f} | P&L: {pnl:+.1f}%\n{news}\n\n")

            scored_w = []
            for t in watchlist:
                d = fresh_an.get(t, {})
                if "error" not in d:
                    score, signal, _ = score_stock(d)
                    price = d.get("current_price")
                    f  = d.get("fundamentals") or {}
                    an = f.get("analyst_estimates") or {}
                    scored_w.append((score or 0, t, price, signal,
                                     an.get("mean_target"), an.get("upside_to_mean_target_pct")))
            scored_w.sort(reverse=True)
            watchlist_ctx = "\n".join(
                f"- {t}: ${p:.2f} | {sig} ({sc}/100) | Target ${tgt} ({usd:+.1f}%)"
                for sc, t, p, sig, tgt, usd in scored_w[:3]
                if p and tgt and usd is not None
            )

            prompt = REVIEW_TEMPLATE.format(
                date=ts.strftime("%Y-%m-%d"),
                curr_value=curr_snap["total_value"],
                cost=curr_snap["total_cost"],
                total_pnl_pct=curr_snap["total_pnl_pct"],
                total_pnl_abs=curr_snap["total_pnl_abs"],
                daily_label=daily_delta.get("label","N/A"),
                daily_val_pct=daily_delta.get("val_pct",0),
                daily_val_delta=daily_delta.get("val_delta",0),
                daily_ticker_deltas=fmt_delta_block(daily_delta),
                weekly_label=weekly_delta.get("label","N/A"),
                weekly_val_pct=weekly_delta.get("val_pct",0),
                weekly_val_delta=weekly_delta.get("val_delta",0),
                weekly_ticker_deltas=fmt_delta_block(weekly_delta),
                holdings_context=holdings_ctx.strip(),
                watchlist_context=watchlist_ctx.strip(),
            )

            client = anthropic.Anthropic(api_key=api_key, timeout=240.0)
            full_text = ""
            with client.messages.stream(
                model="claude-sonnet-4-6", max_tokens=1200,
                system=REVIEW_SYSTEM,
                messages=[{"role":"user","content":prompt}]
            ) as stream:
                for text in stream.text_stream:
                    full_text += text
                    yield text
            save_review(full_text, ts)

        with st.container(border=True):
            st.write_stream(stream_review())
        st.cache_data.clear()
        st.rerun()

    # ── Past reviews ──────────────────────────────────────────────────────────
    reviews = load_reviews()
    if reviews:
        st.divider()
        st.markdown("##### Past Reviews")
        selected = st.selectbox(
            "Select a review",
            options=[r["date"] for r in reviews],
            index=0,
        )
        chosen = next((r for r in reviews if r["date"] == selected), None)
        if chosen:
            with st.container(border=True):
                st.markdown(chosen["text"])


# ── TAB 5: PORTFOLIO CHAT (Agent 5) ───────────────────────────────────────────
with tab5:
    import anthropic as _anthropic

    st.markdown("#### 💬 Portfolio Chat  *(Agent 5)*")
    st.caption("Ask anything about your portfolio — positions, P&L, signals, strategy, what to do next.")

    # ── Build system context once per session ─────────────────────────────────
    if "chat_system" not in st.session_state:
        # Fresh data for context
        try:
            _fresh     = load_data()
            _fresh_pf  = _fresh["portfolio"]
            _fresh_an  = _fresh["analyses"]
        except Exception:
            _fresh_pf  = portfolio
            _fresh_an  = analyses

        _holdings = [h for h in _fresh_pf["holdings"] if h.get("shares", 0) > 0]

        # Holdings block
        _h_lines = []
        _total_cost = _total_val = 0
        for h in _holdings:
            t  = h["ticker"]
            d  = _fresh_an.get(t, {})
            px = d.get("current_price") if "error" not in d else None
            f  = d.get("fundamentals") or {}
            an = f.get("analyst_estimates") or {}
            pnl_pct = ((px - h["avg_cost"]) / h["avg_cost"] * 100) if px and h["avg_cost"] else None
            pnl_abs = (px - h["avg_cost"]) * h["shares"] if px and h["avg_cost"] else None
            if px:
                _total_cost += h["shares"] * h["avg_cost"]
                _total_val  += h["shares"] * px
            rec    = an.get("recommendation", "N/A")
            target = an.get("mean_target")
            upside = an.get("upside_to_mean_target_pct")
            _h_lines.append(
                f"- {t}: {h['shares']} shares @ ${h['avg_cost']:.2f} avg cost | "
                f"Current ${px:.2f} | P&L {pnl_pct:+.1f}% (${pnl_abs:+.0f}) | "
                f"Analyst: {rec.upper()} target ${target:.2f} ({upside:+.1f}% upside)"
                if px and target and upside is not None else
                f"- {t}: {h['shares']} shares @ ${h['avg_cost']:.2f} avg cost | Price unavailable"
            )
        _total_pnl_pct = ((_total_val - _total_cost) / _total_cost * 100) if _total_cost else 0
        _total_pnl_abs = _total_val - _total_cost

        # Watchlist block
        _wl_lines = []
        for t in _fresh_pf.get("watchlist", []):
            d  = _fresh_an.get(t, {})
            if "error" in d:
                continue
            px = d.get("current_price")
            f  = d.get("fundamentals") or {}
            an = f.get("analyst_estimates") or {}
            from portfolio import score_stock as _ss
            score, signal, _ = _ss(d)
            target = an.get("mean_target")
            upside = an.get("upside_to_mean_target_pct")
            _wl_lines.append(
                f"- {t}: ${px:.2f} | {signal} ({score}/100) | "
                f"Target ${target:.2f} ({upside:+.1f}%)" if px and target and upside is not None
                else f"- {t}: data unavailable"
            )

        # Latest weekly review
        _latest_review = ""
        try:
            _rev_path = os.path.join(ROOT_DIR, "data", "reviews.json")
            if os.path.exists(_rev_path):
                with open(_rev_path) as _f:
                    _revs = json.load(_f)
                if _revs:
                    _latest_review = _revs[-1].get("text", "")[:1500]
        except Exception:
            pass

        st.session_state["chat_system"] = f"""You are an expert portfolio advisor for a personal AI-theme stock portfolio. You have full context of the user's current positions, watchlist, and latest weekly review. Answer questions directly and specifically — reference actual tickers, prices, P&L figures, and signals from the data provided. Be concise and actionable. Do not add generic disclaimers.

TODAY'S DATE: {datetime.now().strftime("%Y-%m-%d")}

=== CURRENT HOLDINGS ===
Portfolio P&L: {_total_pnl_pct:+.1f}% (${_total_pnl_abs:+,.0f})
{chr(10).join(_h_lines)}

=== WATCHLIST (scored & ranked) ===
{chr(10).join(_wl_lines[:10])}

=== LATEST WEEKLY REVIEW (summary) ===
{_latest_review if _latest_review else "No review available yet."}
"""

    # ── Session chat history ───────────────────────────────────────────────────
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    # ── Render chat history ────────────────────────────────────────────────────
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state["chat_history"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # ── Chat input ─────────────────────────────────────────────────────────────
    user_input = st.chat_input("Ask about your portfolio...")

    if user_input:
        # Append user message and render immediately
        st.session_state["chat_history"].append({"role": "user", "content": user_input})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(user_input)

        # Build messages array for Claude (full history)
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state["chat_history"]
        ]

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            st.error("ANTHROPIC_API_KEY not set.")
        else:
            client = _anthropic.Anthropic(api_key=api_key, timeout=120.0)
            with chat_container:
                with st.chat_message("assistant"):
                    response_text = st.write_stream(
                        _stream_chat(client, st.session_state["chat_system"], messages)
                    )
            st.session_state["chat_history"].append(
                {"role": "assistant", "content": response_text}
            )

    # ── Clear chat button ──────────────────────────────────────────────────────
    if st.session_state.get("chat_history"):
        if st.button("🗑 Clear chat", key="clear_chat"):
            st.session_state["chat_history"] = []
            st.session_state.pop("chat_system", None)
            st.rerun()
