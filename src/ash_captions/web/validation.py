"""Request-boundary validation shared by more than one router (app.py's
job routes and routes_styles.py's preview route both need "is this a real,
readable video file on this machine").
"""
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .models import ALLOWED_VIDEO_EXTENSIONS


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
