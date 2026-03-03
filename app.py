"""
app.py
Streamlit application combining the EDA dashboard and RAG chatbot.

Run:
    streamlit run app.py
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
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
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("🎓 QS Rankings Explorer")
    st.caption("QS World University Rankings — Interactive Dashboard & Chatbot")
    st.markdown("---")

    # ── Filters ──────────────────────────────────────────────────────────────
    st.subheader("Filters")

    continent_options = ["All"]
    country_options_full = ["All"]
    score_min, score_max = 0.0, 100.0

    if data_available:
        continent_options += sorted(df_all["continent"].dropna().unique().tolist())

    sel_continent = st.selectbox("Region / Continent", continent_options)

    if data_available:
        pool = df_all if sel_continent == "All" else df_all[df_all["continent"] == sel_continent]
        country_options_full = ["All"] + sorted(pool["country"].dropna().unique().tolist())

    sel_country = st.selectbox("Country", country_options_full)

    score_min, score_max = st.slider(
        "Overall Score Range",
        min_value=0.0, max_value=100.0,
        value=(0.0, 100.0), step=0.5,
    )

    st.markdown("---")

    # ── Actions ──────────────────────────────────────────────────────────────
    st.subheader("Actions")

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
            st.success("Scraping and pipeline complete! Refreshing data…")
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

    st.markdown("---")

    # ── LLM Provider ─────────────────────────────────────────────────────────
    st.subheader("LLM Provider")
    llm_choice = st.radio(
        "Provider",
        ["Groq — Llama 3 (free)", "OpenAI — GPT-4o-mini"],
        index=0,
    )
    # Persist to environment so chatbot.py picks it up
    import os
    if "OpenAI" in llm_choice:
        os.environ["LLM_PROVIDER"] = "openai"
    else:
        os.environ["LLM_PROVIDER"] = "groq"

    st.markdown("---")
    if data_available:
        st.caption(f"📊 {len(df_all)} universities loaded")
    else:
        st.warning("No data yet. Click 'Run Scraper' to start.")


# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------
if data_available:
    df_filtered = df_all.copy()
    if sel_continent != "All":
        df_filtered = df_filtered[df_filtered["continent"] == sel_continent]
    if sel_country != "All":
        df_filtered = df_filtered[df_filtered["country"] == sel_country]
    df_filtered = df_filtered[
        (df_filtered["overall_score"].fillna(0) >= score_min) &
        (df_filtered["overall_score"].fillna(100) <= score_max)
    ].reset_index(drop=True)
else:
    df_filtered = pd.DataFrame()

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------
st.title("QS World University Rankings Explorer")

if not data_available:
    st.info(
        "No ranking data found. Use the **Run Scraper** button in the sidebar to "
        "download the rankings, or manually place `data/raw/rankings_main.csv` and "
        "`data/raw/rankings_detail.csv` and then run `python pipeline.py`."
    )
else:
    tab_overview, tab_data = st.tabs(["📊 Overview & Charts", "📋 Data Table"])

    # ── Tab 1: Overview & Charts ──────────────────────────────────────────────
    with tab_overview:
        top100 = df_all[df_all["rank"] <= 100].copy()

        col1, col2 = st.columns(2)

        # Pie chart: Top 100 by Country
        with col1:
            count_by_country = (
                top100.groupby("country").size().reset_index(name="count")
                .sort_values("count", ascending=False)
            )
            fig_pie = px.pie(
                count_by_country,
                values="count",
                names="country",
                title="Top 100 Universities by Country",
                hole=0.35,
            )
            fig_pie.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig_pie, use_container_width=True)

        # Bar: Research vs Learning Experience by continent
        with col2:
            continent_avg = (
                df_filtered
                .groupby("continent")[["research_discovery", "learning_experience"]]
                .mean()
                .reset_index()
                .dropna(subset=["research_discovery", "learning_experience"], how="all")
            )
            if not continent_avg.empty:
                fig_bar = px.bar(
                    continent_avg.melt(
                        id_vars="continent",
                        value_vars=["research_discovery", "learning_experience"],
                        var_name="Metric",
                        value_name="Avg Score",
                    ),
                    x="continent",
                    y="Avg Score",
                    color="Metric",
                    barmode="group",
                    title="Research vs Learning Experience by Continent",
                    labels={"continent": "Continent"},
                    color_discrete_map={
                        "research_discovery": "#636EFA",
                        "learning_experience": "#EF553B",
                    },
                )
                st.plotly_chart(fig_bar, use_container_width=True)
            else:
                st.info("Continent breakdown unavailable (detail scores not yet scraped).")

        # Choropleth heatmap
        choropleth_data = (
            top100.groupby("country").size().reset_index(name="university_count")
        )
        fig_choro = px.choropleth(
            choropleth_data,
            locations="country",
            locationmode="country names",
            color="university_count",
            color_continuous_scale="Viridis",
            title="Geographic Heatmap: Top 100 Universities per Country",
            labels={"university_count": "# Universities"},
        )
        fig_choro.update_layout(geo=dict(showframe=False, showcoastlines=True))
        st.plotly_chart(fig_choro, use_container_width=True)

        # Correlation matrix
        numeric_cols = [
            "overall_score", "citations_per_faculty", "academic_reputation",
            "employer_reputation", "intl_faculty_ratio", "intl_student_ratio",
            "research_discovery", "learning_experience", "employability",
            "global_engagement", "sustainability",
        ]
        available_cols = [c for c in numeric_cols if c in df_filtered.columns]
        corr_df = df_filtered[available_cols].dropna(how="all").corr()
        if not corr_df.empty:
            fig_corr = px.imshow(
                corr_df,
                text_auto=".2f",
                aspect="auto",
                color_continuous_scale="RdBu_r",
                title="Metric Correlation Matrix",
                zmin=-1,
                zmax=1,
            )
            st.plotly_chart(fig_corr, use_container_width=True)

    # ── Tab 2: Data Table ─────────────────────────────────────────────────────
    with tab_data:
        st.subheader(f"Universities — {len(df_filtered)} results")

        display_cols = [
            c for c in [
                "rank", "university_name", "country", "continent",
                "overall_score", "academic_reputation", "employer_reputation",
                "citations_per_faculty", "research_discovery",
                "learning_experience", "employability", "sustainability",
            ]
            if c in df_filtered.columns
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
            df_filtered[display_cols],
            use_container_width=True,
            height=480,
            column_config=col_config,
        )

        csv_bytes = df_filtered.to_csv(index=False).encode("utf-8")
        st.download_button(
            label="⬇ Download as CSV",
            data=csv_bytes,
            file_name="qs_rankings_filtered.csv",
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

        # Build optional filters from sidebar
        chatbot_filters: dict = {}
        if sel_country != "All":
            chatbot_filters["filter_country"] = sel_country
        elif sel_continent != "All":
            chatbot_filters["filter_continent"] = sel_continent

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

            badge_map = {"rag": "🔍 RAG", "sql": "🧮 SQL", "profile": "🎓 Profile"}
            badge = badge_map.get(query_type, "⚠️")
            full_answer = f"**[{badge}]** {answer}"
            st.markdown(full_answer)

        st.session_state.messages.append({"role": "assistant", "content": full_answer})
        st.session_state.reference_cards = cards
        st.rerun()
