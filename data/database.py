"""
Database Manager
Handles: jobs seen, applications submitted, Q&A memory store
"""
import sqlite3
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).parent.parent))
from config import DATABASE


def get_connection():
    Path(DATABASE["path"]).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE["path"])
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    c = conn.cursor()

    # Jobs discovered
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            job_type TEXT,
            description TEXT,
            url TEXT,
            source TEXT,
            match_score INTEGER,
            match_reason TEXT,
            posted_at TEXT,
            discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'new'
        )
    """)

    # Applications submitted
    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
            method TEXT,
            status TEXT DEFAULT 'applied',
            notes TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
    """)

    # Q&A Memory: stores questions + your answers for reuse
    c.execute("""
        CREATE TABLE IF NOT EXISTS qa_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_normalized TEXT UNIQUE,
            question_original TEXT,
            your_answer TEXT,
            used_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Notification log
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            channel TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized.")


# ── JOBS ──────────────────────────────────────

def job_exists(job_id: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone()
    conn.close()
    return row is not None




def job_exists_by_title_company(title: str, company: str) -> bool:
    """
    Secondary check — if same title+company already in DB, it's a repost.
    Catches cases where URL changed but job is the same.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE LOWER(title)=LOWER(?) AND LOWER(company)=LOWER(?)",
        ((title or "").strip(), (company or "").strip())
    ).fetchone()
    conn.close()
    return row is not None

def save_job_seen(job: dict):
    """
    Save a job as 'seen' so it's never processed again — even if it scored too low.
    This prevents re-scraping and re-scoring the same jobs every run.
    """
    conn = get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO jobs
        (id, title, company, location, job_type, description, url, source,
         match_score, match_reason, posted_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        job["id"], job["title"], job["company"], job.get("location",""),
        job.get("job_type",""), job.get("description",""), job["url"],
        job["source"], job.get("match_score", 0), job.get("match_reason",""),
        job.get("posted_at",""), job.get("status", "seen")
    ))
    conn.commit()
    conn.close()


def bulk_save_seen(jobs: list[dict]):
    """Save a batch of jobs as seen — much faster than individual saves."""
    if not jobs:
        return
    conn = get_connection()
    conn.executemany("""
        INSERT OR IGNORE INTO jobs
        (id, title, company, location, job_type, description, url, source,
         match_score, match_reason, posted_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, [
        (
            job["id"], job["title"], job["company"], job.get("location",""),
            job.get("job_type",""), job.get("description",""), job["url"],
            job["source"], job.get("match_score", 0), job.get("match_reason",""),
            job.get("posted_at",""), job.get("status", "seen")
        )
        for job in jobs
    ])
    conn.commit()
    conn.close()

def save_job(job: dict, status: str = "qualified"):
    """Insert or update a scored job without losing the original discovery time."""
    conn = get_connection()
    conn.execute("""
        INSERT INTO jobs
        (id, title, company, location, job_type, description, url, source,
         match_score, match_reason, posted_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            company=excluded.company,
            location=excluded.location,
            job_type=excluded.job_type,
            description=excluded.description,
            url=excluded.url,
            source=excluded.source,
            match_score=excluded.match_score,
            match_reason=excluded.match_reason,
            posted_at=excluded.posted_at,
            status=excluded.status
    """, (
        job["id"], job["title"], job["company"], job.get("location",""),
        job.get("job_type",""), job.get("description",""), job["url"],
        job["source"], job.get("match_score",0), job.get("match_reason",""),
        job.get("posted_at",""), job.get("status", status)
    ))
    conn.commit()
    conn.close()


def update_job_status(job_id: str, status: str):
    conn = get_connection()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()
    conn.close()


def get_jobs(status=None, limit=50):
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY discovered_at DESC LIMIT ?",
            (status, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats():
    conn = get_connection()
    stats = {
        "total_jobs": conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0],
        "new_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='new'").fetchone()[0],
        "seen_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='seen'").fetchone()[0],
        "qualified_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='qualified'").fetchone()[0],
        "rejected_jobs": conn.execute("SELECT COUNT(*) FROM jobs WHERE status='rejected'").fetchone()[0],
        "applied": conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
        "qa_answers": conn.execute("SELECT COUNT(*) FROM qa_memory").fetchone()[0],
        "today_applied": conn.execute(
            "SELECT COUNT(*) FROM applications WHERE date(applied_at)=date('now')"
        ).fetchone()[0],
    }
    conn.close()
    return stats


# ── APPLICATIONS ──────────────────────────────

def save_application(job_id: str, method: str, notes: str = ""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO applications (job_id, method, notes) VALUES (?,?,?)",
        (job_id, method, notes)
    )
    conn.commit()
    conn.close()


def get_today_apply_count() -> int:
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM applications WHERE date(applied_at)=date('now')"
    ).fetchone()[0]
    conn.close()
    return count


# ── Q&A MEMORY ────────────────────────────────

def normalize_question(q: str) -> str:
    """Lowercase + strip for fuzzy matching."""
    import re
    return re.sub(r'\s+', ' ', q.lower().strip())


def find_answer(question: str) -> str | None:
    """Look up stored answer for a question."""
    norm = normalize_question(question)
    conn = get_connection()
    row = conn.execute(
        "SELECT your_answer FROM qa_memory WHERE question_normalized=?", (norm,)
    ).fetchone()
    if row:
        # increment usage
        conn.execute(
            "UPDATE qa_memory SET used_count=used_count+1 WHERE question_normalized=?",
            (norm,)
        )
        conn.commit()
    conn.close()
    return row["your_answer"] if row else None


def save_answer(question: str, answer: str):
    """Save a new Q&A pair to memory."""
    norm = normalize_question(question)
    conn = get_connection()
    conn.execute("""
        INSERT INTO qa_memory (question_normalized, question_original, your_answer)
        VALUES (?,?,?)
        ON CONFLICT(question_normalized) DO UPDATE SET
            your_answer=excluded.your_answer,
            updated_at=CURRENT_TIMESTAMP
    """, (norm, question, answer))
    conn.commit()
    conn.close()


def get_all_qa() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM qa_memory ORDER BY used_count DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
