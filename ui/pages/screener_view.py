"""Screener tab — funnel metrics, sector heatmap, and survivors table."""

from typing import Dict, List

import pandas as pd
import streamlit as st

from ui.components.sector_heatmap import render_sector_heatmap


def render_screener_view(
    survivors: List[Dict],
    filter_summary: Dict,
    universe_size: int = 500,
    validated_count: int = 0,
    thesis_count: int = 0,
) -> None:
    """
    Render the Screener tab content.
    survivors: list of enriched ticker dicts from nifty_screener / signal_engine.
    filter_summary: dict of filter_name → tickers_eliminated.
    """
    # ── Funnel metrics ────────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Universe",    str(universe_size))
    m2.metric("Survivors",   str(len(survivors)),
              delta="-{}".format(universe_size - len(survivors)) if survivors else None)
    m3.metric("Validated",   str(validated_count))
    m4.metric("Thesis Cards",str(thesis_count))

    st.divider()

    # ── Sector heatmap ────────────────────────────────────────────────────────
    st.subheader("Sector Signal Map")
    render_sector_heatmap(survivors)

    # ── Filter breakdown ──────────────────────────────────────────────────────
    if filter_summary:
        with st.expander("Filter Breakdown", expanded=False):
            cols = st.columns(3)
            for i, (name, count) in enumerate(filter_summary.items()):
                cols[i % 3].metric(
                    name.replace("_", " ").title(),
                    "−{}".format(count),
                )

    st.divider()

    # ── Survivors table ───────────────────────────────────────────────────────
    st.subheader("Survivors ({})".format(len(survivors)))
    if not survivors:
        st.info("No survivors yet — run a scan or load latest results.")
        return

    df = pd.DataFrame(survivors)

    DISPLAY_COLS = [
        "ticker", "company", "sector", "signal", "confluence_score",
        "trend_shift", "rsi", "risk_reward", "volume_ratio",
        "buy_zone", "sell_zone", "fwd_pe", "roe_pct",
    ]
    show_cols = [c for c in DISPLAY_COLS if c in df.columns]
    df_show   = df[show_cols].copy()

    if "confluence_score" in df_show.columns:
        df_show = df_show.sort_values("confluence_score", ascending=False)
    if "buy_zone" in df_show.columns:
        df_show["buy_zone"]  = df_show["buy_zone"].apply(
            lambda z: "₹{:,.0f}–{:,.0f}".format(*z) if isinstance(z, list) and len(z) == 2 else "—"
        )
    if "sell_zone" in df_show.columns:
        df_show["sell_zone"] = df_show["sell_zone"].apply(
            lambda z: "₹{:,.0f}–{:,.0f}".format(*z) if isinstance(z, list) and len(z) == 2 else "—"
        )

    st.dataframe(
        df_show,
        use_container_width=True,
        hide_index=True,
        column_config={
            "ticker":           st.column_config.TextColumn("Ticker"),
            "company":          st.column_config.TextColumn("Company"),
            "sector":           st.column_config.TextColumn("Sector"),
            "signal":           st.column_config.TextColumn("Signal"),
            "confluence_score": st.column_config.NumberColumn("Confluence", format="%d"),
            "trend_shift":      st.column_config.TextColumn("Trend"),
            "rsi":              st.column_config.NumberColumn("RSI",      format="%.1f"),
            "risk_reward":      st.column_config.NumberColumn("R:R",      format="%.2f"),
            "volume_ratio":     st.column_config.NumberColumn("Vol Ratio",format="%.2f"),
            "buy_zone":         st.column_config.TextColumn("Buy Zone"),
            "sell_zone":        st.column_config.TextColumn("Sell Zone"),
            "fwd_pe":           st.column_config.NumberColumn("P/E",      format="%.1f"),
            "roe_pct":          st.column_config.NumberColumn("ROE%",     format="%.1f"),
        },
    )
