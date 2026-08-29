from __future__ import annotations


class TestSSEHeaders:
    def test_events_endpoint_sets_sse_headers(self, client):
        with client.stream("GET", "/api/events") as res:
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("text/event-stream")
            assert res.headers["cache-control"] == "no-cache"
            assert res.headers["connection"] == "keep-alive"
