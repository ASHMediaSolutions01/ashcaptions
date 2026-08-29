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
* ``postprocess`` -- run the full post-processing chain (dialect glossary,
  then client glossary, then spelling normalisation) over transcribed
  text, for the engine to call once transcription finishes.

Also re-exported for direct use: ``GlossaryEntry``, ``load_glossary``,
``apply_glossary`` (``glossary.py``); ``normalize_spelling`` and the
``EN_US`` / ``EN_UK`` / ``PT_BR`` / ``PT_PT`` convention constants
(``spelling.py``); ``LanguageInfo``, ``QualityBand``, ``ScriptDirection``
and ``DialectPreset`` for typing call sites.
"""

from __future__ import annotations

import os
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
from .glossary import GlossaryEntry, apply_glossary, load_glossary
from .spelling import EN_UK, EN_US, PT_BR, PT_PT, normalize_spelling

__all__ = [
    "EN_UK",
    "EN_US",
    "PT_BR",
    "PT_PT",
    "DialectPreset",
    "GlossaryEntry",
    "LanguageInfo",
    "QualityBand",
    "ResolvedDialect",
    "ScriptDirection",
    "apply_glossary",
    "default_dialect",
    "get_dialect",
    "get_language",
    "languages_by_band",
    "list_dialects",
    "list_languages",
    "load_glossary",
    "normalize_spelling",
    "postprocess",
    "resolve",
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


def postprocess(
    text: str,
    resolved: ResolvedDialect,
    client_glossary_path: str | os.PathLike[str] | None = None,
) -> str:
    """Run the post-processing chain over transcribed text: dialect
    glossary corrections, then the client's own glossary corrections,
    then spelling normalisation toward the resolved dialect's convention.

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

    client_entries = load_glossary(client_glossary_path)
    if client_entries:
        text, inserted = apply_glossary(text, client_entries)
        protected |= inserted

    return normalize_spelling(text, resolved.spelling_convention, protected=protected)
