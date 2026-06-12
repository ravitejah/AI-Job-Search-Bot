"""
Shared Playwright browser utilities used by all scraper modules.

Centralising these prevents linkedin_scraper.py and glassdoor_scraper.py from
diverging silently when retry logic, user-agent strings, or URL-validation
rules need to change.
"""
import asyncio
import random

_USER_AGENTS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
    ),
]


def random_ua() -> str:
    """Return a random realistic Chrome/Edge user-agent string."""
    return random.choice(_USER_AGENTS)


def is_valid_url(url: str) -> bool:
    """Only navigate to plain HTTPS URLs — block javascript:, data:, relative paths, etc."""
    return bool(url) and url.startswith("https://")


async def goto_with_retry(page, url: str, max_attempts: int = 2) -> bool:
    """
    Navigate to a URL, retrying once on timeout or crash.
    Returns True on success, False if all attempts fail.
    """
    for attempt in range(max_attempts):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return True
        except Exception as e:
            if attempt < max_attempts - 1:
                print(f"    Page load failed (attempt {attempt + 1}), retrying: {e}")
                await asyncio.sleep(2)
            else:
                print(f"    Page load failed after {max_attempts} attempts: {e}")
    return False
