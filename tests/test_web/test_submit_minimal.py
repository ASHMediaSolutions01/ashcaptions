"""The minimal submit (Queue reshape, 2026-09-23): file, language and
client are all a job needs. The look is optional and falls back to the
app's default; Browse... can hand back several files; the Studio's
burn body carries the per-burn choices.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from ash_captions.web.models import JobStatus

from .conftest import LOCAL_BASE_URL
from .fakes import FakeFilePicker
from .test_jobs import VIDEO_BYTES, _submit


def _by_path(client, tmp_path, **body):
    video = tmp_path / "clip.mp4"
    video.write_bytes(VIDEO_BYTES)
    payload = {"path": str(video), "language": "en"}
    payload.update(body)
    return client.post("/api/jobs/by-path", json=payload)


class TestTheLookIsOptional:
    def test_by_path_without_a_look_takes_the_app_default(self, client, tmp_path):
        client.app.state.default_preset = "CLEAN"
        res = _by_path(client, tmp_path)
        assert res.status_code == 201, res.text
        assert res.json()["options"]["preset"] == "CLEAN"
        assert res.json()["options"]["burn_in"] is False

    def test_an_upload_without_a_look_takes_the_app_default(self, client):
        client.app.state.default_preset = "CLEAN"
        res = _submit(client, data={"preset": ""})
        assert res.status_code == 201, res.text
        assert res.json()["options"]["preset"] == "CLEAN"

    def test_no_default_configured_means_the_first_style(self, client, tmp_path, fake_style_provider):
        client.app.state.default_preset = None
        first = fake_style_provider.list_styles()[0].name
        res = _by_path(client, tmp_path)
        assert res.status_code == 201, res.text
        assert res.json()["options"]["preset"] == first

    def test_a_default_that_no_longer_exists_falls_back_rather_than_failing(self, client, tmp_path, fake_style_provider):
        client.app.state.default_preset = "GONE"
        res = _by_path(client, tmp_path)
        assert res.status_code == 201, res.text
        assert res.json()["options"]["preset"] == fake_style_provider.list_styles()[0].name

    def test_a_chosen_look_still_wins(self, client, tmp_path):
        client.app.state.default_preset = "CLEAN"
        res = _by_path(client, tmp_path, preset="POP")
        assert res.json()["options"]["preset"] == "POP"

    def test_an_unknown_chosen_look_is_still_refused(self, client, tmp_path):
        assert _by_path(client, tmp_path, preset="FANCY").status_code == 400


class TestPickFiles:
    def test_several_paths_come_back_in_order(self, client, fake_file_picker):
        fake_file_picker.results = [r"D:\a\one.mp4", r"D:\a\two.mov"]
        res = client.post("/api/pick-files")
        assert res.status_code == 200
        assert res.json() == {"paths": [r"D:\a\one.mp4", r"D:\a\two.mov"]}

    def test_cancelled_is_an_empty_list(self, client, fake_file_picker):
        fake_file_picker.results = []
        assert client.post("/api/pick-files").json() == {"paths": []}

    def test_a_picker_that_only_knows_one_file_still_answers(self, client):
        class OldPicker:
            def pick_video(self):
                return r"D:\one.mp4"

        client.app.state.file_picker = OldPicker()
        assert client.post("/api/pick-files").json() == {"paths": [r"D:\one.mp4"]}

    def test_busy_dialog_is_409(self, client):
        client.app.state.file_picker = FakeFilePicker(busy=True)
        assert client.post("/api/pick-files").status_code == 409

    def test_requires_the_client_header(self, app):
        foreign = TestClient(app, base_url=LOCAL_BASE_URL)
        assert foreign.post("/api/pick-files").status_code == 403


class TestBurnFlagsInTheStudio:
    def test_the_page_has_both_toggles_and_loads_the_module_before_studio_js(self, client):
        page = client.get("/studio/x").text
        assert 'id="reframe-btn" aria-pressed="false" hidden' in page
        assert 'id="behind-btn" aria-pressed="false" hidden' in page
        assert page.index("studio_burn_flags.js") < page.index("/static/studio.js")

    def test_studio_js_sends_the_flags_with_the_burn(self, client):
        source = client.get("/static/studio.js").text
        assert "...burnFlags()" in source
        assert "reframeOn" not in source  # moved to studio_burn_flags.js

    def test_the_module_reads_both_buttons(self, client):
        source = client.get("/static/studio_burn_flags.js").text
        assert 'getElementById("behind-btn")' in source
        assert "behind_speaker: on(behindBtn)" in source
        assert "behindBtn.hidden = !live" in source

    def test_behind_speaker_reaches_the_burn_job(self, client, fake_queue):
        job = _submit(client).json()
        fake_queue.force_status(job["id"], JobStatus.DONE)
        res = client.post(f"/api/jobs/{job['id']}/burn", json={"preset": "POP", "behind_speaker": True, "reframe": False})
        assert res.status_code == 201, res.text
