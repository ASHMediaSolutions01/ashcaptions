"""Tests for QueueAdapter: the four id/progress/options/filename
conversions between web's JobQueue protocol and pipeline.JobStore, and the
push-driven subscribe() contract (spec section 8.3).
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from ash_captions.app.adapter import QueueAdapter
from ash_captions.pipeline.db import JobStore
from ash_captions.web.interfaces import JobNotFoundError, JobNotRetryableError
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
    return QueueAdapter(store, out_dir=tmp_path / "out")


class TestSubmit:
    def test_submit_returns_pending_job_with_str_id_and_derived_filename(
        self, adapter: QueueAdapter, tmp_path: Path
    ) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")

        job = adapter.submit(video, make_options())

        assert isinstance(job.id, str)
        int(job.id)  # doesn't raise -- underlying id is numeric
        assert job.filename == "clip.mp4"
        assert job.status == JobStatus.PENDING
        assert job.progress == 0.0

    def test_submit_computes_output_dir_from_video_stem(
        self, adapter: QueueAdapter, store: JobStore, tmp_path: Path
    ) -> None:
        video = tmp_path / "my_video.mov"
        video.write_bytes(b"fake")

        job = adapter.submit(video, make_options())

        raw = store.get_job(int(job.id))
        assert raw is not None
        assert Path(raw.output_dir) == tmp_path / "out" / "my_video"

    def test_submit_maps_options_field_by_field(self, adapter: QueueAdapter, store: JobStore, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        options = make_options(dialect="es-MX", preset="CLEAN", burn_in=True, translate_to_english=True)

        job = adapter.submit(video, options)

        raw = store.get_job(int(job.id))
        assert raw is not None
        assert raw.options.language == "en"
        assert raw.options.dialect == "es-MX"
        assert raw.options.preset == "CLEAN"
        assert raw.options.burn is True
        assert raw.options.translate is True

        # And it round-trips back out through get_job() as web options.
        fetched = adapter.get_job(job.id)
        assert fetched is not None
        assert fetched.options.dialect == "es-MX"
        assert fetched.options.burn_in is True
        assert fetched.options.translate_to_english is True


class TestGetJobAndList:
    def test_get_job_returns_none_for_unknown_numeric_id(self, adapter: QueueAdapter) -> None:
        assert adapter.get_job("999") is None

    def test_get_job_returns_none_for_non_numeric_id_without_raising(self, adapter: QueueAdapter) -> None:
        assert adapter.get_job("not-a-number") is None

    def test_list_jobs_newest_first(self, adapter: QueueAdapter, tmp_path: Path) -> None:
        first = tmp_path / "a.mp4"
        second = tmp_path / "b.mp4"
        first.write_bytes(b"1")
        second.write_bytes(b"2")

        job_a = adapter.submit(first, make_options())
        job_b = adapter.submit(second, make_options())

        ids = [job.id for job in adapter.list_jobs()]
        assert ids == [job_b.id, job_a.id]


class TestProgressConversion:
    def test_progress_converts_0_to_100_scale_into_0_to_1_float(
        self, adapter: QueueAdapter, store: JobStore, tmp_path: Path
    ) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        job = adapter.submit(video, make_options())
        job_id = int(job.id)

        store.mark_progress(job_id, 33)
        assert adapter.get_job(job.id).progress == pytest.approx(0.33)

        store.mark_progress(job_id, 100)
        assert adapter.get_job(job.id).progress == 1.0

        store.mark_progress(job_id, 0)
        assert adapter.get_job(job.id).progress == 0.0


class TestRetry:
    def test_retry_requeues_a_failed_job_preserving_its_id(
        self, adapter: QueueAdapter, store: JobStore, tmp_path: Path
    ) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        job = adapter.submit(video, make_options())
        job_id = int(job.id)
        store.mark_running(job_id)
        store.mark_failed(job_id, "ffmpeg exploded")

        retried = adapter.retry(job.id)

        assert retried.id == job.id
        assert retried.status == JobStatus.PENDING
        assert retried.error is None
        assert retried.progress == 0.0

    def test_retry_unknown_numeric_id_raises_job_not_found(self, adapter: QueueAdapter) -> None:
        with pytest.raises(JobNotFoundError):
            adapter.retry("999")

    def test_retry_non_numeric_id_raises_job_not_found_not_value_error(self, adapter: QueueAdapter) -> None:
        with pytest.raises(JobNotFoundError):
            adapter.retry("does-not-exist")

    def test_retry_non_failed_job_raises_not_retryable(self, adapter: QueueAdapter, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        job = adapter.submit(video, make_options())  # still pending

        with pytest.raises(JobNotRetryableError):
            adapter.retry(job.id)


class TestPushDrivenSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_yields_current_snapshot_first(self, adapter: QueueAdapter, tmp_path: Path) -> None:
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        adapter.submit(video, make_options())

        subscription = adapter.subscribe()
        snapshot = await subscription.__anext__()

        assert len(snapshot) == 1
        await subscription.aclose()

    @pytest.mark.asyncio
    async def test_notify_from_a_worker_thread_reaches_the_subscriber(
        self, adapter: QueueAdapter, tmp_path: Path
    ) -> None:
        """The queue worker mutates the store from its own background
        thread, never the event loop thread. `_notify()` must marshal that
        back via `call_soon_threadsafe` -- this reproduces exactly that
        cross-thread shape and proves the subscriber still wakes up.
        """
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake")
        job = adapter.submit(video, make_options())
        job_id = int(job.id)

        subscription = adapter.subscribe()
        await subscription.__anext__()  # consume the initial snapshot

        def mutate_from_another_thread() -> None:
            # Exactly what pipeline.JobWorker does: call mark_* on the
            # store handed to it (here, the notifying wrapper) from its own
            # thread, off the event loop entirely.
            adapter.notifying_store.mark_running(job_id)

        thread = threading.Thread(target=mutate_from_another_thread)
        thread.start()
        thread.join()

        snapshot = await asyncio.wait_for(subscription.__anext__(), timeout=2.0)

        assert snapshot[0].status == JobStatus.RUNNING
        await subscription.aclose()

    @pytest.mark.asyncio
    async def test_unsubscribed_queue_is_dropped_on_disconnect(self, adapter: QueueAdapter, tmp_path: Path) -> None:
        subscription = adapter.subscribe()
        await subscription.__anext__()
        assert len(adapter._subscribers) == 1

        await subscription.aclose()

        assert len(adapter._subscribers) == 0
