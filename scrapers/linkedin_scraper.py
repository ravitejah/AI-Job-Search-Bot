"""
LinkedIn Scraper — Two-Stage Architecture

Stage 1 | run_linkedin_scraper()
    Shallow surface scrape. Opens ONE browser for all role+location queries.
    Extracts title/company/location/url/posted_at from the search list view.
    No card clicking. No description fetching. Fast.
    Returns whatever was collected even if a mid-run error occurs.

Stage 3 | fetch_linkedin_descriptions(jobs)
    Targeted deep fetch. Navigates directly to each filtered job URL.
    Uses asyncio.Semaphore(3) for bounded concurrency — polite to LinkedIn,
    avoids bot detection, still ~3x faster than sequential.
    Validates URLs before navigation; skips non-HTTPS hrefs.
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


def make_job_id(url: str) -> str:
    clean = url.split("?")[0].rstrip("/")
    return "li_" + hashlib.md5(clean.encode()).hexdigest()[:14]


def build_linkedin_url(role: str, location: str) -> str:
    # f_TPR=r<seconds> → posted within the freshness window (stays in sync with config)
    # sortBy=DD        → most recent first
    # f_E=2,3          → Entry level (2) + Associate (3) — platform-level filter
    freshness_seconds = int(SEARCH.get("freshness_hours", 48)) * 3600
    return (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote(role)}&location={quote(location)}"
        f"&f_TPR=r{freshness_seconds}&sortBy=DD&f_E=2%2C3"
    )


def _max_jobs() -> int:
    return int(SEARCH.get("max_jobs_per_search", 25))


async def _shallow_scrape_page(page, role: str, location: str) -> list:
    """
    Scrape the LinkedIn search list view for one role+location query.
    Extracts card metadata ONLY — no card clicking, no side-panel loading.
    Returns an empty list on any page-level failure (never raises).
    """
    jobs = []
    url = build_linkedin_url(role, location)
    try:
        if not await goto_with_retry(page, url):
            return jobs

        await asyncio.sleep(2)

        # Scroll 6 passes to trigger all lazy-loaded cards below the fold.
        for _ in range(6):
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(0.5)

        cards = await page.query_selector_all(".job-search-card, .base-card")

        for card in cards[:_max_jobs()]:
            try:
                title_el = await card.query_selector(
                    ".base-search-card__title, h3.base-search-card__title"
                )
                company_el = await card.query_selector(
                    ".base-search-card__subtitle, h4.base-search-card__subtitle"
                )
                location_el = await card.query_selector(".job-search-card__location")
                link_el = await card.query_selector("a.base-card__full-link")
                time_el = await card.query_selector("time")

                title = (await title_el.inner_text()).strip() if title_el else ""
                company = (await company_el.inner_text()).strip() if company_el else ""
                loc = (await location_el.inner_text()).strip() if location_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                # Strip query params — ensures stable, canonical job IDs.
                clean_url = href.split("?")[0] if href else ""

                if not (title and company and clean_url):
                    continue

                # Reject non-HTTPS hrefs (javascript:, data:, relative paths).
                if not is_valid_url(clean_url):
                    continue

                posted_at = ""
                if time_el:
                    dt_attr = await time_el.get_attribute("datetime") or ""
                    dt_text = (await time_el.inner_text()).strip()
                    posted_at = scrape_time_text(dt_attr or dt_text)

                jobs.append({
                    "id": make_job_id(clean_url),
                    "title": title,
                    "company": company,
                    "location": loc,
                    "url": clean_url,
                    "source": "LinkedIn",
                    "posted_at": posted_at,
                    "job_type": "Full-time",
                    "description": "",
                })
            except Exception:
                continue

    except Exception as e:
        print(f"  LinkedIn shallow error [{role} @ {location}]: {e}")

    return jobs


async def run_linkedin_scraper() -> list:
    """
    Stage 1: Shallow-scrape all role+location combinations.
    Reuses a single Chromium browser for all queries — no repeated launch overhead.
    Returns whatever was collected even if an error interrupts the loop.
    """
    all_jobs: list = []
    seen_ids: set = set()
    roles = SEARCH.get("roles", [])
    locations = SEARCH.get("locations", [])
    total = len(roles) * len(locations)

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
            query_num = 0
            try:
                for role in roles:
                    for location in locations:
                        query_num += 1
                        print(f"  LinkedIn [{query_num}/{total}] {role!r} @ {location!r}")
                        jobs = await _shallow_scrape_page(page, role, location)
                        new_count = 0
                        for job in jobs:
                            if job["id"] not in seen_ids:
                                seen_ids.add(job["id"])
                                all_jobs.append(job)
                                new_count += 1
                        print(f"    -> {len(jobs)} cards, {new_count} unique new")
                        await asyncio.sleep(1.5)
            except Exception as e:
                # Catch loop-level errors so we still return partial results.
                print(f"  LinkedIn scrape loop interrupted: {e}")
            finally:
                await browser.close()
    except Exception as e:
        print(f"  LinkedIn playwright init failed: {e}")

    print(f"  LinkedIn shallow complete: {len(all_jobs)} unique surface records")
    return all_jobs


async def fetch_linkedin_descriptions(jobs: list) -> list:
    """
    Stage 3: Targeted deep fetch for a filtered list of LinkedIn jobs.
    Navigates directly to each job URL (no search page needed).
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

                        await asyncio.sleep(1.5)

                        # Expand "Show more" if description is truncated.
                        try:
                            btn = await page.query_selector(
                                "button.show-more-less-html__button--more, "
                                "button[aria-label*='Show more'], "
                                "button[aria-label*='show more']"
                            )
                            if btn:
                                await btn.click()
                                await asyncio.sleep(0.5)
                        except Exception:
                            pass

                        # Try multiple selectors in priority order.
                        desc_el = await page.query_selector(
                            ".show-more-less-html__markup, "
                            "#job-details, "
                            ".jobs-description-content__text, "
                            ".description__text"
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
        print(f"  LinkedIn deep fetch playwright error: {e}")
        for job in jobs:
            if "description" not in job:
                job["description"] = ""

    fetched = sum(1 for j in jobs if j.get("description"))
    print(f"  LinkedIn deep fetch: {fetched}/{len(jobs)} descriptions retrieved")
    return jobs
