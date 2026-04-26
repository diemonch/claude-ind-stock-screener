"""Renders a single thesis card with zones, tags, thesis text, and expandable chart."""

from typing import Dict

import streamlit as st

from ui.components.candlestick_chart import render_candlestick

SIGNAL_COLOR = {
    "BUY":        "#4ade80",
    "STRONG BUY": "#4ade80",
    "SELL":       "#f87171",
    "WATCH":      "#fbbf24",
    "AVOID":      "#94a3b8",
    "HOLD":       "#94a3b8",
}

ACCOUNT_COLOR = {
    "swing":        "#60a5fa",
    "sip_eligible": "#a78bfa",
    "watchlist":    "#fbbf24",
    "avoid":        "#94a3b8",
}

HORIZON_LABEL = {
    "swing_4_6_weeks":        "Swing 4–6w",
    "accumulate_6_18_months": "Accum 6–18m",
}


def _badge(text: str, color: str) -> str:
    """Return an HTML inline badge span."""
    return (
        '<span style="background:{c}22;color:{c};padding:2px 9px;'
        'border-radius:4px;font-size:0.73rem;font-weight:600;'
        'letter-spacing:0.03em">{t}</span>'
    ).format(c=color, t=text)


def _fmt_zone(zone) -> str:
    if zone and len(zone) == 2:
        return "₹{:,.0f}–{:,.0f}".format(zone[0], zone[1])
    return "—"


def render_signal_card(card: Dict) -> None:
    """
    Render one thesis card as a bordered Streamlit block.
    card: thesis dict from sonnet_analyst output.
    """
    ticker     = card.get("ticker", "")
    company    = card.get("company", "")
    sector     = card.get("sector", "")
    signal     = (card.get("signal") or "WATCH").upper()
    buy_zone   = card.get("buy_zone") or []
    sell_zone  = card.get("sell_zone") or []
    stop_loss  = card.get("stop_loss")
    rr         = card.get("risk_reward")
    horizon    = card.get("horizon", "")
    acct_tag   = card.get("account_tag", "")
    confluence = card.get("confluence", "")
    thesis     = card.get("thesis", "")
    risk_text  = card.get("risk", "")
    circuit    = card.get("circuit_flag", False)

    sig_color  = SIGNAL_COLOR.get(signal, "#94a3b8")
    acct_color = ACCOUNT_COLOR.get(acct_tag, "#8b949e")
    hor_label  = HORIZON_LABEL.get(horizon, horizon.replace("_", " ").title() if horizon else "")

    with st.container(border=True):
        # ── Header ────────────────────────────────────────────────────────────
        h1, h2 = st.columns([3, 1])
        with h1:
            st.markdown(
                '<span style="font-family:monospace;font-size:1.05rem;'
                'font-weight:700;color:#e6edf3">{tk}</span>'
                '&nbsp;<span style="color:#8b949e;font-size:0.82rem">{co}</span>'.format(
                    tk=ticker, co=company[:28]
                ),
                unsafe_allow_html=True,
            )
            st.caption(sector)
        with h2:
            badge_html = _badge(signal, sig_color)
            if circuit:
                badge_html += "&nbsp;" + _badge("⚡ CIRCUIT", "#f87171")
            st.markdown(badge_html, unsafe_allow_html=True)

        # ── Zones row ─────────────────────────────────────────────────────────
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Buy Zone",  _fmt_zone(buy_zone))
        c2.metric("Sell Zone", _fmt_zone(sell_zone))
        c3.metric("Stop Loss", "₹{:,.0f}".format(stop_loss) if stop_loss else "—")
        c4.metric("R:R",       "{:.1f}×".format(rr) if rr else "—")

        # ── Tags ──────────────────────────────────────────────────────────────
        tags_html = ""
        if hor_label:
            tags_html += _badge(hor_label, "#60a5fa") + "&nbsp;"
        if acct_tag:
            tags_html += _badge(acct_tag.replace("_", " ").title(), acct_color) + "&nbsp;"
        if confluence:
            tags_html += _badge("Conf " + str(confluence), "#8b949e")
        if tags_html:
            st.markdown(tags_html, unsafe_allow_html=True)
        st.write("")

        # ── Thesis ────────────────────────────────────────────────────────────
        st.markdown(
            '<p style="font-size:0.87rem;color:#c9d1d9;line-height:1.55;'
            'margin:0">{}</p>'.format(thesis),
            unsafe_allow_html=True,
        )

        # ── Risk (collapsed) ──────────────────────────────────────────────────
        with st.expander("Risk", expanded=False):
            st.markdown(
                '<p style="font-size:0.85rem;color:#f87171;line-height:1.5;'
                'margin:0">{}</p>'.format(risk_text),
                unsafe_allow_html=True,
            )

        # ── Chart (collapsed) ─────────────────────────────────────────────────
        with st.expander("Price Chart", expanded=False):
            render_candlestick(ticker, buy_zone, sell_zone, stop_loss)
