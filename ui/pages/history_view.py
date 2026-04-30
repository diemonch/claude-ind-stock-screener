"""History tab — weekly change summary + rolling 6-week pick tracker."""

from typing import Dict, List

import pandas as pd
import streamlit as st


_STATUS_ICON = {
    "active":         "🟢",
    "dropped":        "🟠",
    "target_hit":     "🔵",
    "stopped_out":    "🔴",
    "horizon_expired":"⚫",
}


def _fmt_zone(zone) -> str:
    if isinstance(zone, list) and len(zone) == 2:
        return "₹{:,.0f}–{:,.0f}".format(*zone)
    return "—"


def _fmt_price(p) -> str:
    return "₹{:,.2f}".format(p) if p is not None else "—"


def _status_label(status: str) -> str:
    icon = _STATUS_ICON.get(status, "⚪")
    return "{} {}".format(icon, status.replace("_", " ").title())


def _change_block(entries: List[Dict], header: str, formatter) -> None:
    if not entries:
        return
    st.markdown("**{}**".format(header))
    for e in entries:
        st.markdown(formatter(e))


def render_history_view(registry: Dict, weekly_summary: Dict) -> None:
    """
    Render the History tab.
    registry: full picks_registry dict.
    weekly_summary: output of picks_registry.get_weekly_summary().
    """
    if not registry:
        st.info("No pick history yet — run a scan to start tracking.")
        return

    scan_date = weekly_summary.get("scan_date", "")
    new       = weekly_summary.get("new",     [])
    reentry   = weekly_summary.get("reentry", [])
    dropped   = weekly_summary.get("dropped", [])
    exits     = weekly_summary.get("exits",   [])
    active    = weekly_summary.get("active",  [])

    # ── Summary metrics ───────────────────────────────────────────────────────
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Active",       len(active))
    c2.metric("New",          len(new),
              delta="+{}".format(len(new)) if new else None)
    c3.metric("Re-entries",   len(reentry),
              delta="+{}".format(len(reentry)) if reentry else None)
    c4.metric("Dropped",      len(dropped),
              delta="-{}".format(len(dropped)) if dropped else None,
              delta_color="inverse")
    c5.metric("Exited",       len(exits),
              delta=str(len(exits)) if exits else None,
              delta_color="off")

    st.divider()

    # ── This week's changes ───────────────────────────────────────────────────
    st.subheader("This Week's Changes  {}".format("({})".format(scan_date) if scan_date else ""))

    if not any([new, reentry, dropped, exits]):
        st.caption("No changes this week — all active picks continued.")
    else:
        _change_block(
            new, "New entries",
            lambda e: "- **{ticker}** {company} — {signal} | Buy {bz} | R:R {rr}x | {horizon}".format(
                ticker=e["ticker"], company=e.get("company", ""),
                signal=e.get("signal", "—"),
                bz=_fmt_zone(e.get("buy_zone")),
                rr=e.get("risk_reward", "—"),
                horizon=(e.get("horizon") or "").replace("_", " "),
            ),
        )

        _change_block(
            reentry, "Re-entries  *(dropped previously, now back)*",
            lambda e: "- **{ticker}** {company} — re-entry #{n} | New buy zone {bz} | First seen {fs}".format(
                ticker=e["ticker"], company=e.get("company", ""),
                n=e.get("reentry_count", 1),
                bz=_fmt_zone(e.get("buy_zone")),
                fs=e.get("first_seen", "—"),
            ),
        )

        _change_block(
            dropped, "Dropped  *(not in new scan — screener filtered out)*",
            lambda e: "- **{ticker}** {company} | Last price {lp} | Was in scan since {fs}".format(
                ticker=e["ticker"], company=e.get("company", ""),
                lp=_fmt_price(e.get("last_price")),
                fs=e.get("first_seen", "—"),
            ),
        )

        _change_block(
            exits, "Exited  *(target hit / stopped out / horizon expired)*",
            lambda e: "- **{ticker}** {company} — **{reason}** at {ep} | Buy zone was {bz}".format(
                ticker=e["ticker"], company=e.get("company", ""),
                reason=(e.get("exit_reason") or "—").upper(),
                ep=_fmt_price(e.get("exit_price")),
                bz=_fmt_zone(e.get("buy_zone")),
            ),
        )

    st.divider()

    # ── Rolling 6-week tracker table ──────────────────────────────────────────
    st.subheader("Rolling 6-Week Tracker")

    all_entries = sorted(
        registry.values(),
        key=lambda e: (e["status"] != "active", e.get("last_seen", "")),
        reverse=False,
    )

    rows = []
    for e in all_entries:
        rows.append({
            "Status":      _status_label(e["status"]),
            "Ticker":      e["ticker"],
            "Company":     e.get("company", ""),
            "Signal":      e.get("signal", "—"),
            "Conf":        e.get("confluence", "—"),
            "Buy Zone":    _fmt_zone(e.get("buy_zone")),
            "Sell Zone":   _fmt_zone(e.get("sell_zone")),
            "Stop":        _fmt_price(e.get("stop_loss")),
            "R:R":         e.get("risk_reward", "—"),
            "Last Price":  _fmt_price(e.get("last_price")),
            "First Seen":  e.get("first_seen", "—"),
            "Last Seen":   e.get("last_seen", "—"),
            "# Scans":     len(e.get("scans", [])),
            "Re-entries":  e.get("reentry_count", 0),
            "Exit Reason": e.get("exit_reason") or "—",
            "Horizon End": e.get("horizon_end", "—"),
        })

    if not rows:
        st.info("No entries in registry.")
        return

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # ── Archive note ──────────────────────────────────────────────────────────
    st.caption(
        "Picks older than 6 weeks (non-active) are automatically moved to picks_archive.json. "
        "Re-entries reset the 6-week clock."
    )
