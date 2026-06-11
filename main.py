"""
Main orchestrator.

Pipeline: scrape LinkedIn/Glassdoor -> freshness filter -> dedupe -> score -> email.
"""
import argparse
import asyncio
import time
from collections import Counter
from datetime import datetime

from ai_engine.matcher import filter_jobs, preload_common_answers
from config import PROFILE, SEARCH
from data.database import (
    bulk_save_seen,
    get_stats,
    init_db,
    job_exists,
    job_exists_by_title_company,
    save_job,
)
from notifier.notifications import notify_all
from scrapers.glassdoor_scraper import run_glassdoor_scraper
from scrapers.linkedin_scraper import run_linkedin_scraper
from scrapers.recency_filter import apply_recency_filter

try:
    from config import SCHEDULER
except ImportError:
    SCHEDULER = {"check_interval_minutes": 60}


SCRAPER_REGISTRY = {
    "LinkedIn": run_linkedin_scraper,
    "Glassdoor": run_glassdoor_scraper,
}


def get_enabled_scrapers() -> list[tuple[str, object]]:
    configured = SEARCH.get("enabled_sources", list(SCRAPER_REGISTRY))
    wanted = [str(name).strip() for name in configured if str(name).strip()]

    enabled = []
    for name in wanted:
        scraper = SCRAPER_REGISTRY.get(name)
        if scraper:
            enabled.append((name, scraper))
        else:
            print(f"  Ignoring unknown scraper source in config: {name}")

    return enabled


def print_banner():
    target_name = PROFILE.get("name", "Candidate")
    print(
        "\n"
        "======================================================\n"
        "  JOB HUNTER - LinkedIn + Glassdoor\n"
        f"  Built for: {target_name}\n"
        "======================================================"
    )


async def run_pipeline():
    active_scrapers = get_enabled_scrapers()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"\n{'=' * 54}")
    print(f"  Pipeline started: {now}")
    print(f"{'=' * 54}")

    if not active_scrapers:
        print("  No enabled scrapers. Set SEARCH['enabled_sources'] to LinkedIn and/or Glassdoor.")
        return

    print("\nStep 1: Scraping active job platforms...")
    all_scraped = []
    platform_counts = {}

    for name, scraper_fn in active_scrapers:
        try:
            jobs = await scraper_fn()
            platform_counts[name] = len(jobs)
            all_scraped.extend(jobs)
        except Exception as e:
            print(f"  {name} scraper failed: {e}")
            platform_counts[name] = 0

    print("\nPlatform breakdown:")
    for name, count in platform_counts.items():
        bar = "#" * min(count, 30)
        print(f"  {name:<12} {bar} {count}")
    print(f"  Total scraped: {len(all_scraped)} jobs")

    print("\nStep 1b: Applying freshness filter...")
    all_scraped = apply_recency_filter(all_scraped, strict=True)

    print("\nStep 2: Filtering already-seen jobs...")
    new_jobs = [
        job for job in all_scraped
        if not job_exists(job["id"])
        and not job_exists_by_title_company(job["title"], job["company"])
    ]
    skipped = len(all_scraped) - len(new_jobs)
    print(f"  New: {len(new_jobs)} | Already seen: {skipped}")

    if not new_jobs:
        print("  No new jobs this run.")
        return

    bulk_save_seen(new_jobs)
    print(f"  Saved {len(new_jobs)} jobs as seen")

    print(f"\nStep 3: Filtering and Groq scoring {len(new_jobs)} new jobs...")
    qualifying_jobs = filter_jobs(new_jobs)
    print(f"  Qualifying (score >= {SEARCH['min_match_score']}): {len(qualifying_jobs)}")

    reviewed_counts = Counter(job.get("status", "seen") for job in new_jobs)
    for job in new_jobs:
        save_job(job, status=job.get("status", "seen"))
    print(f"  Persisted reviewed jobs: {dict(reviewed_counts)}")

    if qualifying_jobs:
        q_by_platform = Counter(job.get("source") for job in qualifying_jobs)
        print(f"  Qualifying by platform: {dict(q_by_platform)}")

    print("\nStep 4: Sending notification...")
    notify_all(qualifying_jobs)

    stats = get_stats()
    print(
        "\nSession Summary:\n"
        f"   Platforms scraped : {len([c for c in platform_counts.values() if c > 0])}/{len(active_scrapers)}\n"
        f"   Total scraped     : {len(all_scraped)}\n"
        f"   New jobs found    : {len(new_jobs)}\n"
        f"   Qualifying        : {len(qualifying_jobs)}\n"
        f"   Total in DB       : {stats['total_jobs']}\n"
    )


def run_scheduler():
    interval = int(SCHEDULER["check_interval_minutes"]) * 60
    print_banner()
    init_db()
    preload_common_answers()
    print(f"Scheduler started. Running every {SCHEDULER['check_interval_minutes']} minutes.")

    while True:
        try:
            asyncio.run(run_pipeline())
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\nPipeline error: {e}")
        time.sleep(interval)


def run_once():
    print_banner()
    init_db()
    preload_common_answers()
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Hunter Bot")
    parser.add_argument("--once", action="store_true", help="Run one scrape/score/notify cycle")
    parser.add_argument("--schedule", action="store_true", help="Run continuously on the configured interval")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_scheduler()
