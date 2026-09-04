"""Dependency contracts the web layer relies on.

The web layer never imports the queue or the language catalogue directly --
those belong to other modules (`engine`, and whatever owns the SQLite-backed
queue). Instead it depends on these protocols, and a concrete implementation
is injected into `create_app()`. Tests inject fakes; production wiring
(outside this package) injects the real queue and catalogue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable, NamedTuple, Protocol, runtime_checkable

from .models import Job, JobOptions, Language, PreviewJob, StyleSummary, UpdateApplyJob


class JobNotFoundError(Exception):
    """Raised by a JobQueue implementation when a job id does not exist."""


class JobNotRetryableError(Exception):
    """Raised by a JobQueue implementation when retry() is called on a job
    that is not currently in the `failed` state."""


class JobNotRemovableError(Exception):
    """Raised by a JobQueue implementation when remove_job() is called on a
    job that is still `pending` or `running` -- only finished rows leave
    the list."""


@runtime_checkable
class JobQueue(Protocol):
    """What the web layer needs from the queue.

    Implemented elsewhere (SQLite table + worker thread per spec §8.1). The
    web layer only ever reads state and enqueues/retries -- it never runs a
    job itself.
    """

    def list_jobs(self) -> list[Job]:
        """Return the current queue snapshot, newest first."""
        ...

    def get_job(self, job_id: str) -> Job | None:
        """Return a single job, or None if it does not exist."""
        ...

    def submit(self, file_path: Path, options: JobOptions) -> Job:
        """Enqueue a new job for `file_path` and return it as `pending`.

        Implementations own validating that `file_path` exists and is
        readable; the web layer already checked existence and extension
        before calling this, but the queue is the source of truth.
        """
        ...

    def retry(self, job_id: str) -> Job:
        """Requeue a failed job. Raises JobNotFoundError or
        JobNotRetryableError as appropriate; the web layer maps both to HTTP
        responses."""
        ...

    def subscribe(self) -> AsyncIterator[list[Job]]:
        """Yield a queue snapshot each time job state changes.

        Must be push-driven (e.g. backed by an asyncio.Condition/Queue), not
        a poll loop -- the SSE endpoint awaits this directly and must not
        busy-loop (spec §8.3).
        """
        ...

    # Optional extras -- the web layer probes for these with getattr()/a
    # signature check and degrades gracefully when they're missing:
    #
    # * ``list_jobs(limit: int)`` -- when the signature accepts ``limit``,
    #   the web layer passes 100 so an SSE frame never carries every job
    #   ever run (``routes_jobs.list_jobs_for_web``).
    # * ``health() -> object`` with ``worker_alive: bool | None`` and
    #   ``last_watcher_poll: str | datetime | None`` attributes (or keys),
    #   or those two as plain attributes on the queue itself -- surfaced
    #   as GET /api/health and the ``health`` SSE event
    #   (``routes_events.read_health``).
    # * ``restyle(job_id: str, preset: str, *, position=...) -> Job`` --
    #   regenerates the job's ``.ass`` from its persisted word timings in
    #   another look (fast, no transcription) and returns the updated job
    #   with ``options.preset`` changed; ``output_dir`` stays the same.
    #   ``position`` (v0.5) is ``(caption_x, caption_y)`` fractions of the
    #   frame or ``None`` to clear it; the route omits the keyword entirely
    #   when the request body carried no position keys, so it keeps
    #   whatever the job already has. Raises ``JobNotFoundError``, or
    #   ``ValueError`` when the job has no saved words (run by an older
    #   build), the preset is unknown, or the position is outside the frame.
    # * ``submit_burn(job_id: str, preset: str) -> Job`` -- enqueues a
    #   burn-only job for the same footage using the saved transcript in
    #   that look; returns the new ``pending`` job. Same errors.
    #   Both back the Studio page (``routes_studio.py``); a queue without
    #   them makes those routes answer 501.
    # * ``known_clients() -> list[str]`` -- distinct ``options.client``
    #   values on recent jobs, most recent first, for the control page's
    #   client picker (``routes_clients.py``). Without it the picker lists
    #   only the clients that have a glossary file.
    # * ``remove_job(job_id: str) -> None`` -- forget a finished job's row
    #   (its files stay on disk). Raises ``JobNotFoundError`` or
    #   ``JobNotRemovableError`` (still pending/running). Backs
    #   ``DELETE /api/jobs/{id}`` (``routes_desktop.py``); without it the
    #   route answers 501.


# --- The editor's own desktop -----------------------------------------------
#
# The app runs on the editor's PC, so a real Windows file dialog and a real
# Explorer window are available. The web layer reaches them through these
# two protocols; ``ash_captions.app.desktop`` holds the real implementations
# and ``create_app()`` constructs them by default (lazily), tests inject
# fakes. Neither takes a path from the request: the picker returns one, and
# the revealer only ever gets a job's own ``output_dir``.


class PickerBusyError(Exception):
    """Raised by FilePicker.pick_video() while a dialog is already open --
    two Open File dialogs at once would fight over the desktop. The route
    maps this to a 409."""


@runtime_checkable
class FilePicker(Protocol):
    """Opens a native Open File dialog filtered to video files and blocks
    until the editor picks something or cancels."""

    def pick_video(self) -> str | None:
        """The chosen path, or None when the dialog was cancelled or timed
        out. Blocking; the route runs it in the threadpool."""
        ...


@runtime_checkable
class PathRevealer(Protocol):
    """Shows a file or folder in the desktop's file manager."""

    def reveal(self, path: Path) -> None:
        """Open Explorer at ``path``: a file is shown selected inside its
        folder, a folder is simply opened. Raises FileNotFoundError when
        ``path`` does not exist."""
        ...


@runtime_checkable
class LanguageCatalogueProvider(Protocol):
    """What the web layer needs from the language + dialect catalogue
    (spec §7). Implemented in `ash_captions.languages`.
    """

    def list_languages(self) -> list[Language]:
        """Return every supported language with its dialect presets."""
        ...


# --- Caption styling (spec 7A) ----------------------------------------------
#
# The web layer never imports `ash_captions.styles` or `ash_captions.engine`
# directly here either -- same reasoning as the queue/catalogue above. The
# real implementations (`web.styles_adapter.StylesPackageAdapter`,
# `web.preview_adapter.InProcessPreviewRenderer`) are the ones allowed to
# depend on those packages; `create_app()` constructs them by default so
# production wiring needs no changes, but tests inject fakes instead.


class BundledFontFile(NamedTuple):
    """One bundled face and where its file lives -- what the optional
    ``StyleProvider.list_font_files()`` returns so the Studio page can serve
    the browser renderer the same font files ffmpeg burns with."""

    family: str
    path: Path


class StyleNotFoundError(Exception):
    """Raised by a StyleProvider when a style name is neither shipped nor
    a saved user style."""


class StyleValidationFailedError(Exception):
    """Raised by a StyleProvider when a style definition fails validation.
    `str(exc)` names the exact field at fault (e.g. "font: 'Comic Sans MS'
    is not a bundled font") so the web layer can return it verbatim in a
    400 response, never a 500."""


class StyleIsShippedError(Exception):
    """Raised by StyleProvider.delete_style() when asked to delete one of
    the built-in looks -- the shipped library must never be destroyable
    from the editor (spec 7A.2)."""


class PreviewNotFoundError(Exception):
    """Raised by PreviewRenderer.get_preview() when job_id does not exist."""


class PreviewBusyError(Exception):
    """Raised by PreviewRenderer.submit_preview() while another preview is
    still rendering -- previews share one Whisper model and one CPU, so
    they run one at a time. The web layer maps this to a 409."""


@runtime_checkable
class StyleProvider(Protocol):
    """What the style editor needs from the caption styling system (spec
    7A). Implemented over `ash_captions.styles` -- see `styles_adapter.py`.

    `reset_style` removes a user override of a *shipped* style so the
    built-in definition shows through again; it never touches the shipped
    file and raises StyleNotFoundError for a name that is not shipped.
    """

    def list_styles(self) -> list[StyleSummary]:
        """Every available style, shipped + user, for the style picker."""
        ...

    def get_style(self, name: str, *, shipped_only: bool = False) -> StyleSummary:
        """One style's full definition. Raises StyleNotFoundError.

        `shipped_only=True` bypasses any user override of `name` -- what
        the "reset to shipped" editor action fetches when a shipped look
        has been edited and saved under its own name.
        """
        ...

    def save_style(self, name: str, definition: dict[str, Any]) -> StyleSummary:
        """Validate `definition` and save it as a user style keyed by
        `name` (overriding a shipped style of the same name, per spec
        7A.2) -- the URL path segment is always the identity, regardless
        of any "name" field inside `definition`. Raises
        StyleValidationFailedError with a field-named message on bad input.
        """
        ...

    def delete_style(self, name: str) -> None:
        """Delete a user style. Raises StyleIsShippedError if `name` is one
        of the built-in looks, StyleNotFoundError if no user style exists
        under that name."""
        ...

    def list_fonts(self) -> list[str]:
        """Every bundled font family, for the font dropdown (spec 7A.4)."""
        ...

    # Optional extra, probed with getattr() like the queue's:
    #
    # * ``list_font_files() -> list[BundledFontFile]`` -- every manifest
    #   entry with the path its file is expected at (present or not). The
    #   Studio page serves exactly these files to the browser renderer
    #   (``routes_studio.py``: GET /api/fonts/files, /api/fonts/file/{name})
    #   and nothing else; without it those routes list nothing.


@runtime_checkable
class PreviewRenderer(Protocol):
    """Renders the style editor's live ~3s preview (spec 7A.3).

    Rendering costs real wall-clock time (a short transcription plus two
    ffmpeg passes), so this is job-shaped rather than a blocking call:
    `submit_preview` returns immediately with a `pending`/`running` job,
    and the browser polls `get_preview` for its status -- mirroring how
    `JobQueue` itself never blocks a request on real engine work.
    """

    def submit_preview(self, video_path: Path, start_seconds: float, style: dict[str, Any]) -> PreviewJob:
        """Start rendering a preview clip of `video_path` at `start_seconds`
        with `style` (an in-progress edit) burned in. Raises
        StyleValidationFailedError if `style` doesn't validate, and
        PreviewBusyError if a previous preview is still rendering."""
        ...

    def get_preview(self, job_id: str) -> PreviewJob:
        """Current status of a preview job. Raises PreviewNotFoundError."""
        ...


# --- Per-client glossaries --------------------------------------------------
#
# One shared glossary plus one file per client (`ash_captions.languages.
# glossary`). The web layer edits those files through this protocol; the
# real implementation (`web.glossary_adapter.ClientGlossaryFiles`) is the
# one allowed to import `ash_captions.languages`, same as the adapters above.


class GlossaryValidationFailedError(Exception):
    """Raised by ClientGlossaryProvider.write_glossary() when the text has
    lines the glossary parser would skip. `problems` names each one
    ("line 3: nothing after '=>'"); the route returns them as a 400."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@runtime_checkable
class ClientGlossaryProvider(Protocol):
    """The per-client glossary files, keyed by client name. Every method
    takes the *display* name ("Acme Corp"); the implementation derives the
    file slug. Callers sanitize the name first (`validation.validate_client_name`)."""

    def list_clients(self) -> list[str]:
        """Slugs of every client glossary file present (never the shared one)."""
        ...

    def read_glossary(self, client: str) -> str:
        """The file's text, or "" when the client has no file yet."""
        ...

    def write_glossary(self, client: str, text: str) -> None:
        """Validate and atomically replace the client's file. Raises
        GlossaryValidationFailedError; an empty text is allowed (it empties
        the file rather than deleting it)."""
        ...

    def slug_for(self, client: str) -> str:
        """The file stem the client's glossary lives under."""
        ...


# --- In-app updates (spec 11.4) ---------------------------------------------
#
# Checking for an update -- the background thread, and where its result is
# published -- is owned by `app/__main__.py`, not by anything here: it sets
# `app.state.update_state` itself (same `app.state` convention as
# `app.state.queue`, just wired after `create_app()` returns rather than
# through it -- see that module's own comment). `app.py`'s routes read
# whatever is on `app.state.update_state` structurally (only ever calling
# `.get()` on it and reading attributes off whatever that returns) and never
# import `ash_captions.app.updater` to do so, so this package stays
# decoupled from `app/` the same way it is from `engine`/`styles`/`pipeline`.
# This protocol only covers what happens *after* an editor clicks "Update
# now": download, verify, apply. `web.update_adapter.UpdaterAdapter` is the
# real implementation, constructed by `create_app()` by default.


class UpdateApplyBusyError(Exception):
    """Raised by an UpdateApplier when an apply job is already pending or
    running. Carries the live job's id so the caller can attach to it
    instead of starting a second download/extract/helper chain."""

    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


class UpdateApplyNotFoundError(Exception):
    """Raised by UpdateApplier.get_apply_status() when job_id does not exist."""


@runtime_checkable
class UpdateApplier(Protocol):
    """Downloads, verifies, and applies an in-app update (spec 11.4).

    Job-shaped like PreviewRenderer: downloading and verifying a
    multi-hundred-MB installer takes real time, so `submit_apply` returns
    immediately with a job the browser polls, rather than blocking.
    Applying restarts the app once it succeeds.
    """

    def submit_apply(self, update: Any, *, has_running_job: Callable[[], bool]) -> UpdateApplyJob:
        """Starts downloading, verifying, and applying `update` (whatever
        `app.state.update_state.get()` returned -- structurally an
        `app.updater.UpdateInfo`) on a background thread.

        `has_running_job` is forwarded to `app.updater.apply_update()`'s
        own required guard -- checked there once before extraction and
        once more immediately before the detached helper spawns -- and
        polled again afterward, genuinely unboundedly, before the job is
        marked done. `apply_update()`'s own docstring is explicit that
        closing that residual window (a job starting in the instant
        between its second check and the process actually exiting) is the
        caller's responsibility, normally via a blocking
        `JobWorker.stop(timeout=None)`; polling `has_running_job` is the
        closest equivalent reachable here, since this protocol has no
        reference to the real `JobWorker` -- see `update_adapter.py`.
        """
        ...

    def get_apply_status(self, job_id: str) -> UpdateApplyJob:
        """Raises UpdateApplyNotFoundError if job_id does not exist."""
        ...
