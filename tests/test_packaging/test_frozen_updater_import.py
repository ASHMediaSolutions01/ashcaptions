"""Proves the in-app updater's pkgtools import resolves the way the FROZEN
app will actually use it -- not just the way a source checkout trivially
does (scripts/ sits right there on disk in a checkout regardless of what
build.py bundles, which is exactly why that alone would be false confidence).

src/ash_captions/app/updater.py's `_load_pkgtools_manifest()` does:

    scripts_dir = app_root() / "scripts"
    ...
    from pkgtools.manifest import ManifestError, is_newer, validate_manifest, ...

`app_root()` (config.py) resolves to `Path(sys.executable).parent` when
`sys.frozen` is set -- i.e. the PyInstaller bundle's own root. This test
builds a directory laid out exactly the way `scripts/build.py`'s
`--add-data {PKGTOOLS_DIR};scripts/pkgtools` places files inside that
bundle, points a subprocess's `sys.frozen`/`sys.executable` at it, and
proves the import actually succeeds there -- not just that the source files
exist.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import build

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _build_frozen_bundle(tmp_path: Path) -> tuple[Path, Path]:
    """Lay out tmp_path exactly as build.py's --add-data would inside a real
    PyInstaller onedir bundle (with --contents-directory . -- see
    build_pyinstaller_args): the exe at the bundle root, pkgtools at
    scripts/pkgtools right beside it."""
    bundle_dir = tmp_path / build.APP_NAME
    fake_exe = bundle_dir / "AshCaptions.exe"
    pkgtools_dest = bundle_dir / build.PKGTOOLS_DEST
    pkgtools_dest.mkdir(parents=True)
    fake_exe.write_bytes(b"not a real PyInstaller exe -- only .parent is read")

    for name in build.REQUIRED_PKGTOOLS_FILES:
        shutil.copy(build.PKGTOOLS_DIR / name, pkgtools_dest / name)

    return bundle_dir, fake_exe


def test_bundle_layout_matches_what_build_py_would_produce(tmp_path):
    """Sanity check on the test fixture itself: the destination this test
    copies pkgtools into must be the literal string build.py's --add-data
    uses, or this test would pass for the wrong reason."""
    bundle_dir, fake_exe = _build_frozen_bundle(tmp_path)
    assert (bundle_dir / "scripts" / "pkgtools" / "manifest.py").is_file()
    assert build.PKGTOOLS_DEST == "scripts/pkgtools"


def test_updater_resolves_pkgtools_when_frozen(tmp_path):
    """The actual proof: simulate `sys.frozen` pointed at a bundle laid out
    the way build.py produces one, and confirm
    updater._load_pkgtools_manifest() succeeds -- rather than the ImportError
    it raises today (silently swallowed by check_for_update as a no-op)."""
    _bundle_dir, fake_exe = _build_frozen_bundle(tmp_path)

    script = f"""
import sys
sys.path.insert(0, {str(SRC_DIR)!r})
sys.frozen = True
sys.executable = {str(fake_exe)!r}

from ash_captions.app import updater

result = updater._load_pkgtools_manifest()
assert len(result) == 4, result
ManifestError, is_newer, validate_manifest, verify_artifact_against_manifest = result
assert is_newer("0.2.0", "0.1.0") is True
print("FROZEN_IMPORT_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "FROZEN_IMPORT_OK" in proc.stdout


def test_updater_import_fails_without_bundled_pkgtools(tmp_path):
    """Negative control: an "app_root()" with no scripts/pkgtools at all
    (the bug as originally reported) must still raise ImportError, not
    something check_for_update fails to catch. Proves the previous test
    passes because of the bundled files, not despite them being irrelevant."""
    bundle_dir = tmp_path / build.APP_NAME
    bundle_dir.mkdir(parents=True)
    fake_exe = bundle_dir / "AshCaptions.exe"
    fake_exe.write_bytes(b"no scripts/pkgtools next to this one")

    script = f"""
import sys
sys.path.insert(0, {str(SRC_DIR)!r})
sys.frozen = True
sys.executable = {str(fake_exe)!r}

from ash_captions.app import updater

try:
    updater._load_pkgtools_manifest()
    print("UNEXPECTEDLY_SUCCEEDED")
except ImportError:
    print("EXPECTED_IMPORT_ERROR")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "EXPECTED_IMPORT_ERROR" in proc.stdout


def test_check_for_update_actually_works_when_frozen(tmp_path):
    """End-to-end: check_for_update() (the function the tray/control page
    actually call) must detect a newer version when frozen against a bundled
    pkgtools -- not just that the low-level import succeeds in isolation."""
    _bundle_dir, fake_exe = _build_frozen_bundle(tmp_path)

    script = f"""
import json
import sys
sys.path.insert(0, {str(SRC_DIR)!r})
sys.frozen = True
sys.executable = {str(fake_exe)!r}

from ash_captions.app import updater

manifest = {{
    "schema_version": 1,
    "channel": "stable",
    "version": "9.9.9",
    "build_date": "2026-08-29T00:00:00+00:00",
    "artifact": {{
        "filename": "AshCaptions-9.9.9-win64.zip",
        "url": "https://example.invalid/AshCaptions-9.9.9-win64.zip",
        "sha256": "a" * 64,
        "size_bytes": 12345,
    }},
}}

def fake_fetch(url, timeout):
    return json.dumps(manifest).encode("utf-8")

info = updater.check_for_update("0.1.0", fetch=fake_fetch)
assert info is not None, "expected an update to be detected"
assert info.version == "9.9.9"
print("CHECK_FOR_UPDATE_OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "CHECK_FOR_UPDATE_OK" in proc.stdout
