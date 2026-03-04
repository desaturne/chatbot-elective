"""
scraper.py
Two-layer Playwright scraper for QS World University Rankings.

Layer A  — Collects university names + detail URLs from the main rankings page.
           Strategy 1: network interception (catches the internal JSON API call)
           Strategy 2: DOM link scan  (fallback — finds a[href*='/universities/'])

Layer B  — Visits each university detail page and scrapes:
           • ALL main ranking scores (overall, academic rep, employer rep, …)
           • All 5 QS lens tab scores (Research, Learning, Employability, …)
           • Description, key facts, and student review snippets

Usage:
    python scraper.py
"""

import asyncio
import csv
import json
import logging
import random
import re
import urllib.request as _urllib_req
from pathlib import Path

from playwright.async_api import async_playwright

try:
    from playwright_stealth import stealth_async
except ImportError:
    async def stealth_async(page): pass

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL   = "https://www.topuniversities.com/world-university-rankings"
MAIN_CSV   = Path("data/raw/rankings_main.csv")
DETAIL_CSV = Path("data/raw/rankings_detail.csv")
LOG_FILE   = Path("logs/scraper.log")

MAX_LAYER_A   = 1504  # full QS 2026 dataset (1504 universities)
MAX_LAYER_B   = 200   # max detail pages (each takes ~5–10 s)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Known column-header strings that should never appear as university names
_HEADER_LABELS = {
    "rank", "university", "institution", "overall score", "score",
    "country", "location", "city", "academic reputation",
    "employer reputation", "citations", "international",
    "h-index citations", "faculty", "students",
}

DETAIL_TABS = {
    "Research & Discovery": "research_discovery",
    "Learning Experience":  "learning_experience",
    "Employability":        "employability",
    "Global Engagement":    "global_engagement",
    "Sustainability":       "sustainability",
}

# Selectors for description, key facts, reviews on detail pages
_DESC_SELECTORS = [
    "[class*='about'] p",
    "[class*='overview'] p",
    "[class*='intro'] p",
    "[class*='description'] p",
    "article p",
    "main p",
]
_FACTS_SELECTORS = [
    "[class*='key-facts'] li",
    "[class*='key-stat']",
    "[class*='facts-item']",
    "[class*='stats'] li",
    "dl dt, dl dd",
    "[class*='detail'] li",
]
_REVIEW_SELECTORS = [
    "[class*='review'] p",
    "[class*='testimonial'] p",
    "[class*='student-review'] p",
    "[class*='quote'] p",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_context_kwargs() -> dict:
    return {
        "user_agent": random.choice(USER_AGENTS),
        "viewport": {"width": 1440, "height": 900},
        "locale": "en-US",
    }


def _is_header_label(text: str) -> bool:
    return text.strip().lower() in _HEADER_LABELS


def _normalise_url(path: str) -> str:
    if not path:
        return ""
    return path if path.startswith("http") else "https://www.topuniversities.com" + path


def _parse_api_item(item: dict) -> dict | None:
    """Map one JSON object from the QS API to our standard field names."""
    def _get(*keys):
        for k in keys:
            v = item.get(k)
            if v not in (None, "", "-", "N/A"):
                return v
        return None

    name = _get("title", "name", "institution", "university_name")
    if not name or _is_header_label(str(name)):
        return None

    def _parse_score(val):
        """Safely coerce a score value to float in range [0, 100]."""
        if val in (None, "", "-", "N/A"):
            return None
        try:
            f = float(str(val).replace(",", ""))
            return f if 0 <= f <= 100 else None
        except (ValueError, TypeError):
            return None

    # The QS 2026 API returns per-indicator scores inside a nested dict:
    # {"Research & Discovery": [{"indicator_name": "...", "score": "..."}], ...}
    nested = item.get("scores", {})

    def _indicator_score(category: str, indicator: str):
        for entry in nested.get(category, []):
            if entry.get("indicator_name") == indicator:
                return _parse_score(entry.get("score"))
        return None

    return {
        "rank":            _get("rank_display", "rank", "overall_rank", "overallRank"),
        "university_name": name,
        "country":         _get("country", "country_name", "countryName", "location"),
        "overall_score":   _parse_score(_get("overall_score", "score", "overallScore", "scores_overall")),
        "citations_per_faculty": (
            _indicator_score("Research & Discovery", "Citations per Faculty")
            or _parse_score(_get("citations", "cf_score", "scores_citations", "citationsPerFaculty"))
        ),
        "academic_reputation": (
            _indicator_score("Research & Discovery", "Academic Reputation")
            or _parse_score(_get("ar_score", "scores_ar", "academicReputation"))
        ),
        "employer_reputation": (
            _indicator_score("Employability", "Employer Reputation")
            or _parse_score(_get("er_score", "scores_er", "employerReputation"))
        ),
        "intl_faculty_ratio": (
            _indicator_score("Global Engagement", "International Faculty Ratio")
            or _parse_score(_get("ifr_score", "scores_ifr", "internationalFaculty"))
        ),
        "intl_student_ratio": (
            _indicator_score("Global Engagement", "International Student Ratio")
            or _parse_score(_get("isr_score", "scores_isr", "internationalStudents"))
        ),
        # "path" is the new primary URL field in the QS 2026 API
        "detail_url": _normalise_url(
            _get("path", "url", "guid", "link", "profile_url") or ""
        ),
    }


async def _scroll_to_bottom(page, rounds: int = 20, pause: float = 1.2):
    """Scroll the page to the bottom to trigger infinite-scroll / lazy loading."""
    for _ in range(rounds):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(pause)


# ---------------------------------------------------------------------------
# Layer A — Strategy 1: network response interception
# ---------------------------------------------------------------------------

async def _try_intercept(browser) -> list[dict]:
    """
    Broad JSON interception: try to parse ANY non-trivial response from
    topuniversities.com as JSON. Does NOT filter by content-type because
    QS sometimes serves data as text/plain or text/html.
    """
    captured: list = []

    async def on_response(response):
        if "topuniversities" not in response.url:
            return
        if response.status != 200:
            return
        try:
            body = await response.body()
            if len(body) < 500:           # too small to be a rankings payload
                return
            text = body.decode("utf-8", errors="ignore").strip()
            if text[0] not in ("[", "{"):  # must look like JSON
                return
            data = json.loads(text)

            rows = []
            if isinstance(data, list) and len(data) > 5:
                rows = data
            elif isinstance(data, dict):
                for key in ("data", "results", "items", "universities",
                            "rankings", "rows", "hits", "score_nodes"):
                    val = data.get(key)
                    if isinstance(val, list) and len(val) > 5:
                        rows = val
                        break

            if rows:
                # Quick sanity check: does at least one item look like a university?
                sample = rows[0] if rows else {}
                university_keys = {"title", "name", "institution", "university_name",
                                   "rank", "rank_display", "score", "overallScore",
                                   "score_nid", "core_id"}  # QS 2026 API fields
                if any(k in sample for k in university_keys):
                    captured.extend(rows)
                    print(f"  [✓] Intercepted {len(rows)} rows from {response.url[:80]}")
        except Exception:
            pass

    context = await browser.new_context(**_new_context_kwargs())
    page    = await context.new_page()
    await stealth_async(page)
    page.on("response", on_response)

    print(f"  [>] Navigating to {BASE_URL} (interception mode)...")
    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
    except Exception as e:
        logging.warning("Layer A navigation (interception): %s", e)

    await _scroll_to_bottom(page, rounds=20, pause=1.0)
    await asyncio.sleep(5)   # give in-flight XHR calls time to land
    await context.close()

    return captured


# ---------------------------------------------------------------------------
# Layer A — Strategy 0: direct paginated REST API  (most reliable)
# ---------------------------------------------------------------------------

async def _get_json_async(url: str) -> dict:
    """Fetch a JSON URL in a thread so we don't block the asyncio loop."""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Referer": BASE_URL,
    }
    loop = asyncio.get_event_loop()
    def _sync_fetch():
        req = _urllib_req.Request(url, headers=headers)
        with _urllib_req.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    return await loop.run_in_executor(None, _sync_fetch)


async def _fetch_all_api_pages(browser, max_items: int = MAX_LAYER_A) -> list[dict]:
    """
    Directly query QS's internal paginated rankings REST API.
    The NID (Drupal node ID) is extracted from drupalSettings on the page,
    so this stays correct even when QS publishes new yearly rankings.
    """
    # Step 1: visit rankings page to discover the current NID
    nid = ""
    context = await browser.new_context(**_new_context_kwargs())
    page    = await context.new_page()
    await stealth_async(page)
    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60_000)
        nid = await page.evaluate(
            "() => (window.drupalSettings && drupalSettings.qs_rankings_rest_api"
            "       ? String(drupalSettings.qs_rankings_rest_api.nid) : '')"
        )
    except Exception as e:
        logging.warning("NID extraction failed: %s", e)
    finally:
        await context.close()

    if not nid:
        print("  [!] Could not determine rankings nid from drupalSettings")
        return []

    print(f"  [>] Direct API fetch  nid={nid}  max={max_items}")

    items_per_page = 100
    all_nodes: list[dict] = []
    page_num  = 0

    while len(all_nodes) < max_items:
        api_url = (
            f"https://www.topuniversities.com/rankings/endpoint"
            f"?nid={nid}&page={page_num}&items_per_page={items_per_page}"
            f"&tab=indicators&region=&countries=&cities=&search="
            f"&star=&sort_by=&order_by=&program_type="
        )
        try:
            data = await _get_json_async(api_url)
        except Exception as e:
            logging.warning("API page %d error: %s", page_num, e)
            break

        nodes = data.get("score_nodes", [])
        if not nodes:
            break

        all_nodes.extend(nodes)
        total_pages  = int(data.get("total_pages",  1))
        total_record = int(data.get("total_record", 0))
        effective_max = min(total_record, max_items)
        print(
            f"  [+] Page {page_num+1}/{total_pages}  "
            f"collected {len(all_nodes)}/{effective_max}"
        )

        if page_num + 1 >= total_pages or len(all_nodes) >= max_items:
            break
        page_num += 1
        await asyncio.sleep(random.uniform(0.3, 0.8))   # be polite

    print(f"  [+] Direct API fetch complete — {len(all_nodes)} raw items")
    return all_nodes[:max_items]


# ---------------------------------------------------------------------------
# Layer A — Strategy 2: DOM link scan
# ---------------------------------------------------------------------------

async def _try_dom_links(browser) -> list[dict]:
    """
    Fallback when interception yields nothing.
    Finds every  <a href="/universities/...">  link on the loaded page.
    Deduplicates by URL and assigns sequential rank based on DOM order.
    """
    context = await browser.new_context(**_new_context_kwargs())
    page    = await context.new_page()
    await stealth_async(page)

    print(f"  [>] Navigating to {BASE_URL} (DOM link fallback)...")
    try:
        await page.goto(BASE_URL, wait_until="networkidle", timeout=90_000)
    except Exception as e:
        logging.warning("Layer A navigation (DOM): %s", e)

    await _scroll_to_bottom(page, rounds=25, pause=1.5)
    await asyncio.sleep(4)

    # Collect all university-page links
    links = await page.query_selector_all("a[href*='/universities/']")
    print(f"  [i] Found {len(links)} a[href*='/universities/'] links on page")

    seen_urls: set = set()
    rows: list[dict] = []

    for link_el in links:
        try:
            name = (await link_el.inner_text()).strip()
            href = (await link_el.get_attribute("href") or "").strip()

            # Skip blank, too-short, or header-label entries
            if not name or len(name) < 3 or _is_header_label(name):
                continue
            # Skip navigation links (e.g. site nav "Universities" menu items)
            if not re.search(r"/universities/[a-z]", href):
                continue

            full_url = _normalise_url(href)
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            # Try to grab rank from the closest ancestor that contains a digit
            rank_val = None
            el = link_el
            for _ in range(6):
                parent = await el.evaluate_handle("el => el.parentElement")
                if not parent:
                    break
                parent_text = (await parent.evaluate("el => el.innerText")).strip()
                rank_match  = re.match(r"^[=\s]*(\d{1,4})\b", parent_text)
                if rank_match:
                    rank_val = int(rank_match.group(1))
                    break
                el = parent

            rows.append({
                "rank":            rank_val if rank_val else len(rows) + 1,
                "university_name": name,
                "country":         None,
                "overall_score":   None,
                "citations_per_faculty": None,
                "academic_reputation":   None,
                "employer_reputation":   None,
                "intl_faculty_ratio":    None,
                "intl_student_ratio":    None,
                "detail_url": full_url,
            })
        except Exception:
            continue

    await context.close()
    print(f"  [+] DOM fallback found {len(rows)} unique universities")
    return rows


# ---------------------------------------------------------------------------
# Layer A — public entry point
# ---------------------------------------------------------------------------

def _dedup_rows(rows: list[dict]) -> list[dict]:
    """De-duplicate parsed rows by detail_url then by university_name."""
    seen: set = set()
    unique: list[dict] = []
    for r in rows:
        key = r["detail_url"] or r["university_name"]
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


async def scrape_layer_a(browser) -> list[dict]:
    # ── Strategy 0: direct paginated REST API (preferred) ────────────────────
    print("  [>] Strategy 0: direct REST API...")
    raw = await _fetch_all_api_pages(browser, max_items=MAX_LAYER_A)
    if raw:
        rows = [r for r in (_parse_api_item(i) for i in raw) if r]
        unique = _dedup_rows(rows)
        print(f"  [+] Strategy 0 yielded {len(unique)} universities")
        return unique[:MAX_LAYER_A]

    # ── Strategy 1: network interception ─────────────────────────────────────
    print("  [>] Strategy 1: network interception fallback...")
    api_items = await _try_intercept(browser)
    if api_items:
        rows = [r for r in (_parse_api_item(i) for i in api_items) if r]
        unique = _dedup_rows(rows)
        print(f"  [+] Strategy 1 yielded {len(unique)} universities")
        return unique[:MAX_LAYER_A]

    # ── Strategy 2: DOM link scan ─────────────────────────────────────────────
    print("  [>] Strategy 2: DOM link scan fallback...")
    rows = await _try_dom_links(browser)
    return rows[:MAX_LAYER_A]


# ---------------------------------------------------------------------------
# Layer B helpers
# ---------------------------------------------------------------------------

async def _scrape_text(page, selectors: list[str], max_chars: int = 1200) -> str:
    for sel in selectors:
        try:
            els = await page.query_selector_all(sel)
            if not els:
                continue
            parts = [
                t for el in els[:8]
                if (t := (await el.inner_text()).strip()) and len(t) > 20
            ]
            if parts:
                return " ".join(parts)[:max_chars]
        except Exception:
            continue
    return ""


async def _scrape_score(page, selectors: list[str]) -> float | None:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            text = await el.inner_text(timeout=4_000)
            cleaned = re.sub(r"[^\d.]", "", text.strip())
            if cleaned:
                val = float(cleaned)
                if 0 <= val <= 100:
                    return val
        except Exception:
            continue
    return None


# Score selectors for the main metrics visible on the detail page itself
# (used so Layer B can fill gaps when Layer A has no scores)
_MAIN_SCORES: dict[str, list[str]] = {
    "overall_score": [
        "[class*='overall'] [class*='score'] span",
        "[class*='overall-score']",
        "[class*='score-overall']",
        "[data-score='overall']",
        "[class*='rank-score']",
    ],
    "academic_reputation": [
        "[class*='academic'] [class*='score']",
        "[data-indicator='ar']",
        "[class*='ar-score']",
    ],
    "employer_reputation": [
        "[class*='employer'] [class*='score']",
        "[data-indicator='er']",
        "[class*='er-score']",
    ],
    "citations_per_faculty": [
        "[class*='citations'] [class*='score']",
        "[data-indicator='cf']",
    ],
    "intl_faculty_ratio": [
        "[class*='intl-faculty'] [class*='score']",
        "[data-indicator='ifr']",
    ],
    "intl_student_ratio": [
        "[class*='intl-student'] [class*='score']",
        "[data-indicator='isr']",
    ],
}


async def scrape_detail_page(context, url: str, uni_name: str) -> dict:
    """
    Scrape one university detail page.
    Returns all scores, description, key facts and review snippets.
    """
    page = await context.new_page()
    await stealth_async(page)

    result: dict = {
        "detail_url":    url,
        "university_name": uni_name,
        # main metric scores (may already be set from Layer A; filled here if not)
        "overall_score":   None,
        "academic_reputation": None,
        "employer_reputation": None,
        "citations_per_faculty": None,
        "intl_faculty_ratio": None,
        "intl_student_ratio": None,
        # lens tab scores
        **{field: None for field in DETAIL_TABS.values()},
        # qualitative fields
        "description":          "",
        "university_type":      "",
        "founded_year":         "",
        "total_students":       "",
        "student_faculty_ratio": "",
        "review_snippets":      "",
    }

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        # ── Main metric scores ────────────────────────────────────────────────
        for field, selectors in _MAIN_SCORES.items():
            val = await _scrape_score(page, selectors)
            if val is not None:
                result[field] = val

        # ── Lens tab scores ───────────────────────────────────────────────────
        for tab_label, field_name in DETAIL_TABS.items():
            try:
                btn = page.locator(
                    f"button:has-text('{tab_label}'), "
                    f"[role='tab']:has-text('{tab_label}'), "
                    f"a:has-text('{tab_label}')"
                ).first
                await btn.click(timeout=8_000)
                await asyncio.sleep(random.uniform(0.8, 1.5))

                score_sels = [
                    "[class*='lens-score'] .value",
                    "[class*='tab-score']",
                    "[class*='overall-score'] span",
                    "[data-score]",
                    "[class*='score'] .number",
                    "[class*='score-value']",
                ]
                val = await _scrape_score(page, score_sels)
                if val is not None:
                    result[field_name] = val

            except Exception as e:
                logging.warning("Tab '%s' failed for %s: %s", tab_label, uni_name, e)

        # ── Description ───────────────────────────────────────────────────────
        result["description"] = await _scrape_text(page, _DESC_SELECTORS)

        # ── Key facts ─────────────────────────────────────────────────────────
        facts_raw = await _scrape_text(page, _FACTS_SELECTORS, max_chars=800)
        if facts_raw:
            fl = facts_raw.lower()
            for kw in ("public", "private", "state", "national", "federal"):
                if kw in fl:
                    result["university_type"] = kw.capitalize()
                    break
            yr = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", facts_raw)
            if yr:
                result["founded_year"] = yr.group(1)
            stu = re.search(r"([\d,]+)\s*(?:students|enrolled|undergrad|graduate)", fl)
            if stu:
                result["total_students"] = stu.group(1).replace(",", "")
            ratio = re.search(r"(\d+)\s*[:/]\s*(\d+)", facts_raw)
            if ratio:
                result["student_faculty_ratio"] = f"{ratio.group(1)}:{ratio.group(2)}"

        # ── Reviews ───────────────────────────────────────────────────────────
        result["review_snippets"] = await _scrape_text(page, _REVIEW_SELECTORS, max_chars=600)

    except Exception as e:
        logging.error("Detail page failed for %s (%s): %s", uni_name, url, e)
    finally:
        await page.close()

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("[*] QS Rankings Scraper starting\n")
    MAIN_CSV.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        # ── Layer A ───────────────────────────────────────────────────────────
        print("=" * 60)
        print("[LAYER A] Collecting university list...")
        print("=" * 60)
        rows_a = await scrape_layer_a(browser)

        if not rows_a:
            print("\n[!] Layer A returned 0 rows.")
            print("    Possible causes:")
            print("    • The site blocked the scraper (try again later)")
            print("    • The site URL changed (check BASE_URL in scraper.py)")
            print("    Check logs/scraper.log for details.")
            await browser.close()
            return

        fieldnames_a = [
            "rank", "university_name", "country", "overall_score",
            "citations_per_faculty", "academic_reputation", "employer_reputation",
            "intl_faculty_ratio", "intl_student_ratio", "detail_url",
        ]
        with open(MAIN_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames_a, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows_a)
        print(f"\n[+] Layer A -> {MAIN_CSV}  ({len(rows_a)} universities)")

        # ── Layer B ───────────────────────────────────────────────────────────
        targets = [r for r in rows_a if r.get("detail_url")][:MAX_LAYER_B]
        print(f"\n{'=' * 60}")
        print(f"[LAYER B] Scraping {len(targets)} detail pages...")
        print("=" * 60)

        context = await browser.new_context(**_new_context_kwargs())
        detail_rows: list[dict] = []

        for i, row in enumerate(targets, 1):
            name = row["university_name"]
            url  = row["detail_url"]
            print(f"  [{i:>3}/{len(targets)}] {name[:60]}")
            d = await scrape_detail_page(context, url, name)
            # Back-fill any main scores that Layer A already has
            for fld in ("overall_score", "academic_reputation", "employer_reputation",
                        "citations_per_faculty", "intl_faculty_ratio", "intl_student_ratio"):
                if d[fld] is None and row.get(fld) is not None:
                    d[fld] = row[fld]
            detail_rows.append(d)
            await asyncio.sleep(random.uniform(2.0, 4.5))

        await context.close()

        fieldnames_b = (
            ["detail_url", "university_name",
             "overall_score", "academic_reputation", "employer_reputation",
             "citations_per_faculty", "intl_faculty_ratio", "intl_student_ratio"]
            + list(DETAIL_TABS.values())
            + ["description", "university_type", "founded_year",
               "total_students", "student_faculty_ratio", "review_snippets"]
        )
        with open(DETAIL_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames_b, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(detail_rows)
        print(f"\n[+] Layer B -> {DETAIL_CSV}  ({len(detail_rows)} universities)")
        await browser.close()

    print("\n[+] Done. Check logs/scraper.log for any warnings.")


if __name__ == "__main__":
    asyncio.run(main())
