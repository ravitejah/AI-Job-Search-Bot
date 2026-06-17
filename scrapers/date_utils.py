"""
Shared date parsing utilities for scraper output.
"""
from datetime import datetime, timedelta


def _clean_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def parse_posted_datetime(text: str) -> datetime | None:
    """Parse ISO or relative job-board time text into a naive datetime."""
    cleaned = _clean_text(text)
    if not cleaned:
        return None

    iso_candidate = cleaned.replace("z", "+00:00")
    if "t" in iso_candidate or (len(cleaned) >= 10 and cleaned[4] == "-" and cleaned[7] == "-"):
        try:
            parsed = datetime.fromisoformat(iso_candidate)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass

    now = datetime.now()

    if any(token in cleaned for token in ("just posted", "just now", "today")):
        return now
    if cleaned in {"now", "new", "active"}:
        return now

    digits = "".join(ch for ch in cleaned if ch.isdigit())
    amount = int(digits or 1)

    if "minute" in cleaned or "min" in cleaned or cleaned.endswith("m"):
        return now - timedelta(minutes=amount)
    if "hour" in cleaned or cleaned.endswith("h"):
        return now - timedelta(hours=amount)
    if "day" in cleaned or cleaned.endswith("d"):
        return now - timedelta(days=amount)
    if "week" in cleaned or cleaned.endswith("w"):
        return now - timedelta(weeks=amount)
    if "month" in cleaned or cleaned.endswith("mo"):
        return now - timedelta(days=amount * 30)

    return None


def scrape_time_text(text: str) -> str:
    """Normalize scraper time text to ISO, or return an empty string if unknown."""
    dt = parse_posted_datetime(text)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else ""
