"""Dependency contracts the web layer relies on.

The web layer never imports the queue or the language catalogue directly --
those belong to other modules (`engine`, and whatever owns the SQLite-backed
queue). Instead it depends on these protocols, and a concrete implementation
is injected into `create_app()`. Tests inject fakes; production wiring
(outside this package) injects the real queue and catalogue.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable, Protocol, runtime_checkable

from .models import Job, JobOptions, Language, PreviewJob, StyleSummary, UpdateApplyJob


class JobNotFoundError(Exception):
    """Raised by a JobQueue implementation when a job id does not exist."""


class JobNotRetryableError(Exception):
    """Raised by a JobQueue implementation when retry() is called on a job
    that is not currently in the `failed` state."""


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
