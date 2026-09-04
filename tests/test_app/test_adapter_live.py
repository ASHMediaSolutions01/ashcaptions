"""Tests for the QueueAdapter behaviours added for hour-long jobs: submit
dedupe, unique output folders, progress write/notify throttling, stage
surfacing, and the health dict.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ash_captions.app.adapter import QueueAdapter, _to_web_job
from ash_captions.pipeline.db import JobStore
from ash_captions.web.interfaces import JobNotRetryableError
from ash_captions.web.models import Job as WebJob
from ash_captions.web.models import JobOptions, JobStatus


def make_options(**overrides) -> JobOptions:
    fields = dict(language="en", dialect=None, preset="POP", burn_in=False, translate_to_english=False)
    fields.update(overrides)
    return JobOptions(**fields)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


@pytest.fixture
def adapter(store: JobStore, tmp_path: Path) -> QueueAdapter:
    return QueueAdapter(store, out_dir=tmp_path / "out", notify_interval=0.2)


class TestSubmitDedupe:
    def test_submitting_a_queued_file_again_returns_the_same_job(
        self, adapter: QueueAdapter, tmp_path: Path
    ) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")

        first = adapter.submit(video, make_options())
        second = adapter.submit(video, make_options())

        assert second.id == first.id
        assert len(adapter.list_jobs()) == 1

    def test_restart_shaped_double_submit_leaves_one_live_row(self, store: JobStore, tmp_path: Path) -> None:
        """Two adapters over the same DB file -- what two launches look like."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        QueueAdapter(store, out_dir=tmp_path / "out").submit(video, make_options())
        QueueAdapter(JobStore(store.db_path), out_dir=tmp_path / "out").submit(video, make_options())

        assert len(store.list_live_jobs()) == 1

    def test_retry_of_a_failed_job_whose_file_is_queued_again_is_not_retryable(
        self, adapter: QueueAdapter, store: JobStore, tmp_path: Path
    ) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        first = adapter.submit(video, make_options())
        store.mark_running(int(first.id))
        store.mark_failed(int(first.id), "boom")
        adapter.submit(video, make_options())  # a fresh job for the same file

        with pytest.raises(JobNotRetryableError):
            adapter.retry(first.id)


class TestUniqueOutputDir:
    def test_same_stem_gets_numbered_folders(self, adapter: QueueAdapter, store: JobStore, tmp_path: Path) -> None:
        a = tmp_path / "a" / "clip.mp4"
        b = tmp_path / "b" / "clip.mp4"
        c = tmp_path / "c" / "clip.mp4"
        for video in (a, b, c):
            video.parent.mkdir()
            video.write_bytes(b"fake")

        dirs = [Path(store.get_job(int(adapter.submit(v, make_options()).id)).output_dir) for v in (a, b, c)]  # type: ignore[union-attr]

        assert [d.name for d in dirs] == ["clip", "clip (2)", "clip (3)"]

    def test_a_folder_already_on_disk_is_skipped_too(self, adapter: QueueAdapter, tmp_path: Path) -> None:
        (tmp_path / "out" / "clip").mkdir(parents=True)
        assert adapter.unique_output_dir("clip").name == "clip (2)"


class TestProgressThrottle:
    def test_unchanged_integer_progress_is_not_written(self, adapter: QueueAdapter, store: JobStore, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        job_id = int(adapter.submit(video, make_options()).id)
        writes: list[int] = []
        real = store.mark_progress
        store.mark_progress = lambda jid, pct: (writes.append(pct), real(jid, pct))  # type: ignore[method-assign]

        adapter.notifying_store.mark_running(job_id)
        for pct in (10, 10, 10, 11, 11, 12):
            adapter.notifying_store.mark_progress(job_id, pct)

        assert writes == [10, 11, 12]

    @pytest.mark.asyncio
    async def test_a_burst_of_notifies_yields_one_immediate_and_one_trailing_publish(
        self, adapter: QueueAdapter, tmp_path: Path
    ) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        job_id = int(adapter.submit(video, make_options()).id)
        subscription = adapter.subscribe()
        await subscription.__anext__()

        # A burst well inside one interval, from the worker's thread.
        for pct in range(1, 21):
            adapter.notifying_store.mark_progress(job_id, pct)
        await asyncio.sleep(0.05)
        first = await asyncio.wait_for(subscription.__anext__(), timeout=1.0)
        second = await asyncio.wait_for(subscription.__anext__(), timeout=1.0)

        # Nothing else is pending: a third would time out.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(subscription.__anext__(), timeout=0.5)
        assert second[0].progress == pytest.approx(0.20)  # trailing edge carried the final value
        assert first[0].progress <= second[0].progress
        await subscription.aclose()

    def test_list_jobs_is_capped(self, store: JobStore, tmp_path: Path) -> None:
        adapter = QueueAdapter(store, out_dir=tmp_path / "out", list_limit=3)
        for n in range(5):
            video = tmp_path / f"{n}.mp4"
            video.write_bytes(b"x")
            adapter.submit(video, make_options())
        assert len(adapter.list_jobs()) == 3


class TestStageSurfacing:
    def test_stage_is_passed_only_when_the_web_model_declares_it(
        self, store: JobStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job_id = store.insert_job(tmp_path / "clip.mp4", tmp_path / "out", __import__("ash_captions.pipeline.db", fromlist=["JobOptions"]).JobOptions(
            language="en", dialect=None, preset="POP", burn=False, translate=False))
        store.mark_running(job_id)
        store.mark_stage(job_id, "transcribe")
        job = store.get_job(job_id)
        assert job is not None and job.stage == "transcribe"

        web_job = _to_web_job(job)  # the shipped model: must not blow up
        if "stage" in WebJob.model_fields:
            assert web_job.stage == "transcribe"  # type: ignore[attr-defined]

        class RicherWebJob(WebJob):
            stage: str | None = None
            stage_started_at: str | None = None
            started_at: str | None = None
            input_path: str | None = None
            output_dir: str | None = None

        # _to_web_job resolves WebJob in adapter_convert's namespace (v0.5:
        # the conversions moved out of adapter.py to keep it under 500
        # lines), so that is what must be patched for this to take effect.
        monkeypatch.setattr("ash_captions.app.adapter_convert.WebJob", RicherWebJob)
        richer = _to_web_job(job)
        assert richer.stage == "transcribe"
        assert richer.stage_started_at == job.stage_started_at
        assert richer.started_at == job.started_at
        assert richer.input_path == job.input_path
        assert richer.output_dir == job.output_dir
        assert richer.updated_at.isoformat() == job.stage_started_at


class TestHealth:
    def test_health_without_sources_is_all_dead(self, adapter: QueueAdapter) -> None:
        assert adapter.health() == {
            "worker_alive": False,
            "worker_last_error": None,
            "current_job_id": None,
            "watcher_alive": False,
            "last_watcher_poll": None,
        }

    def test_health_reports_worker_and_watcher(self, adapter: QueueAdapter, store: JobStore, tmp_path: Path) -> None:
        from ash_captions.pipeline import JobWorker, Watcher

        worker = JobWorker(store, run_job=lambda job, report: None, poll_interval=0.01)
        watcher = Watcher(tmp_path / "in", on_ready=lambda p: None)
        adapter.attach_health(worker=worker, watcher=watcher)
        watcher.poll_once()
        worker.start()
        try:
            health = adapter.health()
        finally:
            worker.stop(timeout=2.0)

        assert health["worker_alive"] is True
        assert health["watcher_alive"] is False  # never started, only polled
        assert isinstance(health["last_watcher_poll"], str)
        assert adapter.health()["worker_alive"] is False
