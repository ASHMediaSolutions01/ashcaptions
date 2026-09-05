"""The live-update stream (spec §8.3) and the health snapshot behind it.

`GET /api/events` is one long-lived Server-Sent Events connection per open
tab. It must survive an hour of silence -- a transcription reports no
progress for minutes at a time -- and it must deliver every change on the
*same* connection, not after a reconnect.

Why the pending-task dance below, rather than `asyncio.wait_for(
subscription.__anext__(), timeout=...)`: `wait_for` cancels the awaited
coroutine on timeout, and cancelling `__anext__()` throws CancelledError
*inside* the async generator, which terminates it. The next `__anext__()`
then raises StopAsyncIteration and the loop ends -- the connection closed
itself after one idle second, every second, and a real state change never
reached the browser (verified live: seven reconnects in 26 seconds). So
`__anext__()` is only ever awaited via `asyncio.wait({task}, timeout=...)`,
which leaves the task -- and the generator -- alive across timeouts; on a
timeout an SSE comment goes out as a heartbeat instead.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .interfaces import JobQueue
from .models import Job, QueueHealth
from .routes_jobs import JOB_LIST_LIMIT

DEFAULT_SSE_POLL_INTERVAL = 1.0  # seconds between disconnect checks / heartbeats
# What the browser waits before reconnecting after a drop; EventSource's own
# default is browser-specific, and a few seconds is right for a local app.
SSE_RETRY_MS = 2000
KEEP_ALIVE_FRAME = ": keep-alive\n\n"


def read_health(queue: JobQueue) -> QueueHealth:
    """Whatever liveness signals the queue exposes, and nothing it doesn't.
    See `interfaces.JobQueue` for the optional shapes probed here."""
    source: Any = queue
    health = getattr(queue, "health", None)
    if callable(health):
        try:
            source = health()
        except Exception:  # noqa: BLE001 - a health probe must never take the page down
            source = None
    return QueueHealth(
        worker_alive=_field(source, "worker_alive"),
        last_watcher_poll=_field(source, "last_watcher_poll"),
        server_time=datetime.now(timezone.utc),
    )


def _field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _sse(event: str | None, payload: Any) -> str:
    data = json.dumps(payload)
    return (f"event: {event}\n" if event else "") + f"data: {data}\n\n"


def _jobs_frame(snapshot: list[Job]) -> str:
    # Newest first is the JobQueue contract; cap to match GET /api/jobs.
    return _sse(None, [job.model_dump(mode="json") for job in list(snapshot)[:JOB_LIST_LIMIT]])


def _health_frame(queue: JobQueue) -> str:
    return _sse("health", read_health(queue).model_dump(mode="json"))


def build_events_router(get_queue: Callable[[Request], JobQueue]) -> APIRouter:
    router = APIRouter()

    @router.get("/api/health", response_model=QueueHealth)
    async def health(queue: JobQueue = Depends(get_queue)) -> QueueHealth:
        return read_health(queue)

    @router.get("/api/events")
    async def events(request: Request, queue: JobQueue = Depends(get_queue)) -> StreamingResponse:
        poll_interval: float = request.app.state.sse_poll_interval

        async def event_stream() -> AsyncIterator[str]:
            subscription = queue.subscribe()
            pending: asyncio.Task | None = None
            try:
                yield f"retry: {SSE_RETRY_MS}\n\n"
                while True:
                    # CRITICAL (spec §8.3): check disconnection on every loop
                    # iteration, not just when a new event arrives -- otherwise
                    # a closed tab with no further queue activity leaves this
                    # generator running forever.
                    if await request.is_disconnected():
                        break
                    if pending is None:
                        pending = asyncio.ensure_future(subscription.__anext__())
                    done, _ = await asyncio.wait({pending}, timeout=poll_interval)
                    if not done:
                        # Idle: the subscription stays armed; just prove the
                        # connection is alive to the browser and any proxy.
                        yield KEEP_ALIVE_FRAME
                        yield _health_frame(queue)
                        continue
                    finished, pending = pending, None
                    try:
                        snapshot = finished.result()
                    except StopAsyncIteration:
                        break
                    yield _jobs_frame(snapshot)
                    yield _health_frame(queue)
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    try:
                        await asyncio.wait({pending})
                    except Exception:  # noqa: BLE001, S110 - teardown must not raise over a closed connection
                        pass
                aclose = getattr(subscription, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # noqa: BLE001, S110
                        pass

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
