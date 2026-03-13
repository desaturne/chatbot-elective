# Concepts & Technical Deep Dive

This document explains the core concepts, mechanisms, and technologies used in the QS Rankings Chatbot project. It's designed to help you understand *how* and *why* things work the way they do.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Web Scraping with Playwright](#2-web-scraping-with-playwright)
3. [Data Processing Pipeline](#3-data-processing-pipeline)
4. [Text Embeddings & Vector Representations](#4-text-embeddings--vector-representations)
5. [Vector Databases (ChromaDB)](#5-vector-databases-chromadb)
6. [Retrieval-Augmented Generation (RAG)](#6-retrieval-augmented-generation-rag)
7. [Text-to-SQL](#7-text-to-sql)
8. [Large Language Models (LLMs)](#8-large-language-models-llms)
9. [Query Routing Logic](#9-query-routing-logic)
10. [Complete End-to-End Flow](#10-complete-end-to-end-flow)

---

## 1. System Overview

### What Problem Are We Solving?

We want to let users ask natural language questions about university rankings and get intelligent answers. This requires:

1. **Data Collection**: Getting structured data from the QS website
2. **Data Storage**: Organizing data for efficient querying
3. **Semantic Understanding**: Understanding user intent beyond keyword matching
4. **Intelligent Response**: Generating human-like, informative answers

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          USER ASKS A QUESTION                            │
│                                                                          │
│     "Which UK university has the highest sustainability score?"         │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            QUERY ROUTER                                  │
│                                                                          │
│   Analyzes the question to determine the best processing method:        │
│   • Is it a greeting? → Direct response                                 │
│   • Is it mathematical? → Text-to-SQL                                   │
│   • Is it a profile request? → Database lookup                          │
│   • Is it semantic? → RAG                                               │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    ▼             ▼             ▼
              ┌──────────┐ ┌──────────┐ ┌──────────┐
              │   RAG    │ │   SQL    │ │ Profile  │
              │   Path   │ │   Path   │ │   Path   │
              └────┬─────┘ └────┬─────┘ └────┬─────┘
                   │            │            │
                   ▼            ▼            ▼
              ChromaDB      SQLite       SQLite
                   │            │            │
                   └────────────┼────────────┘
                                ▼
                          ┌──────────┐
                          │   LLM    │
                          │ Response │
                          └──────────┘
```

---

## 2. Web Scraping with Playwright

### Why Playwright?

Traditional scraping libraries like `requests` or `BeautifulSoup` struggle with modern websites because:

1. **JavaScript Rendering**: QS website loads data dynamically via JavaScript
2. **Anti-Bot Protection**: Sites detect and block automated requests
3. **Infinite Scroll**: Data loads as you scroll down

**Playwright** solves these by:
- Running an actual browser (Chromium, Firefox, or WebKit)
- Executing JavaScript like a real user
- Supporting stealth techniques to avoid detection

### Two-Layer Scraping Strategy

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            LAYER A                                       │
│                    Main Rankings Collection                              │
│                                                                          │
│  Goal: Get list of all universities with basic scores                   │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ Strategy 0: Direct REST API (Preferred)                         │   │
│  │                                                                 │   │
│  │   • QS has an internal API endpoint used by their frontend      │   │
│  │   • We extract the API NID from page's drupalSettings           │   │
│  │   • Directly query: /rankings/endpoint?nid=X&page=0             │   │
│  │   • Fast, reliable, structured JSON response                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                          │ Fallback if failed                           │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ Strategy 1: Network Interception                                │   │
│  │                                                                 │   │
│  │   • Listen to all network responses while loading the page      │   │
│  │   • Capture JSON responses that look like university data       │   │
│  │   • Parse and extract relevant fields                           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                          │ Fallback if failed                           │
│                          ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ Strategy 2: DOM Link Scan                                       │   │
│  │                                                                 │   │
│  │   • Find all <a href="/universities/..."> links                 │   │
│  │   • Extract university names and URLs from HTML                 │   │
│  │   • Last resort - slower and less complete                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            LAYER B                                       │
│                    Detail Page Scraping                                  │
│                                                                          │
│  Goal: Get detailed information for top universities                    │
│                                                                          │
│  For each university detail page:                                       │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 1. Navigate to university profile page                          │   │
│  │                                                                 │   │
│  │ 2. Scrape QS Lens Tab Scores:                                   │   │
│  │    • Research & Discovery                                       │   │
│  │    • Learning Experience                                        │   │
│  │    • Employability                                              │   │
│  │    • Global Engagement                                          │   │
│  │    • Sustainability                                             │   │
│  │    (Click each tab button, wait for content, extract score)    │   │
│  │                                                                 │   │
│  │ 3. Scrape qualitative data:                                     │   │
│  │    • University description (from <p> tags)                     │   │
│  │    • Key facts (type, founded year, student count)             │   │
│  │    • Student review snippets                                    │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Code Example: Stealth Scraping

```python
from playwright.async_api import async_playwright
from playwright_stealth import stealth_async

async def scrape_page():
    async with async_playwright() as pw:
        # Launch browser in headless mode (no visible window)
        browser = await pw.chromium.launch(headless=True)

        # Create context with realistic browser fingerprint
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)...",
            viewport={"width": 1440, "height": 900},
        )

        page = await context.new_page()

        # Apply stealth to avoid bot detection
        await stealth_async(page)

        # Navigate and interact
        await page.goto("https://example.com")
        content = await page.content()

        await browser.close()
        return content
```

### Why This Matters

- **Reliability**: Multiple fallback strategies ensure data collection succeeds
- **Completeness**: Two layers capture both breadth (all universities) and depth (detailed profiles)
- **Stealth**: Avoids IP bans and CAPTCHAs

---

## 3. Data Processing Pipeline

### Why Process Raw Data?

Raw scraped data is messy:
- Ranks like `"=42"` or `"501-510"` instead of clean numbers
- Scores with non-numeric characters
- Missing values
- No continent information (only countries)

The pipeline transforms this into clean, queryable data.

### Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RAW DATA (CSV)                                   │
│                                                                          │
│   rank: "=42", "501-510", "1001+"                                       │
│   scores: "95.4", "N/A", "-", "95.4 (est)"                              │
│   country: "United States", "UK", "China (Mainland)"                    │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         CLEANING STAGE                                   │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ clean_rank(val) → int | None                                    │   │
│   │                                                                 │   │
│   │   "=42"      → 42                                               │   │
│   │   "501-510"  → 501  (takes lower bound)                         │   │
│   │   "1001+"    → 1001                                             │   │
│   │   "N/A"      → None                                             │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ clean_score(val) → float | None                                 │   │
│   │                                                                 │   │
│   │   "95.4"        → 95.4                                          │   │
│   │   "95.4 (est)"  → 95.4                                          │   │
│   │   "N/A", "-"    → None                                          │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ country_to_continent(country) → str                             │   │
│   │                                                                 │   │
│   │   "United States"  → "North America"                            │   │
│   │   "Germany"        → "Europe"                                   │   │
│   │   "Japan"          → "Asia"                                     │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       DATABASE CREATION                                  │
│                                                                          │
│   ┌──────────────┐         ┌──────────────────┐                        │
│   │ universities │         │ university_      │                        │
│   │              │         │ details          │                        │
│   │ • id         │◄────────│ • university_id  │                        │
│   │ • rank       │  1:N    │ • research_...   │                        │
│   │ • name       │         │ • learning_...   │                        │
│   │ • country    │         │ • description    │                        │
│   │ • scores...  │         │ • reviews        │                        │
│   └──────────────┘         └──────────────────┘                        │
│           │                                               │              │
│           └─────────────────┬─────────────────────────────┘              │
│                             │                                            │
│                             ▼                                            │
│                   ┌──────────────────┐                                   │
│                   │ v_full_rankings  │                                   │
│                   │ (JOIN View)      │                                   │
│                   └──────────────────┘                                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why SQLite?

- **Embedded**: No server setup, just a file
- **Fast**: Optimized for read-heavy workloads
- **SQL Support**: Full SQL query capabilities
- **Portable**: Single file can be copied anywhere

---

## 4. Text Embeddings & Vector Representations

### The Problem with Keyword Search

Traditional search matches keywords:
- Query: "best engineering schools"
- Document: "MIT is renowned for its engineering programs"
- Problem: No keyword overlap! The document is relevant but won't match.

### Why Use Vectors? (Alternative Approaches Compared)

When building a semantic search system, several approaches exist:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    APPROACH COMPARISON                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. KEYWORD SEARCH (TF-IDF, BM25)                                          │
│     ─────────────────────────────────────────────────────────────────────── │
│     Pros: Fast, simple, no model needed                                    │
│     Cons: No semantic understanding, misses synonyms, context-blind        │
│                                                                             │
│     Example:                                                                │
│       Query: "sustainable campus"                                          │
│       Document: "eco-friendly university with green initiatives"           │
│       Result: NO MATCH (different words, same meaning)                     │
│                                                                             │
│  2. LLM EMBEDDINGS (OpenAI text-embedding-3, Cohere)                       │
│     ─────────────────────────────────────────────────────────────────────── │
│     Pros: Highest quality, very large dimensions (1536+)                   │
│     Cons: API costs, latency, requires internet, rate limits               │
│                                                                             │
│     Cost: ~$0.02 per 1M tokens (adds up for thousands of queries)          │
│                                                                             │
│  3. LOCAL SENTENCE TRANSFORMERS (OUR CHOICE)                               │
│     ─────────────────────────────────────────────────────────────────────── │
│     Pros: Free, fast, runs locally, no API limits, good quality           │
│     Cons: Smaller than LLM embeddings, requires initial download           │
│                                                                             │
│     Cost: FREE after model download (~90MB)                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Why We Chose Local Vectors:**
1. **Zero Cost**: No API fees for thousands of embedding operations
2. **Privacy**: University data stays local, not sent to external APIs
3. **Speed**: No network latency - embeddings generated in milliseconds
4. **Offline**: Works without internet connection after initial setup
5. **Control**: Full control over the model and its behavior

### Why all-MiniLM-L6-v2 Specifically?

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    MODEL SELECTION CRITERIA                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  all-MiniLM-L6-v2 is a distilled version of the larger MiniLM model.       │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    MODEL SPECIFICATIONS                              │   │
│  │                                                                      │   │
│  │  Model Name:     all-MiniLM-L6-v2                                   │   │
│  │  Parameters:     22 Million (tiny!)                                 │   │
│  │  Dimensions:     384 (compact)                                      │   │
│  │  Model Size:     ~80 MB                                             │   │
│  │  Max Sequence:   256 tokens                                         │   │
│  │  Speed:          ~14,000 sentences/second (CPU)                     │   │
│  │                                                                      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  COMPARISON WITH OTHER MODELS:                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ Model                    │ Size    │ Dims │ Speed  │ Quality (MTEB) │ │
│  ├───────────────────────────────────────────────────────────────────────┤ │
│  │ all-MiniLM-L6-v2         │ 80 MB   │ 384  │ ⚡⚡⚡   │ 56.3           │ │
│  │ all-mpnet-base-v2        │ 420 MB  │ 768  │ ⚡⚡    │ 57.8           │ │
│  │ OpenAI text-embedding-3  │ API     │ 1536 │ ⚡     │ 62.3           │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  WHY IT'S PERFECT FOR THIS PROJECT:                                        │
│                                                                             │
│  ✅ Small Size: Downloads quickly, low memory usage                        │
│  ✅ Fast: Can embed 1500 universities in ~1 second on CPU                  │
│  ✅ Good Enough: 95% of larger model quality at 20% of the size            │
│  ✅ No GPU Needed: Runs efficiently on CPU                                 │
│  ✅ Well Tested: 10M+ downloads, battle-tested in production               │
│                                                                             │
│  The "L6" means it has 6 transformer layers (shallow = fast)              │
│  The "all-" prefix means it was trained on all available datasets         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What Are Embeddings?

**Embeddings** convert text into arrays of numbers (vectors) that capture semantic meaning.

```
Text                          Embedding Vector
─────────────────────────────────────────────────────────
"MIT is great for engineering"  → [0.23, -0.45, 0.12, ..., 0.67]
"Best engineering universities"  → [0.21, -0.43, 0.15, ..., 0.64]
"I love pizza"                   → [-0.89, 0.12, -0.34, ..., -0.56]

Similar meanings → Similar vectors (close together in space)
Different meanings → Different vectors (far apart in space)
```

### How Embeddings Are Created

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    EMBEDDING MODEL: all-MiniLM-L6-v2                     │
│                                                                          │
│   A neural network trained to:                                          │
│   • Similar sentences → Similar vectors                                 │
│   • Different sentences → Different vectors                             │
│                                                                          │
│   Training approach: Contrastive Learning                               │
│   • Given: sentence pairs (similar/different)                           │
│   • Learn: vector representations that reflect similarity               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    EMBEDDING PROCESS                                     │
│                                                                          │
│   Input Text:                                                            │
│   "MIT is ranked #1 globally. Located in United States. Overall         │
│    score: 96.1. Known for engineering and technology research..."       │
│                                                                          │
│   Tokenization:                                                          │
│   ["MIT", "is", "ranked", "#1", "globally", ".", "Located", ...]        │
│                                                                          │
│   Neural Network Processing:                                            │
│   [Tokenizer] → [Transformer Encoder] → [Mean Pooling] → Vector        │
│                                                                          │
│   Output Vector (384 dimensions):                                       │
│   [0.234, -0.456, 0.123, 0.789, ..., 0.345]  ← 384 floating point #s  │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why 384 Dimensions?

Each dimension captures a different semantic feature:
- Dimension 0 might capture "academic/research orientation"
- Dimension 1 might capture "location (Americas vs elsewhere)"
- Dimension 2 might capture "prestige level"
- ... and so on

These aren't human-interpretable, but together they encode meaning.

### Cosine Similarity

To compare two vectors, we use **cosine similarity**:

```
similarity = (A · B) / (|A| × |B|)

Where:
- A · B = dot product of vectors
- |A| = magnitude (length) of vector A

Result: -1 to 1
- 1.0 = identical meaning
- 0.0 = unrelated
- -1.0 = opposite meaning
```

### Code: Creating Embeddings

```python
from sentence_transformers import SentenceTransformer

# Load pre-trained model (downloads on first use)
model = SentenceTransformer('all-MiniLM-L6-v2')

# Text to embed
texts = [
    "MIT is the top engineering university in the USA",
    "Stanford excels in computer science and entrepreneurship",
    "Oxford is one of the oldest universities in the world"
]

# Generate embeddings
embeddings = model.encode(texts)

# embeddings.shape = (3, 384)
# Each text is now a 384-dimensional vector
```

---

## 5. Vector Databases (ChromaDB)

### Why Not Just Store Vectors in a List?

You could, but searching becomes slow:

```python
# Naive approach - O(n) search
for stored_vector in all_vectors:
    if cosine_similarity(query_vector, stored_vector) > threshold:
        return document
```

For 1500 universities this is fine, but for millions? Too slow.

### Vector Database Benefits

Vector databases use **Approximate Nearest Neighbor (ANN)** algorithms:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CHROMADB ARCHITECTURE                                 │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    HNSW Index                                    │   │
│   │          (Hierarchical Navigable Small World)                   │   │
│   │                                                                 │   │
│   │   A graph-based index that:                                     │   │
│   │   • Organizes vectors in a multi-layer graph                    │   │
│   │   • Enables O(log n) search instead of O(n)                     │   │
│   │   • Trades small accuracy for massive speed gains               │   │
│   │                                                                 │   │
│   │   Search: Start at top layer, navigate down to find nearest     │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    Storage                                       │   │
│   │                                                                 │   │
│   │   Collection: "qs_rankings"                                     │   │
│   │   ├── documents: ["MIT is ranked...", "Stanford is...", ...]   │   │
│   │   ├── embeddings: [[0.23, ...], [0.45, ...], ...]              │   │
│   │   ├── metadatas: [{name: "MIT", rank: 1}, ...]                 │   │
│   │   └── ids: ["uni_1", "uni_2", ...]                             │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### How Search Works in ChromaDB

```python
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# Connect to persistent storage
client = chromadb.PersistentClient(path="./data/chroma_db")

# Get collection with embedding function
ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
collection = client.get_collection("qs_rankings", embedding_function=ef)

# Search! ChromaDB automatically:
# 1. Embeds your query
# 2. Finds nearest vectors using HNSW index
# 3. Returns matching documents with metadata
results = collection.query(
    query_texts=["Which universities are best for engineering?"],
    n_results=5
)

# Results include:
# - documents: The actual text
# - metadatas: University info (name, rank, country)
# - distances: How similar (lower = more similar)
```

### Persistence

ChromaDB saves to disk, so embeddings survive restarts:
```
data/chroma_db/
├── chroma.sqlite3      # Metadata
└── {collection_id}/    # Vector index files
```

### How Rich Embeddings Are Built

The quality of vector search depends heavily on **what** we embed. A simple "MIT" produces a vague embedding, but a detailed paragraph creates a rich, searchable representation.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BUILDING RICH DOCUMENT TEXT                               │
│                    (from embedder.py)                                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Raw Database Row (many columns, sparse data):                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ university_name: "MIT"                                                │ │
│  │ rank: 1                                                               │ │
│  │ country: "United States"                                              │ │
│  │ continent: "North America"                                            │ │
│  │ overall_score: 96.1                                                   │ │
│  │ academic_reputation: 100.0                                            │ │
│  │ research_discovery: 98.5                                              │ │
│  │ description: "MIT is a private research university..."                │ │
│  │ university_type: "Private"                                            │ │
│  │ founded_year: 1861                                                    │ │
│  │ review_snippets: "Great for engineering..."                           │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                              │                                              │
│                              ▼                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                    DOCUMENT BUILDER FUNCTION                           │ │
│  │                                                                        │ │
│  │   def build_document_text(row: dict) -> str:                          │ │
│  │       # Combine ALL available data into natural language             │ │
│  │       return f"""                                                      │ │
│  │         MIT is ranked #1 globally in the QS World University          │ │
│  │         Rankings. Located in United States (North America).           │ │
│  │         Type: Private. Founded: 1861. Total students: 11520.          │ │
│  │                                                                         │ │
│  │         Overall score: 96.1. Key ranking metrics —                    │ │
│  │         Academic Reputation: 100.0, Employer Reputation: 100.0,       │ │
│  │         Citations per Faculty: 99.9, Intl Faculty Ratio: 91.4.        │ │
│  │                                                                         │ │
│  │         Lens scores — Research & Discovery: 98.5,                     │ │
│  │         Learning Experience: 95.2, Employability: 97.8,               │ │
│  │         Global Engagement: 94.1, Sustainability: 95.6.                │ │
│  │                                                                         │ │
│  │         About: MIT is a private research university known for...      │ │
│  │                                                                         │ │
│  │         Student reviews: Great for engineering, intense workload...   │ │
│  │       """                                                               │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                              │                                              │
│                              ▼                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                    WHY THIS APPROACH WORKS                             │ │
│  │                                                                        │ │
│  │  1. NATURAL LANGUAGE: Embedding models understand prose, not tables  │ │
│  │                                                                        │ │
│  │  2. CONTEXT: "ranked #1" is more meaningful than just "rank: 1"      │ │
│  │                                                                        │ │
│  │  3. KEYWORDS: Includes terms users might search for:                  │ │
│  │     "sustainability", "research", "employability", etc.               │ │
│  │                                                                        │ │
│  │  4. COMPLETENESS: Every piece of data contributes to the vector      │ │
│  │                                                                        │ │
│  │  5. STRUCTURE: Organized sections help the model understand           │ │
│  │     relationships between different data points                       │ │
│  │                                                                        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code from embedder.py:**

```python
def build_document_text(row: dict) -> str:
    """Convert a university row into a rich natural-language paragraph."""

    # Helper to format scores
    def fmt(val, default="N/A"):
        if val is None or str(val).strip() in ("", "None"):
            return default
        try:
            return f"{float(val):.1f}"
        except (ValueError, TypeError):
            return str(val)

    # Build key facts section
    facts_parts = []
    if row.get("university_type"):
        facts_parts.append(f"Type: {row['university_type']}")
    if row.get("founded_year"):
        facts_parts.append(f"Founded: {row['founded_year']}")
    if row.get("total_students"):
        facts_parts.append(f"Total students: {row['total_students']}")
    facts_str = (", ".join(facts_parts) + ". ") if facts_parts else ""

    # Add description (truncated to avoid token limits)
    description = str(row.get("description") or "").strip()
    desc_str = (f"About: {description[:600]} ") if description else ""

    # Add reviews (also truncated)
    reviews = str(row.get("review_snippets") or "").strip()
    review_str = (f"Student reviews: {reviews[:400]} ") if reviews else ""

    # Combine everything into a rich document
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
        # ... more metrics ...
        f"{review_str}"
    )
```

**Result:** Each university becomes a ~500-word document that captures:
- Identity (name, rank, location)
- Facts (type, founded year, size)
- Scores (all 11+ metrics)
- Qualitative data (description, reviews)

This creates embeddings that can match queries on ANY of these dimensions!

---

## 6. Retrieval-Augmented Generation (RAG)

### The Problem with Pure LLMs

LLMs have limitations:
- **No knowledge of your data**: They don't know about specific universities
- **Hallucinations**: They might make up facts
- **Outdated**: Training data is months/years old

### RAG Solution

**RAG** = Retrieval + Generation

1. **Retrieve**: Find relevant documents from your database
2. **Augment**: Add those documents to the LLM prompt
3. **Generate**: LLM answers using the provided context

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         RAG PIPELINE                                     │
│                                                                          │
│   User Query: "Tell me about MIT's sustainability initiatives"          │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 1: Embed the Query                                         │   │
│   │                                                                 │   │
│   │   Query → Embedding Model → Query Vector                        │   │
│   │   [0.23, -0.45, 0.12, ...]                                      │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 2: Vector Search                                            │   │
│   │                                                                 │   │
│   │   Query Vector vs. All Stored Vectors                           │   │
│   │   → Find top 5 most similar                                     │   │
│   │                                                                 │   │
│   │   Results:                                                       │   │
│   │   1. MIT profile (similarity: 0.89)                             │   │
│   │   2. Stanford sustainability (similarity: 0.72)                 │   │
│   │   3. Harvard environmental programs (similarity: 0.68)          │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 3: Build Context                                            │   │
│   │                                                                 │   │
│   │   Retrieved Documents:                                           │   │
│   │   ─────────────────────────────────────────────                 │   │
│   │   [Source 1]                                                     │   │
│   │   MIT is ranked #1 globally. Sustainability score: 95.2.         │   │
│   │   The university has committed to carbon neutrality by 2050...   │   │
│   │                                                                 │   │
│   │   [Source 2]                                                     │   │
│   │   Stanford's sustainability initiatives include...               │   │
│   │   ─────────────────────────────────────────────                 │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 4: Prompt Construction                                      │   │
│   │                                                                 │   │
│   │   You are a university advisor. Answer using ONLY the context   │   │
│   │   below. If the answer is not in the context, say so.           │   │
│   │                                                                 │   │
│   │   Context:                                                       │   │
│   │   [Source 1] MIT is ranked #1...Sustainability score: 95.2...   │   │
│   │   [Source 2] Stanford's sustainability...                       │   │
│   │                                                                 │   │
│   │   Question: Tell me about MIT's sustainability initiatives      │   │
│   │   Answer:                                                        │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 5: LLM Generation                                           │   │
│   │                                                                 │   │
│   │   LLM reads context and generates answer:                       │   │
│   │                                                                 │   │
│   │   "MIT demonstrates strong commitment to sustainability with     │   │
│   │    a score of 95.2. The university has committed to achieving    │   │
│   │    carbon neutrality by 2050..."                                 │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### RAG Code Implementation

```python
def _run_rag_query(query: str, client, model: str, n_results: int = 5) -> tuple[str, list]:
    # 1. Connect to vector store with embedding function
    ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
    chroma_client = chromadb.PersistentClient(path="./data/chroma_db")
    collection = chroma_client.get_collection("qs_rankings", embedding_function=ef)

    # 2. Retrieve relevant documents
    # ChromaDB automatically embeds the query using the provided function
    results = collection.query(
        query_texts=[query],
        n_results=n_results,
        include=["documents", "metadatas", "distances"]
    )

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    # 3. Build context string
    context = "\n\n".join([f"[Source {i+1}]\n{doc}" for i, doc in enumerate(docs)])

    # 4. Create prompt for LLM
    prompt = f"""You are an expert university advisor. Answer the question using ONLY the context below.

Context:
{context}

Question: {query}
Answer:"""

    # 5. Get LLM response
    answer = llm_complete(client, model, prompt, temperature=0.4, max_tokens=800)

    # 6. Build reference cards for UI
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
```

### Why RAG is Powerful

| Aspect | Pure LLM | RAG |
|--------|----------|-----|
| Knowledge | Limited to training data | Your entire database |
| Accuracy | Can hallucinate | Grounded in real data |
| Up-to-date | Frozen at training time | As fresh as your data |
| Citations | Cannot cite sources | Can reference specific documents |

---

## 7. Text-to-SQL

### When to Use SQL vs RAG?

| Query Type | Best Approach | Example |
|------------|---------------|---------|
| Semantic/Descriptive | RAG | "Which universities focus on sustainability?" |
| Exact/Aggregation | SQL | "Average score of top 50 universities" |
| Comparison | SQL | "Top 5 universities by employer reputation" |
| Counting | SQL | "How many UK universities in top 100?" |

### Text-to-SQL Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      TEXT-TO-SQL PIPELINE                                │
│                                                                          │
│   User Query: "What is the average overall score of UK universities?"   │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 1: Schema Understanding                                    │   │
│   │                                                                 │   │
│   │   LLM is given the database schema:                             │   │
│   │   - Table names and columns                                     │   │
│   │   - Column types (TEXT, INTEGER, REAL)                          │   │
│   │   - Column meanings (rank, score, etc.)                         │   │
│   │   - Important notes (lower rank = better)                       │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 2: SQL Generation                                          │   │
│   │                                                                 │   │
│   │   LLM Prompt:                                                   │   │
│   │   "Write a SQLite SELECT query to answer: What is the average   │   │
│   │    overall score of UK universities? Return ONLY the SQL."       │   │
│   │                                                                 │   │
│   │   LLM Output:                                                   │   │
│   │   SELECT AVG(overall_score)                                     │   │
│   │   FROM v_full_rankings                                          │   │
│   │   WHERE country = 'United Kingdom'                              │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 3: Safety Check                                            │   │
│   │                                                                 │   │
│   │   Only allow SELECT statements (no INSERT, UPDATE, DELETE)      │   │
│   │   Strip dangerous patterns                                      │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 4: Execute Query                                           │   │
│   │                                                                 │   │
│   │   conn = sqlite3.connect("qs_rankings.db")                      │   │
│   │   results = conn.execute(sql).fetchall()                        │   │
│   │   # [(78.5,)]                                                   │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │ STEP 5: Natural Language Summary                                │   │
│   │                                                                 │   │
│   │   LLM Prompt:                                                   │   │
│   │   "The user asked: 'What is the average overall score...'       │   │
│   │    The query returned: [(78.5,)]                                │   │
│   │    Please summarize in natural language."                        │   │
│   │                                                                 │   │
│   │   LLM Output:                                                   │   │
│   │   "The average overall score of UK universities is 78.5 out of  │   │
│   │    100. This is based on all UK universities in the QS          │   │
│   │    World University Rankings database."                          │   │
│   └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Code Implementation

```python
def _run_text_to_sql(query: str, client, model: str) -> str:
    # 1. Generate SQL using LLM
    sql_prompt = f"""You are a SQLite expert. Write a SELECT query.

Schema: {SCHEMA_DESCRIPTION}

Question: {query}
Return ONLY the SQL query, no explanation:"""

    sql_raw = llm_complete(client, model, sql_prompt, temperature=0)

    # Strip markdown code fences if present
    sql = re.sub(r"```(?:sql)?", "", sql_raw).strip()

    # 2. Safety check - only allow SELECT
    if not sql.upper().lstrip().startswith("SELECT"):
        return "I can only run SELECT queries."

    # 3. Execute query on SQLite database
    conn = sqlite3.connect("data/processed/qs_rankings.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql)
    results = [dict(r) for r in cur.fetchall()]
    conn.close()

    # 4. Summarize results in natural language
    summary_prompt = f"""The user asked: "{query}"
Results: {results[:20]}
Summarize in natural language, citing specific numbers:"""

    return llm_complete(client, model, summary_prompt, temperature=0.3)
```

---

## 8. Natural Language Processing (NLP) in This Project

### What is NLP?

**Natural Language Processing** is the field of AI that deals with understanding and generating human language. This project uses NLP at multiple stages:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    NLP COMPONENTS IN THIS PROJECT                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. TOKENIZATION                                                            │
│     ─────────────────────────────────────────────────────────────────────── │
│     Breaking text into smaller units (tokens) for processing.              │
│                                                                             │
│     Input:  "Tell me about MIT's sustainability score"                     │
│     Output: ["Tell", "me", "about", "MIT", "'s", "sustainability", "score"]│
│                                                                             │
│     Used in: Embedding model, LLM prompts                                  │
│                                                                             │
│  2. SEMANTIC UNDERSTANDING                                                  │
│     ─────────────────────────────────────────────────────────────────────── │
│     Understanding meaning beyond keywords.                                 │
│                                                                             │
│     Query: "eco-friendly universities"                                     │
│     Matches: Documents about "sustainability", "green initiatives"         │
│                                                                             │
│     Used in: Vector search (embeddings capture semantics)                  │
│                                                                             │
│  3. INTENT CLASSIFICATION                                                  │
│     ─────────────────────────────────────────────────────────────────────── │
│     Determining what the user wants to do.                                 │
│                                                                             │
│     Query: "Top 10 universities in UK"                                     │
│     Intent: AGGREGATION → Route to SQL path                                │
│                                                                             │
│     Query: "Tell me about Harvard"                                         │
│     Intent: PROFILE → Route to profile path                                │
│                                                                             │
│     Used in: Query router (regex patterns + keyword matching)              │
│                                                                             │
│  4. TEXT GENERATION                                                         │
│     ─────────────────────────────────────────────────────────────────────── │
│     Producing human-like responses.                                        │
│                                                                             │
│     Input:  SQL results + user query                                       │
│     Output: "The average score of UK universities is 78.5 out of 100..."   │
│                                                                             │
│     Used in: LLM response generation                                       │
│                                                                             │
│  5. NAMED ENTITY RECOGNITION (Implicit)                                    │
│     ─────────────────────────────────────────────────────────────────────── │
│     Identifying university names in queries.                               │
│                                                                             │
│     Query: "Tell me about Stanford University"                             │
│     Entity: "Stanford University" → Database lookup                        │
│                                                                             │
│     Used in: Profile path (regex extraction)                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### How Query Understanding Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    QUERY UNDERSTANDING PIPELINE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   User Query: "Which UK university has the highest sustainability score?"  │
│                                                                             │
│   STEP 1: Text Normalization                                               │
│   ───────────────────────────────                                          │
│   • Convert to lowercase                                                   │
│   • Remove extra whitespace                                                │
│   • Handle punctuation                                                     │
│                                                                             │
│   STEP 2: Pattern Matching                                                 │
│   ───────────────────────────────                                          │
│   • Check against GREETING_PATTERNS → No match                             │
│   • Check against MATH_KEYWORDS → "highest" matches!                       │
│   • Decision: SQL PATH                                                     │
│                                                                             │
│   STEP 3: Entity Extraction (if profile query)                             │
│   ───────────────────────────────────────────────────────────────          │
│   • Extract university name using PROFILE_PATTERNS                         │
│   • Example: "tell me about [MIT]" → entity = "MIT"                        │
│                                                                             │
│   STEP 4: Route to Appropriate Handler                                     │
│   ───────────────────────────────────────────────────────────────          │
│   • Greeting → Direct response (no LLM needed)                             │
│   • SQL → Text-to-SQL pipeline                                             │
│   • Profile → Database lookup + LLM narration                              │
│   • RAG → Vector search + LLM response                                     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Why Regex-Based Routing Instead of ML Classification?

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ROUTING APPROACH COMPARISON                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  OPTION A: ML Classifier (Logistic Regression, BERT, etc.)                 │
│  ───────────────────────────────────────────────────────────────────────    │
│  Pros: Can learn complex patterns, handles edge cases                      │
│  Cons: Requires training data, adds latency, needs maintenance             │
│                                                                             │
│  OPTION B: LLM-Based Classification                                        │
│  ───────────────────────────────────────────────────────────────────────    │
│  Pros: Very accurate, handles nuance                                       │
│  Cons: Adds latency + cost for every query, overkill for simple routing    │
│                                                                             │
│  OPTION C: Regex + Keyword Matching (OUR CHOICE)                           │
│  ───────────────────────────────────────────────────────────────────────    │
│  Pros: Zero latency, zero cost, easily modifiable, predictable             │
│  Cons: Requires manual pattern definition, may miss edge cases             │
│                                                                             │
│  WHY THIS WORKS FOR US:                                                    │
│  • Our query types are well-defined and distinct                           │
│  • Keywords like "average", "top", "highest" clearly indicate SQL          │
│  • Profile patterns like "tell me about X" are consistent                  │
│  • Greetings are easy to match with regex                                  │
│  • Zero latency = instant response for greetings                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### The Role of Context in NLP

When the LLM generates responses, it uses **context** from multiple sources:

```python
# Example: RAG prompt construction
context = """
[Source 1]
MIT is ranked #1 globally. Located in United States.
Overall score: 96.1. Sustainability: 95.6.
Known for engineering and technology research...

[Source 2]
Stanford is ranked #5 globally. Located in United States.
Overall score: 94.3. Sustainability: 92.1...
"""

prompt = f"""You are an expert university advisor.
Answer the question using ONLY the context below.

Context:
{context}

Question: {user_query}
Answer:"""
```

**Why context matters:**
1. **Grounding**: Prevents hallucination by restricting answers to real data
2. **Specificity**: Provides exact numbers and names to cite
3. **Relevance**: Only includes information related to the query
4. **Consistency**: All users get factually correct answers

---

## 9. Large Language Models (LLMs)

### What is an LLM?

An LLM is a neural network trained on massive text data to:
1. Understand natural language
2. Generate coherent, contextually appropriate responses

### How LLMs Work (Simplified)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      LLM INFERENCE                                       │
│                                                                          │
│   Input: "The capital of France is"                                     │
│                                                                          │
│   Tokenization: ["The", "capital", "of", "France", "is"]                │
│                              │                                          │
│                              ▼                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                    Transformer Model                             │   │
│   │                                                                 │   │
│   │   Layers of attention mechanisms that:                          │   │
│   │   • Understand relationships between tokens                     │   │
│   │   • Build contextual representations                           │   │
│   │   • Predict next token probabilities                           │   │
│   │                                                                 │   │
│   │   "France" ← attends to → "capital"                             │   │
│   │   Context: asking about a capital city                          │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│   Token Probabilities:                                                   │
│   "Paris":     0.85                                                     │
│   "Lyon":      0.03                                                     │
│   "Marseille": 0.02                                                     │
│   ...                                                                    │
│                              │                                          │
│                              ▼                                          │
│   Output: "Paris"                                                       │
└─────────────────────────────────────────────────────────────────────────┘
```

### Why We Use LLMs in This Project

| Task | Why LLM? |
|------|----------|
| SQL Generation | Understands natural language → SQL translation |
| RAG Answer | Synthesizes information from retrieved documents |
| Summarization | Converts raw data into natural language |
| Greeting Response | Understands context and generates appropriate reply |

### LLM Providers We Support

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         GROQ (Recommended)                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  WHAT IS GROQ?                                                              │
│  ───────────────────────────────────────────────────────────────────────    │
│  Groq is an AI inference company that provides extremely fast LLM APIs     │
│  using their custom-designed Language Processing Unit (LPU) hardware.      │
│                                                                             │
│  WHY GROQ FOR THIS PROJECT?                                                 │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                             │
│  1. SPEED                                                                   │
│     • Traditional GPUs: ~50 tokens/second for 70B models                   │
│     • Groq LPUs: ~300+ tokens/second (6x faster!)                          │
│     • User gets answers in ~1-2 seconds instead of 5-10 seconds            │
│                                                                             │
│  2. FREE TIER                                                               │
│     • Generous free tier with rate limits                                  │
│     • Perfect for development and testing                                  │
│     • No credit card required to start                                     │
│                                                                             │
│  3. OPENAI-COMPATIBLE API                                                   │
│     • Same API format as OpenAI                                            │
│     • Just change base_url and api_key                                     │
│     • No code changes needed to switch providers                           │
│                                                                             │
│  4. OPEN SOURCE MODELS                                                      │
│     • Llama 3.3 70B (Meta's latest)                                        │
│     • Mixtral 8x7B                                                         │
│     • Gemma (Google)                                                       │
│     • No vendor lock-in - models available elsewhere                       │
│                                                                             │
│  AVAILABLE MODELS:                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ Model                    │ Parameters │ Context │ Best For         │    │
│  ├────────────────────────────────────────────────────────────────────┤    │
│  │ llama-3.3-70b-versatile  │ 70B        │ 128K    │ General use      │    │
│  │ llama-3.1-8b-instant     │ 8B         │ 128K    │ Fast, simple     │    │
│  │ mixtral-8x7b-32768       │ 47B        │ 32K     │ Multilingual     │    │
│  │ gemma2-9b-it             │ 9B         │ 8K      │ Instruction      │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  RATE LIMITS (Free Tier):                                                  │
│  • Requests per minute: 30                                                 │
│  • Tokens per minute: 7,000                                                │
│  • Tokens per day: 1,000,000                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                         OPENAI (Alternative)                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  • Paid API (pay per token)                                                │
│  • Models: GPT-4o-mini, GPT-4o, GPT-3.5-turbo                              │
│  • Higher quality for complex reasoning                                    │
│  • Cost: ~$0.15 per 1M input tokens (GPT-4o-mini)                          │
│                                                                             │
│  WHEN TO USE OPENAI:                                                        │
│  • Need GPT-4 level reasoning                                              │
│  • Complex multi-step tasks                                                │
│  • Budget is not a concern                                                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### How Groq API Integration Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    GROQ API INTEGRATION FLOW                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐      │
│   │   User Query    │────▶│  chatbot.py     │────▶│   Groq API      │      │
│   │                 │     │                 │     │                 │      │
│   │ "Top 10 unis    │     │ Build prompt    │     │ Process with    │      │
│   │  in UK"         │     │ with context    │     │ Llama 3.3 70B   │      │
│   └─────────────────┘     └─────────────────┘     └─────────────────┘      │
│                                                           │                │
│                                                           ▼                │
│   ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐      │
│   │   User sees     │◀────│  Parse response │◀────│ JSON response   │      │
│   │   answer        │     │  extract text   │     │ with answer     │      │
│   └─────────────────┘     └─────────────────┘     └─────────────────┘      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Code: Groq Client Setup

```python
import os
from openai import OpenAI

def _get_llm_client():
    """
    Returns (client, model_name) based on LLM_PROVIDER environment variable.
    Groq uses OpenAI-compatible API - just different base_url.
    """
    provider = os.getenv("LLM_PROVIDER", "groq").lower()

    if provider == "openai":
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    else:  # groq (default)
        client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"  # Groq's endpoint
        )
        model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    return client, model


def _llm_complete(client, model, prompt: str, temperature: float = 0.3, max_tokens: int = 500) -> str:
    """
    Send prompt to LLM and get response.
    Works identically for both Groq and OpenAI (same API format).
    """
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens
    )
    return response.choices[0].message.content
```

### Why OpenAI-Compatible API Matters

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    API COMPATIBILITY BENEFITS                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Groq designed their API to match OpenAI's format exactly.                 │
│                                                                             │
│  BENEFITS:                                                                  │
│                                                                             │
│  1. CODE PORTABILITY                                                        │
│     Same code works with either provider:                                  │
│                                                                             │
│     # Groq                                                                  │
│     client = OpenAI(base_url="https://api.groq.com/openai/v1", ...)        │
│                                                                             │
│     # OpenAI                                                                │
│     client = OpenAI(...)  # default base_url                               │
│                                                                             │
│  2. EASY SWITCHING                                                          │
│     Change one environment variable to switch providers:                   │
│                                                                             │
│     LLM_PROVIDER=groq   # Use Groq (free, fast)                            │
│     LLM_PROVIDER=openai # Use OpenAI (paid, GPT-4)                         │
│                                                                             │
│  3. ECOSYSTEM COMPATIBILITY                                                 │
│     Works with existing tools:                                             │
│     • LangChain                                                            │
│     • LlamaIndex                                                           │
│     • Any OpenAI SDK                                                       │
│                                                                             │
│  4. FUTURE-PROOF                                                            │
│     New OpenAI-compatible providers can be added easily:                   │
│     • Together AI                                                          │
│     • Anyscale                                                             │
│     • Fireworks AI                                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### API Usage

```python
from openai import OpenAI

# Groq client (OpenAI-compatible)
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Hello!"}],
    temperature=0.3,
    max_tokens=500
)

answer = response.choices[0].message.content
```

### Temperature Parameter

```
Temperature: 0.0          Temperature: 0.5          Temperature: 1.0
    │                          │                          │
    ▼                          ▼                          ▼
Deterministic             Balanced creativity       Maximum creativity
Same output every         Varied but relevant        More random outputs
time for same input       outputs                    May diverge from topic

Use for:                  Use for:                   Use for:
• SQL generation          • RAG answers              • Creative writing
• Data extraction         • Summaries                • Brainstorming
```

---

## 9. Query Routing Logic

### The Router's Job

Before processing a query, we need to determine the best approach:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         QUERY ROUTER                                      │
│                                                                          │
│   Input: User's natural language query                                  │
│   Output: Processing path + parameters                                  │
└─────────────────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      ROUTING DECISION TREE                               │
│                                                                          │
│                         ┌─────────────┐                                 │
│                         │ User Query  │                                 │
│                         └──────┬──────┘                                 │
│                                │                                        │
│                                ▼                                        │
│                    ┌───────────────────────┐                            │
│                    │ Is it a greeting?     │                            │
│                    │ (hi, hello, help)     │                            │
│                    └───────────┬───────────┘                            │
│                          Yes ──┤── No                                   │
│                          │     │                                        │
│                          ▼     ▼                                        │
│                    ┌───────┐  ┌───────────────────────┐                 │
│                    │GREETING│ │ Is it a math query?   │                 │
│                    │ PATH  │ │ (top, average, count,  │                 │
│                    └───────┘ │  highest, ranked #X)   │                 │
│                              └───────────┬───────────┘                 │
│                                    Yes ──┤── No                         │
│                                    │     │                              │
│                                    ▼     ▼                              │
│                              ┌───────┐  ┌───────────────────────┐       │
│                              │  SQL  │  │ Is it a profile query?│       │
│                              │ PATH  │  │ (tell me about X,     │       │
│                              └───────┘  │  X overview)          │       │
│                                        └───────────┬───────────┘       │
│                                              Yes ──┤── No               │
│                                              │     │                    │
│                                              ▼     ▼                    │
│                                        ┌────────┐ ┌───────┐            │
│                                        │PROFILE │ │  RAG  │            │
│                                        │ PATH   │ │ PATH  │            │
│                                        └────────┘ └───────┘            │
└─────────────────────────────────────────────────────────────────────────┘
```

### Pattern Matching Examples

```python
# Math keywords trigger SQL path
MATH_KEYWORDS = [
    "average", "highest", "lowest", "count", "how many",
    "top ", "bottom ", "compare", "rank between", "greater than",
    "less than", "sum", "total", "minimum", "maximum",
    "which country has the most", "how does", "median", "percentile",
    "rank ", "ranked ", "ranking ", "which is the", "who is ranked",
]

# Rank number patterns trigger SQL path
RANK_NUMBER_RE = re.compile(
    r"\b(?:rank(?:ed)?\s*#?\s*\d+|\d+\s*(?:st|nd|rd|th)\s*rank(?:ed)?|#\s*\d+)\b",
    re.IGNORECASE
)
# Matches: "ranked 8th", "rank #5", "5th ranked", "#8 ranked"

# Profile patterns trigger profile path
PROFILE_PATTERNS = [
    r"tell me about (.+)",
    r"(?:describe|overview of|about|what (?:is|are)|info(?:rmation)? (?:on|about)|details? (?:on|about)) (.+)",
    r"(.+) university (?:description|profile|overview|review)",
    r"what (?:do you know|can you tell me) about (.+)",
    r"(.+) (?:ranking|rank|score|rating|review)s?$",
]

# Greeting patterns trigger direct response (no LLM call needed)
GREETING_PATTERNS = [
    r"^hi\b", r"^hello\b", r"^hey\b", r"^hola\b", r"^greetings\b",
    r"^good\s*(morning|afternoon|evening)\b",
    r"^howdy\b", r"^what'?s?\s*up\b", r"^yo\b",
    r"^help\b", r"^what\s+can\s+you\s+do\b", r"^who\s+are\s+you\b",
    r"^thanks?\b", r"^thank\s*you\b", r"^thx\b",
]
```

### Why Routing Matters

| Path | Latency | Use Case |
|------|---------|----------|
| Greeting | ~0ms | Immediate response, no LLM call |
| SQL | ~1-2s | Precise numerical queries |
| Profile | ~2-3s | Rich university information |
| RAG | ~3-5s | Semantic understanding, comparisons |

---

## 10. Complete End-to-End Flow

### Full System Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    COMPLETE SYSTEM FLOW                                   │
└─────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
PHASE 1: DATA COLLECTION (scraper.py)
═══════════════════════════════════════════════════════════════════════════

    ┌─────────────────┐
    │  QS Website     │
    │ topuniversities │
    └────────┬────────┘
             │ Playwright browser automation
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ LAYER A: Rankings List (1500+ universities)                     │
    │   • Direct API call OR network interception OR DOM scanning     │
    │   • Extract: rank, name, country, scores, detail URLs          │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ LAYER B: Detail Pages (top 200)                                 │
    │   • Visit each university page                                  │
    │   • Click tabs to get lens scores                               │
    │   • Extract: description, facts, reviews                        │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────┐
    │ Raw CSV Files   │  data/raw/rankings_main.csv
    │                 │  data/raw/rankings_detail.csv
    └─────────────────┘

═══════════════════════════════════════════════════════════════════════════
PHASE 2: DATA PROCESSING (pipeline.py)
═══════════════════════════════════════════════════════════════════════════

    ┌─────────────────┐
    │ Raw CSV Files   │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ CLEANING                                                         │
    │   • Parse messy rank formats ("=42" → 42)                       │
    │   • Clean scores (remove non-numeric)                            │
    │   • Map countries → continents                                   │
    │   • Handle NULL values                                           │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ DATABASE CREATION                                                │
    │   • Create SQLite database                                       │
    │   • Build universities table                                     │
    │   • Build university_details table                               │
    │   • Create v_full_rankings view (joined data)                   │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────┐
    │ SQLite Database │  data/processed/qs_rankings.db
    └─────────────────┘

═══════════════════════════════════════════════════════════════════════════
PHASE 3: EMBEDDING (embedder.py)
═══════════════════════════════════════════════════════════════════════════

    ┌─────────────────┐
    │ SQLite Database │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ DOCUMENT BUILDING                                                │
    │                                                                 │
    │   For each university row, create rich text:                    │
    │                                                                 │
    │   "MIT is ranked #1 globally in the QS World University         │
    │    Rankings. Located in United States (North America).          │
    │    Type: Private. Founded: 1861. Overall score: 96.1.           │
    │    Key metrics — Academic Reputation: 100.0, Employer           │
    │    Reputation: 100.0... Research & Discovery: 98.5..."         │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ EMBEDDING GENERATION                                             │
    │                                                                 │
    │   Text → SentenceTransformer → 384-dim vector                  │
    │                                                                 │
    │   "MIT is ranked..." → [0.234, -0.456, 0.123, ..., 0.789]      │
    │   "Stanford is..."    → [0.221, -0.434, 0.145, ..., 0.765]     │
    │   ...                                                            │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ VECTOR STORE (ChromaDB)                                          │
    │                                                                 │
    │   • Store vectors with HNSW index                               │
    │   • Attach metadata (name, rank, country)                       │
    │   • Enable fast similarity search                               │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────┐
    │ Vector Store    │  data/chroma_db/
    └─────────────────┘

═══════════════════════════════════════════════════════════════════════════
PHASE 4: QUERY PROCESSING (chatbot.py)
═══════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────┐
    │ USER QUERY: "Which UK university has the highest sustainability?│
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ ROUTER ANALYSIS                                                  │
    │                                                                 │
    │   Keywords detected: "highest" → MATH_KEYWORDS match            │
    │   Decision: SQL PATH                                            │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ SQL GENERATION (LLM)                                             │
    │                                                                 │
    │   "Write a query to find UK university with highest             │
    │    sustainability score"                                        │
    │                                                                 │
    │   → SELECT university_name, sustainability                      │
    │      FROM v_full_rankings                                       │
    │      WHERE country = 'United Kingdom'                           │
    │      ORDER BY sustainability DESC LIMIT 1                       │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ QUERY EXECUTION                                                  │
    │                                                                 │
    │   SQLite returns:                                               │
    │   [("University of Oxford", 98.5)]                              │
    └─────────────────────────────────────────────────────────────────┘
             │
             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ RESPONSE GENERATION (LLM)                                        │
    │                                                                 │
    │   "The UK university with the highest sustainability score is   │
    │    the University of Oxford with a score of 98.5 out of 100."   │
    └─────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════
PHASE 5: USER INTERFACE (app.py)
═══════════════════════════════════════════════════════════════════════════

    ┌─────────────────────────────────────────────────────────────────┐
    │ STREAMLIT APP                                                    │
    │                                                                 │
    │   ┌─────────────────────────────────────────────────────────┐   │
    │   │ Sidebar                          │ Main Area            │   │
    │   │                                  │                      │   │
    │   │ • Stats (universities,           │ • Quick stats bar    │   │
    │   │   countries count)               │ • Data table         │   │
    │   │ • LLM Provider selector          │                      │   │
    │   │ • Admin Tools (scraper,          │ ┌─────────────────┐  │   │
    │   │   embeddings)                    │ │ Chat Interface  │  │   │
    │   │                                  │ │                 │  │   │
    │   │                                  │ │ User: Which UK  │  │   │
    │   │                                  │ │ uni has highest │  │   │
    │   │                                  │ │ sustainability? │  │   │
    │   │                                  │ │                 │  │   │
    │   │                                  │ │ Bot: [SQL] The  │  │   │
    │   │                                  │ │ UK university...│  │   │
    │   │                                  │ └─────────────────┘  │   │
    │   └─────────────────────────────────────────────────────────┘   │
    └─────────────────────────────────────────────────────────────────┘
```

### Data Transformation Summary

```
Raw HTML/JSON
     │ scraper.py
     ▼
Messy CSV (rank: "=42", scores: "95.4 (est)")
     │ pipeline.py
     ▼
Clean SQLite (rank: 42 INTEGER, scores: 95.4 REAL)
     │ embedder.py
     ▼
Vector Store (["MIT is ranked...", ...] → [[0.23, ...], ...])
     │ chatbot.py
     ▼
Natural Language Answer ("MIT is the top university...")
```

---

## Tools & Technologies Summary

| Tool | Purpose | Why This Tool? |
|------|---------|----------------|
| **Playwright** | Web scraping | Handles JavaScript, supports stealth |
| **SQLite** | Data storage | Embedded, fast, portable, SQL support |
| **Pandas** | Data processing | Powerful DataFrame operations |
| **Sentence Transformers** | Embeddings | State-of-art text embeddings, easy to use |
| **ChromaDB** | Vector database | Persistent, HNSW index, simple API |
| **Groq/OpenAI** | LLM APIs | Natural language understanding & generation |
| **Streamlit** | Web UI | Quick Python web apps, chat components |
| **Python asyncio** | Concurrency | Efficient async scraping |

---

## Key Takeaways

1. **Embeddings transform text into numbers** that capture semantic meaning
2. **Vector databases enable fast similarity search** using algorithms like HNSW
3. **RAG combines retrieval with generation** to ground LLM answers in real data
4. **Text-to-SQL bridges natural language and databases** for precise queries
5. **Intelligent routing optimizes** for query type and response quality
6. **Each component is modular** - you can swap embeddings, LLMs, or databases

This architecture is applicable to many domains: document Q&A, customer support, knowledge bases, and more!

---

## 11. Python Libraries Deep Dive

This section covers every Python library used in this project with real-world analogies and code examples.

---

### 11.1 Web Scraping Libraries

#### Playwright (`playwright`)

**What it is:** A browser automation library that controls real browsers (Chromium, Firefox, WebKit) programmatically.

**Analogy:** Think of Playwright as a robotic arm that can use a web browser just like a human. It can click buttons, scroll pages, fill forms, and read what's on the screen - but at superhuman speed.

**Why we need it:** The QS website loads data dynamically using JavaScript. Traditional scraping tools like `requests` only see the initial HTML, but Playwright sees the fully rendered page after JavaScript executes.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PLAYWRIGHT vs TRADITIONAL SCRAPING                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Traditional (requests + BeautifulSoup):                                    │
│  ───────────────────────────────────────────────────────────────────────    │
│  1. Request page HTML                                                       │
│  2. Get initial HTML (before JavaScript runs)                              │
│  3. Data is missing! (loaded later by JavaScript)                          │
│                                                                             │
│  Playwright:                                                                │
│  ───────────────────────────────────────────────────────────────────────    │
│  1. Launch real browser                                                    │
│  2. Navigate to page                                                       │
│  3. Wait for JavaScript to execute                                         │
│  4. Extract fully rendered content                                         │
│  5. Success! All data is visible                                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Features Used in This Project:**

| Feature | Purpose | Code Example |
|---------|---------|--------------|
| `async_playwright` | Async browser control | `async with async_playwright() as pw:` |
| `browser.launch()` | Start browser | `browser = await pw.chromium.launch(headless=True)` |
| `page.goto()` | Navigate to URL | `await page.goto("https://example.com")` |
| `page.query_selector_all()` | Find elements | `links = await page.query_selector_all("a")` |
| `page.evaluate()` | Run JavaScript | `await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")` |

**Code Example from scraper.py:**

```python
from playwright.async_api import async_playwright

async def scrape_example():
    async with async_playwright() as pw:
        # Launch browser (headless = no visible window)
        browser = await pw.chromium.launch(headless=True)

        # Create a browser context (like opening a new window)
        context = await browser.new_context(
            user_agent="Mozilla/5.0...",  # Look like a real browser
            viewport={"width": 1440, "height": 900}
        )

        # Create a new page (like opening a tab)
        page = await context.new_page()

        # Navigate and wait for page to load
        await page.goto("https://www.topuniversities.com/world-university-rankings")

        # Extract content
        content = await page.content()

        # Clean up
        await browser.close()
        return content
```

---

#### Playwright-Stealth (`playwright-stealth`)

**What it is:** A plugin that makes Playwright-controlled browsers harder to detect as bots.

**Analogy:** Imagine a spy trying to blend in at a party. Without stealth, they'd wear a "I'M A SPY" t-shirt. With stealth, they dress and act like everyone else. Websites have "bouncer" systems that check if visitors are real humans or bots - stealth helps our scraper look human.

**How websites detect bots:**
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BOT DETECTION TECHNIQUES                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. USER AGENT CHECKING                                                     │
│     Website: "Are you a real browser?"                                      │
│     Bot: "I'm Python-requests/2.28.0"  ← BLOCKED!                          │
│     Real browser: "I'm Mozilla/5.0 (Windows NT 10.0...)"  ← OK!            │
│                                                                             │
│  2. JAVASCRIPT CHALLENGES                                                   │
│     Website runs hidden JavaScript that bots can't execute                 │
│     Real browsers execute it automatically                                  │
│                                                                             │
│  3. BEHAVIOR ANALYSIS                                                       │
│     Humans: Random mouse movements, varying click timing                   │
│     Bots: Instant clicks, no mouse movement                                │
│                                                                             │
│  4. BROWSER FINGERPRINTING                                                  │
│     Checks for: WebGL rendering, font availability, canvas drawing         │
│     Headless browsers often fail these checks                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code Example:**

```python
from playwright_stealth import stealth_async

# Without stealth - easily detected
page = await context.new_page()
# Website might block us!

# With stealth - looks like a real user
page = await context.new_page()
await stealth_async(page)  # Apply stealth techniques
# Now we look like a real browser!
```

---

### 11.2 Data Processing Libraries

#### Pandas (`pandas`)

**What it is:** The Swiss Army knife of data manipulation in Python. It provides DataFrames - tables you can manipulate programmatically.

**Analogy:** Think of Pandas as "Excel on steroids." It's like having a spreadsheet that you can control with code, but infinitely more powerful. You can filter millions of rows in milliseconds, join tables, calculate statistics, and transform data in ways that would take hours in Excel.

**Core Concepts:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PANDAS DATAFRAME STRUCTURE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   DataFrame = Table with rows and columns                                   │
│                                                                             │
│        Column 0      Column 1      Column 2      Column 3                  │
│           │            │             │             │                       │
│           ▼            ▼             ▼             ▼                       │
│        ┌──────┬─────────────┬─────────────┬─────────────┐                 │
│ Row 0  │  1   │    MIT      │ United States│    96.1     │                 │
│ Row 1  │  2   │  Stanford   │ United States│    94.3     │                 │
│ Row 2  │  3   │  Oxford     │     UK       │    93.8     │                 │
│ Row 3  │  4   │  Harvard    │ United States│    93.5     │                 │
│        └──────┴─────────────┴─────────────┴─────────────┘                 │
│           │                                                           │
│           ▼                                                           │
│        Index (row labels)                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Operations Used in This Project:**

| Operation | Description | Example |
|-----------|-------------|---------|
| `read_csv()` | Load CSV into DataFrame | `df = pd.read_csv("data.csv")` |
| `to_csv()` | Save DataFrame to CSV | `df.to_csv("output.csv")` |
| `fillna()` | Replace missing values | `df["score"].fillna(0)` |
| `apply()` | Apply function to column | `df["rank"].apply(clean_rank)` |
| `dropna()` | Remove rows with missing values | `df.dropna(subset=["name"])` |
| `merge()` | Join two DataFrames | `pd.merge(df1, df2, on="id")` |
| `sort_values()` | Sort rows | `df.sort_values("rank")` |

**Code Examples from pipeline.py:**

```python
import pandas as pd

# 1. Loading data
df = pd.read_csv("data/raw/rankings_main.csv")

# 2. Viewing data
print(df.head())  # First 5 rows
print(df.info())  # Column types and non-null counts
print(df.describe())  # Statistical summary

# 3. Filtering data
top_100 = df[df["rank"] <= 100]  # Only top 100
us_universities = df[df["country"] == "United States"]

# 4. Applying functions to columns
def clean_score(val):
    if pd.isna(val):
        return None
    return float(str(val).replace(",", ""))

df["overall_score"] = df["overall_score"].apply(clean_score)

# 5. Creating new columns
df["continent"] = df["country"].apply(country_to_continent)

# 6. Grouping and aggregation
avg_by_country = df.groupby("country")["overall_score"].mean()

# 7. Merging DataFrames (like SQL JOIN)
df_merged = pd.merge(
    df_main,        # Left table
    df_detail,      # Right table
    on="detail_url", # Join key
    how="left"      # Left join
)
```

---

#### Python-Dotenv (`python-dotenv`)

**What it is:** Loads environment variables from a `.env` file into your Python program.

**Analogy:** Imagine you have a safe (`.env` file) where you keep your passwords and API keys. `python-dotenv` is like a secure courier that takes secrets from the safe and hands them to your program - but never lets them be visible in your code or committed to GitHub.

**Why it matters:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    WHY USE .ENV FILES?                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ❌ BAD - Hardcoding secrets in code:                                       │
│     api_key = "sk-abc123xyz..."  # Visible in GitHub!                      │
│                                                                             │
│  ✅ GOOD - Using environment variables:                                     │
│     from dotenv import load_dotenv                                          │
│     import os                                                               │
│     load_dotenv()                                                           │
│     api_key = os.getenv("GROQ_API_KEY")  # Loaded from .env                │
│                                                                             │
│  The .env file is in .gitignore, so secrets never reach GitHub!            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code Example:**

```python
# .env file (never commit this!)
GROQ_API_KEY=gsk_xxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxx
LLM_PROVIDER=groq

# Python code
from dotenv import load_dotenv
import os

load_dotenv()  # Load .env file

api_key = os.getenv("GROQ_API_KEY")  # Get the value
provider = os.getenv("LLM_PROVIDER", "groq")  # With default
```

---

### 11.3 Embedding & Vector Store Libraries

#### Sentence-Transformers (`sentence-transformers`)

**What it is:** A library for generating dense vector representations (embeddings) of text using pre-trained neural networks.

**Analogy:** Imagine a magical translator that converts any sentence into a "meaning fingerprint" - a list of numbers that captures the essence of what the sentence means. Similar sentences get similar fingerprints, even if they use completely different words.

**How Embeddings Work:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SENTENCE TO EMBEDDING TRANSFORMATION                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Input Sentence:                                                            │
│  "MIT is the best engineering university in the USA"                        │
│                                                                             │
│  Step 1: Tokenization (split into words/pieces)                            │
│  ["MIT", "is", "the", "best", "engineering", "university", "in", "USA"]   │
│                                                                             │
│  Step 2: Neural Network Processing                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    all-MiniLM-L6-v2 Model                            │   │
│  │                                                                      │   │
│  │   [Embedding Layer] → [6 Transformer Layers] → [Pooling]           │   │
│  │                                                                      │   │
│  │   Each layer captures different aspects of meaning:                 │   │
│  │   - Layer 1: Basic word meanings                                    │   │
│  │   - Layer 2: Word relationships                                     │   │
│  │   - Layer 3: Phrase understanding                                   │   │
│  │   - ...                                                              │   │
│  │   - Layer 6: Full semantic representation                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  Output: 384-dimensional vector                                             │
│  [0.234, -0.456, 0.123, 0.789, -0.234, 0.567, ..., 0.345]                 │
│                                                                             │
│  Each number represents a semantic feature:                                │
│  - Dimension 0: might represent "academic quality"                         │
│  - Dimension 1: might represent "location in Americas"                     │
│  - Dimension 2: might represent "research focus"                           │
│  - ... (these are learned, not human-defined)                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Why Similar Sentences Have Similar Vectors:**

```python
from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer('all-MiniLM-L6-v2')

sentences = [
    "MIT is excellent for engineering",
    "Stanford has great engineering programs",
    "I love pizza"
]

embeddings = model.encode(sentences)

# Calculate similarity between first two sentences
similarity_1_2 = np.dot(embeddings[0], embeddings[1]) / (
    np.linalg.norm(embeddings[0]) * np.linalg.norm(embeddings[1])
)
# Result: ~0.85 (high similarity - both about engineering universities)

# Calculate similarity between first and third
similarity_1_3 = np.dot(embeddings[0], embeddings[2]) / (
    np.linalg.norm(embeddings[0]) * np.linalg.norm(embeddings[2])
)
# Result: ~0.12 (low similarity - different topics)
```

**Code Example from embedder.py:**

```python
from sentence_transformers import SentenceTransformer

# Load the model (downloads ~80MB on first use)
model = SentenceTransformer('all-MiniLM-L6-v2')

# Single sentence
text = "MIT is ranked #1 globally for engineering"
embedding = model.encode(text)
# embedding.shape = (384,) - 384 numbers

# Multiple sentences (batch processing)
texts = [
    "MIT is ranked #1 globally",
    "Stanford excels in computer science",
    "Oxford is one of the oldest universities"
]
embeddings = model.encode(texts)
# embeddings.shape = (3, 384) - 3 sentences, each 384 numbers
```

---

#### ChromaDB (`chromadb`)

**What it is:** An open-source vector database designed for AI applications. It stores embeddings and provides fast similarity search.

**Analogy:** Imagine a library where books aren't organized by title or author, but by their content similarity. When you ask "find books about sustainable engineering," the librarian instantly knows which books are semantically closest - even if they don't contain those exact words. ChromaDB is that magical librarian for your vector embeddings.

**Why We Can't Just Store Vectors in a List:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    VECTOR STORAGE COMPARISON                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  NAIVE APPROACH (List + Linear Search):                                     │
│  ───────────────────────────────────────────────────────────────────────    │
│  For 1500 universities:                                                     │
│  - Store vectors in a Python list                                          │
│  - To find similar: compare query to ALL 1500 vectors                      │
│  - Time complexity: O(n) - linear time                                     │
│  - 1500 vectors × 384 dimensions = 576,000 comparisons per query           │
│  - Works fine for small datasets                                           │
│                                                                             │
│  CHROMADB (HNSW Index):                                                     │
│  ───────────────────────────────────────────────────────────────────────    │
│  For 1,000,000 documents:                                                   │
│  - Uses Hierarchical Navigable Small World (HNSW) graph                    │
│  - Organizes vectors in a multi-layer graph structure                      │
│  - Time complexity: O(log n) - logarithmic time                            │
│  - Finds nearest neighbors without checking all vectors                    │
│  - Same query takes milliseconds instead of minutes                        │
│                                                                             │
│  THE DIFFERENCE:                                                            │
│  Linear search on 1M vectors: ~1,000,000 comparisons                       │
│  HNSW search on 1M vectors: ~100-200 comparisons                           │
│  Speedup: ~5000x faster!                                                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**ChromaDB Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    CHROMADB STORAGE STRUCTURE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         COLLECTION                                   │   │
│  │                    ("qs_rankings")                                   │   │
│  │                                                                      │   │
│  │   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐            │   │
│  │   │   Document 1  │ │   Document 2  │ │   Document N  │            │   │
│  │   │               │ │               │ │               │            │   │
│  │   │ id: "uni_1"   │ │ id: "uni_2"   │ │ id: "uni_N"   │            │   │
│  │   │               │ │               │ │               │            │   │
│  │   │ document:     │ │ document:     │ │ document:     │            │   │
│  │   │ "MIT is..."   │ │ "Stanford..." │ │ "Oxford..."   │            │   │
│  │   │               │ │               │ │               │            │   │
│  │   │ embedding:    │ │ embedding:    │ │ embedding:    │            │   │
│  │   │ [0.23, ...]   │ │ [0.21, ...]   │ │ [0.19, ...]   │            │   │
│  │   │               │ │               │ │               │            │   │
│  │   │ metadata:     │ │ metadata:     │ │ metadata:     │            │   │
│  │   │ {name: "MIT", │ │ {name:        │ │ {name:        │            │   │
│  │   │  rank: 1}     │ │  "Stanford",  │ │  "Oxford",    │            │   │
│  │   │               │ │  rank: 5}     │ │  rank: 3}     │            │   │
│  │   └───────────────┘ └───────────────┘ └───────────────┘            │   │
│  │                                                                      │   │
│  │   ┌───────────────────────────────────────────────────────────────┐ │   │
│  │   │                    HNSW INDEX                                  │ │   │
│  │   │                                                                │ │   │
│  │   │   Layer 2 (top)    ●────────────●                             │ │   │
│  │   │                     \            /                              │ │   │
│  │   │   Layer 1          ●───────●──────●                            │ │   │
│  │   │                    /|\     /|\     |                            │ │   │
│  │   │   Layer 0 (base)  ●─●─●───●─●─●───●─●                          │ │   │
│  │   │                    (each ● is a document)                      │ │   │
│  │   │                                                                │ │   │
│  │   │   Search: Start at top, navigate down to nearest neighbors    │ │   │
│  │   └───────────────────────────────────────────────────────────────┘ │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code Example from embedder.py and chatbot.py:**

```python
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

# 1. Create a persistent client (saves to disk)
client = chromadb.PersistentClient(path="./data/chroma_db")

# 2. Define the embedding function
ef = SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")

# 3. Create a collection
collection = client.create_collection(
    name="qs_rankings",
    embedding_function=ef,
    metadata={"hnsw:space": "cosine"}  # Use cosine similarity
)

# 4. Add documents (ChromaDB handles embedding automatically!)
collection.upsert(
    ids=["uni_1", "uni_2"],
    documents=[
        "MIT is ranked #1 globally in QS Rankings...",
        "Stanford is ranked #5 globally..."
    ],
    metadatas=[
        {"university_name": "MIT", "rank": 1, "country": "United States"},
        {"university_name": "Stanford", "rank": 5, "country": "United States"}
    ]
)

# 5. Query for similar documents
results = collection.query(
    query_texts=["best engineering universities in USA"],
    n_results=5
)

# results contains:
# - 'ids': List of matching document IDs
# - 'documents': The actual text
# - 'metadatas': The metadata we stored
# - 'distances': Similarity scores (lower = more similar for cosine)
```

---

### 11.4 LLM Client Libraries

#### OpenAI (`openai`)

**What it is:** The official Python client for OpenAI's API. Also works with OpenAI-compatible APIs like Groq.

**Analogy:** Imagine having a brilliant consultant on speed dial. You send them a message with your question and some context, and they reply with a well-reasoned answer. The `openai` library is your phone - it handles the connection, formats your message, and delivers the response.

**Why We Use OpenAI Library with Groq:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    API COMPATIBILITY                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Groq designed their API to be "OpenAI-compatible":                        │
│  - Same endpoint structure                                                  │
│  - Same request format                                                      │
│  - Same response format                                                     │
│                                                                             │
│  This means:                                                                │
│  - Same code works for both                                                 │
│  - Only change: base_url and api_key                                        │
│  - No need to learn a new API                                               │
│                                                                             │
│  OPENAI:                                                                    │
│  ───────────────────────────────────────────────────────────────────────    │
│  client = OpenAI(api_key="sk-...")                                          │
│  # Uses default base_url: https://api.openai.com/v1                        │
│                                                                             │
│  GROQ:                                                                      │
│  ───────────────────────────────────────────────────────────────────────    │
│  client = OpenAI(                                                           │
│      api_key="gsk-...",                                                     │
│      base_url="https://api.groq.com/openai/v1"                             │
│  )                                                                          │
│  # Everything else is identical!                                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code Example from chatbot.py:**

```python
from openai import OpenAI
import os

# Initialize client (works for both Groq and OpenAI)
def get_client(provider="groq"):
    if provider == "openai":
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    else:  # groq
        return OpenAI(
            api_key=os.getenv("GROQ_API_KEY"),
            base_url="https://api.groq.com/openai/v1"
        )

client = get_client("groq")

# Send a completion request
response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "What is MIT known for?"}
    ],
    temperature=0.3,    # Lower = more deterministic
    max_tokens=500      # Maximum response length
)

# Extract the response text
answer = response.choices[0].message.content
print(answer)
```

**Understanding API Parameters:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    LLM API PARAMETERS EXPLAINED                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  TEMPERATURE (0.0 - 2.0)                                                    │
│  ───────────────────────────────────────────────────────────────────────    │
│  Controls randomness in output generation.                                  │
│                                                                             │
│  0.0 - Deterministic: Same input → Same output every time                  │
│        Use for: SQL generation, data extraction, factual answers           │
│                                                                             │
│  0.3 - Low randomness: Mostly consistent, slight variation                 │
│        Use for: RAG responses, summaries                                    │
│                                                                             │
│  0.7 - Medium: Balanced creativity and consistency                         │
│        Use for: General conversation                                        │
│                                                                             │
│  1.0+ - High randomness: Creative, unpredictable outputs                   │
│        Use for: Creative writing, brainstorming                             │
│                                                                             │
│  MAX_TOKENS                                                                 │
│  ───────────────────────────────────────────────────────────────────────    │
│  Maximum length of the response (1 token ≈ 4 characters).                  │
│                                                                             │
│  100 tokens  → ~75 words (short answer)                                     │
│  500 tokens  → ~375 words (paragraph)                                       │
│  1000 tokens → ~750 words (long response)                                   │
│                                                                             │
│  MESSAGES                                                                   │
│  ───────────────────────────────────────────────────────────────────────    │
│  Conversation context sent to the LLM.                                      │
│                                                                             │
│  [                                                                          │
│    {"role": "system", "content": "You are a helpful assistant."},          │
│    {"role": "user", "content": "Hello!"},                                  │
│    {"role": "assistant", "content": "Hi there!"},                          │
│    {"role": "user", "content": "How are you?"}                             │
│  ]                                                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 11.5 UI Library

#### Streamlit (`streamlit`)

**What it is:** A framework for building web applications entirely in Python, without HTML, CSS, or JavaScript.

**Analogy:** Streamlit is like a magical sketchbook. You write simple Python code like `st.write("Hello")` or `st.button("Click me")`, and Streamlit instantly transforms it into a professional-looking web application. No web development experience needed.

**How Streamlit Works:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STREAMLIT APP STRUCTURE                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  app.py                                                                     │
│  ───────────────────────────────────────────────────────────────────────    │
│  import streamlit as st                                                     │
│                                                                             │
│  # Page config (must be first)                                              │
│  st.set_page_config(page_title="My App")                                   │
│                                                                             │
│  # Sidebar                                                                  │
│  with st.sidebar:                                                           │
│      st.title("Menu")                                                       │
│      option = st.selectbox("Choose", ["A", "B"])                           │
│                                                                             │
│  # Main content                                                             │
│  st.title("Hello World")                                                    │
│  name = st.text_input("Your name")                                          │
│                                                                             │
│  if st.button("Submit"):                                                    │
│      st.write(f"Hello, {name}!")                                            │
│                                                                             │
│  ───────────────────────────────────────────────────────────────────────    │
│                                                                             │
│  Run: streamlit run app.py                                                  │
│  Result: Interactive web app at http://localhost:8501                      │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key Streamlit Components Used in app.py:**

| Component | Purpose | Example |
|-----------|---------|---------|
| `st.set_page_config()` | Page settings | `st.set_page_config(page_title="QS Rankings")` |
| `st.title()` | Main heading | `st.title("QS Rankings Chatbot")` |
| `st.sidebar` | Sidebar container | `with st.sidebar: st.title("Menu")` |
| `st.metric()` | Display metrics | `st.metric("Universities", "1,500")` |
| `st.dataframe()` | Show data table | `st.dataframe(df)` |
| `st.chat_message()` | Chat message | `with st.chat_message("user"): st.write("Hi")` |
| `st.chat_input()` | Chat input | `prompt = st.chat_input("Ask a question")` |
| `st.spinner()` | Loading indicator | `with st.spinner("Loading..."):` |
| `st.expander()` | Collapsible section | `with st.expander("Details"):` |
| `st.session_state` | State management | `st.session_state.messages = []` |

**Code Example from app.py:**

```python
import streamlit as st

# 1. Page configuration (must be first!)
st.set_page_config(
    page_title="QS Rankings Explorer",
    page_icon="🎓",
    layout="wide"
)

# 2. Sidebar
with st.sidebar:
    st.title("🎓 QS Rankings")

    # Radio buttons for selection
    llm_choice = st.radio(
        "LLM Provider",
        ["Groq (Free)", "OpenAI"],
        horizontal=True
    )

    # Expander for admin tools
    with st.expander("⚙️ Admin Tools"):
        if st.button("Run Scraper"):
            st.write("Running...")

# 3. Main content
st.title("QS World University Rankings Chatbot")

# 4. Metrics
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Universities", "1,500")
with col2:
    st.metric("Countries", "100")

# 5. Data table
with st.expander("View Data"):
    st.dataframe(df)

# 6. Chat interface
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if prompt := st.chat_input("Ask a question"):
    # Display user message
    with st.chat_message("user"):
        st.markdown(prompt)

    # Get bot response
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            response = "Here's my answer..."
            st.markdown(response)
```

**Understanding Streamlit's Execution Model:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    STREAMLIT RE-EXECUTION MODEL                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Unlike traditional web apps, Streamlit re-runs your entire script         │
│  every time a user interacts with a widget.                                │
│                                                                             │
│  User clicks button → Script runs from top to bottom → UI updates         │
│                                                                             │
│  PROBLEM: Variables get reset on every run!                                │
│                                                                             │
│  SOLUTION: st.session_state persists across re-runs                         │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ # This gets reset on every run:                                     │   │
│  │ counter = 0                                                         │   │
│  │ if st.button("Increment"):                                          │   │
│  │     counter += 1  # Lost on next run!                               │   │
│  │ st.write(counter)  # Always shows 0 or 1                           │   │
│  │                                                                      │   │
│  │ # This persists across runs:                                        │   │
│  │ if "counter" not in st.session_state:                               │   │
│  │     st.session_state.counter = 0                                    │   │
│  │ if st.button("Increment"):                                          │   │
│  │     st.session_state.counter += 1                                   │   │
│  │ st.write(st.session_state.counter)  # Correctly increments         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 11.6 Utility Libraries

#### NumPy (`numpy`)

**What it is:** The fundamental package for numerical computing in Python. Provides support for arrays, matrices, and mathematical functions.

**Analogy:** NumPy is like having a super calculator that can do millions of calculations simultaneously. Instead of adding numbers one by one, NumPy lets you add entire arrays of numbers in a single operation.

**Why it's used (indirectly):** Pandas, scikit-learn, and sentence-transformers all depend on NumPy for their numerical operations.

```python
import numpy as np

# Create arrays
arr = np.array([1, 2, 3, 4, 5])

# Vector operations (applies to all elements)
arr_squared = arr ** 2  # [1, 4, 9, 16, 25]

# Cosine similarity (used in vector search)
def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
```

---

#### Scikit-learn (`scikit-learn`)

**What it is:** A machine learning library providing tools for classification, regression, clustering, and more.

**Analogy:** Scikit-learn is like a toolbox with every common machine learning tool pre-built and ready to use. Need to cluster data? There's a tool for that. Need to reduce dimensions? There's a tool for that too.

**How it's used in this project (indirectly):** Sentence-transformers uses scikit-learn's utilities for normalization and distance calculations.

```python
from sklearn.metrics.pairwise import cosine_similarity

# Calculate similarity between vectors
vectors = [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
similarity_matrix = cosine_similarity(vectors)
```

---

#### TQDM (`tqdm`)

**What it is:** A progress bar library that shows progress for loops.

**Analogy:** TQDM is like a fuel gauge for your code. It shows you how much work is done and estimates how much time is left, so you're not left wondering if your code is still running or frozen.

```python
from tqdm import tqdm
import time

# Show progress bar for a loop
for i in tqdm(range(100), desc="Processing"):
    time.sleep(0.1)
# Output: Processing: 45%|████▌         | 45/100 [00:04<00:05, 10.00it/s]
```

---

### 11.7 Standard Library (Built-in)

#### SQLite3 (`sqlite3`)

**What it is:** Python's built-in SQLite database driver. No installation needed - it comes with Python.

**Analogy:** SQLite is like a filing cabinet that lives in a single file on your computer. Unlike big databases that need a server, SQLite is completely self-contained. You can copy the file to another computer and it just works.

**Database Structure in This Project:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DATABASE SCHEMA                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Table: universities                                                        │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ id | rank | university_name | country | overall_score | detail_url   │ │
│  ├───────────────────────────────────────────────────────────────────────┤ │
│  │  1 |   1  |       MIT       |   US    |     96.1      | /universi... │ │
│  │  2 |   2  |    Stanford     |   US    |     94.3      | /universi... │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                              │                                              │
│                              │ 1:N (one university can have one detail)    │
│                              ▼                                              │
│  Table: university_details                                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ id | university_id | sustainability | description | founded_year     │ │
│  ├───────────────────────────────────────────────────────────────────────┤ │
│  │  1 |       1       |      95.6       | MIT is...   |     1861        │ │
│  │  2 |       2       |      92.1       | Stanford... |     1885        │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
│  View: v_full_rankings (JOIN of both tables)                               │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │ SELECT * FROM universities                                            │ │
│  │ LEFT JOIN university_details ON universities.id = university_id       │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code Example:**

```python
import sqlite3

# Connect to database (creates if doesn't exist)
conn = sqlite3.connect("data/processed/qs_rankings.db")

# Create a cursor for executing SQL
cur = conn.cursor()

# Execute a query
cur.execute("SELECT * FROM universities WHERE country = 'United Kingdom'")

# Fetch results
results = cur.fetchall()

# Using Row factory for dictionary-like access
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT * FROM v_full_rankings WHERE rank <= 10")
for row in cur.fetchall():
    print(row["university_name"], row["overall_score"])

# Always close the connection
conn.close()
```

---

#### Asyncio (`asyncio`)

**What it is:** Python's built-in library for asynchronous programming.

**Analogy:** Imagine a chef who can put something in the oven and, instead of waiting for it to cook, starts chopping vegetables. When the timer goes off, the chef returns to check the oven. Asyncio lets your code do the same thing - start a task, do other work while waiting, and come back when the task is done.

**Why It's Used in Scraping:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    SYNC vs ASYNC SCRAPING                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  SYNCHRONOUS (one at a time):                                               │
│  ───────────────────────────────────────────────────────────────────────    │
│  Request page 1 → Wait 2 seconds → Get response → Process                  │
│  Request page 2 → Wait 2 seconds → Get response → Process                  │
│  Request page 3 → Wait 2 seconds → Get response → Process                  │
│  Total: 6 seconds                                                           │
│                                                                             │
│  ASYNCHRONOUS (parallel):                                                   │
│  ───────────────────────────────────────────────────────────────────────    │
│  Request page 1 ──┐                                                         │
│  Request page 2 ──┼──→ All wait together (2 seconds) ──→ All process       │
│  Request page 3 ──┘                                                         │
│  Total: 2 seconds (3x faster!)                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Code Example from scraper.py:**

```python
import asyncio

async def scrape_page(url):
    # Simulated async operation
    await asyncio.sleep(1)  # Non-blocking wait
    return f"Data from {url}"

async def main():
    # Run multiple operations concurrently
    results = await asyncio.gather(
        scrape_page("url1"),
        scrape_page("url2"),
        scrape_page("url3"),
    )
    print(results)

# Run the async function
asyncio.run(main())
```

---

#### Regular Expressions (`re`)

**What it is:** Pattern matching library for finding and extracting text patterns.

**Analogy:** Regular expressions are like super-powered "Find" commands. Instead of finding just a specific word, you can find patterns like "any 4-digit number" or "text that looks like an email address."

**Patterns Used in This Project:**

```python
import re

# Clean rank formats: "=42" → 42, "501-510" → 501
def clean_rank(val):
    match = re.search(r"(\d+)", str(val))
    return int(match.group(1)) if match else None

# Match rank patterns: "8th ranked", "rank #5"
RANK_NUMBER_RE = re.compile(
    r"\b(?:rank(?:ed)?\s*#?\s*\d+|\d+\s*(?:st|nd|rd|th)\s*rank(?:ed)?)\b",
    re.IGNORECASE
)

# Extract university name from profile query
PROFILE_PATTERN = r"tell me about (.+)"
match = re.match(PROFILE_PATTERN, "tell me about MIT", re.IGNORECASE)
if match:
    university = match.group(1)  # "MIT"
```

---

## 12. Project File Breakdown

### 12.1 scraper.py - Web Scraping

**Purpose:** Downloads university data from the QS website.

**What it does:**
1. **Layer A:** Collects basic info for all 1500+ universities
2. **Layer B:** Scrapes detailed profiles for top 200

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `scrape_layer_a()` | Collect university list using API/interception/DOM |
| `scrape_detail_page()` | Extract detailed data from one university page |
| `_parse_api_item()` | Parse JSON from QS API into standard format |
| `_scroll_to_bottom()` | Handle infinite scroll pages |

---

### 12.2 pipeline.py - ETL (Extract, Transform, Load)

**Purpose:** Cleans raw scraped data and loads it into SQLite.

**What it does:**
1. **Clean:** Fix messy rank formats, scores, add continents
2. **Save:** Store cleaned data as CSV
3. **Load:** Insert into SQLite database

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `clean_rank()` | Parse rank formats like "=42" or "501-510" |
| `clean_score()` | Extract numeric score from text |
| `country_to_continent()` | Map country name to continent |
| `_clean_main()` | Clean main rankings DataFrame |
| `_clean_detail()` | Clean detail DataFrame |
| `run_pipeline()` | Execute full ETL pipeline |

---

### 12.3 embedder.py - Vector Store Creation

**Purpose:** Converts university data into vector embeddings stored in ChromaDB.

**What it does:**
1. Read clean data from SQLite
2. Build rich text descriptions for each university
3. Generate 384-dimensional embeddings
4. Store in ChromaDB with metadata

**Key Function:**

| Function | Purpose |
|----------|---------|
| `build_document_text()` | Convert database row to rich text paragraph |
| `build_vector_store()` | Create and populate ChromaDB collection |

---

### 12.4 chatbot.py - Query Processing

**Purpose:** Routes user queries and generates responses.

**What it does:**
1. Classify query type (greeting, SQL, profile, RAG)
2. Route to appropriate handler
3. Generate response using LLM

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `chat()` | Main entry point - routes queries |
| `_is_greeting()` | Detect greetings/help requests |
| `_is_math_query()` | Detect aggregation/math queries |
| `_run_text_to_sql()` | Execute Text-to-SQL pipeline |
| `_run_rag_query()` | Execute RAG pipeline |
| `_format_profile()` | Generate university profile |

---

### 12.5 app.py - Streamlit UI

**Purpose:** Web interface for the chatbot.

**What it does:**
1. Display statistics and data table
2. Provide chat interface
3. Handle admin actions (scraper, embeddings)
4. Show reference cards for RAG results

---

## 13. Requirements.txt Summary

| Package | Version | Purpose |
|---------|---------|---------|
| `playwright` | 1.49.0 | Browser automation for scraping |
| `playwright-stealth` | 2.0.2 | Bot detection evasion |
| `pandas` | 2.2.3 | Data manipulation and analysis |
| `pycountry-convert` | 0.7.2 | Country-to-continent conversion |
| `python-dotenv` | 1.0.1 | Environment variable management |
| `sentence-transformers` | 3.3.1 | Text embedding generation |
| `chromadb` | 0.6.3 | Vector database storage |
| `openai` | 1.61.0 | LLM API client (Groq/OpenAI) |
| `plotly` | 5.24.1 | Interactive visualizations |
| `seaborn` | 0.13.2 | Statistical visualizations |
| `matplotlib` | 3.9.4 | Static visualizations |
| `jupyter` | 1.1.1 | Notebook environment |
| `streamlit` | 1.42.0 | Web application framework |
| `numpy` | >=1.26.4 | Numerical computing |
| `scikit-learn` | >=1.5.2 | Machine learning utilities |
| `tqdm` | 4.67.1 | Progress bars |

---

## Key Learning Points

1. **Playwright** runs a real browser to scrape JavaScript-heavy sites
2. **Pandas** is Excel on steroids - handles millions of rows effortlessly
3. **Sentence-Transformers** converts text to "meaning fingerprints" (vectors)
4. **ChromaDB** stores vectors and finds similar ones instantly using HNSW
5. **Streamlit** turns Python scripts into web apps with zero HTML/CSS
6. **The OpenAI library** works with Groq too - just change the base_url
7. **Asyncio** makes scraping 3-5x faster by doing multiple things at once
8. **SQLite** is a complete database in a single file - no server needed
9. **The pipeline pattern** (scrape → clean → embed → query) is reusable for any RAG project