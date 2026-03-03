"""
embedder.py
Reads the SQLite database (v_full_rankings view) and builds a
ChromaDB persistent vector store using all-MiniLM-L6-v2 embeddings.

Usage:
    python embedder.py
"""

import sqlite3
from pathlib import Path

import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DB_PATH = Path("data/processed/qs_rankings.db")
CHROMA_DIR = Path("data/chroma_db")
COLLECTION_NAME = "qs_rankings"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
BATCH_SIZE = 100


# ---------------------------------------------------------------------------
# Document builder
# ---------------------------------------------------------------------------

def build_document_text(row: dict) -> str:
    """
    Convert a university row into a rich natural-language paragraph.
    This text is what gets embedded — richer text improves retrieval quality.
    """
    def fmt(val, default="N/A"):
        if val is None or str(val).strip() in ("", "None"):
            return default
        try:
            return f"{float(val):.1f}"
        except (ValueError, TypeError):
            return str(val)

    # Key facts block
    facts_parts = []
    if row.get("university_type"):
        facts_parts.append(f"Type: {row['university_type']}")
    if row.get("founded_year"):
        facts_parts.append(f"Founded: {row['founded_year']}")
    if row.get("total_students"):
        facts_parts.append(f"Total students: {row['total_students']}")
    if row.get("student_faculty_ratio"):
        facts_parts.append(f"Student-faculty ratio: {row['student_faculty_ratio']}")
    facts_str = (", ".join(facts_parts) + ". ") if facts_parts else ""

    description = str(row.get("description") or "").strip()
    desc_str = (f"About: {description[:600]} ") if description else ""

    reviews = str(row.get("review_snippets") or "").strip()
    review_str = (f"Student reviews: {reviews[:400]} ") if reviews else ""

    return (
        f"{row.get('university_name', 'Unknown')} is ranked "
        f"#{row.get('rank', 'N/A')} globally in the QS World University Rankings. "
        f"Located in {row.get('country', 'N/A')} ({row.get('continent', 'N/A')}). "
        f"{facts_str}"
        f"{desc_str}"
        f"Overall score: {fmt(row.get('overall_score'))}. "
        f"Key ranking metrics — "
        f"Academic Reputation: {fmt(row.get('academic_reputation'))}, "
        f"Employer Reputation: {fmt(row.get('employer_reputation'))}, "
        f"Citations per Faculty: {fmt(row.get('citations_per_faculty'))}, "
        f"International Faculty Ratio: {fmt(row.get('intl_faculty_ratio'))}, "
        f"International Student Ratio: {fmt(row.get('intl_student_ratio'))}. "
        f"Lens scores — "
        f"Research & Discovery: {fmt(row.get('research_discovery'))}, "
        f"Learning Experience: {fmt(row.get('learning_experience'))}, "
        f"Employability: {fmt(row.get('employability'))}, "
        f"Global Engagement: {fmt(row.get('global_engagement'))}, "
        f"Sustainability: {fmt(row.get('sustainability'))}. "
        f"{review_str}"
    )


def _safe_float(val, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (ValueError, TypeError):
        return default


def _safe_int(val, default: int = 9999) -> int:
    try:
        return int(val) if val is not None else default
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_vector_store():
    if not DB_PATH.exists():
        print(f"[!] Database not found at {DB_PATH}. Run pipeline.py first.")
        return

    # Load data
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM v_full_rankings ORDER BY rank")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        print("[!] v_full_rankings is empty. Run scraper.py and pipeline.py first.")
        return

    print(f"[*] Building vector store for {len(rows)} universities...")
    CHROMA_DIR.mkdir(parents=True, exist_ok=True)

    # Embedding function (downloads model ~90MB on first run)
    print(f"[*] Loading embedding model: {EMBEDDING_MODEL}")
    ef = SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)

    # ChromaDB persistent client (embedded, no server needed)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # Delete existing collection to allow full rebuild
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"[*] Deleted existing collection '{COLLECTION_NAME}' for rebuild")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    documents = []
    metadatas = []
    ids = []

    for row in rows:
        doc_text = build_document_text(row)
        documents.append(doc_text)
        metadatas.append({
            "university_id": _safe_int(row.get("id")),
            "rank":          _safe_int(row.get("rank")),
            "university_name": str(row.get("university_name") or "Unknown"),
            "country":       str(row.get("country") or "Unknown"),
            "continent":     str(row.get("continent") or "Unknown"),
            "overall_score": _safe_float(row.get("overall_score")),
        })
        ids.append(f"uni_{row.get('id', len(ids))}")

    # Upsert in batches
    total = len(documents)
    for i in range(0, total, BATCH_SIZE):
        batch_end = min(i + BATCH_SIZE, total)
        collection.upsert(
            documents=documents[i:batch_end],
            metadatas=metadatas[i:batch_end],
            ids=ids[i:batch_end],
        )
        print(f"  Embedded {batch_end}/{total} universities...")

    print(f"[+] Vector store built: {collection.count()} documents in '{COLLECTION_NAME}'")
    print(f"[+] Persisted to {CHROMA_DIR}")


if __name__ == "__main__":
    build_vector_store()
