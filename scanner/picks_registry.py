"""
Picks Registry — persistent cross-scan tracker for thesis cards.
Tracks status, re-entries, price validation, and 6-week rolling history.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("picks_registry")

ROOT_DIR      = Path(__file__).parent.parent
RESULTS_DIR   = ROOT_DIR / "data" / "results"
REGISTRY_FILE = RESULTS_DIR / "picks_registry.json"
ARCHIVE_FILE  = RESULTS_DIR / "picks_archive.json"

TRACKING_WEEKS = 6

STATUS_ACTIVE  = "active"
STATUS_DROPPED = "dropped"
STATUS_TARGET  = "target_hit"
STATUS_STOPPED = "stopped_out"
STATUS_EXPIRED = "horizon_expired"


def _today() -> str:
    return datetime.today().strftime("%Y%m%d")


def _to_dt(date_str: str) -> datetime:
    return datetime.strptime(date_str, "%Y%m%d")


def _to_str(dt: datetime) -> str:
    return dt.strftime("%Y%m%d")


def _parse_horizon_weeks(horizon: str) -> int:
    """Map horizon label to approximate week count."""
    h = (horizon or "").lower()
    if "4_6" in h:    return 6
    if "8_12" in h:   return 12
    if "6_18" in h:   return 26
    if "month" in h:  return 26
    parts = [p for p in h.split("_") if p.isdigit()]
    return int(max(parts, key=int)) if parts else 6


def load_registry() -> Dict:
    if not REGISTRY_FILE.exists():
        return {}
    with open(REGISTRY_FILE) as f:
        return json.load(f)


def save_registry(registry: Dict) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(REGISTRY_FILE, "w") as f:
        json.dump(registry, f, indent=2, default=str)


def _load_archive() -> List[Dict]:
    if not ARCHIVE_FILE.exists():
        return []
    with open(ARCHIVE_FILE) as f:
        return json.load(f)


def _save_archive(archive: List[Dict]) -> None:
    with open(ARCHIVE_FILE, "w") as f:
        json.dump(archive, f, indent=2, default=str)


def _archive_stale(registry: Dict) -> Dict:
    """Move non-active entries older than TRACKING_WEEKS into the archive file."""
    cutoff     = datetime.today() - timedelta(weeks=TRACKING_WEEKS)
    to_archive = []
    keep       = {}

    for ticker, entry in registry.items():
        last_seen_dt = _to_dt(entry["last_seen"])
        if last_seen_dt < cutoff and entry["status"] != STATUS_ACTIVE:
            to_archive.append(entry)
        else:
            keep[ticker] = entry

    if to_archive:
        archive = _load_archive()
        archive.extend(to_archive)
        _save_archive(archive)
        log.info("Archived %d stale picks", len(to_archive))

    return keep


def _make_entry(card: Dict, scan_date: str, is_reentry: bool = False, reentry_count: int = 0, first_seen: Optional[str] = None) -> Dict:
    horizon_weeks = _parse_horizon_weeks(card.get("horizon", ""))
    scan_dt       = _to_dt(scan_date)
    return {
        "ticker":        card["ticker"],
        "company":       card.get("company", ""),
        "sector":        card.get("sector", ""),
        "first_seen":    first_seen or scan_date,
        "last_seen":     scan_date,
        "reentry_count": reentry_count,
        "is_reentry":    is_reentry,
        "scans":         [scan_date],
        "status":        STATUS_ACTIVE,
        "buy_zone":      card.get("buy_zone"),
        "sell_zone":     card.get("sell_zone"),
        "stop_loss":     card.get("stop_loss"),
        "risk_reward":   card.get("risk_reward"),
        "signal":        card.get("signal"),
        "confluence":    card.get("confluence"),
        "horizon":       card.get("horizon"),
        "account_tag":   card.get("account_tag"),
        "thesis":        card.get("thesis"),
        "risk":          card.get("risk"),
        "circuit_flag":  card.get("circuit_flag", False),
        "last_price":    None,
        "exit_reason":   None,
        "exit_date":     None,
        "exit_price":    None,
        "horizon_end":   _to_str(scan_dt + timedelta(weeks=horizon_weeks)),
        "tracking_end":  _to_str(scan_dt + timedelta(weeks=TRACKING_WEEKS)),
    }


def update_registry(
    new_thesis_cards: List[Dict],
    scan_date: Optional[str] = None,
) -> Tuple[Dict, Dict]:
    """
    Merge new scan results into the persistent registry.
    Returns (updated_registry, weekly_summary).
    """
    scan_date   = scan_date or _today()
    registry    = load_registry()
    new_tickers = {c["ticker"]: c for c in new_thesis_cards}
    summary     = {"new": [], "reentry": [], "dropped": [], "continued": []}

    # Step 1 — mark active picks absent from new scan as dropped
    for ticker, entry in registry.items():
        if entry["status"] == STATUS_ACTIVE and ticker not in new_tickers:
            entry["status"]      = STATUS_DROPPED
            entry["exit_date"]   = scan_date
            entry["exit_reason"] = "dropped — not in new scan"
            summary["dropped"].append(ticker)
            log.info("Dropped: %s", ticker)

    # Step 2 — process new scan picks
    for ticker, card in new_tickers.items():
        if ticker not in registry:
            registry[ticker] = _make_entry(card, scan_date)
            summary["new"].append(ticker)
            log.info("New pick: %s", ticker)

        elif registry[ticker]["status"] == STATUS_DROPPED:
            # Re-entry — clock resets, zones update, history preserved
            old   = registry[ticker]
            count = old.get("reentry_count", 0) + 1
            new_e = _make_entry(card, scan_date, is_reentry=True, reentry_count=count, first_seen=old["first_seen"])
            new_e["scans"] = old["scans"] + [scan_date]
            registry[ticker] = new_e
            summary["reentry"].append(ticker)
            log.info("Re-entry: %s (count=%d)", ticker, count)

        else:
            # Still active — refresh zones and thesis from new scan
            entry = registry[ticker]
            entry["last_seen"]   = scan_date
            entry["scans"]       = entry.get("scans", []) + [scan_date]
            entry["buy_zone"]    = card.get("buy_zone")
            entry["sell_zone"]   = card.get("sell_zone")
            entry["stop_loss"]   = card.get("stop_loss")
            entry["risk_reward"] = card.get("risk_reward")
            entry["signal"]      = card.get("signal")
            entry["confluence"]  = card.get("confluence")
            entry["thesis"]      = card.get("thesis")
            horizon_weeks        = _parse_horizon_weeks(card.get("horizon", ""))
            scan_dt              = _to_dt(scan_date)
            entry["horizon_end"] = _to_str(scan_dt + timedelta(weeks=horizon_weeks))
            entry["tracking_end"]= _to_str(scan_dt + timedelta(weeks=TRACKING_WEEKS))
            summary["continued"].append(ticker)

    registry = _archive_stale(registry)
    save_registry(registry)

    log.info(
        "Registry updated — new=%d reentry=%d dropped=%d continued=%d",
        len(summary["new"]), len(summary["reentry"]),
        len(summary["dropped"]), len(summary["continued"]),
    )
    return registry, summary


def validate_prices(registry: Dict) -> Dict:
    """
    Fetch current prices for all active picks and update stop/target/expiry status.
    Saves registry after validation.
    """
    active_tickers = [t for t, e in registry.items() if e["status"] == STATUS_ACTIVE]
    if not active_tickers:
        return registry

    try:
        import yfinance as yf
        today_str = _today()

        for ticker in active_tickers:
            entry = registry[ticker]
            try:
                hist = yf.Ticker(ticker).history(period="2d")
                if hist.empty:
                    continue
                price = float(hist["Close"].iloc[-1])
                entry["last_price"] = round(price, 2)
            except Exception:
                price = None

            # Horizon expiry check
            if today_str > entry.get("horizon_end", "99991231"):
                entry["status"]      = STATUS_EXPIRED
                entry["exit_date"]   = today_str
                entry["exit_reason"] = "horizon expired"
                entry["exit_price"]  = entry.get("last_price")
                continue

            if price is None:
                continue

            stop = entry.get("stop_loss")
            if stop and price <= stop:
                entry["status"]      = STATUS_STOPPED
                entry["exit_date"]   = today_str
                entry["exit_reason"] = "stopped out"
                entry["exit_price"]  = round(price, 2)
                continue

            sell_zone = entry.get("sell_zone")
            if sell_zone and isinstance(sell_zone, list) and price >= sell_zone[0]:
                entry["status"]      = STATUS_TARGET
                entry["exit_date"]   = today_str
                entry["exit_reason"] = "target hit"
                entry["exit_price"]  = round(price, 2)

    except ImportError:
        log.warning("yfinance not available — skipping price validation")

    save_registry(registry)
    return registry


def get_weekly_summary(registry: Dict, scan_date: Optional[str] = None) -> Dict:
    """Build a summary dict of this week's changes for the History tab."""
    scan_date = scan_date or _today()
    return {
        "scan_date": scan_date,
        "new":     [e for e in registry.values() if e["first_seen"] == scan_date and not e["is_reentry"]],
        "reentry": [e for e in registry.values() if e.get("is_reentry") and e["last_seen"] == scan_date],
        "dropped": [e for e in registry.values() if e["status"] == STATUS_DROPPED  and e.get("exit_date") == scan_date],
        "exits":   [e for e in registry.values() if e["status"] in (STATUS_TARGET, STATUS_STOPPED, STATUS_EXPIRED) and e.get("exit_date") == scan_date],
        "active":  [e for e in registry.values() if e["status"] == STATUS_ACTIVE],
    }
