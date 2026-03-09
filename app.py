"""
app.py
Streamlit application for the QS Rankings Chatbot.

Run:
    streamlit run app.py
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="QS Rankings Explorer",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = Path("data/processed/qs_rankings.db")
CHROMA_DIR = Path("data/chroma_db")

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def load_data() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT * FROM v_full_rankings ORDER BY rank", conn)
    conn.close()
    return df


df_all = load_data()
data_available = not df_all.empty

# ---------------------------------------------------------------------------
# Sidebar (minimal - just title and stats)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🎓 QS Rankings")
    st.caption("World University Rankings Chatbot")
    st.markdown("---")

    # ── Stats only ───────────────────────────────────────────────────────────
    if data_available:
        st.metric("Universities", f"{len(df_all):,}")
        st.metric("Countries", f"{df_all['country'].nunique()}")
        st.markdown("---")

    # ── LLM Provider (compact) ────────────────────────────────────────────────
    llm_choice = st.radio(
        "LLM Provider",
        ["Groq (Free)", "OpenAI"],
        index=0,
        horizontal=True,
    )
    import os
    if "OpenAI" in llm_choice:
        os.environ["LLM_PROVIDER"] = "openai"
    else:
        os.environ["LLM_PROVIDER"] = "groq"

    st.markdown("---")

    # ── Admin Actions (collapsed by default) ─────────────────────────────────
    with st.expander("⚙️ Admin Tools"):
        if st.button("▶ Run Scraper", type="primary", use_container_width=True):
            with st.spinner("Running scraper… this can take 15–30 min"):
                result = subprocess.run(
                    [sys.executable, "scraper.py"],
                    capture_output=True, text=True,
                )
            if result.returncode == 0:
                with st.spinner("Running data pipeline…"):
                    subprocess.run([sys.executable, "pipeline.py"], capture_output=True)
                st.cache_data.clear()
                st.success("Scraping complete! Refreshing…")
                st.rerun()
            else:
                st.error(f"Scraper error:\n{result.stderr[:800]}")

        if st.button("🔄 Rebuild Embeddings", use_container_width=True):
            with st.spinner("Embedding universities into ChromaDB…"):
                result = subprocess.run(
                    [sys.executable, "embedder.py"],
                    capture_output=True, text=True,
                )
            if result.returncode == 0:
                st.success("Vector store rebuilt!")
            else:
                st.error(f"Embedder error:\n{result.stderr[:600]}")

    if not data_available:
        st.warning("No data yet. Use Admin Tools → Run Scraper")


# ---------------------------------------------------------------------------
# Main area - Chatbot focused
# ---------------------------------------------------------------------------
st.title("QS World University Rankings Chatbot")

if not data_available:
    st.info(
        "No ranking data found. Use **Admin Tools → Run Scraper** in the sidebar to "
        "download the rankings."
    )
else:
    # ── Quick Stats Bar ─────────────────────────────────────────────────────
    st.markdown("### Quick Stats")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Universities", f"{len(df_all):,}")
    with col2:
        st.metric("Countries", f"{df_all['country'].nunique()}")
    with col3:
        top100 = df_all[df_all['rank'] <= 100]
        st.metric("Top 100", f"{len(top100)}")
    with col4:
        avg_score = df_all['overall_score'].dropna().mean()
        st.metric("Avg Score", f"{avg_score:.1f}" if avg_score else "N/A")

    # ── Data Table ───────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("📋 View Full Data Table", expanded=False):
        st.subheader(f"All Universities — {len(df_all)} total")

        display_cols = [
            c for c in [
                "rank", "university_name", "country", "continent",
                "overall_score", "academic_reputation", "employer_reputation",
                "citations_per_faculty", "research_discovery",
                "learning_experience", "employability", "sustainability",
            ]
            if c in df_all.columns
        ]

        col_config = {
            "rank": st.column_config.NumberColumn("Rank", format="%d"),
            "university_name": st.column_config.TextColumn("University"),
            "overall_score": st.column_config.ProgressColumn(
                "Overall Score", min_value=0, max_value=100, format="%.1f"
            ),
            "academic_reputation": st.column_config.NumberColumn("Acad. Rep.", format="%.1f"),
            "employer_reputation": st.column_config.NumberColumn("Empl. Rep.", format="%.1f"),
            "citations_per_faculty": st.column_config.NumberColumn("Citations", format="%.1f"),
            "research_discovery": st.column_config.NumberColumn("Research", format="%.1f"),
            "learning_experience": st.column_config.NumberColumn("Learning", format="%.1f"),
            "employability": st.column_config.NumberColumn("Employability", format="%.1f"),
            "sustainability": st.column_config.NumberColumn("Sustainability", format="%.1f"),
        }

        st.dataframe(
            df_all[display_cols],
            use_container_width=True,
            height=400,
            column_config=col_config,
        )

        csv_bytes = df_all.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇ Download as CSV",
            data=csv_bytes,
            file_name="qs_rankings.csv",
            mime="text/csv",
        )

# ---------------------------------------------------------------------------
# Chatbot section
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("💬 Ask the Rankings Chatbot")

if not data_available:
    st.info("Chatbot will be available once data has been scraped and embedded.")
else:
    # Initialise session state
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "reference_cards" not in st.session_state:
        st.session_state.reference_cards = []

    # Display conversation history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Reference data cards from last RAG response
    if st.session_state.reference_cards:
        with st.expander("📎 Reference Data Cards", expanded=True):
            n_cards = len(st.session_state.reference_cards)
            card_cols = st.columns(min(n_cards, 3))
            for i, card in enumerate(st.session_state.reference_cards):
                with card_cols[i % 3]:
                    score_str = f"{card['score']:.1f}" if card.get("score") else "N/A"
                    relevance_pct = f"{card['relevance']:.0%}" if card.get("relevance") else "—"
                    st.metric(
                        label=f"#{card.get('rank', '?')}  {card.get('name', 'Unknown')}",
                        value=f"Score: {score_str}",
                        delta=f"Relevance {relevance_pct}",
                        delta_color="off",
                    )
                    st.caption(card.get("country", ""))
                    # Show extra fields present on profile cards
                    if card.get("type") and card["type"] != "N/A":
                        st.caption(f"Type: {card['type']}")
                    if card.get("founded") and card["founded"] != "N/A":
                        st.caption(f"Founded: {card['founded']}")
                    if card.get("students") and card["students"] != "N/A":
                        st.caption(f"Students: {card['students']}")
                    if card.get("description"):
                        st.caption(f"_{card['description'][:160]}…_")

    # Chat input — st.chat_input pins itself to the bottom of the page
    if prompt := st.chat_input("Ask about universities, rankings, countries, scores…"):
        # Show user message immediately
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})

        # Build optional filters from sidebar (none for now - user can ask directly)
        chatbot_filters: dict = {}

        # Run chatbot
        with st.chat_message("assistant"):
            with st.spinner("Thinking…"):
                try:
                    from chatbot import chat
                    answer, cards, query_type = chat(prompt, **chatbot_filters)
                except Exception as exc:
                    answer = f"Error: {exc}"
                    cards = []
                    query_type = "error"

            badge_map = {"rag": "🔍 RAG", "sql": "🧮 SQL", "profile": "🎓 Profile", "greeting": "👋 Hello"}
            badge = badge_map.get(query_type, "⚠️")
            # Don't show badge for greetings - it's cleaner
            if query_type == "greeting":
                full_answer = answer
            else:
                full_answer = f"**[{badge}]** {answer}"
            st.markdown(full_answer)

        st.session_state.messages.append({"role": "assistant", "content": full_answer})
        st.session_state.reference_cards = cards
        st.rerun()
