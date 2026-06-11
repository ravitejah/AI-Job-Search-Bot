# Job Search Bot

AI-assisted job search automation for LinkedIn and Glassdoor.

The bot scrapes fresh listings, removes duplicates, filters out poor matches with deterministic rules, scores plausible jobs with Groq, stores the result in SQLite, and emails the strongest matches.

## What It Does

1. Scrapes LinkedIn and Glassdoor.
2. Keeps only recent jobs, with configurable handling for unknown posting dates.
3. Deduplicates by job ID and title/company.
4. Saves every new listing as `seen`, then updates reviewed jobs to `qualified` or `rejected`.
5. Uses config-driven rules before Groq scoring to reduce wasted API calls.
6. Sends one grouped HTML email for qualifying jobs.

## Project Structure

```text
ai-job-search-bot/
├── .github/workflows/job_hunter.yml
├── ai_engine/
│   └── matcher.py
├── data/
│   └── database.py
├── notifier/
│   └── notifications.py
├── scrapers/
│   ├── date_utils.py
│   ├── glassdoor_scraper.py
│   ├── linkedin_scraper.py
│   └── recency_filter.py
├── config.example.py
├── main.py
└── requirements.txt
```

## Setup

Requirements:

- Python 3.10+
- A Groq API key
- A Gmail app password if email notifications are enabled

Install:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy config.example.py config.py
```

Edit `config.py` with your profile, target roles, target locations, Groq key, and email credentials.

Initialize the database:

```bash
python data/database.py
```

Run once:

```bash
python main.py --once
```

Run continuously:

```bash
python main.py --schedule
```

## Configuration Notes

Important `SEARCH` options:

- `enabled_sources`: keep as `["LinkedIn", "Glassdoor"]`.
- `roles`: search terms used by scrapers.
- `locations`: preferred locations used by the matcher.
- `keywords_required`: cheap prefilter before Groq scoring.
- `keywords_excluded`: title words that should be rejected before Groq scoring.
- `max_required_experience_years`: rejects jobs asking for too much experience.
- `freshness_hours`: default `24`.
- `include_unknown_dates`: keeps jobs when a site hides or changes the posted-date selector.
- `max_jobs_per_search`: caps each role/location scrape.
- `score_delay_seconds`: pause between Groq calls.

## GitHub Actions

The workflow runs on schedule and creates `config.py` at runtime.

Required repository secrets:

| Secret | Purpose |
|---|---|
| `GROQ_API_KEY` | Groq scoring |
| `EMAIL_SENDER` | Gmail sender |
| `EMAIL_PASSWORD` | Gmail app password |
| `EMAIL_RECIPIENT` | Alert recipient |

Optional repository secret:

| Secret | Purpose |
|---|---|
| `PROFILE_PHONE` | Personal phone, kept out of the repo |

Optional repository variables:

| Variable | Example |
|---|---|
| `PROFILE_NAME` | `Your Name` |
| `PROFILE_LINKEDIN` | `https://www.linkedin.com/in/yourprofile/` |
| `PROFILE_GITHUB` | `https://github.com/yourusername` |
| `PROFILE_LOCATION` | `Hyderabad, Telangana` |
| `PROFILE_EDUCATION` | `BTech, 2024` |
| `PROFILE_EXPERIENCE_YEARS` | `1.5` |
| `PROFILE_SUMMARY` | `Java full stack developer targeting SDE 1 roles.` |
| `PROFILE_SKILLS` | `Java, Spring Boot, Angular, PostgreSQL` |
| `SEARCH_ROLES` | `SDE 1, Java Backend Developer, Java Full Stack Developer` |
| `SEARCH_LOCATIONS` | `Hyderabad, Chennai, Bengaluru, Remote` |
| `SEARCH_MIN_MATCH_SCORE` | `85` |
| `SEARCH_MAX_EXPERIENCE_YEARS` | `2` |
| `SEARCH_KEYWORDS_REQUIRED` | `java` |
| `SEARCH_KEYWORDS_EXCLUDED` | `senior, lead, manager, architect` |
| `SEARCH_FRESHNESS_HOURS` | `24` |
| `GLASSDOOR_LOCATION` | `India` |

## Safety

Do not commit:

- `config.py`
- `data/resume.pdf`
- `data/jobs.db`
- `.env`
- logs or local virtual environments

These are covered by `.gitignore`.

## Troubleshooting

Playwright browser missing:

```bash
playwright install chromium
```

No jobs found:

- Check scraper logs for `cards`, `missing`, `errors`, `no_date`, and `no_desc`.
- Try fewer roles or broader locations.
- LinkedIn and Glassdoor selectors can change, so zero cards usually means the page layout changed or the request was blocked.

Groq scoring stops:

- The bot prefilters before Groq, but Groq still has rate limits.
- If the daily token limit is reached, the matcher skips scoring instead of crashing.

Email not sent:

- Confirm `email_enabled` is `True`.
- Confirm Gmail app password credentials.
- Check spam.

## License

MIT
