"""Tests for JobWorker: crash-recovery on startup, one-at-a-time execution,
progress reporting, and that failures are recorded rather than dropped.

Everything here uses ``process_next()`` / ``recover()`` directly rather than
starting the background thread, so the suite runs with no real sleeping and
no thread-timing flakiness.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ash_captions.pipeline.db import Job, JobOptions, JobStatus, JobStore
from ash_captions.pipeline.queue import JobWorker


def make_options() -> JobOptions:
    return JobOptions(language="en", dialect=None, preset="CLEAN", burn=False, translate=False)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def test_process_next_returns_false_when_queue_empty(store: JobStore) -> None:
    worker = JobWorker(store, run_job=lambda job, report: None)
    assert worker.process_next() is False


def test_process_next_runs_oldest_job_and_marks_done(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    calls: list[Job] = []

    def run_job(job: Job, report_progress) -> None:
        calls.append(job)
        report_progress(50)
        report_progress(100)

    worker = JobWorker(store, run_job=run_job)
    processed = worker.process_next()

    assert processed is True
    assert calls[0].id == job_id
    done = store.get_job(job_id)
    assert done is not None
    assert done.status == JobStatus.DONE
    assert done.progress == 100


def test_process_next_serialises_one_job_at_a_time(store: JobStore) -> None:
    first = store.insert_job("a.mp4", "out/a", make_options())
    second = store.insert_job("b.mp4", "out/b", make_options())
    order: list[int] = []

    def run_job(job: Job, report_progress) -> None:
        order.append(job.id)

    worker = JobWorker(store, run_job=run_job)
    assert worker.process_next() is True
    assert worker.process_next() is True
    assert worker.process_next() is False  # queue now empty

    assert order == [first, second]


def test_failed_job_persists_with_error_and_worker_continues(store: JobStore) -> None:
    """A failed job stays visible with its error and does not crash the worker."""
    bad_job = store.insert_job("bad.mp4", "out/bad", make_options())
    good_job = store.insert_job("good.mp4", "out/good", make_options())

    def run_job(job: Job, report_progress) -> None:
        if job.id == bad_job:
            raise RuntimeError("ffmpeg exploded")

    worker = JobWorker(store, run_job=run_job)
    assert worker.process_next() is True  # processes bad_job
    assert worker.process_next() is True  # processes good_job

    failed = store.get_job(bad_job)
    assert failed is not None
    assert failed.status == JobStatus.FAILED
    assert failed.error == "ffmpeg exploded"

    succeeded = store.get_job(good_job)
    assert succeeded is not None
    assert succeeded.status == JobStatus.DONE


def test_recover_resets_stale_running_job_to_pending(store: JobStore) -> None:
    """Crash-recovery case: a job left 'running' is reset to 'pending' on startup."""
    stuck = store.insert_job("stuck.mp4", "out/stuck", make_options())
    store.mark_running(stuck)
    store.mark_progress(stuck, 73)

    worker = JobWorker(store, run_job=lambda job, report: None)
    reset_ids = worker.recover()

    assert reset_ids == [stuck]
    recovered = store.get_job(stuck)
    assert recovered is not None
    assert recovered.status == JobStatus.PENDING
    assert recovered.progress == 0

    # And it is now picked up normally, like any other pending job.
    assert worker.process_next() is True
    assert store.get_job(stuck).status == JobStatus.DONE  # type: ignore[union-attr]


def test_start_calls_recover_before_looping(store: JobStore) -> None:
    stuck = store.insert_job("stuck.mp4", "out/stuck", make_options())
    store.mark_running(stuck)

    processed_ids: list[int] = []
    sleeps: list[float] = []

    def run_job(job: Job, report_progress) -> None:
        processed_ids.append(job.id)

    def fake_sleep(seconds: float) -> None:
        # Record calls but yield only a tiny, fixed amount of real time --
        # enough to avoid busy-spinning the CPU once the queue drains,
        # without making the test depend on the real poll_interval.
        sleeps.append(seconds)
        time.sleep(0.001)

    worker = JobWorker(store, run_job=run_job, poll_interval=0.01, sleep_fn=fake_sleep)
    worker.start()
    try:
        # Give the background thread a brief, bounded window to drain the
        # single recovered job. This is the one place a small real wait is
        # unavoidable since we're exercising the actual thread; the queue
        # has exactly one job so it resolves almost immediately.
        for _ in range(200):
            if store.get_job(stuck).status == JobStatus.DONE:  # type: ignore[union-attr]
                break
            time.sleep(0.01)
    finally:
        worker.stop(timeout=2.0)

    assert processed_ids == [stuck]
    assert store.get_job(stuck).status == JobStatus.DONE  # type: ignore[union-attr]
