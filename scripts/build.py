"""Developer-side build: produce the PyInstaller `onedir` bundle.

Run on Ghazi's build machine only -- never on an editor's PC. Usage:

    .venv/Scripts/python.exe scripts/build.py --model-dir build/models
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
import importlib.util
import json
import sys
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkgtools.manifest import Artifact, build_artifact  # noqa: E402

AV_STUB_DIR_NAME = "scripts/pkgtools/av_stub/av"
REPO_ROOT = Path(__file__).resolve().parents[1]
AV_STUB_DIR = REPO_ROOT / AV_STUB_DIR_NAME
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
# if they are missing -- so the bundle must actually ship them.
REQUIRED_PKGTOOLS_FILES = ("__init__.py", "manifest.py", "gpu_matrix.py")
PKGTOOLS_DEST = "scripts/pkgtools"

# styles/library.py's shipped_styles_dir() == app_root() / "styles" and
# styles/fonts.py's assets_fonts_dir() == app_root() / "assets" / "fonts".
STYLES_DEST = "styles"
FONTS_DEST = "assets/fonts"
FONT_MANIFEST_FILENAME = "manifest.json"

FFMPEG_BINARIES = ("ffmpeg.exe", "ffprobe.exe")
# fetch_ffmpeg.py extracts BtbN's licence text beside the binaries; we
# redistribute a GPL ffmpeg, so a bin/ without it must not ship.
FFMPEG_LICENSE_FILENAME = "LICENSE.txt"

# Shipped at the bundle root: our own terms and the third-party notices
# (which point at bin/LICENSE.txt and assets/fonts/licenses/).
LICENSE_PATH = REPO_ROOT / "LICENSE"
NOTICES_PATH = REPO_ROOT / "NOTICES.md"
NOTICE_FILES: tuple[Path, ...] = (LICENSE_PATH, NOTICES_PATH)

# --collect-all targets that are collected only when importable: PyAV is a
# faster-whisper dependency in a normal install, but PyInstaller aborts on a
# --collect-all for a package that is not there, and a dry run must not.
OPTIONAL_COLLECT_ALL: tuple[str, ...] = ()  # PyAV is excluded on purpose; see the av stub

# The bundled Whisper model is an HF cache root (see fetch_model.py): the
# runtime hands app_root()/models to faster-whisper as its cache_dir.
MODEL_SNAPSHOT_GLOB = "models--*/snapshots/*/model.bin"


class BuildError(Exception):
    """Raised for anything that would produce a broken bundle."""


def read_project_version(pyproject_path: Path = PYPROJECT_PATH) -> str:
    """Read `[project].version` out of pyproject.toml -- the single source
    of truth the build, the manifest and the app must all agree on."""
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
    """Fail loudly if the shared manifest/version-comparison logic the
    in-app updater reuses is missing -- otherwise the bundle builds fine and
    the update checker just silently never works on any of six machines."""
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
    are fetched separately (`python -m ash_captions.styles.fonts download`)
    and ship if present; their absence is not a reason to fail a build. The
    licence text each manifest row promises IS required: we redistribute
    those fonts, and the manifest's `license_file` is where NOTICES.md
    sends anyone who asks under what terms."""
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
    rows = json.loads(manifest.read_text(encoding="utf-8")).get("fonts", [])
    missing_licenses = sorted(
        {row["license_file"] for row in rows if row.get("license_file") and not (fonts_dir / row["license_file"]).is_file()}
    )
    if missing_licenses:
        raise BuildError(
            f"font licence texts named by {manifest} are missing: {missing_licenses}\n"
            "Run `python -m ash_captions.styles.fonts licenses` (they are committed; a full "
            "checkout has them)."
        )


def validate_notice_files(paths: Sequence[Path] = NOTICE_FILES) -> None:
    """LICENSE and NOTICES.md ship at the bundle root; a bundle without
    them redistributes GPL/OFL/MIT components with no notice at all."""
    missing = [str(p) for p in paths if not Path(p).is_file()]
    if missing:
        raise BuildError(f"notice files missing (they must ship in the bundle): {missing}")


def validate_model_cache(model_dir: Path) -> None:
    """`--model-dir` must be the HF cache root fetch_model.py produces --
    the only layout faster-whisper resolves offline. The flat layout an
    older fetch_model.py wrote (`models/small/model.bin`) is exactly the bug
    that made every install ignore the bundled model and re-download it."""
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise BuildError(f"--model-dir does not exist: {model_dir}")
    snapshots = sorted(model_dir.glob(MODEL_SNAPSHOT_GLOB))
    if not snapshots:
        raise BuildError(
            f"--model-dir {model_dir} holds no models--*/snapshots/<hash>/model.bin -- not an HF "
            "cache root. Re-run scripts/fetch_model.py and pass its --dest (default build/models)."
        )
    linked = [p for snapshot in snapshots for p in snapshot.parent.iterdir() if p.is_symlink()]
    if linked:
        raise BuildError(
            f"--model-dir {model_dir} still contains symlinked snapshot files (e.g. {linked[0]}); "
            "neither PyInstaller nor the installer preserves them. Re-run scripts/fetch_model.py, "
            "which materialises them."
        )


def discover_ffmpeg_binaries(ffmpeg_dir: Path) -> list[Path]:
    """Locate ffmpeg.exe/ffprobe.exe fetched by `fetch_ffmpeg.py`. Raises
    BuildError if either is missing -- a bundle without them fails every
    job with an obscure "file not found" instead of a build-time error."""
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


def discover_ffmpeg_license(ffmpeg_dir: Path) -> Path:
    """The licence text fetch_ffmpeg.py extracts beside the binaries."""
    path = Path(ffmpeg_dir) / FFMPEG_LICENSE_FILENAME
    if not path.is_file():
        raise BuildError(
            f"{path} not found -- the shipped ffmpeg is GPL and must carry its licence text. "
            "Re-run scripts/fetch_ffmpeg.py (it extracts LICENSE.txt from BtbN's archive)."
        )
    return path


def available_optional_modules(names: Sequence[str] = OPTIONAL_COLLECT_ALL) -> tuple[str, ...]:
    """The subset of `names` importable in this environment."""
    found = []
    for name in names:
        try:
            if importlib.util.find_spec(name) is not None:
                found.append(name)
        except (ImportError, ValueError):
            continue
    return tuple(found)


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
    ffmpeg_license: Path | None = None,
    model_dir: Path | None = None,
    notice_files: Sequence[Path] = NOTICE_FILES,
    collect_all_optional: Sequence[str] = (),
    app_name: str = APP_NAME,
    console: bool = False,
) -> list[str]:
    """Build the argv PyInstaller.__main__.run() expects, for a onedir build.

    Pure and side-effect-free -- easy to unit test without PyInstaller
    installed. `collect_all_optional` is the probed subset of
    OPTIONAL_COLLECT_ALL (see `available_optional_modules`).
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
        # everything but the exe. config.py's `app_root()` and every
        # app_root()-relative lookup below assume the flat layout --
        # everything sitting directly beside the exe. Restore that.
        "--contents-directory", ".",
        # Compiled extensions with data files PyInstaller's default hooks
        # under-collect; belt and suspenders beats a bundle that imports
        # fine on the build box and fails on an editor's PC.
        "--collect-all", "faster_whisper",
        "--collect-all", "ctranslate2",
    ]
    for module in collect_all_optional:
        args += ["--collect-all", module]
    args += [
        "--collect-submodules", "uvicorn",
        "--add-data", f"{static_dir};ash_captions/web/static",
        # updater.py imports this at runtime from app_root()/scripts/pkgtools.
        "--add-data", f"{pkgtools_dir};{PKGTOOLS_DEST}",
        # styles/library.py and styles/fonts.py read these from app_root().
        "--add-data", f"{styles_dir};{STYLES_DEST}",
        "--add-data", f"{fonts_dir};{FONTS_DEST}",
    ]
    for notice in notice_files:
        args += ["--add-data", f"{notice};."]
    for binary in ffmpeg_binaries or []:
        args += ["--add-binary", f"{binary};bin"]
    if ffmpeg_license is not None:
        args += ["--add-data", f"{ffmpeg_license};bin"]
    if model_dir is not None:
        # Absolute on purpose: PyInstaller resolves relative --add-data sources
        # against its own workpath (build/), so the documented
        # `--model-dir build\models` silently became build/build\models and
        # the build failed. Verified on the first real release rehearsal.
        args += ["--add-data", f"{Path(model_dir).resolve()};models"]
    # PyAV is excluded (its wheel carries a GPL-built FFmpeg); a stub satisfies
    # faster-whisper's import. Audio reaches the model as a numpy array.
    args += ["--exclude-module", "av", "--add-data", f"{AV_STUB_DIR};av"]
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
    the zipped bundle. NOT the release manifest -- it has no download URL
    until `release.py` uploads it and reads this file to build the real one."""
    artifact: Artifact = build_artifact(zip_path, url="pending-release")
    info = {
        "version": version,
        "build_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact_filename": artifact.filename,
        "sha256": artifact.sha256,
        "size_bytes": artifact.size_bytes,
    }
    out_path = Path(dist_dir) / "build-info.json"
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
        default=REPO_ROOT / "bin",  # where fetch_ffmpeg.py puts them, and where config.find_binary looks
        help="Directory containing ffmpeg.exe/ffprobe.exe/LICENSE.txt (see fetch_ffmpeg.py).",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="HF cache root produced by fetch_model.py (default build/models) to bundle as models/.",
    )
    parser.add_argument(
        "--skip-ffmpeg",
        action="store_true",
        help="Build without bundling ffmpeg. For local iteration only -- not shippable.",
    )
    parser.add_argument(
        "--console",
        dest="windowed",
        action="store_false",
        help="Keep a console window (debugging only; every editor would see it at logon).",
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        default=True,
        help="(default) No console window: the tray app owns its logging and the logon task launches it silently.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the PyInstaller command and exit without running it or "
        "requiring PyInstaller to be installed.",
    )
    return parser.parse_args(argv)


def assemble_pyinstaller_args(args: argparse.Namespace) -> list[str]:
    """Validate the checkout and turn parsed CLI args into the PyInstaller
    argv -- everything `main()` does before deciding whether to run it."""
    validate_static_assets(STATIC_DIR)
    validate_pkgtools_assets(PKGTOOLS_DIR)
    validate_styles_assets(STYLES_DIR)
    validate_fonts_assets(FONTS_DIR)
    validate_notice_files(NOTICE_FILES)

    ffmpeg_binaries: list[Path] = []
    ffmpeg_license: Path | None = None
    if not args.skip_ffmpeg:
        ffmpeg_binaries = discover_ffmpeg_binaries(args.ffmpeg_dir)
        ffmpeg_license = discover_ffmpeg_license(args.ffmpeg_dir)
    elif not args.dry_run:
        print("WARNING: --skip-ffmpeg set; this bundle cannot be shipped.", file=sys.stderr)

    if args.model_dir is not None:
        validate_model_cache(args.model_dir)
    elif not args.dry_run:
        print(
            "WARNING: no --model-dir; the bundle ships no Whisper model and every "
            "machine downloads its own on first run.",
            file=sys.stderr,
        )

    return build_pyinstaller_args(
        entry_script=ENTRY_SCRIPT,
        dist_dir=args.dist_dir,
        work_dir=args.work_dir,
        spec_dir=args.spec_dir,
        static_dir=STATIC_DIR,
        ffmpeg_binaries=ffmpeg_binaries,
        ffmpeg_license=ffmpeg_license,
        model_dir=args.model_dir,
        collect_all_optional=available_optional_modules(),
        console=not args.windowed,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    version = read_project_version(PYPROJECT_PATH)
    pyinstaller_args = assemble_pyinstaller_args(args)

    if args.dry_run:
        print("pyinstaller " + " ".join(pyinstaller_args))
        return 0

    if not ENTRY_SCRIPT.is_file():
        raise BuildError(f"entry point not found: {ENTRY_SCRIPT}")

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
