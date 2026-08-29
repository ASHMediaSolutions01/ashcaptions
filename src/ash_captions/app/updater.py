"""In-app update checker (spec section 11.4; consumption contract fully
documented in ``docs/INSTALL.md`` -- read that first if anything here is
ambiguous, it is the source of truth `scripts/release.py` was built
against).

Behaviour, in order, none of it negotiable:

1. On launch, in the background: fetch the manifest, never blocking
   startup and never blocking on a dead network (bounded by a timeout).
2. Compare the manifest's version against the running one -- numeric,
   never lexicographic ("0.10.0" is newer than "0.9.0"). Reuses
   ``scripts/pkgtools/manifest.py``'s comparison logic rather than a
   second implementation that could disagree with it -- see
   ``_load_pkgtools_manifest``.
3. If newer: tell the editor and stop. Never downloads or applies on its
   own.
4. Only after an explicit click: download, verify sha256 (and size)
   against the manifest, then apply. That consent gate is the whole
   security model -- signing was deliberately not implemented (see
   ``docs/INSTALL.md``'s "Updates require an explicit click"), so a code
   path that applies without a human clicking would silently undo the
   reasoning that made that acceptable. Do not add one.

Failure behaviour matters more than the happy path: no network, GitHub
down, a malformed manifest, a 404 because the release repo does not exist
yet -- every one of these is a silent no-op with a log line during the
background *check*. An editor must never see an error, a dialog, or a
delayed startup because a check failed (spec section 4.4: working offline
is a feature). A failure during an *apply* -- which only ever happens in
direct response to an editor's click -- is the one place this module
raises, since staying silent there would hide something the editor is
actively waiting on.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ash_captions.config import app_root

logger = logging.getLogger("ash_captions.app.updater")

MANIFEST_URL = (
    "https://github.com/ASHMediaSolutions01/ashcaptions-releases/"
    "releases/latest/download/manifest.json"
)
CHECK_TIMEOUT_SECONDS = 10
DOWNLOAD_TIMEOUT_SECONDS = 30
DOWNLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB -- matches web/app.py's upload chunking

APP_NAME = "AshCaptions"
EXE_NAME = "AshCaptions.exe"


class UpdateApplyError(Exception):
    """Raised by ``download_and_verify_update``/``apply_update`` -- the
    only place this module raises, since both only ever run in direct
    response to an editor's explicit click and staying silent there would
    hide something they're actively waiting on.
    """


def _load_pkgtools_manifest():
    """Import ``scripts/pkgtools/manifest.py``'s version-comparison and
    validation logic -- the single source of truth this updater and
    ``scripts/release.py`` must agree on. Deliberately does not duplicate
    that logic as a fallback: two implementations that can disagree is
    exactly what reuse avoids.

    Only importable from a source checkout today -- ``scripts/`` is not
    bundled into the frozen PyInstaller build (see ``scripts/build.py``'s
    ``--add-data`` list, which currently ships only ``web/static`` and an
    optional model directory). Raises ``ImportError`` in that case; callers
    treat it as one more silent-no-op failure mode during a background
    check, same as a network failure -- see module docstring.
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
    unsupported manifest, or ``pkgtools`` being unavailable. Every branch
    here is a silent no-op by design; only the log line differs, so a
    caller never needs to distinguish "no update" from "couldn't check."
    """
    try:
        ManifestError, is_newer, validate_manifest, _verify = _load_pkgtools_manifest()
    except ImportError:
        logger.info("Update check skipped: packaging's manifest module isn't available here.")
        return None

    try:
        raw = fetch(manifest_url, timeout)
    except Exception as exc:  # noqa: BLE001 - any network failure is a silent no-op (spec 4.4)
        logger.info("Update check failed (network): %s", exc)
        return None

    try:
        manifest = json.loads(raw)
        validate_manifest(manifest)
    except (ManifestError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        logger.info("Update check failed (malformed manifest): %s", exc)
        return None

    try:
        newer = is_newer(manifest["version"], current_version)
    except ManifestError as exc:
        logger.info("Update check failed (bad version string): %s", exc)
        return None

    if not newer:
        logger.debug(
            "Already up to date (running %s, latest published %s).",
            current_version, manifest["version"],
        )
        return None

    artifact = manifest["artifact"]
    logger.info("Update available: %s -> %s", current_version, manifest["version"])
    return UpdateInfo(
        version=manifest["version"],
        notes=manifest.get("notes"),
        download_url=artifact["url"],
        sha256=artifact["sha256"],
        size_bytes=artifact["size_bytes"],
        manifest=manifest,
    )


class UpdateState:
    """Thread-safe holder for the last check's result -- read by the tray
    menu and the control page, written by the background check thread.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._info: UpdateInfo | None = None

    def get(self) -> UpdateInfo | None:
        with self._lock:
            return self._info

    def set(self, info: UpdateInfo | None) -> None:
        with self._lock:
            self._info = info


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
            info = check_for_update(
                current_version, manifest_url=manifest_url, fetch=fetch, timeout=timeout
            )
            state.set(info)
        except Exception:  # noqa: BLE001 - a background check must never crash the app
            logger.exception("Unexpected error during background update check")

    thread = threading.Thread(target=run, name="ash-captions-update-check", daemon=True)
    thread.start()
    return thread


DownloadFile = Callable[[str, Path, float], None]  # (url, dest, timeout) -> None


def _default_download_file(url: str, dest: Path, timeout: float) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as response, dest.open("wb") as out:  # noqa: S310
        while chunk := response.read(DOWNLOAD_CHUNK_SIZE):
            out.write(chunk)


def download_and_verify_update(
    info: UpdateInfo,
    *,
    dest_dir: Path,
    download_file: DownloadFile = _default_download_file,
    timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    """Download ``info.download_url`` into ``dest_dir``, then verify its
    sha256 and size against the manifest before returning -- mirrors
    ``pkgtools.manifest.verify_artifact_against_manifest()`` exactly
    (``docs/INSTALL.md``: "never unpack an artifact that fails it").

    Only ever called after an editor's explicit click, so unlike
    ``check_for_update`` this raises ``UpdateApplyError`` on any failure
    instead of a silent no-op -- staying quiet here would hide a failure
    from someone actively waiting on it. A verification failure deletes
    the downloaded file rather than leaving a rejected artifact on disk.
    """
    try:
        ManifestError, _is_newer, _validate, verify_artifact_against_manifest = _load_pkgtools_manifest()
    except ImportError as exc:
        raise UpdateApplyError("Update system unavailable (packaging module not bundled).") from exc

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = dest_dir / Path(info.download_url).name

    try:
        download_file(info.download_url, artifact_path, timeout)
    except Exception as exc:  # noqa: BLE001 - surfaced to the editor, not swallowed (see docstring)
        artifact_path.unlink(missing_ok=True)
        raise UpdateApplyError(f"Download failed: {exc}") from exc

    try:
        verify_artifact_against_manifest(artifact_path, info.manifest)
    except ManifestError as exc:
        artifact_path.unlink(missing_ok=True)  # never leave a failed-verification artifact behind
        raise UpdateApplyError(f"Downloaded update failed verification: {exc}") from exc

    return artifact_path


# -- apply -------------------------------------------------------------------
#
# Mirrors installer/install.ps1's Install-Bundle exactly (stop the running
# app, robocopy /MIR a freshly-extracted copy over the install directory,
# never an in-place overwrite of a running exe -- see docs/INSTALL.md).
# The running process cannot replace its own loaded exe/DLLs, so this
# extracts the verified download, then hands off to a short-lived detached
# helper script that waits for this process to exit before touching any
# files. The OS-level handoff (spawning that script) is the one piece of
# this whole module that cannot be safely exercised by an automated test --
# it is behind the `spawn_helper` seam below specifically so tests can
# verify everything up to that point (extraction, script content, the
# arguments handed to it) without it.

_APPLY_HELPER_TEMPLATE = """
param(
    [Parameter(Mandatory=$true)][int]$ParentProcessId,
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ExeName
)

$deadline = (Get-Date).AddSeconds(30)
while ((Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 250
}
Get-Process -Name 'AshCaptions' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

robocopy $SourceDir $InstallDir /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -lt 8) {
    Start-Process -FilePath (Join-Path $InstallDir $ExeName)
}
"""

SpawnHelper = Callable[[list[str]], None]


def _default_spawn_helper(argv: list[str]) -> None:
    # Detached: this process is about to exit as part of the update: the
    # helper must keep running after that, not be a child tied to it.
    subprocess.Popen(  # noqa: S603 - argv is built entirely from our own paths, no shell
        argv,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )


def apply_update(
    artifact_path: Path,
    *,
    install_dir: Path | None = None,
    extract_to: Path | None = None,
    spawn_helper: SpawnHelper = _default_spawn_helper,
) -> None:
    """Extract a verified update artifact and hand off to a detached
    helper that stops the running app, mirrors the new files into
    ``install_dir`` (default: the current install -- ``app_root()``), and
    relaunches it. Only ever call this after
    ``download_and_verify_update`` has already verified ``artifact_path``.

    Does not itself stop or restart anything -- by the time this returns,
    the caller is expected to shut down and exit so the helper's wait-for-
    exit loop can proceed. Raises ``UpdateApplyError`` if the artifact
    can't be extracted; the spawn step itself is not expected to fail
    (a launch failure there is a machine problem, not a data problem) but
    is not swallowed either -- OSError propagates.
    """
    artifact_path = Path(artifact_path)
    install_dir = Path(install_dir) if install_dir is not None else app_root()
    staging = Path(extract_to) if extract_to is not None else artifact_path.parent / "staged_update"

    try:
        with zipfile.ZipFile(artifact_path) as zf:
            zf.extractall(staging)
    except (zipfile.BadZipFile, OSError) as exc:
        raise UpdateApplyError(f"Could not extract update artifact: {exc}") from exc

    # build.py's zip contains a single top-level AshCaptions/ folder (see
    # install.ps1's Install-Bundle, which handles the same ambiguity);
    # unwrap it if present so `source_dir` always points at the folder
    # that actually contains AshCaptions.exe.
    candidate = staging / APP_NAME
    source_dir = candidate if (candidate / EXE_NAME).is_file() else staging
    if not (source_dir / EXE_NAME).is_file():
        raise UpdateApplyError(f"Extracted update at {staging} does not contain {EXE_NAME}.")

    helper_script = staging.parent / "apply_update.ps1"
    helper_script.write_text(_APPLY_HELPER_TEMPLATE, encoding="utf-8")

    argv = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(helper_script),
        "-ParentProcessId", str(os.getpid()),
        "-SourceDir", str(source_dir),
        "-InstallDir", str(install_dir),
        "-ExeName", EXE_NAME,
    ]
    logger.info("Handing off update apply to detached helper: %s", helper_script)
    spawn_helper(argv)
