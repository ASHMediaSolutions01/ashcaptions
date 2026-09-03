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

Load the file once per job (``load_glossary``) and pass the resulting
entries to every ``apply_glossary`` / ``languages.postprocess`` call: the
compiled pattern is cached per entries object, so the per-word calls the
runner makes cost a regex scan each, not a file read plus a regex build.

Per-client glossaries
---------------------
The studio has several clients with their own brand and people names.
``<glossary_dir>/glossary.txt`` is the shared file every job gets;
``<glossary_dir>/<client slug>.txt`` (``client_glossary_path``) is one
client's own. ``load_glossary_entries_for`` merges the two with the
client's entries winning on the same match key, so "Gazi => Ghazi" in the
shared file and "Gazi => Gazi Holdings" in a client's file gives that
client's jobs the second spelling and everyone else the first.
"""

from __future__ import annotations

import logging
import os
import re
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ._textmatch import build_alternation

log = logging.getLogger(__name__)

# The glossary every job gets, client or not.
SHARED_GLOSSARY_FILENAME = "glossary.txt"

_SLUG_SEPARATOR_RE = re.compile(r"\s+")
_SLUG_FORBIDDEN = ("/", "\\", "\0")


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


def validate_glossary_text(text: str) -> list[str]:
    """Line-level problems in glossary file contents, as plain messages
    ("line 3: ..."). Empty when every line is a comment, blank, a bare
    term, or a complete ``wrong => right`` pair -- exactly the lines
    ``parse_glossary`` keeps. ``parse_glossary`` itself never raises (a
    bad glossary must never fail a job); this is for the editor saving
    one, where a skipped line should be a refusal, not a silent drop.
    """

    problems: list[str] = []
    for number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or "=>" not in line:
            continue
        left, _, right = line.partition("=>")
        left, right = left.strip(), right.strip()
        if "=>" in right:
            problems.append(f"line {number}: more than one '=>' -- one correction per line")
        elif not left and not right:
            problems.append(f"line {number}: '=>' with nothing on either side")
        elif not left:
            problems.append(f"line {number}: nothing before '=>' (expected 'wrong spelling => Right Spelling')")
        elif not right:
            problems.append(f"line {number}: nothing after '=>' (expected 'wrong spelling => Right Spelling')")
    return problems


def client_slug(client: str) -> str:
    """The file stem for a client's glossary: trimmed, lower-cased, runs of
    whitespace replaced by ``-`` ("Acme Corp" -> "acme-corp")."""

    return _SLUG_SEPARATOR_RE.sub("-", client.strip()).lower()


def client_glossary_path(glossary_dir: str | os.PathLike[str], client: str) -> Path:
    """``<glossary_dir>/<client_slug(client)>.txt``.

    The web layer sanitizes client names at its boundary; this is the
    belt-and-braces check at the point a name becomes a path. Raises
    ``ValueError`` for an empty slug, one containing a path separator,
    a dot-only name, or one that would collide with the shared file.
    """

    slug = client_slug(client)
    if not slug or slug.strip(".") == "" or any(ch in slug for ch in _SLUG_FORBIDDEN):
        raise ValueError(f"{client!r} is not a usable client name")
    if f"{slug}.txt" == SHARED_GLOSSARY_FILENAME:
        raise ValueError(f"{client!r} would collide with the shared glossary file")
    return Path(glossary_dir) / f"{slug}.txt"


def merge_glossary_entries(
    shared: Sequence[GlossaryEntry], client: Sequence[GlossaryEntry]
) -> tuple[GlossaryEntry, ...]:
    """``shared`` then ``client``, with a client entry replacing any shared
    entry that has the same (case-insensitive) match text -- the client's
    spelling of a name is the one that wins on that client's jobs."""

    overridden = {entry.match.lower() for entry in client}
    kept = tuple(entry for entry in shared if entry.match.lower() not in overridden)
    return kept + tuple(client)


def load_glossary_entries_for(
    glossary_dir: str | os.PathLike[str],
    client: str | None,
    *,
    loader: Callable[[Path], tuple[GlossaryEntry, ...]] | None = None,
) -> tuple[GlossaryEntry, ...]:
    """The shared glossary merged with ``client``'s own (if any), client
    entries winning on conflicts. Never raises: a missing or malformed
    file is an empty glossary, and a client name that can't be a path is
    logged and treated as "no client file". Logs at INFO which files were
    read and how many entries each contributed, so a job's log answers
    "which glossary applied?" without guessing.

    ``loader`` (default ``load_glossary``) is how each file is read -- the
    package-level wrapper passes its own name so a caller counting reads
    through ``languages.load_glossary`` sees exactly one per file.
    """

    read = loader or load_glossary
    directory = Path(glossary_dir)
    shared_path = directory / SHARED_GLOSSARY_FILENAME
    shared = read(shared_path)
    log.info("glossary: %s (%d entries)", shared_path, len(shared))
    if not client or not client.strip():
        return shared
    try:
        path = client_glossary_path(directory, client)
    except ValueError as exc:
        log.warning("glossary: no client file for %r: %s", client, exc)
        return shared
    own = read(path)
    log.info("glossary: client %r -> %s (%d entries)", client, path, len(own))
    return merge_glossary_entries(shared, own) if own else shared


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


# Compiled (by_match, pattern) per entries object. Keyed on id() and kept
# alive by holding the entries themselves, so an id can never be recycled
# to a different object while its cache row exists. Bounded because a
# long-running app sees a new tuple per job (one per glossary load).
_COMPILED: OrderedDict[int, tuple[Sequence[GlossaryEntry], dict[str, str], re.Pattern[str] | None]] = (
    OrderedDict()
)
_COMPILED_MAX = 16


def _compiled(entries: Sequence[GlossaryEntry]) -> tuple[dict[str, str], re.Pattern[str] | None]:
    key = id(entries)
    row = _COMPILED.get(key)
    if row is not None and row[0] is entries:
        _COMPILED.move_to_end(key)
        return row[1], row[2]

    by_match: dict[str, str] = {}
    for entry in entries:
        by_match[entry.match.lower()] = entry.replacement
    pattern = build_alternation(by_match.keys())

    _COMPILED[key] = (entries, by_match, pattern)
    while len(_COMPILED) > _COMPILED_MAX:
        _COMPILED.popitem(last=False)
    return by_match, pattern


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

    by_match, pattern = _compiled(entries)
    if pattern is None:
        return text, frozenset()

    inserted: set[str] = set()

    def _replace(m: re.Match[str]) -> str:
        replacement = by_match[m.group(0).lower()]
        inserted.add(replacement)
        return replacement

    new_text = pattern.sub(_replace, text)
    return new_text, frozenset(inserted)
