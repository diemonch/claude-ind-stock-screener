"""Portfolio tab — India holdings with live prices, P&L, and market context."""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
import streamlit as st

ROOT_DIR          = Path(__file__).parent.parent.parent
PORTFOLIO_FILE    = ROOT_DIR / "portfolio_india.json"
ALERT_STATE_FILE  = ROOT_DIR / "data" / "results" / "alert_state.json"

STATUS_LABEL = {
    "active":          "🟢 Active",
    "target_hit":      "🔵 Prepare to Sell — Target Met",
    "stopped_out":     "🔴 Exit — SL Hit",
    "horizon_expired": "⚫ Horizon Expired",
    "dropped":         "🟠 Dropped from Screener",
}

# Alert severity order — highest first
_ALERT_PRIORITY = ["sl_breach", "danger_zone", "target_hit"]
_ALERT_DISPLAY  = {
    "sl_breach":   "🔴 SL Breached — EXIT",
    "danger_zone": "🟠 Danger Zone",
    "target_hit":  "🔵 Target Hit",
}


def load_alert_state() -> Dict:
    """Load today's alert state from position monitor output."""
    if not ALERT_STATE_FILE.exists():
        return {}
    try:
        with open(ALERT_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def current_alert(ticker: str, state: Dict) -> str:
    """Return the highest-severity alert fired today for a ticker, or empty string."""
    from datetime import date
    entry = state.get(ticker, {})
    if entry.get("date") != str(date.today()):
        return ""
    sent = entry.get("sent", [])
    for level in _ALERT_PRIORITY:
        if level in sent:
            return _ALERT_DISPLAY[level]
    return ""


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_portfolio() -> Dict:
    if not PORTFOLIO_FILE.exists():
        return {"name": "India Portfolio", "holdings": [], "watchlist": []}
    with open(PORTFOLIO_FILE) as f:
        return json.load(f)


def save_portfolio(data: Dict) -> None:
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(data, f, indent=2)


def _nse_session() -> requests.Session:
    """Return a requests Session with NSE cookies loaded."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    })
    try:
        s.get("https://www.nseindia.com", timeout=8)
    except Exception:
        pass
    return s


def _nse_quote(symbol: str, session) -> Optional[Dict]:
    """Fetch lastPrice + previousClose from NSE equity API. symbol = bare NSE symbol."""
    try:
        resp = session.get(
            "https://www.nseindia.com/api/quote-equity?symbol={}".format(symbol),
            timeout=8,
        )
        if resp.ok:
            pi = resp.json().get("priceInfo", {})
            price = float(pi.get("lastPrice", 0) or 0)
            prev  = float(pi.get("previousClose", price) or price)
            if price:
                return {
                    "price":   round(price, 2),
                    "prev":    round(prev,  2),
                    "day_chg": round((price - prev) / prev * 100, 2) if prev else 0.0,
                }
    except Exception:
        pass
    return None


def _ticker_to_symbol(ticker: str) -> str:
    """Strip .NS / .BO suffix to get bare NSE symbol."""
    return ticker.replace(".NS", "").replace(".BO", "").upper()


@st.cache_data(ttl=300)
def fetch_prices(tickers: tuple) -> Dict[str, Dict]:
    """Fetch CMP + day change via NSE India API (yfinance fallback)."""
    import time
    session = _nse_session()
    result  = {}
    for t in tickers:
        symbol = _ticker_to_symbol(t)
        data   = _nse_quote(symbol, session)
        if data:
            result[t] = data
        else:
            # yfinance fallback
            price, prev = 0.0, 0.0
            try:
                import yfinance as yf
                hist = yf.Ticker(t).history(period="5d")
                if not hist.empty:
                    closes = hist["Close"].dropna()
                    price  = round(float(closes.iloc[-1]), 2)
                    prev   = round(float(closes.iloc[-2]), 2) if len(closes) >= 2 else price
            except Exception:
                pass
            result[t] = {
                "price":   price,
                "prev":    prev,
                "day_chg": round((price - prev) / prev * 100, 2) if prev else 0.0,
            }
        time.sleep(0.3)
    return result


@st.cache_data(ttl=300)
def fetch_nifty50() -> Dict:
    """Fetch Nifty 50 level + day change from NSE API."""
    import time
    session = _nse_session()
    try:
        resp = session.get(
            "https://www.nseindia.com/api/allIndices",
            timeout=8,
        )
        if resp.ok:
            for idx in resp.json().get("data", []):
                if idx.get("index") == "NIFTY 50":
                    price = float(idx.get("last", 0) or 0)
                    prev  = float(idx.get("previousClose", price) or price)
                    return {
                        "level":   round(price, 2),
                        "day_chg": round((price - prev) / prev * 100, 2) if prev else 0.0,
                    }
    except Exception:
        pass
    return {"level": 0.0, "day_chg": 0.0}


# ── Add-position form ──────────────────────────────────────────────────────────

def _render_add_form(portfolio: Dict) -> bool:
    """Render inline add-position form. Returns True if a position was added."""
    with st.expander("+ Add Position", expanded=not portfolio["holdings"]):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 3])
        ticker   = c1.text_input("Ticker (e.g. CGCL.NS)", key="add_ticker").strip().upper()
        shares   = c2.number_input("Shares", min_value=0.0, step=1.0, key="add_shares")
        avg_cost = c3.number_input("Avg Cost ₹", min_value=0.0, step=1.0, key="add_cost")
        notes    = c4.text_input("Notes (optional)", key="add_notes")

        if st.button("Add", key="add_pos_btn"):
            if not ticker or shares <= 0 or avg_cost <= 0:
                st.warning("Ticker, shares, and avg cost are required.")
                return False
            portfolio["holdings"].append({
                "ticker":     ticker if ticker.endswith(".NS") or ticker.endswith(".BO") else ticker + ".NS",
                "shares":     shares,
                "avg_cost":   avg_cost,
                "date_added": datetime.today().strftime("%Y-%m-%d"),
                "notes":      notes,
            })
            save_portfolio(portfolio)
            st.success("Added {}".format(ticker))
            st.rerun()
    return False


# ── Main render ────────────────────────────────────────────────────────────────

def render_portfolio_view(
    registry: Dict,
    market_condition: str = "sideways",
    week_context: str = "normal",
) -> None:
    """
    Render the Portfolio tab.
    registry: picks_registry dict for cross-referencing holdings.
    """
    portfolio = load_portfolio()
    holdings  = [h for h in portfolio.get("holdings", []) if h.get("shares", 0) > 0]
    watchlist = portfolio.get("watchlist", [])

    # ── Market context bar ────────────────────────────────────────────────────
    nifty = fetch_nifty50()
    n_col1, n_col2, n_col3, n_col4 = st.columns(4)
    n_col1.metric(
        "Nifty 50",
        "{:,.2f}".format(nifty["level"]) if nifty["level"] else "—",
        delta="{:+.2f}%".format(nifty["day_chg"]) if nifty["level"] else None,
    )
    n_col2.metric("Market Condition", market_condition.replace("_", " ").title())
    n_col3.metric("Week Context",     week_context.replace("_", " ").title())
    n_col4.metric("As Of", datetime.today().strftime("%d %b %Y"))

    st.divider()

    # ── Add position form ─────────────────────────────────────────────────────
    _render_add_form(portfolio)

    if not holdings:
        st.info("No active holdings — add your first position above.")
        _render_watchlist(watchlist, registry)
        return

    # ── Fetch live prices ─────────────────────────────────────────────────────
    all_tickers = tuple(h["ticker"] for h in holdings)
    if st.button("🔄 Refresh Prices", key="refresh_prices"):
        fetch_prices.clear()
        fetch_nifty50.clear()
        st.rerun()
    prices = fetch_prices(all_tickers)

    # ── Portfolio metrics ─────────────────────────────────────────────────────
    total_cost  = sum(h["shares"] * h["avg_cost"]                 for h in holdings)
    total_value = sum(h["shares"] * prices.get(h["ticker"], {}).get("price", h["avg_cost"]) for h in holdings)
    total_pnl   = total_value - total_cost
    pnl_pct     = total_pnl / total_cost * 100 if total_cost else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Portfolio Value", "₹{:,.0f}".format(total_value))
    m2.metric("Total Cost",      "₹{:,.0f}".format(total_cost))
    m3.metric(
        "Unrealised P&L",
        "₹{:,.0f}".format(total_pnl),
        delta="{:+.1f}%".format(pnl_pct),
        delta_color="normal",
    )
    m4.metric("Positions", str(len(holdings)))

    st.divider()

    # ── Holdings table ────────────────────────────────────────────────────────
    st.subheader("Holdings")

    alert_state = load_alert_state()

    rows = []
    for h in holdings:
        t     = h["ticker"]
        px    = prices.get(t, {}).get("price", 0.0)
        dchg  = prices.get(t, {}).get("day_chg", 0.0)
        cost  = h["avg_cost"]
        shrs  = h["shares"]
        val   = shrs * px
        gain  = val - shrs * cost
        gain_pct = gain / (shrs * cost) * 100 if cost else 0

        # Registry cross-reference
        reg_entry = registry.get(t)
        tag = STATUS_LABEL.get(reg_entry["status"], "⚪ Unknown") if reg_entry else "—"

        rows.append({
            "Alert":    current_alert(t, alert_state) or "✅ Clear",
            "Ticker":   t,
            "Shares":   shrs,
            "Avg Cost": cost,
            "CMP":      px,
            "Day %":    dchg,
            "Value ₹":  round(val, 0),
            "P&L ₹":    round(gain, 0),
            "P&L %":    round(gain_pct, 2),
            "Screener": tag,
            "Notes":    h.get("notes", ""),
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Alert":    st.column_config.TextColumn("Alert"),
            "Ticker":   st.column_config.TextColumn("Ticker"),
            "Shares":   st.column_config.NumberColumn("Shares",   format="%.1f"),
            "Avg Cost": st.column_config.NumberColumn("Avg Cost", format="₹%.2f"),
            "CMP":      st.column_config.NumberColumn("CMP",      format="₹%.2f"),
            "Day %":    st.column_config.NumberColumn("Day %",    format="%.2f%%"),
            "Value ₹":  st.column_config.NumberColumn("Value",    format="₹%,.0f"),
            "P&L ₹":    st.column_config.NumberColumn("P&L ₹",   format="₹%+,.0f"),
            "P&L %":    st.column_config.NumberColumn("P&L %",    format="%+.2f%%"),
            "Screener": st.column_config.TextColumn("Screener Status"),
            "Notes":    st.column_config.TextColumn("Notes"),
        },
    )

    # ── Charts ────────────────────────────────────────────────────────────────
    try:
        import plotly.graph_objects as go

        ch_left, ch_right = st.columns(2)

        with ch_right:
            st.caption("Allocation")
            fig_pie = go.Figure(go.Pie(
                labels=[r["Ticker"] for r in rows],
                values=[r["Value ₹"] for r in rows],
                hole=0.4,
                textinfo="label+percent",
            ))
            fig_pie.update_layout(
                paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                font_color="#e6edf3", showlegend=False,
                margin=dict(t=20, b=20, l=20, r=20), height=280,
            )
            st.plotly_chart(fig_pie, use_container_width=True)

        with ch_left:
            st.caption("P&L %  per position")
            colors = ["#2ea043" if r["P&L %"] >= 0 else "#f85149" for r in rows]
            fig_bar = go.Figure(go.Bar(
                x=[r["Ticker"] for r in rows],
                y=[r["P&L %"]  for r in rows],
                marker_color=colors,
                text=["{:+.1f}%".format(r["P&L %"]) for r in rows],
                textposition="outside",
            ))
            fig_bar.update_layout(
                paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                font_color="#e6edf3", showlegend=False,
                xaxis=dict(showgrid=False),
                yaxis=dict(showgrid=False, zeroline=True, zerolinecolor="#30363d"),
                margin=dict(t=30, b=20, l=20, r=20), height=280,
            )
            st.plotly_chart(fig_bar, use_container_width=True)

    except ImportError:
        pass

    st.divider()
    _render_watchlist(watchlist, registry)


def _render_watchlist(watchlist: List[str], registry: Dict) -> None:
    """Render the watchlist section with registry status where available."""
    if not watchlist:
        return

    st.subheader("Watchlist")
    all_tickers = tuple(watchlist)
    prices      = fetch_prices(all_tickers)

    rows = []
    for t in watchlist:
        px   = prices.get(t, {}).get("price", 0.0)
        dchg = prices.get(t, {}).get("day_chg", 0.0)
        reg  = registry.get(t)

        rows.append({
            "Ticker":   t,
            "CMP":      px,
            "Day %":    dchg,
            "Signal":   reg.get("signal", "—")   if reg else "—",
            "Buy Zone": _fmt_zone(reg.get("buy_zone"))  if reg else "—",
            "Sell Zone":_fmt_zone(reg.get("sell_zone")) if reg else "—",
            "Status":   STATUS_LABEL.get(reg["status"], "⚪ Unknown") if reg else "—",
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "CMP":   st.column_config.NumberColumn("CMP",   format="₹%.2f"),
            "Day %": st.column_config.NumberColumn("Day %", format="%.2f%%"),
        },
    )


def _fmt_zone(zone) -> str:
    if isinstance(zone, list) and len(zone) == 2:
        return "₹{:,.0f}–{:,.0f}".format(*zone)
    return "—"


def build_portfolio_context(registry: Dict, market_condition: str, week_context: str) -> str:
    """
    Build a plain-text portfolio context string for the Chat tab's system prompt.
    Called by chat_view.py.
    """
    portfolio = load_portfolio()
    holdings  = [h for h in portfolio.get("holdings", []) if h.get("shares", 0) > 0]
    watchlist = portfolio.get("watchlist", [])

    lines = [
        "INDIA PORTFOLIO CONTEXT",
        "Market Condition: {}".format(market_condition),
        "Week Context:     {}".format(week_context),
        "As Of:            {}".format(datetime.today().strftime("%d %b %Y")),
        "",
    ]

    if holdings:
        all_tickers = tuple(h["ticker"] for h in holdings)
        prices      = fetch_prices(all_tickers)

        total_cost  = sum(h["shares"] * h["avg_cost"] for h in holdings)
        total_value = sum(
            h["shares"] * prices.get(h["ticker"], {}).get("price", h["avg_cost"])
            for h in holdings
        )
        total_pnl   = total_value - total_cost
        pnl_pct     = total_pnl / total_cost * 100 if total_cost else 0

        lines += [
            "=== HOLDINGS (Portfolio P&L: {:+.1f}% / ₹{:+,.0f}) ===".format(pnl_pct, total_pnl),
        ]
        for h in holdings:
            t    = h["ticker"]
            px   = prices.get(t, {}).get("price", 0.0)
            cost = h["avg_cost"]
            shrs = h["shares"]
            gain_pct = (px - cost) / cost * 100 if cost else 0
            reg  = registry.get(t)
            reg_note = " | Screener: {} {}".format(
                reg["signal"], reg.get("buy_zone", ""),
            ) if reg and reg["status"] == "active" else ""
            lines.append(
                "- {t}: {s} shares @ ₹{c:.0f} avg | CMP ₹{p:.2f} | P&L {g:+.1f}%{r}{n}".format(
                    t=t, s=shrs, c=cost, p=px, g=gain_pct,
                    r=reg_note,
                    n=" | {}".format(h["notes"]) if h.get("notes") else "",
                )
            )
    else:
        lines.append("=== HOLDINGS === (none added yet)")

    lines += ["", "=== WATCHLIST ==="]
    for t in watchlist:
        reg = registry.get(t)
        if reg and reg["status"] == "active":
            lines.append("- {} | Signal: {} | Buy: {} | Sell: {} | Conf: {}".format(
                t, reg.get("signal", "—"),
                _fmt_zone(reg.get("buy_zone")),
                _fmt_zone(reg.get("sell_zone")),
                reg.get("confluence", "—"),
            ))
        else:
            lines.append("- {}".format(t))

    active_picks = [e for e in registry.values() if e["status"] == "active"]
    if active_picks:
        lines += ["", "=== ACTIVE SCREENER PICKS (from registry) ==="]
        for e in active_picks:
            lines.append(
                "- {ticker} {company} | {signal} | Buy {bz} | Sell {sz} | R:R {rr}x | {horizon}".format(
                    ticker=e["ticker"], company=e.get("company", ""),
                    signal=e.get("signal", "—"),
                    bz=_fmt_zone(e.get("buy_zone")),
                    sz=_fmt_zone(e.get("sell_zone")),
                    rr=e.get("risk_reward", "—"),
                    horizon=(e.get("horizon") or "").replace("_", " "),
                )
            )

    return "\n".join(lines)
