"""The bundled sound-effect library (v0.7 section 1).

Exactly the shape of ``styles/fonts.py``, and for the same reasons:

1. Read ``assets/sounds/manifest.json`` -- small, committed, offline --
   rather than scanning the directory. A stray ``.wav`` dropped next to
   the real ones can never become a selectable sound, and a style naming
   a sound that is not in the manifest fails validation with a message
   saying so instead of burning silence.
2. Resolve the directory through ``config.app_root()``, so the same code
   finds the sounds beside ``AshCaptions.exe`` in a bundle and at the
   repo root in a source checkout.

The files themselves are synthesised by ``scripts/make_sounds.py``; see
that module for why they are not sampled from a library.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True, slots=True)
class SoundEntry:
    """One bundled sound, as listed in the manifest."""

    name: str
    label: str
    description: str
    file: str
    duration_seconds: float
    sample_rate: int = 48_000
    peak_dbfs: float = -1.5


def assets_sounds_dir() -> Path:
    """Where the bundled ``.wav`` files (and the manifest) live."""
    from ash_captions.config import app_root

    return app_root() / "assets" / "sounds"


def manifest_path() -> Path:
    return assets_sounds_dir() / MANIFEST_FILENAME


@lru_cache(maxsize=1)
def _load_manifest_cached(path_str: str) -> tuple[SoundEntry, ...]:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return tuple(
        SoundEntry(
            name=entry["name"],
            label=entry.get("label", entry["name"]),
            description=entry.get("description", ""),
            file=entry["file"],
            duration_seconds=float(entry.get("duration_seconds", 0.0)),
            sample_rate=int(entry.get("sample_rate", 48_000)),
            peak_dbfs=float(entry.get("peak_dbfs", -1.5)),
        )
        for entry in data["sounds"]
    )


def load_manifest(*, path: Path | None = None) -> tuple[SoundEntry, ...]:
    """Every bundled sound. Returns ``()`` when the manifest is missing.

    Missing rather than raising is deliberate and is the opposite of the
    font manifest's behaviour, because the consequences differ: without
    fonts nothing renders, while without sounds a look simply plays none.
    A source checkout that has never run ``scripts/make_sounds.py``, or a
    bundle built before v0.7, must still load and burn every style it has.
    """
    resolved = path or manifest_path()
    try:
        return _load_manifest_cached(str(resolved))
    except (OSError, ValueError, KeyError):
        return ()


def list_sound_names(*, path: Path | None = None) -> tuple[str, ...]:
    return tuple(entry.name for entry in load_manifest(path=path))


def find_sound_entry(name: str, *, path: Path | None = None) -> SoundEntry | None:
    for entry in load_manifest(path=path):
        if entry.name == name:
            return entry
    return None


def is_sound_bundled(name: str, *, path: Path | None = None) -> bool:
    return find_sound_entry(name, path=path) is not None


def sound_path(name: str, *, directory: Path | None = None) -> Path | None:
    """The ``.wav`` a style's sound name refers to, or ``None`` when the
    manifest does not list it or the file is not actually there.

    The file check is the point: a manifest entry whose ``.wav`` went
    missing must degrade to "no sound", never to an ffmpeg input that
    cannot be opened and fails the whole burn.
    """
    resolved_dir = directory or assets_sounds_dir()
    entry = find_sound_entry(name, path=resolved_dir / MANIFEST_FILENAME)
    if entry is None:
        return None
    path = resolved_dir / entry.file
    return path if path.is_file() else None
