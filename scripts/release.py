"""Publish a built artifact to the public `ashcaptions-releases` repo.

Run on Ghazi's build machine only, after `build.py`:

    .venv/Scripts/python.exe scripts/release.py --repo ASHMediaSolutions01/ashcaptions-releases

Two-repo design (spec section 11.4): source lives in the private
`ASHMediaSolutions01/ashcaptions` repo and never leaves it. Built artifacts
and the version manifest go to a *separate public* repo containing no source
and no secrets, so `installer/install.ps1` and the in-app updater can hit
plain unauthenticated GitHub Release URLs -- there is no token to distribute
or rotate across six PCs, because there is nothing there worth protecting.

This script never reads, writes, or logs an auth token. Publishing is done
entirely through the `gh` CLI's own stored authentication (`gh auth login`,
run once on the build machine) -- see docs/INSTALL.md's Ghazi section. If
`gh` is not authenticated, `gh` itself will say so; this script does not try
to work around that.

The manifest schema this writes -- and the app-side updater must read -- is
documented in full in docs/INSTALL.md. In short: the *stable*, tag-independent
URL

    https://github.com/ASHMediaSolutions01/ashcaptions-releases/releases/latest/download/manifest.json

always resolves to the newest published manifest.json, whose embedded
`artifact.url` then points at that release's immutable, version-tagged asset.
The updater never needs to enumerate releases or parse tags -- it polls one
URL.

`--repo` defaults to that real repo (see `DEFAULT_RELEASES_REPO` below) so a
plain `scripts/release.py` with no flags does the right thing -- but stays
overridable, since a hardcoded-only value would be wrong the day the repo
ever moves.

Two GitHub behaviours worth knowing before the first and the second run:

* A brand-new releases repo has no commits, and `gh release create` on an
  empty repository fails (GitHub cannot create the tag). This script checks
  for that up front and stops with instructions rather than a bare API error:
  seed the repo with one commit (any README will do) and re-run.
* Re-running for a version that already has a release re-uploads the assets
  with `--clobber`, which *replaces* same-named assets. That is deliberate --
  fixing a broken build must not be blocked by a tag existing -- but note the
  consequences: the artifact zip and manifest.json for that tag change under
  installs that already downloaded them (the manifest's sha256 changes with
  the zip, so an in-progress `install.ps1` download can fail verification and
  should simply be re-run), and the `latest/download/manifest.json` alias
  keeps pointing at whichever release is *newest*, so re-publishing an old
  version does not roll the fleet back. Bump the version for anything that
  has already reached an editor's machine.
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


def build_gh_commits_args(*, repo: str) -> list[str]:
    """Argv (without the leading "gh") to ask whether `repo` has any commit
    at all -- GitHub answers 409 "Git Repository is empty" when it does not,
    and `gh release create` fails on such a repo with a much less helpful
    message."""
    return ["api", f"repos/{repo}/commits?per_page=1"]


EMPTY_REPO_HINT = (
    "the releases repo {repo} has no commits yet, so GitHub cannot create a release "
    "tag on it. Seed it once with any commit, e.g.:\n"
    "    git clone https://github.com/{repo}.git && cd ashcaptions-releases\n"
    "    echo \"# ASH Captions releases\" > README.md && git add README.md\n"
    "    git commit -m \"Initial commit\" && git push\n"
    "then re-run scripts/release.py."
)


def repo_has_commits(*, repo: str) -> bool:
    result = run_gh(build_gh_commits_args(repo=repo))
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
        if not repo_has_commits(repo=repo):
            raise ReleaseError(EMPTY_REPO_HINT.format(repo=repo))
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


DEFAULT_RELEASES_REPO = "ASHMediaSolutions01/ashcaptions-releases"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        default=DEFAULT_RELEASES_REPO,
        help=(
            f"owner/ashcaptions-releases -- the PUBLIC artifacts repo, not the source "
            f"repo. Defaults to {DEFAULT_RELEASES_REPO}; override if that ever changes."
        ),
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
