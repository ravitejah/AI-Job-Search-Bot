"""
AI Engine — powered by Groq (Free tier)
- Deep Scans for Java / Experience / Senior Duties
- Scores jobs against profile
"""
import json
import re
import sys
import time
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import AI, PROFILE, SEARCH
from data.database import find_answer, save_answer, get_all_qa


def call_groq(prompt: str, system_prompt: str = None, max_tokens: int = 500) -> str:
    import requests
    api_key = AI["groq_api_key"]
    model   = AI["model"]

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
                timeout=60
            )
            if resp.status_code == 429:
                err = resp.text
                if "tokens per day" in err or "TPD" in err:
                    return ""
                else:
                    time.sleep(30)
                    continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt < 2:
                time.sleep(3)
            else:
                return ""


def score_job(job: dict) -> dict:
    prompt = f"""You are a ruthless, highly strict recruiter filtering jobs for a candidate.

CANDIDATE PROFILE:
Name: {PROFILE['name']}
Skills: Java, Spring Boot, Angular, Retool, PostgreSQL
Experience: Strictly 1.5 Years (Entry Level / SDE 1)

JOB POSTING:
Title: {job['title']}
Company: {job['company']}
Description: {job.get('description', 'Not available')[:2000]}

Respond ONLY with a valid JSON object. No markdown.
{{
  "score": <integer 0-100>,
  "reason": "<1 sentence explanation>",
  "recommendation": "apply" or "skip"
}}

STRICT RULES:
1. FATAL REQUIREMENT CHECK: Read the description requirements deeply. If it asks for any senior duties (leading a team, architecting systems from scratch, mentoring), SCORE 0 and skip.
2. TECH STACK: Unless the title is "SDE 1" or "SWE 1", if the primary tech stack in the description isn't Java or Spring Boot, SCORE 0 and skip. 
3. If it perfectly fits an entry-level Java or SDE 1 role, score 85-95.
"""
    system = "Respond with JSON only."
    text = call_groq(prompt, system_prompt=system, max_tokens=300)
    time.sleep(2)

    try:
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        return {"score": 0, "reason": "Could not evaluate.", "recommendation": "skip"}


def is_experience_violation(text: str) -> bool:
    """Mathematical regex to strictly kill >2 years minimums."""
    text = text.lower()
    
    # 1. Catches ranges like "2-4 years", "3 to 5 yrs". Kills if minimum >= 2.
    ranges = re.findall(r'(\d+)\s*(?:-|to|–)\s*(\d+)\s*y(?:ea)?rs?', text)
    for min_str, max_str in ranges:
        if int(min_str) >= 2: 
            return True
            
    # 2. Catches "3+ years", "4+ yrs". Kills if >= 3. 
    pluses = re.findall(r'(\d+)\s*\+\s*y(?:ea)?rs?', text)
    for val_str in pluses:
        if int(val_str) >= 3:
            return True
            
    # 3. Catches "minimum 2 years", "at least 3 yrs". Kills if >= 2.
    mins = re.findall(r'(?:minimum|at least)(?:\s*of)?\s*(\d+)\s*y(?:ea)?rs?', text)
    for val_str in mins:
        if int(val_str) >= 2:
            return True
            
    return False


def filter_jobs(jobs: list[dict]) -> list[dict]:
    scored = []
    excluded_words = [w.lower() for w in SEARCH.get("keywords_excluded", [])]
    allowed_locations = ["hyderabad", "chennai", "visakhapatnam", "bengaluru", "bangalore", "remote"]
    senior_duties = ['lead a team', 'leading a team', 'architecting', 'mentor junior', 'mentoring junior', 'from scratch']

    for job in jobs:
        title_lower = job['title'].lower()
        desc_lower = job.get('description', '').lower()
        loc_lower = job.get('location', '').lower()
        
        is_excluded = False
        drop_reason = ""

        # 1. Location Check
        if not any(al in loc_lower for al in allowed_locations):
            is_excluded, drop_reason = True, "Location not in preferred list"

        # 2. Title Keyword & Seniority Check
        if not is_excluded:
            for word in excluded_words:
                if re.search(r'\b' + re.escape(word) + r'\b', title_lower):
                    is_excluded, drop_reason = True, f"Keyword '{word}' in title"
                    break
            
            if not is_excluded and re.search(r'\b(ii|iii|iv|2|3|4|5)$', title_lower):
                is_excluded, drop_reason = True, "Seniority level in title"

        # 3. Mathematical Experience Bouncer (Reads full JD)
        if not is_excluded and is_experience_violation(desc_lower):
            is_excluded, drop_reason = True, "JD strictly requires >2 years experience"

        # 4. SDE-1 Bypass OR Java Requirement Check
        if not is_excluded:
            is_sde1 = bool(re.search(r'\b(sde\s*1|swe\s*1|software engineer\s*1|sde\s*i|swe\s*i|software engineer\s*i)\b', title_lower))
            if not is_sde1 and 'java' not in desc_lower and 'java' not in title_lower:
                is_excluded, drop_reason = True, "Java not mentioned in non-SDE role"

        # 5. Senior Duties Scan
        if not is_excluded:
            for duty in senior_duties:
                if duty in desc_lower:
                    is_excluded, drop_reason = True, f"Senior duty detected: '{duty}'"
                    break

        if is_excluded:
            print(f"  🚫 Dropped ({drop_reason}): {job['title']} @ {job['company']}")
            continue

        print(f"  🤖 Scoring: {job['title']} @ {job['company']}...")
        result = score_job(job)
        job["match_score"]   = result.get("score", 0)
        job["match_reason"]  = result.get("reason", "")
        job["recommendation"] = result.get("recommendation", "review")
        
        if job["match_score"] >= SEARCH["min_match_score"]:
            scored.append(job)
            
    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored


# ── APPLICATION Q&A (Kept standard logic) ─────
def answer_question(question: str, job_context: dict = None) -> str:
    return ""

def preload_common_answers():
    pass