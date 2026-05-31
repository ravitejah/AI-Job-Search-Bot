#  Job Search Bot — AI-Powered Job Search Automation

<div align="center">

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Playwright](https://img.shields.io/badge/Playwright-Scraping-45ba4b?style=for-the-badge&logo=playwright&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-AI_Scoring-F55036?style=for-the-badge)
![GitHub Actions](https://img.shields.io/badge/GitHub_Actions-Automated-2088FF?style=for-the-badge&logo=github-actions&logoColor=white)

**Scrapes LinkedIn, Indeed, Glassdoor and more — scores every listing against your resume using AI — then emails you only the jobs worth applying to.**

*Tuned for the Indian job market: Hyderabad · Remote · Bangalore · Chennai · Visakhapatnam*

</div>

---

## What it does

1. Scrapes job listings across multiple platforms on a schedule
2. Deduplicates against a local SQLite database so you never see the same job twice
3. Scores each listing against your resume using Groq AI and filters out weak matches
4. Sends a single grouped email — organised by platform → city → freshness — with direct apply links

---

## Project structure

```
ai-job-hunter/
├── .github/workflows/
│   └── job_hunter.yml        # GitHub Actions schedule
├── ai_engine/
│   └── matcher.py            # Groq AI scoring
├── dashboard/
│   └── cli.py                # Local CLI dashboard
├── data/
│   ├── database.py           # SQLite deduplication
│   └── resume.pdf            # Your resume (not committed)
├── notifier/
│   └── notifications.py      # HTML email alerts
├── scrapers/                 # One file per platform
│   ├── linkedin_scraper.py
│   ├── indeed_scraper.py
│   ├── glassdoor_scraper.py
│   ├── dice_scraper.py
│   ├── handshake_scraper.py
│   └── jobright_scraper.py
├── config.example.py
├── config.py                 # Your config (not committed)
├── main.py
└── requirements.txt
```

---

## Prerequisites

- Python 3.10+
- Git
- A Gmail account with an [App Password](https://myaccount.google.com/apppasswords)
- A free [Groq API key](https://console.groq.com)

---

## Setup

### 1 — Clone and install

```bash
git clone https://github.com/ravitejah/ai-job-hunter.git
cd ai-job-hunter
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2 — Configure

```bash
cp config.example.py config.py    # Windows: copy config.example.py config.py
```

Open `config.py` and fill in your name, skills, target roles, locations, email credentials, and Groq API key.

### 3 — Add your resume

Place your resume at:

```
data/resume.pdf
```

The AI scorer reads this file when evaluating each job.

### 4 — Initialise the database

```bash
python data/database.py
```

---

## Running locally

| Command | What it does |
|---|---|
| `python main.py --once --no-apply` | One full scrape → score → email cycle |
| `python main.py --schedule --no-apply` | Runs on a recurring schedule |
| `python dashboard/cli.py` | Opens the local CLI dashboard |
| `python ai_engine/matcher.py` | Tests the AI scoring engine in isolation |

---

## GitHub Actions (free cloud automation)

### 1 — Push to GitHub

```bash
git init && git add .
git commit -m "initial commit"
git branch -M main
git remote add origin YOUR_REPO_URL
git push -u origin main
```

### 2 — Add secrets

Go to **Repository → Settings → Secrets and Variables → Actions** and add:

| Secret | Value |
|---|---|
| `GROQ_API_KEY` | Your Groq API key |
| `EMAIL_SENDER` | Your Gmail address |
| `EMAIL_PASSWORD` | Your Gmail App Password |
| `EMAIL_RECIPIENT` | Where to send alerts |

### 3 — Trigger

Go to **Actions → Job Hunter Bot → Run workflow** for the first manual run. The schedule takes over from there.

---

## Keeping secrets safe

Never commit `config.py`, `resume.pdf`, or `jobs.db`. They are already listed in `.gitignore`. Store all credentials in GitHub Secrets or environment variables — never hardcoded in source.

---

## Troubleshooting

**Playwright errors** → `playwright install chromium`

**Emails not arriving** → Check your Gmail App Password, confirm the recipient address, and look in spam.

**GitHub Action failing** → Open Actions → the failed run → Logs. Usually a missing secret or a permissions issue on the repo.

---

## How the email is organised

Each alert groups jobs by platform, then by city in this fixed order:

```
Hyderabad → Remote → Bangalore → Chennai → Visakhapatnam → Other
```

Within each city, the freshest listings appear first. Match scores are colour-coded: green ≥ 85 · amber ≥ 70 · red below 70.

---

## Contributors

### Original author

**Sri Krishna Sai Kota** — designed and built the core pipeline

- GitHub: [KRISHNA-05-06](https://github.com/KRISHNA-05-06)
- LinkedIn: [srikrishnasai](https://www.linkedin.com/in/srikrishnasai/)
- Repo: [KRISHNA-05-06/ai-job-hunter](https://github.com/KRISHNA-05-06/ai-job-hunter)

---

### Fork maintainer — Indian edition

**Raviteja Ramisetti** — strict 0–2.5 year experience bouncer, city-grouped email layout, responsive HTML email redesign, and Indian market tuning (Hyderabad · Remote · Bengaluru · Chennai · Visakhapatnam).

- GitHub: [ravitejah](https://github.com/ravitejah)
- LinkedIn: [ravitejarin](https://www.linkedin.com/in/ravitejarin/)
- Location: Hyderabad, Telangana

---

## License

MIT — free to use, modify, and adapt for your own job search.