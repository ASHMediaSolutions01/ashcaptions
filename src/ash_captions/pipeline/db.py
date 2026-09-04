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

One live job per input file: a partial unique index over ``input_path`` for
``pending``/``running`` rows means a restart (the watcher re-seeing a file
still sitting in ``in\\``) or a double submit can never queue the same file
twice. ``insert_job`` returns the existing live job's id in that case rather
than raising -- callers treat "already queued" as success.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterator


class JobStatus(str, Enum):
    """Lifecycle states for a queued job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


LIVE_STATUSES = (JobStatus.PENDING, JobStatus.RUNNING)

# Pipeline stages, in order, as stored in ``jobs.stage`` while a job runs
# so the control page can say "Transcribing - 12 min elapsed".
STAGES = ("extract", "transcribe", "translate", "postprocess", "write", "matte", "burn")


@dataclass(frozen=True)
class JobOptions:
    """User-selectable options for one captioning job.

    Stored as JSON inside the job row rather than as separate columns, since
    this set of options is expected to grow (see spec sections 7 and 10)
    without needing a schema migration. ``from_json`` therefore tolerates
    rows written before a field existed (default filled in) and after one
    was removed (unknown key ignored).
    """

    language: str
    dialect: str | None
    preset: str
    burn: bool
    translate: bool
    # "full" transcribes; "burn_only" reuses the saved transcript beside the
    # outputs and only re-renders captions (and burns); "translate_only"
    # reuses it and adds only the English pass (the Studio's "Translate to
    # check"). The Studio page submits the last two.
    mode: str = "full"
    # Which client the footage belongs to (display form, e.g. "Acme Corp").
    # Picks that client's glossary on top of the shared one; None means the
    # shared glossary alone. Sanitized at the web/watch-folder boundary.
    client: str | None = None
    # Draw the captions behind the person (a matte pass before the burn).
    # Off by default: it costs about the video's length in extra time and
    # is meant for reels.
    behind_speaker: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "JobOptions":
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
        known = {spec.name for spec in fields(JobOptions)}
        merged = {**_OPTION_DEFAULTS, **{k: v for k, v in data.items() if k in known}}
        # Rows written before `client` existed have no key (default fills
        # in); a hand-edited or foreign value that isn't text reads as none.
        if not isinstance(merged["client"], str) or not merged["client"].strip():
            merged["client"] = None
        return JobOptions(**merged)


_OPTION_DEFAULTS: dict[str, Any] = {
    "language": "en",
    "dialect": None,
    "preset": "POP",
    "burn": False,
    "translate": False,
    "mode": "full",
    "client": None,
    "behind_speaker": False,
}

# How many recent rows `known_clients` scans for distinct client names.
KNOWN_CLIENTS_SCAN_LIMIT = 500


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
    stage: str | None = None
    stage_started_at: str | None = None


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
    finished_at TEXT,
    stage TEXT,
    stage_started_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_id ON jobs (status, id);
"""

# Columns added after the first release, applied with ALTER TABLE on an
# existing database (SQLite has no ADD COLUMN IF NOT EXISTS).
_ADDED_COLUMNS = (("stage", "TEXT"), ("stage_started_at", "TEXT"))

_LIVE_INDEX = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_live_input
    ON jobs (input_path) WHERE status IN ('pending', 'running');
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row: sqlite3.Row) -> Job:
    keys = row.keys()
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
        stage=row["stage"] if "stage" in keys else None,
        stage_started_at=row["stage_started_at"] if "stage_started_at" in keys else None,
    )


class JobStore:
    """Typed access layer over the jobs SQLite database.

    A new connection is opened per call rather than held open for the life
    of the store. Volume here is one video job at a time (spec: video work
    is serialised), so this is not a hot path; keeping connections
    short-lived avoids any cross-thread sqlite3.Connection sharing concerns
    between the FastAPI request thread and the queue worker thread, while
    WAL mode (set once, at schema init -- it is persistent in the file)
    keeps concurrent readers from blocking on the writer.
    """

    def __init__(self, db_path: str | Path) -> None:
        if not str(db_path).strip():
            raise ValueError("db_path must not be empty")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One connection, committed on success, always closed."""
        with closing(self._connect()) as conn, conn:
            yield conn

    def _init_schema(self) -> None:
        with self._tx() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            present = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
            for name, sql_type in _ADDED_COLUMNS:
                if name not in present:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")
            self._collapse_duplicate_live_jobs(conn)
            conn.executescript(_LIVE_INDEX)

    @staticmethod
    def _collapse_duplicate_live_jobs(conn: sqlite3.Connection) -> None:
        """Migration for databases written before the live-input index
        existed: keep the oldest live row per input path, fail the rest."""
        rows = conn.execute(
            "SELECT id, input_path FROM jobs WHERE status IN ('pending', 'running') ORDER BY id ASC"
        ).fetchall()
        keeper: dict[str, int] = {}
        for row in rows:
            original = keeper.setdefault(row["input_path"], row["id"])
            if original != row["id"]:
                conn.execute(
                    "UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                    (JobStatus.FAILED.value, f"duplicate of job {original}", _now_iso(), row["id"]),
                )

    def insert_job(
        self, input_path: str | Path, output_dir: str | Path, options: JobOptions
    ) -> int:
        """Insert a new pending job and return its id.

        If a ``pending``/``running`` job already exists for ``input_path``,
        that job's id is returned instead and nothing is inserted.
        """
        input_str = str(input_path).strip()
        output_str = str(output_dir).strip()
        if not input_str:
            raise ValueError("input_path must not be empty")
        if not output_str:
            raise ValueError("output_dir must not be empty")
        if not isinstance(options, JobOptions):
            raise TypeError("options must be a JobOptions instance")
        try:
            with self._tx() as conn:
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
        except sqlite3.IntegrityError:
            existing = self.find_live_job(input_str)
            if existing is None:  # the live row vanished in between; try once more
                return self.insert_job(input_str, output_str, options)
            return existing.id
        if job_id is None:
            raise RuntimeError("insert_job: sqlite did not return a row id")
        return job_id

    def get_job(self, job_id: int) -> Job | None:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return _row_to_job(row) if row is not None else None

    def find_live_job(self, input_path: str | Path) -> Job | None:
        """The ``pending``/``running`` job for ``input_path``, if any."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE input_path = ? AND status IN ('pending', 'running') "
                "ORDER BY id ASC LIMIT 1",
                (str(input_path).strip(),),
            ).fetchone()
            return _row_to_job(row) if row is not None else None

    def list_jobs(self, status: JobStatus | None = None, *, limit: int | None = None) -> list[Job]:
        """List jobs, newest first. Optionally filtered by status and capped
        at ``limit`` rows (the control page never needs the whole history)."""
        sql = "SELECT * FROM jobs"
        params: list[object] = []
        if status is not None:
            sql += " WHERE status = ?"
            params.append(status.value)
        sql += " ORDER BY id DESC"
        if limit is not None:
            if limit < 0:
                raise ValueError("limit must not be negative")
            sql += " LIMIT ?"
            params.append(limit)
        with self._tx() as conn:
            return [_row_to_job(row) for row in conn.execute(sql, params).fetchall()]

    def list_live_jobs(self) -> list[Job]:
        """Every ``pending`` or ``running`` job, oldest first."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('pending', 'running') ORDER BY id ASC"
            ).fetchall()
            return [_row_to_job(row) for row in rows]

    def known_clients(self, *, limit: int = KNOWN_CLIENTS_SCAN_LIMIT) -> list[str]:
        """Distinct client names on the newest ``limit`` rows, most recently
        used first -- what the control page's client picker suggests. Two
        spellings differing only in case count as one (the first seen wins)."""
        if limit < 0:
            raise ValueError("limit must not be negative")
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT options_json FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        seen: dict[str, str] = {}
        for row in rows:
            client = JobOptions.from_json(row["options_json"]).client
            if client is not None:
                seen.setdefault(client.lower(), client)
        return list(seen.values())

    def output_dir_in_use(self, output_dir: str | Path) -> bool:
        """True if any job row (any status) already names ``output_dir``."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE output_dir = ? LIMIT 1", (str(output_dir),)
            ).fetchone()
            return row is not None

    def fetch_oldest_pending(self) -> Job | None:
        """Return the longest-waiting pending job, or None if the queue is empty."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id ASC LIMIT 1",
                (JobStatus.PENDING.value,),
            ).fetchone()
            return _row_to_job(row) if row is not None else None

    def mark_running(self, job_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, started_at = ?, error = NULL, "
                "stage = NULL, stage_started_at = NULL WHERE id = ?",
                (JobStatus.RUNNING.value, _now_iso(), job_id),
            )

    def mark_progress(self, job_id: int, progress: int) -> None:
        if not 0 <= progress <= 100:
            raise ValueError(f"progress must be within 0..100, got {progress}")
        with self._tx() as conn:
            conn.execute("UPDATE jobs SET progress = ? WHERE id = ?", (progress, job_id))

    def mark_stage(self, job_id: int, stage: str) -> None:
        """Record which pipeline stage the running job just entered."""
        if stage not in STAGES:
            raise ValueError(f"unknown stage {stage!r}; expected one of {STAGES}")
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET stage = ?, stage_started_at = ? WHERE id = ?",
                (stage, _now_iso(), job_id),
            )

    def mark_done(self, job_id: int) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, progress = 100, finished_at = ?, "
                "stage = NULL, stage_started_at = NULL WHERE id = ?",
                (JobStatus.DONE.value, _now_iso(), job_id),
            )

    def update_options(self, job_id: int, options: JobOptions) -> None:
        """Replace a job's stored options (the Studio changes the preset
        after a restyle so the row matches the .ass on disk)."""
        if not isinstance(options, JobOptions):
            raise TypeError("options must be a JobOptions instance")
        with self._tx() as conn:
            conn.execute("UPDATE jobs SET options_json = ? WHERE id = ?", (options.to_json(), job_id))

    def mark_failed(self, job_id: int, error: str) -> None:
        """Mark a job failed. The job row (and its error) is kept, never dropped."""
        error_text = error.strip() if error else "Unknown error"
        with self._tx() as conn:
            conn.execute(
                "UPDATE jobs SET status = ?, error = ?, finished_at = ?, "
                "stage = NULL, stage_started_at = NULL WHERE id = ?",
                (JobStatus.FAILED.value, error_text, _now_iso(), job_id),
            )

    def requeue(self, job_id: int) -> None:
        """Reset a single job back to ``pending`` (the control page's retry
        button, and the worker's own cancellation path).

        Unlike ``reset_stale_running``, this targets exactly one job and
        works regardless of its current status -- retrying a ``failed`` job
        is the expected case, but a ``done`` job can be requeued too (e.g.
        to re-run it after a glossary change); it is simply reset the same
        way. Raises ``KeyError`` if no job with that id exists, so a caller
        can distinguish "reset" from "nothing happened", and
        ``DuplicateJobError`` if another live job already covers the same
        input file.
        """
        try:
            with self._tx() as conn:
                cur = conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, progress = 0, error = NULL, started_at = NULL,
                        finished_at = NULL, stage = NULL, stage_started_at = NULL
                    WHERE id = ?
                    """,
                    (JobStatus.PENDING.value, job_id),
                )
                if cur.rowcount == 0:
                    raise KeyError(f"no job with id {job_id}")
        except sqlite3.IntegrityError as exc:
            raise DuplicateJobError(
                f"job {job_id} cannot be requeued: another job for the same file is already queued"
            ) from exc

    def delete_job(self, job_id: int) -> None:
        """Forget one finished job's row (the control page's "Remove from
        list"). Only the row goes: the outputs on disk are never touched
        here. Raises ``KeyError`` for an unknown id and ``ValueError`` for
        a ``pending``/``running`` job -- a live job leaves the list by
        finishing, not by being deleted from under the worker.
        """
        with self._tx() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"no job with id {job_id}")
            if JobStatus(row["status"]) in LIVE_STATUSES:
                raise ValueError(f"job {job_id} is {row['status']}; only finished jobs can be removed")
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def reset_stale_running(self) -> list[int]:
        """Crash recovery: reset every ``running`` job back to ``pending``.

        Called on startup, before the queue worker starts pulling jobs. A
        job in ``running`` state when the store is (re)opened was
        interrupted by a crash or restart; its partial output is not
        trusted, so it goes back to the front of the pending queue with
        progress and any stale error cleared. Returns the ids reset, for
        logging.
        """
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = ?", (JobStatus.RUNNING.value,)
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, progress = 0, started_at = NULL, error = NULL,
                        stage = NULL, stage_started_at = NULL
                    WHERE status = ?
                    """,
                    (JobStatus.PENDING.value, JobStatus.RUNNING.value),
                )
            return ids


class DuplicateJobError(Exception):
    """Raised by ``requeue`` when another live job already covers the file."""
