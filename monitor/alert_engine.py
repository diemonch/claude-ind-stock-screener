"""
Alert engine — threshold checks, dedup, and delivery.
No AI calls. Pure price comparison against SL / sell zone levels.
"""

import json
import subprocess
import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("alert_engine")

ROOT_DIR        = Path(__file__).parent.parent
ALERT_STATE_FILE = ROOT_DIR / "data" / "results" / "alert_state.json"

# Alert types — ordered by severity
ALERT_TARGET   = "target_hit"
ALERT_DANGER   = "danger_zone"     # within 5% of SL
ALERT_SL       = "sl_breach"

DANGER_BUFFER  = 0.05              # 5% above SL triggers danger zone


# ── Alert state (dedup) ────────────────────────────────────────────────────────

def _load_state() -> Dict:
    if not ALERT_STATE_FILE.exists():
        return {}
    with open(ALERT_STATE_FILE) as f:
        return json.load(f)


def _save_state(state: Dict) -> None:
    ALERT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERT_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _already_sent(state: Dict, ticker: str, alert_type: str) -> bool:
    today = str(date.today())
    entry = state.get(ticker, {})
    return entry.get("date") == today and alert_type in entry.get("sent", [])


def _mark_sent(state: Dict, ticker: str, alert_type: str) -> Dict:
    today = str(date.today())
    if ticker not in state or state[ticker].get("date") != today:
        state[ticker] = {"date": today, "sent": []}
    if alert_type not in state[ticker]["sent"]:
        state[ticker]["sent"].append(alert_type)
    return state


# ── Delivery ───────────────────────────────────────────────────────────────────

def _mac_notify(title: str, message: str) -> None:
    try:
        script = 'display notification "{}" with title "{}" sound name "Sosumi"'.format(
            message.replace('"', "'"), title.replace('"', "'")
        )
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
    except Exception:
        pass  # silent fail — terminal output is the fallback


def _deliver(title: str, message: str, level: str) -> None:
    prefix = {"sl_breach": "🔴", "danger_zone": "🟠", "target_hit": "🔵"}.get(level, "⚪")
    log.warning("%s  %s — %s", prefix, title, message)
    print("\n{} {}".format(prefix, title))
    print("   {}".format(message))
    _mac_notify("{} {}".format(prefix, title), message)


# ── Threshold checks ───────────────────────────────────────────────────────────

def check_position(
    ticker: str,
    current_price: float,
    avg_cost: float,
    stop_loss: Optional[float],
    sell_zone: Optional[List[float]],
    state: Dict,
) -> Dict:
    """
    Check one position against its thresholds.
    Fires alerts that haven't been sent today.
    Returns updated state.
    """
    if not current_price:
        return state

    target = sell_zone[0] if isinstance(sell_zone, list) and sell_zone else None

    # ── Target hit ────────────────────────────────────────────────────────────
    if target and current_price >= target:
        if not _already_sent(state, ticker, ALERT_TARGET):
            gain_pct = (current_price - avg_cost) / avg_cost * 100
            _deliver(
                "{} — TARGET HIT".format(ticker),
                "CMP ₹{:.2f} reached sell zone ₹{:.0f}–{:.0f}. P&L {:+.1f}%. Consider taking profit.".format(
                    current_price,
                    sell_zone[0], sell_zone[1] if len(sell_zone) > 1 else sell_zone[0],
                    gain_pct,
                ),
                ALERT_TARGET,
            )
            state = _mark_sent(state, ticker, ALERT_TARGET)

    # ── SL breach ─────────────────────────────────────────────────────────────
    if stop_loss and current_price <= stop_loss:
        if not _already_sent(state, ticker, ALERT_SL):
            loss_pct = (current_price - avg_cost) / avg_cost * 100
            _deliver(
                "{} — STOP LOSS BREACHED".format(ticker),
                "CMP ₹{:.2f} below SL ₹{:.2f}. P&L {:+.1f}%. EXIT NOW.".format(
                    current_price, stop_loss, loss_pct,
                ),
                ALERT_SL,
            )
            state = _mark_sent(state, ticker, ALERT_SL)

    # ── Danger zone (within 5% of SL) ─────────────────────────────────────────
    elif stop_loss and current_price <= stop_loss * (1 + DANGER_BUFFER):
        if not _already_sent(state, ticker, ALERT_DANGER):
            gap_pct = (current_price - stop_loss) / stop_loss * 100
            _deliver(
                "{} — DANGER ZONE".format(ticker),
                "CMP ₹{:.2f} is {:.1f}% above SL ₹{:.2f}. Watch closely.".format(
                    current_price, gap_pct, stop_loss,
                ),
                ALERT_DANGER,
            )
            state = _mark_sent(state, ticker, ALERT_DANGER)

    return state


def run_checks(positions: List[Dict]) -> None:
    """
    Run threshold checks for all positions.
    positions: list of dicts with keys: ticker, current_price, avg_cost,
               stop_loss (optional), sell_zone (optional).
    """
    state = _load_state()
    for p in positions:
        state = check_position(
            ticker        = p["ticker"],
            current_price = p.get("current_price", 0.0),
            avg_cost      = p.get("avg_cost", 0.0),
            stop_loss     = p.get("stop_loss"),
            sell_zone     = p.get("sell_zone"),
            state         = state,
        )
    _save_state(state)
