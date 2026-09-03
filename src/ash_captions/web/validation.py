"""Request-boundary validation shared by more than one router (app.py's
job routes and routes_styles.py's preview route both need "is this a real,
readable video file on this machine"; the job routes, the client routes
and the watch folder all need "is this a usable client name").
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from .models import ALLOWED_VIDEO_EXTENSIONS

# A client name becomes a glossary file name (`<slug>.txt`) and a watch
# subfolder name, so it is held to what is safe as a single path segment
# on Windows: letters, digits, space, dot, underscore, hyphen; must start
# with a letter or digit (so no ".", "..", ".hidden"); no trailing dot.
MAX_CLIENT_NAME_LENGTH = 60
_CLIENT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
)
CLIENT_NAME_RULE = (
    f"letters, digits, spaces, dots, underscores or hyphens; up to {MAX_CLIENT_NAME_LENGTH} characters"
)


def sanitize_client_name(raw: str | None) -> str | None:
    """The cleaned client name, or None for an empty/absent one. Raises
    ``ValueError`` with a plain-English reason for anything that could not
    safely become a file or folder name."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("Client must be text.")
    name = raw.strip()
    if not name:
        return None
    if len(name) > MAX_CLIENT_NAME_LENGTH:
        raise ValueError(f"Client name is too long ({len(name)} characters; the limit is {MAX_CLIENT_NAME_LENGTH}).")
    if not _CLIENT_NAME_RE.match(name) or name.endswith("."):
        raise ValueError(f"Client name {name!r} can only use {CLIENT_NAME_RULE}, and must start with a letter or digit.")
    if name.split(".")[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"Client name {name!r} is a reserved name on Windows; pick another.")
    return name


def validate_client_name(raw: str | None) -> str | None:
    """``sanitize_client_name`` as a 400 for the routes."""
    try:
        return sanitize_client_name(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def clean_path_string(raw: str) -> str:
    """Strip whitespace and a pair of surrounding quotes.

    Windows Explorer's "Copy as path" wraps the result in double quotes
    (`"D:\\clip.mp4"`); pasted verbatim that would otherwise fail the
    exists() check and be the #1 support question.
    """
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"'):
        cleaned = cleaned[1:-1]
    return cleaned.strip()


def validate_local_path(raw_path: str) -> Path:
    cleaned = clean_path_string(raw_path)
    if not cleaned:
        raise HTTPException(status_code=400, detail="No file path provided.")

    path = Path(cleaned)
    if not path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Can't find {cleaned!r}. Check the path and try again.",
        )
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"{cleaned!r} is not a file.")
    if path.suffix.lower() not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type {path.suffix!r}. Expected a video file.",
        )
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Can't read {cleaned!r}: {exc.strerror or exc}.")

    return path
