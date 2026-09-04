"""Tests for scripts/build.py -- the review fixes: shipped notices
(LICENSE, NOTICES.md, bin/LICENSE.txt), the probed --collect-all, and the
HF-cache-root check on --model-dir. Pure/local like test_build.py; no
PyInstaller.
"""

from __future__ import annotations

import build
import pytest


# -- review items 8, 18: notices, ffmpeg licence, optional --collect-all -----


def _basic_args(tmp_path, **overrides):
    kwargs = dict(
        entry_script=tmp_path / "m.py",
        dist_dir=tmp_path / "d",
        work_dir=tmp_path / "w",
        spec_dir=tmp_path / "s",
        static_dir=tmp_path / "static",
    )
    kwargs.update(overrides)
    return build.build_pyinstaller_args(**kwargs)


def test_notice_files_ship_at_the_bundle_root_by_default(tmp_path):
    args = _basic_args(tmp_path)
    assert f"{build.LICENSE_PATH};." in args
    assert f"{build.NOTICES_PATH};." in args


def test_validate_notice_files_against_real_repo():
    build.validate_notice_files()
    assert build.LICENSE_PATH.read_text(encoding="utf-8").startswith("Copyright (c) 2026 Ash Media Solutions")
    notices = build.NOTICES_PATH.read_text(encoding="utf-8")
    for component in ("ffmpeg", "faster-whisper", "CTranslate2", "onnxruntime", "Whisper", "Fonts"):
        assert component in notices


def test_validate_notice_files_missing(tmp_path):
    with pytest.raises(build.BuildError, match="notice files missing"):
        build.validate_notice_files([tmp_path / "LICENSE"])


def test_ffmpeg_license_ships_in_bin(tmp_path):
    lic = tmp_path / "LICENSE.txt"
    args = _basic_args(tmp_path, ffmpeg_license=lic)
    assert f"{lic};bin" in args


def test_discover_ffmpeg_license(tmp_path):
    with pytest.raises(build.BuildError, match="LICENSE.txt"):
        build.discover_ffmpeg_license(tmp_path)
    (tmp_path / "LICENSE.txt").write_text("GPL", encoding="utf-8")
    assert build.discover_ffmpeg_license(tmp_path) == tmp_path / "LICENSE.txt"


def test_collect_all_av_only_when_requested(tmp_path):
    args = _basic_args(tmp_path)
    assert "--collect-all" not in [args[i - 1] for i, a in enumerate(args) if a == "av"]
    # PyAV is excluded and replaced by the stub in every build
    assert args[args.index("--exclude-module") + 1] == "av"
    args = _basic_args(tmp_path, collect_all_optional=("somepkg",))
    assert args[args.index("somepkg") - 1] == "--collect-all"


def test_available_optional_modules_probes_importability():
    found = build.available_optional_modules(("json", "definitely_not_a_module_xyz"))
    assert found == ("json",)


def test_validate_fonts_assets_requires_promised_licence_texts(tmp_path):
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "manifest.json").write_text(
        '{"fonts": [{"family": "X", "file": "X.ttf", "license": "OFL-1.1", "license_file": "licenses/X.txt"}]}',
        encoding="utf-8",
    )
    with pytest.raises(build.BuildError, match="licence texts"):
        build.validate_fonts_assets(fonts_dir)
    (fonts_dir / "licenses").mkdir()
    (fonts_dir / "licenses" / "X.txt").write_text("OFL", encoding="utf-8")
    build.validate_fonts_assets(fonts_dir)


# -- review item 2: --model-dir must be an HF cache root --------------------


def _fake_hf_cache(root, size="small", sha="0123456789abcdef0123456789abcdef01234567"):
    snapshot = root / f"models--Systran--faster-whisper-{size}" / "snapshots" / sha
    snapshot.mkdir(parents=True)
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (snapshot / name).write_bytes(b"x")
    refs = root / f"models--Systran--faster-whisper-{size}" / "refs"
    refs.mkdir()
    (refs / "main").write_text(sha, encoding="utf-8")
    return snapshot


def test_validate_model_cache_rejects_flat_layout(tmp_path):
    flat = tmp_path / "models" / "small"
    flat.mkdir(parents=True)
    (flat / "model.bin").write_bytes(b"x")
    with pytest.raises(build.BuildError, match="not an HF cache root"):
        build.validate_model_cache(tmp_path / "models")


def test_validate_model_cache_accepts_hf_layout(tmp_path):
    _fake_hf_cache(tmp_path / "models")
    build.validate_model_cache(tmp_path / "models")


def test_validate_model_cache_missing_dir(tmp_path):
    with pytest.raises(build.BuildError, match="does not exist"):
        build.validate_model_cache(tmp_path / "nope")


def test_validate_model_cache_rejects_symlinked_snapshot(tmp_path):
    snapshot = _fake_hf_cache(tmp_path / "models")
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"y")
    link = snapshot / "model.bin"
    link.unlink()
    try:
        link.symlink_to(blob)
    except OSError:
        pytest.skip("symlinks not permitted for this user")
    with pytest.raises(build.BuildError, match="symlinked"):
        build.validate_model_cache(tmp_path / "models")


def test_assemble_args_dry_run_with_model_dir(tmp_path):
    _fake_hf_cache(tmp_path / "models")
    args = build.assemble_pyinstaller_args(
        build.parse_args(["--dry-run", "--skip-ffmpeg", "--model-dir", str(tmp_path / "models")])
    )
    assert f"{tmp_path / 'models'};models" in args


def test_assemble_args_requires_ffmpeg_license(tmp_path, monkeypatch):
    (tmp_path / "ffmpeg.exe").write_bytes(b"x")
    (tmp_path / "ffprobe.exe").write_bytes(b"x")
    with pytest.raises(build.BuildError, match="LICENSE.txt"):
        build.assemble_pyinstaller_args(build.parse_args(["--dry-run", "--ffmpeg-dir", str(tmp_path)]))
    (tmp_path / "LICENSE.txt").write_text("GPL", encoding="utf-8")
    args = build.assemble_pyinstaller_args(build.parse_args(["--dry-run", "--ffmpeg-dir", str(tmp_path)]))
    assert f"{tmp_path / 'LICENSE.txt'};bin" in args
    assert f"{tmp_path / 'ffmpeg.exe'};bin" in args
