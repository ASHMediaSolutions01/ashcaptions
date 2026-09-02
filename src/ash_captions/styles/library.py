"""Loads styles from the shipped ``styles/`` directory and a per-editor
user directory (spec 7A.2, 7A.3).

Two different failure behaviours are deliberate, for two different
callers:

* ``validate_style_dict`` / ``load_style_file`` raise
  ``StyleValidationError`` -- used by the style editor (spec 7A.3) while
  someone is actively typing, where a precise error is the whole point.
* ``list_styles`` / ``resolve_style`` never raise for a bad *file* --
  used by the render pipeline, where spec 7A.4 is explicit: "A bad style
  file must never crash a job; it should be rejected with a clear error
  and the default style used." A broken shipped or user JSON file is
  logged and skipped rather than taking a whole job down with it.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from .schema import DEFAULT_STYLE, Style, StyleValidationError

logger = logging.getLogger(__name__)

__all__ = [
    "StyleValidationError",
    "shipped_styles_dir",
    "user_styles_dir",
    "validate_style_dict",
    "load_style_file",
    "list_styles",
    "resolve_style",
    "save_user_style",
    "delete_user_style",
    "is_shipped_style",
    "DEFAULT_STYLE",
]


def shipped_styles_dir() -> Path:
    """Where the built-in looks ship, e.g. ``styles/pop.json``.

    Resolved the same way ``config.app_root()`` resolves ``bin/``: beside
    the executable under PyInstaller onedir, or the repo root in a source
    checkout.
    """
    from ash_captions.config import app_root

    return app_root() / "styles"


def user_styles_dir() -> Path:
    """Where an editor's own saved styles live -- overrides a shipped
    style of the same name (spec 7A.2)."""
    override = os.environ.get("ASH_CAPTIONS_USER_STYLES_DIR")
    if override:
        return Path(override)
    from ash_captions.config import data_root

    return data_root() / "styles"


def validate_style_dict(data: dict) -> Style:
    """Validate a style edited in the style editor. Raises
    ``StyleValidationError`` with the exact field at fault."""
    return Style.from_dict(data)


def load_style_file(path: Path) -> Style:
    """Load and validate one style JSON file. Raises
    ``StyleValidationError`` (invalid content) or ``OSError``/
    ``json.JSONDecodeError`` (unreadable/malformed file)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Style.from_dict(raw)


def list_styles(*, shipped_dir: Path | None = None, user_dir: Path | None = None) -> dict[str, Style]:
    """Every valid style, shipped ones first, then user ones layered on
    top by name (spec 7A.2: "User-created styles ... override shipped
    ones by name"). A file that fails to parse or validate is logged and
    skipped -- never raised -- so one bad file can't break every other
    style, let alone a job (spec 7A.4)."""
    styles: dict[str, Style] = {}
    for directory in (shipped_dir or shipped_styles_dir(), user_dir or user_styles_dir()):
        for style in _load_directory(directory):
            styles[style.name] = style
    return styles


def resolve_style(
    name: str, *, shipped_dir: Path | None = None, user_dir: Path | None = None
) -> Style:
    """The style a job should actually render with. Never raises: an
    unknown name or a style file that failed validation both fall back to
    ``DEFAULT_STYLE`` rather than failing the job (spec 7A.4)."""
    styles = list_styles(shipped_dir=shipped_dir, user_dir=user_dir)
    style = styles.get(name)
    if style is None:
        logger.warning("style %r not found or invalid; falling back to the default style", name)
        return DEFAULT_STYLE
    return style


def save_user_style(style: Style, *, user_dir: Path | None = None) -> Path:
    """Write a validated style to the user styles directory, keyed by a
    filename derived from its name. Used by the style editor's save
    action (spec 7A.3).

    Written atomically (temp file + ``os.replace``) so a crash or a
    concurrent read mid-write can never leave a half-written JSON file
    that ``list_styles`` then skips as invalid -- silently losing the
    look the editor just saved.

    Raises ``ValueError`` (the web layer turns it into a 400) when the
    name's slug is a Windows reserved device name (``CON``, ``NUL``,
    ``COM1``...; those paths are not files on Windows at all) or when it
    collides with a *different* existing style name that slugifies the
    same way (``"Hype!"`` vs ``"hype"``) -- otherwise saving one would
    silently overwrite the other's file.
    """
    directory = user_dir or user_styles_dir()
    directory.mkdir(parents=True, exist_ok=True)
    slug = _slugify(style.name)
    if slug.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"style name {style.name!r} is a reserved Windows device name")
    path = directory / f"{slug}.json"
    existing = _existing_style_name(path)
    if existing is not None and existing != style.name and existing.casefold() != style.name.casefold():
        raise ValueError(
            f"style name {style.name!r} would overwrite the saved style {existing!r} "
            f"(both save as {path.name})"
        )
    payload = json.dumps(style.to_dict(), indent=2) + "\n"
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    os.replace(tmp_path, path)
    return path


_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _existing_style_name(path: Path) -> str | None:
    """The ``name`` of the style already saved at ``path``, or None if
    there is no readable style there."""
    if not path.is_file():
        return None
    try:
        return load_style_file(path).name
    except (StyleValidationError, OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def delete_user_style(name: str, *, user_dir: Path | None = None) -> bool:
    """Delete a saved user style by name (spec 7A.3's editor needs this
    to let an editor remove their own look). Only ever touches the user
    directory -- a shipped style can never be deleted this way, so
    ``is_shipped_style`` stays reliable. Returns True if a file was
    removed, False if no user style with that name existed (not an
    error: deleting something already gone is a no-op)."""
    directory = user_dir or user_styles_dir()
    if not directory.is_dir():
        return False
    for path in directory.glob("*.json"):
        try:
            style = load_style_file(path)
        except (StyleValidationError, OSError, json.JSONDecodeError, KeyError, TypeError):
            continue
        if style.name == name:
            path.unlink()
            return True
    return False


def is_shipped_style(name: str, *, shipped_dir: Path | None = None) -> bool:
    """True if ``name`` belongs to one of the built-in looks shipped in
    ``styles/`` -- regardless of whether a user override of the same name
    also exists. Lets the editor grey out "delete" for a built-in style
    (a user "delete" on a shipped name should only remove their
    override, if any, reverting to the shipped look -- see
    ``delete_user_style``)."""
    for style in _load_directory(shipped_dir or shipped_styles_dir()):
        if style.name == name:
            return True
    return False


def _load_directory(directory: Path) -> list[Style]:
    if not directory.is_dir():
        return []
    styles = []
    for path in sorted(directory.glob("*.json")):
        try:
            styles.append(load_style_file(path))
        except (StyleValidationError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("skipping invalid style file %s: %s", path, exc)
    return styles


def _slugify(name: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "style"
