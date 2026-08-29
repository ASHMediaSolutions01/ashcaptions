"""Tests for the SQLite job store: schema, lifecycle transitions, and the
crash-recovery / failed-job-persistence guarantees from spec section 12.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.pipeline.db import Job, JobOptions, JobStatus, JobStore


def make_options(**overrides: object) -> JobOptions:
    defaults: dict[str, object] = {
        "language": "en",
        "dialect": "US",
        "preset": "POP",
        "burn": False,
        "translate": False,
    }
    defaults.update(overrides)
    return JobOptions(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def test_insert_and_get_job(store: JobStore) -> None:
    job_id = store.insert_job(r"C:\AshCaptions\in\clip.mp4", r"C:\AshCaptions\out\clip", make_options())

    job = store.get_job(job_id)

    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.progress == 0
    assert job.error is None
    assert job.options.language == "en"
    assert job.started_at is None
    assert job.finished_at is None
    assert job.created_at


def test_get_job_missing_returns_none(store: JobStore) -> None:
    assert store.get_job(999) is None


def test_insert_job_rejects_empty_paths(store: JobStore) -> None:
    with pytest.raises(ValueError):
        store.insert_job("", "out", make_options())
    with pytest.raises(ValueError):
        store.insert_job("in.mp4", "  ", make_options())


def test_insert_job_rejects_non_options(store: JobStore) -> None:
    with pytest.raises(TypeError):
        store.insert_job("in.mp4", "out", {"language": "en"})  # type: ignore[arg-type]


def test_fetch_oldest_pending_is_fifo(store: JobStore) -> None:
    first = store.insert_job("a.mp4", "out/a", make_options())
    second = store.insert_job("b.mp4", "out/b", make_options())

    oldest = store.fetch_oldest_pending()

    assert oldest is not None
    assert oldest.id == first
    assert oldest.id != second


def test_fetch_oldest_pending_ignores_non_pending(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(job_id)

    assert store.fetch_oldest_pending() is None


def test_mark_running_then_done_lifecycle(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())

    store.mark_running(job_id)
    running = store.get_job(job_id)
    assert running is not None
    assert running.status == JobStatus.RUNNING
    assert running.started_at is not None

    store.mark_progress(job_id, 42)
    assert store.get_job(job_id).progress == 42  # type: ignore[union-attr]

    store.mark_done(job_id)
    done = store.get_job(job_id)
    assert done is not None
    assert done.status == JobStatus.DONE
    assert done.progress == 100
    assert done.finished_at is not None


def test_mark_progress_validates_range(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    with pytest.raises(ValueError):
        store.mark_progress(job_id, 101)
    with pytest.raises(ValueError):
        store.mark_progress(job_id, -1)


def test_failed_job_persists_with_its_error(store: JobStore) -> None:
    """A failed job stays visible with its error; it is never silently dropped."""
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(job_id)

    store.mark_failed(job_id, "ffmpeg exited with code 1: no such file")

    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error == "ffmpeg exited with code 1: no such file"
    assert job.finished_at is not None
    # Still enumerable via list_jobs -- never dropped from the table.
    failed_jobs = store.list_jobs(status=JobStatus.FAILED)
    assert [j.id for j in failed_jobs] == [job_id]


def test_reset_stale_running_on_startup(store: JobStore) -> None:
    """Crash-recovery: a job left 'running' is reset to 'pending' on startup."""
    stuck = store.insert_job("a.mp4", "out/a", make_options())
    also_stuck = store.insert_job("b.mp4", "out/b", make_options())
    still_pending = store.insert_job("c.mp4", "out/c", make_options())
    already_done = store.insert_job("d.mp4", "out/d", make_options())

    store.mark_running(stuck)
    store.mark_progress(stuck, 55)
    store.mark_running(also_stuck)
    store.mark_running(already_done)
    store.mark_done(already_done)

    reset_ids = store.reset_stale_running()

    assert sorted(reset_ids) == sorted([stuck, also_stuck])

    for job_id in (stuck, also_stuck):
        job = store.get_job(job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.progress == 0
        assert job.started_at is None
        assert job.error is None

    assert store.get_job(still_pending).status == JobStatus.PENDING  # type: ignore[union-attr]
    assert store.get_job(already_done).status == JobStatus.DONE  # type: ignore[union-attr]


def test_reset_stale_running_is_idempotent_when_nothing_stuck(store: JobStore) -> None:
    store.insert_job("a.mp4", "out/a", make_options())
    assert store.reset_stale_running() == []


def test_wal_mode_enabled(store: JobStore, tmp_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(store.db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"
