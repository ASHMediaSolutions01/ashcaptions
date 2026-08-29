"""The language catalogue: every Whisper language ASH Captions exposes.

Whisper carries 99 languages. We expose the Latin-script ones plus Arabic,
banded by real-world accuracy (spec ss7.1). A language is a *data entry*
here, never a code path -- adding one is a new ``LanguageInfo`` in
``_LANGUAGES``, nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class QualityBand(str, Enum):
    """How much correction a caption editor should expect for a language."""

    FLAGSHIP = "flagship"  # ship without caveats (WER <~ 8%)
    STRONG = "strong"  # fine for client delivery after the normal skim
    WORKS = "works"  # usable, expect more correction


class ScriptDirection(str, Enum):
    LTR = "ltr"
    RTL = "rtl"


@dataclass(frozen=True, slots=True)
class LanguageInfo:
    """One entry in the language catalogue."""

    code: str  # Whisper/ISO-639-1 language code, e.g. "en"
    english_name: str
    native_name: str
    band: QualityBand
    direction: ScriptDirection = ScriptDirection.LTR


# Spec ss7.1 -- Flagship tier: English, Spanish, Portuguese, Italian, German,
# French, Dutch, Catalan, Polish, Indonesian.
_FLAGSHIP: tuple[LanguageInfo, ...] = (
    LanguageInfo("en", "English", "English", QualityBand.FLAGSHIP),
    LanguageInfo("es", "Spanish", "Espanol", QualityBand.FLAGSHIP),
    LanguageInfo("pt", "Portuguese", "Portugues", QualityBand.FLAGSHIP),
    LanguageInfo("it", "Italian", "Italiano", QualityBand.FLAGSHIP),
    LanguageInfo("de", "German", "Deutsch", QualityBand.FLAGSHIP),
    LanguageInfo("fr", "French", "Francais", QualityBand.FLAGSHIP),
    LanguageInfo("nl", "Dutch", "Nederlands", QualityBand.FLAGSHIP),
    LanguageInfo("ca", "Catalan", "Catala", QualityBand.FLAGSHIP),
    LanguageInfo("pl", "Polish", "Polski", QualityBand.FLAGSHIP),
    LanguageInfo("id", "Indonesian", "Bahasa Indonesia", QualityBand.FLAGSHIP),
)

# Spec ss7.1 -- Strong tier.
_STRONG: tuple[LanguageInfo, ...] = (
    LanguageInfo("ro", "Romanian", "Romana", QualityBand.STRONG),
    LanguageInfo("cs", "Czech", "Cestina", QualityBand.STRONG),
    LanguageInfo("sk", "Slovak", "Slovencina", QualityBand.STRONG),
    LanguageInfo("hr", "Croatian", "Hrvatski", QualityBand.STRONG),
    LanguageInfo("bs", "Bosnian", "Bosanski", QualityBand.STRONG),
    LanguageInfo("sl", "Slovenian", "Slovenscina", QualityBand.STRONG),
    LanguageInfo("hu", "Hungarian", "Magyar", QualityBand.STRONG),
    LanguageInfo("fi", "Finnish", "Suomi", QualityBand.STRONG),
    LanguageInfo("sv", "Swedish", "Svenska", QualityBand.STRONG),
    LanguageInfo("no", "Norwegian", "Norsk", QualityBand.STRONG),
    LanguageInfo("da", "Danish", "Dansk", QualityBand.STRONG),
    LanguageInfo("tr", "Turkish", "Turkce", QualityBand.STRONG),
    LanguageInfo("ms", "Malay", "Bahasa Melayu", QualityBand.STRONG),
    LanguageInfo("vi", "Vietnamese", "Tieng Viet", QualityBand.STRONG),
    LanguageInfo("tl", "Tagalog", "Tagalog", QualityBand.STRONG),
    LanguageInfo("gl", "Galician", "Galego", QualityBand.STRONG),
    LanguageInfo("af", "Afrikaans", "Afrikaans", QualityBand.STRONG),
    LanguageInfo("et", "Estonian", "Eesti", QualityBand.STRONG),
    LanguageInfo("lv", "Latvian", "Latviesu", QualityBand.STRONG),
    LanguageInfo("lt", "Lithuanian", "Lietuviu", QualityBand.STRONG),
    LanguageInfo("is", "Icelandic", "Islenska", QualityBand.STRONG),
    LanguageInfo("cy", "Welsh", "Cymraeg", QualityBand.STRONG),
    LanguageInfo("sw", "Swahili", "Kiswahili", QualityBand.STRONG),
    LanguageInfo("az", "Azerbaijani", "Azerbaycanca", QualityBand.STRONG),
)

# Spec ss7.1 -- Works tier.
_WORKS: tuple[LanguageInfo, ...] = (
    LanguageInfo("mt", "Maltese", "Malti", QualityBand.WORKS),
    LanguageInfo("eu", "Basque", "Euskara", QualityBand.WORKS),
    LanguageInfo("sq", "Albanian", "Shqip", QualityBand.WORKS),
    LanguageInfo("lb", "Luxembourgish", "Letzebuergesch", QualityBand.WORKS),
    LanguageInfo("oc", "Occitan", "Occitan", QualityBand.WORKS),
    LanguageInfo("jw", "Javanese", "Basa Jawa", QualityBand.WORKS),
    LanguageInfo("su", "Sundanese", "Basa Sunda", QualityBand.WORKS),
    LanguageInfo("so", "Somali", "Soomaali", QualityBand.WORKS),
    LanguageInfo("ha", "Hausa", "Hausa", QualityBand.WORKS),
    LanguageInfo("yo", "Yoruba", "Yoruba", QualityBand.WORKS),
    LanguageInfo("ln", "Lingala", "Lingala", QualityBand.WORKS),
    LanguageInfo("mi", "Maori", "Maori", QualityBand.WORKS),
    LanguageInfo("ht", "Haitian Creole", "Kreyol Ayisyen", QualityBand.WORKS),
    LanguageInfo("br", "Breton", "Brezhoneg", QualityBand.WORKS),
    LanguageInfo("fo", "Faroese", "Foroyskt", QualityBand.WORKS),
    LanguageInfo("tk", "Turkmen", "Turkmence", QualityBand.WORKS),
    LanguageInfo("sn", "Shona", "chiShona", QualityBand.WORKS),
    LanguageInfo("nn", "Norwegian Nynorsk", "Nynorsk", QualityBand.WORKS),
    LanguageInfo("la", "Latin", "Latina", QualityBand.WORKS),
)

# Spec ss7.3 -- Arabic: not Latin-script, added alongside the bands above.
# Whisper is solid on Modern Standard Arabic and weaker on conversational
# regional dialect (Egyptian/Gulf/Levantine), which puts it roughly in line
# with the Strong tier's "fine after the normal skim" expectation.
_ARABIC: LanguageInfo = LanguageInfo(
    "ar", "Arabic", "Al-Arabiyyah", QualityBand.STRONG, ScriptDirection.RTL
)

_LANGUAGES: tuple[LanguageInfo, ...] = _FLAGSHIP + _STRONG + _WORKS + (_ARABIC,)

_BY_CODE: dict[str, LanguageInfo] = {lang.code: lang for lang in _LANGUAGES}

if len(_BY_CODE) != len(_LANGUAGES):  # pragma: no cover - catalogue sanity check
    raise AssertionError("duplicate language code in catalogue")


def list_languages() -> tuple[LanguageInfo, ...]:
    """Return every catalogued language, Flagship first, then Strong, Works."""

    return _LANGUAGES


def get_language(code: str) -> LanguageInfo | None:
    """Look up a language by its Whisper/ISO-639-1 code (case-insensitive)."""

    if not code:
        return None
    return _BY_CODE.get(code.strip().lower())


def languages_by_band(band: QualityBand) -> tuple[LanguageInfo, ...]:
    """Return the catalogued languages in a given quality band, in list order."""

    return tuple(lang for lang in _LANGUAGES if lang.band is band)
