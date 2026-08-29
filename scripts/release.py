"""Publish a built artifact to the public `ash-captions-releases` repo.

Run on Ghazi's build machine only, after `build.py`:

    .venv/Scripts/python.exe scripts/release.py --repo <owner>/ash-captions-releases

Two-repo design (spec section 11.4): source lives in the private
`ash-captions` repo and never leaves it. Built artifacts and the version
manifest go to a *separate public* repo containing no source and no secrets,
so `installer/install.ps1` and the in-app updater can hit plain
unauthenticated GitHub Release URLs -- there is no token to distribute or
rotate across six PCs, because there is nothing there worth protecting.

This script never reads, writes, or logs an auth token. Publishing is done
entirely through the `gh` CLI's own stored authentication (`gh auth login`,
run once on the build machine) -- see docs/INSTALL.md's Ghazi section. If
`gh` is not authenticated, `gh` itself will say so; this script does not try
to work around that.

The manifest schema this writes -- and the app-side updater must read -- is
documented in full in docs/INSTALL.md. In short: the *stable*, tag-independent
URL

    https://github.com/<owner>/ash-captions-releases/releases/latest/download/manifest.json

always resolves to the newest published manifest.json, whose embedded
`artifact.url` then points at that release's immutable, version-tagged asset.
The updater never needs to enumerate releases or parse tags -- it polls one
URL.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pkgtools.manifest import Artifact, build_manifest, write_manifest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST_DIR = REPO_ROOT / "dist"
MANIFEST_FILENAME = "manifest.json"


class ReleaseError(Exception):
    pass


def read_build_info(dist_dir: Path) -> dict:
    path = Path(dist_dir) / "build-info.json"
    if not path.is_file():
        raise ReleaseError(f"{path} not found -- run scripts/build.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def release_tag(version: str) -> str:
    return f"v{version}"


def artifact_download_url(*, repo: str, tag: str, filename: str) -> str:
    """The immutable, version-tagged asset URL. Never changes once published,
    unlike the "latest" alias used for the manifest itself."""
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def manifest_stable_url(*, repo: str) -> str:
    """The URL the updater actually polls: always the newest release's
    manifest.json, regardless of tag."""
    return f"https://github.com/{repo}/releases/latest/download/{MANIFEST_FILENAME}"


def build_release_manifest(
    build_info: dict, *, repo: str, channel: str, min_supported_version: str | None, notes: str | None
) -> dict:
    tag = release_tag(build_info["version"])
    artifact = Artifact(
        filename=build_info["artifact_filename"],
        url=artifact_download_url(repo=repo, tag=tag, filename=build_info["artifact_filename"]),
        sha256=build_info["sha256"],
        size_bytes=build_info["size_bytes"],
    )
    return build_manifest(
        version=build_info["version"],
        artifact=artifact,
        channel=channel,
        build_date=build_info.get("build_date"),
        min_supported_version=min_supported_version,
        notes=notes,
    )


def build_gh_view_args(*, repo: str, tag: str) -> list[str]:
    """Argv (without the leading "gh") to check whether `tag` already has a
    release -- decides create vs. upload --clobber."""
    return ["release", "view", tag, "--repo", repo]


def build_gh_create_args(
    *, repo: str, tag: str, title: str, notes: str, asset_paths: list[Path]
) -> list[str]:
    args = ["release", "create", tag, "--repo", repo, "--title", title, "--notes", notes]
    args += [str(p) for p in asset_paths]
    return args


def build_gh_upload_args(*, repo: str, tag: str, asset_paths: list[Path]) -> list[str]:
    """Re-publish onto an existing tag (re-running a release after fixing a
    build must not fail just because the tag exists already)."""
    args = ["release", "upload", tag, "--repo", repo, "--clobber"]
    args += [str(p) for p in asset_paths]
    return args


def run_gh(args: list[str]) -> subprocess.CompletedProcess:
    """The only function here that shells out. `gh` supplies its own auth
    from `gh auth login` -- this script passes no token, in an env var or
    otherwise."""
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def tag_already_released(*, repo: str, tag: str) -> bool:
    result = run_gh(build_gh_view_args(repo=repo, tag=tag))
    return result.returncode == 0


def publish_release(
    *,
    repo: str,
    dist_dir: Path = DEFAULT_DIST_DIR,
    channel: str = "stable",
    min_supported_version: str | None = None,
    notes: str | None = None,
) -> Path:
    """Orchestrate: read build-info.json, write manifest.json, and upload
    both the artifact zip and the manifest as release assets."""
    build_info = read_build_info(dist_dir)
    tag = release_tag(build_info["version"])

    manifest = build_release_manifest(
        build_info, repo=repo, channel=channel, min_supported_version=min_supported_version, notes=notes
    )
    manifest_path = Path(dist_dir) / MANIFEST_FILENAME
    write_manifest(manifest, manifest_path)

    artifact_path = Path(dist_dir) / build_info["artifact_filename"]
    if not artifact_path.is_file():
        raise ReleaseError(f"artifact not found: {artifact_path} -- run scripts/build.py first")

    asset_paths = [artifact_path, manifest_path]
    if tag_already_released(repo=repo, tag=tag):
        print(f"Release {tag} already exists on {repo}; uploading assets with --clobber")
        result = run_gh(build_gh_upload_args(repo=repo, tag=tag, asset_paths=asset_paths))
    else:
        print(f"Creating release {tag} on {repo}")
        result = run_gh(
            build_gh_create_args(
                repo=repo,
                tag=tag,
                title=f"ASH Captions {build_info['version']}",
                notes=notes or f"ASH Captions {build_info['version']}",
                asset_paths=asset_paths,
            )
        )

    if result.returncode != 0:
        raise ReleaseError(f"gh failed ({result.returncode}): {result.stderr.strip()}")

    print(result.stdout.strip())
    print(f"Manifest URL for the updater: {manifest_stable_url(repo=repo)}")
    return manifest_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        required=True,
        help="owner/ash-captions-releases -- the PUBLIC artifacts repo, not the source repo.",
    )
    parser.add_argument("--dist-dir", type=Path, default=DEFAULT_DIST_DIR)
    parser.add_argument("--channel", default="stable")
    parser.add_argument("--min-supported-version", default=None)
    parser.add_argument("--notes", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    publish_release(
        repo=args.repo,
        dist_dir=args.dist_dir,
        channel=args.channel,
        min_supported_version=args.min_supported_version,
        notes=args.notes,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
