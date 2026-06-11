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
    return "gd_" + hashlib.md5(url.encode()).hexdigest()[:14]


def build_glassdoor_url(role: str, location: str = "India") -> str:
    return (
        "https://www.glassdoor.co.in/Job/jobs.htm"
        f"?sc.keyword={quote(role)}&locKeyword={quote(location)}&sortBy=date_desc"
    )


def _max_jobs(default: int) -> int:
    return int(SEARCH.get("max_jobs_per_search", default))


async def scrape_glassdoor(role: str, location: str = "India", max_jobs: int | None = None) -> list[dict]:
    jobs = []
    url = build_glassdoor_url(role, location)
    limit = max_jobs or _max_jobs(20)
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
            ),
            locale="en-IN",
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)

            try:
                close_btn = await page.query_selector("[alt='Close'], button[data-test='modal-close-btn']")
                if close_btn:
                    await close_btn.click()
                    await asyncio.sleep(1)
            except Exception:
                pass

            cards = await page.query_selector_all(
                "li.JobsList_jobListItem__JBBUV, article.JobCard, [data-test='jobListing']"
            )
            cards_found = len(cards)

            for card in cards[:limit]:
                try:
                    title_el = await card.query_selector(
                        "a.JobCard_seoLink__WdqHZ, [data-test='job-title'], .job-title"
                    )
                    company_el = await card.query_selector(
                        "[data-test='employer-name'], .EmployerProfile_employerName__Xemli"
                    )
                    location_el = await card.query_selector(
                        "[data-test='emp-location'], .JobCard_location__N_iYE"
                    )
                    link_el = await card.query_selector("a[href*='/job-listing/'], a[href*='/Job/']")
                    date_el = await card.query_selector(
                        "[data-test='job-age'], .JobCard_listingAge__KuaxZ, "
                        "[class*='age'], [class*='date'], [class*='posted'], time"
                    )

                    title = (await title_el.inner_text()).strip() if title_el else ""
                    company = (await company_el.inner_text()).strip() if company_el else "Unknown"
                    job_location = (await location_el.inner_text()).strip() if location_el else ""
                    href = await link_el.get_attribute("href") if link_el else ""

                    if href and not href.startswith("http"):
                        href = "https://www.glassdoor.co.in" + href

                    if not (title and href):
                        skipped_missing += 1
                        continue

                    description = ""
                    try:
                        await card.click()
                        await asyncio.sleep(1.5)
                        desc_el = await page.query_selector(
                            "[data-test='jobDescriptionText'], .JobDetails_jobDescriptionWrapper__xGBca"
                        )
                        if desc_el:
                            description = (await desc_el.inner_text()).strip()
                    except Exception:
                        pass

                    if not description:
                        missing_description += 1

                    posted_at = ""
                    if date_el:
                        dt_attr = await date_el.get_attribute("datetime") or ""
                        dt_text = (await date_el.inner_text()).strip()
                        posted_at = scrape_time_text(dt_attr or dt_text)

                    if not posted_at:
                        missing_date += 1

                    jobs.append({
                        "id": make_job_id(href),
                        "title": title,
                        "company": company,
                        "location": job_location,
                        "url": href,
                        "source": "Glassdoor",
                        "posted_at": posted_at,
                        "job_type": "Full-time",
                        "description": description,
                    })
                except Exception:
                    skipped_errors += 1
        except Exception as e:
            print(f"  Glassdoor scrape error [{role} - {location}]: {e}")
        finally:
            await browser.close()

    print(
        f"  Glassdoor [{role} - {location}] -> {len(jobs)} jobs "
        f"(cards={cards_found}, missing={skipped_missing}, errors={skipped_errors}, "
        f"no_date={missing_date}, no_desc={missing_description})"
    )
    return jobs


async def run_glassdoor_scraper() -> list[dict]:
    all_jobs = []
    seen_ids = set()
    location = SEARCH.get("glassdoor_location", "India")
    for role in SEARCH["roles"]:
        jobs = await scrape_glassdoor(role, location=location)
        for job in jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                all_jobs.append(job)
        await asyncio.sleep(3)
    return all_jobs
