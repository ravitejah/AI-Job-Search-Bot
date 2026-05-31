"""
LinkedIn Job Scraper
Scrapes LinkedIn job listings using Playwright (headless browser).
No login required for basic listings; Easy Apply detection works with login.
"""
import asyncio
import hashlib
import re
from datetime import datetime
from playwright.async_api import async_playwright
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import SEARCH, PROFILE


def make_job_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]


def build_linkedin_url(role: str, location: str) -> str:
    from urllib.parse import quote
    role_enc = quote(role)
    loc_enc = quote(location)
    # f_TPR=r86400 = past 24 hours
    return (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={role_enc}&location={loc_enc}"
        f"&f_TPR=r86400&sortBy=DD"
    )


async def scrape_linkedin(role: str, location: str, max_jobs: int = 25) -> list[dict]:
    """Scrape LinkedIn jobs for a given role + location."""
    jobs = []
    url = build_linkedin_url(role, location)

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
            )
        )
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Scroll to load more listings
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(1)

            # Get job cards
            cards = await page.query_selector_all(".job-search-card, .base-card")

            for card in cards[:max_jobs]:
                try:
                    title_el = await card.query_selector(
                        ".base-search-card__title, h3.base-search-card__title"
                    )
                    company_el = await card.query_selector(
                        ".base-search-card__subtitle, h4.base-search-card__subtitle"
                    )
                    location_el = await card.query_selector(
                        ".job-search-card__location"
                    )
                    link_el = await card.query_selector("a.base-card__full-link")
                    time_el = await card.query_selector("time")

                    title   = (await title_el.inner_text()).strip()    if title_el    else ""
                    company = (await company_el.inner_text()).strip()  if company_el  else ""
                    loc     = (await location_el.inner_text()).strip() if location_el else ""
                    href    = await link_el.get_attribute("href")      if link_el     else ""

                    # LinkedIn <time> has both datetime attr and text like "2 hours ago"
                    posted_at = ""
                    if time_el:
                        dt_attr = await time_el.get_attribute("datetime") or ""
                        dt_text = (await time_el.inner_text()).strip()
                        raw = dt_attr or dt_text
                        if raw:
                            from scrapers.date_utils import scrape_time_text
                            posted_at = scrape_time_text(raw)

                    # Clean URL (remove tracking params after ?)
                    clean_url = href.split("?")[0] if href else ""

                    if title and company and clean_url:
                        jobs.append({
                            "id":        make_job_id(clean_url),
                            "title":     title,
                            "company":   company,
                            "location":  loc,
                            "url":       clean_url,
                            "source":    "LinkedIn",
                            "posted_at": posted_at,
                            "job_type":  "Full-time",
                            "description": "",
                        })
                except Exception:
                    continue

        except Exception as e:
            print(f"  ⚠️  LinkedIn scrape error: {e}")
        finally:
            await browser.close()

    print(f"  📋 LinkedIn [{role} - {location}] → {len(jobs)} jobs found")
    return jobs


async def run_linkedin_scraper() -> list[dict]:
    """Run scraper for all configured roles and locations."""
    all_jobs = []
    seen_ids = set()

    for role in SEARCH["roles"]:  # Removed limits
        for location in SEARCH["locations"]:  # Removed limits
            jobs = await scrape_linkedin(role, location)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await asyncio.sleep(2)  # be polite

    return all_jobs


if __name__ == "__main__":
    jobs = asyncio.run(run_linkedin_scraper())
    for j in jobs[:5]:
        print(f"  {j['title']} @ {j['company']} — {j['location']}")