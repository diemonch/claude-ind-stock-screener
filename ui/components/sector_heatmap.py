"""Plotly heatmap showing signal distribution across Nifty sectors."""

from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def render_sector_heatmap(survivors: List[Dict]) -> None:
    """
    Render a sector × signal heatmap from the survivors list.
    survivors: list of enriched ticker dicts with 'sector' and 'signal' fields.
    """
    if not survivors:
        st.info("No survivors data — run a scan first.")
        return

    df         = pd.DataFrame(survivors)
    sector_col = "sector" if "sector" in df.columns else "industry"
    signal_col = "signal" if "signal" in df.columns else None

    SIGNAL_ORDER = ["buy", "watch", "avoid", "sell",
                    "BUY", "STRONG BUY", "WATCH", "HOLD"]

    if signal_col and signal_col in df.columns:
        # Normalise signal strings to lowercase for grouping
        df["_sig_norm"] = df[signal_col].str.lower().str.strip()
        pivot = (
            df.groupby([sector_col, "_sig_norm"])
            .size()
            .reset_index(name="count")
        )
        sectors = sorted(pivot[sector_col].dropna().unique())
        signals = [s for s in ["buy", "watch", "avoid", "sell"] if s in pivot["_sig_norm"].values]
        if not signals:
            signals = sorted(pivot["_sig_norm"].unique())

        z, text = [], []
        for sig in signals:
            row, trow = [], []
            for sec in sectors:
                mask  = (pivot[sector_col] == sec) & (pivot["_sig_norm"] == sig)
                count = int(pivot.loc[mask, "count"].sum())
                row.append(count)
                trow.append(str(count) if count else "")
            z.append(row)
            text.append(trow)

        fig = go.Figure(data=go.Heatmap(
            z=z, x=sectors, y=[s.upper() for s in signals],
            text=text, texttemplate="%{text}",
            colorscale=[[0, "#161b22"], [0.4, "#1f6feb"], [1, "#4ade80"]],
            showscale=False,
            hoverongaps=False,
        ))
    else:
        counts = df[sector_col].value_counts()
        fig = go.Figure(data=go.Heatmap(
            z=[counts.values.tolist()],
            x=counts.index.tolist(),
            y=["Count"],
            colorscale=[[0, "#161b22"], [1, "#4ade80"]],
            showscale=False,
        ))

    fig.update_layout(
        height=200,
        paper_bgcolor="#0d1117",
        plot_bgcolor="#0d1117",
        font_color="#e6edf3",
        margin=dict(l=0, r=0, t=10, b=0),
        xaxis=dict(side="bottom", tickangle=-30),
    )
    st.plotly_chart(fig, use_container_width=True)
