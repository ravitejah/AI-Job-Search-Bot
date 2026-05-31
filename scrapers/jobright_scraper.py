"""
JobRight.ai Scraper — uses their search API directly (more reliable than browser scraping)
"""
import asyncio
import hashlib
import json
import urllib.request
import urllib.parse
from datetime import datetime
from playwright.async_api import async_playwright
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import SEARCH


def make_job_id(url: str) -> str:
    return "jr_" + hashlib.md5(url.encode()).hexdigest()[:14]


async def scrape_jobright_api(role: str, location: str, max_jobs: int = 25) -> list[dict]:
    """Try JobRight's internal API first — much faster and more reliable."""
    jobs = []
    try:
        url = (
            f"https://jobright.ai/api/jobs/search"
            f"?keyword={urllib.parse.quote(role)}"
            f"&location={urllib.parse.quote(location)}"
            f"&sortBy=date&pageSize={max_jobs}&page=1"
            f"&postedWithin=24"  # last 24 hours
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://jobright.ai/",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
            items = data.get("jobs", data.get("data", data.get("results", [])))
            for item in items[:max_jobs]:
                job_id  = str(item.get("id", item.get("jobId", "")))
                title   = item.get("title", item.get("jobTitle", "")).strip()
                company = item.get("company", item.get("companyName", "Unknown")).strip()
                loc     = item.get("location", item.get("jobLocation", location)).strip()
                href    = item.get("url", item.get("applyUrl", f"https://jobright.ai/jobs/{job_id}"))
                posted  = item.get("postedAt", item.get("datePosted", datetime.now().isoformat()))

                if title and href:
                    jobs.append({
                        "id": make_job_id(href), "title": title,
                        "company": company, "location": loc,
                        "url": href, "source": "JobRight",
                        "posted_at": posted, "job_type": "Full-time", "description": "",
                    })
        if jobs:
            print(f"  📋 JobRight API [{role} - {location}] → {len(jobs)} jobs found")
            return jobs
    except Exception as e:
        pass  # Fall through to browser scraping

    # Browser fallback
    return await scrape_jobright_browser(role, location, max_jobs)


async def scrape_jobright_browser(role: str, location: str, max_jobs: int = 20) -> list[dict]:
    """Browser-based fallback for JobRight."""
    jobs = []
    url = f"https://jobright.ai/jobs/search?keyword={urllib.parse.quote(role)}&location={urllib.parse.quote(location)}&sortBy=date"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # Intercept API calls made by the page
        captured_jobs = []
        async def handle_response(response):
            if "jobright.ai/api" in response.url or "jobright.ai/jobs" in response.url:
                try:
                    body = await response.json()
                    items = body.get("jobs", body.get("data", body.get("results", [])))
                    for item in items:
                        title   = item.get("title", item.get("jobTitle", "")).strip()
                        company = item.get("company", item.get("companyName", "Unknown")).strip()
                        loc     = item.get("location", "India") # Changed from US to India
                        job_id  = str(item.get("id", item.get("jobId", "")))
                        href    = item.get("url", f"https://jobright.ai/jobs/{job_id}")
                        posted  = item.get("postedAt", item.get("datePosted", ""))
                        if title:
                            captured_jobs.append({
                                "id": make_job_id(href or title+company),
                                "title": title, "company": company, "location": loc,
                                "url": href, "source": "JobRight",
                                "posted_at": posted, "job_type": "Full-time", "description": "",
                            })
                except Exception:
                    pass

        page.on("response", handle_response)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40000)
            await asyncio.sleep(6)  # Wait for API calls to complete

            if captured_jobs:
                jobs = captured_jobs[:max_jobs]
            else:
                # Manual DOM scraping as last resort
                cards = await page.query_selector_all(
                    "[class*='job-card'], [class*='JobCard'], article[class*='job'], li[class*='job']"
                )
                for card in cards[:max_jobs]:
                    try:
                        title = company = loc = href = posted = ""
                        for sel in ["h2","h3","[class*='title']"]:
                            el = await card.query_selector(sel)
                            if el: title = (await el.inner_text()).strip(); break
                        for sel in ["[class*='company']","[class*='Company']"]:
                            el = await card.query_selector(sel)
                            if el: company = (await el.inner_text()).strip(); break
                        for sel in ["[class*='location']"]:
                            el = await card.query_selector(sel)
                            if el: loc = (await el.inner_text()).strip(); break
                        for sel in ["time","[class*='date']","[class*='posted']"]:
                            el = await card.query_selector(sel)
                            if el:
                                posted = await el.get_attribute("datetime") or (await el.inner_text()).strip()
                                break
                        link_el = await card.query_selector("a")
                        if link_el:
                            href = await link_el.get_attribute("href") or ""
                            if href and not href.startswith("http"):
                                href = "https://jobright.ai" + href
                        if title and href:
                            jobs.append({
                                "id": make_job_id(href), "title": title,
                                "company": company or "Unknown", "location": loc or "India", # Fallback to India
                                "url": href, "source": "JobRight",
                                "posted_at": posted, "job_type": "Full-time", "description": "",
                            })
                    except Exception:
                        continue
        except Exception as e:
            print(f"  ⚠️  JobRight browser error: {e}")
        finally:
            await browser.close()

    print(f"  📋 JobRight browser [{role} - {location}] → {len(jobs)} jobs found")
    return jobs


async def run_jobright_scraper() -> list[dict]:
    all_jobs = []
    seen_ids = set()
    for role in SEARCH["roles"][:5]: # Checking more roles
        for location in SEARCH["locations"]: # Checking all locations
            jobs = await scrape_jobright_api(role, location)
            for job in jobs:
                if job["id"] not in seen_ids:
                    seen_ids.add(job["id"])
                    all_jobs.append(job)
            await asyncio.sleep(3)
    return all_jobs


if __name__ == "__main__":
    jobs = asyncio.run(run_jobright_scraper())
    print(f"\nTotal: {len(jobs)}")
    for j in jobs[:5]:
        print(f"  {j['title']} @ {j['company']} — {j['location']}")