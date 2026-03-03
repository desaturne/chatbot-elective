"""
chatbot.py
RAG + Text-to-SQL router for the QS Rankings chatbot.

Routes queries to either:
  - RAG path  : semantic vector search → LLM answer with citations
  - SQL path  : Text-to-SQL generation → SQLite execution → LLM summary

Usage (from app.py):
    from chatbot import chat
    answer, reference_cards, query_type = chat("Which UK university has highest sustainability?")
"""

import os
import re
import sqlite3
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Paths & config
# ---------------------------------------------------------------------------
DB_PATH = Path("data/processed/qs_rankings.db")
CHROMA_DIR = Path("data/chroma_db")
COLLECTION_NAME = "qs_rankings"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# ---------------------------------------------------------------------------
# LLM client factory
# ---------------------------------------------------------------------------

def _get_llm_client():
    """Returns (client, model_name). Supports both OpenAI and Groq."""
    provider = os.getenv("LLM_PROVIDER", "groq").lower()
    if provider == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    else:  # groq — uses OpenAI-compatible API
        from openai import OpenAI
        client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1",
        )
        model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
    return client, model


def _llm_complete(client, model: str, prompt: str, temperature: float = 0.3, max_tokens: int = 600) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Query router
# ---------------------------------------------------------------------------

MATH_KEYWORDS = [
    "average", "highest", "lowest", "count", "how many", "top ", "bottom ",
    "compare", "rank between", "greater than", "less than", "sum", "total",
    "minimum", "maximum", "what is the", "which country has the most",
    "how does", "median", "percentile",
]


def _is_math_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in MATH_KEYWORDS)


# ---------------------------------------------------------------------------
# Schema description for Text-to-SQL
# ---------------------------------------------------------------------------

SCHEMA_DESCRIPTION = """
SQLite database schema (use view v_full_rankings for all queries):

View: v_full_rankings
  - id (INTEGER)
  - rank (INTEGER)               -- numeric rank (1 = best)
  - university_name (TEXT)
  - country (TEXT)               -- e.g. "United States", "United Kingdom"
  - continent (TEXT)             -- e.g. "Europe", "Asia", "North America"
  - overall_score (REAL)         -- 0-100
  - citations_per_faculty (REAL) -- 0-100
  - academic_reputation (REAL)   -- 0-100
  - employer_reputation (REAL)   -- 0-100
  - intl_faculty_ratio (REAL)    -- 0-100
  - intl_student_ratio (REAL)    -- 0-100
  - research_discovery (REAL)    -- 0-100 (may be NULL)
  - learning_experience (REAL)   -- 0-100 (may be NULL)
  - employability (REAL)         -- 0-100 (may be NULL)
  - global_engagement (REAL)     -- 0-100 (may be NULL)
  - sustainability (REAL)        -- 0-100 (may be NULL)

Notes:
  - Lower rank = better (rank 1 is the top university)
  - Some detail scores may be NULL for universities outside the top 200
  - Always use WHERE rank IS NOT NULL to avoid NULLs in rank comparisons
"""


# ---------------------------------------------------------------------------
# Text-to-SQL path
# ---------------------------------------------------------------------------

def _run_text_to_sql(query: str, client, model: str) -> str:
    sql_prompt = f"""You are a SQLite expert. Based on the schema below, write a valid SQLite SELECT query to answer the user's question. Return ONLY the SQL query with no explanation, no markdown fences, no comments.

Schema:
{SCHEMA_DESCRIPTION}

User question: {query}
SQL:"""

    sql_raw = _llm_complete(client, model, sql_prompt, temperature=0, max_tokens=300)

    # Strip markdown code fences if present
    sql = re.sub(r"```(?:sql)?", "", sql_raw, flags=re.IGNORECASE).strip()

    # Safety: only allow SELECT statements
    if not sql.upper().lstrip().startswith("SELECT"):
        return "I can only run SELECT queries. Please rephrase your question."

    # Remove any trailing semicolons that can cause issues
    sql = sql.rstrip(";").strip()

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql)
        results = [dict(r) for r in cur.fetchall()]
        conn.close()
    except sqlite3.Error as db_err:
        return f"Database error while running the query: {db_err}\n\nGenerated SQL:\n```sql\n{sql}\n```"

    if not results:
        return "The query ran successfully but returned no results."

    # Ask LLM to summarize the raw results in plain English
    summarize_prompt = f"""The user asked: "{query}"

The following data was retrieved from the database:
{results[:20]}

Please answer the user's question in clear, natural language. Cite specific numbers and university names from the data. Keep the answer concise."""

    return _llm_complete(client, model, summarize_prompt, temperature=0.3, max_tokens=500)


# ---------------------------------------------------------------------------
# RAG path
# ---------------------------------------------------------------------------

def _run_rag_query(
    query: str,
    client,
    model: str,
    n_results: int = 5,
    filter_country: str | None = None,
    filter_continent: str | None = None,
) -> tuple[str, list[dict]]:
    """Returns (answer_text, reference_cards)."""
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    try:
        collection = chroma_client.get_collection(COLLECTION_NAME, embedding_function=ef)
    except Exception:
        return (
            "The vector store is not set up yet. Please click 'Rebuild Embeddings' in the sidebar first.",
            [],
        )

    # Build optional metadata filter
    where_clause: dict | None = None
    if filter_country:
        where_clause = {"country": {"$eq": filter_country}}
    elif filter_continent:
        where_clause = {"continent": {"$eq": filter_continent}}

    query_kwargs: dict = {
        "query_texts": [query],
        "n_results": min(n_results, collection.count()),
        "include": ["documents", "metadatas", "distances"],
    }
    if where_clause:
        query_kwargs["where"] = where_clause

    results = collection.query(**query_kwargs)
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    if not docs:
        return "No relevant universities found for your query.", []

    context = "\n\n".join(
        [f"[Source {i + 1}]\n{doc}" for i, doc in enumerate(docs)]
    )

    rag_prompt = f"""You are an expert assistant for QS World University Rankings data. Answer the user's question using ONLY the context provided below. Cite specific universities, ranks, and scores in your answer. If the answer is not in the context, say so.

Context:
{context}

Question: {query}
Answer:"""

    answer = _llm_complete(client, model, rag_prompt, temperature=0.4, max_tokens=600)

    # Build reference cards for the Streamlit UI
    cards = [
        {
            "rank": m.get("rank"),
            "name": m.get("university_name"),
            "country": m.get("country"),
            "score": m.get("overall_score"),
            "relevance": round(1.0 - distances[i], 3),
        }
        for i, m in enumerate(metas)
    ]

    return answer, cards


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def chat(
    user_query: str,
    filter_country: str | None = None,
    filter_continent: str | None = None,
    n_results: int = 5,
) -> tuple[str, list[dict], str]:
    """
    Main entry point called by app.py.

    Parameters
    ----------
    user_query       : The user's question
    filter_country   : Optionally restrict RAG search to this country
    filter_continent : Optionally restrict RAG search to this continent
    n_results        : How many RAG results to retrieve

    Returns
    -------
    (answer, reference_cards, query_type)
      answer          : str — the chatbot's response
      reference_cards : list[dict] — data cards shown in the UI (RAG path only)
      query_type      : "rag" | "sql"
    """
    if not DB_PATH.exists():
        return (
            "No data found. Please run the scraper and pipeline first.",
            [],
            "error",
        )

    client, model = _get_llm_client()

    if _is_math_query(user_query):
        answer = _run_text_to_sql(user_query, client, model)
        return answer, [], "sql"
    else:
        answer, cards = _run_rag_query(
            user_query, client, model,
            n_results=n_results,
            filter_country=filter_country,
            filter_continent=filter_continent,
        )
        return answer, cards, "rag"
