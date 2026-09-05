"""The saved transcript beside a job's outputs: ``<stem>.transcript.json``.

Why it exists (v0.3, the Studio page): once a video has been transcribed
once, every further caption operation -- re-styling into another look,
burning after the editor has picked one, retrying a failed burn -- is a
matter of seconds if the word timings are on disk, and a matter of
minutes (an hour, for a long file) if they have to be produced again.
The transcript is the expensive, stable part; captions are cheap views
of it.

The record carries the source file's size and mtime so a stale
transcript (the editor re-exported the video under the same name) is
never reused for the wrong footage.

Hand edits (v0.6 section 1)
---------------------------
From v0.6 the record is also where an editor's corrections live, so a
restyle or a re-burn keeps them instead of overwriting them. ``Word``
itself is untouched -- it is used across the whole engine -- so the
editing state rides in a parallel tuple, ``meta``, one ``WordMeta`` per
word, and the file gains a ``revision`` counter so two Studio tabs
cannot silently clobber each other.

The five operations below (``set_text``, ``retime``, ``split``,
``merge``, ``set_style``) are pure functions from one record to the
next: no I/O, no globals, each bumping the revision by one. The route
that applies them (``web/routes_transcript.py``) is a thin atomic
wrapper -- it loads once, applies the list, saves once, re-renders once.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from ash_captions.engine import Segment, Word

TRANSCRIPT_SUFFIX = ".transcript.json"
FORMAT_VERSION = 2
# Version 1 is every transcript written before v0.6: no meta, no revision.
# It loads to ``meta=None``, which renders exactly as it did.
SUPPORTED_FORMATS = (1, 2)

# A retimed word may not be squeezed below this: shorter than a frame or
# two and the karaoke fill has nothing to animate over.
MIN_WORD_SECONDS = 0.060

try:  # tracks B and F put ``WordStyle`` in the renderer's own package, so
    # the renderer never imports from ``app``. Until that lands (or in a
    # tree without it) the identical class is defined here, and the moment
    # it does land both sides are the same class.
    from ash_captions.styles.schema import WordStyle  # type: ignore[attr-defined]
except (ImportError, AttributeError):  # pragma: no cover - exercised by whichever tree runs

    @dataclass(frozen=True)
    class WordStyle:  # type: ignore[no-redef]
        """One word painted differently from its neighbours (v0.6 section 2)."""

        colour: str | None = None  # "#RRGGBB"
        scale: float | None = None  # multiplier on the look's size, 0.5-3.0
        bold: bool | None = None
        italic: bool | None = None
        x: float | None = None  # free placement only, fraction of the frame
        y: float | None = None


@dataclass(frozen=True)
class WordMeta:
    """What was done to one word by hand. All-default means "nothing"."""

    edited: bool = False  # text changed by hand; confidence dropped
    retimed: bool = False  # start or end dragged
    break_before: bool = False  # this word must start a new caption line
    no_break_before: bool = False  # ...and this one must not
    style: WordStyle | None = None

    @property
    def is_default(self) -> bool:
        return self == _DEFAULT_META


_DEFAULT_META = WordMeta()


@dataclass(frozen=True)
class SourceStamp:
    """Enough to notice the input file changed since transcription."""

    size: int
    mtime_ns: int

    @classmethod
    def of(cls, path: Path) -> "SourceStamp":
        stat = os.stat(path)
        return cls(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


@dataclass(frozen=True)
class TranscriptRecord:
    language: str
    words: tuple[Word, ...]
    segments: tuple[Segment, ...]
    en_words: tuple[Word, ...] | None = None
    play_res: tuple[int, int] | None = None
    source: SourceStamp | None = None
    dialect: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # v0.6: one entry per word, or None for "nothing has been edited".
    meta: tuple[WordMeta, ...] | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        # The one validator: nothing may construct a record whose meta has
        # drifted out of alignment with its words -- not an operation, not
        # ``dataclasses.replace``, not the loader.
        if self.meta is not None and len(self.meta) != len(self.words):
            raise ValueError(
                f"transcript meta has {len(self.meta)} entries for {len(self.words)} words"
            )

    def matches(self, video_path: Path) -> bool:
        """True when ``video_path`` is the file this transcript came from."""
        if self.source is None:
            return False
        try:
            return SourceStamp.of(video_path) == self.source
        except OSError:
            return False

    def meta_at(self, index: int) -> WordMeta:
        """This word's editing state; the default when nothing was done."""
        if self.meta is None:
            return _DEFAULT_META
        return self.meta[index]

    @property
    def en_stale(self) -> bool:
        """True once a source word was retyped: the saved English no longer
        matches the words it was translated from. ``en_words`` is left
        alone; "Translate to check" re-runs the English pass."""
        return bool(self.extra.get("en_stale"))


def transcript_path(output_dir: Path, stem: str) -> Path:
    return Path(output_dir) / f"{stem}{TRANSCRIPT_SUFFIX}"


# ---------------------------------------------------------------------------
# per-word style: validated here, at the boundary, and stored
# ---------------------------------------------------------------------------

_HEX_COLOUR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_MIN_SCALE, _MAX_SCALE = 0.5, 3.0


class WordStyleError(ValueError):
    """A per-word style dict was not something the renderer can be given."""


def _style_field_names() -> tuple[str, ...]:
    return tuple(f.name for f in fields(WordStyle))


def _number(name: str, value: Any, *, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WordStyleError(f"style.{name}: {value!r} is not a number")
    if not (lo <= float(value) <= hi):
        raise WordStyleError(f"style.{name}: {value} is out of range (expected {lo}-{hi})")
    return float(value)


def parse_word_style(data: Any) -> WordStyle | None:
    """Turn the browser's style object into a ``WordStyle``, or ``None`` for
    "no override". Every key is optional; an unknown one, a colour that is
    not ``#RRGGBB``, a scale outside 0.5-3.0 or a placement fraction outside
    [0, 1] is a ``WordStyleError`` naming the field."""
    if data is None:
        return None
    if not isinstance(data, dict):
        raise WordStyleError(f"style: expected an object, got {type(data).__name__}")
    allowed = _style_field_names()
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise WordStyleError(f"style: unknown field(s) {', '.join(unknown)}")
    values: dict[str, Any] = {}
    for name, value in data.items():
        if value is None:
            continue
        if name == "colour":
            if not isinstance(value, str) or not _HEX_COLOUR_RE.match(value):
                raise WordStyleError(f"style.colour: {value!r} is not a hex colour (expected '#RRGGBB')")
            values[name] = value
        elif name == "scale":
            values[name] = _number(name, value, lo=_MIN_SCALE, hi=_MAX_SCALE)
        elif name in ("bold", "italic"):
            if not isinstance(value, bool):
                raise WordStyleError(f"style.{name}: {value!r} is not true/false")
            values[name] = value
        else:  # x, y -- fractions of the frame
            values[name] = _number(name, value, lo=0.0, hi=1.0)
    if not values:
        return None
    return WordStyle(**values)


def word_style_to_dict(style: WordStyle | None) -> dict[str, Any] | None:
    """The set keys only, so a style with one colour is one key on disk."""
    if style is None:
        return None
    out = {name: getattr(style, name) for name in _style_field_names()}
    return {name: value for name, value in out.items() if value is not None} or None


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------


def _word_to_dict(w: Word) -> dict[str, Any]:
    return {"t": w.text, "s": round(w.start, 3), "e": round(w.end, 3), "p": round(w.probability, 3)}


def _word_from_dict(d: dict[str, Any]) -> Word:
    return Word(text=str(d["t"]), start=float(d["s"]), end=float(d["e"]), probability=float(d.get("p", 1.0)))


def _meta_to_dict(m: WordMeta) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("edited", "retimed", "break_before", "no_break_before"):
        if getattr(m, name):
            out[name] = True
    style = word_style_to_dict(m.style)
    if style is not None:
        out["style"] = style
    return out


def _meta_from_dict(d: Any) -> WordMeta:
    if not isinstance(d, dict):
        return _DEFAULT_META
    return WordMeta(
        edited=bool(d.get("edited")),
        retimed=bool(d.get("retimed")),
        break_before=bool(d.get("break_before")),
        no_break_before=bool(d.get("no_break_before")),
        style=parse_word_style(d.get("style")),
    )


def _meta_payload(record: TranscriptRecord) -> list[dict[str, Any]] | None:
    """``None`` while nothing has been edited, so an untouched v2 file is
    the v1 file plus a revision -- and every golden comparison of the words
    themselves still holds."""
    if record.meta is None or all(m.is_default for m in record.meta):
        return None
    return [_meta_to_dict(m) for m in record.meta]


def save_transcript(path: Path, record: TranscriptRecord) -> Path:
    """Write atomically (temp + replace), like every other job output."""
    payload = {
        "format": FORMAT_VERSION,
        "language": record.language,
        "dialect": record.dialect,
        "play_res": list(record.play_res) if record.play_res else None,
        "source": {"size": record.source.size, "mtime_ns": record.source.mtime_ns} if record.source else None,
        "words": [_word_to_dict(w) for w in record.words],
        "segments": [
            {"text": s.text, "s": round(s.start, 3), "e": round(s.end, 3), "words": [_word_to_dict(w) for w in s.words]}
            for s in record.segments
        ],
        "en_words": [_word_to_dict(w) for w in record.en_words] if record.en_words is not None else None,
        "extra": record.extra,
        "meta": _meta_payload(record),
        "revision": int(record.revision),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path


class TranscriptError(Exception):
    """The transcript file is missing, unreadable, or from another format."""


def load_transcript(path: Path) -> TranscriptRecord:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TranscriptError(f"No saved transcript at {path}") from exc
    except (OSError, ValueError) as exc:
        raise TranscriptError(f"Could not read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("format") not in SUPPORTED_FORMATS:
        raise TranscriptError(f"{path} is not a transcript this version understands")
    try:
        words = tuple(_word_from_dict(w) for w in data["words"])
        segments = tuple(
            Segment(
                text=str(s["text"]),
                start=float(s["s"]),
                end=float(s["e"]),
                words=tuple(_word_from_dict(w) for w in s.get("words", [])),
            )
            for s in data.get("segments", [])
        )
        en_raw = data.get("en_words")
        en_words = tuple(_word_from_dict(w) for w in en_raw) if en_raw is not None else None
        play_raw = data.get("play_res")
        play_res = (int(play_raw[0]), int(play_raw[1])) if play_raw else None
        src_raw = data.get("source")
        source = SourceStamp(size=int(src_raw["size"]), mtime_ns=int(src_raw["mtime_ns"])) if src_raw else None
        meta_raw = data.get("meta")
        meta = tuple(_meta_from_dict(m) for m in meta_raw) if meta_raw is not None else None
    except (KeyError, TypeError, ValueError) as exc:
        raise TranscriptError(f"{path} is malformed: {exc}") from exc
    try:
        return TranscriptRecord(
            language=str(data.get("language", "")),
            dialect=data.get("dialect"),
            words=words,
            segments=segments,
            en_words=en_words,
            play_res=play_res,
            source=source,
            extra=dict(data.get("extra") or {}),
            meta=meta,
            revision=int(data.get("revision", 0) or 0),
        )
    except ValueError as exc:  # the meta/words length validator
        raise TranscriptError(f"{path} is malformed: {exc}") from exc


# ---------------------------------------------------------------------------
# operations: pure functions over a record (v0.6 section 1)
# ---------------------------------------------------------------------------

# "haramienta," -> ("", "haramienta", ","). The core is what a bulk fix
# matches on and replaces; the punctuation around it is the occurrence's
# own and is kept.
_CORE_RE = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)


def split_word_text(text: str) -> tuple[str, str, str]:
    """``(leading punctuation, core, trailing punctuation)``."""
    m = _CORE_RE.match(text)
    return (m.group(1), m.group(2), m.group(3)) if m else ("", text, "")


def apply_case(sample: str, text: str) -> str:
    """``text`` wearing ``sample``'s capitalisation: an all-caps occurrence
    stays all-caps, a capitalised one stays capitalised, a lower-case one
    stays lower-case."""
    if not sample or not text:
        return text
    if sample.isupper() and len(sample) > 1:
        return text.upper()
    if sample[0].isupper():
        return text[0].upper() + text[1:]
    return text[0].lower() + text[1:]


def occurrences(record: TranscriptRecord, index: int) -> tuple[int, ...]:
    """Every word in the transcript that is the same word as the one at
    ``index``: same core, ignoring case and surrounding punctuation. The
    count the popup shows and the set a bulk fix changes are this one
    function, so they cannot disagree."""
    _check_index(record, index)
    core = split_word_text(record.words[index].text)[1]
    if not core:
        return (index,)
    key = core.casefold()
    return tuple(i for i, w in enumerate(record.words) if split_word_text(w.text)[1].casefold() == key)


def _check_index(record: TranscriptRecord, index: int) -> None:
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError(f"word index must be a whole number, got {index!r}")
    if not (0 <= index < len(record.words)):
        raise ValueError(f"there is no word {index} in this transcript ({len(record.words)} words)")


def _meta_list(record: TranscriptRecord) -> list[WordMeta]:
    if record.meta is None:
        return [_DEFAULT_META] * len(record.words)
    return list(record.meta)


def _bumped(
    record: TranscriptRecord,
    *,
    words: Sequence[Word] | None = None,
    meta: Sequence[WordMeta] | None = None,
    segments: Sequence[Segment] | None = None,
    extra: dict[str, Any] | None = None,
) -> TranscriptRecord:
    """The record with these parts replaced and the revision bumped once."""
    new_meta = tuple(meta) if meta is not None else record.meta
    if new_meta is not None and all(m.is_default for m in new_meta):
        new_meta = None
    return replace(
        record,
        words=tuple(words) if words is not None else record.words,
        segments=tuple(segments) if segments is not None else record.segments,
        meta=new_meta,
        extra=extra if extra is not None else record.extra,
        revision=record.revision + 1,
    )


def _resync_segments(segments: Sequence[Segment], changes: dict[tuple[float, float], Word]) -> tuple[Segment, ...]:
    """Carry word edits into the segments, so the plain-text ``.txt``
    written from segment text says what the captions say. A segment whose
    text is not simply its words joined (postprocessing rewrote it) keeps
    that text: replacing it with the join would lose more than it fixes."""
    if not changes:
        return tuple(segments)
    out: list[Segment] = []
    for seg in segments:
        new_words = tuple(changes.get((w.start, w.end), w) for w in seg.words)
        if new_words == seg.words:
            out.append(seg)
            continue
        was_joined = seg.text == " ".join(w.text for w in seg.words)
        text = " ".join(w.text for w in new_words) if was_joined else seg.text
        start = min((w.start for w in new_words), default=seg.start)
        end = max((w.end for w in new_words), default=seg.end)
        out.append(Segment(text=text, start=min(seg.start, start), end=max(seg.end, end), words=new_words))
    return tuple(out)


def _clean_text(raw: Any) -> str:
    if not isinstance(raw, str):
        raise ValueError(f"the new text must be text, got {raw!r}")
    text = " ".join(raw.split())
    if not text:
        raise ValueError("a word cannot be empty")
    return text


def set_text(record: TranscriptRecord, index: int, text: str, *, all_occurrences: bool = False) -> TranscriptRecord:
    """Retype one word, or every occurrence of it.

    A single fix takes the text exactly as typed. A bulk fix treats the
    typed text as the new *core* and gives every occurrence its own
    capitalisation and its own surrounding punctuation back, so
    ``Haramienta,`` becomes ``Herramienta,`` and ``HARAMIENTA`` becomes
    ``HERRAMIENTA`` from one edit.

    Every word it touches loses its confidence: a hand-typed word has no
    model confidence, and keeping the old number would be a lie. The saved
    English is left alone and marked stale."""
    _check_index(record, index)
    typed = _clean_text(text)
    words = list(record.words)
    meta = _meta_list(record)
    changes: dict[tuple[float, float], Word] = {}
    if all_occurrences:
        new_core = split_word_text(typed)[1] or typed
        targets = occurrences(record, index)
        for i in targets:
            lead, core, trail = split_word_text(words[i].text)
            words[i] = replace(words[i], text=f"{lead}{apply_case(core, new_core)}{trail}", probability=0.0)
    else:
        targets = (index,)
        words[index] = replace(words[index], text=typed, probability=0.0)
    for i in targets:
        meta[i] = replace(meta[i], edited=True)
        changes[(record.words[i].start, record.words[i].end)] = words[i]
    extra = dict(record.extra)
    extra["en_stale"] = True
    return _bumped(
        record,
        words=words,
        meta=meta,
        segments=_resync_segments(record.segments, changes),
        extra=extra,
    )


def retime(
    record: TranscriptRecord, index: int, *, start: float | None = None, end: float | None = None
) -> TranscriptRecord:
    """Move one word's start, end, or both.

    Each edge is clamped by its neighbour -- a word can never start before
    the one in front of it ends, nor end after the next one starts -- and
    then to ``MIN_WORD_SECONDS``. When the neighbours leave less room than
    that, the neighbours win: the word gets what there is."""
    _check_index(record, index)
    if start is None and end is None:
        raise ValueError("a retime needs a start, an end, or both")
    word = record.words[index]
    new_start = word.start if start is None else _seconds("start", start)
    new_end = word.end if end is None else _seconds("end", end)
    floor = record.words[index - 1].end if index > 0 else 0.0
    ceiling = record.words[index + 1].start if index + 1 < len(record.words) else math.inf
    new_start = max(new_start, floor)
    new_end = min(new_end, ceiling)
    if new_end - new_start < MIN_WORD_SECONDS:
        if start is not None and end is None:
            new_start = max(floor, new_end - MIN_WORD_SECONDS)
        elif end is not None and start is None:
            new_end = min(ceiling, new_start + MIN_WORD_SECONDS)
        else:
            new_end = min(ceiling, new_start + MIN_WORD_SECONDS)
    if new_end <= new_start:  # the neighbours left nothing at all
        raise ValueError("there is no room between the words on either side to move this one")
    words = list(record.words)
    words[index] = replace(word, start=round(new_start, 3), end=round(new_end, 3))
    meta = _meta_list(record)
    meta[index] = replace(meta[index], retimed=True)
    return _bumped(
        record,
        words=words,
        meta=meta,
        segments=_resync_segments(record.segments, {(word.start, word.end): words[index]}),
    )


def _seconds(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be a number of seconds, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return float(value)


def split(record: TranscriptRecord, index: int) -> TranscriptRecord:
    """This word starts a new caption line."""
    _check_index(record, index)
    if index == 0:
        raise ValueError("the first word already starts the first line")
    meta = _meta_list(record)
    meta[index] = replace(meta[index], break_before=True, no_break_before=False)
    return _bumped(record, meta=meta)


def merge(record: TranscriptRecord, index: int) -> TranscriptRecord:
    """This word does *not* start a new caption line -- it joins the one
    above. ``split`` and ``merge`` are exact opposites on the same index;
    "merge with the next line" is this, called with that line's first word."""
    _check_index(record, index)
    meta = _meta_list(record)
    meta[index] = replace(meta[index], break_before=False, no_break_before=True)
    return _bumped(record, meta=meta)


def set_style(record: TranscriptRecord, index: int, style: Any) -> TranscriptRecord:
    """Give one word its own colour, size, weight or slant -- or ``None`` to
    put it back to whatever the look does. The dict is validated here (this
    track owns the boundary); what the renderer does with it is track B's."""
    _check_index(record, index)
    parsed = style if isinstance(style, WordStyle) else parse_word_style(style)
    meta = _meta_list(record)
    meta[index] = replace(meta[index], style=parsed)
    return _bumped(record, meta=meta)


# ---------------------------------------------------------------------------
# reading the markers back out
# ---------------------------------------------------------------------------


def break_indexes(record: TranscriptRecord) -> tuple[frozenset[int], frozenset[int]]:
    """``(must start a new card, must not)`` -- the two sets
    ``engine.rules.build_cards`` takes, empty for an unedited record."""
    if record.meta is None:
        return frozenset(), frozenset()
    before = frozenset(i for i, m in enumerate(record.meta) if m.break_before)
    not_before = frozenset(i for i, m in enumerate(record.meta) if m.no_break_before)
    return before, not_before


def styled_words(record: TranscriptRecord) -> Iterable[tuple[Word, WordStyle]]:
    """Every word carrying an override, with it."""
    if record.meta is None:
        return ()
    return tuple((record.words[i], m.style) for i, m in enumerate(record.meta) if m.style is not None)
