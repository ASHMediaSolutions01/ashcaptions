"""Per-client glossary loading and application (spec ss7.2, lever 3).

A glossary is a plain text file, one rule per line:

    # a comment (ignored, as are blank lines)
    Ash Media Solutions
    Gazi => Ghazi
    ash captions => ASH Captions

A bare line is a *known-good term*: whenever it appears in the transcript
(any casing), it is forced to exactly that spelling. A ``wrong => right``
line is a *correction*: whenever the left side appears, it is replaced with
the exact right side. Matching is always case-insensitive and
word/phrase-boundary aware.

Glossary correction runs before spelling normalisation, and every term it
inserts is exempt from that later pass -- see ``apply_glossary``'s return
value and ``spelling.normalize_spelling``'s ``protected`` parameter.

A missing or malformed glossary file never raises: it is treated as an
empty glossary, and malformed individual lines are skipped rather than
failing the whole file.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ._textmatch import build_alternation


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    """One glossary rule: replace ``match`` (case-insensitive) with the
    exact text ``replacement``.
    """

    match: str
    replacement: str


def parse_glossary(text: str) -> tuple[GlossaryEntry, ...]:
    """Parse glossary file contents into entries. Skips blank lines, ``#``
    comments, and any malformed line (e.g. ``wrong =>`` with no right-hand
    side) instead of raising.
    """

    entries: list[GlossaryEntry] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            left, _, right = line.partition("=>")
            left, right = left.strip(), right.strip()
            if not left or not right:
                continue  # malformed correction pair -- skip, don't crash
            entries.append(GlossaryEntry(left, right))
        else:
            entries.append(GlossaryEntry(line, line))
    return tuple(entries)


def load_glossary(path: str | os.PathLike[str] | None) -> tuple[GlossaryEntry, ...]:
    """Load and parse a glossary file. Returns an empty tuple (never
    raises) if ``path`` is falsy, the file is missing, unreadable, not a
    file, or not valid UTF-8 text.
    """

    if not path:
        return ()
    try:
        candidate = Path(path)
        if not candidate.is_file():
            return ()
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return ()
    except UnicodeDecodeError:
        return ()
    return parse_glossary(text)


def apply_glossary(
    text: str, entries: Sequence[GlossaryEntry]
) -> tuple[str, frozenset[str]]:
    """Apply glossary corrections to ``text``.

    Returns ``(corrected_text, protected_terms)`` where ``protected_terms``
    are the exact replacement strings that were inserted -- pass these to
    ``spelling.normalize_spelling(..., protected=protected_terms)`` so the
    spelling pass leaves forced glossary terms alone.
    """

    if not entries or not text:
        return text, frozenset()

    by_match: dict[str, str] = {}
    for entry in entries:
        by_match[entry.match.lower()] = entry.replacement

    pattern = build_alternation(by_match.keys())
    if pattern is None:
        return text, frozenset()

    inserted: set[str] = set()

    def _replace(m: re.Match[str]) -> str:
        replacement = by_match[m.group(0).lower()]
        inserted.add(replacement)
        return replacement

    new_text = pattern.sub(_replace, text)
    return new_text, frozenset(inserted)
