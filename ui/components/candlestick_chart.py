"""Plotly candlestick chart with buy/sell zone overlays and volume bars."""

from typing import List, Optional

import plotly.graph_objects as go
import streamlit as st


@st.cache_data(ttl=3_600, show_spinner=False)
def _fetch_history(ticker: str, days: int):
    """Fetch OHLCV from yfinance. Cached for 1 hour."""
    import yfinance as yf
    stock = yf.Ticker(ticker)
    hist  = stock.history(period="{}d".format(days), interval="1d", auto_adjust=True)
    return hist


def render_candlestick(
    ticker: str,
    buy_zone: List[float],
    sell_zone: List[float],
    stop_loss: Optional[float],
    days: int = 60,
) -> None:
    """
    Render a dark-theme candlestick chart with buy/sell zone bands and stop loss line.
    ticker: NSE ticker string. buy_zone/sell_zone: [lower, upper] price lists.
    """
    try:
        hist = _fetch_history(ticker, days)
        if hist.empty:
            st.warning("No price data for {}".format(ticker))
            return
    except Exception as exc:
        st.warning("Chart unavailable: {}".format(exc))
        return

    fig = go.Figure()

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=hist.index,
        open=hist["Open"],
        high=hist["High"],
        low=hist["Low"],
        close=hist["Close"],
        name=ticker,
        increasing_line_color="#4ade80",
        decreasing_line_color="#f87171",
        increasing_fillcolor="#4ade80",
        decreasing_fillcolor="#f87171",
        showlegend=False,
    ))

    # Buy zone — green band
    if len(buy_zone) == 2:
        fig.add_hrect(
            y0=buy_zone[0], y1=buy_zone[1],
            fillcolor="rgba(74,222,128,0.12)",
            line_width=1,
            line_color="rgba(74,222,128,0.5)",
            annotation_text="Buy", annotation_position="left",
            annotation_font_color="#4ade80",
            annotation_font_size=11,
        )

    # Sell zone — red band
    if len(sell_zone) == 2:
        fig.add_hrect(
            y0=sell_zone[0], y1=sell_zone[1],
            fillcolor="rgba(248,113,113,0.12)",
            line_width=1,
            line_color="rgba(248,113,113,0.5)",
            annotation_text="Sell", annotation_position="left",
            annotation_font_color="#f87171",
            annotation_font_size=11,
        )

    # Stop loss — amber dashed line
    if stop_loss:
        fig.add_hline(
            y=stop_loss,
            line_dash="dash",
            line_color="#fbbf24",
            line_width=1.5,
            annotation_text="Stop",
            annotation_font_color="#fbbf24",
            annotation_font_size=11,
        )

    # Volume bars (secondary y-axis)
    bar_colors = [
        "#4ade80" if float(c) >= float(o) else "#f87171"
        for c, o in zip(hist["Close"], hist["Open"])
    ]
    fig.add_trace(go.Bar(
        x=hist.index,
        y=hist["Volume"],
        name="Volume",
        marker_color=bar_colors,
        opacity=0.35,
        yaxis="y2",
        showlegend=False,
    ))

    fig.update_layout(
        height=360,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font_color="#e6edf3",
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis_rangeslider_visible=False,
        xaxis=dict(gridcolor="#21262d", showgrid=True),
        yaxis=dict(gridcolor="#21262d", title="₹", side="right"),
        yaxis2=dict(overlaying="y", side="left", showgrid=False, showticklabels=False),
    )

    st.plotly_chart(fig, use_container_width=True)
