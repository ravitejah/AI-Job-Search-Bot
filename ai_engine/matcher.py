"""
AI Engine — powered by Groq (Free tier)
- Score jobs against your profile
- Generate Q&A answers for applications
- Generate cover letters
"""
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import AI, PROFILE, SEARCH
from data.database import find_answer, save_answer, get_all_qa


# ── GROQ API CALLER ───────────────────────────

def call_groq(prompt: str, system_prompt: str = None, max_tokens: int = 500) -> str:
    import requests
    api_key = AI["groq_api_key"]
    model   = AI["model"]

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    # Added 3-attempt retry loop for network timeouts
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
                    "temperature": 0.3,
                },
                timeout=60
            )
            if resp.status_code == 429:
                err = resp.text
                if "tokens per day" in err or "TPD" in err:
                    print("  ❌ Daily token limit reached.")
                    return ""
                else:
                    print("  ⏳ Rate limit — waiting 30 seconds...")
                    time.sleep(30)
                    continue # Retry after waiting
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt < 2:
                print(f"  ⚠️  Groq call failed ({e}). Retrying...")
                time.sleep(3)
            else:
                print(f"  ❌ Groq call failed after 3 attempts: {e}")
                return ""


# ── JOB MATCHING ──────────────────────────────

def score_job(job: dict) -> dict:
    prompt = f"""You are evaluating a job match for a candidate.

CANDIDATE PROFILE:
Name: {PROFILE['name']}
Role seeking: Java Full Stack Developer / Backend Engineer / SWE 1 / SDE 1
Skills: {', '.join(PROFILE['skills'])}
Experience: {PROFILE['experience_years']} years
Education: {PROFILE['education']}
Location: {PROFILE['location']}

JOB POSTING:
Title: {job['title']}
Company: {job['company']}
Location: {job['location']}
Description: {job.get('description', 'Not available')[:1500]}

Respond ONLY with a valid JSON object. No markdown, no explanation, just raw JSON:
{{
  "score": <integer 0-100>,
  "reason": "<2 sentence explanation>",
  "relevant_skills": ["skill1", "skill2"],
  "recommendation": "apply"
}}

STRICT SCORING RULES:
1. EXPERIENCE LIMIT: If the job description requires 3 or more years of experience, score it 0. Strictly look for 0, 1, 2, or 2.5 years.
2. GENERALIST OVERRIDE: If the title contains "SDE 1", "SWE 1", "Software Engineer 1" AND requires 0-2 years experience, score it 85-95 EVEN IF the tech stack doesn't perfectly match Java.
3. STANDARD MATCH: For all other roles, score 70-100 based on overlap with Java, Spring Boot, Angular.
4. Set recommendation to "apply" if score >= 85.
"""
    system = "You are a precise job-matching evaluator. Always respond with only valid JSON, no extra text."
    text = call_groq(prompt, system_prompt=system, max_tokens=400)
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
        return {"score": 50, "reason": "Could not evaluate.", "relevant_skills": [], "recommendation": "review"}


def filter_jobs(jobs: list[dict]) -> list[dict]:
    scored = []
    excluded_words = [w.lower() for w in SEARCH.get("keywords_excluded", [])]
    
    # Target cities only
    allowed_locations = ["hyderabad", "chennai", "visakhapatnam", "bengaluru", "bangalore", "remote"]

    # Regex patterns looking for 3+ years of experience in the description
    exp_patterns = [
        r'[3-9]\s*(?:-|to|–)\s*\d+\s*years',       # e.g., 3-5 years, 4-6 years
        r'[3-9]\+\s*years',                        # e.g., 3+ years, 5+ years
        r'minimum\s*(?:of\s*)?[3-9]\s*years',      # e.g., minimum of 3 years
        r'[3-9]\s*years\s*(?:of\s*)?experience',   # e.g., 3 years of experience
        r'[1-9][0-9]\s*\+?\s*years'                # e.g., 10+ years
    ]

    for job in jobs:
        title_lower = job['title'].lower()
        desc_lower = job.get('description', '').lower()
        loc_lower = job.get('location', '').lower()
        
        is_excluded = False
        drop_reason = ""

        # 1. Location Bouncer
        if not any(al in loc_lower for al in allowed_locations):
            is_excluded = True
            drop_reason = f"Location '{job.get('location')}' not in preferred list"

        # 2. Title Bouncer (Seniors, Leads, II, III)
        if not is_excluded:
            for word in excluded_words:
                pattern = r'\b' + re.escape(word) + r'\b'
                if re.search(pattern, title_lower):
                    is_excluded = True
                    drop_reason = f"Keyword '{word}' in title"
                    break
            
            if not is_excluded and re.search(r'\b(ii|iii|iv|2|3|4|5)$', title_lower):
                is_excluded = True
                drop_reason = "Seniority Level in title"

        # 3. Experience Bouncer (Reads the JD for 3+ years)
        if not is_excluded:
            for pattern in exp_patterns:
                if re.search(pattern, desc_lower):
                    is_excluded = True
                    drop_reason = "JD requires 3+ years experience"
                    break

        if is_excluded:
            print(f"  🚫 Dropped ({drop_reason}): {job['title']} @ {job['company']}")
            job["match_score"] = 0
            job["match_reason"] = f"Blocked: {drop_reason}"
            job["recommendation"] = "skip"
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


# ── APPLICATION Q&A ───────────────────────────

SYSTEM_PROMPT = f"""You are filling out job applications on behalf of {PROFILE['name']}.
Always answer in first person. Be concise, professional, and specific.

CANDIDATE FACTS:
- Name:       {PROFILE['name']}
- Email:      {PROFILE['email']}
- Phone:      {PROFILE['phone']}
- Location:   {PROFILE['location']}
- Education:  {PROFILE['education']}
- Skills:     {', '.join(PROFILE['skills'])}
- Experience: {PROFILE['experience_years']} years as Jr Software Engineer at Cognizant
- LinkedIn:   {PROFILE['linkedin']}
- GitHub:     {PROFILE['github']}

ANSWER RULES:
- 1-4 sentences max — be direct, no fluff
- Reference real skills (Java, Spring Boot, Angular, Retool) when relevant
- Salary: "I am targeting 10-16 LPA, depending on the scope of the role."
- Work authorization: "Yes, I am authorized to work in India."
- Give ONLY the answer — no intro like "Here is my answer:" or "Sure!"
"""


def answer_question(question: str, job_context: dict = None) -> str:
    cached = find_answer(question)
    if cached: return cached
    
    job_info = ""
    if job_context:
        job_info = f"\nApplying for: {job_context.get('title','')} at {job_context.get('company','')}"

    existing_qa = get_all_qa()[:5]
    qa_examples = ""
    if existing_qa:
        qa_examples = "\nMY PREVIOUS ANSWERS (match this style and tone):\n"
        for qa in existing_qa:
            qa_examples += f"Q: {qa['question_original']}\nA: {qa['your_answer']}\n\n"

    prompt = f"""Answer this job application question for me.{job_info}

{qa_examples}
QUESTION: {question}

Answer only (1-4 sentences, no preamble):"""

    answer = call_groq(prompt, system_prompt=SYSTEM_PROMPT, max_tokens=250)
    time.sleep(2)
    if answer: save_answer(question, answer)
    return answer


def generate_cover_letter(job: dict) -> str:
    return "Cover letters disabled for speed."


# ── COMMON ANSWERS PRE-LOADED ─────────────────

def preload_common_answers():
    common = [
        ("Are you authorized to work in India?",
         "Yes, I am a citizen and fully authorized to work in India."),

        ("What is your desired salary or compensation?",
         "I am targeting a compensation in the range of 10 to 16 LPA, depending on the responsibilities and scope of the role."),

        ("Are you willing to relocate?",
         "I am currently based in Hyderabad. I am open to roles here, as well as in Chennai, Visakhapatnam, Bengaluru, or remote positions."),

        ("How many years of experience do you have with Java?",
         "I have 1.5 years of professional experience using Java, primarily building robust backend services and RESTful APIs with Spring Boot and Spring Data JPA."),

        ("Do you have experience with front-end technologies?",
         "Yes, I have experience building user interfaces and internal dashboards using Angular and low-code platforms like Retool, using JavaScript for custom logic."),

        ("Do you have experience with SQL or databases?",
         "Yes, I have hands-on experience designing and managing relational databases using PostgreSQL and MySQL."),

        ("Tell us about a recent project you worked on.",
         "At Cognizant, I contributed to the 'Giving Tree' internal tool. I built secure REST APIs using Spring Boot and connected them to Retool frontend dashboards to manage the tools and contacts modules."),

        ("What is your highest level of education?",
         "I hold a BTech in Electrical and Electronics Engineering from R.V.R & J.C College of Engineering, graduating in 2024 with a CGPA of 7.98."),

        ("Tell us about yourself.",
         "I'm Raviteja Ramisetti, a Java Full Stack Developer with 1.5 years of experience at Cognizant. I specialize in building end-to-end applications, combining Spring Boot backend services with intuitive Retool and Angular frontends. I'm passionate about creating efficient internal tools and automated workflows."),
    ]

    loaded = 0
    for question, answer in common:
        if not find_answer(question):
            save_answer(question, answer)
            loaded += 1

    print(f"✅ Pre-loaded {loaded} new Q&A answers ({len(common)} total checked).")


if __name__ == "__main__":
    preload_common_answers()