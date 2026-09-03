"""Tests for runner.build_run_job: the progress budget, the postprocess
bridge into engine's Word/Segment types, output writing, and style/punch
wiring. The input-deletion matrix, cancellation, stage reporting, disk
space and upload handling live in test_runner_inputs.py.

A fake Transcriber and monkeypatched extract_audio/burn_captions mean none
of this needs faster-whisper, ffmpeg, or a GPU.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions import engine, languages, styles
from ash_captions.app.runner import _postprocess_segments, _postprocess_words, _progress_budget, build_run_job
from ash_captions.config import Settings
from ash_captions.pipeline.db import JobOptions, JobStore


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        in_dir=tmp_path / "in",
        out_dir=tmp_path / "out",
        db_path=tmp_path / "jobs.db",
        log_path=tmp_path / "log.txt",
        glossary_dir=tmp_path / "glossaries",
        upload_dir=tmp_path / "uploads",
        tmp_dir=tmp_path / "tmp",
        min_free_disk_gb=0,
    )


def _result(word_texts: list[str], *, start: float = 0.0, step: float = 0.5, language: str = "en"):
    words = []
    t = start
    for text in word_texts:
        words.append(engine.Word(text=text, start=t, end=t + step, probability=1.0))
        t += step
    segment = engine.Segment(
        text=" ".join(word_texts), start=words[0].start, end=words[-1].end, words=tuple(words)
    )
    return engine.TranscriptionResult(segments=(segment,), language=language)


class FakeTranscriber:
    """Implements engine.Transcriber *without* the newer on_progress /
    should_stop keywords -- the runner must cope with an older engine."""

    def __init__(self, transcribe_result, translate_result=None) -> None:
        self.transcribe_result = transcribe_result
        self.translate_result = translate_result
        self.calls: list[tuple] = []

    def transcribe(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
        self.calls.append(("transcribe", language, initial_prompt))
        return self.transcribe_result

    def translate(self, audio_path, *, language=None, initial_prompt=None, vad_filter=True):
        self.calls.append(("translate", language, initial_prompt))
        return self.translate_result


def run_to_completion(run_job, job, report=lambda _p: None):
    """What JobWorker does: run, then (after mark_done) run the returned cleanup."""
    after_done = run_job(job, report)
    if after_done is not None:
        after_done()
    return after_done


@pytest.fixture(autouse=True)
def fake_extract_audio(monkeypatch: pytest.MonkeyPatch):
    def _fake(video_path, output_path, *, ffmpeg_path=None):
        Path(output_path).write_bytes(b"RIFF")
        return Path(output_path)

    monkeypatch.setattr(engine, "extract_audio", _fake)


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def make_job(store: JobStore, input_path: Path, output_dir: Path, **option_overrides):
    fields = dict(language="en", dialect=None, preset="POP", burn=False, translate=False)
    fields.update(option_overrides)
    job_id = store.insert_job(input_path, output_dir, JobOptions(**fields))
    return store.get_job(job_id)


def _video(tmp_path: Path, folder: str = "footage") -> Path:
    video = tmp_path / folder / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"fake video")
    return video


def _fake_burn(received: dict | None = None):
    def fake_burn_captions(
        video_path, ass_path, output_path, *,
        duration_seconds, ffmpeg_path=None, fontsdir=None, on_progress=None,
        use_nvenc=None, punch_filter=None, **_extra,
    ):
        if received is not None:
            received.update(dict(fontsdir=fontsdir, punch_filter=punch_filter, duration=duration_seconds, extra=_extra))
        Path(output_path).write_bytes(b"fake mp4")
        if on_progress is not None:
            on_progress(0.0)
            on_progress(100.0)
        return Path(output_path)

    return fake_burn_captions


class TestProgressBudget:
    def test_always_spans_0_to_100(self) -> None:
        for translate in (False, True):
            for burn in (False, True):
                budget = _progress_budget(translate=translate, burn=burn)
                assert max(budget.values(), key=lambda span: span[1])[1] == 100
                assert min(start for start, _ in budget.values()) == 0

    def test_transcription_owns_the_largest_share_of_the_bar(self) -> None:
        budget = _progress_budget(translate=True, burn=True)
        spans = {name: end - start for name, (start, end) in budget.items()}
        assert spans["transcribe"] == max(spans.values())

    def test_disabled_stages_are_absent(self) -> None:
        budget = _progress_budget(translate=False, burn=False)
        assert "translate" not in budget and "burn" not in budget
        assert "extract" in budget and "transcribe" in budget


class TestPostprocessBridge:
    def test_postprocess_words_applies_spelling_convention_per_word(self) -> None:
        resolved = languages.resolve("en", "uk")
        words = (engine.Word(text="color", start=0.0, end=0.5),)
        result = _postprocess_words(words, resolved, Path("does-not-exist.txt"))
        assert result[0].text.lower() == "colour"
        assert result[0].start == 0.0 and result[0].end == 0.5

    def test_postprocess_segments_applies_glossary_correction(self, tmp_path: Path) -> None:
        glossary_path = tmp_path / "glossary.txt"
        glossary_path.write_text("Gazi => Ghazi\n", encoding="utf-8")
        resolved = languages.resolve("en", "us")
        segment = engine.Segment(text="hello Gazi", start=0.0, end=1.0, words=())
        result = _postprocess_segments((segment,), resolved, glossary_path)
        assert "Ghazi" in result[0].text

    def test_preloaded_entries_are_forwarded_when_postprocess_accepts_them(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list = []

        def fake_postprocess(text, resolved, client_glossary_path=None, *, entries=None):
            seen.append(entries)
            return text

        monkeypatch.setattr(languages, "postprocess", fake_postprocess)
        resolved = languages.resolve("en", "us")
        words = (engine.Word(text="hi", start=0.0, end=0.5),)
        _postprocess_words(words, resolved, tmp_path / "g.txt", entries=("preloaded",))
        assert seen == [("preloaded",)]


    def test_batch_postprocess_words_is_preferred_when_languages_offers_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """languages.postprocess_words (one call per transcript, phrase
        matches across word boundaries) is used when present; the texts
        are zipped back onto the timed Word tuples."""
        seen: dict = {}

        def fake_batch(texts, resolved, client_glossary_path=None, *, entries=None):
            seen["texts"] = list(texts)
            seen["entries"] = entries
            return tuple(t.upper() for t in texts)

        monkeypatch.setattr(languages, "postprocess_words", fake_batch, raising=False)
        resolved = languages.resolve("en", "us")
        words = (engine.Word(text="hello", start=0.0, end=0.5), engine.Word(text="there", start=0.5, end=1.0))

        result = _postprocess_words(words, resolved, tmp_path / "g.txt", entries=("e",))

        assert seen == {"texts": ["hello", "there"], "entries": ("e",)}
        assert [w.text for w in result] == ["HELLO", "THERE"]
        assert [(w.start, w.end) for w in result] == [(0.0, 0.5), (0.5, 1.0)]


class TestRunJobOutputs:
    def test_writes_srt_ass_txt_and_reports_completion(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = _video(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, video, output_dir)
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["hello", "there", "friend", "how", "are", "you"])))

        progress: list[int] = []
        run_to_completion(run_job, job, progress.append)

        for suffix in (".srt", ".ass", ".txt"):
            assert (output_dir / f"clip{suffix}").is_file()
            assert not (output_dir / f"clip{suffix}.part").exists()  # atomic: no .part left
        assert not (output_dir / "clip.en.srt").exists()
        assert (output_dir / ".ash-captions-job").read_text(encoding="utf-8").strip() == str(job.id)
        assert progress[-1] == 100
        assert all(0 <= p <= 100 for p in progress)
        assert list(settings.tmp_dir.iterdir()) == []  # per-job scratch cleaned up

    def test_translate_flag_writes_en_srt(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = _video(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, video, output_dir, translate=True)
        transcriber = FakeTranscriber(
            transcribe_result=_result(["hola", "amigo", "como", "estas"], language="es"),
            translate_result=_result(["hello", "friend", "how", "are", "you"], language="en"),
        )
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber), job)

        assert (output_dir / "clip.en.srt").is_file()
        assert any(call[0] == "translate" and call[1] == "en" for call in transcriber.calls)

    def test_burn_flag_invokes_burn_captions_with_progress_in_its_own_slice(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = make_settings(tmp_path)
        video = _video(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, video, output_dir, burn=True)
        monkeypatch.setattr(engine, "burn_captions", _fake_burn())
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["hello", "there", "friend", "how", "are", "you"])))

        progress: list[int] = []
        run_to_completion(run_job, job, progress.append)

        assert (output_dir / "clip.captioned.mp4").is_file()
        assert progress[-1] == 100
        assert progress[-2] > 0  # burn's on_progress(0.0) maps inside its slice

    def test_burn_passes_the_bundled_fontsdir_to_burn_captions(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = make_settings(tmp_path)
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip", burn=True)
        received: dict = {}
        monkeypatch.setattr(engine, "burn_captions", _fake_burn(received))
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["hello", "there", "friend"]))), job)
        assert received["fontsdir"] == styles.fontsdir_arg()


class TestStyleWiring:
    def test_hype_style_produces_shake_tags_and_respects_its_max_words(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, _video(tmp_path), output_dir, preset="HYPE")
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["one", "two", "three", "four", "five", "six"]))), job)

        ass_content = (output_dir / "clip.ass").read_text(encoding="utf-8")
        assert "\\frz" in ass_content
        assert "HYPE" in ass_content
        text_lines = [
            line for line in (output_dir / "clip.srt").read_text(encoding="utf-8").splitlines()
            if line and not line[0].isdigit() and "-->" not in line
        ]
        assert text_lines and all(len(line.split()) <= 2 for line in text_lines)

    def test_neon_glow_style_produces_glow_tags(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, _video(tmp_path), output_dir, preset="NEON GLOW")
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["one", "two", "three", "four", "five"]))), job)
        ass_content = (output_dir / "clip.ass").read_text(encoding="utf-8")
        assert "\\blur" in ass_content and "NEON_GLOW" in ass_content

    def test_unknown_style_name_falls_back_without_failing_the_job(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, _video(tmp_path), output_dir, preset="DOES-NOT-EXIST")
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["hello", "there", "friend"]))), job)
        assert (output_dir / "clip.ass").is_file()


class TestSilenceGapWiring:
    @staticmethod
    def _result_with_isolated_word() -> "engine.TranscriptionResult":
        words = (
            engine.Word(text="hello", start=0.0, end=0.5),
            engine.Word(text="there", start=0.5, end=1.0),
            engine.Word(text="lonely", start=1.8, end=2.3),
            engine.Word(text="friend", start=3.1, end=3.6),
            engine.Word(text="again", start=3.6, end=4.1),
        )
        segment = engine.Segment(text=" ".join(w.text for w in words), start=0.0, end=4.1, words=words)
        return engine.TranscriptionResult(segments=(segment,), language="en")

    def test_default_silence_gap_keeps_the_isolated_word(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        output_dir = settings.out_dir / "clip"
        job = make_job(store, _video(tmp_path), output_dir)
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(self._result_with_isolated_word())), job)
        assert "lonely" in (output_dir / "clip.srt").read_text(encoding="utf-8").lower()

    def test_a_tighter_configured_silence_gap_drops_the_isolated_word(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        settings.silence_gap_seconds = 0.5
        output_dir = settings.out_dir / "clip"
        job = make_job(store, _video(tmp_path), output_dir)
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(self._result_with_isolated_word())), job)
        srt_content = (output_dir / "clip.srt").read_text(encoding="utf-8").lower()
        assert "lonely" not in srt_content and "hello" in srt_content


class TestPunchInWiring:
    def test_punch_filter_reaches_burn_when_enabled(self, tmp_path, store, monkeypatch):
        received: dict = {}
        monkeypatch.setattr(engine, "burn_captions", _fake_burn(received))
        monkeypatch.setattr(engine, "probe_video", lambda *_a, **_k: engine.VideoInfo(1080, 1920, 30.0, 60.0))
        settings = make_settings(tmp_path)
        settings.punch_mode = "sentence"
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip", burn=True)
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["Hello.", "there", "friend", "how"]))), job)

        punch_filter = received["punch_filter"]
        assert punch_filter  # a non-empty ffmpeg filter chain reached the burn
        # The timestamp-preserving scale/crop chain works in iw/ih and t, so
        # it carries no literal geometry; the probe still feeds moment
        # selection (video_duration) and the burn's progress duration.
        assert "crop=" in punch_filter and "scale=" in punch_filter
        assert "eval=frame" in punch_filter

    def test_punch_is_off_by_default(self, tmp_path, store, monkeypatch):
        received: dict = {}
        monkeypatch.setattr(engine, "burn_captions", _fake_burn(received))
        settings = make_settings(tmp_path)
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip", burn=True)
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["Hello.", "there", "friend"]))), job)
        assert received["punch_filter"] is None

    def test_a_broken_probe_still_produces_captions(self, tmp_path, store, monkeypatch):
        received: dict = {}

        def explode(*_a, **_k):
            raise engine.ProbeError("ffprobe is missing")

        monkeypatch.setattr(engine, "burn_captions", _fake_burn(received))
        monkeypatch.setattr(engine, "probe_video", explode)
        settings = make_settings(tmp_path)
        settings.punch_mode = "sentence"
        job = make_job(store, _video(tmp_path), settings.out_dir / "clip", burn=True)
        run_to_completion(build_run_job(settings, watch_dir=settings.in_dir, transcriber=FakeTranscriber(_result(["Hello.", "there", "friend"]))), job)

        assert received["punch_filter"] is None
        assert (Path(job.output_dir) / "clip.captioned.mp4").exists()
