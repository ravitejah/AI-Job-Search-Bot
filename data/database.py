"""
Database Manager
Handles: jobs seen, applications submitted, Q&A memory store

Performance notes
-----------------
- WAL journal mode and NORMAL synchronous pragma are set on every connection.
  WAL gives concurrent read-while-write capability and is ~3x faster for the
  incremental scoring pattern (one INSERT/UPDATE per scored job).
- Indices on (status), (source), (discovered_at) make get_jobs() fast.
- get_all_seen_ids() and get_all_seen_title_companies() load the full ID /
  title+company sets into Python memory in one query each.  This is cheaper
  than N individual job_exists() calls when deduplicating ~1500 surface records.
"""
import re
import sqlite3
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).parent.parent))
from config import DATABASE


def get_connection():
    Path(DATABASE["path"]).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE["path"])
    conn.row_factory = sqlite3.Row
    # WAL: allows concurrent reads while a write is in progress; much faster
    # for the incremental-save pattern (40+ individual writes per scoring run).
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """Create tables and indices if they do not already exist."""
    conn = get_connection()
    c = conn.cursor()

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

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            channel TEXT,
            sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT
        )
    """)

    # Indices — make status/source filters and discovered_at sorts O(log n).
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status       ON jobs(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_source       ON jobs(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_discovered   ON jobs(discovered_at)")
    # Covering index for the title+company dedup query.
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_title_company "
        "ON jobs(LOWER(title), LOWER(company))"
    )

    conn.commit()
    conn.close()
    print("Database initialized.")


# ── JOBS ──────────────────────────────────────────────────────────────────────

def get_all_seen_ids() -> set:
    """Return every job ID in the database as a Python set (single query)."""
    conn = get_connection()
    rows = conn.execute("SELECT id FROM jobs").fetchall()
    conn.close()
    return {row[0] for row in rows}


def get_all_seen_title_companies() -> set:
    """
    Return (title_lower, company_lower) pairs for every job in the database.
    Used for secondary dedup — catches reposts where the URL changed.
    """
    conn = get_connection()
    rows = conn.execute("SELECT LOWER(title), LOWER(company) FROM jobs").fetchall()
    conn.close()
    return {(row[0].strip(), row[1].strip()) for row in rows}


def bulk_save_seen(jobs: list) -> None:
    """
    Persist a batch of jobs as status='seen'.
    Uses INSERT OR IGNORE so already-present rows are never overwritten.
    Much faster than individual saves for the initial dedup checkpoint.
    """
    if not jobs:
        return
    conn = get_connection()
    conn.executemany(
        """
        INSERT OR IGNORE INTO jobs
            (id, title, company, location, job_type, description, url, source,
             match_score, match_reason, posted_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        [
            (
                job["id"],
                job["title"],
                job["company"],
                job.get("location", ""),
                job.get("job_type", ""),
                job.get("description", ""),
                job["url"],
                job["source"],
                job.get("match_score", 0),
                job.get("match_reason", ""),
                job.get("posted_at", ""),
                job.get("status", "seen"),
            )
            for job in jobs
        ],
    )
    conn.commit()
    conn.close()


def save_job(job: dict, status: str = "qualified") -> None:
    """
    Insert or update a scored job without losing the original discovery time.
    The ON CONFLICT clause updates every column except discovered_at.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO jobs
            (id, title, company, location, job_type, description, url, source,
             match_score, match_reason, posted_at, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            title         = excluded.title,
            company       = excluded.company,
            location      = excluded.location,
            job_type      = excluded.job_type,
            description   = excluded.description,
            url           = excluded.url,
            source        = excluded.source,
            match_score   = excluded.match_score,
            match_reason  = excluded.match_reason,
            posted_at     = excluded.posted_at,
            status        = excluded.status
        """,
        (
            job["id"],
            job["title"],
            job["company"],
            job.get("location", ""),
            job.get("job_type", ""),
            job.get("description", ""),
            job["url"],
            job["source"],
            job.get("match_score", 0),
            job.get("match_reason", ""),
            job.get("posted_at", ""),
            job.get("status", status),
        ),
    )
    conn.commit()
    conn.close()


def update_job_status(job_id: str, status: str) -> None:
    conn = get_connection()
    conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    conn.commit()
    conn.close()


def get_jobs(status: str = None, limit: int = 50) -> list:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY discovered_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY discovered_at DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """
    Return aggregate counts in a single connection.
    Job status counts come from one GROUP BY query instead of 5 separate COUNTs.
    """
    conn = get_connection()

    # One query for all job-status buckets.
    status_rows = conn.execute(
        "SELECT status, COUNT(*) FROM jobs GROUP BY status"
    ).fetchall()
    by_status = {row[0]: row[1] for row in status_rows}

    stats = {
        "total_jobs":      sum(by_status.values()),
        "new_jobs":        by_status.get("new", 0),
        "seen_jobs":       by_status.get("seen", 0),
        "qualified_jobs":  by_status.get("qualified", 0),
        "rejected_jobs":   by_status.get("rejected", 0),
        "applied":         conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
        "qa_answers":      conn.execute("SELECT COUNT(*) FROM qa_memory").fetchone()[0],
        "today_applied":   conn.execute(
            "SELECT COUNT(*) FROM applications WHERE date(applied_at)=date('now')"
        ).fetchone()[0],
    }
    conn.close()
    return stats


def delete_old_jobs(days: int = 30) -> int:
    """
    Delete seen/rejected jobs older than `days` days.
    Keeps the DB lean — stale records have no dedup value after a month.
    Returns the number of rows deleted.
    """
    conn = get_connection()
    cursor = conn.execute(
        "DELETE FROM jobs WHERE status IN ('seen', 'rejected') "
        "AND discovered_at < datetime('now', ?)",
        (f"-{days} days",),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


# ── APPLICATIONS ──────────────────────────────────────────────────────────────

def save_application(job_id: str, method: str, notes: str = "") -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO applications (job_id, method, notes) VALUES (?,?,?)",
        (job_id, method, notes),
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


# ── Q&A MEMORY ────────────────────────────────────────────────────────────────

def normalize_question(q: str) -> str:
    """Lowercase + collapse whitespace for fuzzy matching."""
    return re.sub(r"\s+", " ", q.lower().strip())


def find_answer(question: str) -> str | None:
    """Look up a stored answer; increments the usage counter on hit."""
    norm = normalize_question(question)
    conn = get_connection()
    row = conn.execute(
        "SELECT your_answer FROM qa_memory WHERE question_normalized=?", (norm,)
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE qa_memory SET used_count=used_count+1 WHERE question_normalized=?",
            (norm,),
        )
        conn.commit()
    conn.close()
    return row["your_answer"] if row else None


def save_answer(question: str, answer: str) -> None:
    """Upsert a Q&A pair."""
    norm = normalize_question(question)
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO qa_memory (question_normalized, question_original, your_answer)
        VALUES (?,?,?)
        ON CONFLICT(question_normalized) DO UPDATE SET
            your_answer  = excluded.your_answer,
            updated_at   = CURRENT_TIMESTAMP
        """,
        (norm, question, answer),
    )
    conn.commit()
    conn.close()


def get_all_qa() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM qa_memory ORDER BY used_count DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
