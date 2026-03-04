"""
pipeline.py
Reads the raw CSVs produced by scraper.py, cleans the data,
and ingests it into an SQLite database.

Usage:
    python pipeline.py
"""

import re
import sqlite3
from pathlib import Path

import pandas as pd

try:
    import pycountry_convert as pc
    _HAS_PYCOUNTRY = True
except ImportError:
    _HAS_PYCOUNTRY = False
    print("[!] pycountry-convert not installed; continent will be 'Unknown'")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Raw (scraped, uncleaned)
MAIN_CSV = Path("data/raw/rankings_main.csv")
DETAIL_CSV = Path("data/raw/rankings_detail.csv")

# Processed (cleaned outputs — these are what all downstream steps use)
PROCESSED_DIR = Path("data/processed")
CLEAN_MAIN_CSV = PROCESSED_DIR / "rankings_main_cleaned.csv"
CLEAN_DETAIL_CSV = PROCESSED_DIR / "rankings_detail_cleaned.csv"
MERGED_CSV = PROCESSED_DIR / "rankings_merged.csv"   # joined, fully cleaned
DB_PATH = PROCESSED_DIR / "qs_rankings.db"

# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

def clean_score(val) -> float | None:
    """Strip non-numeric characters and return float, or None if unparseable."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s in ("", "-", "N/A", "n/a", "—"):
        return None
    cleaned = re.sub(r"[^\d.]", "", s)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def clean_rank(val) -> int | None:
    """
    Handle QS rank formats:
      "1"       -> 1
      "=42"     -> 42
      "501-510" -> 501
    """
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s in ("", "-", "N/A"):
        return None
    match = re.search(r"(\d+)", s)
    return int(match.group(1)) if match else None


# Continent code → readable name
_CONTINENT_MAP = {
    "AF": "Africa",
    "AN": "Antarctica",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
}

# Manual overrides for countries that pycountry_convert misses
_COUNTRY_OVERRIDES = {
    "United States": "North America",
    "USA": "North America",
    "UK": "Europe",
    "United Kingdom": "Europe",
    "South Korea": "Asia",
    "North Korea": "Asia",
    "Hong Kong SAR": "Asia",
    "Hong Kong": "Asia",
    "Macau SAR": "Asia",
    "Taiwan": "Asia",
    "Russia": "Europe",
    "Iran": "Asia",
    "Syria": "Asia",
    "Palestine": "Asia",
    "Kosovo": "Europe",
    "Czechia": "Europe",
    "Czech Republic": "Europe",
    "New Zealand": "Oceania",
    "Australia": "Oceania",
}


def country_to_continent(country_name: str) -> str:
    if not country_name or pd.isna(country_name):
        return "Unknown"
    name = str(country_name).strip()
    if name in _COUNTRY_OVERRIDES:
        return _COUNTRY_OVERRIDES[name]
    if not _HAS_PYCOUNTRY:
        return "Unknown"
    try:
        alpha2 = pc.country_name_to_country_alpha2(name, cn_name_format="default")
        cont_code = pc.country_alpha2_to_continent_code(alpha2)
        return _CONTINENT_MAP.get(cont_code, "Unknown")
    except Exception:
        return "Unknown"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
DROP TABLE IF EXISTS university_details;
DROP TABLE IF EXISTS universities;
CREATE TABLE universities (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    rank                  INTEGER,
    university_name       TEXT NOT NULL,
    country               TEXT,
    continent             TEXT,
    overall_score         REAL,
    citations_per_faculty REAL,
    academic_reputation   REAL,
    employer_reputation   REAL,
    intl_faculty_ratio    REAL,
    intl_student_ratio    REAL,
    detail_url            TEXT UNIQUE,
    scraped_at            TEXT
);

CREATE TABLE university_details (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    university_id         INTEGER NOT NULL,
    research_discovery    REAL,
    learning_experience   REAL,
    employability         REAL,
    global_engagement     REAL,
    sustainability        REAL,
    description           TEXT,
    university_type       TEXT,
    founded_year          TEXT,
    total_students        TEXT,
    student_faculty_ratio TEXT,
    review_snippets       TEXT,
    scraped_at            TEXT,
    FOREIGN KEY (university_id) REFERENCES universities(id) ON DELETE CASCADE
);
"""

VIEW_SQL = """
DROP VIEW IF EXISTS v_full_rankings;
CREATE VIEW v_full_rankings AS
    SELECT
        u.id,
        u.rank,
        u.university_name,
        u.country,
        u.continent,
        u.overall_score,
        u.citations_per_faculty,
        u.academic_reputation,
        u.employer_reputation,
        u.intl_faculty_ratio,
        u.intl_student_ratio,
        d.research_discovery,
        d.learning_experience,
        d.employability,
        d.global_engagement,
        d.sustainability,
        d.description,
        d.university_type,
        d.founded_year,
        d.total_students,
        d.student_faculty_ratio,
        d.review_snippets
    FROM universities u
    LEFT JOIN university_details d ON d.university_id = u.id;
"""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _clean_main(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean the raw rankings list (Layer A).
    All downstream steps use the output of this function — never the raw CSV.
    """
    score_cols = [
        "overall_score", "citations_per_faculty", "academic_reputation",
        "employer_reputation", "intl_faculty_ratio", "intl_student_ratio",
    ]
    for col in score_cols:
        if col in df.columns:
            df[col] = df[col].apply(clean_score)
        else:
            df[col] = None

    df["rank"] = df["rank"].apply(clean_rank)
    df["university_name"] = df["university_name"].fillna("Unknown University")
    df["continent"] = df["country"].apply(country_to_continent)
    df["cleaned_at"] = pd.Timestamp.now().isoformat()

    # Drop rows with no university name at all after filling
    df = df[df["university_name"] != "Unknown University"].copy()

    # Sort by rank (NaN ranks go to the bottom)
    df = df.sort_values("rank", na_position="last").reset_index(drop=True)
    return df


def _clean_detail(df: pd.DataFrame) -> pd.DataFrame:
    """
    Clean the raw university detail scrape (Layer B).
    All downstream steps use the output of this function — never the raw CSV.
    """
    detail_score_cols = [
        "research_discovery", "learning_experience", "employability",
        "global_engagement", "sustainability",
    ]
    for col in detail_score_cols:
        if col in df.columns:
            df[col] = df[col].apply(clean_score)
        else:
            df[col] = None

    # Numeric score range guard: scores must be 0–100
    for col in detail_score_cols:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: v if v is None or 0 <= v <= 100 else None)

    # Text fields: strip whitespace, map empty → None
    text_cols = ["description", "university_type", "founded_year",
                 "total_students", "student_faculty_ratio", "review_snippets"]
    for col in text_cols:
        if col in df.columns:
            df[col] = df[col].apply(
                lambda v: str(v).strip() if pd.notna(v) and str(v).strip() not in ("", "nan") else None
            )
        else:
            df[col] = None

    df["cleaned_at"] = pd.Timestamp.now().isoformat()
    return df


def run_pipeline():
    if not MAIN_CSV.exists():
        print(f"[!] {MAIN_CSV} not found. Run scraper.py first.")
        return

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 1 — CLEAN (raw CSV → cleaned DataFrame)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n── Step 1: Cleaning raw data ─────────────────────────────────────")

    print(f"  [*] Loading raw {MAIN_CSV} ({MAIN_CSV.stat().st_size // 1024} KB)...")
    df_main_raw = pd.read_csv(MAIN_CSV)
    print(f"      {len(df_main_raw)} rows before cleaning")
    df_main = _clean_main(df_main_raw.copy())
    print(f"      {len(df_main)} rows after cleaning  "
          f"({len(df_main_raw) - len(df_main)} dropped)")

    df_detail = None
    if DETAIL_CSV.exists():
        print(f"  [*] Loading raw {DETAIL_CSV} ({DETAIL_CSV.stat().st_size // 1024} KB)...")
        df_detail_raw = pd.read_csv(DETAIL_CSV)
        print(f"      {len(df_detail_raw)} rows before cleaning")
        df_detail = _clean_detail(df_detail_raw.copy())
        print(f"      {len(df_detail)} rows after cleaning")
    else:
        print(f"  [!] {DETAIL_CSV} not found — skipping detail cleaning.")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 2 — SAVE CLEANED CSVs (transparent, inspectable output)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n── Step 2: Saving cleaned CSVs → data/processed/ ────────────────")

    df_main.to_csv(CLEAN_MAIN_CSV, index=False, encoding="utf-8")
    print(f"  [+] {CLEAN_MAIN_CSV}  ({len(df_main)} rows)")

    if df_detail is not None:
        df_detail.to_csv(CLEAN_DETAIL_CSV, index=False, encoding="utf-8")
        print(f"  [+] {CLEAN_DETAIL_CSV}  ({len(df_detail)} rows)")

        # Build merged / joined CSV — this is the single source of truth
        # Drop columns from detail that already exist in main to avoid _x/_y suffixes
        _detail_only_cols = [
            "university_name", "cleaned_at",
            "overall_score", "academic_reputation", "employer_reputation",
            "citations_per_faculty", "intl_faculty_ratio", "intl_student_ratio",
        ]
        df_merged = pd.merge(
            df_main,
            df_detail.drop(columns=_detail_only_cols, errors="ignore"),
            on="detail_url",
            how="left",
        )
        df_merged.to_csv(MERGED_CSV, index=False, encoding="utf-8")
        print(f"  [+] {MERGED_CSV}  ({len(df_merged)} rows, fully joined)")
    else:
        # No detail data — merged is just the main cleaned file
        df_main.to_csv(MERGED_CSV, index=False, encoding="utf-8")
        print(f"  [+] {MERGED_CSV}  (detail not available; main only)")

    # ═══════════════════════════════════════════════════════════════════════
    # STEP 3 — LOAD CLEANED DATA INTO SQLITE (from cleaned DataFrames)
    # ═══════════════════════════════════════════════════════════════════════
    print("\n── Step 3: Loading cleaned data into SQLite ──────────────────────")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA_SQL)
    conn.commit()

    # Insert cleaned main table
    main_db_cols = [
        "rank", "university_name", "country", "continent", "overall_score",
        "citations_per_faculty", "academic_reputation", "employer_reputation",
        "intl_faculty_ratio", "intl_student_ratio", "detail_url", "cleaned_at",
    ]
    df_main_db = df_main.rename(columns={"cleaned_at": "scraped_at"})
    for col in main_db_cols:
        if col not in df_main_db.columns:
            df_main_db[col] = None
    df_main_db[main_db_cols].rename(columns={"cleaned_at": "scraped_at"}).to_sql(
        "universities", conn, if_exists="append", index=False, method="multi"
    )
    print(f"  [+] universities table: {len(df_main)} rows")

    # Insert cleaned detail table
    if df_detail is not None:
        cur.execute("SELECT id, detail_url FROM universities")
        url_to_id = {row[1]: row[0] for row in cur.fetchall()}
        df_detail["university_id"] = df_detail["detail_url"].map(url_to_id)
        df_detail = df_detail.dropna(subset=["university_id"]).copy()
        df_detail["university_id"] = df_detail["university_id"].astype(int)

        detail_db_cols = [
            "university_id", "research_discovery", "learning_experience",
            "employability", "global_engagement", "sustainability",
            "description", "university_type", "founded_year",
            "total_students", "student_faculty_ratio", "review_snippets",
            "cleaned_at",
        ]
        df_detail_db = df_detail.copy()
        for col in detail_db_cols:
            if col not in df_detail_db.columns:
                df_detail_db[col] = None
        df_detail_db[detail_db_cols].rename(columns={"cleaned_at": "scraped_at"}).to_sql(
            "university_details", conn, if_exists="append", index=False, method="multi"
        )
        print(f"  [+] university_details table: {len(df_detail)} rows")

    cur.executescript(VIEW_SQL)
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM v_full_rankings")
    count = cur.fetchone()[0]
    print(f"  [+] v_full_rankings view: {count} rows")
    conn.close()

    # ═══════════════════════════════════════════════════════════════════════
    # Summary
    # ═══════════════════════════════════════════════════════════════════════
    print("\n── Pipeline complete ─────────────────────────────────────────────")
    print(f"  Raw data   : {MAIN_CSV}, {DETAIL_CSV}")
    print(f"  Cleaned CSV: {CLEAN_MAIN_CSV}")
    if df_detail is not None:
        print(f"             : {CLEAN_DETAIL_CSV}")
    print(f"  Merged CSV : {MERGED_CSV}   ← use this for EDA / inspection")
    print(f"  Database   : {DB_PATH}   ← used by embedder, chatbot, app")


if __name__ == "__main__":
    run_pipeline()
