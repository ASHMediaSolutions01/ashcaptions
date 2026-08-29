"""Developer-side build: produce the PyInstaller `onedir` bundle.

Run on Ghazi's build machine only -- never on an editor's PC. Usage:

    .venv/Scripts/python.exe scripts/build.py
    .venv/Scripts/python.exe scripts/build.py --dry-run   # print the
        PyInstaller command without running it or requiring PyInstaller
        to be installed

Why `onedir` and not `onefile`: `onefile` unpacks the whole app to a temp
directory on *every launch*, which is painful with a multi-GB payload
(bundled ffmpeg + a pre-seeded Whisper model) and means an update has to
replace a running exe atomically. `onedir` is a folder that
`installer/install.ps1` can drop into place and that a future update can
patch file-by-file. See spec section 11.1.

This module is imported by tests for its pure argument-construction and
validation functions. PyInstaller itself is imported lazily inside
`run_pyinstaller()` so importing this module -- or running the test suite --
never requires PyInstaller to be installed.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkgtools.manifest import Artifact, build_artifact  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
PACKAGE_DIR = SRC_DIR / "ash_captions"
STATIC_DIR = PACKAGE_DIR / "web" / "static"
ENTRY_SCRIPT = PACKAGE_DIR / "__main__.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
PKGTOOLS_DIR = Path(__file__).resolve().parent / "pkgtools"
# Top-level, sibling of src/ -- NOT the src/ash_captions/styles Python
# package (which contains the *code* that reads these). Both are read via
# `app_root()`, same as bin/ and models/, so both must ship the same way.
STYLES_DIR = REPO_ROOT / "styles"
FONTS_DIR = REPO_ROOT / "assets" / "fonts"

APP_NAME = "AshCaptions"

# The files whose absence is "the classic PyInstaller failure" the task
# calls out by name: a missing static dir means the control page 404s with
# no obvious cause on an editor's machine, three time zones away from a
# terminal that could tell them why.
REQUIRED_STATIC_FILES = ("index.html", "app.js", "style.css")

# src/ash_captions/app/updater.py reuses this module's version-comparison
# and manifest-validation logic on purpose (see its `_load_pkgtools_manifest`)
# rather than a second implementation that could disagree with the one
# scripts/release.py was built against. It looks for these files at
# `app_root() / "scripts" / "pkgtools"` and silently no-ops the update check
# if they are missing -- so the bundle must actually ship them, or six
# machines never learn an update exists and the failure looks exactly like
# "no network," never like a bug.
REQUIRED_PKGTOOLS_FILES = ("__init__.py", "manifest.py", "gpu_matrix.py")
# Destination inside the bundle, relative to its root -- must match what
# `updater._load_pkgtools_manifest()` looks for via `app_root() / "scripts"`.
PKGTOOLS_DEST = "scripts/pkgtools"

# styles/library.py's shipped_styles_dir() == app_root() / "styles". Without
# this, list_styles() finds nothing and every job silently falls back to the
# default style -- no error, no log an editor would ever see, just the
# entire styling feature quietly absent from the shipped product.
STYLES_DEST = "styles"

# styles/fonts.py's assets_fonts_dir() == app_root() / "assets" / "fonts".
# manifest.json is the part that MUST ship -- it is what style validation
# reads, committed and offline. The actual .ttf files are fetched separately
# (`python -m ash_captions.styles.fonts download`, not a scripts/ concern)
# and ship if present at build time, but are not required for the build to
# proceed -- without manifest.json, though, validation rejects every font by
# design, so that file failing to ship must fail the build.
FONTS_DEST = "assets/fonts"
FONT_MANIFEST_FILENAME = "manifest.json"

FFMPEG_BINARIES = ("ffmpeg.exe", "ffprobe.exe")


class BuildError(Exception):
    """Raised for anything that would produce a broken bundle."""


def read_project_version(pyproject_path: Path = PYPROJECT_PATH) -> str:
    """Read `[project].version` out of pyproject.toml.

    We do not edit pyproject.toml (out of scope, owned elsewhere) -- this
    just reads the version that is the single source of truth there, so the
    build, the manifest and the app itself never disagree.
    """
    import tomllib

    with Path(pyproject_path).open("rb") as f:
        data = tomllib.load(f)
    try:
        return data["project"]["version"]
    except KeyError as exc:
        raise BuildError(f"{pyproject_path} has no [project].version") from exc


def validate_static_assets(static_dir: Path = STATIC_DIR) -> None:
    """Fail loudly, before invoking PyInstaller, if the web static assets are
    missing or incomplete -- rather than shipping a bundle that 404s."""
    static_dir = Path(static_dir)
    if not static_dir.is_dir():
        raise BuildError(
            f"static assets directory not found: {static_dir}\n"
            "The control page will 404 without it. Build from a full checkout."
        )
    missing = [name for name in REQUIRED_STATIC_FILES if not (static_dir / name).is_file()]
    if missing:
        raise BuildError(
            f"static assets directory {static_dir} is missing required files: {missing}"
        )


def validate_pkgtools_assets(pkgtools_dir: Path = PKGTOOLS_DIR) -> None:
    """Fail loudly, before invoking PyInstaller, if the shared manifest/
    version-comparison logic the in-app updater reuses is missing --
    otherwise the bundle builds fine and the update checker just silently
    never works on any of six machines. See REQUIRED_PKGTOOLS_FILES above."""
    pkgtools_dir = Path(pkgtools_dir)
    if not pkgtools_dir.is_dir():
        raise BuildError(
            f"pkgtools directory not found: {pkgtools_dir}\n"
            "The in-app update checker silently no-ops without it. Build from a full checkout."
        )
    missing = [name for name in REQUIRED_PKGTOOLS_FILES if not (pkgtools_dir / name).is_file()]
    if missing:
        raise BuildError(f"pkgtools directory {pkgtools_dir} is missing required files: {missing}")


def validate_styles_assets(styles_dir: Path = STYLES_DIR) -> None:
    """Fail loudly if the shipped style presets are missing -- otherwise
    `list_styles()` finds nothing at runtime and every job silently falls
    back to the default style, with no error an editor would ever see."""
    styles_dir = Path(styles_dir)
    if not styles_dir.is_dir():
        raise BuildError(
            f"styles directory not found: {styles_dir}\n"
            "Every job would silently fall back to the default style. Build from a full checkout."
        )
    style_files = sorted(p.name for p in styles_dir.glob("*.json"))
    if not style_files:
        raise BuildError(f"styles directory {styles_dir} contains no *.json style files")


def validate_fonts_assets(fonts_dir: Path = FONTS_DIR) -> None:
    """Fail loudly if assets/fonts/manifest.json is missing -- style
    validation reads it to know which fonts are bundled, and rejects every
    font by design when it can't find the manifest at all. The .ttf files
    themselves are not required here: they're fetched separately
    (`python -m ash_captions.styles.fonts download`) and ship if present,
    but their absence is not a reason to fail a build -- a missing
    manifest is."""
    fonts_dir = Path(fonts_dir)
    if not fonts_dir.is_dir():
        raise BuildError(
            f"fonts directory not found: {fonts_dir}\n"
            "Style validation rejects every font without assets/fonts/manifest.json. "
            "Build from a full checkout."
        )
    manifest = fonts_dir / FONT_MANIFEST_FILENAME
    if not manifest.is_file():
        raise BuildError(
            f"{manifest} not found -- style validation rejects every font without it."
        )


def discover_ffmpeg_binaries(ffmpeg_dir: Path) -> list[Path]:
    """Locate ffmpeg.exe/ffprobe.exe fetched by `fetch_ffmpeg.py`.

    Raises BuildError if either is missing -- a bundle without them fails
    every job with an obscure "file not found" instead of a build-time error.
    """
    ffmpeg_dir = Path(ffmpeg_dir)
    found = []
    missing = []
    for name in FFMPEG_BINARIES:
        path = ffmpeg_dir / name
        (found if path.is_file() else missing).append(path)
    if missing:
        raise BuildError(
            f"ffmpeg binaries missing from {ffmpeg_dir}: {[p.name for p in missing]}\n"
            "Run scripts/fetch_ffmpeg.py first, or pass --skip-ffmpeg for a "
            "dry/partial build that cannot be shipped."
        )
    return found


def build_pyinstaller_args(
    *,
    entry_script: Path,
    dist_dir: Path,
    work_dir: Path,
    spec_dir: Path,
    static_dir: Path,
    pkgtools_dir: Path = PKGTOOLS_DIR,
    styles_dir: Path = STYLES_DIR,
    fonts_dir: Path = FONTS_DIR,
    ffmpeg_binaries: list[Path] | None = None,
    model_dir: Path | None = None,
    app_name: str = APP_NAME,
    console: bool = True,
) -> list[str]:
    """Build the argv PyInstaller.__main__.run() expects, for a onedir build.

    Pure and side-effect-free -- easy to unit test without PyInstaller
    installed. `console=True` keeps a console window for now; the tray app
    can flip this to windowed once it manages its own log file (see
    config.py's `log_path` -- errors must stay reachable from the tray menu
    per spec section 12 either way).
    """
    args: list[str] = [
        str(entry_script),
        "--name", app_name,
        "--onedir",
        "--noconfirm",
        "--clean",
        "--distpath", str(dist_dir),
        "--workpath", str(work_dir),
        "--specpath", str(spec_dir),
        "--console" if console else "--windowed",
        # PyInstaller >=6 defaults onedir to a `_internal/` subdirectory for
        # everything but the exe. config.py's `app_root()` (bin/ffmpeg.exe,
        # models/) and every app_root()-relative lookup below (scripts/,
        # styles/, assets/fonts/) both assume the flat, pre-6.0 layout --
        # everything sitting directly beside the exe. Restore that instead
        # of quietly breaking every one of those lookups at once.
        "--contents-directory", ".",
        # Compiled extensions with data files PyInstaller's default hooks
        # under-collect; belt and suspenders beats a bundle that imports
        # fine on the build box and fails on an editor's PC.
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
        "--collect-all", "av",
        "--collect-submodules", "uvicorn",
        "--add-data", f"{static_dir};ash_captions/web/static",
        # src/ash_captions/app/updater.py imports this at runtime from
        # `app_root() / "scripts" / "pkgtools"` -- see REQUIRED_PKGTOOLS_FILES
        # above for why this is not optional the way ffmpeg/model bundling is.
        "--add-data", f"{pkgtools_dir};{PKGTOOLS_DEST}",
        # styles/library.py's shipped_styles_dir() and styles/fonts.py's
        # assets_fonts_dir() both read from app_root() at runtime -- required,
        # not optional, exactly like static/ above. See STYLES_DEST/FONTS_DEST.
        "--add-data", f"{styles_dir};{STYLES_DEST}",
        "--add-data", f"{fonts_dir};{FONTS_DEST}",
    ]
    for binary in ffmpeg_binaries or []:
        args += ["--add-binary", f"{binary};bin"]
    if model_dir is not None:
        args += ["--add-data", f"{model_dir};models"]
    return args


def run_pyinstaller(args: list[str]) -> None:
    """Actually invoke PyInstaller. Imported lazily so this module -- and the
    test suite -- never requires PyInstaller to be installed."""
    import PyInstaller.__main__

    PyInstaller.__main__.run(args)


def zip_bundle(bundle_dir: Path, dest_zip: Path) -> Path:
    """Zip the onedir bundle into the single file `install.ps1` unpacks and
    `release.py` uploads."""
    bundle_dir = Path(bundle_dir)
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    if dest_zip.exists():
        dest_zip.unlink()
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in bundle_dir.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(bundle_dir.parent))
    return dest_zip


def write_build_info(dist_dir: Path, *, version: str, zip_path: Path) -> Path:
    """Write a local build receipt: version, build date, sha256 and size of
    the zipped bundle. This is NOT the release manifest -- it has no download
    URL yet, since that only exists once `release.py` uploads it. `release.py`
    reads this file to build the real manifest.
    """
    artifact: Artifact = build_artifact(zip_path, url="pending-release")
    info = {
        "version": version,
        "build_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_filename": artifact.filename,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    out_path = Path(dist_dir) / "build-info.json"
    import json

    out_path.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--work-dir", type=Path, default=REPO_ROOT / "build" / "pyinstaller")
    parser.add_argument("--spec-dir", type=Path, default=REPO_ROOT / "build")
    parser.add_argument(
        "--ffmpeg-dir",
        type=Path,
        default=REPO_ROOT / "build" / "ffmpeg",
        help="Directory containing ffmpeg.exe/ffprobe.exe (see fetch_ffmpeg.py).",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="Pre-seeded model directory to bundle into models/ (see fetch_model.py).",
    )
    parser.add_argument(
        "--skip-ffmpeg",
        action="store_true",
        help="Build without bundling ffmpeg. For local iteration only -- not shippable.",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help="Build without a console window (once the tray app owns its own logging).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PyInstaller command and exit without running it or "
        "requiring PyInstaller to be installed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    validate_static_assets(STATIC_DIR)
    validate_pkgtools_assets(PKGTOOLS_DIR)
    validate_styles_assets(STYLES_DIR)
    validate_fonts_assets(FONTS_DIR)
    version = read_project_version(PYPROJECT_PATH)

    ffmpeg_binaries: list[Path] = []
    if not args.skip_ffmpeg:
        ffmpeg_binaries = discover_ffmpeg_binaries(args.ffmpeg_dir)
    elif not args.dry_run:
        print("WARNING: --skip-ffmpeg set; this bundle cannot be shipped.", file=sys.stderr)

    if args.model_dir is not None and not args.model_dir.is_dir():
        raise BuildError(f"--model-dir does not exist: {args.model_dir}")

    pyinstaller_args = build_pyinstaller_args(
        entry_script=ENTRY_SCRIPT,
        dist_dir=args.dist_dir,
        work_dir=args.work_dir,
        spec_dir=args.spec_dir,
        static_dir=STATIC_DIR,
        ffmpeg_binaries=ffmpeg_binaries,
        model_dir=args.model_dir,
        console=not args.windowed,
    )

    if args.dry_run:
        print("pyinstaller " + " ".join(pyinstaller_args))
        return 0

    if not ENTRY_SCRIPT.is_file():
        raise BuildError(
            f"entry point not found: {ENTRY_SCRIPT}\n"
            "src/ash_captions/__main__.py is owned by another part of this "
            "project (pyproject.toml's [project.scripts] entry) -- build "
            "once it lands."
        )

    run_pyinstaller(pyinstaller_args)

    bundle_dir = args.dist_dir / APP_NAME
    zip_path = args.dist_dir / f"{APP_NAME}-{version}-win64.zip"
    zip_bundle(bundle_dir, zip_path)
    info_path = write_build_info(args.dist_dir, version=version, zip_path=zip_path)

    print(f"Built {bundle_dir}")
    print(f"Zipped {zip_path} (sha256 in {info_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
