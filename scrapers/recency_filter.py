"""
Freshness and duplicate filtering for scraped jobs.
"""
from datetime import datetime, timedelta

from scrapers.date_utils import parse_posted_datetime

try:
    from config import SEARCH
except ImportError:
    SEARCH = {}


def _freshness_hours() -> int:
    return int(SEARCH.get("freshness_hours", 24))


def _include_unknown_dates() -> bool:
    return bool(SEARCH.get("include_unknown_dates", True))


def _is_fresh(job: dict, hours: int, include_unknown: bool) -> tuple[bool, str]:
    dt = parse_posted_datetime(job.get("posted_at", ""))
    if not dt:
        job["posted_at_missing"] = True
        return include_unknown, "unknown"

    job["posted_at_missing"] = False
    cutoff = datetime.now() - timedelta(hours=hours)
    return dt >= cutoff, "fresh" if dt >= cutoff else "old"


def _job_key(job: dict) -> tuple[str, str]:
    return (
        (job.get("title") or "").lower().strip(),
        (job.get("company") or "").lower().strip(),
    )


def _posted_sort_value(job: dict) -> datetime:
    return parse_posted_datetime(job.get("posted_at", "")) or datetime.min


def deduplicate_by_title_company(jobs: list[dict]) -> list[dict]:
    """
    Remove reposted duplicates by title + company.
    Keeps the copy with the most recent parsed posting time.
    """
    seen: dict[tuple[str, str], dict] = {}
    for job in jobs:
        key = _job_key(job)
        if key not in seen:
            seen[key] = job
            continue

        if _posted_sort_value(job) > _posted_sort_value(seen[key]):
            seen[key] = job

    return list(seen.values())


def apply_recency_filter(jobs: list[dict], strict: bool = True) -> list[dict]:
    """
    Apply freshness filters, remove reposted duplicates, and sort newest first.
    Unknown dates are included by default but counted in the log.
    """
    original_count = len(jobs)
    hours = _freshness_hours()
    include_unknown = _include_unknown_dates()

    if strict:
        fresh = []
        removed_old = 0
        kept_unknown = 0
        removed_unknown = 0

        for job in jobs:
            keep, reason = _is_fresh(job, hours, include_unknown)
            if keep:
                fresh.append(job)
                if reason == "unknown":
                    kept_unknown += 1
            elif reason == "unknown":
                removed_unknown += 1
            else:
                removed_old += 1

        if removed_old:
            print(f"  Removed {removed_old} jobs older than {hours} hours")
        if kept_unknown:
            print(f"  Kept {kept_unknown} jobs with unknown posting time")
        if removed_unknown:
            print(f"  Removed {removed_unknown} jobs with unknown posting time")
    else:
        fresh = jobs

    deduped = deduplicate_by_title_company(fresh)
    removed_dupes = len(fresh) - len(deduped)
    if removed_dupes:
        print(f"  Removed {removed_dupes} reposted duplicates (same title+company)")

    deduped.sort(key=_posted_sort_value, reverse=True)
    print(f"  After freshness filter: {len(deduped)} jobs (from {original_count})")
    return deduped
