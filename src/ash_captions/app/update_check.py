"""Checking for an update: the manifest, the comparison, the result.

Split from ``updater.py`` when that file outgrew the project's 500-line
ceiling. The seam is the one the module always had: **this half only ever
reads**, and can fail silently on a dead network because the spec says
working offline is a feature (4.4). ``updater.py`` keeps the half that
writes -- download, verify, extract, mirror, relaunch -- which raises,
because it only ever runs in direct response to an editor's click.

Everything here is re-exported from ``updater``, so nothing else had to
change its imports.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ash_captions.config import app_root

logger = logging.getLogger("ash_captions.app.updater")

MANIFEST_URL = (
    "https://github.com/ASHMediaSolutions01/ashcaptions-releases/"
    "releases/latest/download/manifest.json"
)
CHECK_TIMEOUT_SECONDS = 10


def _load_pkgtools_manifest():
    """Import ``scripts/pkgtools/manifest.py``'s version-comparison and
    validation logic -- the single source of truth this updater and
    ``scripts/release.py`` must agree on. Deliberately does not duplicate
    that logic as a fallback: two implementations that can disagree is
    exactly what reuse avoids.

    ``scripts/pkgtools`` IS shipped in the frozen bundle (see
    ``scripts/build.py``'s ``PKGTOOLS_DEST``); an ImportError here is
    treated as one more silent-no-op failure mode during a background
    check, same as a network failure.
    """
    scripts_dir = app_root() / "scripts"
    if scripts_dir.is_dir() and str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from pkgtools.manifest import (  # type: ignore[import-not-found]
        ManifestError,
        is_newer,
        validate_manifest,
        verify_artifact_against_manifest,
    )

    return ManifestError, is_newer, validate_manifest, verify_artifact_against_manifest


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    """What the tray menu / control page need to show and act on."""

    version: str
    notes: str | None
    download_url: str
    sha256: str
    size_bytes: int
    manifest: dict  # the full, already-validated manifest -- kept for verify_artifact_against_manifest


FetchManifest = Callable[[str, float], bytes]  # (url, timeout) -> raw response bytes


def _default_fetch_manifest(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed, hardcoded host
        return response.read()


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """What one check actually established.

    ``check_for_update`` collapses every non-update result to ``None``,
    which is right for the tray and the banner: both only ever act on a
    newer version. It is wrong for telling an editor where they stand,
    because "you are up to date" and "I could not reach the server" then
    look identical -- and the control page said neither, which is how a
    working updater came to look broken. Anything that reports a resting
    state uses this instead.
    """

    info: UpdateInfo | None
    ok: bool                    # the manifest was fetched, parsed and understood
    latest: str | None = None   # what it advertised, when we got that far
    detail: str | None = None   # why not, when ok is False


def check_for_update_outcome(
    current_version: str,
    *,
    manifest_url: str = MANIFEST_URL,
    fetch: FetchManifest = _default_fetch_manifest,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> CheckOutcome:
    """``check_for_update``, but saying which of its outcomes happened."""
    try:
        ManifestError, is_newer, validate_manifest, _verify = _load_pkgtools_manifest()
    except ImportError:
        logger.info("Update check skipped: packaging's manifest module isn't available here.")
        return CheckOutcome(None, ok=False, detail="This build cannot check for updates.")

    try:
        raw = fetch(manifest_url, timeout)
    except Exception as exc:  # noqa: BLE001 - any network failure is a silent no-op (spec 4.4)
        logger.info("Update check failed (network): %s", exc)
        return CheckOutcome(None, ok=False, detail="Couldn't reach the update server.")

    try:
        manifest = json.loads(raw)
        validate_manifest(manifest)
    except (ManifestError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        logger.info("Update check failed (malformed manifest): %s", exc)
        return CheckOutcome(None, ok=False, detail="The update server sent something unreadable.")

    try:
        newer = is_newer(manifest["version"], current_version)
    except ManifestError as exc:
        logger.info("Update check failed (bad version string): %s", exc)
        return CheckOutcome(None, ok=False, detail="The update server sent an unusable version.")

    if not newer:
        logger.debug(
            "Already up to date (running %s, latest published %s).",
            current_version, manifest["version"],
        )
        return CheckOutcome(None, ok=True, latest=manifest["version"])

    artifact = manifest["artifact"]
    logger.info("Update available: %s -> %s", current_version, manifest["version"])
    return CheckOutcome(
        UpdateInfo(
            version=manifest["version"],
            notes=manifest.get("notes"),
            download_url=artifact["url"],
            sha256=artifact["sha256"],
            size_bytes=artifact["size_bytes"],
            manifest=manifest,
        ),
        ok=True,
        latest=manifest["version"],
    )


def check_for_update(
    current_version: str,
    *,
    manifest_url: str = MANIFEST_URL,
    fetch: FetchManifest = _default_fetch_manifest,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> UpdateInfo | None:
    """Check once, synchronously. Returns an ``UpdateInfo`` only when the
    manifest advertises a strictly newer version; ``None`` for every other
    outcome -- same version, older version, no network, a malformed or
    unsupported manifest, or ``pkgtools`` being unavailable. Unchanged: the
    tray and the banner only ever act on a newer version. Callers that have
    to report a resting state want ``check_for_update_outcome``.
    """
    return check_for_update_outcome(
        current_version, manifest_url=manifest_url, fetch=fetch, timeout=timeout
    ).info


class UpdateState:
    """Thread-safe holder for the last check's result -- read by the tray
    menu and the control page, written by the background check thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._info: UpdateInfo | None = None
        self._outcome: CheckOutcome | None = None

    def get(self) -> UpdateInfo | None:
        with self._lock:
            return self._info

    def set(self, info: UpdateInfo | None) -> None:
        """Record a result. Kept for callers (and tests) that only have an
        ``UpdateInfo``; the outcome is inferred as a successful check, which
        is the only way one of these can have been produced."""
        with self._lock:
            self._info = info
            self._outcome = CheckOutcome(
                info, ok=True, latest=info.version if info is not None else None
            )

    def set_outcome(self, outcome: CheckOutcome) -> None:
        with self._lock:
            self._info = outcome.info
            self._outcome = outcome

    def outcome(self) -> CheckOutcome | None:
        """The last check's full result, or ``None`` if none has finished --
        which is not the same as "up to date" and must not be shown as it."""
        with self._lock:
            return self._outcome


def check_for_update_in_background(
    current_version: str,
    state: UpdateState,
    *,
    manifest_url: str = MANIFEST_URL,
    fetch: FetchManifest = _default_fetch_manifest,
    timeout: float = CHECK_TIMEOUT_SECONDS,
) -> threading.Thread:
    """Kick off ``check_for_update`` on a daemon thread and store the
    result in ``state``. Never blocks the caller -- startup must never
    wait on this -- and never blocks on a dead network beyond ``timeout``.
    """

    def run() -> None:
        try:
            state.set_outcome(check_for_update_outcome(
                current_version, manifest_url=manifest_url, fetch=fetch, timeout=timeout
            ))
        except Exception:  # noqa: BLE001 - a background check must never crash the app
            logger.exception("Unexpected error during background update check")

    thread = threading.Thread(target=run, name="ash-captions-update-check", daemon=True)
    thread.start()
    return thread


