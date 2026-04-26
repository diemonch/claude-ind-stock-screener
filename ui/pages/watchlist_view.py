"""Shortlist tab — 2-column grid of thesis cards with sidebar filter support."""

from typing import Dict, List, Optional

import streamlit as st

from ui.components.signal_card import render_signal_card


def render_watchlist_view(
    thesis_cards: List[Dict],
    min_confluence: int = 0,
    horizon_filter: Optional[List[str]] = None,
    account_filter: Optional[List[str]] = None,
) -> None:
    """
    Render the Shortlist tab as a 2-column card grid.
    thesis_cards: output from sonnet_analyst.run_sonnet_analyst.
    Sidebar filter params narrow which cards are shown.
    """
    if not thesis_cards:
        st.info("No thesis cards — run a scan or load the latest results.")
        return

    # Apply sidebar filters
    filtered = thesis_cards[:]

    if min_confluence > 0:
        def _conf_int(card: Dict) -> int:
            raw = str(card.get("confluence", "0/4"))
            try:
                return int(raw.split("/")[0])
            except ValueError:
                return 0
        filtered = [c for c in filtered if _conf_int(c) >= min_confluence]

    if horizon_filter:
        filtered = [c for c in filtered
                    if any(h in (c.get("horizon") or "") for h in horizon_filter)]

    if account_filter:
        filtered = [c for c in filtered
                    if c.get("account_tag") in account_filter]

    if not filtered:
        st.warning("No picks match the current filters.")
        return

    st.caption("{} pick{} shown".format(len(filtered), "s" if len(filtered) != 1 else ""))

    # 2-column grid
    pairs = [filtered[i:i + 2] for i in range(0, len(filtered), 2)]
    for pair in pairs:
        left, right = st.columns(2)
        with left:
            render_signal_card(pair[0])
        with right:
            if len(pair) > 1:
                render_signal_card(pair[1])
