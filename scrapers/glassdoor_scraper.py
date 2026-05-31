"""
Glassdoor Job Scraper
Scrapes Glassdoor job listings using Playwright.
"""
import asyncio
import hashlib
from urllib.parse import quote
from playwright.async_api import async_playwright
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import SEARCH


def make_job_id(url: str) -> str:
    return "gd_" + hashlib.md5(url.encode()).hexdigest()[:14]


def build_glassdoor_url(role: str, location: str) -> str:
    return (
        f"https://www.glassdoor.co.in/Job/jobs.htm"
        f"?sc.keyword={quote(role)}&sortBy=date_desc"
    )


async def scrape_glassdoor(role: str, max_jobs: int = 20) -> list[dict]:
    jobs = []
    url = build_glassdoor_url(role, "India")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-IN"
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(4)

            # Close sign-in modal if it appears
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

            for card in cards[:max_jobs]:
                try:
                    title_el    = await card.query_selector("a.JobCard_seoLink__WdqHZ, [data-test='job-title'], .job-title")
                    company_el  = await card.query_selector("[data-test='employer-name'], .EmployerProfile_employerName__Xemli")
                    location_el = await card.query_selector("[data-test='emp-location'], .JobCard_location__N_iYE")
                    link_el     = await card.query_selector("a[href*='/job-listing/'], a[href*='/Job/']")
                    date_el     = await card.query_selector(
                        "[data-test='job-age'], .JobCard_listingAge__KuaxZ, "
                        "[class*='age'], [class*='date'], [class*='posted'], time"
                    )

                    title    = (await title_el.inner_text()).strip()    if title_el    else ""
                    company  = (await company_el.inner_text()).strip()  if company_el  else ""
                    location = (await location_el.inner_text()).strip() if location_el else ""
                    href     = await link_el.get_attribute("href")      if link_el     else ""

                    posted_at = ""
                    if date_el:
                        dt_attr = await date_el.get_attribute("datetime") or ""
                        dt_text = (await date_el.inner_text()).strip()
                        raw = dt_attr or dt_text
                        from scrapers.date_utils import scrape_time_text
                        posted_at = scrape_time_text(raw)

                    if href and not href.startswith("http"):
                        href = "https://www.glassdoor.co.in" + href

                    if title and href:
                        jobs.append({
                            "id":        make_job_id(href),
                            "title":     title,
                            "company":   company or "Unknown",
                            "location":  location,
                            "url":       href,
                            "source":    "Glassdoor",
                            "posted_at": posted_at,
                            "job_type":  "Full-time",
                            "description": "",
                        })
                except Exception:
                    continue

        except Exception as e:
            print(f"  ⚠️  Glassdoor scrape error: {e}")
        finally:
            await browser.close()

    print(f"  📋 Glassdoor [{role}] → {len(jobs)} jobs found")
    return jobs


async def run_glassdoor_scraper() -> list[dict]:
    all_jobs = []
    seen_ids = set()
    for role in SEARCH["roles"]: # Removed limits
        jobs = await scrape_glassdoor(role)
        for job in jobs:
            if job["id"] not in seen_ids:
                seen_ids.add(job["id"])
                all_jobs.append(job)
        await asyncio.sleep(3)
    return all_jobs


if __name__ == "__main__":
    jobs = asyncio.run(run_glassdoor_scraper())
    for j in jobs[:5]:
        print(f"  {j['title']} @ {j['company']} — {j['location']}")