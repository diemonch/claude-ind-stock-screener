# AI Portfolio Terminal — India Module
## CLAUDE.md — Project Context for Claude Code

---

## Project Overview

This is a standalone Streamlit application for Indian equity market intelligence,
built as a companion to an existing US Portfolio Terminal. The two apps run
independently and will be connected via a landing page router in a future phase.

The India module screens the full Nifty 500 universe weekly using a 3-brain pipeline:
- **Python Brain** — quantitative screening, zero Claude tokens
- **Haiku Brain** — contextual signal validation, cheap and fast
- **Sonnet Brain** — deep-dive thesis generation, only on validated picks

The existing `scanner_india.py` is the foundation. Build around it, not over it.

---

## Repository Structure

```
portfolio-india/
│
├── CLAUDE.md                    ← you are here
├── .env                         ← ANTHROPIC_API_KEY (agents only, not Claude Code)
├── requirements.txt
│
├── app_india.py                 ← main Streamlit entry point
│
├── scanner/
│   ├── scanner_india.py         ← EXISTING — do not rewrite, extend only
│   ├── nifty_screener.py        ← NEW — 6-filter quantitative pipeline
│   ├── signal_engine.py         ← NEW — trend shift, buy/sell zones computation
│   └── sector_batcher.py        ← NEW — groups survivors into sector batches
│
├── agents/
│   ├── haiku_validator.py       ← NEW — Haiku contextual validation agent
│   ├── sonnet_analyst.py        ← NEW — Sonnet deep-dive thesis agent
│   └── agent_utils.py           ← NEW — shared retry logic, token tracking
│
├── data/
│   ├── cache/                   ← Parquet files (nifty500_raw_YYYYMMDD.parquet)
│   └── results/                 ← Weekly output JSON files
│
├── ui/
│   ├── components/
│   │   ├── signal_card.py       ← ticker card with signal + zones
│   │   ├── sector_heatmap.py    ← sector performance heatmap
│   │   └── candlestick_chart.py ← price chart with buy/sell zones overlaid
│   └── pages/
│       ├── screener_view.py     ← full 500-ticker funnel results
│       └── watchlist_view.py    ← shortlisted picks with thesis cards
│
└── scheduler/
    └── weekly_runner.py         ← Sunday 22:00 IST orchestrator
```

---

## Core Architecture — The Funnel

```
Nifty 500 (500 tickers)
  ↓ nifty_screener.py — 6 quantitative filters (Python only, 0 tokens)
50–80 survivors
  ↓ signal_engine.py — trend shift + buy/sell zone computation (Python only)
50–80 enriched signals
  ↓ sector_batcher.py — group by sector, max 8 tickers per batch
11–12 sector batches
  ↓ haiku_validator.py — contextual sanity check (claude-haiku-4-5-20251001)
15–20 validated picks
  ↓ sonnet_analyst.py — full thesis generation (claude-sonnet-4-6)
Thesis cards → Streamlit India UI
```

**Non-negotiable rule: Claude agents never receive raw ticker data.
They only receive pre-computed signals in structured JSON.**

---

## Signal Architecture

### Layer 1 — Quantitative Filters (nifty_screener.py)

All computed with `pandas-ta`. No Claude tokens.

```python
FILTERS = {
    "rsi_range": (35, 65),           # RSI(14) — not extended
    "trend": "ema20_above_ema50",    # EMA crossover confirmation
    "volume_min": 500_000,           # 20-day avg volume minimum
    "pe_ratio": "below_sector_median_x1.2",
    "roe_min": 12.0,                 # ROE > 12%
    "max_drawdown_52w": 40.0,        # 52-week drawdown < 40%
}
```

### Layer 1 — Signal Engine (signal_engine.py)

Computes three signal types per surviving ticker:

```python
# 1. Trend Shift Signal
{
    "trend_shift": "bullish_reversal | bearish_reversal | continuation | none",
    "trend_strength": "strong | moderate | weak",
    "indicators": ["ema_crossover", "macd_histogram_flip", "volume_confirmation"]
}

# 2. Buy/Sell Zones
{
    "buy_zone": [lower_price, upper_price],   # Fibonacci + support confluence
    "sell_zone": [lower_price, upper_price],  # Resistance + Bollinger upper
    "stop_loss": price,                       # Below buy zone support
    "risk_reward": float                      # (sell_zone_mid - entry) / (entry - stop)
}

# 3. Signal Confluence Score (0-4)
{
    "signal": "buy | sell | watch | avoid",
    "confluence_score": int,   # Count of confirming indicators
    "volume_ratio": float,     # Today vol / 20d avg vol
    "circuit_risk": bool       # Recent circuit breaker history flag
}
```

---

## Agent Specifications

### Haiku Validator (haiku_validator.py)

**Model:** `claude-haiku-4-5-20251001`
**Purpose:** Contextual sanity check — catches false signals rules cannot detect
**Token budget:** ~15K/week
**Runs:** Per sector batch (max 12 calls/week)

**System prompt pattern:**
```
You are a quantitative signal validator for Indian equity markets.
Validate whether technical signals are contextually actionable.
Week context will specify: results_week | budget_week | expiry_week | normal
Return ONLY valid JSON. No explanation. No preamble. No markdown.
```

**Input per batch:**
```json
{
  "sector": "renewable_energy",
  "week_context": "normal",
  "market_condition": "fii_buying | fii_selling | sideways",
  "tickers": [
    {
      "ticker": "SUZLON.NS",
      "trend_shift": "bullish_reversal",
      "trend_strength": "strong",
      "confluence_score": 3,
      "rsi": 42,
      "volume_ratio": 2.1,
      "circuit_risk": false
    }
  ]
}
```

**Output per ticker:**
```json
{
  "ticker": "SUZLON.NS",
  "validated": true,
  "confidence": "high | medium | low | watch",
  "reason": "one line max — no verbose explanation"
}
```

**Validation downgrade triggers:**
- Results week + sector in reporting cycle → downgrade to `watch`
- Identical signals across 5+ sector peers → flag as sector-wide move
- Volume spike is single block deal pattern → flag volume quality
- Circuit breaker history present → add risk flag

### Sonnet Analyst (sonnet_analyst.py)

**Model:** `claude-sonnet-4-6`
**Purpose:** Full investment thesis per validated pick
**Token budget:** ~30K/week
**Runs:** Once per week on final 15–20 validated picks

**Output structure per ticker:**
```json
{
  "ticker": "VATECH.NS",
  "company": "Va Tech Wabag",
  "sector": "Water Infrastructure",
  "trend": "Bullish reversal — EMA 20 crossed above EMA 50, MACD confirming",
  "buy_zone": [320, 335],
  "sell_zone": [410, 425],
  "stop_loss": 308,
  "risk_reward": 2.8,
  "signal": "BUY",
  "confluence": "3/4",
  "horizon": "swing_4_6_weeks | accumulate_6_18_months",
  "account_tag": "swing | sip_eligible | avoid",
  "thesis": "2-3 sentence max investment case",
  "risk": "1-2 sentence key risk",
  "circuit_flag": false
}
```

---

## Streamlit UI Specifications (app_india.py)

### Layout
- Dark theme consistent with existing US app aesthetic
- Sidebar: scan controls, week selector, market condition toggle
- Main area: two tabs — `📊 Screener` and `🎯 Shortlist`

### Screener Tab
- Funnel metrics: 500 → X survivors → Y validated → Z thesis cards
- Sector heatmap (performance by sector this week)
- Full survivors table with signal columns (sortable)

### Shortlist Tab
- Thesis cards — one per validated pick
- Each card shows: ticker, signal badge, buy zone, sell zone, stop loss, R:R, horizon tag, account tag, thesis snippet
- Candlestick chart on card expand — price with buy zone (green band) and sell zone (red band) overlaid using Plotly

### Sidebar Controls
```python
week_context = selectbox(["normal", "results_week", "budget_week", "expiry_week"])
market_condition = selectbox(["fii_buying", "fii_selling", "sideways"])
min_confluence = slider(0, 4, default=2)
horizon_filter = multiselect(["swing", "accumulate", "both"])
account_filter = multiselect(["swing_account", "sip_eligible"])
run_scan = button("▶ Run Weekly Scan")
```

---

## Environment & Dependencies

```python
# requirements.txt
streamlit>=1.35.0
pandas>=2.0.0
pandas-ta>=0.3.14b
yfinance>=0.2.40
plotly>=5.20.0
anthropic>=0.25.0
python-dotenv>=1.0.0
pyarrow>=14.0.0       # Parquet support
apscheduler>=3.10.0   # Sunday scheduler
```

```bash
# .env (agents use API key, Claude Code sessions do NOT)
ANTHROPIC_API_KEY=your_key_here
NIFTY500_TICKERS_CSV=data/nifty500_tickers.csv
CACHE_DIR=data/cache
RESULTS_DIR=data/results
```

---

## Token Budget — Hard Limits

```python
TOKEN_BUDGET = {
    "haiku_weekly_max": 20_000,    # Haiku validation pass
    "sonnet_weekly_max": 35_000,   # Sonnet deep-dive
    "total_india_weekly": 55_000,  # India module ceiling
}

# Agent must check budget before each call
# Graceful degradation: if Sonnet budget hit → skip narrative, keep JSON signal
# Never silently retry in a loop — exponential backoff max 3 attempts
```

---

## Coding Standards

- **Type hints** on all function signatures
- **Docstrings** on all public functions — one-line purpose + params
- **No hardcoded ticker lists** — always read from CSV or config
- **Structured logging** — every agent call logs: model, tokens_used, tickers_processed
- **Fail gracefully** — if a sector batch fails, skip it and log — never abort the full run
- **JSON output validation** — always validate agent JSON before parsing (try/except)
- **No f-strings in prompts** — use `.format()` or template strings for prompt construction

---

## What NOT to do

- ❌ Do not rewrite `scanner_india.py` — extend it or import from it
- ❌ Do not pass raw OHLCV data to any Claude agent
- ❌ Do not use `claude-sonnet-4-6` for validation — that's Haiku's job
- ❌ Do not run both US and India heavy scans simultaneously
- ❌ Do not store API keys anywhere except `.env`
- ❌ Do not use `streamlit.experimental_*` APIs — use stable APIs only
- ❌ Do not build multi-user auth — single user, local run only (this phase)

---

## Build Sequence (follow this order)

```
Phase 1 — Data Foundation
  1. nifty_screener.py — 6-filter pipeline on top of scanner_india.py
  2. signal_engine.py  — trend shift + zone computation
  3. sector_batcher.py — sector grouping utility

Phase 2 — Agent Layer
  4. agent_utils.py    — retry, token tracking, JSON validation
  5. haiku_validator.py
  6. sonnet_analyst.py

Phase 3 — UI
  7. signal_card.py    — Plotly candlestick + zone overlay
  8. sector_heatmap.py
  9. app_india.py      — main Streamlit shell

Phase 4 — Orchestration
  10. weekly_runner.py — full pipeline orchestrator + scheduler
```

---

## Current State

- `scanner_india.py` — EXISTS, working, do not touch internals
- All other files — TO BE BUILT in the sequence above
- US Portfolio Terminal — separate repo, do not import from or modify