"""Tests for scripts/fetch_ffmpeg.py.

No network calls: `download_file()` and `resolve_release_url()` are the only
network-touching functions and neither is exercised here. Archive handling is
tested against a small synthetic zip built in-memory with the stdlib.
"""

from __future__ import annotations

import zipfile

import fetch_ffmpeg
import pytest


def test_default_asset_url_variants():
    lgpl = fetch_ffmpeg.default_asset_url("lgpl")
    gpl = fetch_ffmpeg.default_asset_url("gpl")
    assert lgpl.endswith("ffmpeg-master-latest-win64-lgpl.zip")
    assert gpl.endswith("ffmpeg-master-latest-win64-gpl.zip")
    assert lgpl != gpl
    assert lgpl.startswith("https://github.com/BtbN/FFmpeg-Builds/")


def _make_ffmpeg_zip(path, *, nested=True, missing=None):
    missing = missing or set()
    prefix = "ffmpeg-master-latest-win64-lgpl/bin/" if nested else ""
    with zipfile.ZipFile(path, "w") as zf:
        if "ffmpeg.exe" not in missing:
            zf.writestr(f"{prefix}ffmpeg.exe", b"fake ffmpeg binary")
        if "ffprobe.exe" not in missing:
            zf.writestr(f"{prefix}ffprobe.exe", b"fake ffprobe binary")
        zf.writestr(f"{prefix}../LICENSE.txt", b"LGPL")
        zf.writestr(f"{prefix}../README.txt", b"readme")


def test_extract_binaries_ok(tmp_path):
    zip_path = tmp_path / "ffmpeg.zip"
    _make_ffmpeg_zip(zip_path)
    dest_dir = tmp_path / "out"

    found = fetch_ffmpeg.extract_binaries(zip_path, dest_dir)

    assert set(found) == {"ffmpeg.exe", "ffprobe.exe"}
    assert (dest_dir / "ffmpeg.exe").read_bytes() == b"fake ffmpeg binary"
    assert (dest_dir / "ffprobe.exe").read_bytes() == b"fake ffprobe binary"
    # nothing else from the archive (LICENSE.txt etc.) is copied out
    assert set(p.name for p in dest_dir.iterdir()) == {"ffmpeg.exe", "ffprobe.exe"}


def test_extract_binaries_missing_member_raises(tmp_path):
    zip_path = tmp_path / "ffmpeg.zip"
    _make_ffmpeg_zip(zip_path, missing={"ffprobe.exe"})
    with pytest.raises(fetch_ffmpeg.FetchError, match="missing expected binaries"):
        fetch_ffmpeg.extract_binaries(zip_path, tmp_path / "out")


def test_verify_zip_integrity_ok(tmp_path):
    zip_path = tmp_path / "good.zip"
    _make_ffmpeg_zip(zip_path)
    fetch_ffmpeg.verify_zip_integrity(zip_path)  # does not raise


def test_verify_zip_integrity_detects_corruption(tmp_path):
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("ffmpeg.exe", b"A" * 5000)

    # Flip a byte in the middle of the file's compressed data (well past the
    # local/central directory headers) so the CRC no longer matches.
    raw = bytearray(zip_path.read_bytes())
    mid = len(raw) // 2
    raw[mid] ^= 0xFF
    zip_path.write_bytes(bytes(raw))

    with pytest.raises((fetch_ffmpeg.FetchError, zipfile.BadZipFile, RuntimeError)):
        fetch_ffmpeg.verify_zip_integrity(zip_path)


def test_write_version_file(tmp_path):
    info_path = fetch_ffmpeg.write_version_file(
        tmp_path,
        source_url="https://example.invalid/ffmpeg.zip",
        tag_name="autobuild-2026-08-20",
        binary_versions={
            "ffmpeg.exe": "ffmpeg version N-112233-gabc1234",
            "ffprobe.exe": "ffprobe version N-112233-gabc1234",
        },
    )
    text = info_path.read_text(encoding="utf-8")
    assert "autobuild-2026-08-20" in text
    assert "https://example.invalid/ffmpeg.zip" in text
    assert "ffmpeg.exe: ffmpeg version N-112233-gabc1234" in text
    assert "ffprobe.exe: ffprobe version N-112233-gabc1234" in text


def test_write_version_file_unresolved_tag(tmp_path):
    info_path = fetch_ffmpeg.write_version_file(
        tmp_path, source_url="https://example.invalid/x.zip", tag_name=None, binary_versions={}
    )
    assert "unresolved" in info_path.read_text(encoding="utf-8")


def test_parse_args_defaults():
    args = fetch_ffmpeg.parse_args([])
    assert args.variant == "lgpl"
    assert args.dest == fetch_ffmpeg.DEFAULT_DEST
    assert args.url is None
