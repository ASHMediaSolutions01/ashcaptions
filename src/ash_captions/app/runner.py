"""The captioning pipeline itself -- the only place ``engine`` and
``languages`` meet (spec sections 8, 9, 10).

``build_run_job()`` returns the ``RunJob`` callable ``pipeline.JobWorker``
executes for each queued job: resolve the (language, dialect) choice,
extract audio, transcribe (and translate, if asked), post-process the
text, build caption cards, write every output file, optionally burn in,
then -- the single most dangerous line in this module -- delete the input
file, but *only* if it came from the watch folder (or the control page's
own upload folder). A file submitted by path lives wherever the editor
keeps their footage; deleting a client's source file would be a
catastrophe, so that check is not optional (spec section 10, 12). The
deletion itself is handed back to the worker to run *after* the job row
says ``done``: a crash between the two must leave the file, never the
row saying "pending" for a file that is gone.

Built for hour-long jobs
------------------------
Every engine stage reports progress through ``report`` (a
``pipeline.queue.ProgressReporter``: callable with a percentage, plus
``stage()`` and ``should_stop()``), the transcriber's own per-segment
progress drives the biggest slice of the bar, and ``should_stop`` is
threaded into transcribe/translate/burn so Quit cancels within a segment
rather than after the file. Optional engine/languages keywords are passed
only when the callee accepts them, so this module keeps working against
an engine that predates them.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from ash_captions import engine, languages, styles
from ash_captions.config import Settings, app_root, find_binary
from ash_captions.pipeline.db import Job
from ash_captions.pipeline.queue import AfterDone, JobCancelled

from .catalogue import dialect_preset_id
from .runner_transcript import _reusable_transcript, _save_transcript
from .runner_translate import run_translate_only
from .transcript import (
    SourceStamp,
    TranscriptError,
    TranscriptRecord,
    load_transcript,
    save_transcript,
    transcript_path,
)
from .lifecycle import write_job_marker
from .runner_util import (  # noqa: F401 - re-exported for tests and callers
    _CANCEL_EXCEPTIONS,
    DiskSpaceError,
    _ffprobe_beside,
    _is_within,
    _postprocess_segments,
    _postprocess_words,
    _progress_budget,
    accepted_kwargs,
    atomic_write,
    check_free_space,
    load_glossary_entries,
    load_glossary_entries_for,
)

log = logging.getLogger(__name__)

RunJob = Callable[[Job, Callable[[int], None]], "AfterDone | None"]

# engine.rules.build_cards()'s own default for its `min_words` floor --
# not re-exported from engine, so mirrored here as the ceiling
# `style.layout.max_words` is clamped against (see run_job): a style
# asking for 1- or 2-word cards (e.g. HYPE) must not have that request
# undone by a `min_words` floor higher than the style's own max.
_DEFAULT_MIN_WORDS_PER_CARD = 3

_TRANSCRIBER_OPTIONALS = ("cpu_threads", "condition_on_previous_text", "hallucination_silence_threshold")


class SharedTranscriber:
    """A ``Transcriber`` that defers to the pipeline's lazily-built model,
    so the style editor's preview renderer can share one loaded model
    with the queue instead of loading a second copy. ``build_run_job``
    attaches the getter as ``run_job.get_transcriber``."""

    def __init__(self, get_transcriber: Callable[[], "engine.Transcriber"]) -> None:
        self._get = get_transcriber

    def transcribe(self, audio_path: Any, **kwargs: Any) -> Any:
        return self._get().transcribe(audio_path, **kwargs)

    def translate(self, audio_path: Any, **kwargs: Any) -> Any:
        return self._get().translate(audio_path, **kwargs)


def _call_with_optionals(fn: Callable[..., Any], *args: Any, optional: dict[str, Any], **kwargs: Any) -> Any:
    """Call ``fn`` passing only those ``optional`` keywords it accepts."""
    accepted = accepted_kwargs(fn, optional)
    extras = {name: value for name, value in optional.items() if name in accepted}
    return fn(*args, **kwargs, **extras)


def _probe_or_none(video_path: Path, ffmpeg_path: Path) -> "engine.VideoInfo | None":
    try:
        return engine.probe_video(video_path, ffprobe_path=_ffprobe_beside(ffmpeg_path))
    except Exception:  # noqa: BLE001 - a probe failure degrades to defaults, never fails the job
        log.warning("could not probe %s; using default PlayRes and transcript-end duration", video_path.name, exc_info=True)
        return None


def build_run_job(
    settings: Settings,
    *,
    watch_dir: Path,
    transcriber: engine.Transcriber | None = None,
    ffmpeg_path: Path | None = None,
    upload_dir: Path | None = None,
) -> RunJob:
    """Build the ``run_job`` callable ``JobWorker`` executes.

    ``transcriber`` and ``ffmpeg_path`` are injectable so tests never need
    a real faster-whisper model or ffmpeg binary. In production both
    default lazily: a single ``WhisperTranscriber`` is constructed on
    first use and reused for every subsequent job (model loading is the
    expensive part; the worker thread already serialises jobs one at a
    time, so sharing one instance across them is safe and avoids reloading
    per job), and ffmpeg is resolved via ``config.find_binary`` (bundled
    ``bin/ffmpeg.exe``, falling back to PATH).

    ``upload_dir`` (default ``settings.upload_dir``) is treated exactly
    like ``watch_dir`` for post-success deletion: both hold copies that
    are ours, never an editor's originals.
    """
    resolved_ffmpeg = ffmpeg_path or find_binary("ffmpeg") or engine.DEFAULT_FFMPEG_PATH
    watch_dir = Path(watch_dir)
    upload_dir = Path(upload_dir) if upload_dir is not None else Path(settings.upload_dir)
    lazy_transcriber: dict[str, engine.Transcriber] = {}

    def get_transcriber() -> engine.Transcriber:
        if transcriber is not None:
            return transcriber
        if "instance" not in lazy_transcriber:
            optional = {name: getattr(settings, name) for name in _TRANSCRIBER_OPTIONALS}
            # A bundled model cache (installer-shipped app_root()/models)
            # must never trigger a HuggingFace download on an office PC.
            optional["local_files_only"] = (app_root() / "models").is_dir()
            lazy_transcriber["instance"] = _call_with_optionals(
                engine.WhisperTranscriber,
                settings.model_size,
                device=settings.device,
                download_root=settings.model_cache_dir,
                optional=optional,
            )
        return lazy_transcriber["instance"]

    def run_job(job: Job, report: Callable[[int], None]) -> AfterDone | None:
        set_stage: Callable[[str], None] = getattr(report, "stage", None) or (lambda _name: None)
        should_stop: Callable[[], bool] | None = getattr(report, "should_stop", None)
        video_path = Path(job.input_path)
        output_dir = Path(job.output_dir)
        write_job_marker(output_dir, job.id)
        stem = video_path.stem

        preset_id = dialect_preset_id(job.options.language, job.options.dialect)
        resolved = languages.resolve(job.options.language, preset_id)
        # The shared glossary merged with this job's client's own file,
        # client entries winning (languages.load_glossary_entries_for logs
        # which files applied). The shared path is still passed down as the
        # fallback an older `postprocess` reads per call when `entries` is
        # None.
        client = getattr(job.options, "client", None)
        client_glossary_path = settings.glossary_dir / "glossary.txt"
        glossary_entries = load_glossary_entries_for(settings.glossary_dir, client)
        if glossary_entries is not None:
            log.info("job %s: client %r, %d glossary entries", job.id, client, len(glossary_entries))
        # Never raises -- an unknown or invalid style name falls back to
        # styles.DEFAULT_STYLE (spec 7A.4), so a bad/renamed style can
        # never fail a job.
        style = styles.resolve_style(job.options.preset)
        card_max_words = style.layout.max_words
        card_min_words = min(_DEFAULT_MIN_WORDS_PER_CARD, card_max_words)

        if getattr(job.options, "mode", "full") == "translate_only":
            # Caption check (v0.5): English from the saved transcript, no
            # transcription, no burn, and never a deleted input.
            run_translate_only(
                job, report, settings=settings, resolved=resolved, glossary_path=client_glossary_path,
                glossary_entries=glossary_entries, get_transcriber=get_transcriber, ffmpeg_path=resolved_ffmpeg,
                run_transcriber=_run_transcriber, card_words=(card_max_words, card_min_words),
            )
            return None

        budget = _progress_budget(translate=job.options.translate, burn=job.options.burn)
        # A saved transcript beside the outputs makes re-styling and burning
        # a matter of seconds. burn_only *requires* one (it never
        # transcribes); a full job reuses one only when it provably came
        # from this exact file and already has everything the job needs.
        saved = _reusable_transcript(output_dir, stem, video_path, needs_translation=job.options.translate)
        mode = getattr(job.options, "mode", "full")
        if mode == "burn_only" and saved is None:
            raise RuntimeError(
                "No saved transcript for this video (or the file changed since it was "
                "transcribed). Run a normal captioning job first, then burn from the Studio."
            )
        model = get_transcriber() if saved is None else None
        # One probe, up front: PlayRes for the .ass (a 1920x1080 landscape
        # file rendered at the 1080x1920 default comes out ~56% size),
        # duration for the burn progress bar, geometry for punch-in.
        info = _probe_or_none(video_path, resolved_ffmpeg)

        job_tmp = Path(settings.tmp_dir) / f"job-{job.id}"
        try:
            job_tmp.mkdir(parents=True, exist_ok=True)
            audio_path = job_tmp / f"{stem}.wav"

            en_words: tuple | None = None
            if saved is not None:
                # Skip straight to writing: the words are already clean
                # (post-processed before they were saved).
                for skipped in ("extract", "transcribe") + (("translate",) if job.options.translate else ()):
                    set_stage(skipped)
                    report(budget[skipped][1])
                words, segments, en_words = saved.words, saved.segments, saved.en_words
                if info is None and saved.play_res is not None:
                    info = engine.VideoInfo(saved.play_res[0], saved.play_res[1], 0.0, 0.0)
            else:
                set_stage("extract")
                report(budget["extract"][0])
                engine.extract_audio(video_path, audio_path, ffmpeg_path=resolved_ffmpeg)
                report(budget["extract"][1])

                set_stage("transcribe")
                result = _run_transcriber(model.transcribe, audio_path, resolved, budget["transcribe"], report, should_stop)

                translation = None
                if job.options.translate:
                    set_stage("translate")
                    # No dialect priming for the translate pass: the Spanish
                    # (or Portuguese...) initial_prompt biased Whisper into
                    # leaving whole chunks of the "English" output in the
                    # source language. Seen on a real Spanish interview.
                    translation = _run_transcriber(
                        model.translate, audio_path, resolved, budget["translate"], report, should_stop,
                        initial_prompt=None,
                    )

                set_stage("postprocess")
                report(budget["postprocess"][0])
                words = _postprocess_words(result.words, resolved, client_glossary_path, entries=glossary_entries)
                segments = _postprocess_segments(result.segments, resolved, client_glossary_path, entries=glossary_entries)
                if translation is not None:
                    # The translation is English whatever the source was, so it
                    # takes English conventions (not, say, Portuguese spelling
                    # rules that would rewrite English words). The client
                    # glossary still applies: brand and product names are the
                    # same in both.
                    en_words = _postprocess_words(
                        translation.words, languages.resolve("en"), client_glossary_path, entries=glossary_entries
                    )
                report(budget["postprocess"][1])
                _save_transcript(output_dir, stem, video_path, job, resolved, words, segments, en_words, info)

            set_stage("write")
            report(budget["cards_and_write"][0])
            cards = engine.build_cards(
                words,
                max_words=card_max_words,
                min_words=card_min_words,
                silence_gap=settings.silence_gap_seconds,
            )
            atomic_write(lambda p: engine.write_srt(cards, p), output_dir / f"{stem}.srt")
            ass_optional = {"play_res": (info.width, info.height)} if info is not None else {}
            atomic_write(
                lambda p: _call_with_optionals(engine.write_ass, cards, p, style, optional=ass_optional),
                output_dir / f"{stem}.ass",
            )
            atomic_write(lambda p: engine.write_txt(segments, p), output_dir / f"{stem}.txt")
            if en_words is not None:
                en_cards = engine.build_cards(
                    en_words,
                    max_words=card_max_words,
                    min_words=card_min_words,
                    silence_gap=settings.silence_gap_seconds,
                )
                atomic_write(lambda p: engine.write_srt(en_cards, p), output_dir / f"{stem}.en.srt")
            report(budget["cards_and_write"][1])

            if job.options.burn:
                set_stage("burn")
                _burn(job, video_path, output_dir, stem, words, segments, info, budget["burn"], report, should_stop)
        except _CANCEL_EXCEPTIONS as exc:
            raise JobCancelled(str(exc)) from exc
        finally:
            shutil.rmtree(job_tmp, ignore_errors=True)

        # DANGER: only ever delete a file that came from the watch folder
        # or our own upload folder. A path submitted directly by an editor
        # points at their live footage -- deleting it would destroy a
        # client's source file. Returned, not run: the worker calls it
        # only after the job row is marked done.
        from_watch = _is_within(video_path, watch_dir)
        from_upload = _is_within(video_path, upload_dir)
        if not (from_watch or from_upload):
            return None

        def delete_consumed_input() -> None:
            video_path.unlink(missing_ok=True)
            # The upload route gives each file its own <uuid>/ folder;
            # leave nothing behind once the file itself is gone.
            if from_upload and video_path.parent != upload_dir:
                try:
                    video_path.parent.rmdir()
                except OSError:
                    pass

        return delete_consumed_input

    def _run_transcriber(
        fn: Callable[..., Any],
        audio_path: Path,
        resolved: languages.ResolvedDialect,
        span: tuple[int, int],
        report: Callable[[int], None],
        should_stop: Callable[[], bool] | None,
        initial_prompt: str | None = "",
    ) -> Any:
        start, end = span
        report(start)
        # "" (the default) means "use the dialect's prompt"; None means none.
        prompt = (resolved.initial_prompt or None) if initial_prompt == "" else initial_prompt

        def on_progress(seconds_done: float, total_seconds: float) -> None:
            if total_seconds and total_seconds > 0:
                fraction = max(0.0, min(1.0, seconds_done / total_seconds))
                report(round(start + (end - start) * fraction))

        result = _call_with_optionals(
            fn,
            audio_path,
            language=resolved.whisper_language,
            initial_prompt=prompt,
            optional={"on_progress": on_progress, "should_stop": should_stop},
        )
        report(end)
        return result

    def _burn(
        job: Job,
        video_path: Path,
        output_dir: Path,
        stem: str,
        words: tuple,
        segments: tuple,
        info: "engine.VideoInfo | None",
        span: tuple[int, int],
        report: Callable[[int], None],
        should_stop: Callable[[], bool] | None,
    ) -> None:
        start, end = span
        try:
            input_size = video_path.stat().st_size
        except OSError:
            input_size = 0
        check_free_space(output_dir, input_size_bytes=input_size, min_free_gb=settings.min_free_disk_gb)

        # Real duration from ffprobe when it answered; the transcript's own
        # last timestamp is the fallback (close enough to drive the bar,
        # but it stops early on a file that ends in silence or music).
        duration = info.duration_seconds if info is not None and info.duration_seconds > 0 else 0.0
        if duration <= 0:
            duration = max((seg.end for seg in segments), default=0.0)

        def on_burn_progress(pct: float) -> None:
            report(round(start + (end - start) * (pct / 100)))

        # Punch-in (engine/punch.py). Off unless the studio turned it on,
        # because it changes how a client's video is framed and should
        # never happen to footage silently. Any failure to build it
        # degrades to a normal burn rather than losing the job: captions
        # are the deliverable, the zoom is a flourish.
        punch_filter = None
        if settings.punch_mode != "off":
            try:
                geometry = info if info is not None else engine.probe_video(
                    video_path, ffprobe_path=_ffprobe_beside(resolved_ffmpeg)
                )
                moments = engine.select_punch_moments(
                    words,
                    mode=settings.punch_mode,
                    keywords=tuple(settings.punch_keywords),
                    duration=settings.punch_duration_seconds,
                    min_spacing=settings.punch_min_spacing_seconds,
                    video_duration=geometry.duration_seconds,
                )
                # build_punch_filter is the timestamp-preserving scale/crop
                # chain and needs no geometry (it works in iw/ih and t);
                # build_zoompan_filter is the older zoompan form, which must
                # be told the output size and fps. Passing geometry to the
                # new one raised TypeError, which the guard below turned
                # into a silent "no punch-in" on every job.
                build_punch = getattr(engine, "build_punch_filter", None)
                if build_punch is not None:
                    punch_filter = build_punch(moments, zoom=settings.punch_zoom)
                else:
                    punch_filter = engine.build_zoompan_filter(
                        moments,
                        width=geometry.width,
                        height=geometry.height,
                        fps=geometry.fps,
                        zoom=settings.punch_zoom,
                    )
                log.info("punch-in: %d moment(s) at zoom %.2f", len(moments), settings.punch_zoom)
            except Exception:  # noqa: BLE001
                log.warning("punch-in unavailable; burning without it", exc_info=True)
                punch_filter = None

        # Captions behind the speaker: a person matte first (about the
        # video's own length on a CPU), then a two-input burn. The matte is
        # the first 40% of the burn's progress span. Any failure here is a
        # job failure with a plain message, not a silent fall-through to a
        # normal burn: the editor asked for the effect.
        matte_path = None
        if getattr(job.options, "behind_speaker", False):
            if info is None or info.width <= 0:
                raise RuntimeError(
                    "Captions behind the speaker need the video's frame size, and ffprobe could not read it."
                )
            matte_end = start + round((end - start) * 0.4)
            _stage(report, "matte")
            model_path = engine.ensure_matte_model(settings.model_cache_dir, download=True)
            matte_path = _matte_work_dir(job) / "matte.mp4"
            engine.render_matte(
                video_path,
                matte_path,
                model_path=model_path,
                width=info.width,
                height=info.height,
                fps=info.fps,
                duration_seconds=duration,
                ffmpeg_path=resolved_ffmpeg,
                threads=settings.cpu_threads,
                on_progress=lambda pct: report(round(start + (matte_end - start) * (pct / 100))),
                should_stop=should_stop,
            )
            start = matte_end
            _stage(report, "burn")

        # fontsdir points libass at the bundled font directory (spec 7A.4)
        # so a style's font resolves identically on all six machines
        # without being installed into Windows.
        _call_with_optionals(
            engine.burn_captions,
            video_path,
            output_dir / f"{stem}.ass",
            output_dir / f"{stem}.captioned.mp4",
            duration_seconds=duration,
            ffmpeg_path=resolved_ffmpeg,
            fontsdir=styles.fontsdir_arg(),
            punch_filter=punch_filter,
            on_progress=on_burn_progress,
            optional={"should_stop": should_stop, "matte_path": matte_path},
        )
        report(end)

    def _stage(report: Any, name: str) -> None:
        setter = getattr(report, "stage", None)
        if callable(setter):
            setter(name)

    def _matte_work_dir(job: Job) -> Path:
        path = Path(settings.tmp_dir) / f"job-{job.id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    run_job.get_transcriber = get_transcriber  # type: ignore[attr-defined]
    return run_job
