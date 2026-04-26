#!/usr/bin/env python3
"""
Sonnet Analyst — Phase 2, Step 3.
Full investment thesis generation using claude-sonnet-4-6.
Receives validated picks from Haiku, returns structured thesis cards.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR    = Path(__file__).parent.parent
AGENTS_DIR  = Path(__file__).parent
DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"

sys.path.insert(0, str(AGENTS_DIR))
from agent_utils import TokenTracker, retry_with_backoff, validate_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sonnet_analyst")

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "You are a senior equity analyst specialising in Indian markets.\n"
    "You write concise, actionable investment thesis cards for swing and\n"
    "accumulate positions. You understand NSE/BSE market structure,\n"
    "results seasons, FII/DII dynamics, and Indian sector cycles.\n\n"
    "Return ONLY a JSON array of thesis cards. No preamble. No markdown.\n"
    "Each card must follow the exact schema provided. Thesis max 3 sentences.\n"
    "Risk max 2 sentences. Be specific — avoid generic statements."
)

# Exact output schema enforced in the prompt
THESIS_SCHEMA = (
    "[\n"
    "  {{\n"
    '    "ticker": "VATECH.NS",\n'
    '    "company": "Va Tech Wabag",\n'
    '    "sector": "Water Infrastructure",\n'
    '    "trend": "one line trend description",\n'
    '    "buy_zone": [320, 335],\n'
    '    "sell_zone": [410, 425],\n'
    '    "stop_loss": 308,\n'
    '    "risk_reward": 2.8,\n'
    '    "signal": "BUY",\n'
    '    "confluence": "3/4",\n'
    '    "horizon": "swing_4_6_weeks",\n'
    '    "account_tag": "swing",\n'
    '    "thesis": "2-3 sentence investment case",\n'
    '    "risk": "1-2 sentence key risk",\n'
    '    "circuit_flag": false\n'
    "  }}\n"
    "]"
)

# Fields from the full signal payload passed to Sonnet
ANALYST_FIELDS = [
    "ticker", "company", "sector",
    "trend_shift", "trend_strength", "indicators",
    "buy_zone", "sell_zone", "stop_loss", "risk_reward",
    "signal", "confluence_score", "rsi", "volume_ratio",
    "circuit_risk", "fwd_pe", "roe_pct", "net_margin_pct",
    "rev_growth_pct", "beta", "high_52w", "low_52w",
]

REQUIRED_FIELDS = {
    "ticker", "company", "sector", "trend", "buy_zone", "sell_zone",
    "stop_loss", "risk_reward", "signal", "confluence", "horizon",
    "account_tag", "thesis", "risk", "circuit_flag",
}


def _build_user_message(validated_tickers: List[Dict]) -> str:
    """Build the user message for Sonnet containing signal payloads + schema."""
    payloads = [{k: t.get(k) for k in ANALYST_FIELDS} for t in validated_tickers]
    return (
        "Generate thesis cards for these {n} validated Indian equity picks.\n\n"
        "Signal data:\n{data}\n\n"
        "Output schema (return a JSON array matching this exactly):\n{schema}"
    ).format(
        n=len(payloads),
        data=json.dumps(payloads, indent=2),
        schema=THESIS_SCHEMA,
    )


def _validate_thesis_card(card: Dict) -> bool:
    """Return True if a thesis card has all required fields."""
    missing = REQUIRED_FIELDS - set(card.keys())
    if missing:
        log.warning("Thesis card missing fields %s for ticker=%s", missing, card.get("ticker"))
        return False
    return True


def run_sonnet_analyst(validated_tickers: List[Dict]) -> List[Dict]:
    """
    Generate thesis cards for all validated picks in a single Sonnet call.
    validated_tickers: output from haiku_validator.run_haiku_validator.
    Saves to data/results/thesis_YYYYMMDD.json. Returns list of thesis cards.
    """
    if not validated_tickers:
        log.warning("No validated tickers — skipping Sonnet call")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot run Sonnet analysis")
        return []

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=120.0)
    except ImportError:
        log.error("Missing: pip install anthropic")
        return []

    tracker      = TokenTracker()
    user_message = _build_user_message(validated_tickers)

    if tracker.is_over_budget(MODEL):
        log.warning("Sonnet budget exhausted — skipping thesis generation")
        return []

    log.info("Calling Sonnet for %d validated picks...", len(validated_tickers))

    def _call() -> Optional[List[Dict]]:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8_192,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        tokens_used = resp.usage.input_tokens + resp.usage.output_tokens
        tracker.debit(MODEL, tokens_used)

        raw    = resp.content[0].text.strip()
        parsed = validate_json(raw)
        if not isinstance(parsed, list):
            log.error("Sonnet returned non-list: %r", raw[:200])
            return None
        return parsed

    result = retry_with_backoff(_call, MODEL)
    if result is None:
        log.error("Sonnet analysis failed — no thesis cards generated")
        return []

    thesis_cards = [c for c in result if _validate_thesis_card(c)]
    log.info(
        "Sonnet complete: %d thesis cards generated | tokens used: %d",
        len(thesis_cards), tracker.used(MODEL),
    )
    log.info("Token summary: %s", tracker.summary())

    date_str = datetime.today().strftime("%Y%m%d")
    out_path = RESULTS_DIR / "thesis_{}.json".format(date_str)
    with open(out_path, "w") as f:
        json.dump(thesis_cards, f, indent=2, default=str)
    log.info("Saved → %s", out_path)

    return thesis_cards


if __name__ == "__main__":
    date_str       = datetime.today().strftime("%Y%m%d")
    validated_file = RESULTS_DIR / "haiku_validated_{}.json".format(date_str)

    # Fall back to signals file if no Haiku output (allows standalone testing)
    if not validated_file.exists():
        signals_file = RESULTS_DIR / "signals_{}.json".format(date_str)
        if not signals_file.exists():
            log.error("No validated or signals file for today — run pipeline first")
            sys.exit(1)
        log.warning("No Haiku output found — using raw signals for test")
        with open(signals_file) as f:
            validated = json.load(f)
    else:
        with open(validated_file) as f:
            validated = json.load(f)

    cards = run_sonnet_analyst(validated)
    print("\nThesis cards: {}".format(len(cards)))
    if cards:
        print("\nSample thesis card:")
        print(json.dumps(cards[0], indent=2))
