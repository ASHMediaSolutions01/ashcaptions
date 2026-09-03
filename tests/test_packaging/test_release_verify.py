"""publish_release must refuse an artifact that no longer matches
build-info.json -- otherwise the manifest describes one file and the
release carries another, and every updater rejects it on hash."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import release  # noqa: E402
from pkgtools.manifest import sha256_file  # noqa: E402


def _fake_build_info(tmp_path: Path, *, version: str = "0.4.0") -> dict:
    artifact_path = tmp_path / f"AshCaptions-{version}-win64.zip"
    artifact_path.write_bytes(b"x" * 1024)
    info = {
        "version": version,
        "build_date": "2026-08-29T12:00:00+00:00",
        "artifact_filename": artifact_path.name,
        "sha256": sha256_file(artifact_path),
        "size_bytes": artifact_path.stat().st_size,
    }
    (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    return info


def test_publish_refuses_when_artifact_changed_since_build(tmp_path, monkeypatch):
    build_info = _fake_build_info(tmp_path)
    artifact = tmp_path / build_info["artifact_filename"]
    with artifact.open("ab") as f:
        f.write(b"one more byte")  # a rebuild or a hand edit after build.py ran

    monkeypatch.setattr(release, "run_gh", lambda args: subprocess.CompletedProcess(args, 0))

    with pytest.raises(release.ReleaseError, match="does not match build-info.json"):
        release.publish_release(repo="o/r", dist_dir=tmp_path)


def test_verify_passes_for_an_untouched_artifact(tmp_path):
    build_info = _fake_build_info(tmp_path)
    release.verify_artifact_matches_build_info(tmp_path, build_info)  # no raise
