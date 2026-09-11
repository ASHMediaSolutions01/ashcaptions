"""Where the bundled emoji artwork lives, and which names resolve.

Mirrors ``styles/sounds.py``, including the rule that matters: a name
that does not resolve to a file on disk comes back as ``None`` rather
than as a path. An ffmpeg input that cannot be opened fails the whole
burn, so "this build does not ship that emoji" has to degrade to a
missing sticker, never to a missing video.

There is no manifest here as there is for sounds. The directory listing
*is* the manifest: the artwork is a flat set of ``<name>.png`` files
fetched by ``scripts/fetch_emoji.py``, and a name is legitimate exactly
when its file is present.
"""

from __future__ import annotations

import re
from pathlib import Path

# Names are used as filenames, so they are restricted rather than
# escaped. Anything outside this cannot name a file at all, which closes
# the traversal question before it is asked.
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def assets_emoji_dir() -> Path:
    """Where the bundled emoji ``.png`` files live."""
    from ash_captions.config import app_root

    return app_root() / "assets" / "emoji"


def emoji_path(name: str, *, directory: Path | None = None) -> Path | None:
    """The ``.png`` an emoji name refers to, or None when it is not there."""
    if not isinstance(name, str) or not SAFE_NAME.match(name):
        return None
    path = (directory or assets_emoji_dir()) / f"{name}.png"
    return path if path.is_file() else None


def list_emoji(*, directory: Path | None = None) -> tuple[str, ...]:
    """Every emoji this build ships, by name, in a stable order."""
    folder = directory or assets_emoji_dir()
    if not folder.is_dir():
        return ()
    return tuple(sorted(
        p.stem for p in folder.glob("*.png") if SAFE_NAME.match(p.stem)
    ))


def is_emoji_bundled(name: str, *, directory: Path | None = None) -> bool:
    return emoji_path(name, directory=directory) is not None
