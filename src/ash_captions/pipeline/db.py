"""SQLite-backed job queue storage for the captioning pipeline.

A job row represents one video dropped into the watch folder (or submitted
via the control page). This module owns the schema and every access path to
it: callers never write raw SQL. All timestamps are ISO-8601 UTC strings.

Crash-recovery contract (spec section 12): any job left in ``running`` state
when the store is opened was interrupted mid-work and its partial ffmpeg
output cannot be trusted. ``reset_stale_running`` puts those jobs back to
``pending`` so the queue worker retries them from scratch. Callers (the
queue worker) are responsible for invoking it on startup, before the worker
loop begins pulling jobs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class JobStatus(str, Enum):
    """Lifecycle states for a queued job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class JobOptions:
    """User-selectable options for one captioning job.

    Stored as JSON inside the job row rather than as separate columns, since
    this set of options is expected to grow (see spec sections 7 and 10)
    without needing a schema migration.
    """

    language: str
    dialect: str | None
    preset: str
    burn: bool
    translate: bool

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "JobOptions":
        return JobOptions(**json.loads(raw))


@dataclass(frozen=True)
class Job:
    """A row in the jobs table."""

    id: int
    input_path: str
    output_dir: str
    status: JobStatus
    options: JobOptions
    progress: int
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_path TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    options_json TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs (status, id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        input_path=row["input_path"],
        output_dir=row["output_dir"],
        status=JobStatus(row["status"]),
        options=JobOptions.from_json(row["options_json"]),
        progress=row["progress"],
        error=row["error"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


class JobStore:
    """Typed access layer over the jobs SQLite database.

    A new connection is opened per call rather than held open for the life
    of the store. Volume here is one video job at a time (spec: video work
    is serialised), so this is not a hot path; keeping connections
    short-lived avoids any cross-thread sqlite3.Connection sharing concerns
    between the FastAPI request thread and the queue worker thread, while
    WAL mode keeps concurrent readers from blocking on the writer.
    """

    def __init__(self, db_path: str | Path) -> None:
        if not str(db_path).strip():
            raise ValueError("db_path must not be empty")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def insert_job(
        self, input_path: str | Path, output_dir: str | Path, options: JobOptions
    ) -> int:
        """Insert a new pending job and return its id."""
        input_str = str(input_path).strip()
        output_str = str(output_dir).strip()
        if not input_str:
            raise ValueError("input_path must not be empty")
        if not output_str:
            raise ValueError("output_dir must not be empty")
        if not isinstance(options, JobOptions):
            raise TypeError("options must be a JobOptions instance")
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs
                    (input_path, output_dir, status, options_json, progress,
                     error, created_at, started_at, finished_at)
                VALUES (?, ?, ?, ?, 0, NULL, ?, NULL, NULL)
                """,
                (input_str, output_str, JobStatus.PENDING.value, options.to_json(), _now_iso()),
            )
            job_id = cur.lastrowid
            if job_id is None:
                raise RuntimeError("insert_job: sqlite did not return a row id")
            return job_id

    def get_job(self, job_id: int) -> Job | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return _row_to_job(row) if row is not None else None

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        """List jobs, newest first. Optionally filtered by status."""
        with self._connect() as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM jobs ORDER BY id DESC").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC",
                    (status.value,),
                ).fetchall()
            return [_row_to_job(row) for row in rows]

    def fetch_oldest_pending(self) -> Job | None:
        """Return the longest-waiting pending job, or None if the queue is empty."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC LIMIT 1",
                (JobStatus.PENDING.value,),
            ).fetchone()
            return _row_to_job(row) if row is not None else None

    def mark_running(self, job_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ?, error = NULL WHERE id = ?",
                (JobStatus.RUNNING.value, _now_iso(), job_id),
            )

    def mark_progress(self, job_id: int, progress: int) -> None:
        if not 0 <= progress <= 100:
            raise ValueError(f"progress must be within 0..100, got {progress}")
        with self._connect() as conn:
            conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (progress, job_id))

    def mark_done(self, job_id: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, progress = 100, finished_at = ? WHERE id = ?",
                (JobStatus.DONE.value, _now_iso(), job_id),
            )

    def mark_failed(self, job_id: int, error: str) -> None:
        """Mark a job failed. The job row (and its error) is kept, never dropped."""
        error_text = error.strip() if error else "Unknown error"
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                (JobStatus.FAILED.value, error_text, _now_iso(), job_id),
            )

    def reset_stale_running(self) -> list[int]:
        """Crash recovery: reset every ``running`` job back to ``pending``.

        Called on startup, before the queue worker starts pulling jobs. A
        job in ``running`` state when the store is (re)opened was
        interrupted by a crash or restart; its partial output is not
        trusted, so it goes back to the front of the pending queue with
        progress and any stale error cleared. Returns the ids reset, for
        logging.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = ?", (JobStatus.RUNNING.value,)
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, progress = 0, started_at = NULL, error = NULL
                    WHERE status = ?
                    """,
                    (JobStatus.PENDING.value, JobStatus.RUNNING.value),
                )
            return ids
