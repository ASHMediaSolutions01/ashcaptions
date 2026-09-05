"""The Studio's pipeline side: a full job saves its transcript beside the
outputs; a burn_only job re-renders from it without transcribing; the queue
adapter can restyle in place and enqueue a burn-only job."""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions import engine
from ash_captions.app.adapter import QueueAdapter
from ash_captions.app.runner import build_run_job
from ash_captions.app.transcript import (
    SourceStamp,
    TranscriptError,
    TranscriptRecord,
    load_transcript,
    save_transcript,
    transcript_path,
)
from ash_captions.config import Settings
from ash_captions.pipeline.db import JobOptions, JobStore
from ash_captions.web.interfaces import JobNotFoundError


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        in_dir=tmp_path / "in", out_dir=tmp_path / "out", db_path=tmp_path / "jobs.db",
        log_path=tmp_path / "log.txt", glossary_dir=tmp_path / "glossaries",
        upload_dir=tmp_path / "uploads", tmp_dir=tmp_path / "tmp", min_free_disk_gb=0,
    )


def _result(texts: list[str], *, step: float = 0.5):
    words, t = [], 0.0
    for text in texts:
        words.append(engine.Word(text=text, start=t, end=t + step))
        t += step
    seg = engine.Segment(text=" ".join(texts), start=0.0, end=t, words=tuple(words))
    return engine.TranscriptionResult(segments=(seg,), language="en")


class CountingTranscriber:
    def __init__(self, result=None):
        self.result = result or _result(["hello", "there", "friend", "how", "are", "you"])
        self.calls = 0

    def transcribe(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
        self.calls += 1
        return self.result

    translate = transcribe


@pytest.fixture(autouse=True)
def no_real_media(monkeypatch: pytest.MonkeyPatch):
    def fake_extract(video_path, output_path, *, ffmpeg_path=None):
        Path(output_path).write_bytes(b"RIFF")
        return Path(output_path)

    def fake_burn(video_path, ass_path, output_path, *, duration_seconds, **_):
        Path(output_path).write_bytes(b"fake mp4")
        return Path(output_path)

    monkeypatch.setattr(engine, "extract_audio", fake_extract)
    monkeypatch.setattr(engine, "burn_captions", fake_burn)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def _video(tmp_path: Path) -> Path:
    video = tmp_path / "footage" / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake video")
    return video


def _job(store: JobStore, video: Path, out: Path, **overrides):
    fields = dict(language="en", dialect=None, preset="POP", burn=False, translate=False)
    fields.update(overrides)
    return store.get_job(store.insert_job(video, out, JobOptions(**fields)))


class _Reporter:
    def __call__(self, p): pass
    def stage(self, name): pass
    def should_stop(self): return False


# -- transcript file ----------------------------------------------------------


def test_transcript_round_trips_and_notices_a_changed_source(tmp_path: Path):
    video = _video(tmp_path)
    words = (engine.Word("Hi", 0.0, 0.4), engine.Word("there", 0.4, 0.9, probability=0.5))
    rec = TranscriptRecord(
        language="en", dialect="en-US", words=words,
        segments=(engine.Segment("Hi there", 0.0, 0.9, words),),
        en_words=None, play_res=(1920, 1080), source=SourceStamp.of(video),
    )
    path = save_transcript(transcript_path(tmp_path, "clip"), rec)
    back = load_transcript(path)
    assert back.words == words and back.segments[0].text == "Hi there"
    assert back.play_res == (1920, 1080) and back.en_words is None
    assert back.matches(video)
    video.write_bytes(b"a re-export with different bytes!")
    assert not back.matches(video)


def test_load_transcript_rejects_garbage(tmp_path: Path):
    bad = tmp_path / "x.transcript.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(TranscriptError):
        load_transcript(bad)
    with pytest.raises(TranscriptError):
        load_transcript(tmp_path / "missing.transcript.json")


# -- runner -------------------------------------------------------------------


def test_full_job_saves_a_transcript_beside_the_outputs(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    job = _job(store, video, tmp_path / "out" / "clip")
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(job, _Reporter())
    rec = load_transcript(transcript_path(tmp_path / "out" / "clip", "clip"))
    assert [w.text for w in rec.words] == ["hello", "there", "friend", "how", "are", "you"]
    assert rec.matches(video)


def test_burn_only_job_reuses_the_transcript_and_never_transcribes(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    out = tmp_path / "out" / "clip"
    first = CountingTranscriber()
    first_job = _job(store, video, out)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=first)(first_job, _Reporter())
    assert first.calls == 1
    store.mark_done(first_job.id)  # a live row for the same input would be deduped into

    second = CountingTranscriber()
    burn_job = _job(store, video, out, preset="HYPE", burn=True, mode="burn_only")
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=second)(burn_job, _Reporter())

    assert second.calls == 0
    assert "Style: HYPE" in (out / "clip.ass").read_text(encoding="utf-8")
    assert (out / "clip.captioned.mp4").is_file()


def test_burn_only_without_a_transcript_fails_with_a_plain_message(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    job = _job(store, video, tmp_path / "out" / "clip", burn=True, mode="burn_only")
    with pytest.raises(RuntimeError, match="No saved transcript"):
        build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(job, _Reporter())


def test_full_job_transcribes_again_when_the_source_changed(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    out = tmp_path / "out" / "clip"
    first_job = _job(store, video, out)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(first_job, _Reporter())
    store.mark_done(first_job.id)
    video.write_bytes(b"re-exported, different bytes")
    again = CountingTranscriber()
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=again)(_job(store, video, out), _Reporter())
    assert again.calls == 1


def test_full_job_with_translation_does_not_reuse_a_transcript_lacking_english(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    out = tmp_path / "out" / "clip"
    first_job = _job(store, video, out)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(first_job, _Reporter())
    store.mark_done(first_job.id)
    again = CountingTranscriber()
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=again)(_job(store, video, out, translate=True), _Reporter())
    assert again.calls == 2  # transcribe + translate


# -- queue adapter --------------------------------------------------------------


def _done_job_with_transcript(tmp_path: Path, store: JobStore):
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    out = tmp_path / "out" / "clip"
    job = _job(store, video, out)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(job, _Reporter())
    store.mark_done(job.id)
    return job, out


def test_restyle_rewrites_the_ass_and_records_the_preset(tmp_path: Path, store: JobStore):
    job, out = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)

    updated = adapter.restyle(str(job.id), "COMIC")

    assert updated.options.preset == "COMIC"
    assert "Style: COMIC" in (out / "clip.ass").read_text(encoding="utf-8")
    assert store.get_job(job.id).options.preset == "COMIC"


def test_two_tabs_restyling_the_same_job_at_once_both_succeed(tmp_path: Path, store: JobStore):
    """Two Studio tabs on one job: each restyle writes its own temp file, so
    neither hits the other's half-written .part (PermissionError on rename)."""
    import threading

    job, out = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)
    errors: list[BaseException] = []

    def go(preset: str) -> None:
        try:
            for _ in range(15):
                adapter.restyle(str(job.id), preset)
        except BaseException as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(p,)) for p in ("COMIC", "POP", "CLEAN")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert "[Script Info]" in (out / "clip.ass").read_text(encoding="utf-8")
    assert not list(out.glob("*.part"))


def test_restyle_refuses_unknown_style_and_unknown_job(tmp_path: Path, store: JobStore):
    job, _ = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="Unknown caption style"):
        adapter.restyle(str(job.id), "NOT A STYLE")
    with pytest.raises(JobNotFoundError):
        adapter.restyle("999", "POP")


def test_restyle_needs_a_saved_transcript(tmp_path: Path, store: JobStore):
    video = _video(tmp_path)
    job = _job(store, video, tmp_path / "out" / "clip")
    store.mark_done(job.id)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    with pytest.raises(ValueError, match="no saved transcript"):
        adapter.restyle(str(job.id), "POP")


def test_submit_burn_enqueues_a_burn_only_job_into_the_same_folder(tmp_path: Path, store: JobStore):
    job, out = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")

    created = adapter.submit_burn(str(job.id), "NEON GLOW")

    row = store.get_job(int(created.id))
    assert row.id != job.id
    assert row.options.mode == "burn_only" and row.options.burn is True and row.options.preset == "NEON GLOW"
    assert Path(row.output_dir) == out and row.input_path == job.input_path


def test_translate_pass_gets_no_source_dialect_prompt(tmp_path: Path, store: JobStore):
    """The es-MX priming prompt made Whisper leave chunks of the English
    translation in Spanish on a real interview; translate must run without it."""
    settings = _settings(tmp_path)
    video = _video(tmp_path)

    class Recording(CountingTranscriber):
        def __init__(self):
            super().__init__()
            self.prompts = {}

        def transcribe(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
            self.prompts["transcribe"] = initial_prompt
            return self.result

        def translate(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
            self.prompts["translate"] = initial_prompt
            return self.result

    rec = Recording()
    job = _job(store, video, tmp_path / "out" / "clip", language="es", dialect="es-MX", translate=True)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=rec)(job, _Reporter())
    assert rec.prompts["transcribe"], "the source pass keeps its dialect priming"
    assert rec.prompts["translate"] is None


def test_atomic_write_survives_a_racing_rename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Windows fails the loser of two simultaneous replacements of one
    destination with PermissionError even though both files are fine; the
    write retries instead of surfacing it as a 500 in a Studio tab."""
    from ash_captions.app import runner_util

    target = tmp_path / "clip.ass"
    calls: list[int] = []
    real_replace = runner_util.os.replace

    def flaky_replace(src, dst):
        calls.append(1)
        if len(calls) <= 2:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(runner_util.os, "replace", flaky_replace)
    monkeypatch.setattr(runner_util, "REPLACE_BACKOFF_SECONDS", 0.001)

    runner_util.atomic_write(lambda p: p.write_text("x", encoding="utf-8"), target)

    assert target.read_text(encoding="utf-8") == "x"
    assert len(calls) == 3


def test_atomic_write_gives_up_and_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from ash_captions.app import runner_util

    def always_denied(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(runner_util.os, "replace", always_denied)
    monkeypatch.setattr(runner_util, "REPLACE_BACKOFF_SECONDS", 0.0)

    with pytest.raises(PermissionError):
        runner_util.atomic_write(lambda p: p.write_text("x", encoding="utf-8"), tmp_path / "clip.ass")

# -- caption position (v0.5) ---------------------------------------------------


def _ass_events(out: Path) -> list[str]:
    text = (out / "clip.ass").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("Dialogue:")]


def test_restyle_with_a_position_pins_every_event_and_stores_it(tmp_path: Path, store: JobStore):
    job, out = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)

    updated = adapter.restyle(str(job.id), "COMIC", position=(0.5, 0.25))

    assert (updated.options.caption_x, updated.options.caption_y) == (0.5, 0.25)
    assert store.get_job(job.id).options.caption_position == (0.5, 0.25)
    events = _ass_events(out)
    # No ffprobe in tests, so the transcript has no play_res and the .ass
    # uses the 1080x1920 default: (0.5, 0.25) -> (540, 480).
    assert events and all("540,480" in line for line in events), events[:3]


def test_restyle_without_a_position_keeps_it_and_none_clears_it(tmp_path: Path, store: JobStore):
    job, out = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)
    adapter.restyle(str(job.id), "COMIC", position=(0.5, 0.25))

    kept = adapter.restyle(str(job.id), "POP")  # picking another look keeps the position
    assert kept.options.preset == "POP"
    assert (kept.options.caption_x, kept.options.caption_y) == (0.5, 0.25)
    assert all("540,480" in line for line in _ass_events(out))

    cleared = adapter.restyle(str(job.id), "POP", position=None)
    assert cleared.options.caption_x is None and cleared.options.caption_y is None
    assert store.get_job(job.id).options.caption_position is None
    assert not any("\\pos(" in line for line in _ass_events(out))


def test_submit_burn_carries_the_position_into_the_burn_only_job(tmp_path: Path, store: JobStore):
    job, out = _done_job_with_transcript(tmp_path, store)
    adapter = QueueAdapter(store, out_dir=tmp_path / "out")
    adapter._settings = _settings(tmp_path)
    adapter.restyle(str(job.id), "COMIC", position=(0.5, 0.25))

    created = adapter.submit_burn(str(job.id), "NEON GLOW")

    row = store.get_job(int(created.id))
    assert row.options.mode == "burn_only" and row.options.preset == "NEON GLOW"
    assert row.options.caption_position == (0.5, 0.25)
    assert (created.options.caption_x, created.options.caption_y) == (0.5, 0.25)


def test_burn_only_job_renders_the_stored_caption_position(tmp_path: Path, store: JobStore):
    """The burn path renders the .ass again from the transcript (runner, not
    the adapter), so it must apply the position the Studio stored."""
    settings = _settings(tmp_path)
    video = _video(tmp_path)
    out = tmp_path / "out" / "clip"
    first_job = _job(store, video, out)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(first_job, _Reporter())
    store.mark_done(first_job.id)

    burn_job = _job(store, video, out, burn=True, mode="burn_only", caption_x=0.5, caption_y=0.25)
    build_run_job(settings, watch_dir=settings.in_dir, transcriber=CountingTranscriber())(burn_job, _Reporter())

    events = _ass_events(out)
    assert events and all("540,480" in line for line in events), events[:3]
    assert (out / "clip.captioned.mp4").is_file()
