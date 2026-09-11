"""In-app update *applying* -- download, verify, mirror, relaunch (spec
11.4; the checking half is ``update_check.py``, re-exported below so this
module remains the public surface).

Originally one file for both halves; split when it outgrew 500 lines. The
seam is the one the behaviour already had: checking only reads and fails
silently, applying writes and raises.

Consumption contract fully documented in ``docs/INSTALL.md`` -- read that
first if anything here is ambiguous; it is the source of truth
``scripts/release.py`` was built against.

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

import logging
import os
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from ash_captions.config import app_root

from .jobobject import CREATE_BREAKAWAY_FROM_JOB
# Re-exported so this stays the public surface it has always been:
# every caller and test still imports these from `app.updater`.
from .update_check import (
    CHECK_TIMEOUT_SECONDS,
    MANIFEST_URL,
    CheckOutcome,
    FetchManifest,
    UpdateInfo,
    UpdateState,
    _load_pkgtools_manifest,
    check_for_update,
    check_for_update_in_background,
    check_for_update_outcome,
)

log = logging.getLogger(__name__)

logger = logging.getLogger("ash_captions.app.updater")

__all__ = [
    "CHECK_TIMEOUT_SECONDS", "MANIFEST_URL", "CheckOutcome", "FetchManifest",
    "UpdateInfo", "UpdateState", "check_for_update",
    "check_for_update_in_background", "check_for_update_outcome",
    "DOWNLOAD_TIMEOUT_SECONDS", "APP_NAME", "EXE_NAME", "UpdateApplyError",
    "JOB_RUNNING_MESSAGE", "SOURCE_CHECKOUT_MESSAGE", "download_and_verify_update",
    "apply_update", "clean_update_leftovers", "DownloadFile", "SpawnHelper",
    "HasRunningJob",
]

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

# The wait-for-exit deadline below is deliberately long (6 hours), and
# deliberately NOT the thing that bounds how long an in-flight job gets to
# finish. The studio runs this on 60-90 minute recordings: a CPU-only
# transcription plus burn-in of one of those legitimately takes hours, and
# an earlier version of this template used a 30-second figure that would
# have force-killed a perfectly healthy job every time, defeating the
# entire point of apply_update()'s has_running_job guard. The real gate
# lives in the Python process itself (see app/update_flow.py: an update is
# never applied while a job is running -- it waits, unbounded, for the
# queue to go idle before this helper is even spawned); this deadline is a
# last-resort backstop only for a process wedged badly enough that its own
# shutdown never completes -- not the normal exit path.
_HELPER_WAIT_DEADLINE_SECONDS = 6 * 3600

_APPLY_HELPER_TEMPLATE = f"""
param(
    [Parameter(Mandatory=$true)][int]$ParentProcessId,
    [Parameter(Mandatory=$true)][string]$SourceDir,
    [Parameter(Mandatory=$true)][string]$InstallDir,
    [Parameter(Mandatory=$true)][string]$ExeName
)

$deadline = (Get-Date).AddSeconds({_HELPER_WAIT_DEADLINE_SECONDS})
while ((Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 250
}}
Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($ExeName)) -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

robocopy $SourceDir $InstallDir /MIR /NFL /NDL /NJH /NJS /NC /NS | Out-Null
if ($LASTEXITCODE -lt 8) {{
    Start-Process -FilePath (Join-Path $InstallDir $ExeName)
}}
"""

# subprocess has no constant for this one. A hidden console, as opposed to
# DETACHED_PROCESS (no console at all): powershell.exe started with
# DETACHED_PROCESS exits immediately with code 0 without running the
# script, so the first real update (0.4.0 -> 0.4.1, 2026-09-04) downloaded,
# extracted, handed off -- and nothing came back.
CREATE_NO_WINDOW = 0x08000000

SpawnHelper = Callable[[list[str]], None]
HasRunningJob = Callable[[], bool]

JOB_RUNNING_MESSAGE = "A caption job is still running. Try again when the queue is clear."
SOURCE_CHECKOUT_MESSAGE = "This is a source checkout; update it with git, not the in-app updater."


def _default_spawn_helper(argv: list[str]) -> None:
    # This process is about to exit as part of the update: the helper must
    # keep running after that, not be a child tied to it. A hidden console
    # (CREATE_NO_WINDOW), never DETACHED_PROCESS -- see the constant above.
    # CREATE_BREAKAWAY_FROM_JOB matters just as much: the app sits in a
    # kill-on-close Job Object (app/jobobject.py) so ffmpeg dies with it,
    # and without breaking away the helper would die with it too -- the
    # app would exit and never come back. Retried without the flag only
    # if the OS refuses it (an outer job that forbids breakaway), with a
    # warning, since a helper that can't outlive us can't relaunch us.
    flags = CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        subprocess.Popen(  # noqa: S603 - argv is built entirely from our own paths, no shell
            argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, close_fds=True, **quiet
        )
    except OSError as exc:
        logger.warning(
            "Update helper could not break away from the job object (%s); it may be "
            "killed when this process exits, in which case relaunch AshCaptions by hand.",
            exc,
        )
        subprocess.Popen(argv, creationflags=flags, close_fds=True, **quiet)  # noqa: S603


def clean_update_leftovers(updates_dir: Path | str) -> int:
    """Remove what a finished update leaves behind: the staged tree (a full
    copy of the bundle, ~1 GB) and downloaded zips (~700 MB each). Called
    at startup, when no apply can be in flight. Returns entries removed."""
    updates_dir = Path(updates_dir)
    if not updates_dir.is_dir():
        return 0
    removed = 0
    for entry in updates_dir.iterdir():
        try:
            if entry.is_dir() and entry.name == "staged_update":
                shutil.rmtree(entry)
                removed += 1
            elif entry.is_file() and entry.suffix.lower() in (".zip", ".ps1", ".part"):
                entry.unlink()
                removed += 1
        except OSError as exc:
            log.warning("could not remove update leftover %s: %s", entry, exc)
    if removed:
        log.info("removed %d leftover update file(s) from %s", removed, updates_dir)
    return removed


def _refuse_escaping_members(zf: zipfile.ZipFile, staging: Path) -> None:
    """Raise unless every member lands inside ``staging``.

    ``extractall`` happily writes a member named ``../../evil`` outside
    the directory it was given. Reaching this point already means the
    artifact matched the manifest's sha256, so a zip that does this is a
    compromised manifest rather than a corrupt download -- which is
    exactly when the app is about to copy the result over its own install
    directory, and the worst possible moment to trust an archive's own
    idea of where its files belong.
    """
    root = staging.resolve()
    for name in zf.namelist():
        # Zip member names are always "/"-separated; a backslash inside
        # one is part of the name until it is joined onto a path.
        target = (root / name.replace("\\", "/")).resolve()
        if target != root and root not in target.parents:
            raise UpdateApplyError(
                f"The update archive contains a file that would be written outside "
                f"the staging directory ({name!r}); refusing to extract it."
            )


def apply_update(
    artifact_path: Path,
    *,
    has_running_job: HasRunningJob,
    install_dir: Path | None = None,
    extract_to: Path | None = None,
    spawn_helper: SpawnHelper = _default_spawn_helper,
) -> None:
    """Extract a verified update artifact and hand off to a detached
    helper that stops the running app, mirrors the new files into
    ``install_dir`` (default: the current install -- ``app_root()``), and
    relaunches it. Only ever call this after
    ``download_and_verify_update`` has already verified ``artifact_path``.

    ``apply_update()`` stops the running app to replace its files (spec:
    "not an in-place overwrite of a running exe") -- doing that mid-
    transcode would both lose an editor's in-progress job outright and,
    on the watch-folder path, potentially strand it: the input file may
    already be consumed, and ``JobStore.reset_stale_running()`` would
    requeue it from scratch on the next launch, so the editor would see a
    job mysteriously restart with no explanation. ``has_running_job`` is
    therefore a **required** parameter, not an optional safety net --
    there is deliberately no default that lets a caller silently skip it.
    Raises ``UpdateApplyError`` with an editor-facing message (never a
    generic error) if any job is running, checked once up front and once
    more immediately before the point of no return (the helper spawn), so
    a job that started during extraction is still caught.

    That second check does not close the window all the way: a job could
    still start in the moment between it and the caller's actual process
    exit, which is outside this function's control (this module never
    stops the worker or exits the process itself -- see below). Closing
    that residual window is the caller's responsibility, the same way the
    single-instance lock's real guarantee comes from an OS-level wait, not
    a check: the caller applying an update must shut the job worker down
    with a blocking, unbounded wait (``JobWorker.stop(timeout=None)``,
    not the short timeout a normal quit uses) before exiting, so any job
    that slipped past this function's checks still finishes -- genuinely,
    not just up to a timeout -- before the detached helper's own
    wait-for-exit loop lets it touch a single file.

    Does not itself stop or restart anything else -- by the time this
    returns, the caller is expected to shut down (per the paragraph
    above) and exit so the helper's wait-for-exit loop can proceed.
    Raises ``UpdateApplyError`` if the artifact can't be extracted; the
    spawn step itself is not expected to fail (a launch failure there is
    a machine problem, not a data problem) but is not swallowed either --
    OSError propagates.
    """
    if has_running_job():
        raise UpdateApplyError(JOB_RUNNING_MESSAGE)

    artifact_path = Path(artifact_path)
    install_dir = Path(install_dir) if install_dir is not None else app_root()
    if (install_dir / ".git").exists():
        # A developer's source checkout is updated with git; robocopy /MIR
        # over it would wipe the working tree. The control page already
        # refuses this (web.runtime.updates_supported); the tray path
        # arrives here directly.
        raise UpdateApplyError(SOURCE_CHECKOUT_MESSAGE)
    staging = Path(extract_to) if extract_to is not None else artifact_path.parent / "staged_update"

    try:
        # A previous, failed apply may have left a half-extracted tree
        # here; robocopy /MIR would faithfully mirror its stale files.
        if staging.exists():
            shutil.rmtree(staging)
        with zipfile.ZipFile(artifact_path) as zf:
            _refuse_escaping_members(zf, staging)
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

    # Re-checked immediately before the point of no return: a job could
    # have started during extraction, above. See the docstring for why
    # this still isn't the whole guarantee.
    if has_running_job():
        raise UpdateApplyError(JOB_RUNNING_MESSAGE)

    argv = [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-File", str(helper_script),
        "-ParentProcessId", str(os.getpid()),
        "-SourceDir", str(source_dir),
        "-InstallDir", str(install_dir),
        "-ExeName", EXE_NAME,
    ]
    logger.info("Handing off update apply to detached helper: %s", helper_script)
    spawn_helper(argv)
