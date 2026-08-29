"""Tests for the style editor's API surface (spec 7A): GET/PUT/DELETE
/api/styles, GET /api/fonts, and the POST/GET preview job flow. All
against fakes -- no ffmpeg, no whisper, no real `ash_captions.styles`
rendering, no network (see fakes.py)."""
from __future__ import annotations

from ash_captions.web.models import PreviewStatus

from .fakes import default_style_definition


def test_list_styles_distinguishes_shipped_from_user(client, fake_style_provider):
    fake_style_provider.save_style("MY LOOK", default_style_definition("MY LOOK"))

    res = client.get("/api/styles")
    assert res.status_code == 200
    by_name = {s["name"]: s for s in res.json()}
    assert by_name["CLEAN"]["shipped"] is True
    assert by_name["POP"]["shipped"] is True
    assert by_name["MY LOOK"]["shipped"] is False


def test_shadowed_shipped_style_is_flagged_customized_locally(client, fake_style_provider):
    """Saving a user style under a shipped name (e.g. "POP") silently
    overrides it for every job that uses that name -- this must be visible,
    not indistinguishable from the pristine built-in (see team-lead's
    finding: `shipped: True` alone hides this)."""
    untouched = client.get("/api/styles/CLEAN").json()
    assert untouched["shipped"] is True
    assert untouched["customized_locally"] is False

    overridden = default_style_definition("POP")
    overridden["font"] = "Montserrat"
    fake_style_provider.save_style("POP", overridden)

    res = client.get("/api/styles/POP")
    assert res.json()["shipped"] is True
    assert res.json()["customized_locally"] is True
    assert res.json()["definition"]["font"] == "Montserrat"

    by_name = {s["name"]: s for s in client.get("/api/styles").json()}
    assert by_name["POP"]["customized_locally"] is True
    assert by_name["CLEAN"]["customized_locally"] is False


def test_customized_locally_never_true_for_a_pure_user_style(client, fake_style_provider):
    fake_style_provider.save_style("MY LOOK", default_style_definition("MY LOOK"))

    res = client.get("/api/styles/MY LOOK")

    assert res.json()["shipped"] is False
    assert res.json()["customized_locally"] is False


def test_shipped_only_view_of_a_shadowed_style_is_never_flagged_customized(client, fake_style_provider):
    """`?shipped_only=true` -- the "reset to shipped" fetch -- returns the
    pristine version, so it must never claim to be customized."""
    overridden = default_style_definition("POP")
    overridden["font"] = "Montserrat"
    fake_style_provider.save_style("POP", overridden)

    res = client.get("/api/styles/POP", params={"shipped_only": True})

    assert res.json()["customized_locally"] is False
    assert res.json()["definition"]["font"] != "Montserrat"


def test_get_style_returns_full_definition(client):
    res = client.get("/api/styles/CLEAN")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "CLEAN"
    assert body["shipped"] is True
    assert body["definition"]["colors"]["text"] == "#FFFFFF"


def test_get_unknown_style_is_404(client):
    res = client.get("/api/styles/DOES-NOT-EXIST")
    assert res.status_code == 404


def test_get_style_shipped_only_bypasses_user_override(client, fake_style_provider):
    fake_style_provider.save_style("CLEAN", default_style_definition("CLEAN"))
    fake_style_provider._user["CLEAN"]["font"] = "Montserrat"

    overridden = client.get("/api/styles/CLEAN")
    assert overridden.json()["definition"]["font"] == "Montserrat"

    shipped = client.get("/api/styles/CLEAN", params={"shipped_only": True})
    assert shipped.json()["definition"]["font"] == "Inter"


def test_save_style_creates_a_user_style(client):
    definition = default_style_definition("MY LOOK")
    definition["size"] = 90

    res = client.put("/api/styles/MY LOOK", json=definition)

    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "MY LOOK"
    assert body["shipped"] is False
    assert body["definition"]["size"] == 90


def test_save_style_url_name_is_authoritative(client):
    """The URL path segment is the identity even if the body's "name"
    field says something else -- consistent PUT-by-key semantics."""
    definition = default_style_definition("SOMETHING ELSE")

    res = client.put("/api/styles/MY LOOK", json=definition)

    assert res.status_code == 200
    assert res.json()["name"] == "MY LOOK"
    assert client.get("/api/styles/MY LOOK").status_code == 200
    assert client.get("/api/styles/SOMETHING ELSE").status_code == 404


def test_save_invalid_style_returns_400_with_field_named_message(client):
    definition = default_style_definition("BAD FONT")
    definition["font"] = "Comic Sans MS"

    res = client.put("/api/styles/BAD FONT", json=definition)

    assert res.status_code == 400
    assert "font" in res.json()["detail"]
    assert "Comic Sans MS" in res.json()["detail"]


def test_save_invalid_style_never_500s(client):
    definition = default_style_definition("BAD EFFECT")
    definition["active_word"] = {"effect": "explode", "scale": 1.0, "box": False}

    res = client.put("/api/styles/BAD EFFECT", json=definition)

    assert res.status_code == 400
    assert res.status_code != 500


def test_delete_shipped_style_is_refused(client):
    res = client.delete("/api/styles/CLEAN")

    assert res.status_code == 409
    # the built-in must still be there
    assert client.get("/api/styles/CLEAN").status_code == 200


def test_delete_user_style_succeeds(client, fake_style_provider):
    fake_style_provider.save_style("MY LOOK", default_style_definition("MY LOOK"))

    res = client.delete("/api/styles/MY LOOK")

    assert res.status_code == 204
    assert client.get("/api/styles/MY LOOK").status_code == 404


def test_delete_unknown_style_is_404(client):
    res = client.delete("/api/styles/NOPE")
    assert res.status_code == 404


def test_list_fonts(client):
    res = client.get("/api/fonts")
    assert res.status_code == 200
    fonts = res.json()
    assert "Inter" in fonts
    assert isinstance(fonts, list)


def test_style_editor_page_is_served(client):
    res = client.get("/style-editor")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


# --- Preview rendering (spec 7A.3) ------------------------------------------


def test_submit_preview_returns_a_job_handle(client, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not a real video, just needs to exist")

    res = client.post(
        "/api/styles/preview",
        json={"video_path": str(video), "start_seconds": 4.0, "style": default_style_definition("POP")},
    )

    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "pending"
    assert body["id"]


def test_submit_preview_rejects_missing_video(client):
    res = client.post(
        "/api/styles/preview",
        json={"video_path": r"D:\nope\missing.mp4", "start_seconds": 0.0, "style": default_style_definition("POP")},
    )
    assert res.status_code == 400


def test_submit_preview_rejects_invalid_style(client, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stub")
    definition = default_style_definition("BAD")
    definition["font"] = "Comic Sans MS"

    res = client.post(
        "/api/styles/preview",
        json={"video_path": str(video), "start_seconds": 0.0, "style": definition},
    )

    assert res.status_code == 400
    assert "font" in res.json()["detail"]


def test_poll_preview_job_status(client, tmp_path, fake_preview_renderer):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stub")

    submitted = client.post(
        "/api/styles/preview",
        json={"video_path": str(video), "start_seconds": 0.0, "style": default_style_definition("POP")},
    ).json()
    job_id = submitted["id"]

    pending = client.get(f"/api/styles/preview/{job_id}")
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    fake_preview_renderer.force_status(job_id, PreviewStatus.RUNNING)
    assert client.get(f"/api/styles/preview/{job_id}").json()["status"] == "running"

    fake_preview_renderer.force_status(job_id, PreviewStatus.FAILED, error="ffmpeg exploded")
    failed = client.get(f"/api/styles/preview/{job_id}")
    assert failed.json()["status"] == "failed"
    assert failed.json()["error"] == "ffmpeg exploded"


def test_get_unknown_preview_job_is_404(client):
    res = client.get("/api/styles/preview/does-not-exist")
    assert res.status_code == 404


def test_preview_clip_available_once_done(client, tmp_path, fake_preview_renderer):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"stub")
    clip = tmp_path / "preview.mp4"
    clip.write_bytes(b"fake rendered clip bytes")

    submitted = client.post(
        "/api/styles/preview",
        json={"video_path": str(video), "start_seconds": 0.0, "style": default_style_definition("POP")},
    ).json()
    job_id = submitted["id"]

    not_ready = client.get(f"/api/styles/preview/{job_id}/clip")
    assert not_ready.status_code == 409

    fake_preview_renderer.force_status(job_id, PreviewStatus.DONE, clip_path=str(clip))

    ready = client.get(f"/api/styles/preview/{job_id}/clip")
    assert ready.status_code == 200
    assert ready.content == b"fake rendered clip bytes"
