"""
AI Engine powered by Groq.

Two-layer filtering:
  1. pre_filter_jobs()  — fast deterministic checks (no API, no sleep).
                          Run BEFORE Stage 3 deep fetch so we only spend browser
                          time on genuinely promising candidates.
  2. score_job()        — individual Groq scoring for jobs that cleared pre-filter.
                          Called per-job inside asyncio.to_thread() in main.py so
                          blocking time.sleep() rate-limit waits don't stall the
                          event loop.

filter_jobs() combines both layers for any caller that already has descriptions.
"""
import json
import re
import sys
import time
from functools import lru_cache
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from config import AI, PROFILE, SEARCH


DEFAULT_SENIOR_DUTIES = [
    "lead a team",
    "leading a team",
    "team lead",
    "architecting",
    "architecture owner",
    "mentor junior",
    "mentoring junior",
    "technical leadership",
]

EARLY_CAREER_TITLE_RE = re.compile(
    r"\b(sde\s*1|swe\s*1|software engineer\s*1|sde\s*i|swe\s*i|"
    r"software engineer\s*i|junior|entry[-\s]?level|associate|"
    r"fresher|trainee)\b",
    re.IGNORECASE,
)

# Boilerplate footer patterns to strip before sending to Groq (saves tokens).
_BOILERPLATE_RE = re.compile(
    r"(equal\s+opportunity\s+employer|we\s+are\s+an\s+eoe|"
    r"all\s+qualified\s+applicants|disability.*veteran|affirmative\s+action|"
    r"powered\s+by\s+(lever|greenhouse|workday)|apply\s+now\s+at\s+careers)",
    re.IGNORECASE,
)

# Experience-requirement phrases only — avoids false positives like
# "founded 3 years ago" or "growing 2x year on year".
_EXP_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|to|\u2013)\s*(\d+(?:\.\d+)?)\s*y(?:ea)?rs?\s+"
    r"(?:of\s+)?(?:experience|exp|work)",
    re.IGNORECASE,
)
_EXP_PLUS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*\+\s*y(?:ea)?rs?\s+(?:of\s+)?(?:experience|exp)",
    re.IGNORECASE,
)
_EXP_MIN_RE = re.compile(
    r"(?:minimum|min\.?|at\s+least)(?:\s+of)?\s*(\d+(?:\.\d+)?)\s*y(?:ea)?rs?",
    re.IGNORECASE,
)
_EXP_REQUIRED_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*y(?:ea)?rs?\s+(?:of\s+)?(?:relevant\s+)?(?:experience|exp)"
    r"(?:\s+(?:required|preferred|needed|minimum))?",
    re.IGNORECASE,
)


# ── Groq API ──────────────────────────────────────────────────────────────────

def call_groq(prompt: str, system_prompt: str = None, max_tokens: int = 500) -> str:
    import requests

    api_key = AI.get("groq_api_key", "")
    model = AI.get("match_model") or AI.get("model")

    if not api_key or api_key.startswith("YOUR_"):
        print("  AI skipped: Groq API key is not configured.")
        return ""

    if not model:
        print("  AI skipped: no model configured in AI['match_model'] or AI['model'].")
        return ""

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                },
                timeout=60,
            )

            if resp.status_code == 429:
                err = resp.text
                if "tokens per day" in err or "TPD" in err:
                    print("  AI skipped: Groq daily token limit reached.")
                    return ""
                retry_after = resp.headers.get("retry-after", "")
                try:
                    wait_seconds = int(float(retry_after)) if retry_after else 30
                except (ValueError, TypeError):
                    wait_seconds = 30
                if attempt < 2:
                    print(f"  Groq rate limited. Retrying in {wait_seconds}s...")
                    time.sleep(wait_seconds)
                    continue
                print("  AI skipped: Groq rate limited after 3 attempts.")
                return ""

            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return content.strip() if content else ""

        except Exception as exc:
            if attempt < 2:
                print(f"  Groq request error (attempt {attempt + 1}): {exc}")
                time.sleep(3)
            else:
                print(f"  AI skipped: Groq request failed after 3 attempts.")

    return ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if str(item).strip()]


def _csv(values) -> str:
    items = [item.strip() for item in _as_list(values) if item.strip()]
    return ", ".join(items) if items else "Not specified"


def _haystack(job: dict) -> str:
    return " ".join(
        str(job.get(field, "") or "")
        for field in ("title", "company", "location", "description")
    ).lower()


@lru_cache(maxsize=1)
def _location_terms() -> tuple:
    """
    Build the set of location terms to match against job locations.
    Cached — SEARCH config is constant within a process run, so this
    computes once instead of once-per-job in the filter loop.
    Returns a tuple (hashable, required for lru_cache).
    """
    terms: set = set()
    for raw_location in _as_list(SEARCH.get("locations")):
        loc = raw_location.lower().strip()
        if not loc:
            continue
        terms.add(loc)
        for part in re.split(r"[,/|]", loc):
            part = part.strip()
            if part:
                terms.add(part)

    if "bangalore" in terms or "bengaluru" in terms:
        terms.update({"bangalore", "bengaluru", "karnataka"})
    if "visakhapatnam" in terms or "vizag" in terms:
        terms.update({"visakhapatnam", "vizag", "andhra pradesh"})
    if "hyderabad" in terms:
        terms.add("telangana")
    if "chennai" in terms:
        terms.add("tamil nadu")

    return tuple(sorted(terms))


@lru_cache(maxsize=1)
def _max_experience_years() -> float:
    """
    Return the maximum acceptable experience years from config.
    Cached — avoids recomputing on every job in the filter loop.
    """
    configured = SEARCH.get("max_required_experience_years")
    if configured is not None:
        return float(configured)
    return float(PROFILE.get("experience_years", 0) or 0) + 0.5


def _matches_location(job: dict) -> bool:
    loc = (job.get("location") or "").lower().strip()
    if not loc:
        return bool(SEARCH.get("allow_unknown_locations", False))

    terms = _location_terms()
    if not terms:
        return True

    return any(term in loc for term in terms)


def _matches_required_keywords(job: dict, haystack: str = None) -> bool:
    required = [
        term.lower().strip()
        for term in _as_list(SEARCH.get("keywords_required"))
        if term.strip()
    ]
    if not required:
        return True

    text = haystack if haystack is not None else _haystack(job)
    if any(term in text for term in required):
        return True

    # Keep early-career SDE/SWE roles when description is thin — AI decides.
    return bool(EARLY_CAREER_TITLE_RE.search(job.get("title", "")))


def _is_blacklisted_company(job: dict) -> bool:
    company = (job.get("company") or "").lower()
    return any(
        name.lower() in company for name in _as_list(SEARCH.get("blacklist_companies"))
    )


def _extract_min_experience_years(text: str) -> list:
    """
    Extract the minimum required experience years from text.
    Uses tightened patterns that anchor to experience-requirement phrasing
    to avoid false positives (e.g. "founded 5 years ago", "growing 2x yearly").
    """
    numbers: list = []

    for m in _EXP_RANGE_RE.finditer(text):
        numbers.append(float(m.group(1)))  # minimum of range

    for m in _EXP_PLUS_RE.finditer(text):
        numbers.append(float(m.group(1)))

    for m in _EXP_MIN_RE.finditer(text):
        numbers.append(float(m.group(1)))

    for m in _EXP_REQUIRED_RE.finditer(text):
        # Avoid double-counting ranges already captured above.
        val = float(m.group(1))
        # Only add if not preceded by a range dash/hyphen (those are captured by _EXP_RANGE_RE).
        start = m.start()
        preceding = text[max(0, start - 5):start]
        if not re.search(r"[-–]\s*$", preceding):
            numbers.append(val)

    return numbers


def is_experience_violation(text: str, max_years: float = None) -> bool:
    """Return True when the posting asks for more experience than allowed."""
    if not text:
        return False
    limit = _max_experience_years() if max_years is None else max_years
    return any(years >= limit for years in _extract_min_experience_years(text.lower()))


def _clean_description(text: str, max_chars: int = 3000) -> str:
    """
    Strip boilerplate footer lines and collapse excess blank lines before
    sending to Groq. Reduces token usage and improves signal quality.
    """
    if not text:
        return ""
    lines = text.split("\n")
    cleaned_lines = [line for line in lines if not _BOILERPLATE_RE.search(line)]
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    return cleaned[:max_chars]


# ── Deterministic pre-filter (no API calls) ───────────────────────────────────

def _check_deterministic(
    job: dict,
    excluded_pattern,   # re.Pattern | None — pre-compiled once by caller
    senior_duties: list,
    max_years: float,
) -> tuple:
    """
    Returns (True, "") if the job passes all deterministic checks.
    Returns (False, reason) if it should be dropped.
    Safe to call on metadata-only records (description may be empty string).
    """
    title = str(job.get("title") or "")
    title_lower = title.lower()
    combined_lower = _haystack(job)
    desc_lower = (job.get("description") or "").lower()

    if not title:
        return False, "Job has no title"

    if _is_blacklisted_company(job):
        return False, "Company is blacklisted"

    if not _matches_location(job):
        return False, "Location not in preferred list"

    if excluded_pattern:
        m = excluded_pattern.search(title_lower)
        if m:
            return False, f"Excluded keyword '{m.group()}' in title"

    if re.search(r"\b(ii|iii|iv|2|3|4|5)$", title_lower):
        return False, "Seniority level suffix in title"

    if not _matches_required_keywords(job, haystack=combined_lower):
        return False, "Required keyword not found in title/description"

    # Experience check is only meaningful when description text is present.
    if desc_lower and is_experience_violation(desc_lower, max_years=max_years):
        return False, f"JD requires {max_years:g}+ years experience"

    for duty in senior_duties:
        if duty in combined_lower:
            return False, f"Senior duty detected: '{duty}'"

    return True, ""


def pre_filter_jobs(jobs: list) -> list:
    """
    Stage 2c: Fast deterministic title/keyword filter.
    Run BEFORE deep fetch — operates on metadata-only records (no descriptions).
    Drops clearly ineligible jobs so we never waste browser time on them.
    """
    excluded_words = [
        w.lower().strip() for w in SEARCH.get("keywords_excluded", []) if str(w).strip()
    ]
    # Pre-compile one alternation pattern — O(1) compile instead of O(words) per job.
    excluded_pattern = (
        re.compile(
            r"\b(?:" + "|".join(re.escape(w) for w in excluded_words) + r")\b",
            re.IGNORECASE,
        )
        if excluded_words else None
    )
    senior_duties = [
        d.lower().strip()
        for d in SEARCH.get("senior_duties", DEFAULT_SENIOR_DUTIES)
        if str(d).strip()
    ]
    max_years = _max_experience_years()

    passed = []
    for job in jobs:
        ok, reason = _check_deterministic(job, excluded_pattern, senior_duties, max_years)
        if ok:
            passed.append(job)
        else:
            title = job.get("title", "?")
            company = job.get("company", "?")
            print(f"  Pre-filter drop ({reason}): {title} @ {company}")

    return passed


# ── AI scoring ────────────────────────────────────────────────────────────────

def score_job(job: dict) -> dict:
    """
    Score a single job via Groq.
    Called inside asyncio.to_thread() in main.py so the blocking time.sleep()
    rate-limit waits do NOT stall the async event loop.
    """
    skills = _csv(PROFILE.get("skills"))
    roles = _csv(SEARCH.get("roles"))
    locations = _csv(SEARCH.get("locations"))
    required = _csv(SEARCH.get("keywords_required"))
    max_years = _max_experience_years()
    description = _clean_description(job.get("description") or "")

    title = str(job.get("title") or "Unknown")
    company = str(job.get("company") or "Unknown")
    location = str(job.get("location") or "Not available")

    prompt = f"""You are a strict recruiter filtering job postings for one specific candidate.

CANDIDATE PROFILE:
Name: {PROFILE.get('name', 'Candidate')}
Skills: {skills}
Experience: {PROFILE.get('experience_years', 'Not specified')} years total
Summary: {PROFILE.get('summary', 'Not specified')}

SEARCH TARGET:
Desired roles: {roles}
Desired locations: {locations}
Required skills / keywords: {required}
Maximum acceptable required experience: strictly under {max_years:g} years

JOB POSTING:
Title: {title}
Company: {company}
Location: {location}
Description:
{description if description else '(Not available — judge on title and company only)'}

Respond with ONLY a valid JSON object. No markdown fences. No text outside the JSON.
{{
  "score": <integer 0-100>,
  "reason": "<one concise sentence>",
  "recommendation": "apply" | "skip"
}}

HARD RULES (non-negotiable):
1. Required experience >= {max_years:g} years anywhere in description → score 0, recommendation "skip".
2. Senior duties (team lead, architect, mentoring juniors, etc.) → score 0, recommendation "skip".
3. Required skills completely absent from both title and description → score 0, recommendation "skip".
4. Score >= {SEARCH.get('min_match_score', 75)} only for clear matches on role, skills, location, and experience level.
5. If description is unavailable, score conservatively from title and company alone.
"""
    result_text = call_groq(
        prompt,
        system_prompt="You are a JSON-only response bot. Output valid JSON and nothing else.",
        max_tokens=300,
    )
    # Respect configured delay between Groq calls (rate-limit compliance).
    time.sleep(float(SEARCH.get("score_delay_seconds", 1.0)))

    # Parse the response robustly.
    try:
        cleaned = re.sub(r"```json|```", "", result_text).strip()
        result = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", result_text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except Exception:
                result = {"score": 0, "reason": "Could not parse AI response.", "recommendation": "skip"}
        else:
            result = {"score": 0, "reason": "No AI response.", "recommendation": "skip"}

    # Normalise output fields.
    try:
        result["score"] = max(0, min(100, int(result.get("score", 0))))
    except (TypeError, ValueError):
        result["score"] = 0
    result["reason"] = str(result.get("reason", ""))[:500]  # cap length
    result["recommendation"] = str(result.get("recommendation", "skip")).lower().strip()
    if result["recommendation"] not in ("apply", "skip", "review"):
        result["recommendation"] = "skip"

    return result


# ── Combined convenience wrapper ──────────────────────────────────────────────

def filter_jobs(jobs: list) -> list:
    """
    Convenience wrapper: deterministic pre-filter + Groq scoring in one call.
    Used when descriptions are already attached to job records.
    The main pipeline in main.py calls pre_filter_jobs and score_job separately
    to enable incremental DB saves between each scored job.
    """
    excluded_words = [
        w.lower().strip() for w in SEARCH.get("keywords_excluded", []) if str(w).strip()
    ]
    excluded_pattern = (
        re.compile(
            r"\b(?:" + "|".join(re.escape(w) for w in excluded_words) + r")\b",
            re.IGNORECASE,
        )
        if excluded_words else None
    )
    senior_duties = [
        d.lower().strip()
        for d in SEARCH.get("senior_duties", DEFAULT_SENIOR_DUTIES)
        if str(d).strip()
    ]
    max_years = _max_experience_years()
    min_score = SEARCH.get("min_match_score", 75)
    scored = []

    for job in jobs:
        ok, reason = _check_deterministic(job, excluded_pattern, senior_duties, max_years)
        if not ok:
            job.update(
                match_score=0,
                match_reason=reason,
                recommendation="skip",
                status="rejected",
            )
            print(f"  Dropped ({reason}): {job.get('title')} @ {job.get('company')}")
            continue

        print(f"  Scoring: {job.get('title')} @ {job.get('company')}...")
        result = score_job(job)
        job.update(
            match_score=result.get("score", 0),
            match_reason=result.get("reason", ""),
            recommendation=result.get("recommendation", "skip"),
            status="qualified" if result.get("score", 0) >= min_score else "rejected",
        )
        if job["match_score"] >= min_score:
            scored.append(job)

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored


def preload_common_answers() -> None:
    """Stub — reserved for future Q&A preloading."""
