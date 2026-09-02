"""Post-transcription spelling normalisation between spelling conventions
(spec section 7.2, lever 2) -- the client-visible one. A US client
receiving British spellings, or vice versa, looks sloppy; this rewrites
the output toward whichever convention the client dialect preset specifies.

Covers US vs UK/Commonwealth English, and Brazilian vs European
Portuguese, where they differ in common words. The word tables are
deliberately curated rather than suffix-rule-driven: pattern rules like
"-or -> -our" are prone to false positives on ordinary words (e.g. "motor",
"actor" are not dialect-variant nouns), so we only convert words we can
name explicitly. This keeps the table short of exhaustive, but safe.

All matching is word/phrase-boundary aware and case-preserving: "Color" ->
"Colour", "COLOR" -> "COLOUR". Terms passed in ``protected`` (typically
the output of ``glossary.apply_glossary``) are left untouched, so a
glossary-forced brand name or proper noun is never rewritten here. A few
words that routinely form proper nouns ("Labor Party", "Lincoln Center")
are additionally left alone when they appear Title-cased mid-sentence --
see ``_PROPER_NOUN_PRONE``.
"""

from __future__ import annotations

from collections.abc import Iterable

from ._textmatch import (
    build_alternation,
    find_spans,
    is_sentence_initial,
    match_case,
    overlaps_any,
)

EN_US = "en_US"
EN_UK = "en_UK"  # British/Commonwealth
PT_BR = "pt_BR"
PT_PT = "pt_PT"

# American -> British spelling. Deliberately excludes ambiguous pairs where
# the same word has a different spelling only in *some* senses (e.g.
# "meter"/"metre", "program"/"programme", "practice"/"practise",
# "license"/"licence", "tire"/"tyre", "curb"/"kerb", "story"/"storey") or
# words too likely to double as a proper noun ("gray"/"Grey" as a surname).
_EN_US_TO_UK: dict[str, str] = {
    # -or -> -our
    "color": "colour",
    "colors": "colours",
    "colored": "coloured",
    "coloring": "colouring",
    "colorful": "colourful",
    "favor": "favour",
    "favors": "favours",
    "favorite": "favourite",
    "favorites": "favourites",
    "favorable": "favourable",
    "honor": "honour",
    "honors": "honours",
    "honorable": "honourable",
    "humor": "humour",
    # NB: "humorous" is spelt the same in both conventions -- no entry.
    "labor": "labour",
    "labors": "labours",
    "neighbor": "neighbour",
    "neighbors": "neighbours",
    "neighborhood": "neighbourhood",
    "neighborhoods": "neighbourhoods",
    "rumor": "rumour",
    "rumors": "rumours",
    "vapor": "vapour",
    "armor": "armour",
    "behavior": "behaviour",
    "behaviors": "behaviours",
    "endeavor": "endeavour",
    "endeavors": "endeavours",
    "flavor": "flavour",
    "flavors": "flavours",
    "flavored": "flavoured",
    "harbor": "harbour",
    "odor": "odour",
    "valor": "valour",
    "vigor": "vigour",
    # -er -> -re
    "center": "centre",
    "centers": "centres",
    "centered": "centred",
    "theater": "theatre",
    "theaters": "theatres",
    "liter": "litre",
    "liters": "litres",
    "fiber": "fibre",
    "fibers": "fibres",
    "caliber": "calibre",
    "somber": "sombre",
    "saber": "sabre",
    # -ize/-yze -> -ise/-yse
    "organize": "organise",
    "organized": "organised",
    "organizing": "organising",
    "organization": "organisation",
    "organizations": "organisations",
    "realize": "realise",
    "realized": "realised",
    "realizing": "realising",
    "recognize": "recognise",
    "recognized": "recognised",
    "recognizing": "recognising",
    "apologize": "apologise",
    "apologized": "apologised",
    "criticize": "criticise",
    "criticized": "criticised",
    "emphasize": "emphasise",
    "emphasized": "emphasised",
    "finalize": "finalise",
    "finalized": "finalised",
    "finalizing": "finalising",
    "minimize": "minimise",
    "minimized": "minimised",
    "maximize": "maximise",
    "maximized": "maximised",
    "prioritize": "prioritise",
    "prioritized": "prioritised",
    "summarize": "summarise",
    "summarized": "summarised",
    "utilize": "utilise",
    "utilized": "utilised",
    "customize": "customise",
    "customized": "customised",
    "categorize": "categorise",
    "categorized": "categorised",
    "capitalize": "capitalise",
    "capitalized": "capitalised",
    "characterize": "characterise",
    "characterized": "characterised",
    "familiarize": "familiarise",
    "memorize": "memorise",
    "modernize": "modernise",
    "mobilize": "mobilise",
    "normalize": "normalise",
    "normalized": "normalised",
    "optimize": "optimise",
    "optimized": "optimised",
    "optimizing": "optimising",
    "specialize": "specialise",
    "specialized": "specialised",
    "standardize": "standardise",
    "sympathize": "sympathise",
    "synchronize": "synchronise",
    "visualize": "visualise",
    "visualized": "visualised",
    "analyze": "analyse",
    "analyzed": "analysed",
    "analyzing": "analysing",
    "paralyze": "paralyse",
    "paralyzed": "paralysed",
    # doubled consonant on -l- inflections
    "traveled": "travelled",
    "traveling": "travelling",
    "traveler": "traveller",
    "travelers": "travellers",
    "canceled": "cancelled",
    "canceling": "cancelling",
    "labeled": "labelled",
    "labeling": "labelling",
    "modeled": "modelled",
    "modeling": "modelling",
    "fueled": "fuelled",
    "fueling": "fuelling",
    "signaled": "signalled",
    "signaling": "signalling",
    "jeweler": "jeweller",
    "jewelry": "jewellery",
    "counselor": "counsellor",
    "counselors": "counsellors",
    "marvelous": "marvellous",
    "quarreled": "quarrelled",
    # -og -> -ogue
    "catalog": "catalogue",
    "catalogs": "catalogues",
    "dialog": "dialogue",
    "dialogs": "dialogues",
    "analog": "analogue",
    "epilog": "epilogue",
    # -ense -> -ence
    "defense": "defence",
    "offense": "offence",
    "pretense": "pretence",
    # misc
    "mustache": "moustache",
    "airplane": "aeroplane",
    "checkbook": "chequebook",
    "aluminum": "aluminium",
    "sulfate": "sulphate",
    "sulfur": "sulphur",
}
_EN_UK_TO_US: dict[str, str] = {v: k for k, v in _EN_US_TO_UK.items()}

# Words that are at least as often part of a proper noun as a common noun:
# "Labor Party", "Rockefeller Center", "Chicago Theatre". A Title-case hit
# on one of these that is *not* the first word of a sentence is left alone
# -- the capital is the only evidence we have, and rewriting a name is
# worse than leaving one spelling variant unconverted. Listed as the
# lowercase form in *either* convention so both directions are covered.
_PROPER_NOUN_PRONE: frozenset[str] = frozenset({
    "labor", "labour", "labors", "labours",
    "center", "centre", "centers", "centres",
    "theater", "theatre", "theaters", "theatres",
})

# Brazilian -> European Portuguese, *spelling and everyday lexical* pairs
# only, and only where the source word has no competing sense. Vocabulary
# pairs whose source word is also an ordinary adjective or has another
# common meaning are deliberately absent, however common the pair is in
# phrasebooks: "gelado" is also "cold" ("um chopp bem gelado" must never
# become "sorvete"), "sumo" is a sport, "descarga" is any discharge,
# "comboio" is also a convoy, "aterrar" is also to fill with earth.
_PT_BR_TO_PT: dict[str, str] = {
    "ônibus": "autocarro",
    "celular": "telemóvel",
    "geladeira": "frigorífico",
    "banheiro": "casa de banho",
    "xícara": "chávena",
    "esporte": "desporto",
    "esportes": "desportos",
    "café da manhã": "pequeno-almoço",
    "trem-bala": "comboio de alta velocidade",
}
_PT_PT_TO_BR: dict[str, str] = {v: k for k, v in _PT_BR_TO_PT.items()}


def _convention_map(convention: str | None) -> dict[str, str]:
    """The word map to *apply* to reach ``convention``: it maps the *other*
    convention's spellings (as found in raw transcript text) onto this
    convention's spellings.
    """

    if convention == EN_UK:
        return _EN_US_TO_UK
    if convention == EN_US:
        return _EN_UK_TO_US
    if convention == PT_PT:
        return _PT_BR_TO_PT
    if convention == PT_BR:
        return _PT_PT_TO_BR
    return {}


def known_conventions() -> tuple[str, ...]:
    return (EN_US, EN_UK, PT_BR, PT_PT)


def normalize_spelling(
    text: str, convention: str | None, protected: Iterable[str] = ()
) -> str:
    """Rewrite ``text`` toward ``convention``'s spelling. ``convention`` of
    ``None`` (languages with no spelling-convention split, e.g. plain
    Spanish or German) is a no-op that returns ``text`` unchanged.

    ``protected`` terms (case-insensitive, word/phrase-boundary matched --
    normally the terms a glossary pass just inserted) are never rewritten,
    even if they happen to overlap a convention word.
    """

    if not text or not convention:
        return text
    word_map = _convention_map(convention)
    if not word_map:
        return text

    pattern = build_alternation(word_map.keys())
    if pattern is None:
        return text

    protected_spans = find_spans(text, protected) if protected else []

    pieces: list[str] = []
    last_end = 0
    for match in pattern.finditer(text):
        span = match.span()
        if overlaps_any(span, protected_spans):
            continue
        start, end = span
        matched = match.group(0)
        if _looks_like_proper_noun(matched, text, start):
            continue
        pieces.append(text[last_end:start])
        replacement = word_map[matched.lower()]
        pieces.append(match_case(matched, replacement))
        last_end = end
    pieces.append(text[last_end:])
    return "".join(pieces)


def _looks_like_proper_noun(matched: str, text: str, start: int) -> bool:
    """A Title-case hit on a proper-noun-prone word that is not
    sentence-initial ("the Labor Party", "Rockefeller Center") -- see
    ``_PROPER_NOUN_PRONE``."""

    if matched.lower() not in _PROPER_NOUN_PRONE:
        return False
    if not matched[0].isupper() or matched.isupper():
        return False
    return not is_sentence_initial(text, start)
