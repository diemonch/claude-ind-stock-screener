"""
AI Portfolio Terminal — India Module
Run: streamlit run app_india.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

# ── Path setup (must happen before local imports) ──────────────────────────────
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))
sys.path.insert(0, str(ROOT_DIR / "scripts" / "agents"))
sys.path.insert(0, str(ROOT_DIR / "scanner"))
sys.path.insert(0, str(ROOT_DIR / "agents"))

DATA_DIR    = ROOT_DIR / "data"
RESULTS_DIR = DATA_DIR / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="India Portfolio Terminal",
    page_icon="🇮🇳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Dark theme CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .stApp { background-color: #0d1117; color: #e6edf3; }
  section[data-testid="stSidebar"] { background-color: #161b22; }
  .stMetric label { color: #8b949e !important; font-size: 0.78rem !important; }
  .stMetric [data-testid="stMetricValue"] { color: #e6edf3; font-family: monospace; }
  .stDataFrame { background-color: #161b22; }
  div[data-testid="stExpander"] { background-color: #161b22; border: 1px solid #30363d; }
  .stButton>button { background-color: #238636; color: #fff; border: none; }
  .stButton>button:hover { background-color: #2ea043; }
  h1, h2, h3 { color: #e6edf3; }
  .stTabs [data-baseweb="tab"] { color: #8b949e; }
  .stTabs [aria-selected="true"] { color: #58a6ff; border-bottom-color: #58a6ff; }
</style>
""", unsafe_allow_html=True)


# ── Session state init ─────────────────────────────────────────────────────────
def _init_state():
    defaults = {
        "survivors":       [],
        "filter_summary":  {},
        "validated":       [],
        "thesis_cards":    [],
        "universe_size":   500,
        "scan_date":       None,
        "pipeline_log":    [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Result loaders ─────────────────────────────────────────────────────────────
def _load_latest_results() -> bool:
    """Load the most recent thesis + signals JSON from data/results/. Returns True on success."""
    # Thesis cards
    thesis_files = sorted(RESULTS_DIR.glob("thesis_*.json"), reverse=True)
    if not thesis_files:
        st.warning("No thesis files found in data/results/")
        return False

    with open(thesis_files[0]) as f:
        st.session_state["thesis_cards"] = json.load(f)

    date_str = thesis_files[0].stem.replace("thesis_", "")
    st.session_state["scan_date"] = date_str

    # Signals (survivors)
    signals_file = RESULTS_DIR / "signals_{}.json".format(date_str)
    if signals_file.exists():
        with open(signals_file) as f:
            st.session_state["survivors"] = json.load(f)

    # Haiku validated
    haiku_file = RESULTS_DIR / "haiku_validated_{}.json".format(date_str)
    if haiku_file.exists():
        with open(haiku_file) as f:
            st.session_state["validated"] = json.load(f)

    # Filter summary
    screener_file = RESULTS_DIR / "screener_{}.json".format(date_str)
    if screener_file.exists():
        with open(screener_file) as f:
            data = json.load(f)
            st.session_state["filter_summary"]  = data.get("filter_summary", {})
            st.session_state["universe_size"]   = data.get("universe_size", 500)

    return True


def _run_pipeline(
    week_context: str,
    market_condition: str,
) -> None:
    """Run the full scan pipeline and populate session state."""
    from nifty_screener  import run_screener
    from signal_engine   import run_signal_engine
    from sector_batcher  import run_batcher
    from haiku_validator import run_haiku_validator
    from sonnet_analyst  import run_sonnet_analyst

    log = []

    with st.spinner("Step 1/5 — Fetching Nifty 500 data and applying filters..."):
        survivors, summary = run_screener()
        log.append("Screener: {} survivors from {} universe".format(len(survivors), 500))
        st.session_state["survivors"]      = survivors
        st.session_state["filter_summary"] = summary
        st.session_state["universe_size"]  = 500

    if not survivors:
        st.error("No survivors passed the screener filters.")
        return

    with st.spinner("Step 2/5 — Computing signals..."):
        enriched = run_signal_engine(survivors)
        log.append("Signal engine: {} enriched".format(len(enriched)))
        st.session_state["survivors"] = enriched

    with st.spinner("Step 3/5 — Grouping into sector batches..."):
        batches = run_batcher(enriched, week_context=week_context, market_condition=market_condition)
        log.append("Batcher: {} batches".format(len(batches)))

    with st.spinner("Step 4/5 — Haiku validation..."):
        validated = run_haiku_validator(batches)
        log.append("Haiku: {} validated picks".format(len(validated)))
        st.session_state["validated"] = validated

    if not validated:
        st.warning("Haiku validated 0 picks — falling back to top enriched tickers.")
        validated = sorted(enriched, key=lambda t: t.get("confluence_score", 0), reverse=True)[:20]

    with st.spinner("Step 5/5 — Sonnet thesis generation..."):
        cards = run_sonnet_analyst(validated)
        log.append("Sonnet: {} thesis cards".format(len(cards)))
        st.session_state["thesis_cards"] = cards

    st.session_state["scan_date"]   = datetime.today().strftime("%Y%m%d")
    st.session_state["pipeline_log"] = log
    st.success("Scan complete — {} thesis cards generated.".format(len(cards)))


# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🇮🇳 India Terminal")
    st.divider()

    week_context = st.selectbox(
        "Week Context",
        ["normal", "results_week", "budget_week", "expiry_week"],
        index=0,
    )
    market_condition = st.selectbox(
        "Market Condition",
        ["sideways", "fii_buying", "fii_selling"],
        index=0,
    )
    st.divider()

    min_confluence = st.slider("Min Confluence", 0, 4, 2)
    horizon_filter = st.multiselect(
        "Horizon",
        ["swing_4_6_weeks", "accumulate_6_18_months"],
        default=[],
    )
    account_filter = st.multiselect(
        "Account Tag",
        ["swing", "sip_eligible", "watchlist", "avoid"],
        default=[],
    )
    st.divider()

    run_col, load_col = st.columns(2)
    with run_col:
        run_scan = st.button("▶ Run Scan", use_container_width=True, type="primary")
    with load_col:
        load_latest = st.button("📂 Load", use_container_width=True)

    if st.session_state["scan_date"]:
        st.caption("Last scan: {}".format(st.session_state["scan_date"]))

    if st.session_state["pipeline_log"]:
        with st.expander("Pipeline Log", expanded=False):
            for line in st.session_state["pipeline_log"]:
                st.caption(line)


# ── Pipeline triggers ──────────────────────────────────────────────────────────
if run_scan:
    _run_pipeline(week_context, market_condition)

if load_latest:
    if _load_latest_results():
        st.success("Loaded latest results (scan {})".format(st.session_state["scan_date"]))
    else:
        st.error("No saved results found — run a scan first.")


# ── Main area ──────────────────────────────────────────────────────────────────
st.markdown("# AI Portfolio Terminal — India")

tab_screener, tab_shortlist = st.tabs(["📊 Screener", "🎯 Shortlist"])

with tab_screener:
    from ui.pages.screener_view import render_screener_view
    render_screener_view(
        survivors       = st.session_state["survivors"],
        filter_summary  = st.session_state["filter_summary"],
        universe_size   = st.session_state["universe_size"],
        validated_count = len(st.session_state["validated"]),
        thesis_count    = len(st.session_state["thesis_cards"]),
    )

with tab_shortlist:
    from ui.pages.watchlist_view import render_watchlist_view
    render_watchlist_view(
        thesis_cards    = st.session_state["thesis_cards"],
        min_confluence  = min_confluence,
        horizon_filter  = horizon_filter or None,
        account_filter  = account_filter or None,
    )
