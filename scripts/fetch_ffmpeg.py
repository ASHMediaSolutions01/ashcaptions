"""Fetch the bundled ffmpeg/ffprobe binaries from BtbN's static Windows build.

Run on Ghazi's build machine only, before `build.py`:

    .venv/Scripts/python.exe scripts/fetch_ffmpeg.py

Why BtbN, and why the GPL variant (spec section 11.1): we ship `ffmpeg.exe`
inside our app rather than requiring editors to install it, which means *we*
are the ones redistributing it -- the GPL/LGPL question is about
redistributing our app, not about the (unencumbered) captioned video it
produces. We started on LGPL, which excludes libx264 (x264 is GPL), and the
burn-in fell back to libopenh264. Ghazi chose GPL on 2026-09-02 for libx264:
better quality per bitrate, `-preset`/`-crf` control, and a far faster CPU
encode on hour-long renders. The app only ever calls ffmpeg as a separate
process and is not linked to it, and the source is published under its own
terms, so shipping a GPL ffmpeg beside it is the ordinary arrangement.
BtbN's Windows builds are static (a single self-contained .exe, no DLL
fan-out to bundle correctly) and are the build most commonly pointed to for
exactly this "ship ffmpeg with your app" use case. `--variant lgpl` still
works if that ever needs to change back.

This script downloads a zip release asset, pulls `ffmpeg.exe`, `ffprobe.exe`
and the archive's `LICENSE.txt` out of it, verifies each binary actually
runs, and records the exact version banner each binary reports next to them
-- "what we shipped", not just "the tag we asked for", since a rolling
`latest` release can move. The licence text is not optional: we redistribute
a GPL binary, so `build.py` refuses to ship `bin/` without it (see
NOTICES.md at the repo root for the full third-party list).

Everything that touches the network lives in `download_file()` and
`resolve_release_url()`; the rest is pure/local and covered by tests.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError

REPO_ROOT = Path(__file__).resolve().parents[1]
# `bin/` beside the repo root, because that is where `config.find_binary()`
# looks -- `app_root()/bin/ffmpeg.exe`. Fetching into `build/ffmpeg/` meant
# a source checkout never found ffmpeg at all; only the packaged build did,
# since `build.py` copies it into the bundle's `bin/`. Same path either way
# now, so running from source behaves like the shipped app.
DEFAULT_DEST = REPO_ROOT / "bin"

# BtbN publishes a rolling "latest" release with these predictable asset
# names. "gpl" (not "gpl-shared") is the static build: one self-contained
# exe per tool, nothing else to bundle correctly.
RELEASES_API = "https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest"
ASSET_NAME_TEMPLATE = "ffmpeg-master-latest-win64-{variant}.zip"
DEFAULT_VARIANT = "gpl"

BINARY_NAMES = ("ffmpeg.exe", "ffprobe.exe")
# BtbN's archives carry the licence text at their top level. It ships in
# bin/ beside the binaries it covers.
LICENSE_NAME = "LICENSE.txt"
VERSION_FILE_NAME = "ffmpeg-build-info.txt"


class FetchError(Exception):
    pass


def default_asset_url(variant: str = DEFAULT_VARIANT) -> str:
    """The stable "always latest" download URL for a given build variant.

    This is a *convenience* URL that always points at the newest build --
    good for routine refreshes. `resolve_release_url()` additionally queries
    the API for the concrete asset URL and release tag when we want to know
    exactly what we asked for, ahead of the version banner we capture from
    the binary itself.
    """
    asset_name = ASSET_NAME_TEMPLATE.format(variant=variant)
    return f"https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{asset_name}"


@dataclass(frozen=True)
class ResolvedRelease:
    url: str
    tag_name: str | None
    asset_name: str


def resolve_release_url(variant: str = DEFAULT_VARIANT, *, timeout: float = 30) -> ResolvedRelease:
    """Ask the GitHub API which concrete release the rolling `latest` tag
    currently points to, and return the exact asset download URL.

    Falls back to the convenience URL (still correct, just without a
    resolved tag name to record) if the API call fails -- GitHub's API is
    rate-limited for unauthenticated callers, and this script must not be a
    hard dependency on that quota.
    """
    import json

    asset_name = ASSET_NAME_TEMPLATE.format(variant=variant)
    try:
        req = urllib.request.Request(
            RELEASES_API, headers={"Accept": "application/vnd.github+json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        tag_name = data.get("tag_name")
        for asset in data.get("assets", []):
            if asset.get("name") == asset_name:
                return ResolvedRelease(
                    url=asset["browser_download_url"], tag_name=tag_name, asset_name=asset_name
                )
        raise FetchError(f"release {tag_name!r} has no asset named {asset_name!r}")
    except (URLError, TimeoutError, ValueError) as exc:
        print(f"WARNING: could not resolve exact release via API ({exc}); using latest URL", file=sys.stderr)
        return ResolvedRelease(url=default_asset_url(variant), tag_name=None, asset_name=asset_name)


def download_file(url: str, dest: Path, *, timeout: float = 300) -> Path:
    """Download `url` to `dest`. The only function here that touches the
    network for the actual artifact."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as resp, dest.open("wb") as f:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    return dest


def verify_zip_integrity(zip_path: Path) -> None:
    """Raise FetchError if the downloaded zip is truncated or corrupt."""
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad is not None:
            raise FetchError(f"corrupt member in downloaded zip: {bad}")


def extract_binaries(zip_path: Path, dest_dir: Path) -> dict[str, Path]:
    """Pull ffmpeg.exe/ffprobe.exe and LICENSE.txt out of the (nested, e.g.
    `ffmpeg-*/bin/ffmpeg.exe`, `ffmpeg-*/LICENSE.txt`) archive, flattened
    into `dest_dir`.

    Pure filesystem logic given an already-downloaded zip -- exercised in
    tests against a small synthetic zip, no network involved. A missing
    licence text is an error like a missing binary: we ship a GPL build,
    and `build.py` refuses a `bin/` without it.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    wanted = (*BINARY_NAMES, LICENSE_NAME)
    found: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as zf:
        names_by_basename: dict[str, str] = {}
        for name in zf.namelist():
            basename = Path(name).name
            if basename in wanted and basename not in names_by_basename:
                names_by_basename[basename] = name
        missing = [b for b in BINARY_NAMES if b not in names_by_basename]
        if missing:
            raise FetchError(f"archive {zip_path} is missing expected binaries: {missing}")
        if LICENSE_NAME not in names_by_basename:
            raise FetchError(
                f"archive {zip_path} has no {LICENSE_NAME} -- refusing to ship a GPL ffmpeg "
                "without its licence text"
            )
        for basename, member_name in names_by_basename.items():
            out_path = dest_dir / basename
            with zf.open(member_name) as src, out_path.open("wb") as out:
                out.write(src.read())
            found[basename] = out_path
    return found


def verify_binary_runs(exe_path: Path, *, timeout: float = 15) -> str:
    """Run `<exe> -version` and return its first output line -- both a
    smoke test (does it even execute on this machine) and the "exact build
    version" text we record."""
    result = subprocess.run(
        [str(exe_path), "-version"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise FetchError(f"{exe_path} -version exited {result.returncode}: {result.stderr[:500]}")
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    if not first_line:
        raise FetchError(f"{exe_path} -version produced no output")
    return first_line


def write_version_file(
    dest_dir: Path,
    *,
    source_url: str,
    tag_name: str | None,
    binary_versions: dict[str, str],
) -> Path:
    """Record exactly what we shipped, beside the binaries, so a bug report
    six months from now can be matched to a known-good or known-bad build."""
    lines = [
        f"fetched: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"source_url: {source_url}",
        f"release_tag: {tag_name or '(unresolved -- fetched via rolling latest URL)'}",
        "",
    ]
    for name, version in sorted(binary_versions.items()):
        lines.append(f"{name}: {version}")
    out_path = Path(dest_dir) / VERSION_FILE_NAME
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--variant", default=DEFAULT_VARIANT, choices=["lgpl", "gpl"])
    parser.add_argument("--url", default=None, help="Override the asset URL entirely.")
    parser.add_argument(
        "--zip-cache",
        type=Path,
        default=None,
        help="Reuse an already-downloaded zip instead of fetching one (for offline retries).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.url:
        resolved = ResolvedRelease(url=args.url, tag_name=None, asset_name=Path(args.url).name)
    else:
        resolved = resolve_release_url(args.variant)

    if args.zip_cache and args.zip_cache.is_file():
        zip_path = args.zip_cache
        print(f"Using cached archive {zip_path}")
    else:
        zip_path = args.dest / resolved.asset_name
        print(f"Downloading {resolved.url}")
        download_file(resolved.url, zip_path)

    verify_zip_integrity(zip_path)
    extracted = extract_binaries(zip_path, args.dest)
    binaries = {name: path for name, path in extracted.items() if name in BINARY_NAMES}
    print(f"  {LICENSE_NAME}: {extracted[LICENSE_NAME]}")

    binary_versions = {name: verify_binary_runs(path) for name, path in binaries.items()}
    for name, version in binary_versions.items():
        print(f"  {name}: {version}")

    info_path = write_version_file(
        args.dest, source_url=resolved.url, tag_name=resolved.tag_name, binary_versions=binary_versions
    )
    print(f"Wrote {info_path}")
    print(f"ffmpeg binaries ready in {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
