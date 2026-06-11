import asyncio
import hashlib
import sys
from pathlib import Path
from urllib.parse import quote

from playwright.async_api import async_playwright

sys.path.append(str(Path(__file__).parent.parent))
from config import SEARCH
from scrapers.date_utils import scrape_time_text


def make_job_id(url: str) -> str:
    return "li_" + hashlib.md5(url.encode()).hexdigest()[:14]


def build_linkedin_url(role: str, location: str) -> str:
    return (
        "https://www.linkedin.com/jobs/search/"
        f"?keywords={quote(role)}&location={quote(location)}"
        "&f_TPR=r86400&sortBy=DD"
    )


def _max_jobs(default: int) -> int:
    return int(SEARCH.get("max_jobs_per_search", default))


async def scrape_linkedin(role: str, location: str, max_jobs: int | None = None) -> list[dict]:
    jobs = []
    url = build_linkedin_url(role, location)
    limit = max_jobs or _max_jobs(25)
    cards_found = 0
    skipped_missing = 0
    skipped_errors = 0
    missing_date = 0
    missing_description = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(1)

            cards = await page.query_selector_all(".job-search-card, .base-card")
            cards_found = len(cards)

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector(".base-search-card__title, h3.base-search-card__title")
                    company_el = await card.query_selector(".base-search-card__subtitle, h4.base-search-card__subtitle")
                    location_el = await card.query_selector(".job-search-card__location")
                    link_el = await card.query_selector("a.base-card__full-link")
                    time_el = await card.query_selector("time")

                    title = (await title_el.inner_text()).strip() if title_el else ""
                    company = (await company_el.inner_text()).strip() if company_el else ""
                    loc = (await location_el.inner_text()).strip() if location_el else ""
                    href = await link_el.get_attribute("href") if link_el else ""
                    clean_url = href.split("?")[0] if href else ""

                    if not (title and company and clean_url):
                        skipped_missing += 1
                        continue

                    description = ""
                    try:
                        await card.click()
                        await asyncio.sleep(1.5)
                        desc_el = await page.query_selector(
                            ".show-more-less-html__markup, "
                            ".core-section-container__content, "
                            ".description__text"
                        )
                        if desc_el:
                            description = (await desc_el.inner_text()).strip()
                    except Exception:
                        pass

                    if not description:
                        missing_description += 1

                    posted_at = ""
                    if time_el:
                        dt_attr = await time_el.get_attribute("datetime") or ""
                        dt_text = (await time_el.inner_text()).strip()
                        posted_at = scrape_time_text(dt_attr or dt_text)

                    if not posted_at:
                        missing_date += 1

                    jobs.append({
                        "id": make_job_id(clean_url),
                        "title": title,
                        "company": company,
                        "location": loc,
                        "url": clean_url,
                        "source": "LinkedIn",
                        "posted_at": posted_at,
                        "job_type": "Full-time",
                        "description": description,
                    })
                except Exception:
                    skipped_errors += 1
        except Exception as e:
            print(f"  LinkedIn scrape error [{role} - {location}]: {e}")
        finally:
            await browser.close()

    print(
        f"  LinkedIn [{role} - {location}] -> {len(jobs)} jobs "
        f"(cards={cards_found}, missing={skipped_missing}, errors={skipped_errors}, "
        f"no_date={missing_date}, no_desc={missing_description})"
    )
    return jobs


async def run_linkedin_scraper() -> list[dict]:
    all_jobs = []
    seen_ids = set()
    for role in SEARCH["roles"]:
        for location in SEARCH["locations"]:
            jobs = await scrape_linkedin(role, location)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await asyncio.sleep(2)
    return all_jobs
