#!/usr/bin/env python3
"""
Agent utilities — Phase 2, Step 1.
Shared token tracking, retry logic, and JSON validation for all agents.
"""

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR    = Path(__file__).parent.parent
DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("agent_utils")

TOKEN_BUDGET: Dict[str, int] = {
    "claude-haiku-4-5-20251001": 30_000,
    "claude-sonnet-4-6":         35_000,
}

T = TypeVar("T")


class TokenTracker:
    """Tracks weekly token usage per model against hard budget limits."""

    def __init__(self) -> None:
        week_str      = datetime.today().strftime("%Y_W%W")
        self._path    = RESULTS_DIR / "token_usage_{}.json".format(week_str)
        self._data    = self._load()

    def _load(self) -> Dict[str, int]:
        if self._path.exists():
            try:
                with open(self._path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save(self) -> None:
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def debit(self, model: str, tokens: int) -> None:
        """Record tokens used for a model call."""
        self._data[model] = self._data.get(model, 0) + tokens
        self._save()
        log.info("Token debit: model=%s tokens=%d used_total=%d budget=%d",
                 model, tokens, self._data[model], TOKEN_BUDGET.get(model, 0))

    def used(self, model: str) -> int:
        """Return total tokens used for model this week."""
        return self._data.get(model, 0)

    def remaining(self, model: str) -> int:
        """Return remaining token budget for model this week."""
        budget = TOKEN_BUDGET.get(model, 0)
        return max(0, budget - self.used(model))

    def is_over_budget(self, model: str) -> bool:
        """Return True if model has exceeded its weekly token budget."""
        return self.used(model) >= TOKEN_BUDGET.get(model, 0)

    def summary(self) -> Dict[str, Dict]:
        """Return usage + remaining for all tracked models."""
        result = {}
        for model, budget in TOKEN_BUDGET.items():
            used = self.used(model)
            result[model] = {
                "used":      used,
                "budget":    budget,
                "remaining": max(0, budget - used),
                "pct":       round(used / budget * 100, 1) if budget else 0,
            }
        return result


def retry_with_backoff(
    fn: Callable[[], T],
    model: str,
    max_attempts: int = 3,
    base_delay: float = 2.0,
) -> Optional[T]:
    """
    Call fn() up to max_attempts times with exponential backoff.
    fn: zero-argument callable wrapping an agent API call.
    Returns fn() result or None on final failure — never raises.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            wait = base_delay ** attempt
            if attempt < max_attempts:
                log.warning(
                    "Attempt %d/%d failed for %s: %s — retrying in %.0fs",
                    attempt, max_attempts, model, exc, wait,
                )
                time.sleep(wait)
            else:
                log.error(
                    "All %d attempts failed for %s: %s",
                    max_attempts, model, exc,
                )
    return None


def validate_json(raw: str) -> Optional[Any]:
    """
    Parse a JSON string from a Claude response.
    Strips markdown code fences if present. Returns parsed object or None.
    """
    if not raw:
        return None
    # Strip markdown fences: ```json ... ``` or ``` ... ```
    stripped = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    stripped = re.sub(r"\s*```$", "", stripped.strip())
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        log.error("JSON parse failed: %s | raw[:200]=%r", exc, raw[:200])
        return None
