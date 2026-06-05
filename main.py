"""
Main Orchestrator — scrapes LinkedIn, Glassdoor
Pipeline: Scrape → Filter → Score → Email
"""
import asyncio
import time
from datetime import datetime
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent))

from config import SEARCH
try:
    from config import SCHEDULER
except ImportError:
    SCHEDULER = {"check_interval_minutes": 60}

from data.database import init_db, job_exists, job_exists_by_title_company, save_job, bulk_save_seen, get_stats
from scrapers.linkedin_scraper  import run_linkedin_scraper
from scrapers.glassdoor_scraper import run_glassdoor_scraper
from ai_engine.matcher import filter_jobs, preload_common_answers
from notifier.notifications import notify_all
from scrapers.recency_filter import apply_recency_filter


SCRAPERS = [
    ("LinkedIn",  run_linkedin_scraper),
    ("Glassdoor", run_glassdoor_scraper),
]


def print_banner():
    print("""
╔══════════════════════════════════════════════════════╗
║  🎯 JOB HUNTER — Multi-Platform Java Full Stack Bot  ║
║  Sources: LinkedIn · Glassdoor                       ║
║  Built for: Raviteja Ramisetti                       ║
╚══════════════════════════════════════════════════════╝
    """)


async def run_pipeline():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*54}")
    print(f"  🔄 Pipeline started: {now}")
    print(f"{'='*54}")

    # ── Step 1: Scrape all platforms ──────────
    print("\n📡 Step 1: Scraping all job platforms...")
    all_scraped = []
    platform_counts = {}

    for name, scraper_fn in SCRAPERS:
        try:
            jobs = await scraper_fn()
            platform_counts[name] = len(jobs)
            all_scraped.extend(jobs)
        except Exception as e:
            print(f"  ⚠️  {name} scraper failed: {e}")
            platform_counts[name] = 0

    print(f"\n  Platform breakdown:")
    for name, count in platform_counts.items():
        bar = "█" * min(count, 30)
        print(f"    {name:<12} {bar} {count}")
    print(f"  Total scraped: {len(all_scraped)} jobs")

    # ── Step 1b: 24-hour freshness filter ─────
    print("\n🕐 Step 1b: Applying 24-hour freshness filter...")
    all_scraped = apply_recency_filter(all_scraped, strict=True)

    # ── Step 2: Deduplicate ───────────────────
    print("\n🔍 Step 2: Filtering already-seen jobs...")
    new_jobs = [
        j for j in all_scraped
        if not job_exists(j["id"])
        and not job_exists_by_title_company(j["title"], j["company"])
    ]
    skipped = len(all_scraped) - len(new_jobs)
    print(f"  New: {len(new_jobs)}  |  Already seen: {skipped}")

    if not new_jobs:
        print("  ℹ️  No new jobs this run.")
        return

    bulk_save_seen(new_jobs)
    print(f"  💾 Saved {len(new_jobs)} jobs to DB")

    # ── Step 3: AI Scoring ────────────────────
    print(f"\n🤖 Step 3: Deep Scanning & AI scoring {len(new_jobs)} new jobs...")
    qualifying_jobs = filter_jobs(new_jobs)
    print(f"  Qualifying (score ≥ {SEARCH['min_match_score']}): {len(qualifying_jobs)}")

    if qualifying_jobs:
        from collections import Counter
        q_by_platform = Counter(j.get("source") for j in qualifying_jobs)
        print(f"  By platform: {dict(q_by_platform)}")

    for job in qualifying_jobs:
        save_job(job)

    # ── Step 4: Send email alert ──────────────
    print(f"\n🔔 Step 4: Sending email alert...")
    notify_all(qualifying_jobs)

    # ── Summary ───────────────────────────────
    stats = get_stats()
    print(f"""
📊 Session Summary:
   • Platforms scraped  : {len([c for c in platform_counts.values() if c > 0])}/{len(SCRAPERS)}
   • Total scraped      : {len(all_scraped)}
   • New jobs found     : {len(new_jobs)}
   • Qualifying         : {len(qualifying_jobs)}
   • Total in DB        : {stats['total_jobs']}
    """)


def run_scheduler():
    interval = SCHEDULER["check_interval_minutes"] * 60
    print_banner()
    init_db()
    preload_common_answers()
    print(f"⏰ Scheduler started. Running every {SCHEDULER['check_interval_minutes']} minutes.")
    
    while True:
        try:
            asyncio.run(run_pipeline())
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n⚠️  Pipeline error: {e}")
        time.sleep(interval)


def run_once():
    print_banner()
    init_db()
    preload_common_answers()
    asyncio.run(run_pipeline())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Job Hunter Bot")
    parser.add_argument("--once",     action="store_true")
    parser.add_argument("--schedule", action="store_true")
    args = parser.parse_args()

    if args.once:
        run_once()
    else:
        run_scheduler()