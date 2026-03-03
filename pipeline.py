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
DB_PATH = Path("data/processed/qs_rankings.db")
MAIN_CSV = Path("data/raw/rankings_main.csv")
DETAIL_CSV = Path("data/raw/rankings_detail.csv")

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
CREATE TABLE IF NOT EXISTS universities (
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

CREATE TABLE IF NOT EXISTS university_details (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    university_id       INTEGER NOT NULL,
    research_discovery  REAL,
    learning_experience REAL,
    employability       REAL,
    global_engagement   REAL,
    sustainability      REAL,
    scraped_at          TEXT,
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
        d.sustainability
    FROM universities u
    LEFT JOIN university_details d ON d.university_id = u.id;
"""


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline():
    if not MAIN_CSV.exists():
        print(f"[!] {MAIN_CSV} not found. Run scraper.py first.")
        return

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(SCHEMA_SQL)
    conn.commit()

    # ── Layer A ──────────────────────────────────────────────────────────────
    print(f"[*] Loading {MAIN_CSV} ...")
    df_main = pd.read_csv(MAIN_CSV)
    print(f"    {len(df_main)} rows loaded")

    score_cols = [
        "overall_score", "citations_per_faculty", "academic_reputation",
        "employer_reputation", "intl_faculty_ratio", "intl_student_ratio",
    ]
    for col in score_cols:
        if col in df_main.columns:
            df_main[col] = df_main[col].apply(clean_score)
        else:
            df_main[col] = None

    df_main["rank"] = df_main["rank"].apply(clean_rank)
    df_main["continent"] = df_main["country"].apply(country_to_continent)
    df_main["scraped_at"] = pd.Timestamp.now().isoformat()

    # Fill missing university_name with a placeholder so NOT NULL is satisfied
    df_main["university_name"] = df_main["university_name"].fillna("Unknown University")

    insert_cols = [
        "rank", "university_name", "country", "continent", "overall_score",
        "citations_per_faculty", "academic_reputation", "employer_reputation",
        "intl_faculty_ratio", "intl_student_ratio", "detail_url", "scraped_at",
    ]
    # Use INSERT OR REPLACE to handle re-runs gracefully
    for col in insert_cols:
        if col not in df_main.columns:
            df_main[col] = None

    df_main[insert_cols].to_sql(
        "universities", conn,
        if_exists="replace",
        index=False,
        method="multi",
    )
    print(f"[+] Inserted {len(df_main)} rows into universities table")

    # ── Layer B ──────────────────────────────────────────────────────────────
    if not DETAIL_CSV.exists():
        print(f"[!] {DETAIL_CSV} not found. Skipping detail ingestion.")
    else:
        print(f"[*] Loading {DETAIL_CSV} ...")
        df_detail = pd.read_csv(DETAIL_CSV)
        print(f"    {len(df_detail)} rows loaded")

        detail_score_cols = [
            "research_discovery", "learning_experience", "employability",
            "global_engagement", "sustainability",
        ]
        for col in detail_score_cols:
            if col in df_detail.columns:
                df_detail[col] = df_detail[col].apply(clean_score)
            else:
                df_detail[col] = None

        df_detail["scraped_at"] = pd.Timestamp.now().isoformat()

        # Map detail_url → university id
        cur.execute("SELECT id, detail_url FROM universities")
        url_to_id = {row[1]: row[0] for row in cur.fetchall()}
        df_detail["university_id"] = df_detail["detail_url"].map(url_to_id)
        df_detail = df_detail.dropna(subset=["university_id"]).copy()
        df_detail["university_id"] = df_detail["university_id"].astype(int)

        insert_cols_b = [
            "university_id", "research_discovery", "learning_experience",
            "employability", "global_engagement", "sustainability", "scraped_at",
        ]
        df_detail[insert_cols_b].to_sql(
            "university_details", conn,
            if_exists="replace",
            index=False,
            method="multi",
        )
        print(f"[+] Inserted {len(df_detail)} rows into university_details table")

    # ── View ─────────────────────────────────────────────────────────────────
    cur.executescript(VIEW_SQL)
    conn.commit()

    # Quick sanity check
    cur.execute("SELECT COUNT(*) FROM v_full_rankings")
    count = cur.fetchone()[0]
    print(f"[+] v_full_rankings view has {count} rows")

    conn.close()
    print(f"[+] Pipeline complete → {DB_PATH}")


if __name__ == "__main__":
    run_pipeline()
