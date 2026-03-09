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
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
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
    "minimum", "maximum", "which country has the most",
    "how does", "median", "percentile",
    "rank ", "ranked ", "ranking ", "which is the", "who is ranked",
]

# Matches: "8th ranked", "ranked 8th", "rank 8", "ranked #8", "#8 ranked", "8th"
RANK_NUMBER_RE = re.compile(
    r"\b(?:rank(?:ed)?\s*#?\s*\d+|\d+\s*(?:st|nd|rd|th)\s*rank(?:ed)?|#\s*\d+)\b",
    re.IGNORECASE,
)

PROFILE_PATTERNS = [
    r"tell me about (.+)",
    r"(?:describe|overview of|about|what (?:is|are)|info(?:rmation)? (?:on|about)|details? (?:on|about)) (.+)",
    r"(.+) university (?:description|profile|overview|review)",
    r"what (?:do you know|can you tell me) about (.+)",
    r"(.+) (?:ranking|rank|score|rating|review)s?$",
]


def _is_math_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in MATH_KEYWORDS) or bool(RANK_NUMBER_RE.search(q))


def _extract_profile_university(query: str) -> str | None:
    """
    If the query looks like 'tell me about MIT', return 'MIT'.
    Returns None if it doesn't match a profile pattern.
    """
    q = query.strip()
    for pattern in PROFILE_PATTERNS:
        m = re.match(pattern, q, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


# ---------------------------------------------------------------------------
# Schema description for Text-to-SQL
# ---------------------------------------------------------------------------

SCHEMA_DESCRIPTION = """
SQLite database schema (use view v_full_rankings for all queries):

View: v_full_rankings
  - id (INTEGER)
  - rank (INTEGER)                 -- numeric rank (1 = best)
  - university_name (TEXT)
  - country (TEXT)                 -- e.g. "United States", "United Kingdom"
  - continent (TEXT)               -- e.g. "Europe", "Asia", "North America"
  - overall_score (REAL)           -- 0-100
  - citations_per_faculty (REAL)   -- 0-100
  - academic_reputation (REAL)     -- 0-100
  - employer_reputation (REAL)     -- 0-100
  - intl_faculty_ratio (REAL)      -- 0-100
  - intl_student_ratio (REAL)      -- 0-100
  - research_discovery (REAL)      -- 0-100 (may be NULL)
  - learning_experience (REAL)     -- 0-100 (may be NULL)
  - employability (REAL)           -- 0-100 (may be NULL)
  - global_engagement (REAL)       -- 0-100 (may be NULL)
  - sustainability (REAL)          -- 0-100 (may be NULL)
  - description (TEXT)             -- overview/about text (may be NULL)
  - university_type (TEXT)         -- e.g. "Public", "Private" (may be NULL)
  - founded_year (TEXT)            -- year founded (may be NULL)
  - total_students (TEXT)          -- total enrolled students (may be NULL)
  - student_faculty_ratio (TEXT)   -- e.g. "12:1" (may be NULL)
  - review_snippets (TEXT)         -- student review excerpts (may be NULL)

Notes:
  - Lower rank = better (rank 1 is the top university)
  - Detail columns may be NULL for universities outside the top 200
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
# Profile lookup path
# ---------------------------------------------------------------------------

def _fetch_university_row(name_hint: str) -> dict | None:
    """
    Search SQLite for the best-matching university by name (case-insensitive LIKE).
    Returns the full row dict or None if not found.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    # Try exact match first, then partial
    for sql in [
        "SELECT * FROM v_full_rankings WHERE LOWER(university_name) = LOWER(?) LIMIT 1",
        "SELECT * FROM v_full_rankings WHERE LOWER(university_name) LIKE LOWER(?) ORDER BY rank LIMIT 1",
    ]:
        param = name_hint if "= LOWER" in sql else f"%{name_hint}%"
        cur.execute(sql, (param,))
        row = cur.fetchone()
        if row:
            conn.close()
            return dict(row)
    conn.close()
    return None


def _format_profile(row: dict, client, model: str, original_query: str) -> tuple[str, list[dict]]:
    """
    Build a structured university profile string and ask the LLM to narrate it.
    Returns (answer, reference_cards).
    """
    def _v(key, default="N/A"):
        v = row.get(key)
        return str(v) if v is not None and str(v).strip() not in ("", "None") else default

    def _score(key):
        v = row.get(key)
        try:
            return f"{float(v):.1f}" if v is not None else "N/A"
        except (TypeError, ValueError):
            return "N/A"

    # Structured data block passed to the LLM
    profile_block = f"""
University Profile: {_v('university_name')}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Global Rank       : #{_v('rank')}
Country           : {_v('country')} ({_v('continent')})
Type              : {_v('university_type')}
Founded           : {_v('founded_year')}
Total Students    : {_v('total_students')}
Student:Faculty   : {_v('student_faculty_ratio')}

─── QS Overall Score ───────────────────
Overall Score     : {_score('overall_score')} / 100

─── Core QS Metrics ────────────────────
Academic Reputation    : {_score('academic_reputation')}
Employer Reputation    : {_score('employer_reputation')}
Citations per Faculty  : {_score('citations_per_faculty')}
Intl. Faculty Ratio    : {_score('intl_faculty_ratio')}
Intl. Student Ratio    : {_score('intl_student_ratio')}

─── QS Lens Scores ─────────────────────
Research & Discovery   : {_score('research_discovery')}
Learning Experience    : {_score('learning_experience')}
Employability          : {_score('employability')}
Global Engagement      : {_score('global_engagement')}
Sustainability         : {_score('sustainability')}

─── About ──────────────────────────────
{_v('description', 'No description available.')}

─── Student Reviews ────────────────────
{_v('review_snippets', 'No reviews available.')}
"""

    prompt = f"""You are an expert university advisor. The user asked: "{original_query}"

Here is the complete data profile for this university:
{profile_block}

Please write a comprehensive, well-structured response that:
1. Opens with the university name, rank, and country
2. Describes what the university is known for (using the About section)
3. Highlights standout scores and what they mean for students
4. Mentions any student review highlights if available
5. Closes with a brief summary of who this university is best suited for

Use a friendly, informative tone. Format with markdown headers and bullet points."""

    answer = _llm_complete(client, model, prompt, temperature=0.5, max_tokens=900)

    card = [{
        "rank": row.get("rank"),
        "name": row.get("university_name"),
        "country": row.get("country"),
        "score": row.get("overall_score"),
        "relevance": 1.0,
        "description": str(row.get("description") or "")[:200],
        "type": _v("university_type"),
        "founded": _v("founded_year"),
        "students": _v("total_students"),
    }]
    return answer, card


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

    rag_prompt = f"""You are an expert university advisor with deep knowledge of QS World University Rankings. Answer the user's question using ONLY the context provided below.

For each university you mention:
- State its global rank and overall score
- Highlight the most relevant scores for the question (e.g. sustainability, employability)
- Include a brief description of the university if available in the context
- Mention any student review highlights if relevant

If the answer is not in the context, say so clearly.

Context:
{context}

Question: {query}
Answer:"""

    answer = _llm_complete(client, model, rag_prompt, temperature=0.4, max_tokens=800)

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

    # 1. Aggregation / math query → Text-to-SQL
    if _is_math_query(user_query):
        answer = _run_text_to_sql(user_query, client, model)
        return answer, [], "sql"

    # 2. Profile query ("tell me about MIT") → direct DB lookup + structured profile
    uni_name = _extract_profile_university(user_query)
    if uni_name:
        row = _fetch_university_row(uni_name)
        if row:
            answer, cards = _format_profile(row, client, model, user_query)
            return answer, cards, "profile"
        # Name not found in DB — fall through to RAG

    # 3. Semantic / comparison query → RAG
    answer, cards = _run_rag_query(
        user_query, client, model,
        n_results=n_results,
        filter_country=filter_country,
        filter_continent=filter_continent,
    )
    return answer, cards, "rag"
