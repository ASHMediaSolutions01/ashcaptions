"""Proves styles/ and assets/fonts/manifest.json resolve the way the FROZEN
app actually uses them -- the exact bug reported against a real PyInstaller
build: `styles/library.py::list_styles()` found nothing and every job
silently fell back to the default style, while `styles/fonts.py` had no
manifest to validate fonts against, so every font was rejected. All 705
tests were green at the time because nothing exercised the frozen bundle
layout `scripts/build.py` actually produces -- only a source checkout,
where `styles/` and `assets/fonts/` sit at the repo root regardless of what
build.py bundles.

Same technique as test_frozen_updater_import.py: build a temp directory
laid out exactly as `build.py --add-data` would inside a real onedir bundle
(exe at the root; `styles/` and `assets/fonts/` right beside it, per
STYLES_DEST/FONTS_DEST), point a subprocess's `sys.frozen`/`sys.executable`
at it, and call the real app-side functions -- not a mock, not an
assumption that the destination strings are correct.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import build

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _build_frozen_bundle(tmp_path: Path, *, include_styles: bool, include_fonts: bool) -> Path:
    """Lay out tmp_path exactly as build.py's --add-data would inside a real
    PyInstaller onedir bundle (--contents-directory .): the exe at the
    bundle root, styles/ and assets/fonts/ right beside it."""
    bundle_dir = tmp_path / build.APP_NAME
    fake_exe = bundle_dir / "AshCaptions.exe"
    bundle_dir.mkdir(parents=True)
    fake_exe.write_bytes(b"not a real PyInstaller exe -- only .parent is read")

    if include_styles:
        styles_dest = bundle_dir / build.STYLES_DEST
        shutil.copytree(build.STYLES_DIR, styles_dest)

    if include_fonts:
        fonts_dest = bundle_dir / build.FONTS_DEST
        fonts_dest.mkdir(parents=True)
        shutil.copy(
            build.FONTS_DIR / build.FONT_MANIFEST_FILENAME,
            fonts_dest / build.FONT_MANIFEST_FILENAME,
        )

    return fake_exe


def _run_frozen(fake_exe: Path, body: str) -> subprocess.CompletedProcess:
    script = f"""
import sys
sys.path.insert(0, {str(SRC_DIR)!r})
sys.frozen = True
sys.executable = {str(fake_exe)!r}

{body}
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )


def test_bundle_layout_matches_what_build_py_would_produce(tmp_path):
    """Sanity check on the fixture itself, same purpose as the equivalent
    check in test_frozen_updater_import.py."""
    fake_exe = _build_frozen_bundle(tmp_path, include_styles=True, include_fonts=True)
    bundle_dir = fake_exe.parent
    assert (bundle_dir / "styles" / "pop.json").is_file()
    assert (bundle_dir / "assets" / "fonts" / "manifest.json").is_file()
    assert build.STYLES_DEST == "styles"
    assert build.FONTS_DEST == "assets/fonts"


def test_list_styles_finds_shipped_styles_when_frozen(tmp_path):
    """The actual proof for styles/: this is precisely `list_styles()`, the
    function the render pipeline calls for every job -- not a lower-level
    path helper. Before this fix, this returned {} in the real bundle and
    every job silently rendered with the default style instead.

    Fonts are bundled here too (a real build always ships both, and
    validation checks a style's font is bundled) -- this proves the two
    required directories work together, not each in isolation."""
    fake_exe = _build_frozen_bundle(tmp_path, include_styles=True, include_fonts=True)

    proc = _run_frozen(
        fake_exe,
        """
from ash_captions.styles.library import list_styles

styles = list_styles()
assert len(styles) > 0, "expected at least one shipped style, found none"
assert "POP" in styles, sorted(styles)
print("STYLES_OK", len(styles))
""",
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "STYLES_OK" in proc.stdout
    # Every shipped style file the repo commits must actually be found --
    # not just "at least one."
    expected_count = len(list(build.STYLES_DIR.glob("*.json")))
    assert f"STYLES_OK {expected_count}" in proc.stdout


def test_list_styles_finds_nothing_without_bundled_styles(tmp_path):
    """Negative control: with no styles/ directory in the bundle at all
    (the bug as reported), list_styles() must return empty -- proving the
    previous test passes because of the bundled files, not despite them."""
    fake_exe = _build_frozen_bundle(tmp_path, include_styles=False, include_fonts=False)

    proc = _run_frozen(
        fake_exe,
        """
from ash_captions.styles.library import list_styles

styles = list_styles()
print("EMPTY" if not styles else f"UNEXPECTEDLY_FOUND {len(styles)}")
""",
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "EMPTY" in proc.stdout


def test_font_manifest_resolves_when_frozen(tmp_path):
    """The actual proof for fonts: is_font_bundled() (what style validation
    calls) must find a real bundled font family -- not just that the
    manifest file is readable in isolation."""
    fake_exe = _build_frozen_bundle(tmp_path, include_styles=False, include_fonts=True)

    proc = _run_frozen(
        fake_exe,
        """
from ash_captions.styles.fonts import is_font_bundled, list_font_families

families = list_font_families()
assert len(families) > 0, "expected at least one bundled font family, found none"
assert is_font_bundled("Inter") is True
print("FONTS_OK", len(families))
""",
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "FONTS_OK" in proc.stdout


def test_font_validation_rejects_everything_without_bundled_manifest(tmp_path):
    """Negative control: with no assets/fonts/manifest.json in the bundle
    (the bug as reported), font lookups must fail -- not silently succeed
    for the wrong reason."""
    fake_exe = _build_frozen_bundle(tmp_path, include_styles=False, include_fonts=False)

    proc = _run_frozen(
        fake_exe,
        """
from ash_captions.styles.fonts import is_font_bundled

try:
    is_font_bundled("Inter")
    print("UNEXPECTEDLY_SUCCEEDED")
except (FileNotFoundError, OSError):
    print("EXPECTED_MANIFEST_MISSING")
""",
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "EXPECTED_MANIFEST_MISSING" in proc.stdout


def test_pyinstaller_args_cover_every_app_root_asset_destination():
    """Audit, run against the real build_pyinstaller_args(): every directory
    an app_root()-relative lookup reads from (grepped and enumerated by
    hand against src/ash_captions -- see docs/INSTALL.md's build section)
    must appear as an --add-data/--add-binary destination. This is the
    check that would have caught styles/ and assets/fonts/ being missing
    without needing a real PyInstaller build to notice."""
    args = build.build_pyinstaller_args(
        entry_script=build.ENTRY_SCRIPT,
        dist_dir=Path("dist"),
        work_dir=Path("build/pyinstaller"),
        spec_dir=Path("build"),
        static_dir=build.STATIC_DIR,
        ffmpeg_binaries=[Path("ffmpeg.exe"), Path("ffprobe.exe")],
        model_dir=Path("models/small"),
    )
    destinations = set()
    for i, arg in enumerate(args):
        if arg in ("--add-data", "--add-binary"):
            destinations.add(args[i + 1].split(";", 1)[1])

    # Every app_root()-relative consumer found by:
    #   grep -rn "app_root()" src/ash_captions --include="*.py"
    required = {
        "bin",  # config.py: find_binary() -- ffmpeg.exe/ffprobe.exe
        "models",  # config.py: Settings.model_cache_dir
        build.PKGTOOLS_DEST,  # updater.py: _load_pkgtools_manifest()
        build.STYLES_DEST,  # styles/library.py: shipped_styles_dir()
        build.FONTS_DEST,  # styles/fonts.py: assets_fonts_dir()
    }
    missing = required - destinations
    assert not missing, f"app_root()-relative asset dirs not bundled: {missing}"
