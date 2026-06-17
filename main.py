"""
Main Orchestrator — Two-Stage Lazy-Loading Pipeline

Stage 1  Shallow surface scrape (metadata only, no card clicking).
         LinkedIn and Glassdoor run CONCURRENTLY via asyncio.gather — saves 3-4 min.

Stage 2a Freshness filter — drop jobs older than SEARCH['freshness_hours'].
Stage 2b DB deduplication — two batch queries instead of N individual lookups.
Stage 2c Title/keyword pre-filter — fast deterministic sieve before any API call.
         Drops payload from ~1500 surface records to ~30-40 genuine candidates.

Stage 3  Targeted deep fetch — navigate directly to each filtered job's URL.
         asyncio.Semaphore(3) keeps concurrent page loads bounded and polite.

Stage 4  AI scoring via Groq — one call per job inside asyncio.to_thread() so
         blocking time.sleep() rate-limit waits never stall the event loop.
         Incremental DB save after each score — pipeline is fully crash-safe.

Stage 5  Email notification.

Flags
-----
--once       Run one complete cycle and exit.
--schedule   Run continuously on SCHEDULER['check_interval_minutes'].
--dry-run    Run the full pipeline but skip DB writes and email.  Use to verify
             scraping and scoring without side effects.
"""
import argparse
import asyncio
import time
import traceback
from collections import Counter
from datetime import datetime, timezone, timedelta

from ai_engine.matcher import pre_filter_jobs, preload_common_answers, score_job
from config import PROFILE, SEARCH
from data.database import (
    bulk_save_seen,
    delete_old_jobs,
    get_all_seen_ids,
    get_all_seen_title_companies,
    get_stats,
    init_db,
    save_job,
)
from notifier.notifications import notify_all
from scrapers.glassdoor_scraper import fetch_glassdoor_descriptions, run_glassdoor_scraper
from scrapers.linkedin_scraper import fetch_linkedin_descriptions, run_linkedin_scraper
from scrapers.recency_filter import apply_recency_filter

IST = timezone(timedelta(hours=5, minutes=30))

try:
    from config import SCHEDULER
except ImportError:
    SCHEDULER = {"check_interval_minutes": 60}

# Maps source name → deep-fetch function for Stage 3.
_DEEP_FETCH = {
    "LinkedIn": fetch_linkedin_descriptions,
    "Glassdoor": fetch_glassdoor_descriptions,
}

# Maps source name → shallow-scrape function for Stage 1.
_SHALLOW_SCRAPE = {
    "LinkedIn": run_linkedin_scraper,
    "Glassdoor": run_glassdoor_scraper,
}


def print_banner(dry_run: bool = False):
    target_name = PROFILE.get("name", "Candidate")
    mode = "  *** DRY-RUN MODE — no DB writes, no email ***\n" if dry_run else ""
    print(
        "\n"
        "======================================================\n"
        "  JOB HUNTER - LinkedIn + Glassdoor\n"
        f"  Built for: {target_name}\n"
        f"{mode}"
        "======================================================"
    )


def _divider(label: str = "") -> None:
    line = "─" * 54
    if label:
        print(f"\n{line}\n  {label}\n{line}")
    else:
        print(line)


async def run_pipeline(dry_run: bool = False):
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    t_start = time.time()
    print(f"\n{'=' * 54}")
    print(f"  Pipeline started: {now}")
    if dry_run:
        print("  [DRY-RUN] DB writes and email are disabled.")
    print(f"{'=' * 54}")

    enabled_sources = SEARCH.get("enabled_sources", ["LinkedIn", "Glassdoor"])
    active = [s for s in enabled_sources if s in _SHALLOW_SCRAPE]
    if not active:
        print("  No valid enabled_sources in config. Exiting.")
        return

    # Remove seen/rejected records older than 30 days — keeps the DB lean
    # and ensures dedup queries stay fast as the database grows over time.
    if not dry_run:
        deleted = delete_old_jobs(days=30)
        if deleted:
            print(f"  DB cleanup: removed {deleted} stale seen/rejected jobs (>30 days)")

    # ─────────────────────────────────────────────────────────
    # Stage 1: Concurrent shallow surface scrape
    # LinkedIn and Glassdoor run simultaneously — saves ~3-4 min.
    # ─────────────────────────────────────────────────────────
    _divider(f"Stage 1 — Concurrent shallow scrape ({', '.join(active)})")

    scraper_tasks = [_SHALLOW_SCRAPE[source]() for source in active]
    raw_results = await asyncio.gather(*scraper_tasks, return_exceptions=True)

    all_shallow: list = []
    platform_counts: dict = {}

    for source, result in zip(active, raw_results):
        if isinstance(result, Exception):
            print(f"  {source} scraper failed: {result}")
            traceback.print_exception(type(result), result, result.__traceback__)
            platform_counts[source] = 0
        else:
            jobs: list = result
            platform_counts[source] = len(jobs)
            all_shallow.extend(jobs)

    print(f"\n  Surface scrape results:")
    for src, count in platform_counts.items():
        bar = "#" * min(count // 3, 30)
        print(f"    {src:<12} {bar} {count}")
    print(f"  Total surface records : {len(all_shallow)}")

    if not all_shallow:
        print("  No jobs scraped this run.")
        _print_stats(platform_counts, 0, 0, 0, t_start)
        return

    # ─────────────────────────────────────────────────────────
    # Stage 2a: Freshness filter
    # ─────────────────────────────────────────────────────────
    _divider("Stage 2a — Freshness filter")
    fresh_jobs = apply_recency_filter(all_shallow, strict=True)

    # ─────────────────────────────────────────────────────────
    # Stage 2b: Database deduplication (two batch queries)
    # ─────────────────────────────────────────────────────────
    _divider("Stage 2b — Database deduplication")
    seen_ids = get_all_seen_ids()
    seen_tc = get_all_seen_title_companies()

    new_candidates = [
        j for j in fresh_jobs
        if j["id"] not in seen_ids
        and (j["title"].lower().strip(), j["company"].lower().strip()) not in seen_tc
    ]
    skipped_db = len(fresh_jobs) - len(new_candidates)
    print(f"  New candidates : {len(new_candidates)}")
    print(f"  Already in DB  : {skipped_db}")

    if not new_candidates:
        print("  No new candidates this run.")
        _print_stats(platform_counts, len(all_shallow), 0, 0, t_start)
        return

    # ─────────────────────────────────────────────────────────
    # Stage 2c: Title/keyword pre-filter (fast, no API)
    # ─────────────────────────────────────────────────────────
    _divider("Stage 2c — Title/keyword pre-filter")
    pre_filtered = pre_filter_jobs(new_candidates)
    dropped_pre = len(new_candidates) - len(pre_filtered)
    print(f"  Passed pre-filter  : {len(pre_filtered)}")
    print(f"  Dropped by filter  : {dropped_pre}")

    # Mark ALL new_candidates as "seen" — before deep fetch — so a crash
    # mid-pipeline never re-processes the same records on the next run.
    if not dry_run:
        bulk_save_seen(new_candidates)
        print(f"  Marked {len(new_candidates)} candidates as 'seen' in DB")
    else:
        print(f"  [DRY-RUN] Would mark {len(new_candidates)} candidates as 'seen'")

    if not pre_filtered:
        print("  No candidates survived pre-filter.")
        _print_stats(platform_counts, len(all_shallow), len(new_candidates), 0, t_start)
        return

    # ─────────────────────────────────────────────────────────
    # Stage 3: Targeted deep fetch (descriptions only)
    # ─────────────────────────────────────────────────────────
    _divider(f"Stage 3 — Targeted deep fetch ({len(pre_filtered)} candidates)")

    by_source: dict = {}
    for job in pre_filtered:
        src = job.get("source", "LinkedIn")
        by_source.setdefault(src, []).append(job)

    for source, source_jobs in by_source.items():
        fetch_fn = _DEEP_FETCH.get(source)
        if not fetch_fn:
            print(f"  No deep fetcher registered for source: {source}")
            continue
        print(f"  Fetching {len(source_jobs)} {source} JDs (concurrency=3)...")
        try:
            await fetch_fn(source_jobs)
        except Exception as e:
            print(f"  Deep fetch failed for {source}: {e}")
            traceback.print_exc()

    got_desc = sum(1 for j in pre_filtered if j.get("description"))
    print(f"\n  Descriptions retrieved : {got_desc}/{len(pre_filtered)}")

    # ─────────────────────────────────────────────────────────
    # Stage 4: AI scoring — incremental DB save after each job
    # ─────────────────────────────────────────────────────────
    _divider(f"Stage 4 — AI scoring ({len(pre_filtered)} jobs via Groq)")
    qualifying: list = []
    min_score = int(SEARCH.get("min_match_score", 75))

    for i, job in enumerate(pre_filtered, 1):
        title_short = job["title"][:45] if len(job["title"]) > 45 else job["title"]
        print(f"  [{i:>2}/{len(pre_filtered)}] {title_short} @ {job['company'][:30]}...")

        # Run blocking Groq call + time.sleep in a thread — keeps event loop free.
        try:
            result = await asyncio.to_thread(score_job, job)
        except Exception as e:
            print(f"    Scoring error: {e}")
            result = {"score": 0, "reason": "Scoring failed.", "recommendation": "skip"}

        job["match_score"] = result.get("score", 0)
        job["match_reason"] = result.get("reason", "")
        job["recommendation"] = result.get("recommendation", "skip")
        job["status"] = "qualified" if job["match_score"] >= min_score else "rejected"

        # Incremental save — every job is persisted the moment it is scored.
        # A crash after this point loses no work.
        if not dry_run:
            save_job(job, status=job["status"])

        if job["status"] == "qualified":
            qualifying.append(job)
            print(f"    QUALIFIED  score={job['match_score']}  — {job['match_reason'][:70]}")
        else:
            print(f"    rejected   score={job['match_score']}  — {job['match_reason'][:70]}")

    print(f"\n  Qualifying (score >= {min_score}) : {len(qualifying)}")

    if qualifying:
        q_by_platform = Counter(j.get("source") for j in qualifying)
        print(f"  By platform: {dict(q_by_platform)}")

    # Cross-source dedup — the same job posted on both LinkedIn and Glassdoor
    # would otherwise appear twice in the email. Keep the higher-scored version.
    if len(qualifying) > 1:
        cross_seen: dict = {}
        deduped: list = []
        for job in sorted(qualifying, key=lambda j: j["match_score"], reverse=True):
            key = (job["title"].lower().strip(), job["company"].lower().strip())
            if key not in cross_seen:
                cross_seen[key] = True
                deduped.append(job)
        removed = len(qualifying) - len(deduped)
        if removed:
            print(f"  Cross-source dedup: removed {removed} duplicate(s) (same job on multiple platforms)")
        qualifying = deduped

    # ─────────────────────────────────────────────────────────
    # Stage 5: Notification
    # ─────────────────────────────────────────────────────────
    _divider("Stage 5 — Notification")
    if dry_run:
        print(f"  [DRY-RUN] Would send email for {len(qualifying)} qualifying jobs.")
    else:
        notify_all(qualifying)

    _print_stats(platform_counts, len(all_shallow), len(new_candidates), len(qualifying), t_start)


def _print_stats(
    platform_counts: dict,
    total_scraped: int,
    new_found: int,
    qualifying: int,
    t_start: float,
) -> None:
    elapsed = time.time() - t_start
    minutes, seconds = divmod(int(elapsed), 60)
    db_stats = get_stats()
    print(
        "\n"
        "══════════════════════════════════════════════════════\n"
        "  Session Summary\n"
        "══════════════════════════════════════════════════════\n"
        f"  Total surface scraped : {total_scraped}\n"
        f"  New candidates        : {new_found}\n"
        f"  Qualifying this run   : {qualifying}\n"
        f"  Total in DB           : {db_stats['total_jobs']}\n"
        f"  Total qualified ever  : {db_stats['qualified_jobs']}\n"
        f"  Pipeline runtime      : {minutes}m {seconds}s\n"
        "══════════════════════════════════════════════════════"
    )


def run_once(dry_run: bool = False):
    print_banner(dry_run)
    init_db()
    preload_common_answers()
    asyncio.run(run_pipeline(dry_run=dry_run))


def run_scheduler(dry_run: bool = False):
    interval = int(SCHEDULER.get("check_interval_minutes", 60)) * 60
    print_banner(dry_run)
    init_db()
    preload_common_answers()
    print(f"Scheduler started. Running every {SCHEDULER.get('check_interval_minutes', 60)} minutes.")

    while True:
        try:
            asyncio.run(run_pipeline(dry_run=dry_run))
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
            break
        except Exception as e:
            print(f"\nPipeline error: {e}")
            traceback.print_exc()
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nScheduler stopped.")
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Job Hunter Bot")
    parser.add_argument("--once", action="store_true", help="Run one scrape/score/notify cycle")
    parser.add_argument(
        "--schedule", action="store_true", help="Run continuously on the configured interval"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run full pipeline but skip DB writes and email (safe for testing)"
    )
    args = parser.parse_args()
    dry = args.dry_run

    if args.once:
        run_once(dry_run=dry)
    else:
        run_scheduler(dry_run=dry)
