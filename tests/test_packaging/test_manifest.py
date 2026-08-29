"""Tests for scripts/pkgtools/manifest.py: manifest generation, parsing and
version comparison -- the schema the in-app updater (owned elsewhere) reads."""

from __future__ import annotations

import pytest
from pkgtools.manifest import (
    Artifact,
    ManifestError,
    build_artifact,
    build_manifest,
    compare_versions,
    is_newer,
    parse_version,
    read_manifest,
    sha256_file,
    validate_manifest,
    verify_artifact_against_manifest,
    write_manifest,
)


# -- version parsing / comparison -------------------------------------------


@pytest.mark.parametrize(
    "version, expected",
    [
        ("0.1.0", (0, 1, 0)),
        ("1.2.3", (1, 2, 3)),
        ("2.0", (2, 0)),
        ("10.20.30", (10, 20, 30)),
        ("0.3.1-rc1", (0, 3, 1)),
    ],
)
def test_parse_version(version, expected):
    assert parse_version(version) == expected


@pytest.mark.parametrize("bad", ["", "abc", "..", None])
def test_parse_version_rejects_garbage(bad):
    with pytest.raises(ManifestError):
        parse_version(bad)


@pytest.mark.parametrize(
    "a, b, expected",
    [
        ("0.1.0", "0.1.0", 0),
        ("0.2.0", "0.1.0", 1),
        ("0.1.0", "0.2.0", -1),
        ("1.0", "1.0.0", 0),  # missing trailing component treated as 0
        ("1.0.1", "1.0", 1),
        ("0.10.0", "0.9.0", 1),  # numeric, not lexicographic
    ],
)
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


def test_is_newer():
    assert is_newer("0.3.1", "0.3.0") is True
    assert is_newer("0.3.0", "0.3.1") is False
    assert is_newer("0.3.0", "0.3.0") is False


# -- hashing / artifact description -----------------------------------------


def test_sha256_file(tmp_path):
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"hello ash captions")
    import hashlib

    expected = hashlib.sha256(b"hello ash captions").hexdigest()
    assert sha256_file(path) == expected


def test_build_artifact(tmp_path):
    path = tmp_path / "AshCaptions-0.1.0-win64.zip"
    path.write_bytes(b"x" * 4096)
    artifact = build_artifact(path, url="https://example.invalid/x.zip")
    assert artifact.filename == "AshCaptions-0.1.0-win64.zip"
    assert artifact.size_bytes == 4096
    assert len(artifact.sha256) == 64


def test_build_artifact_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_artifact(tmp_path / "nope.zip", url="https://example.invalid/x.zip")


# -- manifest building / validation ------------------------------------------


def _valid_artifact(tmp_path) -> Artifact:
    path = tmp_path / "AshCaptions-0.2.0-win64.zip"
    path.write_bytes(b"payload")
    return build_artifact(path, url="https://example.invalid/AshCaptions-0.2.0-win64.zip")


def test_build_manifest_roundtrip(tmp_path):
    artifact = _valid_artifact(tmp_path)
    manifest = build_manifest(
        version="0.2.0",
        artifact=artifact,
        channel="stable",
        build_date="2026-08-29T12:00:00+00:00",
        min_supported_version="0.1.0",
        notes="First public build",
    )
    assert manifest["schema_version"] == 1
    assert manifest["version"] == "0.2.0"
    assert manifest["artifact"]["filename"] == artifact.filename
    assert manifest["artifact"]["sha256"] == artifact.sha256
    validate_manifest(manifest)  # does not raise

    out = tmp_path / "manifest.json"
    write_manifest(manifest, out)
    reread = read_manifest(out)
    assert reread == manifest


def test_build_manifest_rejects_bad_version(tmp_path):
    artifact = _valid_artifact(tmp_path)
    with pytest.raises(ManifestError):
        build_manifest(version="not-a-version", artifact=artifact)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda m: m.pop("version"), "missing required keys"),
        (lambda m: m.update(schema_version=99), "unsupported schema_version"),
        (lambda m: m["artifact"].pop("sha256"), "missing required keys"),
        (lambda m: m["artifact"].update(sha256="not-hex"), "sha256"),
        (lambda m: m["artifact"].update(size_bytes=0), "size_bytes"),
        (lambda m: m["artifact"].update(size_bytes=-5), "size_bytes"),
    ],
)
def test_validate_manifest_rejects_malformed(tmp_path, mutate, match):
    artifact = _valid_artifact(tmp_path)
    manifest = build_manifest(version="0.2.0", artifact=artifact)
    mutate(manifest)
    with pytest.raises(ManifestError, match=match):
        validate_manifest(manifest)


def test_read_manifest_rejects_invalid_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        read_manifest(path)


def test_verify_artifact_against_manifest_ok(tmp_path):
    artifact_path = tmp_path / "AshCaptions-0.3.0-win64.zip"
    artifact_path.write_bytes(b"the real bytes")
    artifact = build_artifact(artifact_path, url="https://example.invalid/x.zip")
    manifest = build_manifest(version="0.3.0", artifact=artifact)

    verify_artifact_against_manifest(artifact_path, manifest)  # does not raise


def test_verify_artifact_against_manifest_detects_tamper(tmp_path):
    artifact_path = tmp_path / "AshCaptions-0.3.0-win64.zip"
    artifact_path.write_bytes(b"the real bytes")
    artifact = build_artifact(artifact_path, url="https://example.invalid/x.zip")
    manifest = build_manifest(version="0.3.0", artifact=artifact)

    # Simulate a corrupted/tampered download: same size, different content.
    assert len(b"the reel bytes") == len(b"the real bytes")
    artifact_path.write_bytes(b"the reel bytes")

    with pytest.raises(ManifestError):
        verify_artifact_against_manifest(artifact_path, manifest)
