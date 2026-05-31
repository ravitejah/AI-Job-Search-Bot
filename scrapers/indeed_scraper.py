"""
Indeed Job Scraper
Scrapes Indeed using Playwright. No login needed.
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
    return "in_" + hashlib.md5(url.encode()).hexdigest()[:14]


def _parse_indeed_date(text: str) -> str:
    """
    Convert Indeed's relative date text to an ISO datetime string.
    Examples: 'just posted', 'today', '1 day ago', '3 days ago'
    """
    from datetime import datetime, timedelta
    now = datetime.now()
    text = text.lower().strip()

    if any(w in text for w in ["just posted", "today", "just now", "active"]):
        return now.strftime("%Y-%m-%dT%H:%M:%S")
    elif "hour" in text:
        try:
            hours = int(''.join(filter(str.isdigit, text)) or 1)
            return (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            return now.strftime("%Y-%m-%dT%H:%M:%S")
    elif "day" in text:
        try:
            days = int(''.join(filter(str.isdigit, text)) or 1)
            return (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            return (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
    elif "week" in text:
        try:
            weeks = int(''.join(filter(str.isdigit, text)) or 1)
            return (now - timedelta(weeks=weeks)).strftime("%Y-%m-%dT%H:%M:%S")
        except Exception:
            return (now - timedelta(weeks=1)).strftime("%Y-%m-%dT%H:%M:%S")
    else:
        return now.strftime("%Y-%m-%dT%H:%M:%S")


def build_indeed_url(role: str, location: str) -> str:
    # Using in.indeed.com for India
    return (
        f"https://in.indeed.com/jobs?q={quote(role)}"
        f"&l={quote(location)}&sort=date&fromage=1&radius=0"
    )


async def scrape_indeed(role: str, location: str, max_jobs: int = 25) -> list[dict]:
    jobs = []
    url = build_indeed_url(role, location)

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

            cards = await page.query_selector_all("div.job_seen_beacon, div.cardOutline, .result")

            for card in cards[:max_jobs]:
                try:
                    title_el    = await card.query_selector("h2.jobTitle span, h2 a span")
                    company_el  = await card.query_selector("[data-testid='company-name'], .companyName")
                    location_el = await card.query_selector("[data-testid='text-location'], .companyLocation")
                    link_el     = await card.query_selector("h2 a, a.jcs-JobTitle")
                    date_el     = await card.query_selector(
                        "[data-testid='myJobsStateDate'], .date, span.date, "
                        "[class*='posted'], [class*='date'], span[class*='ago']"
                    )

                    title    = (await title_el.inner_text()).strip()    if title_el    else ""
                    company  = (await company_el.inner_text()).strip()  if company_el  else ""
                    location = (await location_el.inner_text()).strip() if location_el else ""
                    href     = await link_el.get_attribute("href")      if link_el     else ""

                    # Indeed shows "Posted 2 days ago" — convert to datetime
                    posted_at = ""
                    if date_el:
                        date_text = (await date_el.inner_text()).strip().lower()
                        posted_at = _parse_indeed_date(date_text)

                    if href and not href.startswith("http"):
                        href = "https://in.indeed.com" + href

                    if title and href:
                        jobs.append({
                            "id":        make_job_id(href),
                            "title":     title,
                            "company":   company or "Unknown",
                            "location":  location,
                            "url":       href,
                            "source":    "Indeed",
                            "posted_at": posted_at,
                            "job_type":  "Full-time",
                            "description": "",
                        })
                except Exception:
                    continue

        except Exception as e:
            print(f"  ⚠️  Indeed scrape error: {e}")
        finally:
            await browser.close()

    print(f"  📋 Indeed [{role}] → {len(jobs)} jobs found")
    return jobs


async def run_indeed_scraper() -> list[dict]:
    all_jobs = []
    seen_ids = set()
    for role in SEARCH["roles"]: # Removed limits
        for location in SEARCH["locations"]: # Removed limits
            jobs = await scrape_indeed(role, location)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await asyncio.sleep(3)
    return all_jobs


if __name__ == "__main__":
    jobs = asyncio.run(run_indeed_scraper())
    for j in jobs[:5]:
        print(f"  {j['title']} @ {j['company']} — {j['location']}")