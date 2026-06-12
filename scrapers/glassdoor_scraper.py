"""
Glassdoor Scraper — Two-Stage Architecture

Stage 1 | run_glassdoor_scraper()
    Shallow surface scrape. Opens ONE browser for all role queries.
    Extracts title/company/location/url/posted_at from the job card list.
    No card clicking. No description fetching. Fast.
    Returns whatever was collected even if a mid-run error occurs.

Stage 3 | fetch_glassdoor_descriptions(jobs)
    Targeted deep fetch. Navigates directly to each filtered job URL.
    Uses asyncio.Semaphore(3) for bounded concurrency.
    Validates URLs before navigation; skips non-HTTPS hrefs.

Bug fix: Glassdoor job IDs are generated from the clean canonical URL
(query params stripped). Previously the raw href was hashed, causing the same
job to receive a different ID on every scrape when Glassdoor appended tracking
params — leading to endless re-scraping of already-seen jobs.
"""
import asyncio
import hashlib
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

sys.path.append(str(Path(__file__).parent.parent))
from config import SEARCH
from scrapers.browser_utils import goto_with_retry, is_valid_url, random_ua
from scrapers.date_utils import scrape_time_text


def _canonical_url(href: str) -> str:
    """
    Return the canonical Glassdoor job URL with tracking query params stripped.
    The job identity is in the path, not the query string.
    """
    if not href:
        return ""
    if not href.startswith("http"):
        href = "https://www.glassdoor.co.in" + href
    return href.split("?")[0].rstrip("/")


def make_job_id(url: str) -> str:
    clean = _canonical_url(url)
    return "gd_" + hashlib.md5(clean.encode()).hexdigest()[:14]


def build_glassdoor_url(role: str, location: str = "India") -> str:
    # fromAge=3 → posted in the last 3 days at Glassdoor query level.
    # We use 3 (not 1) so jobs posted 23-47h ago aren't missed by Glassdoor's
    # day-boundary rounding. The Python freshness_hours filter (default 24h)
    # enforces the precise cutoff afterward.
    # sortBy=date_desc → most recent first
    return (
        "https://www.glassdoor.co.in/Job/jobs.htm"
        f"?sc.keyword={quote(role)}&locKeyword={quote(location)}"
        "&sortBy=date_desc&fromAge=3"
    )


def _max_jobs() -> int:
    return int(SEARCH.get("max_jobs_per_search", 20))


async def _dismiss_modal(page) -> None:
    """Attempt to close sign-in or cookie consent modals (silently)."""
    try:
        close_btn = await page.query_selector(
            "[alt='Close'], "
            "button[data-test='modal-close-btn'], "
            "button[class*='modal-close'], "
            "[data-test='modal-close'], "
            "button[aria-label='Close']"
        )
        if close_btn:
            await close_btn.click()
            await asyncio.sleep(0.8)
    except Exception:
        pass


async def _shallow_scrape_page(page, role: str, location: str) -> list:
    """
    Scrape Glassdoor search result list for one role query.
    Extracts card metadata ONLY — no card clicking.
    Returns an empty list on any page-level failure (never raises).
    """
    jobs = []
    url = build_glassdoor_url(role, location)
    try:
        if not await goto_with_retry(page, url):
            return jobs

        await asyncio.sleep(3)
        await _dismiss_modal(page)

        # Scroll to load more cards below the fold.
        for _ in range(4):
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(0.5)
        await _dismiss_modal(page)  # modal can re-appear after scroll

        cards = await page.query_selector_all(
            "li.JobsList_jobListItem__JBBUV, "
            "article.JobCard, "
            "[data-test='jobListing']"
        )

        for card in cards[:_max_jobs()]:
            try:
                title_el = await card.query_selector(
                    "a.JobCard_seoLink__WdqHZ, "
                    "[data-test='job-title'], "
                    ".job-title"
                )
                company_el = await card.query_selector(
                    "[data-test='employer-name'], "
                    ".EmployerProfile_employerName__Xemli"
                )
                location_el = await card.query_selector(
                    "[data-test='emp-location'], "
                    ".JobCard_location__N_iYE"
                )
                link_el = await card.query_selector(
                    "a[href*='/job-listing/'], a[href*='/Job/']"
                )
                date_el = await card.query_selector(
                    "[data-test='job-age'], "
                    ".JobCard_listingAge__KuaxZ, "
                    "[class*='listingAge'], "
                    "[class*='age'], "
                    "time"
                )

                title = (await title_el.inner_text()).strip() if title_el else ""
                company = (await company_el.inner_text()).strip() if company_el else "Unknown"
                job_location = (await location_el.inner_text()).strip() if location_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                clean_url = _canonical_url(href)

                if not (title and clean_url):
                    continue

                # Reject non-HTTPS hrefs.
                if not is_valid_url(clean_url):
                    continue

                posted_at = ""
                if date_el:
                    dt_attr = await date_el.get_attribute("datetime") or ""
                    dt_text = (await date_el.inner_text()).strip()
                    posted_at = scrape_time_text(dt_attr or dt_text)

                jobs.append({
                    "id": make_job_id(clean_url),   # use canonical URL, not raw href
                    "title": title,
                    "company": company,
                    "location": job_location,
                    "url": clean_url,
                    "source": "Glassdoor",
                    "posted_at": posted_at,
                    "job_type": "Full-time",
                    "description": "",
                })
            except Exception:
                continue

    except Exception as e:
        print(f"  Glassdoor shallow error [{role} @ {location}]: {e}")

    return jobs


async def run_glassdoor_scraper() -> list:
    """
    Stage 1: Shallow-scrape all roles.
    Reuses a single Chromium browser for all queries — no repeated launch overhead.
    Returns whatever was collected even if an error interrupts the loop.
    """
    all_jobs: list = []
    seen_ids: set = set()
    location = SEARCH.get("glassdoor_location", "India")
    roles = SEARCH.get("roles", [])
    total = len(roles)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=random_ua(),
                locale="en-IN",
            )
            page = await context.new_page()
            try:
                for i, role in enumerate(roles, 1):
                    print(f"  Glassdoor [{i}/{total}] {role!r} @ {location!r}")
                    jobs = await _shallow_scrape_page(page, role, location)
                    new_count = 0
                    for job in jobs:
                        if job["id"] not in seen_ids:
                            seen_ids.add(job["id"])
                            all_jobs.append(job)
                            new_count += 1
                    print(f"    -> {len(jobs)} cards, {new_count} unique new")
                    await asyncio.sleep(2)
            except Exception as e:
                # Catch loop-level errors so we still return partial results.
                print(f"  Glassdoor scrape loop interrupted: {e}")
            finally:
                await browser.close()
    except Exception as e:
        print(f"  Glassdoor playwright init failed: {e}")

    print(f"  Glassdoor shallow complete: {len(all_jobs)} unique surface records")
    return all_jobs


async def fetch_glassdoor_descriptions(jobs: list) -> list:
    """
    Stage 3: Targeted deep fetch for a filtered list of Glassdoor jobs.
    Navigates directly to each job URL.
    Uses asyncio.Semaphore(3) — max 3 concurrent page loads at a time.
    Modifies the job dicts in-place, populating the 'description' field.
    Skips any job whose URL fails the HTTPS validation check.
    """
    if not jobs:
        return jobs

    sem = asyncio.Semaphore(3)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                user_agent=random_ua(),
                locale="en-IN",
            )

            async def _fetch_one(job: dict) -> None:
                if not is_valid_url(job.get("url", "")):
                    job["description"] = ""
                    return

                async with sem:
                    page = None
                    try:
                        page = await context.new_page()
                        success = await goto_with_retry(page, job["url"])
                        if not success:
                            job["description"] = ""
                            return

                        await asyncio.sleep(2)
                        await _dismiss_modal(page)

                        # Try multiple selectors in priority order.
                        desc_el = await page.query_selector(
                            "[data-test='jobDescriptionText'], "
                            ".JobDetails_jobDescriptionWrapper__xGBca, "
                            ".jobDescriptionContent, "
                            "#JobDescriptionContainer"
                        )
                        job["description"] = (
                            (await desc_el.inner_text()).strip() if desc_el else ""
                        )
                    except Exception:
                        job["description"] = ""
                    finally:
                        if page is not None:
                            await page.close()

            await asyncio.gather(*[_fetch_one(j) for j in jobs])
            await browser.close()
    except Exception as e:
        print(f"  Glassdoor deep fetch playwright error: {e}")
        for job in jobs:
            if "description" not in job:
                job["description"] = ""

    fetched = sum(1 for j in jobs if j.get("description"))
    print(f"  Glassdoor deep fetch: {fetched}/{len(jobs)} descriptions retrieved")
    return jobs
