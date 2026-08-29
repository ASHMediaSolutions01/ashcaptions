"""The dialect preset layer (spec section 7.2).

Whisper has one model per *language*, not per dialect -- there is no
``es-MX`` model, and accent robustness already handles regional accents
well. What a client actually notices is regional *vocabulary* and
*spelling* in the output text, and that is controlled with three cheap
levers stacked on top of the plain language code:

1. ``initial_prompt`` priming -- biases Whisper's vocabulary and phrasing
   toward the variant.
2. A spelling convention (see ``spelling.py``) -- the client-visible one.
3. An optional per-dialect glossary, stacked under the client's own
   ``glossary.txt`` (see ``glossary.py``).

Each preset here is a config entry: language code + prompt + spelling
convention + optional glossary. Adding "Peruvian Spanish" later is a new
``DialectPreset`` tuple entry, not a release.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import spelling
from .glossary import GlossaryEntry


@dataclass(frozen=True, slots=True)
class DialectPreset:
    """One dialect preset for a catalogued language."""

    language_code: str  # must match a catalog.LanguageInfo.code
    preset_id: str  # short slug, unique within language_code, e.g. "us"
    label: str  # display label, e.g. "English (US)"
    initial_prompt: str
    spelling_convention: str | None = None  # a spelling.EN_US / EN_UK / ... constant
    glossary_entries: tuple[GlossaryEntry, ...] = field(default_factory=tuple)
    is_default: bool = False  # the preset used when none is explicitly chosen


_DIALECTS: tuple[DialectPreset, ...] = (
    # -- English --------------------------------------------------------
    DialectPreset(
        "en", "us", "English (US)",
        "The color of the truck in the parking lot needs to be "
        "double-checked before we finalize the schedule.",
        spelling.EN_US, is_default=True,
    ),
    DialectPreset(
        "en", "uk", "English (UK)",
        "The colour of the lorry in the car park needs to be "
        "double-checked before we finalise the schedule.",
        spelling.EN_UK,
    ),
    DialectPreset(
        "en", "au", "English (Australian)",
        "The bloke driving the ute pulled into the servo for a snag and "
        "a can of soft drink, no worries.",
        spelling.EN_UK,
    ),
    DialectPreset(
        "en", "ca", "English (Canadian)",
        "We grabbed a double-double at Tim Hortons before heading down "
        "to the basement to watch the hockey game, eh.",
        spelling.EN_UK,
    ),
    DialectPreset(
        "en", "in", "English (Indian)",
        "Please do the needful and revert on this matter by tomorrow, "
        "otherwise we will have to prepone the meeting.",
        spelling.EN_UK,
    ),
    DialectPreset(
        "en", "ie", "English (Irish)",
        "Yer man was after fixing the car below in the station car park, "
        "so he was, grand altogether.",
        spelling.EN_UK,
    ),
    DialectPreset(
        "en", "za", "English (South African)",
        "Just now we'll head to the robot near the bakkie, hey, and "
        "grab some biltong for the braai.",
        spelling.EN_UK,
    ),
    # -- Spanish ----------------------------------------------------------
    DialectPreset(
        "es", "mx", "Spanish (Mexico)",
        "Ahorita vamos a platicar con el compa sobre la troca y checamos "
        "los boletos para el camión.",
        is_default=True,
    ),
    DialectPreset(
        "es", "es", "Spanish (Spain)",
        "Vale, ahora mismo cogemos el coche y vamos a la tienda a por el "
        "móvil, tío, que hay un atasco enorme.",
    ),
    DialectPreset(
        "es", "ar", "Spanish (Argentina)",
        "Che, vos tenés que agarrar el colectivo y hablame por WhatsApp "
        "cuando llegués a la esquina.",
    ),
    DialectPreset(
        "es", "co", "Spanish (Colombia)",
        "Parce, ¿qué más? Vamos a coger el bus y de una nos tomamos un "
        "tinto en la tienda de la esquina.",
    ),
    DialectPreset(
        "es", "cl", "Spanish (Chile)",
        "Oye, cachái que la micro se atrasó caleta y ahora andamos "
        "apurados al tiro.",
    ),
    DialectPreset(
        "es", "us", "Spanish (US Latino)",
        "Vamos a parquear la troca y luego te llamo pa' atrás para "
        "confirmar la cita, ¿va?",
    ),
    # -- Portuguese ---------------------------------------------------------
    DialectPreset(
        "pt", "br", "Portuguese (Brazil)",
        "A gente vai pegar o ônibus até o shopping e depois passar no "
        "açougue para comprar a carne, tá bom?",
        spelling.PT_BR, is_default=True,
    ),
    DialectPreset(
        "pt", "pt", "Portuguese (Portugal)",
        "Nós vamos apanhar o autocarro até ao centro comercial e depois "
        "passar no talho para comprar a carne, está bem?",
        spelling.PT_PT,
    ),
    # -- French -----------------------------------------------------------
    DialectPreset(
        "fr", "fr", "French (France)",
        "On va prendre le week-end pour faire du shopping et ensuite "
        "garer la voiture au parking avant de rentrer.",
        is_default=True,
    ),
    DialectPreset(
        "fr", "ca", "French (Quebec)",
        "On va magasiner cet après-midi pis stationner le char dans le "
        "stationnement avant de retourner à la maison.",
    ),
    # -- German -------------------------------------------------------------
    DialectPreset(
        "de", "de", "German (Germany)",
        "Wir fahren mit dem Auto zum Bahnhof und kaufen dort ein Handy "
        "sowie ein paar Brötchen.",
        is_default=True,
    ),
    DialectPreset(
        "de", "at", "German (Austria)",
        "Wir fahren mit dem Auto zum Bahnhof und kaufen dort ein Handy "
        "sowie ein paar Semmeln, servus.",
    ),
    DialectPreset(
        "de", "ch", "German (Switzerland)",
        "Wir fahren mit dem Velo zum Bahnhof und kaufen dort ein Natel "
        "sowie ein Gipfeli für den Znüni.",
    ),
    # -- Dutch --------------------------------------------------------------
    DialectPreset(
        "nl", "nl", "Dutch (Netherlands)",
        "We gaan straks met de fiets naar de supermarkt om een lekkere "
        "kroket te halen, gezellig toch?",
        is_default=True,
    ),
    DialectPreset(
        "nl", "be", "Dutch (Flemish)",
        "We gaan seffens met de velo naar de winkel om een lekker "
        "frietje te halen, hé, dat is toch plezant.",
    ),
)

_BY_LANGUAGE: dict[str, tuple[DialectPreset, ...]] = {}
for _preset in _DIALECTS:
    _BY_LANGUAGE.setdefault(_preset.language_code, ())
    _BY_LANGUAGE[_preset.language_code] += (_preset,)

_BY_KEY: dict[tuple[str, str], DialectPreset] = {
    (p.language_code, p.preset_id): p for p in _DIALECTS
}
if len(_BY_KEY) != len(_DIALECTS):  # pragma: no cover - catalogue sanity check
    raise AssertionError("duplicate (language_code, preset_id) in dialect presets")

_DEFAULTS: dict[str, DialectPreset] = {}
for _presets in _BY_LANGUAGE.values():
    _defaults_for_lang = [p for p in _presets if p.is_default]
    if len(_defaults_for_lang) > 1:  # pragma: no cover - catalogue sanity check
        raise AssertionError(
            f"more than one default dialect for {_presets[0].language_code!r}"
        )
    if _defaults_for_lang:
        _DEFAULTS[_presets[0].language_code] = _defaults_for_lang[0]


def list_dialects(language_code: str) -> tuple[DialectPreset, ...]:
    """Return the dialect presets available for a language code. Empty for
    a valid language with no presets (e.g. Italian, Polish).
    """

    if not language_code:
        return ()
    return _BY_LANGUAGE.get(language_code.strip().lower(), ())


def get_dialect(language_code: str, preset_id: str) -> DialectPreset | None:
    """Look up one dialect preset by (language_code, preset_id), both
    case-insensitive.
    """

    if not language_code or not preset_id:
        return None
    return _BY_KEY.get((language_code.strip().lower(), preset_id.strip().lower()))


def default_dialect(language_code: str) -> DialectPreset | None:
    """The preset used when a language has dialects but none was chosen
    explicitly. ``None`` if the language has no presets at all.
    """

    if not language_code:
        return None
    return _DEFAULTS.get(language_code.strip().lower())
