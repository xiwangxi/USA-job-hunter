"""SQLite-backed store for de-duplicating jobs across daily runs."""

import hashlib
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_job_id TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    url TEXT,
    score INTEGER,
    sponsorship_likelihood TEXT,
    seniority_fit TEXT,
    reason TEXT,
    rejected_reason TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL
);
"""


def compute_uid(source: str, source_job_id: str | None, company: str, title: str, location: str) -> str:
    if source_job_id:
        return f"{source}:{source_job_id}"
    raw = f"{company.strip().lower()}|{title.strip().lower()}|{location.strip().lower()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"{source}:hash:{digest}"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    with closing(conn.cursor()) as cur:
        cur.executescript(SCHEMA)
    conn.commit()
    return conn


def is_seen(conn: sqlite3.Connection, uid: str) -> bool:
    cur = conn.execute("SELECT 1 FROM seen_jobs WHERE uid = ?", (uid,))
    return cur.fetchone() is not None


def record_job(
    conn: sqlite3.Connection,
    uid: str,
    job: dict,
    *,
    score: int | None = None,
    sponsorship_likelihood: str | None = None,
    seniority_fit: str | None = None,
    reason: str | None = None,
    rejected_reason: str | None = None,
    notified: bool = False,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO seen_jobs (
            uid, source, source_job_id, title, company, location, url,
            score, sponsorship_likelihood, seniority_fit, reason,
            rejected_reason, notified, first_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            uid,
            job.get("source"),
            job.get("source_job_id"),
            job.get("title"),
            job.get("company"),
            job.get("location"),
            job.get("url"),
            score,
            sponsorship_likelihood,
            seniority_fit,
            reason,
            rejected_reason,
            1 if notified else 0,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
