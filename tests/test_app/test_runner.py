"""Tests for runner.build_run_job: the progress budget, the postprocess
bridge into engine's Word/Segment types, and -- the most important case
in this whole package -- that the watch-folder-only input deletion rule
(spec section 10, 12) is actually enforced.

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
    """Implements engine.Transcriber without faster-whisper."""

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


@pytest.fixture(autouse=True)
def fake_extract_audio(monkeypatch: pytest.MonkeyPatch):
    """No real ffmpeg -- FakeTranscriber never reads the audio file, so
    extraction only needs to not blow up."""

    def _fake(video_path, output_path, *, ffmpeg_path=None):
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


class TestProgressBudget:
    def test_always_spans_0_to_100(self) -> None:
        for translate in (False, True):
            for burn in (False, True):
                budget = _progress_budget(translate=translate, burn=burn)
                last_stage = max(budget.values(), key=lambda span: span[1])
                assert last_stage[1] == 100
                assert min(start for start, _ in budget.values()) == 0

    def test_transcription_owns_the_largest_share_of_the_bar(self) -> None:
        budget = _progress_budget(translate=True, burn=True)
        spans = {name: end - start for name, (start, end) in budget.items()}
        assert spans["transcribe"] == max(spans.values())

    def test_disabled_stages_are_absent(self) -> None:
        budget = _progress_budget(translate=False, burn=False)
        assert "translate" not in budget
        assert "burn" not in budget
        assert "extract" in budget and "transcribe" in budget


class TestPostprocessBridge:
    def test_postprocess_words_applies_spelling_convention_per_word(self) -> None:
        resolved = languages.resolve("en", "uk")  # American -> British spelling
        words = (engine.Word(text="color", start=0.0, end=0.5),)

        result = _postprocess_words(words, resolved, Path("does-not-exist.txt"))

        assert result[0].text.lower() == "colour"
        assert result[0].start == 0.0 and result[0].end == 0.5  # timing preserved

    def test_postprocess_segments_applies_glossary_correction(self, tmp_path: Path) -> None:
        glossary_path = tmp_path / "glossary.txt"
        glossary_path.write_text("Gazi => Ghazi\n", encoding="utf-8")
        resolved = languages.resolve("en", "us")
        segment = engine.Segment(text="hello Gazi", start=0.0, end=1.0, words=())

        result = _postprocess_segments((segment,), resolved, glossary_path)

        assert "Ghazi" in result[0].text


class TestRunJobOutputs:
    def test_writes_srt_ass_txt_and_reports_completion(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend", "how", "are", "you"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        progress: list[int] = []
        run_job(job, progress.append)

        assert (output_dir / "clip.srt").is_file()
        assert (output_dir / "clip.ass").is_file()
        assert (output_dir / "clip.txt").is_file()
        assert not (output_dir / "clip.en.srt").exists()
        assert progress[-1] == 100
        assert all(0 <= p <= 100 for p in progress)

    def test_translate_flag_writes_en_srt(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir, translate=True)
        transcriber = FakeTranscriber(
            transcribe_result=_result(["hola", "amigo", "como", "estas"], language="es"),
            translate_result=_result(["hello", "friend", "how", "are", "you"], language="en"),
        )
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        run_job(job, lambda _p: None)

        assert (output_dir / "clip.en.srt").is_file()
        assert any(call[0] == "translate" and call[1] == "en" for call in transcriber.calls)

    def test_burn_flag_invokes_burn_captions_with_progress_in_its_own_slice(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir, burn=True)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend", "how", "are", "you"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        def fake_burn_captions(
            video_path, ass_path, output_path, *,
            duration_seconds, ffmpeg_path=None, fontsdir=None, on_progress=None, use_nvenc=None,
        ):
            Path(output_path).write_bytes(b"fake mp4")
            if on_progress is not None:
                on_progress(0.0)
                on_progress(100.0)
            return Path(output_path)

        monkeypatch.setattr(engine, "burn_captions", fake_burn_captions)

        progress: list[int] = []
        run_job(job, progress.append)

        assert (output_dir / "clip.captioned.mp4").is_file()
        assert progress[-1] == 100
        # The burn stage's on_progress(0.0) call must map to somewhere
        # *inside* the burn stage's slice of the bar, not reset to 0.
        burn_stage_reports = progress[-2:]
        assert burn_stage_reports[0] > 0

    def test_burn_passes_the_bundled_fontsdir_to_burn_captions(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without this, a bundled-but-not-Windows-installed font silently
        falls back to a default face on burn-in, defeating the point of
        bundling fonts at all (spec 7A.4)."""
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir, burn=True)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        received: dict = {}

        def fake_burn_captions(
            video_path, ass_path, output_path, *,
            duration_seconds, ffmpeg_path=None, fontsdir=None, on_progress=None, use_nvenc=None,
        ):
            received["fontsdir"] = fontsdir
            Path(output_path).write_bytes(b"fake mp4")
            return Path(output_path)

        monkeypatch.setattr(engine, "burn_captions", fake_burn_captions)

        run_job(job, lambda _p: None)

        assert received["fontsdir"] == styles.fontsdir_arg()


class TestStyleWiring:
    """The whole point of the styling system (spec 7A) is worthless if a
    job never actually reaches it. These assert the ASS runner.py writes
    for a non-legacy style contains *that style's* real, distinctive
    animation tags -- not just that a file exists -- so a regression back
    to the two static CLEAN/POP presets would fail loudly here rather than
    silently shipping to a client.
    """

    def test_hype_style_produces_shake_tags_and_respects_its_max_words(
        self, tmp_path: Path, store: JobStore
    ) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        # HYPE (styles/hype.json): active_word.effect="shake", layout.max_words=2.
        job = make_job(store, video, output_dir, preset="HYPE")
        transcriber = FakeTranscriber(_result(["one", "two", "three", "four", "five", "six"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        run_job(job, lambda _p: None)

        ass_content = (output_dir / "clip.ass").read_text(encoding="utf-8")
        assert "\\frz" in ass_content  # shake's rotation tags -- never emitted by the legacy AssPreset renderer
        assert "HYPE" in ass_content  # the style's own name, not a hardcoded CLEAN/POP

        srt_content = (output_dir / "clip.srt").read_text(encoding="utf-8")
        # No caption line should show more than HYPE's 2-word cards.
        text_lines = [
            line for line in srt_content.splitlines()
            if line and not line[0].isdigit() and "-->" not in line
        ]
        assert text_lines, "expected at least one caption line"
        assert all(len(line.split()) <= 2 for line in text_lines)

    def test_neon_glow_style_produces_glow_tags(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        # NEON GLOW (styles/neon_glow.json): active_word.effect="glow".
        job = make_job(store, video, output_dir, preset="NEON GLOW")
        transcriber = FakeTranscriber(_result(["one", "two", "three", "four", "five"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        run_job(job, lambda _p: None)

        ass_content = (output_dir / "clip.ass").read_text(encoding="utf-8")
        assert "\\blur" in ass_content  # glow's blurred outline tag
        assert "NEON_GLOW" in ass_content  # style name, space-sanitised for the ASS Style field

    def test_unknown_style_name_falls_back_without_failing_the_job(
        self, tmp_path: Path, store: JobStore
    ) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir, preset="DOES-NOT-EXIST")
        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        run_job(job, lambda _p: None)  # must not raise

        assert (output_dir / "clip.ass").is_file()


class TestInputDeletionRule:
    """The most dangerous line in this package: only ever delete a file
    that came from the watch folder. Tested explicitly on both axes
    (location x outcome) per the task brief.
    """

    def test_watch_folder_file_is_deleted_on_success(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        settings.in_dir.mkdir(parents=True)
        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        run_job(job, lambda _p: None)

        assert not video.exists()

    def test_submit_by_path_file_is_never_deleted_on_success(self, tmp_path: Path, store: JobStore) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "editors_own_footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        job = make_job(store, video, output_dir)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        run_job(job, lambda _p: None)

        assert video.exists()

    def test_submit_by_path_file_is_never_deleted_on_failure(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = make_settings(tmp_path)
        video = tmp_path / "editors_own_footage" / "clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        def boom(video_path, output_path, *, ffmpeg_path=None):
            raise engine.AudioExtractionError("ffmpeg exploded")

        monkeypatch.setattr(engine, "extract_audio", boom)

        job = make_job(store, video, output_dir)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        with pytest.raises(engine.AudioExtractionError):
            run_job(job, lambda _p: None)

        assert video.exists()

    def test_watch_folder_file_is_not_deleted_on_failure(
        self, tmp_path: Path, store: JobStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = make_settings(tmp_path)
        settings.in_dir.mkdir(parents=True)
        video = settings.in_dir / "clip.mp4"
        video.write_bytes(b"fake video")
        output_dir = settings.out_dir / "clip"

        def boom(video_path, output_path, *, ffmpeg_path=None):
            raise engine.AudioExtractionError("ffmpeg exploded")

        monkeypatch.setattr(engine, "extract_audio", boom)

        job = make_job(store, video, output_dir)
        transcriber = FakeTranscriber(_result(["hello", "there", "friend"]))
        run_job = build_run_job(settings, watch_dir=settings.in_dir, transcriber=transcriber)

        with pytest.raises(engine.AudioExtractionError):
            run_job(job, lambda _p: None)

        assert video.exists()
