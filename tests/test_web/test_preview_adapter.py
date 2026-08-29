"""Tests for InProcessPreviewRenderer's job bookkeeping, phase reporting,
and transcription caching (spec 7A.3) -- with `transcriber`/
`extract_window_audio`/`run_ffmpeg` all injected, so no ffmpeg, no whisper
model, and no real filesystem rendering happen here."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ash_captions.engine import Segment, TranscriptionError, TranscriptionResult, Word
from ash_captions.web.interfaces import StyleValidationFailedError
from ash_captions.web.models import PreviewStatus
from ash_captions.web.preview_adapter import InProcessPreviewRenderer

from .fakes import default_style_definition


@dataclass
class _FakeTranscriber:
    calls: list[Path] = field(default_factory=list)
    words: tuple[Word, ...] = (Word(text="hi", start=0.0, end=0.3), Word(text="there", start=0.3, end=0.7))
    fail: bool = False

    def transcribe(self, audio_path, **kwargs) -> TranscriptionResult:
        self.calls.append(Path(audio_path))
        if self.fail:
            raise TranscriptionError("model exploded")
        segment = Segment(text=" ".join(w.text for w in self.words), start=0.0, end=self.words[-1].end, words=self.words)
        return TranscriptionResult(segments=(segment,), language="en")

    def translate(self, audio_path, **kwargs) -> TranscriptionResult:  # pragma: no cover - unused here
        raise NotImplementedError


def _noop_extract(video_path, out_wav, start_seconds, duration_seconds, ffmpeg_path) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    out_wav.write_bytes(b"fake wav")


def _make_renderer(tmp_path, *, transcriber=None, run_ffmpeg=None) -> tuple[InProcessPreviewRenderer, _FakeTranscriber]:
    fake_transcriber = transcriber or _FakeTranscriber()
    calls = []

    def default_run_ffmpeg(args):
        calls.append(args)
        # simulate the clip landing on disk, same as real ffmpeg would
        Path(args[-1]).write_bytes(b"clip bytes")

    renderer = InProcessPreviewRenderer(
        transcriber=fake_transcriber,
        work_dir=tmp_path / "previews",
        extract_window_audio=_noop_extract,
        run_ffmpeg=run_ffmpeg or default_run_ffmpeg,
    )
    return renderer, fake_transcriber


def _wait_until_finished(renderer, job_id, timeout=5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = renderer.get_preview(job_id)
        if job.status in (PreviewStatus.DONE, PreviewStatus.FAILED):
            return
        time.sleep(0.02)
    raise AssertionError(f"preview job {job_id} never finished")


def test_submit_preview_rejects_invalid_style_before_starting_a_thread(tmp_path):
    renderer, transcriber = _make_renderer(tmp_path)
    bad_style = default_style_definition("BAD")
    bad_style["font"] = "Comic Sans MS"

    with pytest.raises(StyleValidationFailedError):
        renderer.submit_preview(tmp_path / "clip.mp4", 0.0, bad_style)

    assert transcriber.calls == []


def test_preview_job_completes_and_reports_phases(tmp_path):
    renderer, _ = _make_renderer(tmp_path)

    job = renderer.submit_preview(tmp_path / "clip.mp4", 1.5, default_style_definition("POP"))
    assert job.status == PreviewStatus.PENDING

    _wait_until_finished(renderer, job.id)

    finished = renderer.get_preview(job.id)
    assert finished.status == PreviewStatus.DONE
    assert finished.clip_path
    assert Path(finished.clip_path).read_bytes() == b"clip bytes"


def test_transcription_failure_marks_job_failed_not_crashed(tmp_path):
    renderer, _ = _make_renderer(tmp_path, transcriber=_FakeTranscriber(fail=True))

    job = renderer.submit_preview(tmp_path / "clip.mp4", 0.0, default_style_definition("POP"))
    _wait_until_finished(renderer, job.id)

    failed = renderer.get_preview(job.id)
    assert failed.status == PreviewStatus.FAILED
    assert "exploded" in failed.error


def test_repeated_preview_at_same_video_and_timestamp_reuses_transcription(tmp_path):
    """The editor flips through styles at the same spot far more than they
    change the timestamp -- re-transcribing on every style change would make
    that feel slow for no reason."""
    renderer, transcriber = _make_renderer(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"real video bytes")

    job1 = renderer.submit_preview(video, 2.0, default_style_definition("POP"))
    _wait_until_finished(renderer, job1.id)
    job2 = renderer.submit_preview(video, 2.0, default_style_definition("CLEAN"))
    _wait_until_finished(renderer, job2.id)

    assert renderer.get_preview(job1.id).status == PreviewStatus.DONE
    assert renderer.get_preview(job2.id).status == PreviewStatus.DONE
    assert len(transcriber.calls) == 1  # second preview hit the cache


def test_different_timestamp_does_not_reuse_transcription(tmp_path):
    renderer, transcriber = _make_renderer(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"real video bytes")

    _wait_until_finished(renderer, renderer.submit_preview(video, 2.0, default_style_definition("POP")).id)
    _wait_until_finished(renderer, renderer.submit_preview(video, 9.0, default_style_definition("POP")).id)

    assert len(transcriber.calls) == 2


def test_reexporting_the_video_at_the_same_path_invalidates_the_cache(tmp_path):
    """A stale transcript would be worse than a slow one: if the editor
    re-exports over the same path, the cache must not serve the old words."""
    renderer, transcriber = _make_renderer(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"first export")
    original_mtime = video.stat().st_mtime

    _wait_until_finished(renderer, renderer.submit_preview(video, 2.0, default_style_definition("POP")).id)

    # Force a distinguishable mtime -- some filesystems have coarse mtime
    # resolution, so nudge it forward explicitly rather than relying on
    # real wall-clock time passing between writes.
    video.write_bytes(b"re-exported, different content")
    os.utime(video, (original_mtime + 5, original_mtime + 5))

    _wait_until_finished(renderer, renderer.submit_preview(video, 2.0, default_style_definition("POP")).id)

    assert len(transcriber.calls) == 2  # the re-export was not served from cache


def test_transcription_cache_is_bounded(tmp_path):
    """Bounded LRU: exploring many distinct timestamps in one long session
    must not grow the cache without limit."""
    from ash_captions.web.preview_adapter import MAX_TRANSCRIPTION_CACHE_ENTRIES

    renderer, transcriber = _make_renderer(tmp_path)
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"real video bytes")

    for i in range(MAX_TRANSCRIPTION_CACHE_ENTRIES + 5):
        _wait_until_finished(renderer, renderer.submit_preview(video, float(i), default_style_definition("POP")).id)

    assert len(renderer._transcription_cache) == MAX_TRANSCRIPTION_CACHE_ENTRIES

    # The earliest timestamps were evicted -- revisiting one re-transcribes.
    calls_before = len(transcriber.calls)
    _wait_until_finished(renderer, renderer.submit_preview(video, 0.0, default_style_definition("POP")).id)
    assert len(transcriber.calls) == calls_before + 1


def test_get_unknown_preview_raises(tmp_path):
    from ash_captions.web.interfaces import PreviewNotFoundError

    renderer, _ = _make_renderer(tmp_path)
    with pytest.raises(PreviewNotFoundError):
        renderer.get_preview("does-not-exist")
