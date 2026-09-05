"""Tests for the JobWorker behaviours an hour-long job depends on: the loop
outliving a raising store call, cancellation putting the job back to
pending, done-before-delete ordering, and failed-job causes that keep
ffmpeg's stderr.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ash_captions.pipeline.db import Job, JobOptions, JobStatus, JobStore
from ash_captions.pipeline.queue import JobCancelled, JobWorker, ProgressReporter, format_job_error


def make_options() -> JobOptions:
    return JobOptions(language="en", dialect=None, preset="CLEAN", burn=False, translate=False)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


class FlakyStore:
    """Forwards to a real JobStore but raises from fetch_oldest_pending the
    first ``failures`` times -- a locked database, a full disk."""

    def __init__(self, inner: JobStore, failures: int) -> None:
        self._inner = inner
        self.failures_left = failures

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def fetch_oldest_pending(self):
        if self.failures_left > 0:
            self.failures_left -= 1
            raise RuntimeError("database is locked")
        return self._inner.fetch_oldest_pending()


class TestLoopSurvivesStoreErrors:
    def test_a_raising_fetch_does_not_kill_the_thread_and_later_jobs_run(
        self, store: JobStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())
        flaky = FlakyStore(store, failures=3)
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            time.sleep(0.001)

        worker = JobWorker(flaky, run_job=lambda job, report: None, poll_interval=0.01, sleep_fn=fake_sleep)
        with caplog.at_level("ERROR", logger="ash_captions.pipeline.queue"):
            worker.start()
            try:
                for _ in range(500):
                    if store.get_job(job_id).status == JobStatus.DONE:  # type: ignore[union-attr]
                        break
                    time.sleep(0.01)
                assert worker.is_alive()
            finally:
                worker.stop(timeout=2.0)

        assert store.get_job(job_id).status == JobStatus.DONE  # type: ignore[union-attr]
        assert sleeps[:3] == [1.0, 2.0, 4.0]  # backoff, doubling
        assert worker.last_error is not None and "locked" in worker.last_error
        assert any("queue worker loop error" in r.getMessage() for r in caplog.records)

    def test_backoff_caps_at_30s_and_resets_on_success(self, store: JobStore) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())
        flaky = FlakyStore(store, failures=7)
        sleeps: list[float] = []
        worker = JobWorker(flaky, run_job=lambda job, report: None, poll_interval=0.5, sleep_fn=sleeps.append)

        worker.start()
        try:
            for _ in range(500):
                if store.get_job(job_id).status == JobStatus.DONE:  # type: ignore[union-attr]
                    break
                time.sleep(0.01)
        finally:
            worker.stop(timeout=2.0)

        assert sleeps[:7] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
        assert 0.5 in sleeps[7:]  # back to the plain poll interval once it worked

    def test_is_alive_reflects_the_thread(self, store: JobStore) -> None:
        worker = JobWorker(store, run_job=lambda job, report: None, poll_interval=0.01)
        assert worker.is_alive() is False
        worker.start()
        assert worker.is_alive() is True
        worker.stop(timeout=2.0)
        assert worker.is_alive() is False


class TestCancellation:
    def test_run_job_honouring_should_stop_puts_the_job_back_to_pending(self, store: JobStore) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())

        def run_job(job: Job, report) -> None:
            report(40)
            assert isinstance(report, ProgressReporter)
            if report.should_stop():
                raise JobCancelled("stopped mid-segment")

        worker = JobWorker(store, run_job=run_job)
        worker._cancel_event.set()  # what stop() does before the join
        assert worker.process_next() is True

        job = store.get_job(job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.progress == 0
        assert job.error is None

    def test_any_failure_while_cancelling_counts_as_cancellation(self, store: JobStore) -> None:
        """An engine that predates the cancel exceptions still raises
        *something* when its ffmpeg is killed; that must not become a
        'failed' row the editor has to retry by hand."""
        job_id = store.insert_job("a.mp4", "out/a", make_options())

        def run_job(job: Job, report) -> None:
            raise RuntimeError("ffmpeg exited -1")

        worker = JobWorker(store, run_job=run_job)
        worker._cancel_event.set()
        worker.process_next()

        assert store.get_job(job_id).status == JobStatus.PENDING  # type: ignore[union-attr]

    def test_stop_cancels_a_running_job_and_joins(self, store: JobStore) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())
        started = __import__("threading").Event()

        def slow_job(job: Job, report) -> None:
            started.set()
            for _ in range(200):
                if report.should_stop():
                    raise JobCancelled("asked to stop")
                time.sleep(0.01)

        worker = JobWorker(store, run_job=slow_job, poll_interval=0.01)
        worker.start()
        assert started.wait(timeout=5)
        worker.stop(timeout=5.0)

        assert worker.is_alive() is False
        assert store.get_job(job_id).status == JobStatus.PENDING  # type: ignore[union-attr]


class TestDoneBeforeDelete:
    def test_after_done_runs_only_once_the_row_says_done(self, store: JobStore) -> None:
        store.insert_job("a.mp4", "out/a", make_options())
        seen: list[JobStatus] = []

        def run_job(job: Job, report):
            def after_done() -> None:
                seen.append(store.get_job(job.id).status)  # type: ignore[union-attr]
            return after_done

        JobWorker(store, run_job=run_job).process_next()

        assert seen == [JobStatus.DONE]

    def test_a_failing_after_done_is_logged_not_fatal(
        self, store: JobStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())

        def run_job(job: Job, report):
            def after_done() -> None:
                raise OSError("file in use")
            return after_done

        with caplog.at_level("ERROR", logger="ash_captions.pipeline.queue"):
            assert JobWorker(store, run_job=run_job).process_next() is True

        assert store.get_job(job_id).status == JobStatus.DONE  # type: ignore[union-attr]
        assert any("post-completion cleanup" in r.getMessage() for r in caplog.records)

    def test_stage_reported_through_the_reporter_is_persisted(self, store: JobStore) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())
        observed: list[str | None] = []

        def run_job(job: Job, report) -> None:
            report.stage("transcribe")
            observed.append(store.get_job(job.id).stage)  # type: ignore[union-attr]

        JobWorker(store, run_job=run_job).process_next()
        assert observed == ["transcribe"]
        assert store.get_job(job_id).stage is None  # type: ignore[union-attr]


class TestFailureCauses:
    def test_stderr_tail_is_appended_to_the_stored_error(
        self, store: JobStore, caplog: pytest.LogCaptureFixture
    ) -> None:
        job_id = store.insert_job("a.mp4", "out/a", make_options())

        class FfmpegError(Exception):
            def __init__(self) -> None:
                super().__init__("ffmpeg failed (exit 1)")
                self.stderr = "\n".join(f"line {n}" for n in range(1, 41))

        def run_job(job: Job, report) -> None:
            raise FfmpegError()

        with caplog.at_level("ERROR", logger="ash_captions.pipeline.queue"):
            JobWorker(store, run_job=run_job).process_next()

        error = store.get_job(job_id).error  # type: ignore[union-attr]
        assert error is not None
        assert error.startswith("ffmpeg failed (exit 1)")
        assert "line 40" in error and "line 21" in error and "line 20" not in error
        assert any(r.exc_info for r in caplog.records)  # log.exception, with traceback

    def test_error_text_is_capped_at_4kb(self) -> None:
        class Huge(Exception):
            stderr = "x" * 10_000 + "\nlast line"

        text = format_job_error(Huge("boom"))
        assert len(text.encode("utf-8")) <= 4096
        assert text.endswith("last line")

    def test_plain_exceptions_are_unchanged(self) -> None:
        assert format_job_error(RuntimeError("ffmpeg exploded")) == "ffmpeg exploded"
