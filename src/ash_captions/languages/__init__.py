"""Language, dialect, spelling and glossary layer for ASH Captions
(spec section 7).

Public interface:

* ``list_languages`` / ``get_language`` / ``languages_by_band`` -- the
  catalogue (``catalog.py``), for the web UI to list supported languages.
* ``list_dialects`` / ``get_dialect`` -- the dialect presets available for
  a chosen language (``dialects.py``), for the web UI's dialect picker.
* ``resolve`` -- turn a (language, dialect) choice into a
  ``ResolvedDialect`` carrying the Whisper language code and priming
  prompt the engine needs to kick off transcription.
* ``load_glossary_entries`` -- read a client glossary file once per job.
* ``load_glossary_entries_for`` -- the shared glossary merged with one
  client's own (``client_glossary_path``), client entries winning.
* ``validate_glossary_text`` -- line-level problems in glossary text, for
  the control page saving a client's file.
* ``postprocess`` -- run the full post-processing chain (dialect glossary,
  then client glossary, then spelling normalisation) over transcribed
  text, for the engine to call once transcription finishes.
* ``postprocess_words`` -- the same chain over a sequence of word-timed
  tokens, so multi-word glossary phrases can fire across word boundaries
  without disturbing per-word timing.

Also re-exported for direct use: ``GlossaryEntry``, ``load_glossary``,
``apply_glossary`` (``glossary.py``); ``normalize_spelling`` and the
``EN_US`` / ``EN_UK`` / ``PT_BR`` / ``PT_PT`` convention constants
(``spelling.py``); ``LanguageInfo``, ``QualityBand``, ``ScriptDirection``
and ``DialectPreset`` for typing call sites.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from .catalog import (
    LanguageInfo,
    QualityBand,
    ScriptDirection,
    get_language,
    languages_by_band,
    list_languages,
)
from .dialects import DialectPreset, default_dialect, get_dialect, list_dialects
from .glossary import (
    SHARED_GLOSSARY_FILENAME,
    GlossaryEntry,
    apply_glossary,
    client_glossary_path,
    client_slug,
    load_glossary,
    parse_glossary,
    validate_glossary_text,
)
from .glossary import load_glossary_entries_for as _load_glossary_entries_for
from .spelling import EN_UK, EN_US, PT_BR, PT_PT, normalize_spelling

__all__ = [
    "EN_UK",
    "EN_US",
    "PT_BR",
    "PT_PT",
    "SHARED_GLOSSARY_FILENAME",
    "DialectPreset",
    "GlossaryEntry",
    "LanguageInfo",
    "QualityBand",
    "ResolvedDialect",
    "ScriptDirection",
    "apply_glossary",
    "client_glossary_path",
    "client_slug",
    "default_dialect",
    "get_dialect",
    "get_language",
    "languages_by_band",
    "list_dialects",
    "list_languages",
    "load_glossary",
    "load_glossary_entries",
    "load_glossary_entries_for",
    "normalize_spelling",
    "parse_glossary",
    "postprocess",
    "postprocess_words",
    "resolve",
    "validate_glossary_text",
]


@dataclass(frozen=True, slots=True)
class ResolvedDialect:
    """What the engine needs to run one job: the language, the dialect
    chosen (if any), the Whisper language code to pass in, the priming
    prompt, and the spelling convention to normalise toward afterward.
    """

    language: LanguageInfo
    dialect: DialectPreset | None
    whisper_language: str
    initial_prompt: str
    spelling_convention: str | None


def resolve(language_code: str, preset_id: str | None = None) -> ResolvedDialect:
    """Resolve a (language, dialect) choice for the engine.

    ``preset_id`` may be omitted, in which case the language's default
    dialect preset is used if one exists (e.g. Spanish defaults to
    Mexico, Portuguese to Brazil); languages with no presets at all
    (e.g. Italian) simply get an empty prompt and no spelling convention.

    Raises ``ValueError`` for an unknown language code or an unknown
    preset id for that language -- this is a system boundary (the web UI
    or engine passing user-chosen values), so bad input is rejected here
    rather than silently producing a wrong transcription.
    """

    if not language_code or not language_code.strip():
        raise ValueError("language_code is required")

    language = get_language(language_code)
    if language is None:
        raise ValueError(f"unknown language code: {language_code!r}")

    dialect: DialectPreset | None
    if preset_id:
        dialect = get_dialect(language.code, preset_id)
        if dialect is None:
            raise ValueError(
                f"unknown dialect preset {preset_id!r} for language {language.code!r}"
            )
    else:
        dialect = default_dialect(language.code)

    return ResolvedDialect(
        language=language,
        dialect=dialect,
        whisper_language=language.code,
        initial_prompt=dialect.initial_prompt if dialect else "",
        spelling_convention=dialect.spelling_convention if dialect else None,
    )


def load_glossary_entries(
    path: str | os.PathLike[str] | None,
) -> tuple[GlossaryEntry, ...]:
    """Read a client glossary file once, for passing as ``entries=`` to
    every ``postprocess`` call of a job. Never raises: a missing,
    unreadable or malformed file is an empty glossary."""

    return load_glossary(path)


def load_glossary_entries_for(
    glossary_dir: str | os.PathLike[str], client: str | None
) -> tuple[GlossaryEntry, ...]:
    """The shared ``glossary.txt`` merged with ``client``'s own file
    (``client_glossary_path``), the client's entries winning on the same
    match text. Loaded once per job like ``load_glossary_entries``; never
    raises. Reads go through this package's ``load_glossary`` name so each
    file is read exactly once per call."""

    return _load_glossary_entries_for(glossary_dir, client, loader=load_glossary)


def postprocess(
    text: str,
    resolved: ResolvedDialect,
    client_glossary_path: str | os.PathLike[str] | None = None,
    *,
    entries: Sequence[GlossaryEntry] | None = None,
) -> str:
    """Run the post-processing chain over transcribed text: dialect
    glossary corrections, then the client's own glossary corrections,
    then spelling normalisation toward the resolved dialect's convention.

    Pass the client glossary either as ``entries`` (already loaded with
    ``load_glossary_entries`` -- the right choice when this runs once per
    word, since it avoids a file read per call) or as
    ``client_glossary_path`` (read on every call). ``entries`` wins when
    both are given.

    Both glossary passes protect their inserted terms from the spelling
    pass that follows. A missing or malformed client glossary file is
    silently treated as empty (see ``glossary.load_glossary``) -- a bad
    glossary must never fail a job.
    """

    if not text:
        return text

    protected: set[str] = set()

    if resolved.dialect and resolved.dialect.glossary_entries:
        text, inserted = apply_glossary(text, resolved.dialect.glossary_entries)
        protected |= inserted

    client_entries = entries if entries is not None else load_glossary(client_glossary_path)
    if client_entries:
        text, inserted = apply_glossary(text, client_entries)
        protected |= inserted

    return normalize_spelling(text, resolved.spelling_convention, protected=protected)


def postprocess_words(
    words: Sequence[str],
    resolved: ResolvedDialect,
    client_glossary_path: str | os.PathLike[str] | None = None,
    *,
    entries: Sequence[GlossaryEntry] | None = None,
) -> tuple[str, ...]:
    """``postprocess`` for word-timed tokens, keeping one output token per
    input token so the caller's timings still line up.

    The tokens are joined with single spaces and post-processed as one
    run of text, which lets a multi-word glossary phrase ("ash captions
    => ASH Captions") match across word boundaries -- something a
    per-word call can never see. When a correction changes the number of
    whitespace-separated tokens (e.g. "New York => NYC"), or any input
    token itself contains whitespace, the joined result cannot be mapped
    back onto the input timings, so this falls back to processing each
    token on its own -- the same result the per-word path always gave.
    """

    if not words:
        return ()
    if any(not word or any(ch.isspace() for ch in word) for word in words):
        return tuple(
            postprocess(word, resolved, client_glossary_path, entries=entries) for word in words
        )

    joined = postprocess(" ".join(words), resolved, client_glossary_path, entries=entries)
    tokens = joined.split(" ")
    if len(tokens) == len(words) and all(tokens):
        return tuple(tokens)
    return tuple(
        postprocess(word, resolved, client_glossary_path, entries=entries) for word in words
    )
