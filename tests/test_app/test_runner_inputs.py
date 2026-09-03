"""Tests for the runner behaviours that matter on an hour-long job: the
input-deletion matrix (watch folder / upload folder / editor's own path x
success / failure, and done-before-delete), cancellation, per-stage
progress from the transcriber, disk-space refusal before a burn, and the
probe-once wiring (PlayRes into the .ass, duration into the burn bar).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions import engine
from ash_captions.app.runner import DiskSpaceError, build_run_job
from ash_captions.app.runner_util import check_free_space
from ash_captions.pipeline.db import JobStatus, JobStore
from ash_captions.pipeline.queue import JobCancelled, JobWorker, ProgressReporter

from .test_runner import FakeTranscriber, _fake_burn, _result, _video, make_job, make_settings, run_to_completion

pytest_plugins: list[str] = []


@pytest.fixture(autouse=True)
def fake_extract_audio(monkeypatch: pytest.MonkeyPatch):
    def _fake(video_path, output_path, *, ffmpeg_path=None):
        Path(output_path).write_bytes(b"RIFF")
        return Path(output_path)

    monkeypatch.setattr(engine, "extract_audio", _fake)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def _boom_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(video_path, output_path, *, ffmpeg_path=None):
        raise engine.AudioExtractionError("ffmpeg exploded")

    monkeypatch.setattr(engine, "extract_audio", boom)


def _runner(settings, transcriber=None):
    transcriber = transcriber or FakeTranscriber(_result(["hello", "there", "friend"]))
    return build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)


class TestInputDeletionRule:
    """The most dangerous line in this package: only ever delete a file
    that came from the watch folder or our own upload folder."""

    def test_watch_folder_file_is_deleted_on_success(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = _video(tmp_path, "in")
        job = make_job(store, video, settings.out_dir / "clip")
        run_to_completion(_runner(settings), job)
        assert not video.exists()

    def test_upload_folder_file_and_its_uuid_folder_are_deleted_on_success(
        self, tmp_path: Path, store: JobStore
    ) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "uploads" / "0123abcd" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake")
        job = make_job(store, video, settings.out_dir / "clip")
        run_to_completion(_runner(settings), job)
        assert not video.exists()
        assert not video.parent.exists()
        assert settings.upload_dir.exists()

    def test_submit_by_path_file_is_never_deleted_on_success(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = _video(tmp_path, "editors_own_footage")
        job = make_job(store, video, settings.out_dir / "clip")
        assert run_to_completion(_runner(settings), job) is None
        assert video.exists()

    @pytest.mark.parametrize("folder", ["in", "uploads/abcd", "editors_own_footage"])
    def test_nothing_is_deleted_on_failure(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch, folder: str
    ) -> None:
        settings = make_settings(tmp_path)
        video = _video(tmp_path, folder)
        _boom_extract(monkeypatch)
        job = make_job(store, video, settings.out_dir / "clip")
        with pytest.raises(engine.AudioExtractionError):
            run_to_completion(_runner(settings), job)
        assert video.exists()

    def test_deletion_happens_only_after_the_row_is_marked_done(self, tmp_path: Path, store: JobStore) -> None:
        """Through the real worker: the file must still exist at the moment
        mark_done runs, and be gone afterwards."""
        settings = make_settings(tmp_path)
        video = _video(tmp_path, "in")
        job = make_job(store, video, settings.out_dir / "clip")
        existed_at_done: list[bool] = []
        real_mark_done = store.mark_done

        def spy_mark_done(job_id: int) -> None:
            existed_at_done.append(video.exists())
            real_mark_done(job_id)

        store.mark_done = spy_mark_done  # type: ignore[method-assign]
        JobWorker(store, run_job=_runner(settings)).process_next()

        assert existed_at_done == [True]
        assert not video.exists()
        assert store.get_job(job.id).status == JobStatus.DONE  # type: ignore[union-attr]


class ProgressTranscriber:
    """A transcriber with the newer engine's on_progress/should_stop keywords."""

    def __init__(self, result, *, stop_after: int | None = None) -> None:
        self.result = result
        self.stop_after = stop_after
        self.should_stop_seen: list[bool] = []

    def transcribe(self, audio_path, *, language=None, initial_prompt=None, on_progress=None, should_stop=None, **_kw):
        total = 3600.0
        for n in range(1, 5):
            if should_stop is not None:
                self.should_stop_seen.append(should_stop())
                if should_stop():
                    raise _TranscriptionCancelled("stopped")
            if on_progress is not None:
                on_progress(n * 900.0, total)
        return self.result

    translate = transcribe


class _TranscriptionCancelled(Exception):
    pass


class TestTranscribeProgressAndStage:
    def test_transcriber_progress_moves_the_bar_through_its_slice(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip")
        progress: list[int] = []
        run_to_completion(_runner(settings, ProgressTranscriber(_result(["a", "b", "c"]))), job, progress.append)

        from ash_captions.app.runner import _progress_budget

        start, end = _progress_budget(translate=False, burn=False)["transcribe"]
        expected = [round(start + (end - start) * f) for f in (0.25, 0.5, 0.75)]
        assert [p for p in progress if start < p < end] == expected  # the bar moved *inside* the slice
        assert end in progress

    def test_stages_are_reported_in_order(self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = make_settings(tmp_path)
        monkeypatch.setattr(engine, "burn_captions", _fake_burn())
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip", burn=True, translate=True)
        stages: list[str] = []
        reporter = ProgressReporter(on_progress=lambda p: None, on_stage=stages.append, should_stop=lambda: False)
        transcriber = FakeTranscriber(_result(["a", "b", "c"]), translate_result=_result(["x", "y", "z"]))
        run_to_completion(_runner(settings, transcriber), job, reporter)
        assert stages == ["extract", "transcribe", "translate", "postprocess", "write", "burn"]

    def test_stage_is_persisted_by_the_worker(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip")
        seen: dict[str, str | None] = {}

        class Peeking(FakeTranscriber):
            def transcribe(self, *a, **k):
                seen["during"] = store.get_job(job.id).stage  # type: ignore[union-attr]
                return super().transcribe(*a, **k)

        JobWorker(store, run_job=_runner(settings, Peeking(_result(["a", "b", "c"])))).process_next()
        assert seen["during"] == "transcribe"
        assert store.get_job(job.id).stage is None  # type: ignore[union-attr]


class TestCancellation:
    def test_engine_cancellation_becomes_job_cancelled(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ash_captions.app.runner as runner_module

        monkeypatch.setattr(runner_module, "_CANCEL_EXCEPTIONS", (_TranscriptionCancelled,))
        settings = make_settings(tmp_path)
        video = _video(tmp_path, "in")
        job = make_job(store, video, settings.out_dir / "clip")
        reporter = ProgressReporter(on_progress=lambda p: None, on_stage=lambda s: None, should_stop=lambda: True)

        with pytest.raises(JobCancelled):
            _runner(settings, ProgressTranscriber(_result(["a", "b", "c"])))(job, reporter)

        assert video.exists()  # cancelled, not consumed
        assert list(settings.tmp_dir.iterdir()) == []

    def test_worker_stop_requeues_a_cancelled_job(self, tmp_path: Path, store: JobStore, monkeypatch) -> None:
        import ash_captions.app.runner as runner_module

        monkeypatch.setattr(runner_module, "_CANCEL_EXCEPTIONS", (_TranscriptionCancelled,))
        settings = make_settings(tmp_path)
        job = make_job(store, _video(tmp_path, "in"), settings.out_dir / "clip")
        worker = JobWorker(store, run_job=_runner(settings, ProgressTranscriber(_result(["a", "b", "c"]))))
        worker._cancel_event.set()
        worker.process_next()
        assert store.get_job(job.id).status == JobStatus.PENDING  # type: ignore[union-attr]


class TestDiskSpace:
    def test_refuses_a_burn_with_a_plain_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil

        monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * 1024**3, 97 * 1024**3, 3 * 1024**3))
        with pytest.raises(DiskSpaceError) as raised:
            check_free_space(tmp_path / "out" / "clip", input_size_bytes=7 * 1024**3, min_free_gb=5)
        message = str(raised.value)
        assert message.startswith("Not enough free space on ")
        assert "need about 8.4 GB, have 3.0 GB" in message

    def test_minimum_wins_over_a_small_input(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import shutil

        monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * 1024**3, 97 * 1024**3, 3 * 1024**3))
        with pytest.raises(DiskSpaceError, match="need about 5.0 GB"):
            check_free_space(tmp_path, input_size_bytes=10, min_free_gb=5)

    def test_a_burn_job_fails_fast_before_ffmpeg(self, tmp_path: Path, store: JobStore, monkeypatch) -> None:
        import shutil

        settings = make_settings(tmp_path)
        settings.min_free_disk_gb = 5
        monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10, 9, 1))
        burned = {"called": False}

        def never(*a, **k):
            burned["called"] = True

        monkeypatch.setattr(engine, "burn_captions", never)
        job = make_job(store, _video(tmp_path, "in"), settings.out_dir / "clip", burn=True)
        JobWorker(store, run_job=_runner(settings)).process_next()

        failed = store.get_job(job.id)
        assert failed is not None and failed.status == JobStatus.FAILED
        assert failed.error.startswith("Not enough free space")  # type: ignore[union-attr]
        assert burned["called"] is False
        assert (settings.out_dir / "clip" / "clip.srt").is_file()  # captions still delivered


class TestProbeOnce:
    def test_play_res_and_burn_duration_come_from_the_probe(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[Path] = []

        def fake_probe(video_path, *, ffprobe_path=None):
            probes.append(Path(video_path))
            return engine.VideoInfo(1920, 1080, 25.0, 5400.0)

        monkeypatch.setattr(engine, "probe_video", fake_probe)
        received: dict = {}
        monkeypatch.setattr(engine, "burn_captions", _fake_burn(received))
        settings = make_settings(tmp_path)
        settings.punch_mode = "sentence"
        output_dir = settings.out_dir / "clip"
        job = make_job(store, _video(tmp_path), output_dir, burn=True)
        run_to_completion(_runner(settings, FakeTranscriber(_result(["Hello.", "there", "friend"]))), job)

        assert len(probes) == 1  # once per job, even with punch-in on
        assert received["duration"] == 5400.0
        ass = (output_dir / "clip.ass").read_text(encoding="utf-8")
        assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass

    def test_transcript_end_is_the_duration_fallback(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def explode(*a, **k):
            raise engine.ProbeError("no ffprobe")

        monkeypatch.setattr(engine, "probe_video", explode)
        received: dict = {}
        monkeypatch.setattr(engine, "burn_captions", _fake_burn(received))
        settings = make_settings(tmp_path)
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip", burn=True)
        run_to_completion(_runner(settings, FakeTranscriber(_result(["a", "b", "c"], step=0.5))), job)
        assert received["duration"] == pytest.approx(1.5)


class TestGlossaryLoadedOnce:
    def test_glossary_file_is_read_once_per_job_when_postprocess_accepts_entries(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from ash_captions import languages

        settings = make_settings(tmp_path)
        settings.glossary_dir.mkdir(parents=True)
        (settings.glossary_dir / "glossary.txt").write_text("Gazi => Ghazi\n", encoding="utf-8")
        loads: list[Path] = []
        real_load = languages.load_glossary

        def counting_load(path):
            loads.append(Path(path))
            return real_load(path)

        monkeypatch.setattr(languages, "load_glossary", counting_load)
        calls: list = []

        def fake_postprocess(text, resolved, client_glossary_path=None, *, entries=None):
            calls.append(entries)
            return text

        monkeypatch.setattr(languages, "postprocess", fake_postprocess)
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip")
        run_to_completion(_runner(settings, FakeTranscriber(_result(["hello", "Gazi", "friend"]))), job)

        assert loads == [settings.glossary_dir / "glossary.txt"]
        assert calls and all(entries == calls[0] for entries in calls)
        assert calls[0] and calls[0][0].replacement == "Ghazi"
