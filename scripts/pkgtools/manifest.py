"""Version manifest generation and parsing.

This is the schema `installer/install.ps1` and the in-app updater (owned
elsewhere -- see docs/INSTALL.md) both read. It is documented in full in
`docs/INSTALL.md`; keep that doc in sync with this module.

Deliberately pure: everything here operates on paths and dicts it is handed
and does no networking, so it is safe to import from tests and from both
`build.py` (which produces a manifest) and `release.py` (which publishes one).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

REQUIRED_ARTIFACT_KEYS = {"filename", "url", "sha256", "size_bytes"}
REQUIRED_MANIFEST_KEYS = {"schema_version", "channel", "version", "build_date", "artifact"}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """Raised for a manifest that is missing keys or has the wrong shape."""


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file in fixed-size chunks so a multi-GB artifact never sits in
    memory whole."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_version(version: str) -> tuple[int, ...]:
    """Parse a dotted version string into a comparable tuple of ints.

    Accepts plain semver-ish strings ("0.3.1"). A trailing pre-release/build
    suffix on the last component (e.g. "0.3.1-rc1") is truncated to its
    leading digits so "0.3.1-rc1" sorts as (0, 3, 1) -- callers that care about
    pre-release ordering should not rely on this.
    """
    if not version or not isinstance(version, str):
        raise ManifestError(f"invalid version string: {version!r}")
    parts = version.strip().split(".")
    out: list[int] = []
    for part in parts:
        match = re.match(r"\d+", part)
        if not match:
            raise ManifestError(f"invalid version string: {version!r}")
        out.append(int(match.group()))
    if not out:
        raise ManifestError(f"invalid version string: {version!r}")
    return tuple(out)


def compare_versions(a: str, b: str) -> int:
    """Return -1, 0 or 1 as `a` is less than, equal to, or greater than `b`."""
    ta, tb = parse_version(a), parse_version(b)
    length = max(len(ta), len(tb))
    ta = ta + (0,) * (length - len(ta))
    tb = tb + (0,) * (length - len(tb))
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def is_newer(candidate: str, baseline: str) -> bool:
    """True when `candidate` is a strictly newer version than `baseline`.

    This is the exact question the in-app updater needs answered for each
    poll: "is what the manifest advertises newer than what is installed?"
    """
    return compare_versions(candidate, baseline) > 0


@dataclass(frozen=True)
class Artifact:
    filename: str
    url: str
    sha256: str
    size_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "url": self.url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def build_artifact(
    artifact_path: Path,
    *,
    url: str,
    filename: str | None = None,
) -> Artifact:
    """Describe a built artifact on disk: hash it and record its size.

    `url` is the public download URL it will be published at (or will be, at
    publish time) -- it is not derived here since that depends on the release
    tag, which `release.py` decides.
    """
    artifact_path = Path(artifact_path)
    if not artifact_path.is_file():
        raise FileNotFoundError(f"artifact not found: {artifact_path}")
    return Artifact(
        filename=filename or artifact_path.name,
        url=url,
        sha256=sha256_file(artifact_path),
        size_bytes=artifact_path.stat().st_size,
    )


def build_manifest(
    *,
    version: str,
    artifact: Artifact,
    channel: str = "stable",
    build_date: str | None = None,
    min_supported_version: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Assemble the manifest dict written beside a release.

    `build_date` defaults to now (UTC, ISO 8601) when omitted. Validated
    parsing of `version` happens here so a malformed version never makes it
    into a published manifest.
    """
    parse_version(version)  # raises ManifestError on garbage
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "channel": channel,
        "version": version,
        "build_date": build_date or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifact": artifact.as_dict(),
    }
    if min_supported_version is not None:
        manifest["min_supported_version"] = min_supported_version
    if notes:
        manifest["notes"] = notes
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict[str, Any]) -> None:
    """Raise ManifestError with a specific reason for anything malformed.

    Used both when building a manifest (catch mistakes before they publish)
    and when reading one back (never trust a downloaded file blindly, even
    from our own release repo).
    """
    if not isinstance(manifest, dict):
        raise ManifestError("manifest is not a JSON object")

    missing = REQUIRED_MANIFEST_KEYS - manifest.keys()
    if missing:
        raise ManifestError(f"manifest missing required keys: {sorted(missing)}")

    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported schema_version {manifest['schema_version']!r}; "
            f"this tool understands {SCHEMA_VERSION}"
        )

    parse_version(manifest["version"])
    if "min_supported_version" in manifest:
        parse_version(manifest["min_supported_version"])

    artifact = manifest["artifact"]
    if not isinstance(artifact, dict):
        raise ManifestError("manifest.artifact is not a JSON object")
    missing_artifact = REQUIRED_ARTIFACT_KEYS - artifact.keys()
    if missing_artifact:
        raise ManifestError(f"manifest.artifact missing required keys: {sorted(missing_artifact)}")

    sha = artifact["sha256"]
    if not isinstance(sha, str) or not _SHA256_RE.match(sha):
        raise ManifestError(f"manifest.artifact.sha256 is not a 64-char hex digest: {sha!r}")

    size = artifact["size_bytes"]
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ManifestError(f"manifest.artifact.size_bytes must be a positive int: {size!r}")

    if not isinstance(artifact["filename"], str) or not artifact["filename"]:
        raise ManifestError("manifest.artifact.filename must be a non-empty string")
    if not isinstance(artifact["url"], str) or not artifact["url"]:
        raise ManifestError("manifest.artifact.url must be a non-empty string")


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    validate_manifest(manifest)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> dict[str, Any]:
    """Read and validate a manifest file, raising ManifestError on anything
    that would confuse a downstream updater rather than letting a KeyError
    surface."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest is not valid JSON: {exc}") from exc
    validate_manifest(raw)
    return raw


def verify_artifact_against_manifest(artifact_path: Path, manifest: dict[str, Any]) -> None:
    """Verify a downloaded file matches the manifest's recorded hash/size.

    Raises ManifestError on any mismatch. This is the check the in-app
    updater must run before it ever unpacks or replaces a running install.
    """
    validate_manifest(manifest)
    artifact_path = Path(artifact_path)
    expected = manifest["artifact"]
    actual_size = artifact_path.stat().st_size
    if actual_size != expected["size_bytes"]:
        raise ManifestError(
            f"downloaded artifact size {actual_size} != manifest size {expected['size_bytes']}"
        )
    actual_sha = sha256_file(artifact_path)
    if actual_sha != expected["sha256"]:
        raise ManifestError(
            f"downloaded artifact sha256 {actual_sha} != manifest sha256 {expected['sha256']}"
        )
