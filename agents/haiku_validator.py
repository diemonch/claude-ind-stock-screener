#!/usr/bin/env python3
"""
Haiku Validator — Phase 2, Step 2.
Contextual signal validation using claude-haiku-4-5-20251001.
Receives sector batches from sector_batcher, returns validated picks for Sonnet.
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
log = logging.getLogger("haiku_validator")

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = (
    "You are a quantitative signal validator for Indian equity markets.\n"
    "Your job is to identify whether technical signals are contextually\n"
    "actionable given current market conditions.\n\n"
    "You will receive a sector batch with week_context and market_condition.\n"
    "For each ticker, determine if the signal should be acted on this week.\n\n"
    "Downgrade to 'watch' if:\n"
    "- It is results_week and the sector is in active reporting cycle\n"
    "- 5 or more peers in the same batch show identical signals (sector-wide move)\n"
    "- Volume ratio pattern suggests a single block deal rather than broad accumulation\n"
    "- circuit_risk is true\n\n"
    "Return ONLY a JSON array. No explanation. No preamble. No markdown fences.\n"
    'Format: [{{"ticker": "X.NS", "validated": true, "confidence": "high", "reason": "..."}}]\n'
    "Reason must be one sentence maximum."
)


def validate_batch(batch: Dict, client, tracker: TokenTracker) -> List[Dict]:
    """
    Send one sector batch to Haiku for contextual validation.
    batch: dict with sector, week_context, market_condition, tickers.
    Returns list of validated ticker dicts (validated=True only).
    """
    sector      = batch["sector"]
    ticker_count = len(batch["tickers"])

    if tracker.is_over_budget(MODEL):
        log.warning("Haiku budget exhausted — skipping sector=%s", sector)
        return []

    log.info(
        "Validating sector=%s tickers=%d week=%s market=%s",
        sector, ticker_count, batch["week_context"], batch["market_condition"],
    )

    user_message = json.dumps(batch, indent=2)

    def _call() -> Optional[List[Dict]]:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1_024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        tokens_used = resp.usage.input_tokens + resp.usage.output_tokens
        tracker.debit(MODEL, tokens_used)

        raw     = resp.content[0].text.strip()
        parsed  = validate_json(raw)
        if not isinstance(parsed, list):
            log.error("Haiku returned non-list for sector=%s: %r", sector, raw[:200])
            return None
        return parsed

    result = retry_with_backoff(_call, MODEL)
    if result is None:
        log.error("Haiku validation failed for sector=%s — skipping", sector)
        return []

    validated = [t for t in result if t.get("validated") is True]
    log.info(
        "sector=%s validated=%d/%d",
        sector, len(validated), ticker_count,
    )
    return validated


def run_haiku_validator(
    batches: List[Dict],
    week_context: str = "normal",
    market_condition: str = "sideways",
) -> List[Dict]:
    """
    Validate all sector batches through Haiku.
    batches: output from sector_batcher.run_batcher.
    Returns flat list of all validated ticker dicts across all sectors.
    Saves to data/results/haiku_validated_YYYYMMDD.json.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error("ANTHROPIC_API_KEY not set — cannot run Haiku validation")
        return []

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    except ImportError:
        log.error("Missing: pip install anthropic")
        return []

    tracker      = TokenTracker()
    all_validated: List[Dict] = []

    for batch in batches:
        validated = validate_batch(batch, client, tracker)
        all_validated.extend(validated)

    log.info(
        "Haiku complete: %d validated picks from %d batches | tokens used: %s",
        len(all_validated), len(batches), tracker.used(MODEL),
    )
    log.info("Token summary: %s", tracker.summary())

    date_str = datetime.today().strftime("%Y%m%d")
    out_path = RESULTS_DIR / "haiku_validated_{}.json".format(date_str)
    with open(out_path, "w") as f:
        json.dump(all_validated, f, indent=2, default=str)
    log.info("Saved → %s", out_path)

    return all_validated


if __name__ == "__main__":
    date_str     = datetime.today().strftime("%Y%m%d")
    batches_file = RESULTS_DIR / "batches_{}.json".format(date_str)
    if not batches_file.exists():
        log.error("Run sector_batcher.py first — %s not found", batches_file)
        sys.exit(1)
    with open(batches_file) as f:
        batches = json.load(f)
    validated = run_haiku_validator(batches)
    print("\nValidated picks: {}".format(len(validated)))
    if validated:
        print("\nSample validated pick:")
        print(json.dumps(validated[0], indent=2))
