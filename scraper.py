"""
scraper.py
Two-layer Playwright scraper for QS World University Rankings.

Layer A: Intercepts the internal JSON API on the main rankings page.
Layer B: Visits each university detail page and scrapes tab scores.

Usage:
    python scraper.py
"""

import asyncio
import csv
import json
import logging
import random
from pathlib import Path

from playwright.async_api import async_playwright, Response
from playwright_stealth import stealth_async

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = "https://www.topuniversities.com/world-university-rankings"
MAIN_CSV = Path("data/raw/rankings_main.csv")
DETAIL_CSV = Path("data/raw/rankings_detail.csv")
LOG_FILE = Path("logs/scraper.log")

MAX_DETAIL_PAGES = 200  # limit Layer B to Top 200 to keep runtime reasonable

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

DETAIL_TABS = {
    "Research & Discovery": "research_discovery",
    "Learning Experience": "learning_experience",
    "Employability": "employability",
    "Global Engagement": "global_engagement",
    "Sustainability": "sustainability",
}

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
# Layer A helpers
# ---------------------------------------------------------------------------

def _parse_layer_a_item(item: dict) -> dict:
    """Normalise one entry from the rankings JSON payload."""
    # Field names vary slightly across QS API versions
    def _get(*keys):
        for k in keys:
            v = item.get(k)
            if v not in (None, "", "-"):
                return v
        return None

    detail_path = _get("url", "guid", "link") or ""
    if detail_path and not detail_path.startswith("http"):
        detail_path = "https://www.topuniversities.com" + detail_path

    return {
        "rank": _get("rank_display", "rank", "overallRank"),
        "university_name": _get("title", "name", "institution"),
        "country": _get("country", "countryName", "location"),
        "overall_score": _get("overall_score", "score", "overallScore"),
        "citations_per_faculty": _get("citations", "cf_score", "citationsPerFaculty"),
        "academic_reputation": _get("ar_score", "academicReputation", "ar"),
        "employer_reputation": _get("er_score", "employerReputation", "er"),
        "intl_faculty_ratio": _get("ifr_score", "internationalFaculty", "ifr"),
        "intl_student_ratio": _get("isr_score", "internationalStudents", "isr"),
        "detail_url": detail_path,
    }


async def _intercept_rankings_json(page) -> list[dict]:
    """
    Register a response listener BEFORE navigating.
    TopUniversities loads ranking data via an internal JSON endpoint;
    this captures that payload without DOM parsing.
    """
    captured: list[dict] = []
    raw_payloads: list = []

    async def on_response(response: Response):
        url = response.url
        content_type = response.headers.get("content-type", "")
        # Match likely data endpoints
        if (
            "qs-rankings-data" in url
            or "rankings-data" in url
            or (
                "topuniversities.com" in url
                and "json" in content_type
                and response.status == 200
            )
        ):
            try:
                body = await response.body()
                data = json.loads(body)
                if isinstance(data, list) and len(data) > 0:
                    raw_payloads.extend(data)
                elif isinstance(data, dict):
                    # Some endpoints wrap the list: {"data": [...]}
                    for key in ("data", "results", "items", "universities"):
                        if isinstance(data.get(key), list):
                            raw_payloads.extend(data[key])
                            break
            except Exception as exc:
                logging.warning("Response parse error for %s: %s", url, exc)

    page.on("response", on_response)
    return raw_payloads


async def scrape_layer_a(browser) -> list[dict]:
    """Navigate to the rankings page and collect the main list via network interception."""
    context = await browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1440, "height": 900},
        locale="en-US",
    )
    page = await context.new_page()
    await stealth_async(page)

    raw_payloads = await _intercept_rankings_json(page)

    print(f"  [>] Navigating to {BASE_URL} ...")
    await page.goto(BASE_URL, wait_until="networkidle", timeout=90_000)

    # Scroll to trigger lazy-loading / pagination
    for _ in range(15):
        await page.keyboard.press("End")
        await asyncio.sleep(random.uniform(0.8, 1.8))

    # Wait a moment for remaining XHR responses to arrive
    await asyncio.sleep(3)
    await context.close()

    if not raw_payloads:
        # Fallback: try DOM scraping if network interception got nothing
        logging.warning("Network interception returned 0 items; attempting DOM fallback")
        print("  [!] Network interception empty - trying DOM fallback...")
        context2 = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1440, "height": 900},
        )
        page2 = await context2.new_page()
        await stealth_async(page2)
        await page2.goto(BASE_URL, wait_until="networkidle", timeout=90_000)
        await asyncio.sleep(5)
        # Try common row selectors used by QS
        selectors = [
            "tr[class*='ranking']",
            "[class*='uni-link']",
            "[data-nid]",
            "table tbody tr",
        ]
        for sel in selectors:
            rows = await page2.query_selector_all(sel)
            if rows:
                for row in rows[:500]:
                    try:
                        name_el = await row.query_selector("a, [class*='name'], td:nth-child(2)")
                        country_el = await row.query_selector("[class*='country'], td:nth-child(3)")
                        score_el = await row.query_selector("[class*='score'], td:last-child")
                        link_el = await row.query_selector("a[href*='/universities/']")
                        raw_payloads.append({
                            "title": await name_el.inner_text() if name_el else "",
                            "country": await country_el.inner_text() if country_el else "",
                            "score": await score_el.inner_text() if score_el else "",
                            "url": await link_el.get_attribute("href") if link_el else "",
                        })
                    except Exception:
                        continue
                if raw_payloads:
                    break
        await context2.close()

    rows = [_parse_layer_a_item(item) for item in raw_payloads]
    rows = [r for r in rows if r.get("university_name")]  # drop empty
    print(f"  [+] Layer A: captured {len(rows)} universities")
    return rows


# ---------------------------------------------------------------------------
# Layer B helpers
# ---------------------------------------------------------------------------

async def scrape_detail_page(context, url: str, uni_name: str) -> dict:
    """Visit one university detail page and scrape tab scores."""
    page = await context.new_page()
    await stealth_async(page)

    result = {
        "detail_url": url,
        "university_name": uni_name,
        **{field: None for field in DETAIL_TABS.values()},
    }

    try:
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        await asyncio.sleep(random.uniform(1.5, 3.0))

        for tab_label, field_name in DETAIL_TABS.items():
            try:
                # Try to find and click the tab
                tab_btn = page.locator(
                    f"button:has-text('{tab_label}'), "
                    f"[role='tab']:has-text('{tab_label}'), "
                    f"a:has-text('{tab_label}')"
                ).first
                await tab_btn.click(timeout=8_000)
                await asyncio.sleep(random.uniform(0.8, 1.5))

                # Attempt multiple score selectors in priority order
                score_selectors = [
                    "[class*='lens-score'] .value",
                    "[class*='tab-score']",
                    "[class*='overall-score'] span",
                    "[data-score]",
                    "[class*='score'] .number",
                ]
                score_text = None
                for sel in score_selectors:
                    try:
                        el = page.locator(sel).first
                        score_text = await el.inner_text(timeout=4_000)
                        if score_text and score_text.strip().replace(".", "").isdigit():
                            break
                    except Exception:
                        continue

                if score_text:
                    cleaned = score_text.strip().replace(",", "")
                    try:
                        result[field_name] = float(cleaned)
                    except ValueError:
                        pass

            except Exception as tab_exc:
                logging.warning("Tab '%s' failed for %s: %s", tab_label, uni_name, tab_exc)

    except Exception as page_exc:
        logging.error("Detail page failed for %s (%s): %s", uni_name, url, page_exc)
    finally:
        await page.close()

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main():
    print("[*] Starting QS Rankings Scraper")

    for directory in (MAIN_CSV.parent, DETAIL_CSV.parent):
        directory.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)

        # ── Layer A ──────────────────────────────────────────────────────────
        print("\n[*] Layer A: Scraping main rankings list...")
        rows_a = await scrape_layer_a(browser)

        if not rows_a:
            print("[!] Layer A returned 0 rows. Check logs/scraper.log for details.")
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
        print(f"[+] Layer A complete → {MAIN_CSV} ({len(rows_a)} rows)")

        # ── Layer B ──────────────────────────────────────────────────────────
        print(f"\n[*] Layer B: Scraping detail pages (up to {MAX_DETAIL_PAGES})...")

        context = await browser.new_context(
            user_agent=random.choice(USER_AGENTS),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )

        detail_rows = []
        targets = [r for r in rows_a if r.get("detail_url")][:MAX_DETAIL_PAGES]

        for i, row in enumerate(targets, 1):
            print(f"  [{i}/{len(targets)}] {row['university_name']}")
            detail = await scrape_detail_page(context, row["detail_url"], row["university_name"])
            detail_rows.append(detail)
            await asyncio.sleep(random.uniform(2.0, 5.0))

        await context.close()

        fieldnames_b = ["detail_url", "university_name"] + list(DETAIL_TABS.values())
        with open(DETAIL_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames_b, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(detail_rows)
        print(f"[+] Layer B complete → {DETAIL_CSV} ({len(detail_rows)} rows)")

        await browser.close()
    print("\n[+] Scraping finished. Check logs/scraper.log for any warnings/errors.")


if __name__ == "__main__":
    asyncio.run(main())
