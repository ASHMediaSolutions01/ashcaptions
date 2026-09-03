"""Tests for scripts/build.py.

Only pure/local logic is exercised -- PyInstaller is never imported (it does
not need to be installed for this file to pass; `run_pyinstaller()` is not
called anywhere here).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import build
import pytest
from pkgtools.manifest import sha256_file


def test_importing_build_does_not_import_pyinstaller():
    assert "PyInstaller" not in sys.modules


def test_read_project_version_reads_real_pyproject():
    version = build.read_project_version(build.PYPROJECT_PATH)
    assert version == "0.4.0"


def test_read_project_version_missing_key(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text("[project]\nname = 'x'\n", encoding="utf-8")
    with pytest.raises(build.BuildError):
        build.read_project_version(path)


# -- static asset validation --------------------------------------------


def test_validate_static_assets_missing_dir(tmp_path):
    with pytest.raises(build.BuildError, match="not found"):
        build.validate_static_assets(tmp_path / "does-not-exist")


def test_validate_static_assets_missing_files(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    # app.js and style.css deliberately missing
    with pytest.raises(build.BuildError, match="missing required files"):
        build.validate_static_assets(static_dir)


def test_validate_static_assets_ok(tmp_path):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    for name in build.REQUIRED_STATIC_FILES:
        (static_dir / name).write_text("x", encoding="utf-8")
    build.validate_static_assets(static_dir)  # does not raise


def test_validate_static_assets_against_real_repo():
    """The actual web/static directory this build ships must never regress
    below what the classic-failure check requires."""
    build.validate_static_assets(build.STATIC_DIR)


# -- pkgtools asset validation (the in-app updater's dependency) -----------


def test_validate_pkgtools_assets_missing_dir(tmp_path):
    with pytest.raises(build.BuildError, match="not found"):
        build.validate_pkgtools_assets(tmp_path / "does-not-exist")


def test_validate_pkgtools_assets_missing_files(tmp_path):
    pkgtools_dir = tmp_path / "pkgtools"
    pkgtools_dir.mkdir()
    (pkgtools_dir / "__init__.py").write_text("", encoding="utf-8")
    # manifest.py and gpu_matrix.py deliberately missing
    with pytest.raises(build.BuildError, match="missing required files"):
        build.validate_pkgtools_assets(pkgtools_dir)


def test_validate_pkgtools_assets_ok(tmp_path):
    pkgtools_dir = tmp_path / "pkgtools"
    pkgtools_dir.mkdir()
    for name in build.REQUIRED_PKGTOOLS_FILES:
        (pkgtools_dir / name).write_text("x", encoding="utf-8")
    build.validate_pkgtools_assets(pkgtools_dir)  # does not raise


def test_validate_pkgtools_assets_against_real_repo():
    """The actual scripts/pkgtools this build ships must never regress below
    what the in-app updater needs -- see updater._load_pkgtools_manifest()."""
    build.validate_pkgtools_assets(build.PKGTOOLS_DIR)


# -- styles asset validation (styles/library.py's shipped_styles_dir) -----


def test_validate_styles_assets_missing_dir(tmp_path):
    with pytest.raises(build.BuildError, match="not found"):
        build.validate_styles_assets(tmp_path / "does-not-exist")


def test_validate_styles_assets_empty_dir(tmp_path):
    styles_dir = tmp_path / "styles"
    styles_dir.mkdir()
    with pytest.raises(build.BuildError, match="no \\*.json"):
        build.validate_styles_assets(styles_dir)


def test_validate_styles_assets_ok(tmp_path):
    styles_dir = tmp_path / "styles"
    styles_dir.mkdir()
    (styles_dir / "pop.json").write_text("{}", encoding="utf-8")
    build.validate_styles_assets(styles_dir)  # does not raise


def test_validate_styles_assets_against_real_repo():
    """This is the exact bug report: a real build silently shipped no
    styles/ directory at all, and every job fell back to the default style
    with no error anyone would see. This must never regress."""
    build.validate_styles_assets(build.STYLES_DIR)


# -- fonts asset validation (styles/fonts.py's assets_fonts_dir) ----------


def test_validate_fonts_assets_missing_dir(tmp_path):
    with pytest.raises(build.BuildError, match="not found"):
        build.validate_fonts_assets(tmp_path / "does-not-exist")


def test_validate_fonts_assets_missing_manifest(tmp_path):
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "SomeFont.ttf").write_bytes(b"not a real font")
    with pytest.raises(build.BuildError, match="manifest.json"):
        build.validate_fonts_assets(fonts_dir)


def test_validate_fonts_assets_ok_without_ttf_files(tmp_path):
    """The manifest is required; the .ttf files are not -- they are fetched
    separately and their absence must not fail the build."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "manifest.json").write_text('{"fonts": []}', encoding="utf-8")
    build.validate_fonts_assets(fonts_dir)  # does not raise


def test_validate_fonts_assets_against_real_repo():
    """assets/fonts/manifest.json is a committed file -- this must never
    regress the way styles/ did (see test_validate_styles_assets_against_real_repo)."""
    build.validate_fonts_assets(build.FONTS_DIR)


# -- ffmpeg discovery -----------------------------------------------------


def test_discover_ffmpeg_binaries_missing(tmp_path):
    with pytest.raises(build.BuildError, match="ffmpeg binaries missing"):
        build.discover_ffmpeg_binaries(tmp_path)


def test_discover_ffmpeg_binaries_ok(tmp_path):
    (tmp_path / "ffmpeg.exe").write_bytes(b"x")
    (tmp_path / "ffprobe.exe").write_bytes(b"x")
    found = build.discover_ffmpeg_binaries(tmp_path)
    assert {p.name for p in found} == {"ffmpeg.exe", "ffprobe.exe"}


# -- pyinstaller argument construction (pure) ------------------------------


def test_build_pyinstaller_args_basic(tmp_path):
    entry = tmp_path / "__main__.py"
    static_dir = tmp_path / "static"
    dist_dir = tmp_path / "dist"
    work_dir = tmp_path / "work"
    spec_dir = tmp_path / "spec"

    args = build.build_pyinstaller_args(
        entry_script=entry,
        dist_dir=dist_dir,
        work_dir=work_dir,
        spec_dir=spec_dir,
        static_dir=static_dir,
    )

    assert args[0] == str(entry)
    assert "--onedir" in args
    assert "--noconfirm" in args
    assert "--console" in args
    assert "--windowed" not in args
    assert "AshCaptions" in args
    assert f"{static_dir};ash_captions/web/static" in args
    # dist/work/spec dirs passed through, not hardcoded
    assert str(dist_dir) in args
    assert str(work_dir) in args
    assert str(spec_dir) in args
    # PyInstaller >=6's default `_internal/` layout would break every
    # app_root()-relative lookup (bin/ffmpeg.exe, models/, scripts/pkgtools);
    # this restores the flat layout config.py and updater.py assume.
    assert "--contents-directory" in args
    assert args[args.index("--contents-directory") + 1] == "."
    # pkgtools ships by default -- updater.py's update checker depends on it
    # being at exactly this path inside the bundle.
    assert f"{build.PKGTOOLS_DIR};{build.PKGTOOLS_DEST}" in args
    # styles/ and assets/fonts/ ship by default too -- required, not
    # optional, the exact bug report this test guards against.
    assert f"{build.STYLES_DIR};{build.STYLES_DEST}" in args
    assert f"{build.FONTS_DIR};{build.FONTS_DEST}" in args


def test_build_pyinstaller_args_pkgtools_dest_matches_updater_expectation():
    """updater._load_pkgtools_manifest() does `app_root() / "scripts"` then
    `from pkgtools.manifest import ...` -- the --add-data destination here
    must produce exactly that layout, or the frozen import still fails."""
    assert build.PKGTOOLS_DEST == "scripts/pkgtools"


def test_build_pyinstaller_args_styles_and_fonts_dest_match_app_expectation():
    """styles/library.py's shipped_styles_dir() == app_root() / "styles" and
    styles/fonts.py's assets_fonts_dir() == app_root() / "assets" / "fonts"
    -- the --add-data destinations here must produce exactly that layout."""
    assert build.STYLES_DEST == "styles"
    assert build.FONTS_DEST == "assets/fonts"


def test_build_pyinstaller_args_styles_fonts_dir_override(tmp_path):
    custom_styles = tmp_path / "custom-styles"
    custom_fonts = tmp_path / "custom-fonts"
    args = build.build_pyinstaller_args(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
        styles_dir=custom_styles,
        fonts_dir=custom_fonts,
    )
    assert f"{custom_styles};styles" in args
    assert f"{custom_fonts};assets/fonts" in args


def test_build_pyinstaller_args_pkgtools_dir_override(tmp_path):
    custom_pkgtools = tmp_path / "custom-pkgtools"
    args = build.build_pyinstaller_args(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
        pkgtools_dir=custom_pkgtools,
    )
    assert f"{custom_pkgtools};scripts/pkgtools" in args


def test_build_pyinstaller_args_windowed(tmp_path):
    args = build.build_pyinstaller_args(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
        console=False,
    )
    assert "--windowed" in args
    assert "--console" not in args


def test_build_pyinstaller_args_includes_ffmpeg_binaries(tmp_path):
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    args = build.build_pyinstaller_args(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
        ffmpeg_binaries=[ffmpeg, ffprobe],
    )
    assert f"{ffmpeg};bin" in args
    assert f"{ffprobe};bin" in args


def test_build_pyinstaller_args_includes_model_dir(tmp_path):
    model_dir = tmp_path / "models" / "small"
    args = build.build_pyinstaller_args(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
        model_dir=model_dir,
    )
    assert f"{model_dir};models" in args


def test_build_pyinstaller_args_omits_model_dir_when_none(tmp_path):
    args = build.build_pyinstaller_args(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
        model_dir=None,
    )
    assert not any(a.endswith(";models") for a in args)


# -- zip + build-info -------------------------------------------------------


def test_zip_bundle_and_build_info(tmp_path):
    bundle_dir = tmp_path / "dist" / "AshCaptions"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "AshCaptions.exe").write_bytes(b"fake exe")
    (bundle_dir / "bin").mkdir()
    (bundle_dir / "bin" / "ffmpeg.exe").write_bytes(b"fake ffmpeg")

    dist_dir = tmp_path / "dist"
    zip_path = dist_dir / "AshCaptions-0.1.0-win64.zip"
    build.zip_bundle(bundle_dir, zip_path)

    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert any(n.endswith("AshCaptions.exe") for n in names)
    assert any(n.endswith("bin/ffmpeg.exe") or n.endswith("bin\\ffmpeg.exe") for n in names)

    info_path = build.write_build_info(dist_dir, version="0.1.0", zip_path=zip_path)
    import json

    info = json.loads(info_path.read_text(encoding="utf-8"))
    assert info["version"] == "0.1.0"
    assert info["artifact_filename"] == zip_path.name
    assert info["sha256"] == sha256_file(zip_path)
    assert info["size_bytes"] == zip_path.stat().st_size


def test_zip_bundle_overwrites_existing(tmp_path):
    bundle_dir = tmp_path / "dist" / "AshCaptions"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "a.txt").write_text("v1", encoding="utf-8")
    zip_path = tmp_path / "dist" / "out.zip"

    build.zip_bundle(bundle_dir, zip_path)
    first_size = zip_path.stat().st_size

    (bundle_dir / "a.txt").write_text("v1 but much much longer now", encoding="utf-8")
    build.zip_bundle(bundle_dir, zip_path)
    second_size = zip_path.stat().st_size

    assert first_size != second_size  # actually rewritten, not appended-to


# -- CLI argument parsing ---------------------------------------------------


def test_parse_args_defaults():
    args = build.parse_args([])
    assert args.dist_dir == build.REPO_ROOT / "dist"
    assert args.skip_ffmpeg is False
    assert args.dry_run is False


def test_parse_args_dry_run_and_skip_ffmpeg():
    args = build.parse_args(["--dry-run", "--skip-ffmpeg"])
    assert args.dry_run is True
    assert args.skip_ffmpeg is True


def test_main_dry_run_does_not_require_pyinstaller_or_entry_point(tmp_path, capsys):
    """--dry-run must work even with no ffmpeg fetched, no model, and no
    __main__.py yet (it is owned by another part of the project and may not
    exist at the time this test runs)."""
    exit_code = build.main(["--dry-run", "--skip-ffmpeg"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "pyinstaller" in out
    assert "--onedir" in out
