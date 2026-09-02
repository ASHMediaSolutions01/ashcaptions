"""In-app update routes (spec 11.4): the availability banner and the
apply-update job flow. Split out of app.py to keep that module a slim
application factory -- `create_app()` builds this router with its
dependency getters and mounts it, same as `routes_styles.py`.
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

# Deliberate, narrow exception to "this package never imports
# ash_captions.app.updater" (see interfaces.py's module docstring): a
# string constant only, no type/behavioral coupling. Reusing it rather
# than retyping the message keeps the API response and app.updater's own
# internal refusal (surfaced via UpdateApplyError -> str(exc), see
# update_adapter.py) from being able to drift apart -- team-lead's ask.
from ash_captions.app.updater import JOB_RUNNING_MESSAGE

from .interfaces import JobQueue, UpdateApplier, UpdateApplyNotFoundError
from .models import JobStatus, UpdateApplyJob, UpdateAvailable

UNSUPPORTED_INSTALL_DETAIL = (
    "In-app updates only work on an installed build. This copy is running from a source "
    "checkout -- pull the latest code instead."
)


def build_update_router(
    get_queue, get_update_applier, updates_supported: Callable[[], bool]
) -> APIRouter:
    """`get_queue`/`get_update_applier` are the same `Request -> JobQueue`/
    `Request -> UpdateApplier` closures `create_app()` builds for its own
    routes -- passed in rather than reconstructed here.

    `updates_supported` (normally `runtime.updates_supported`) gates the
    whole feature: when it says no, GET reports "no update" -- so the
    banner never shows -- and POST refuses with 409, even if a background
    check found a newer version. Applying robocopies a bundle over
    `app_root()`; on a source checkout that would mirror over the git
    repository (see `runtime.py`)."""
    router = APIRouter()

    @router.get("/api/update", response_model=UpdateAvailable | None)
    async def get_update(request: Request, queue: JobQueue = Depends(get_queue)) -> UpdateAvailable | None:
        if not updates_supported():
            return None
        info = _current_update_info(request)
        if info is None:
            return None
        return UpdateAvailable(
            version=info.version,
            notes=info.notes,
            size_bytes=info.size_bytes,
            blocked_reason=_update_blocked_reason(queue),
        )

    @router.post("/api/update/apply", response_model=UpdateApplyJob, status_code=202)
    async def submit_update_apply(
        request: Request,
        queue: JobQueue = Depends(get_queue),
        update_applier: UpdateApplier = Depends(get_update_applier),
    ) -> UpdateApplyJob:
        """The click IS the consent -- no confirmation dialog here or in the
        frontend (a second "are you sure?" just trains people to click
        through unread). Applying restarts the app; the control page says
        so beside the button, not this route."""
        if not updates_supported():
            raise HTTPException(status_code=409, detail=UNSUPPORTED_INSTALL_DETAIL)
        info = _current_update_info(request)
        if info is None:
            raise HTTPException(status_code=404, detail="No update is currently available.")

        blocked_reason = _update_blocked_reason(queue)
        if blocked_reason is not None:
            raise HTTPException(status_code=409, detail=blocked_reason)

        # Forwarded to app.updater.apply_update()'s own required guard
        # (checked again, twice, inside that function) -- this proactive
        # check above and that guard read the same live queue snapshot
        # conceptually, but are two separate calls in time, so both stay
        # in place rather than trusting the first.
        return update_applier.submit_apply(info, has_running_job=lambda: _any_job_running(queue))

    @router.get("/api/update/apply/{job_id}", response_model=UpdateApplyJob)
    async def get_update_apply(
        job_id: str,
        update_applier: UpdateApplier = Depends(get_update_applier),
    ) -> UpdateApplyJob:
        try:
            return update_applier.get_apply_status(job_id)
        except UpdateApplyNotFoundError:
            raise HTTPException(status_code=404, detail=f"Update job {job_id!r} not found.")

    return router


def _current_update_info(request: Request):
    """Whatever the last background check found -- structurally an
    `app.updater.UpdateInfo`, or None for "no update" (or "nobody has
    checked yet", which looks identical and is fine to treat the same way).

    Reads `request.app.state.update_state` via getattr rather than a
    dependency-injected getter (unlike queue/catalogue/style_provider/
    preview_renderer/update_applier) because that attribute isn't set by
    `create_app()` at all -- `app/__main__.py` sets it directly on the
    FastAPI app object it gets back, after construction (see
    `create_app()`'s own docstring on this). A test -- or any other caller
    of `create_app()` that never sets it -- gets a normal "no update"
    result here, not an AttributeError.
    """
    state = getattr(request.app.state, "update_state", None)
    if state is None:
        return None
    return state.get()


def _any_job_running(queue: JobQueue) -> bool:
    return any(job.status == JobStatus.RUNNING for job in queue.list_jobs())


def _update_blocked_reason(queue: JobQueue) -> str | None:
    """Non-None while applying an update should be refused because a
    caption job is running (`app.updater.apply_update`'s own required
    guard refuses this too, via the same `has_running_job` this module
    passes it -- checking here as well lets the control page disable its
    Update button proactively -- spec: "so the editor understands rather
    than clicks and gets rejected" -- instead of only finding out after a
    click)."""
    if _any_job_running(queue):
        return JOB_RUNNING_MESSAGE
    return None
