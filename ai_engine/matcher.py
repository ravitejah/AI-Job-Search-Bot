"""
AI Engine powered by Groq.

The matcher runs cheap deterministic filters first, then spends Groq calls only
on jobs that are plausible matches.
"""
import json
import re
import sys
import time
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
    "from scratch",
    "technical leadership",
]

EARLY_CAREER_TITLE_RE = re.compile(
    r"\b(sde\s*1|swe\s*1|software engineer\s*1|sde\s*i|swe\s*i|"
    r"software engineer\s*i|junior|entry[-\s]?level|associate)\b",
    re.IGNORECASE,
)


def call_groq(prompt: str, system_prompt: str = None, max_tokens: int = 500) -> str:
    import requests

    api_key = AI.get("groq_api_key", "")
    model = AI.get("match_model") or AI.get("model")

    if not api_key or api_key.startswith("YOUR_"):
        print("  AI skipped: Groq API key is not configured.")
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
                retry_after = resp.headers.get("retry-after")
                wait_seconds = int(retry_after) if retry_after and retry_after.isdigit() else 30
                print(f"  Groq rate limited. Retrying in {wait_seconds}s...")
                time.sleep(wait_seconds)
                continue

            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception:
            if attempt < 2:
                time.sleep(3)
            else:
                print("  AI skipped: Groq request failed after retries.")
                return ""


def _as_list(value) -> list[str]:
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


def _location_terms() -> list[str]:
    terms = set()
    for raw_location in _as_list(SEARCH.get("locations")):
        loc = raw_location.lower().strip()
        if not loc:
            continue
        terms.add(loc)
        for part in re.split(r"[,/|]", loc):
            part = part.strip()
            if part:
                terms.add(part)

    # Common Indian city aliases used by job boards.
    if "bangalore" in terms or "bengaluru" in terms:
        terms.update({"bangalore", "bengaluru", "karnataka"})
    if "visakhapatnam" in terms or "vizag" in terms:
        terms.update({"visakhapatnam", "vizag", "andhra pradesh"})
    if "hyderabad" in terms:
        terms.add("telangana")
    if "chennai" in terms:
        terms.add("tamil nadu")

    return sorted(terms)


def _matches_location(job: dict) -> bool:
    loc = (job.get("location") or "").lower().strip()
    if not loc:
        return bool(SEARCH.get("allow_unknown_locations", False))

    terms = _location_terms()
    if not terms:
        return True

    return any(term in loc for term in terms)


def _matches_required_keywords(job: dict) -> bool:
    required = [term.lower().strip() for term in _as_list(SEARCH.get("keywords_required"))]
    required = [term for term in required if term]
    if not required:
        return True

    text = _haystack(job)
    if any(term in text for term in required):
        return True

    # Keep early-career SDE/SWE roles for AI review when the page description is thin.
    return bool(EARLY_CAREER_TITLE_RE.search(job.get("title", "")))


def _is_blacklisted_company(job: dict) -> bool:
    company = (job.get("company") or "").lower()
    return any(name.lower() in company for name in _as_list(SEARCH.get("blacklist_companies")))


def _max_experience_years() -> float:
    configured = SEARCH.get("max_required_experience_years")
    if configured is not None:
        return float(configured)
    return float(PROFILE.get("experience_years", 0) or 0) + 0.5


def _extract_min_experience_years(text: str) -> list[float]:
    text = text.lower()
    numbers: list[float] = []

    for min_str, _ in re.findall(
        r"(\d+(?:\.\d+)?)\s*(?:-|to|\u2013)\s*(\d+(?:\.\d+)?)\s*y(?:ea)?rs?",
        text,
    ):
        numbers.append(float(min_str))

    for val_str in re.findall(r"(\d+(?:\.\d+)?)\s*\+\s*y(?:ea)?rs?", text):
        numbers.append(float(val_str))

    for val_str in re.findall(
        r"(?:minimum|min\.?|at least)(?:\s*of)?\s*(\d+(?:\.\d+)?)\s*y(?:ea)?rs?",
        text,
    ):
        numbers.append(float(val_str))

    for val_str in re.findall(
        r"(\d+(?:\.\d+)?)\s*y(?:ea)?rs?\s+(?:of\s+)?(?:experience|exp)",
        text,
    ):
        numbers.append(float(val_str))

    return numbers


def score_job(job: dict) -> dict:
    skills = _csv(PROFILE.get("skills"))
    roles = _csv(SEARCH.get("roles"))
    locations = _csv(SEARCH.get("locations"))
    required = _csv(SEARCH.get("keywords_required"))
    excluded = _csv(SEARCH.get("keywords_excluded"))
    max_years = _max_experience_years()

    prompt = f"""You are a ruthless, highly strict recruiter filtering jobs for a candidate.

CANDIDATE PROFILE:
Name: {PROFILE.get('name', 'Candidate')}
Skills: {skills}
Experience: {PROFILE.get('experience_years', 'Not specified')} years
Summary: {PROFILE.get('summary', 'Not specified')}

SEARCH TARGET:
Roles: {roles}
Locations: {locations}
Required keywords: {required}
Excluded keywords: {excluded}
Maximum acceptable required experience: strictly below {max_years:g} years unless the listing explicitly says entry-level.
Minimum score to recommend: {SEARCH.get('min_match_score', 0)}

JOB POSTING:
Title: {job['title']}
Company: {job['company']}
Location: {job.get('location', 'Not available')}
Description: {job.get('description', 'Not available')[:2000]}

Respond ONLY with a valid JSON object. No markdown.
{{
  "score": <integer 0-100>,
  "reason": "<1 sentence explanation>",
  "recommendation": "apply" or "skip"
}}

STRICT RULES:
1. If the job asks for senior duties, team leadership, architecture ownership, or mentoring, SCORE 0 and skip.
2. If the required experience is {max_years:g}+ years or higher, SCORE 0 and skip.
3. If required keywords or candidate skills are absent from both title and description, SCORE 0 and skip.
4. Give high scores only to jobs that clearly fit the target roles, skills, locations, and experience level.
"""
    system = "Respond with JSON only."
    text = call_groq(prompt, system_prompt=system, max_tokens=300)
    time.sleep(float(SEARCH.get("score_delay_seconds", 1.0)))

    try:
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
            except Exception:
                result = {"score": 0, "reason": "Could not evaluate.", "recommendation": "skip"}
        else:
            result = {"score": 0, "reason": "Could not evaluate.", "recommendation": "skip"}

    try:
        result["score"] = max(0, min(100, int(result.get("score", 0))))
    except (TypeError, ValueError):
        result["score"] = 0
    result["reason"] = str(result.get("reason", ""))
    result["recommendation"] = str(result.get("recommendation", "review")).lower()
    return result


def is_experience_violation(text: str, max_years: float | None = None) -> bool:
    """Return True when the posting asks for too much required experience."""
    if not text:
        return False
    limit = _max_experience_years() if max_years is None else max_years
    return any(years >= limit for years in _extract_min_experience_years(text))


def filter_jobs(jobs: list[dict]) -> list[dict]:
    scored = []
    excluded_words = [w.lower().strip() for w in SEARCH.get("keywords_excluded", []) if str(w).strip()]
    senior_duties = [d.lower().strip() for d in SEARCH.get("senior_duties", DEFAULT_SENIOR_DUTIES) if str(d).strip()]
    max_years = _max_experience_years()

    for job in jobs:
        title_lower = job["title"].lower()
        desc_lower = job.get("description", "").lower()
        combined_lower = _haystack(job)

        is_excluded = False
        drop_reason = ""

        if _is_blacklisted_company(job):
            is_excluded, drop_reason = True, "Company is blacklisted"

        if not is_excluded and not _matches_location(job):
            is_excluded, drop_reason = True, "Location not in preferred list"

        if not is_excluded:
            for word in excluded_words:
                if re.search(r"\b" + re.escape(word) + r"\b", title_lower):
                    is_excluded, drop_reason = True, f"Keyword '{word}' in title"
                    break

            if not is_excluded and re.search(r"\b(ii|iii|iv|2|3|4|5)$", title_lower):
                is_excluded, drop_reason = True, "Seniority level in title"

        if not is_excluded and not _matches_required_keywords(job):
            is_excluded, drop_reason = True, "Required keyword not found"

        if not is_excluded and is_experience_violation(desc_lower, max_years=max_years):
            is_excluded, drop_reason = True, f"JD requires {max_years:g}+ years experience"

        if not is_excluded:
            for duty in senior_duties:
                if duty in combined_lower:
                    is_excluded, drop_reason = True, f"Senior duty detected: '{duty}'"
                    break

        if is_excluded:
            job["match_score"] = 0
            job["match_reason"] = drop_reason
            job["recommendation"] = "skip"
            job["status"] = "rejected"
            print(f"  Dropped ({drop_reason}): {job['title']} @ {job['company']}")
            continue

        print(f"  Scoring: {job['title']} @ {job['company']}...")
        result = score_job(job)
        job["match_score"] = result.get("score", 0)
        job["match_reason"] = result.get("reason", "")
        job["recommendation"] = result.get("recommendation", "review")
        job["status"] = "qualified" if job["match_score"] >= SEARCH["min_match_score"] else "rejected"

        if job["match_score"] >= SEARCH["min_match_score"]:
            scored.append(job)

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored


def answer_question(question: str, job_context: dict = None) -> str:
    return ""


def preload_common_answers():
    pass
