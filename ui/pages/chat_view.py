"""Chat/Analyser tab — context-aware Sonnet chat for India portfolio and screener."""

import os
from typing import Dict, List

import streamlit as st

MODEL = "claude-sonnet-4-6"

SYSTEM_TEMPLATE = """\
You are an expert equity analyst and portfolio advisor specialising in Indian markets (NSE/BSE).
You have full context of the user's current India portfolio, active screener picks, and market conditions.
Answer questions directly and specifically — reference actual tickers, prices, P&L figures, buy/sell zones, \
and signals from the data provided. Be concise and actionable.
Do not add generic disclaimers. Do not hallucinate prices or data not in the context.

{portfolio_context}

=== SCAN SUMMARY ===
Last Scan Date:  {scan_date}
Survivors:       {survivors_count} tickers passed all filters
Validated Picks: {validated_count}
Thesis Cards:    {thesis_count}
"""


def _build_system_prompt(
    portfolio_context: str,
    scan_date: str,
    survivors_count: int,
    validated_count: int,
    thesis_count: int,
) -> str:
    return SYSTEM_TEMPLATE.format(
        portfolio_context = portfolio_context,
        scan_date         = scan_date or "not yet run",
        survivors_count   = survivors_count,
        validated_count   = validated_count,
        thesis_count      = thesis_count,
    )


def _stream_response(client, system: str, messages: List[Dict]):
    """Generator that yields tokens from a streaming Sonnet call."""
    with client.messages.stream(
        model      = MODEL,
        max_tokens = 1024,
        system     = system,
        messages   = messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def render_chat_view(
    portfolio_context: str,
    registry: Dict,
    scan_date: str,
    survivors_count: int,
    validated_count: int,
    thesis_count: int,
) -> None:
    """
    Render the Chat/Analyser tab.
    portfolio_context: pre-built context string from portfolio_view.build_portfolio_context().
    """
    # ── Init session state ────────────────────────────────────────────────────
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []
    if "chat_system" not in st.session_state:
        st.session_state["chat_system"] = ""

    # Rebuild system prompt whenever context changes (scan or portfolio update)
    new_system = _build_system_prompt(
        portfolio_context = portfolio_context,
        scan_date         = scan_date,
        survivors_count   = survivors_count,
        validated_count   = validated_count,
        thesis_count      = thesis_count,
    )
    if new_system != st.session_state.get("chat_system"):
        st.session_state["chat_system"] = new_system

    # ── Header + controls ─────────────────────────────────────────────────────
    hdr, btn_col = st.columns([6, 1])
    with hdr:
        st.subheader("India Portfolio Analyser")
        st.caption("Powered by claude-sonnet-4-6 · Context: portfolio + registry + last scan")
    with btn_col:
        if st.button("Clear", key="chat_clear"):
            st.session_state["chat_history"] = []
            st.rerun()

    # ── API key check ─────────────────────────────────────────────────────────
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        st.error("ANTHROPIC_API_KEY not set — add it to your .env file.")
        return

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=60.0)
    except ImportError:
        st.error("Missing: pip install anthropic")
        return

    # ── Suggested prompts (shown only when history is empty) ──────────────────
    if not st.session_state["chat_history"]:
        st.markdown("**Try asking:**")
        suggestions = [
            "Which of my holdings are near their stop loss?",
            "Summarise the active screener picks for this week",
            "Which watchlist stocks have buy signals?",
            "What's the best accumulate opportunity right now?",
            "Give me a risk review of my current portfolio",
        ]
        cols = st.columns(len(suggestions))
        for col, s in zip(cols, suggestions):
            if col.button(s, key="suggest_{}".format(s[:20])):
                st.session_state["chat_history"].append({"role": "user", "content": s})
                st.rerun()

    # ── Chat history ──────────────────────────────────────────────────────────
    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # ── User input ────────────────────────────────────────────────────────────
    user_input = st.chat_input("Ask about your portfolio or the screener picks…")

    if user_input:
        st.session_state["chat_history"].append({"role": "user", "content": user_input})

        with st.chat_message("user"):
            st.markdown(user_input)

        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state["chat_history"]
        ]

        with st.chat_message("assistant"):
            try:
                response_text = st.write_stream(
                    _stream_response(client, st.session_state["chat_system"], messages)
                )
                st.session_state["chat_history"].append(
                    {"role": "assistant", "content": response_text}
                )
            except Exception as e:
                err = "Error calling Claude: {}".format(str(e))
                st.error(err)
                st.session_state["chat_history"].append(
                    {"role": "assistant", "content": err}
                )
