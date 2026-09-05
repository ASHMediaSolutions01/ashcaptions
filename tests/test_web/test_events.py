"""The SSE stream (spec §8.3) must hold ONE connection open through long
idle stretches and still deliver a state change on that same connection.

The regression these tests pin: `asyncio.wait_for(subscription.__anext__(),
timeout=...)` cancelled the generator on every idle second, so the stream
ended itself and the browser reconnected forever, never receiving a real
event. See `routes_events.py`'s module docstring.

Why these drive the ASGI app directly instead of using `TestClient`:
Starlette's test client buffers the *entire* response body before
returning it, and only delivers `http.disconnect` once the app has
finished -- so a correct, never-ending SSE stream can't be observed (or
ended) through it at all. The old header test passed only because the bug
killed the stream after a second. `_SSEConnection` below speaks raw ASGI:
frames are read as they're sent, and the test decides when the browser
"closes the tab" by injecting the disconnect message."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ash_captions.web.models import JobOptions, JobStatus

POLL = 0.02  # tiny, so a test can sit through many heartbeat timeouts quickly


@pytest.fixture
def sse_poll_interval() -> float:
    return POLL


class _SSEConnection:
    """One open `GET /api/events` against the ASGI app, on the current loop."""

    def __init__(self, app, path: str = "/api/events") -> None:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"127.0.0.1:8756"), (b"accept", b"text/event-stream")],
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8756),
        }
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._outbox: asyncio.Queue = asyncio.Queue()
        self.task = asyncio.ensure_future(app(scope, self._receive, self._send))
        self.frames: list[str] = []

    async def _receive(self):
        return await self._inbox.get()

    async def _send(self, message) -> None:
        await self._outbox.put(message)

    async def start(self) -> dict:
        message = await asyncio.wait_for(self._outbox.get(), 5)
        assert message["type"] == "http.response.start", message
        return {k.decode(): v.decode() for k, v in message["headers"]} | {"status": message["status"]}

    async def next_frame(self, timeout: float = 5.0) -> str | None:
        """The next SSE frame, or None once the stream has ended."""
        message = await asyncio.wait_for(self._outbox.get(), timeout)
        body = message.get("body", b"").decode()
        if not body and not message.get("more_body", False):
            return None
        self.frames.append(body)
        return body

    async def frame_matching(self, predicate, *, timeout: float = 5.0) -> str:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise AssertionError(f"no matching frame; last frames: {self.frames[-8:]}")
            frame = await self.next_frame(remaining)
            if frame is None:
                raise AssertionError(f"stream ended before the expected frame; saw {self.frames[-8:]}")
            if predicate(frame):
                return frame

    async def close_tab(self) -> None:
        """What the browser does on navigation: the server sees http.disconnect."""
        await self._inbox.put({"type": "http.disconnect"})
        await asyncio.wait_for(self.task, 5)


def _jobs_in(frame: str) -> list[dict]:
    assert frame.startswith("data: ")
    return json.loads(frame[len("data: "):])


def _is_jobs_frame(frame: str) -> bool:
    return frame.startswith("data: [")


class TestSSEHeaders:
    def test_events_endpoint_sets_sse_headers(self, app):
        async def scenario():
            conn = _SSEConnection(app)
            head = await conn.start()
            await conn.close_tab()
            return head

        head = asyncio.run(scenario())
        assert head["status"] == 200
        assert head["content-type"].startswith("text/event-stream")
        assert head["cache-control"] == "no-cache"
        assert head["connection"] == "keep-alive"


class TestSingleLongLivedConnection:
    def test_first_frames_are_retry_hint_then_snapshot(self, app):
        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            first = await conn.next_frame()
            snapshot = await conn.frame_matching(_is_jobs_frame)
            await conn.close_tab()
            return first, snapshot

        first, snapshot = asyncio.run(scenario())
        assert first == "retry: 2000\n\n"
        assert _jobs_in(snapshot) == []

    def test_idle_stream_sends_heartbeats_instead_of_closing(self, app):
        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            await conn.frame_matching(_is_jobs_frame)
            for _ in range(5):
                await conn.frame_matching(lambda f: f == ": keep-alive\n\n")
            assert not conn.task.done()  # still the same connection
            await conn.close_tab()

        asyncio.run(scenario())

    def test_state_change_after_many_idle_intervals_arrives_on_same_connection(self, app, fake_queue):
        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            await conn.frame_matching(_is_jobs_frame)  # initial snapshot

            # Sit through >10 poll intervals of silence. Under the old
            # wait_for() code the generator would have been cancelled and
            # the stream ended during this sleep.
            await asyncio.sleep(POLL * 15)
            assert not conn.task.done()

            fake_queue.submit(Path("hour-long-interview.mp4"), JobOptions(language="en", preset="POP"))
            frame = await conn.frame_matching(lambda f: _is_jobs_frame(f) and "hour-long-interview.mp4" in f)
            heartbeats = [f for f in conn.frames if f == ": keep-alive\n\n"]
            await conn.close_tab()
            return frame, heartbeats

        frame, heartbeats = asyncio.run(scenario())
        assert len(heartbeats) >= 5, "expected the idle stretch to be bridged by heartbeats"
        assert _jobs_in(frame)[0]["status"] == "pending"

    def test_progress_updates_keep_flowing_on_one_connection(self, app, fake_queue):
        job = fake_queue.submit(Path("clip.mp4"), JobOptions(language="en", preset="POP"))

        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            await conn.frame_matching(_is_jobs_frame)
            seen = []
            for progress in (0.1, 0.4, 0.9):
                await asyncio.sleep(POLL * 3)
                fake_queue.force_status(job.id, JobStatus.RUNNING, progress=progress, stage="transcribe")
                frame = await conn.frame_matching(lambda f, p=progress: _is_jobs_frame(f) and _jobs_in(f)[0]["progress"] == p)
                seen.append(_jobs_in(frame)[0])
            assert not conn.task.done()
            await conn.close_tab()
            return seen

        seen = asyncio.run(scenario())
        assert [j["progress"] for j in seen] == [0.1, 0.4, 0.9]
        assert all(j["stage"] == "transcribe" for j in seen)

    def test_closing_the_tab_ends_the_stream_and_the_subscription(self, app, fake_queue):
        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            await conn.frame_matching(_is_jobs_frame)
            assert len(fake_queue._subscribers) == 1
            await conn.close_tab()
            return len(fake_queue._subscribers)

        assert asyncio.run(scenario()) == 0

    def test_frames_carry_at_most_the_newest_100_jobs(self, app, fake_queue):
        for i in range(130):
            fake_queue.submit(Path(f"clip-{i:03d}.mp4"), JobOptions(language="en", preset="POP"))

        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            frame = await conn.frame_matching(_is_jobs_frame)
            await conn.close_tab()
            return _jobs_in(frame)

        jobs = asyncio.run(scenario())
        assert len(jobs) == 100
        assert jobs[0]["filename"] == "clip-129.mp4"


class TestHealth:
    def test_health_endpoint_degrades_to_unknown_when_queue_reports_nothing(self, client):
        res = client.get("/api/health")
        assert res.status_code == 200
        body = res.json()
        assert body["worker_alive"] is None
        assert body["last_watcher_poll"] is None
        assert body["server_time"]

    def test_health_endpoint_surfaces_queue_attributes(self, client, fake_queue):
        fake_queue.worker_alive = True
        fake_queue.last_watcher_poll = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
        body = client.get("/api/health").json()
        assert body["worker_alive"] is True
        assert body["last_watcher_poll"].startswith("2026-09-02T12:00")

    def test_health_method_on_queue_wins_over_attributes(self, client, fake_queue):
        fake_queue.worker_alive = True
        fake_queue.health = lambda: {"worker_alive": False, "last_watcher_poll": None}
        assert client.get("/api/health").json()["worker_alive"] is False

    def test_broken_health_probe_degrades_to_unknown(self, client, fake_queue):
        def boom():
            raise RuntimeError("db locked")

        fake_queue.health = boom
        assert client.get("/api/health").status_code == 200

    def test_stream_carries_health_events_with_heartbeats(self, app, fake_queue):
        fake_queue.worker_alive = True

        async def scenario():
            conn = _SSEConnection(app)
            await conn.start()
            frame = await conn.frame_matching(lambda f: f.startswith("event: health\n"))
            await conn.close_tab()
            return frame

        frame = asyncio.run(scenario())
        payload = json.loads(frame.split("data: ", 1)[1])
        assert payload["worker_alive"] is True
