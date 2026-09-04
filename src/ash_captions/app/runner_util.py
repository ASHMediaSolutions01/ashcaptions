"""Pure helpers behind ``runner.build_run_job``: the progress budget, the
post-processing bridge into engine's Word/Segment types, atomic output
writes, the disk-space guard, and the "does this callee accept that
keyword?" probe that lets the runner wire optional engine/languages
parameters without crashing against an older module."""

from __future__ import annotations

import dataclasses
import logging
import inspect
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from ash_captions import engine, languages

log = logging.getLogger(__name__)

# Base weights for the progress bar. Transcription dominates real runtime
# (spec section 9: "timing quality is the feature"), so it must dominate
# the bar too -- not a naive linear 20/40/60/80 split. Stages that don't
# apply to a given job (translate, burn) are dropped and the rest
# re-normalised to still span 0-100 -- see ``_progress_budget``.
_STAGE_ORDER = ("extract", "transcribe", "translate", "postprocess", "cards_and_write", "burn")
_BASE_WEIGHTS = {
    "extract": 5,
    "transcribe": 55,
    "translate": 15,
    "postprocess": 3,
    "cards_and_write": 7,
    "burn": 15,
}

GB = 1024 ** 3

# The engine's cancellation exceptions, when this tree's engine has them.
# Both the full runner (runner.py) and the translate-only runner
# (runner_translate.py) map these to ``pipeline.queue.JobCancelled``.
# runner.py re-exports the name; test_runner_inputs.py monkeypatches it there.
_CANCEL_EXCEPTIONS: tuple[type[BaseException], ...] = tuple(
    exc for exc in (
        getattr(engine, "TranscriptionCancelled", None),
        getattr(engine, "BurnCancelled", None),
        getattr(engine, "MatteCancelled", None),
    ) if isinstance(exc, type)
)


class DiskSpaceError(RuntimeError):
    """Refused before starting a burn-in that could not finish."""


def _progress_budget(*, translate: bool, burn: bool, transcribe: bool = True) -> dict[str, tuple[int, int]]:
    """Allocate ``_BASE_WEIGHTS`` proportionally over the stages that
    actually run for this job, returning ``{stage: (start_pct, end_pct)}``.
    The last active stage always ends exactly at 100, regardless of
    rounding drift in the stages before it.

    ``transcribe=False`` is the translate-only job (v0.5 caption check):
    the saved transcript is reused, so only extract, translate,
    postprocess and write run.
    """
    active = {"extract", "postprocess", "cards_and_write"}
    if transcribe:
        active.add("transcribe")
    if translate:
        active.add("translate")
    if burn:
        active.add("burn")
    total_weight = sum(weight for name, weight in _BASE_WEIGHTS.items() if name in active)

    budget: dict[str, tuple[int, int]] = {}
    cursor = 0.0
    for name in _STAGE_ORDER:
        if name not in active:
            continue
        share = _BASE_WEIGHTS[name] / total_weight * 100
        start, cursor = cursor, cursor + share
        budget[name] = (round(start), round(cursor))

    last_stage = next(name for name in reversed(_STAGE_ORDER) if name in active)
    start, _ = budget[last_stage]
    budget[last_stage] = (start, 100)
    return budget


def accepted_kwargs(fn: Callable[..., Any], names: Iterable[str]) -> set[str]:
    """The subset of ``names`` that ``fn`` will accept as keywords -- by
    name, or via ``**kwargs``. Empty when the signature can't be read."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return set()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return set(names)
    return {name for name in names if name in params}


def _postprocess_text(text: str, resolved: languages.ResolvedDialect, glossary_path: Path, entries: Any) -> str:
    if entries is not None and "entries" in accepted_kwargs(languages.postprocess, ("entries",)):
        return languages.postprocess(text, resolved, glossary_path, entries=entries)
    return languages.postprocess(text, resolved, glossary_path)


def _postprocess_words(
    words: tuple, resolved: languages.ResolvedDialect, glossary_path: Path, *, entries: Any = None
) -> tuple:
    """Apply the post-processing chain (dialect glossary, client glossary,
    spelling) word-by-word, preserving each word's timing.

    Matching per-word (rather than over the full segment) means a
    multi-word glossary phrase only reliably fires in the plain-text
    transcript (``_postprocess_segments``, below), not in the word-timed
    captions -- an accepted limitation given ``build_cards`` needs
    per-``Word`` timing to produce the .srt/.ass files, and most glossary
    corrections in practice are single terms (a name, a brand).

    ``entries`` is the client glossary pre-loaded once per job; it is
    forwarded when ``languages.postprocess`` accepts it (otherwise that
    function re-reads the glossary file per word, which on an hour-long
    transcript is the difference between 0.2 s and 19 s).
    """
    batch = getattr(languages, "postprocess_words", None)
    if batch is not None and words:
        # One call for the whole transcript: multi-word glossary phrases
        # match across word boundaries, and the glossary is applied once
        # rather than once per word.
        kwargs = {"entries": entries} if entries is not None else {}
        texts = batch([word.text for word in words], resolved, glossary_path, **kwargs)
        if len(texts) == len(words):
            return tuple(dataclasses.replace(word, text=text) for word, text in zip(words, texts))
    return tuple(
        dataclasses.replace(word, text=_postprocess_text(word.text, resolved, glossary_path, entries))
        for word in words
    )


def _postprocess_segments(
    segments: tuple, resolved: languages.ResolvedDialect, glossary_path: Path, *, entries: Any = None
) -> tuple:
    return tuple(
        dataclasses.replace(seg, text=_postprocess_text(seg.text, resolved, glossary_path, entries))
        for seg in segments
    )


def load_glossary_entries(glossary_path: Path) -> Any:
    """The client glossary, loaded once. ``None`` if the languages package
    has no loader (older tree) or the load fails -- callers then fall back
    to per-call loading inside ``postprocess``."""
    loader = getattr(languages, "load_glossary_entries", None) or getattr(languages, "load_glossary", None)
    if loader is None:
        return None
    try:
        return loader(glossary_path)
    except Exception:  # noqa: BLE001 - a bad glossary must never fail a job
        return None


def load_glossary_entries_for(glossary_dir: Path, client: str | None) -> Any:
    """The shared glossary merged with ``client``'s own, loaded once per
    job (``languages.load_glossary_entries_for``). On an older languages
    package without the merge, the shared file alone. ``None`` when the
    load fails -- a bad glossary must never fail a job."""
    merged = getattr(languages, "load_glossary_entries_for", None)
    if merged is None:
        return load_glossary_entries(Path(glossary_dir) / "glossary.txt")
    try:
        return merged(glossary_dir, client)
    except Exception:  # noqa: BLE001
        return None


def _is_within(path: Path, directory: Path) -> bool:
    """True if ``path`` resolves to somewhere inside ``directory``."""
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def client_for_watch_path(path: Path, watch_dir: Path) -> str | None:
    """The client a watch-folder drop belongs to: the first subfolder under
    ``watch_dir`` (``in\\Acme\\clip.mp4`` -> ``"Acme"``), sanitized the same
    way the control page's field is. ``None`` for a file directly in
    ``watch_dir``, one outside it, or a folder name that isn't a usable
    client name (that drop still runs, with the shared glossary only)."""
    from ash_captions.web.validation import sanitize_client_name

    try:
        relative = Path(path).resolve().relative_to(Path(watch_dir).resolve())
    except (OSError, ValueError):
        return None
    if len(relative.parts) < 2:
        return None
    try:
        return sanitize_client_name(relative.parts[0])
    except ValueError as exc:
        log.warning(
            "watch folder %r is not a usable client name (%s); running with the shared glossary only",
            relative.parts[0], exc,
        )
        return None


def _ffprobe_beside(ffmpeg_path: Path) -> Path:
    """ffprobe ships next to ffmpeg in the bundle, so derive it rather than
    making the caller configure a second path that can drift out of sync."""
    candidate = Path(ffmpeg_path).with_name("ffprobe.exe")
    return candidate if candidate.is_file() else Path("ffprobe")


def atomic_write(writer: Callable[[Path], Any], final_path: Path) -> Path:
    """Run ``writer`` against a ``.part`` sibling, then rename over
    ``final_path`` -- a crash mid-write leaves no truncated deliverable
    under the real name. The sibling's name is unique per call: two Studio
    tabs restyling the same job at once each write their own temp file and
    the last rename wins, instead of one tab's rename hitting the other's
    open handle (PermissionError on Windows).

    The rename itself is retried: on Windows two replacements of the *same*
    destination that overlap in time fail the loser with
    ``PermissionError: Access is denied`` even though nothing is wrong with
    either file, and an antivirus scanner holding the new file for a
    moment does the same. A few short retries turn both into the intended
    "last writer wins"; a rename that keeps failing still raises."""
    final_path = Path(final_path)
    partial = final_path.with_name(f"{final_path.name}.{uuid.uuid4().hex[:8]}.part")
    writer(partial)
    _replace_with_retry(partial, final_path)
    return final_path


REPLACE_ATTEMPTS = 12
REPLACE_BACKOFF_SECONDS = 0.02


def _replace_with_retry(partial: Path, final_path: Path) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(partial, final_path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_SECONDS * (attempt + 1))


def format_gb(size_bytes: float) -> str:
    gb = size_bytes / GB
    return f"{gb:.0f} GB" if gb >= 10 else f"{gb:.1f} GB"


def check_free_space(output_dir: Path, *, input_size_bytes: int, min_free_gb: float) -> None:
    """Raise ``DiskSpaceError`` unless ``output_dir``'s drive has more free
    space than ``max(1.2 x input, min_free_gb)``. Skipped silently if the
    drive can't be measured -- a failed measurement is not evidence."""
    output_dir = Path(output_dir)
    required = max(input_size_bytes * 1.2, float(min_free_gb) * GB)
    probe = output_dir
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return
    if free < required:
        drive = output_dir.resolve().drive or str(probe)
        raise DiskSpaceError(
            f"Not enough free space on {drive} — need about {format_gb(required)}, have {format_gb(free)}"
        )
