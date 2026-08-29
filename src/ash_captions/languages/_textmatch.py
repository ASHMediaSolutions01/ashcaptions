"""Private word/phrase-boundary matching helpers shared by glossary.py and
spelling.py. Not part of the public interface -- import from the two
modules above instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def build_alternation(terms: Iterable[str]) -> re.Pattern[str] | None:
    """Compile a case-insensitive, word-boundary alternation over ``terms``.

    Longer terms are tried first so a multi-word phrase (e.g. "cafe da
    manha") wins over any shorter term that happens to be a substring of
    it. Returns ``None`` if ``terms`` is empty.
    """

    ordered = sorted({t for t in terms if t}, key=len, reverse=True)
    if not ordered:
        return None
    escaped = "|".join(re.escape(t) for t in ordered)
    return re.compile(rf"\b(?:{escaped})\b", re.IGNORECASE)


def find_spans(text: str, terms: Iterable[str]) -> list[tuple[int, int]]:
    """Return the (start, end) spans in ``text`` where any of ``terms``
    occurs, matched case-insensitively on word boundaries.
    """

    pattern = build_alternation(terms)
    if pattern is None:
        return []
    return [match.span() for match in pattern.finditer(text)]


def overlaps_any(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    start, end = span
    return any(start < s_end and end > s_start for s_start, s_end in spans)


def match_case(source: str, target: str) -> str:
    """Re-case ``target`` (assumed lowercase) to follow the casing pattern
    of ``source``: UPPER -> UPPER, Title -> Title, lower -> lower. Mixed or
    irregular casing falls back to capitalising only if ``source`` started
    with an uppercase letter, otherwise ``target`` is returned unchanged.
    """

    if not source:
        return target
    if source.isupper():
        return target.upper()
    if source.istitle():
        return target.title()
    if source.islower():
        return target
    if source[0].isupper():
        return target[0].upper() + target[1:]
    return target
