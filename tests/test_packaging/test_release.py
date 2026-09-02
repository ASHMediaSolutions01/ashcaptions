"""Tests for scripts/release.py.

`run_gh()` is monkeypatched everywhere here -- no real `gh` CLI invocation,
no network. This exercises the full publish orchestration (manifest
assembly, create-vs-upload branching) offline.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import release
from pkgtools.manifest import read_manifest


def _fake_build_info(tmp_path, *, version="0.4.0"):
    artifact_path = tmp_path / f"AshCaptions-{version}-win64.zip"
    artifact_path.write_bytes(b"x" * 1024)
    from pkgtools.manifest import sha256_file

    info = {
        "version": version,
        "build_date": "2026-08-29T12:00:00+00:00",
        "artifact_filename": artifact_path.name,
        "sha256": sha256_file(artifact_path),
        "size_bytes": artifact_path.stat().st_size,
    }
    (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    return info


# -- URL / tag construction --------------------------------------------


def test_release_tag():
    assert release.release_tag("0.4.0") == "v0.4.0"


def test_artifact_download_url():
    url = release.artifact_download_url(
        repo="ASHMediaSolutions01/ashcaptions-releases", tag="v0.4.0", filename="AshCaptions-0.4.0-win64.zip"
    )
    assert url == (
        "https://github.com/ASHMediaSolutions01/ashcaptions-releases/"
        "releases/download/v0.4.0/AshCaptions-0.4.0-win64.zip"
    )


def test_manifest_stable_url():
    url = release.manifest_stable_url(repo="ASHMediaSolutions01/ashcaptions-releases")
    assert url == (
        "https://github.com/ASHMediaSolutions01/ashcaptions-releases/"
        "releases/latest/download/manifest.json"
    )
    # Crucially: no tag/version in this URL -- it must always resolve to
    # whatever was published most recently.
    assert "v0.4.0" not in url


# -- manifest assembly from build-info.json ------------------------------


def test_build_release_manifest(tmp_path):
    build_info = _fake_build_info(tmp_path)
    manifest = release.build_release_manifest(
        build_info,
        repo="ASHMediaSolutions01/ashcaptions-releases",
        channel="stable",
        min_supported_version="0.1.0",
        notes="notes",
    )
    assert manifest["version"] == "0.4.0"
    assert manifest["artifact"]["url"] == (
        "https://github.com/ASHMediaSolutions01/ashcaptions-releases/"
        "releases/download/v0.4.0/AshCaptions-0.4.0-win64.zip"
    )
    assert manifest["artifact"]["sha256"] == build_info["sha256"]
    assert manifest["artifact"]["size_bytes"] == build_info["size_bytes"]


def test_read_build_info_missing(tmp_path):
    with pytest.raises(release.ReleaseError):
        release.read_build_info(tmp_path)


# -- gh argv construction (pure) ------------------------------------------


def test_build_gh_view_args():
    args = release.build_gh_view_args(repo="o/r", tag="v0.4.0")
    assert args == ["release", "view", "v0.4.0", "--repo", "o/r"]


def test_build_gh_create_args(tmp_path):
    zip_path = tmp_path / "a.zip"
    manifest_path = tmp_path / "manifest.json"
    args = release.build_gh_create_args(
        repo="o/r", tag="v0.4.0", title="ASH Captions 0.4.0", notes="notes here",
        asset_paths=[zip_path, manifest_path],
    )
    assert args[:2] == ["release", "create"]
    assert "v0.4.0" in args
    assert "--repo" in args and "o/r" in args
    assert str(zip_path) in args
    assert str(manifest_path) in args


def test_build_gh_upload_args_uses_clobber(tmp_path):
    zip_path = tmp_path / "a.zip"
    args = release.build_gh_upload_args(repo="o/r", tag="v0.4.0", asset_paths=[zip_path])
    assert args[:2] == ["release", "upload"]
    assert "--clobber" in args
    assert str(zip_path) in args


def test_release_script_never_touches_token_env_vars():
    """Nothing in this module should read GH_TOKEN/GITHUB_TOKEN -- publishing
    relies entirely on `gh`'s own stored auth (spec section 11.4: no token to
    distribute or rotate across six PCs)."""
    import inspect

    source = inspect.getsource(release)
    assert "GH_TOKEN" not in source
    assert "GITHUB_TOKEN" not in source
    assert "os.environ" not in source


# -- full orchestration, gh mocked ----------------------------------------


def test_publish_release_creates_when_tag_absent(tmp_path, monkeypatch):
    _fake_build_info(tmp_path)
    calls: list[list[str]] = []

    def fake_run_gh(args):
        calls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="not found")
        return subprocess.CompletedProcess(args, returncode=0, stdout="published ok", stderr="")

    monkeypatch.setattr(release, "run_gh", fake_run_gh)

    manifest_path = release.publish_release(repo="o/r", dist_dir=tmp_path)

    assert calls[0][:2] == ["release", "view"]
    assert calls[1][:1] == ["api"]  # empty-repo guard runs before create
    assert calls[2][:2] == ["release", "create"]
    manifest = read_manifest(manifest_path)
    assert manifest["version"] == "0.4.0"


def test_publish_release_refuses_an_empty_releases_repo(tmp_path, monkeypatch):
    """`gh release create` on a repo with no commits fails with an opaque
    API error; the script must say what to do instead (seed one commit)."""
    _fake_build_info(tmp_path)
    calls: list[list[str]] = []

    def fake_run_gh(args):
        calls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="not found")
        if args[:1] == ["api"]:
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="HTTP 409: Git Repository is empty.")
        return subprocess.CompletedProcess(args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(release, "run_gh", fake_run_gh)

    with pytest.raises(release.ReleaseError, match="no commits yet"):
        release.publish_release(repo="o/r", dist_dir=tmp_path)
    assert not any(c[:2] == ["release", "create"] for c in calls)


def test_build_gh_commits_args():
    assert release.build_gh_commits_args(repo="o/r") == ["api", "repos/o/r/commits?per_page=1"]


def test_publish_release_uploads_when_tag_exists(tmp_path, monkeypatch):
    _fake_build_info(tmp_path)
    calls: list[list[str]] = []

    def fake_run_gh(args):
        calls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, returncode=0, stdout="exists", stderr="")
        return subprocess.CompletedProcess(args, returncode=0, stdout="uploaded ok", stderr="")

    monkeypatch.setattr(release, "run_gh", fake_run_gh)

    release.publish_release(repo="o/r", dist_dir=tmp_path)

    assert calls[1][:2] == ["release", "upload"]
    assert "--clobber" in calls[1]


def test_publish_release_raises_on_gh_failure(tmp_path, monkeypatch):
    _fake_build_info(tmp_path)

    def fake_run_gh(args):
        if args[:1] == ["api"]:  # the repo has commits; it is `release create` that fails
            return subprocess.CompletedProcess(args, returncode=0, stdout="[]", stderr="")
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(release, "run_gh", fake_run_gh)

    with pytest.raises(release.ReleaseError, match="boom"):
        release.publish_release(repo="o/r", dist_dir=tmp_path)


def test_publish_release_missing_artifact(tmp_path, monkeypatch):
    build_info = _fake_build_info(tmp_path)
    # Remove the artifact the build-info.json claims exists.
    (tmp_path / build_info["artifact_filename"]).unlink()

    monkeypatch.setattr(release, "run_gh", lambda args: subprocess.CompletedProcess(args, 1))

    with pytest.raises(release.ReleaseError, match="artifact not found"):
        release.publish_release(repo="o/r", dist_dir=tmp_path)


def test_parse_args_defaults_to_real_releases_repo():
    """A plain `scripts/release.py` with no flags must resolve to the real
    public artifacts repo, not 404 against a placeholder."""
    args = release.parse_args([])
    assert args.repo == "ASHMediaSolutions01/ashcaptions-releases"
    assert args.repo == release.DEFAULT_RELEASES_REPO


def test_parse_args_repo_override():
    args = release.parse_args(["--repo", "o/r"])
    assert args.repo == "o/r"
    assert args.channel == "stable"


def test_install_ps1_default_manifest_repo_matches_release_py():
    """installer/install.ps1's zero-argument download target and
    release.py's DEFAULT_RELEASES_REPO must name the same repo -- a drift
    here means a fresh install 404s even though publishing still works."""
    install_ps1 = Path(__file__).resolve().parents[2] / "installer" / "install.ps1"
    text = install_ps1.read_text(encoding="utf-8")
    assert release.DEFAULT_RELEASES_REPO in text
    assert release.manifest_stable_url(repo=release.DEFAULT_RELEASES_REPO) in text
