"""
Copy this file to config.py and fill in your details.
Do not commit config.py; it contains credentials and personal data.
"""

PROFILE = {
    "name": "Your Full Name",
    "email": "your@email.com",
    "phone": "",
    "linkedin": "https://www.linkedin.com/in/yourprofile/",
    "github": "https://github.com/yourusername",
    "location": "Hyderabad, Telangana",
    "resume_path": "data/resume.pdf",
    "skills": [
        "Java",
        "Spring Boot",
        "Angular",
        "PostgreSQL",
        "REST API",
        "Microservices",
        "Full Stack Development",
    ],
    "education": "Your degree and graduation year",
    "experience_years": 1.5,
    "summary": "Short profile summary used by the Groq matcher.",
}

SEARCH = {
    "enabled_sources": ["LinkedIn", "Glassdoor"],
    "roles": [
        "SDE 1",
        "SWE 1",
        "Java Backend Developer",
        "Java Full Stack Developer",
        "Junior Java Developer",
        "Java Software Engineer",
    ],
    "locations": [
        "Hyderabad, Telangana",
        "Chennai, Tamil Nadu",
        "Visakhapatnam, Andhra Pradesh",
        "Bengaluru, Karnataka",
        "Remote",
    ],
    "glassdoor_location": "India",
    "job_types": ["Full-time"],
    "experience_levels": ["Entry level", "Associate"],
    "min_match_score": 85,
    "auto_apply_min_score": 0,
    "blacklist_companies": [],
    "keywords_required": ["java"],
    "keywords_excluded": [
        "intern",
        "internship",
        "contract",
        "freelance",
        "senior",
        "sr",
        "lead",
        "architect",
        "manager",
        "principal",
        "staff",
    ],
    "senior_duties": [
        "lead a team",
        "leading a team",
        "team lead",
        "architecting",
        "architecture owner",
        "mentor junior",
        "mentoring junior",
        "technical leadership",
    ],
    "max_required_experience_years": 2,
    "freshness_hours": 24,
    "include_unknown_dates": True,
    "allow_unknown_locations": False,
    "max_jobs_per_search": 25,
    "score_delay_seconds": 1.0,
}

SCHEDULER = {
    "check_interval_minutes": 60,
    "max_applies_per_day": 0,
    "apply_delay_seconds": 45,
}

NOTIFICATIONS = {
    "email_enabled": True,
    "email_sender": "your@gmail.com",
    "email_password": "YOUR_GMAIL_APP_PASSWORD",
    "email_recipient": "your@gmail.com",
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_chat_id": "",
}

AI = {
    "groq_api_key": "YOUR_GROQ_API_KEY",
    "model": "llama-3.1-8b-instant",
    "match_model": "llama-3.1-8b-instant",
}

DATABASE = {
    "path": "data/jobs.db",
}
