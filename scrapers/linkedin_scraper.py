import asyncio
import hashlib
from urllib.parse import quote
from playwright.async_api import async_playwright
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import SEARCH

def make_job_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:16]

def build_linkedin_url(role: str, location: str) -> str:
    return (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={quote(role)}&location={quote(location)}"
        f"&f_TPR=r86400&sortBy=DD"
    )

async def scrape_linkedin(role: str, location: str, max_jobs: int = 25) -> list[dict]:
    jobs = []
    url = build_linkedin_url(role, location)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        page = await context.new_page()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 800)")
                await asyncio.sleep(1)

            cards = await page.query_selector_all(".job-search-card, .base-card")

            for card in cards[:max_jobs]:
                try:
                    title_el = await card.query_selector(".base-search-card__title, h3.base-search-card__title")
                    company_el = await card.query_selector(".base-search-card__subtitle, h4.base-search-card__subtitle")
                    location_el = await card.query_selector(".job-search-card__location")
                    link_el = await card.query_selector("a.base-card__full-link")
                    time_el = await card.query_selector("time")

                    title   = (await title_el.inner_text()).strip() if title_el else ""
                    company = (await company_el.inner_text()).strip() if company_el else ""
                    loc     = (await location_el.inner_text()).strip() if location_el else ""
                    href    = await link_el.get_attribute("href") if link_el else ""

                    # ── DEEP SCAN: Fetch Description ──
                    description = ""
                    try:
                        await card.click()
                        await asyncio.sleep(1.5)
                        desc_el = await page.query_selector(".show-more-less-html__markup, .core-section-container__content, .description__text")
                        if desc_el:
                            description = (await desc_el.inner_text()).strip()
                    except Exception:
                        pass # Silently fail if unable to fetch deep description

                    posted_at = ""
                    if time_el:
                        dt_attr = await time_el.get_attribute("datetime") or ""
                        dt_text = (await time_el.inner_text()).strip()
                        raw = dt_attr or dt_text
                        if raw:
                            from scrapers.date_utils import scrape_time_text
                            posted_at = scrape_time_text(raw)

                    clean_url = href.split("?")[0] if href else ""

                    if title and company and clean_url:
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
                    continue
        except Exception as e:
            print(f"  ⚠️  LinkedIn scrape error: {e}")
        finally:
            await browser.close()

    print(f"  📋 LinkedIn [{role} - {location}] → {len(jobs)} jobs found")
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