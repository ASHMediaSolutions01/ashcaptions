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
2. ``download_fonts()`` actually fetches the .ttf files (and each
   family's licence text) from Google Fonts into this directory. It
   touches the network, so it must never run during tests and nothing
   here needs it to have run.

Also provides ``fontsdir_arg()``: the ffmpeg wiring so libass finds the
bundled fonts by directory instead of needing them installed into
Windows (spec 7A.4 / 8).

Manifest ``family`` names are the *face* names libass matches on -- the
font file's own family name (OpenType name ID 1), not the Google Fonts
catalogue name. Google serves weight variants as instanced files whose
family name carries the weight ("Baloo 2 SemiBold", "Fredoka Medium"),
and libass matches on that name only: a style asking for "Baloo 2"
against a file named "Baloo 2 SemiBold" silently renders in Arial. So a
manifest row is exactly what a style must write, and ``find_font_entry``
matches it exactly -- there is no prefix or base-family fallback that
could validate a name libass will then fail to find.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"
LICENSES_SUBDIR = "licenses"

# A non-browser User-Agent makes Google's CSS endpoint serve plain .ttf
# files (``format('truetype')``). Any browser-like UA -- including the
# "legacy Chrome" string often suggested for this -- gets .woff instead,
# which then ships under a .ttf name: libass (via FreeType) happens to
# read WOFF, but nothing else that is handed a "TrueType" file does.
_USER_AGENT = "AshCaptions-font-fetch/1.0 (+https://github.com/ASHMediaSolutions01/ashcaptions)"

# Magic numbers of the sfnt containers a .ttf may legitimately hold.
_SFNT_MAGICS = (b"\x00\x01\x00\x00", b"true", b"OTTO")

_LICENSE_TREE = "https://raw.githubusercontent.com/google/fonts/main"
# Google's fonts repo keeps each family under a directory named for its
# licence, with the licence text at a fixed filename per licence type.
_LICENSE_LAYOUT = {"OFL": ("ofl", "OFL.txt"), "APACHE": ("apache", "LICENSE.txt"), "UFL": ("ufl", "UFL.txt")}


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

    Exact match only: the manifest lists face names as libass sees them,
    so anything looser would validate a name that renders as Arial.
    """
    for entry in load_manifest(path=path):
        if entry.family == font_name:
            return entry
    return None


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
    """Fetch the bundled .ttf files -- and each family's licence text --
    from Google Fonts.

    Never called by the test suite and never required for it to pass --
    validation and rendering only need ``manifest.json``, which is
    committed to the repo. This is the one-time (or CI-time) step that
    actually populates ``assets/fonts/`` for a real build.
    """
    target_dir = dest or assets_fonts_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    entries = _selected_entries(families)

    written: list[Path] = []
    failed: list[tuple[str, str]] = []
    for entry in entries:
        out_path = target_dir / entry.file
        if out_path.exists() and not overwrite:
            written.append(out_path)
            continue
        # One family failing must never abandon the rest. Google Fonts
        # returns 400 for some family/weight combinations, and aborting
        # there left a real install with 6 of 24 faces -- every style then
        # silently falling back to a system font, which is exactly what
        # bundling exists to prevent.
        try:
            out_path.write_bytes(_fetch_font_bytes(entry))
        except Exception as exc:  # noqa: BLE001 -- any failure, keep going
            failed.append((entry.family, str(exc)))
            continue
        written.append(out_path)

    if failed:
        detail = "; ".join(f"{family} ({reason})" for family, reason in failed)
        print(
            f"WARNING: {len(failed)} of {len(entries)} fonts could not be "
            f"downloaded and will fall back to a system face: {detail}"
        )
    download_licenses(dest=target_dir, families=families, overwrite=overwrite)
    return written


def download_licenses(
    *,
    dest: Path | None = None,
    families: list[str] | None = None,
    overwrite: bool = False,
) -> list[Path]:
    """Fetch each family's licence text (OFL / Apache / UFL) into
    ``<dest>/licenses/``, at the path the manifest's ``license_file``
    names. A failure is reported and skipped, never raised: a missing
    licence must not abort a font fetch, but it must not go unnoticed
    either, so the build's notice check (``scripts/build.py``) is what
    ultimately refuses to ship without them.
    """
    target_dir = dest or assets_fonts_dir()
    written: list[Path] = []
    failed: list[tuple[str, str]] = []
    seen: set[Path] = set()
    for entry in _selected_entries(families):
        out_path = target_dir / entry.license_file
        if out_path in seen:
            continue
        seen.add(out_path)
        if out_path.exists() and not overwrite:
            written.append(out_path)
            continue
        try:
            url = _license_url(entry)
            request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                text = response.read().decode("utf-8")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 -- report, keep going
            failed.append((entry.family, str(exc)))
            continue
        written.append(out_path)
    if failed:
        detail = "; ".join(f"{family} ({reason})" for family, reason in failed)
        print(f"WARNING: {len(failed)} font licence file(s) could not be downloaded: {detail}")
    return written


def _selected_entries(families: list[str] | None) -> tuple[FontEntry, ...]:
    entries = load_manifest()
    if families is None:
        return entries
    wanted = set(families)
    return tuple(e for e in entries if e.family in wanted)


def _fetch_font_bytes(entry: FontEntry) -> bytes:
    css_url = (
        "https://fonts.googleapis.com/css2?family="
        + _google_family(entry).replace(" ", "+")
        + f":wght@{entry.weight}&display=swap"
    )
    css_request = urllib.request.Request(css_url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(css_request, timeout=30) as response:  # noqa: S310
        css_text = response.read().decode("utf-8")
    font_url = _extract_font_url(css_text)
    if font_url is None:
        raise RuntimeError(
            f"no font file URL in the Google Fonts CSS response for {entry.family!r}"
        )
    font_request = urllib.request.Request(font_url, headers={"User-Agent": _USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(font_request, timeout=30) as response:  # noqa: S310
        data = response.read()
    if not data.startswith(_SFNT_MAGICS):
        raise RuntimeError(
            f"Google served {entry.family!r} as {data[:4]!r}, not a TrueType/OpenType file"
        )
    return data


def _google_family(entry: FontEntry) -> str:
    """The family name Google Fonts' CSS API expects.

    Our manifest names faces as libass sees them -- "Montserrat
    ExtraBold" -- because that is how a style refers to them. Google has
    one family plus a ``wght`` axis, so asking for family="Montserrat
    ExtraBold" returns HTTP 400. The manifest's ``source`` specimen URL
    carries the real family, so prefer it and fall back to the declared
    family when there is no usable source.
    """
    source = (entry.source or "").rstrip("/")
    marker = "/specimen/"
    if marker in source:
        slug = source.rsplit(marker, 1)[1].split("?", 1)[0]
        if slug:
            return slug.replace("+", " ").replace("%20", " ")
    return entry.family


def _license_url(entry: FontEntry) -> str:
    """Where the google/fonts repo keeps this family's licence text."""
    key = entry.license.upper().split("-", 1)[0]
    try:
        directory, filename = _LICENSE_LAYOUT[key]
    except KeyError:
        raise RuntimeError(f"no known licence layout for {entry.license!r}") from None
    slug = _google_family(entry).replace(" ", "").lower()
    return f"{_LICENSE_TREE}/{directory}/{slug}/{filename}"


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

    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "download":
        paths = download_fonts()
        print(f"wrote {len(paths)} font file(s) to {assets_fonts_dir()}")
    elif command == "licenses":
        paths = download_licenses()
        print(f"wrote {len(paths)} licence file(s) to {assets_fonts_dir() / LICENSES_SUBDIR}")
    else:
        print("usage: python -m ash_captions.styles.fonts download|licenses")
