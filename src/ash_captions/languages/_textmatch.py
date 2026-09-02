"""Private word/phrase-boundary matching helpers shared by glossary.py and
spelling.py. Not part of the public interface -- import from the two
modules above instead.

Compiled alternations are cached: the runner calls the post-processing
chain once per transcribed *word* (15k calls on a 90-minute file), and
rebuilding a 200-term regex on every one of those calls was the single
largest cost in the whole post-processing stage.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache


def build_alternation(terms: Iterable[str]) -> re.Pattern[str] | None:
    """Compile a case-insensitive, boundary-aware alternation over ``terms``.

    Longer terms are tried first so a multi-word phrase (e.g. "cafe da
    manha") wins over any shorter term that happens to be a substring of
    it. Returns ``None`` if ``terms`` is empty.

    Boundaries are lookarounds applied only where a term's edge is a word
    character: ``\\b`` would silently fail on terms like ``C++`` or
    ``#hashtag`` (a ``\\b`` after ``+`` demands a following word char, so
    "C++ is" never matches), whereas ``(?<!\\w)C\\+\\+`` matches exactly
    where a reader expects it to.
    """

    ordered = tuple(sorted({t for t in terms if t}, key=len, reverse=True))
    if not ordered:
        return None
    return _compile_alternation(ordered)


@lru_cache(maxsize=128)
def _compile_alternation(ordered: tuple[str, ...]) -> re.Pattern[str]:
    branches = "|".join(_bounded(term) for term in ordered)
    return re.compile(f"(?:{branches})", re.IGNORECASE)


def _bounded(term: str) -> str:
    prefix = r"(?<!\w)" if _is_word_char(term[0]) else ""
    suffix = r"(?!\w)" if _is_word_char(term[-1]) else ""
    return f"{prefix}{re.escape(term)}{suffix}"


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


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
    of ``source``: UPPER -> UPPER, Title -> Capitalised, lower -> lower.
    Mixed or irregular casing falls back to capitalising only if
    ``source`` started with an uppercase letter, otherwise ``target`` is
    returned unchanged.

    "Title" means the *first letter* is uppercased, never ``str.title()``:
    a multi-word or hyphenated replacement ("casa de banho",
    "pequeno-almoço") must come out as "Casa de banho", not "Casa De
    Banho" -- the sentence-initial capital is the only one the source
    casing justifies.
    """

    if not source:
        return target
    if source.isupper():
        return target.upper()
    if source.islower():
        return target
    if source.istitle() or source[0].isupper():
        return target[:1].upper() + target[1:]
    return target


def is_sentence_initial(text: str, index: int) -> bool:
    """True if the character at ``index`` begins a sentence: it is at the
    start of ``text``, or everything between it and the previous
    sentence-ending punctuation / line break is whitespace or opening
    quotes/brackets.
    """

    i = index - 1
    while i >= 0 and (text[i].isspace() or text[i] in _OPENERS):
        if text[i] == "\n":
            return True
        i -= 1
    if i < 0:
        return True
    return text[i] in _SENTENCE_END


_OPENERS = "\"'“‘([{"
_SENTENCE_END = ".!?…"
