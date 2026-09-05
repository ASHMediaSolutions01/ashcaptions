"""Caption styling routes (spec 7A): the style CRUD surface, font list, and
the style editor's live preview job flow. Split out of app.py to keep that
module a slim application factory -- `create_app()` builds this router with
its dependency getters and mounts it, same as `routes_updates.py`.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .interfaces import (
    PreviewBusyError,
    PreviewNotFoundError,
    PreviewRenderer,
    StyleIsShippedError,
    StyleNotFoundError,
    StyleProvider,
    StyleValidationFailedError,
)
from .models import PreviewJob, PreviewRequest, PreviewStatus, SoundSummary, StyleSummary
from .validation import validate_local_path


def build_styles_router(get_style_provider, get_preview_renderer) -> APIRouter:  # noqa: C901 - one branch per route
    """`get_style_provider`/`get_preview_renderer` are the same `Request ->
    StyleProvider`/`Request -> PreviewRenderer` closures `create_app()`
    builds for its own routes -- passed in rather than reconstructed here
    so there is exactly one `app.state.style_provider`/`preview_renderer`
    read path."""
    router = APIRouter()

    @router.get("/api/styles", response_model=list[StyleSummary])
    async def list_styles(
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> list[StyleSummary]:
        return style_provider.list_styles()

    @router.get("/api/styles/{name}", response_model=StyleSummary)
    async def get_style(
        name: str,
        shipped_only: bool = False,
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> StyleSummary:
        try:
            return style_provider.get_style(name, shipped_only=shipped_only)
        except StyleNotFoundError:
            raise HTTPException(status_code=404, detail=f"Style {name!r} not found.")

    @router.put("/api/styles/{name}", response_model=StyleSummary)
    async def save_style(
        name: str,
        definition: dict = Body(...),
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> StyleSummary:
        try:
            return style_provider.save_style(name, definition)
        except StyleValidationFailedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.post("/api/styles/{name}/reset", response_model=StyleSummary)
    async def reset_style(
        name: str,
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> StyleSummary:
        """Remove the local override of a built-in style so the shipped
        definition is what every future job uses again."""
        try:
            return style_provider.reset_style(name)
        except StyleNotFoundError:
            raise HTTPException(status_code=404, detail=f"{name!r} is not a built-in style.")

    @router.delete("/api/styles/{name}", status_code=204)
    async def delete_style(
        name: str,
        style_provider: StyleProvider = Depends(get_style_provider),
    ) -> Response:
        try:
            style_provider.delete_style(name)
        except StyleIsShippedError:
            raise HTTPException(
                status_code=409, detail=f"{name!r} is a built-in style and can't be deleted."
            )
        except StyleNotFoundError:
            raise HTTPException(status_code=404, detail=f"Style {name!r} not found.")
        return Response(status_code=204)

    @router.get("/api/fonts", response_model=list[str])
    async def list_fonts(style_provider: StyleProvider = Depends(get_style_provider)) -> list[str]:
        return style_provider.list_fonts()

    @router.get("/api/sounds", response_model=list[SoundSummary])
    async def list_sounds(style_provider: StyleProvider = Depends(get_style_provider)) -> list[SoundSummary]:
        """The bundled sound library, with a URL for each so the page can
        play it. An empty list is a real answer, not an error: a bundle
        from before v0.7 carries no sounds."""
        entries = await run_in_threadpool(_bundled_sounds, style_provider)
        return [
            SoundSummary(
                name=entry.name, label=entry.label, description=entry.description,
                duration_seconds=entry.duration_seconds,
                url=f"/api/sounds/{quote(entry.name)}",
            )
            for entry in entries
        ]

    @router.get("/api/sounds/{name}")
    async def serve_sound(
        name: str, style_provider: StyleProvider = Depends(get_style_provider)
    ) -> FileResponse:
        """Serve one bundled sound so the Styles page can play it.

        The name is matched against the library and the *library's* own
        path is served; nothing the browser sent is ever joined onto a
        directory. Same rule as ``serve_font_file``.
        """
        entry = next((e for e in _bundled_sounds(style_provider) if e.name == name), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{name!r} is not a bundled sound.")
        if not await run_in_threadpool(entry.path.is_file):
            raise HTTPException(status_code=404, detail=f"The sound {name!r} is not installed.")
        return FileResponse(
            entry.path, media_type="audio/wav", filename=entry.path.name,
            content_disposition_type="inline",
        )

    @router.post("/api/styles/preview", response_model=PreviewJob, status_code=202)
    async def submit_preview(
        body: PreviewRequest,
        preview_renderer: PreviewRenderer = Depends(get_preview_renderer),
    ) -> PreviewJob:
        """Kicks off a ~3s styled preview render (spec 7A.3) and returns a
        job handle immediately -- rendering takes real seconds, so the
        browser polls GET /api/styles/preview/{id} rather than this route
        blocking.

        `validate_local_path` touches the filesystem (exists/open) -- on a
        network share that can stall for seconds, so it runs off the event
        loop rather than freezing every other request, the SSE stream
        included."""
        video_path = await run_in_threadpool(validate_local_path, body.video_path)
        try:
            return preview_renderer.submit_preview(video_path, body.start_seconds, body.style)
        except StyleValidationFailedError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except PreviewBusyError:
            raise HTTPException(status_code=409, detail="A preview is already rendering. Wait for it to finish.")

    @router.get("/api/styles/preview/{job_id}", response_model=PreviewJob)
    async def get_preview(
        job_id: str,
        preview_renderer: PreviewRenderer = Depends(get_preview_renderer),
    ) -> PreviewJob:
        try:
            return preview_renderer.get_preview(job_id)
        except PreviewNotFoundError:
            raise HTTPException(status_code=404, detail=f"Preview job {job_id!r} not found.")

    @router.get("/api/styles/preview/{job_id}/clip")
    async def get_preview_clip(
        job_id: str,
        preview_renderer: PreviewRenderer = Depends(get_preview_renderer),
    ) -> FileResponse:
        try:
            job = preview_renderer.get_preview(job_id)
        except PreviewNotFoundError:
            raise HTTPException(status_code=404, detail=f"Preview job {job_id!r} not found.")
        if job.status != PreviewStatus.DONE or not job.clip_path:
            raise HTTPException(status_code=409, detail=f"Preview job {job_id!r} isn't ready yet.")
        return FileResponse(job.clip_path, media_type="video/mp4")

    return router


def _bundled_sounds(style_provider: StyleProvider) -> list:
    """The provider's sound library, or nothing.

    Probed with ``getattr`` like the queue's optional methods: a provider
    written before v0.7 (and every test fake) has no ``list_sounds``, and
    must give an empty library rather than a 500.
    """
    lister = getattr(style_provider, "list_sounds", None)
    if not callable(lister):
        return []
    return list(lister())
