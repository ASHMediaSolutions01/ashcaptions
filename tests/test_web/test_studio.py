"""Studio page (routes_studio.py + static/studio.*): the restyle/burn routes
against the fake queue, the .srt and burned-output routes (Range against a
real temp file), the font-file routes refusing anything the manifest
doesn't list, the page rendering with the cache-buster stamped and every
asset it references resolving, and the vendored JASSUB runtime being
present."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ash_captions.web.app import STATIC_DIR
from ash_captions.web.models import JobOptions, JobStatus

from .conftest import LOCAL_BASE_URL
from .fakes import FakeJobQueue, FakeStyleProvider

VIDEO = bytes(range(256)) * 40  # 10,240 distinguishable bytes
ASS = b"[Script Info]\nTitle: original\n"
SRT = b"1\n00:00:00,000 --> 00:00:01,000\nhello there\n"


@pytest.fixture
def fake_queue(tmp_path) -> FakeJobQueue:
    return FakeJobQueue(output_root=tmp_path / "out", known_presets={"CLEAN", "POP"})


@pytest.fixture
def fake_style_provider(tmp_path) -> FakeStyleProvider:
    # Only Inter's file is "installed"; the other bundled faces are listed
    # by the manifest but absent on disk, like a fresh checkout.
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "Inter-Regular.ttf").write_bytes(b"\x00\x01\x00\x00" + b"F" * 100)
    return FakeStyleProvider(fonts_dir=fonts_dir)


@pytest.fixture
def finished_job(fake_queue, tmp_path):
    video = tmp_path / "footage" / "reel.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(VIDEO)
    job = fake_queue.submit(video, JobOptions(language="en", preset="POP"))
    fake_queue.force_status(job.id, JobStatus.DONE, progress=1.0)
    out = Path(job.output_dir)
    out.mkdir(parents=True)
    (out / "reel.srt").write_bytes(SRT)
    (out / "reel.ass").write_bytes(ASS)
    (out / "reel.captioned.mp4").write_bytes(VIDEO)
    return fake_queue.get_job(job.id)


class LegacyQueue:
    """A queue from before Studio existed: reads only, no restyle/submit_burn."""

    def __init__(self, inner: FakeJobQueue) -> None:
        self._inner = inner

    def list_jobs(self):
        return self._inner.list_jobs()

    def get_job(self, job_id):
        return self._inner.get_job(job_id)


class TestSingleJob:
    def test_returns_one_job(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}")
        assert res.status_code == 200
        assert res.json()["id"] == finished_job.id
        assert res.json()["status"] == "done"

    def test_unknown_job_is_404(self, client):
        assert client.get("/api/jobs/nope").status_code == 404


class TestRestyle:
    def test_changes_the_preset_and_the_served_track(self, client, finished_job, fake_queue):
        assert client.get(f"/api/jobs/{finished_job.id}/ass").text.startswith("[Script Info]\nTitle: original")
        res = client.post(f"/api/jobs/{finished_job.id}/restyle", json={"preset": "CLEAN"})
        assert res.status_code == 200
        body = res.json()
        assert body["options"]["preset"] == "CLEAN"
        assert body["output_dir"] == finished_job.output_dir
        assert fake_queue.restyled == [(finished_job.id, "CLEAN")]
        assert "restyled as CLEAN" in client.get(f"/api/jobs/{finished_job.id}/ass").text

    def test_unknown_job_is_404(self, client):
        assert client.post("/api/jobs/nope/restyle", json={"preset": "CLEAN"}).status_code == 404

    def test_unknown_preset_is_409_with_the_queues_message(self, client, finished_job):
        res = client.post(f"/api/jobs/{finished_job.id}/restyle", json={"preset": "NOPE"})
        assert res.status_code == 409
        assert "NOPE" in res.json()["detail"]

    def test_job_without_saved_words_is_409(self, client, finished_job, fake_queue):
        fake_queue.no_saved_words.add(finished_job.id)
        res = client.post(f"/api/jobs/{finished_job.id}/restyle", json={"preset": "CLEAN"})
        assert res.status_code == 409
        assert "older version" in res.json()["detail"]

    def test_empty_preset_is_rejected(self, client, finished_job):
        assert client.post(f"/api/jobs/{finished_job.id}/restyle", json={"preset": ""}).status_code == 422

    def test_queue_without_restyle_is_501(self, client, finished_job, fake_queue):
        client.app.state.queue = LegacyQueue(fake_queue)
        res = client.post(f"/api/jobs/{finished_job.id}/restyle", json={"preset": "CLEAN"})
        assert res.status_code == 501
        assert res.json()["detail"] == "this build cannot restyle"

    def test_requires_the_client_header_like_every_mutation(self, app, finished_job, fake_queue):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.post(f"/api/jobs/{finished_job.id}/restyle", json={"preset": "CLEAN"}).status_code == 403
        assert foreign.post(f"/api/jobs/{finished_job.id}/burn", json={"preset": "CLEAN"}).status_code == 403
        assert fake_queue.restyled == [] and fake_queue.burns == []


class TestBurn:
    def test_enqueues_a_pending_burn_job_for_the_same_footage(self, client, finished_job):
        res = client.post(f"/api/jobs/{finished_job.id}/burn", json={"preset": "CLEAN"})
        assert res.status_code == 201
        burn = res.json()
        assert burn["id"] != finished_job.id
        assert burn["status"] == "pending"
        assert burn["options"]["burn_in"] is True
        assert burn["options"]["preset"] == "CLEAN"
        assert burn["input_path"] == finished_job.input_path
        assert burn["id"] in {j["id"] for j in client.get("/api/jobs").json()}

    def test_unknown_job_is_404_and_unknown_preset_409(self, client, finished_job):
        assert client.post("/api/jobs/nope/burn", json={"preset": "CLEAN"}).status_code == 404
        assert client.post(f"/api/jobs/{finished_job.id}/burn", json={"preset": "NOPE"}).status_code == 409

    def test_queue_without_submit_burn_is_501(self, client, finished_job, fake_queue):
        client.app.state.queue = LegacyQueue(fake_queue)
        res = client.post(f"/api/jobs/{finished_job.id}/burn", json={"preset": "CLEAN"})
        assert res.status_code == 501
        assert "cannot burn" in res.json()["detail"]


class TestSrt:
    def test_serves_the_transcript(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/srt")
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/plain")
        assert "hello there" in res.text

    def test_no_srt_is_404(self, client, finished_job):
        (Path(finished_job.output_dir) / "reel.srt").unlink()
        assert client.get(f"/api/jobs/{finished_job.id}/srt").status_code == 404


class TestOutput:
    def test_full_response_advertises_ranges(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/output")
        assert res.status_code == 200
        assert res.headers["accept-ranges"] == "bytes"
        assert res.headers["content-type"] == "video/mp4"
        assert res.headers["content-disposition"].startswith("inline")
        assert res.content == VIDEO

    def test_range_request_returns_the_exact_slice(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/output", headers={"Range": "bytes=2000-2999"})
        assert res.status_code == 206
        assert res.headers["content-range"] == f"bytes 2000-2999/{len(VIDEO)}"
        assert res.content == VIDEO[2000:3000]

    def test_unsatisfiable_range_is_416(self, client, finished_job):
        res = client.get(f"/api/jobs/{finished_job.id}/output", headers={"Range": f"bytes={len(VIDEO) + 5}-"})
        assert res.status_code == 416

    def test_prefers_the_captioned_file_over_other_mp4s(self, client, finished_job):
        (Path(finished_job.output_dir) / "a-preview.mp4").write_bytes(b"not it")
        assert client.get(f"/api/jobs/{finished_job.id}/output").content == VIDEO

    def test_no_burned_output_is_404(self, client, finished_job):
        (Path(finished_job.output_dir) / "reel.captioned.mp4").unlink()
        assert client.get(f"/api/jobs/{finished_job.id}/output").status_code == 404


class TestFonts:
    def test_lists_only_installed_faces_with_their_urls(self, client):
        res = client.get("/api/fonts/files")
        assert res.status_code == 200
        assert res.json() == [{"family": "Inter", "url": "/api/fonts/file/Inter-Regular.ttf"}]

    def test_serves_a_listed_font_file(self, client):
        res = client.get("/api/fonts/file/Inter-Regular.ttf")
        assert res.status_code == 200
        assert res.headers["content-type"] == "font/ttf"
        assert res.content.startswith(b"\x00\x01\x00\x00")

    def test_listed_but_missing_file_is_404(self, client):
        res = client.get("/api/fonts/file/Montserrat-Regular.ttf")
        assert res.status_code == 404
        assert "not installed" in res.json()["detail"]

    def test_refuses_names_not_in_the_manifest(self, client, tmp_path):
        (tmp_path / "fonts" / "secret.txt").write_text("nope")
        for name in ("secret.txt", "Arial.ttf", "..%2Fsecret.txt", "%2E%2E%2Fsecret.txt"):
            res = client.get(f"/api/fonts/file/{name}")
            assert res.status_code == 404, name
            assert b"nope" not in res.content

    def test_provider_without_font_files_lists_nothing(self, client):
        client.app.state.style_provider = FakeStyleProvider()
        assert client.get("/api/fonts/files").json() == []
        assert client.get("/api/fonts/file/Inter-Regular.ttf").status_code == 404

    def test_real_adapter_lists_exactly_the_manifest(self):
        from ash_captions.styles.fonts import load_manifest
        from ash_captions.web.styles_adapter import StylesPackageAdapter

        files = StylesPackageAdapter().list_font_files()
        assert {f.path.name for f in files} == {entry.file for entry in load_manifest()}
        assert "Inter-Regular.ttf" in {f.path.name for f in files}
        assert "manifest.json" not in {f.path.name for f in files}


class TestPage:
    def test_studio_page_is_served_with_resolving_assets(self, client, app):
        page = client.get("/studio/any-id")
        assert page.status_code == 200
        assert "__VERSION__" not in page.text
        assert f"/static/studio.js?v={app.state.version}" in page.text
        assert f"/static/vendor/jassub/jassub.umd.js?v={app.state.version}" in page.text
        assets = set(re.findall(r'(?:src|href)="(/static/[^"?]+)', page.text))
        assert assets
        for asset in assets:
            assert client.get(asset).status_code == 200, asset

    def test_player_script_references_only_vendored_files_that_exist(self, client):
        script = (STATIC_DIR / "studio_player.js").read_text(encoding="utf-8")
        assert 'VENDOR = "/static/vendor/jassub/"' in script
        names = set(re.findall(r'VENDOR \+ "([^"]+)"', script))
        assert names == {"default.woff2", "jassub-worker.js", "jassub-worker.wasm", "jassub-worker-modern.wasm"}
        for name in names:
            assert client.get(f"/static/vendor/jassub/{name}").status_code == 200, name

    def test_jassub_runtime_is_present_and_real(self):
        vendor = STATIC_DIR / "vendor" / "jassub"
        for name in ("jassub.umd.js", "jassub-worker.js", "default.woff2", "LICENSE", "COPYRIGHT", "README.md"):
            assert (vendor / name).stat().st_size > 0, name
        for name in ("jassub-worker.wasm", "jassub-worker-modern.wasm"):
            data = (vendor / name).read_bytes()
            assert data[:4] == b"\x00asm", name  # a real WebAssembly module, not an HTML error page
            assert len(data) > 1_000_000, name
        assert "1.8.8" in (vendor / "README.md").read_text(encoding="utf-8")

    def test_wasm_and_worker_are_served_with_sensible_types(self, client):
        assert client.get("/static/vendor/jassub/jassub-worker.wasm").headers["content-type"] == "application/wasm"
        assert "javascript" in client.get("/static/vendor/jassub/jassub-worker.js").headers["content-type"]

    def test_no_hand_typed_version_in_the_studio_page(self):
        html = (STATIC_DIR / "studio.html").read_text(encoding="utf-8")
        assert not re.search(r"\?v=\d", html)
        assert "__VERSION__" in html

    def test_control_page_carries_the_studio_hook(self, client, app):
        index = client.get("/").text
        assert 'id="open-studio-check" checked' in index
        assert f"/static/studio_hook.js?v={app.state.version}" in index
        app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        for call in ("AshStudio.noteSubmitted(", "AshStudio.onJobs("):
            assert call in app_js, call
        # Finished cards link to Studio, and a finished job this tab started
        # is announced (toast + notification) before Studio opens.
        queue_js = (STATIC_DIR / "queue.js").read_text(encoding="utf-8")
        assert '"Open in Studio"' in queue_js and "/studio/" in queue_js
        hook_js = (STATIC_DIR / "studio_hook.js").read_text(encoding="utf-8")
        assert "AshQueue.jobFinished(job)" in hook_js

    def test_every_page_loads_the_shared_theme_and_nav(self, client, app):
        for url in ("/", "/style-editor", "/guide", "/studio/any-id"):
            page = client.get(url).text
            assert f"/static/theme.css?v={app.state.version}" in page, url
            assert 'class="app-nav"' in page, url
            for label in ("Queue", "Studio", "Styles", "Help"):
                assert f">{label}</a>" in page, (url, label)
            assets = set(re.findall(r'(?:src|href)="(/static/[^"?]+)', page))
            for asset in assets:
                assert client.get(asset).status_code == 200, (url, asset)

    def test_web_files_stay_under_500_lines(self):
        web = STATIC_DIR.parent
        for path in list(web.glob("*.py")) + list(STATIC_DIR.glob("*.js")) + list(STATIC_DIR.glob("*.css")):
            assert len(path.read_text(encoding="utf-8").splitlines()) < 500, path.name


def test_transcript_strip_prefers_the_source_language_srt(tmp_path):
    """A translate job writes <stem>.srt and <stem>.en.srt; the strip must
    show the source-language cards, not the English ones that sort first."""
    from ash_captions.web.routes_studio import _transcript_srt

    (tmp_path / "clip.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    (tmp_path / "clip.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHola\n", encoding="utf-8")
    assert _transcript_srt(tmp_path).name == "clip.srt"
    (tmp_path / "clip.srt").unlink()
    assert _transcript_srt(tmp_path).name == "clip.en.srt"
    assert _transcript_srt(tmp_path / "missing") is None
