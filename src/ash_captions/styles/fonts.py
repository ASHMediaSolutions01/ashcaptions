"""The bundled font set (spec 7A.4).

Styles are worthless if the font resolves differently on each of six
editors' machines -- that is the classic caption-styling support call.
So the installer ships a broad set of SIL Open Font License / Apache 2.0
faces (both permit redistribution) and no style may reference a font
outside that set.

This module has two jobs, kept deliberately separate:

1. Read ``assets/fonts/manifest.json`` -- a small, committed, offline
   data file listing every bundled family -- so ``schema.py`` can
   validate a style's ``font`` field with no network access and no
   dependency on the actual .ttf files being present. This is what
   ``schema.py`` and the test suite use.
2. ``download_fonts()`` actually fetches the .ttf files from Google
   Fonts into this directory. It touches the network, so it must never
   run during tests and nothing here needs it to have run.

Also provides ``fontsdir_arg()``: the ffmpeg wiring so libass finds the
bundled fonts by directory instead of needing them installed into
Windows (spec 7A.4 / 8).
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"

# Old User-Agent string that Google's fonts.googleapis.com CSS endpoint
# still recognises as "does not support woff2", so it serves plain .ttf
# instead of .woff2 -- the standard, widely used trick for fetching real
# TTF files from the Google Fonts CSS API without a build toolchain.
_LEGACY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 6.1) AppleWebKit/534.30 "
    "(KHTML, like Gecko) Chrome/12.0.742.112 Safari/534.30"
)


@dataclass(frozen=True, slots=True)
class FontEntry:
    """One bundled face, as listed in the manifest."""

    family: str
    category: str
    weight: int
    style: str
    file: str
    license: str
    license_file: str
    source: str = ""


def assets_fonts_dir() -> Path:
    """Where bundled font files (and the manifest) live.

    Resolved the same way ``config.app_root()`` resolves ``bin/``: beside
    the executable under PyInstaller onedir, or the repo root in a source
    checkout -- so this works unmodified in both.
    """
    from ash_captions.config import app_root

    return app_root() / "assets" / "fonts"


def manifest_path() -> Path:
    return assets_fonts_dir() / MANIFEST_FILENAME


@lru_cache(maxsize=1)
def _load_manifest_cached(path_str: str) -> tuple[FontEntry, ...]:
    path = Path(path_str)
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        FontEntry(
            family=entry["family"],
            category=entry.get("category", ""),
            weight=entry.get("weight", 400),
            style=entry.get("style", "normal"),
            file=entry["file"],
            license=entry.get("license", ""),
            license_file=entry.get("license_file", ""),
            source=entry.get("source", ""),
        )
        for entry in data["fonts"]
    )


def load_manifest(*, path: Path | None = None) -> tuple[FontEntry, ...]:
    """Every bundled font entry. Cached per manifest path -- this is read
    on every style validation, so re-parsing the JSON each time would be
    wasteful."""
    resolved = path or manifest_path()
    return _load_manifest_cached(str(resolved))


def list_font_families(*, path: Path | None = None) -> tuple[str, ...]:
    return tuple(entry.family for entry in load_manifest(path=path))


def find_font_entry(font_name: str, *, path: Path | None = None) -> FontEntry | None:
    """Resolve a style's ``font`` field to the manifest entry it names.

    Matches the exact family first (``"Inter"``), then a family used as a
    prefix with a trailing weight/variant word (``"Montserrat ExtraBold"``
    matching family ``"Montserrat ExtraBold"`` exactly, or -- if no exact
    entry exists for that combination -- falling back to the base family
    ``"Montserrat"``). This mirrors how the style JSON in spec 7A.2 names
    fonts: a full face name that may or may not have its own manifest row.
    """
    entries = load_manifest(path=path)
    by_family = {entry.family: entry for entry in entries}
    if font_name in by_family:
        return by_family[font_name]
    # Fall back to the base family for a "Family Weight" style name that
    # has no dedicated manifest row of its own.
    for entry in entries:
        if font_name.startswith(entry.family + " "):
            return entry
    base_family = font_name.split(" ")[0]
    return by_family.get(base_family)


def is_font_bundled(font_name: str, *, path: Path | None = None) -> bool:
    return find_font_entry(font_name, path=path) is not None


def fontsdir_arg(*, directory: Path | None = None) -> str:
    """The libass ``fontsdir`` value for the ffmpeg ``subtitles`` filter,
    so bundled fonts resolve without being installed into Windows."""
    resolved = directory or assets_fonts_dir()
    return str(resolved)


def download_fonts(
    *,
    dest: Path | None = None,
    families: list[str] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Fetch the bundled .ttf files from Google Fonts.

    Never called by the test suite and never required for it to pass --
    validation and rendering only need ``manifest.json``, which is
    committed to the repo. This is the one-time (or CI-time) step that
    actually populates ``assets/fonts/`` for a real build.
    """
    target_dir = dest or assets_fonts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    entries = load_manifest()
    if families is not None:
        wanted = set(families)
        entries = tuple(e for e in entries if e.family in wanted)

    written: list[Path] = []
    for entry in entries:
        out_path = target_dir / entry.file
        if out_path.exists() and not overwrite:
            written.append(out_path)
            continue
        css_url = (
            "https://fonts.googleapis.com/css2?family="
            + entry.family.replace(" ", "+")
            + f":wght@{entry.weight}&display=swap"
        )
        css_request = urllib.request.Request(
            css_url, headers={"User-Agent": _LEGACY_USER_AGENT}
        )
        with urllib.request.urlopen(css_request, timeout=30) as response:  # noqa: S310
            css_text = response.read().decode("utf-8")
        font_url = _extract_font_url(css_text)
        if font_url is None:
            raise RuntimeError(
                f"could not find a font file URL in the Google Fonts CSS "
                f"response for {entry.family!r}"
            )
        font_request = urllib.request.Request(
            font_url, headers={"User-Agent": _LEGACY_USER_AGENT}
        )
        with urllib.request.urlopen(font_request, timeout=30) as response:  # noqa: S310
            out_path.write_bytes(response.read())
        written.append(out_path)
    return written


def _extract_font_url(css_text: str) -> str | None:
    marker = "url("
    start = css_text.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = css_text.find(")", start)
    if end == -1:
        return None
    return css_text[start:end].strip("'\"")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "download":
        paths = download_fonts()
        print(f"wrote {len(paths)} font file(s) to {assets_fonts_dir()}")
    else:
        print("usage: python -m ash_captions.styles.fonts download")
