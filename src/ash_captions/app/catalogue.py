"""Bridges web's ``LanguageCatalogueProvider`` protocol onto
``ash_captions.languages`` (spec section 7).

The dialect *code* exposed to the web layer -- and round-tripped through
``JobOptions.dialect`` on a submitted job -- is ``{language_code}-{PRESET
ID UPPERCASE}`` (e.g. ``"es-MX"``), matching the examples in
``web.models.JobPathRequest``. ``languages.resolve()`` wants the bare,
lowercase ``preset_id`` instead (e.g. ``"mx"``); ``dialect_preset_id()``
below is the inverse mapping ``runner.py`` uses to get back there.
"""

from __future__ import annotations

from ash_captions import languages
from ash_captions.web.models import Dialect, Language


def _dialect_code(language_code: str, preset_id: str) -> str:
    return f"{language_code}-{preset_id.upper()}"


def dialect_preset_id(language_code: str, dialect_code: str | None) -> str | None:
    """Recover the bare preset id ``languages.resolve()`` expects (e.g.
    ``"mx"``) from a web dialect code (e.g. ``"es-MX"``).

    Returns ``None`` for no dialect, or one that doesn't belong to
    ``language_code`` -- callers pass that straight through to
    ``resolve()``, which treats "no preset" as "use the language's
    default dialect, if any."
    """
    if not dialect_code:
        return None
    prefix = f"{language_code}-".lower()
    if not dialect_code.lower().startswith(prefix):
        return None
    return dialect_code[len(prefix):].lower()


def _label(info: languages.LanguageInfo) -> str:
    """A dropdown-friendly label for a non-technical editor: the English
    name, with the native name alongside where they differ (e.g. "Spanish
    (Español)"); just the English name when they're the same (e.g.
    "English", "Tagalog").
    """
    if info.native_name and info.native_name != info.english_name:
        return f"{info.english_name} ({info.native_name})"
    return info.english_name


def _to_dialect(preset: languages.DialectPreset) -> Dialect:
    return Dialect(
        code=_dialect_code(preset.language_code, preset.preset_id),
        label=preset.label,
    )


def _to_language(info: languages.LanguageInfo) -> Language:
    dialects = [_to_dialect(preset) for preset in languages.list_dialects(info.code)]
    return Language(code=info.code, label=_label(info), band=info.band.value, dialects=dialects)


class LanguageCatalogue:
    """Implements web's ``LanguageCatalogueProvider`` over
    ``ash_captions.languages``."""

    def list_languages(self) -> list[Language]:
        return [_to_language(info) for info in languages.list_languages()]
