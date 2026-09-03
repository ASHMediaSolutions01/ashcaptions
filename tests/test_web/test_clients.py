"""Per-client glossaries at the API: the `client` field on job submission
(sanitized at the boundary), GET /api/clients, and the glossary read/write
routes -- against the fakes, plus the real file-backed adapter over a temp
folder for the atomic-write and line-error behaviour."""
from __future__ import annotations

import json

import pytest

from ash_captions.web.validation import MAX_CLIENT_NAME_LENGTH, sanitize_client_name

from .conftest import LOCAL_BASE_URL


def _submit(http, path, **overrides):
    body = {"path": str(path), "language": "en", "dialect": "en-US", "preset": "POP"}
    body.update(overrides)
    return http.post("/api/jobs/by-path", content=json.dumps(body), headers={"Content-Type": "application/json"})


@pytest.fixture
def video(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    return clip


# --- the client field on jobs ------------------------------------------------


class TestClientOnJobs:
    def test_client_is_stored_and_returned_on_the_job(self, client, video, fake_queue):
        res = _submit(client, video, client="  Acme Corp ")

        assert res.status_code == 201
        assert res.json()["options"]["client"] == "Acme Corp"
        assert fake_queue.get_job(res.json()["id"]).options.client == "Acme Corp"

    def test_no_client_is_none_not_empty_string(self, client, video):
        assert _submit(client, video).json()["options"]["client"] is None
        assert _submit(client, video, client="   ").json()["options"]["client"] is None

    @pytest.mark.parametrize(
        "bad",
        ["../x", "CON", "con.txt", "a" * (MAX_CLIENT_NAME_LENGTH + 1), "acme/clip", "acme\\clip", ".hidden", "..", "Acme."],
    )
    def test_unsafe_client_names_are_400(self, client, video, bad):
        res = _submit(client, video, client=bad)

        assert res.status_code == 400, bad
        assert "lient" in res.json()["detail"]

    def test_upload_route_takes_the_client_too(self, client, fake_queue):
        res = client.post(
            "/api/jobs",
            files={"file": ("clip.mp4", b"bytes", "video/mp4")},
            data={"language": "en", "preset": "POP", "client": "Acme"},
        )
        assert res.status_code == 201
        assert res.json()["options"]["client"] == "Acme"

    def test_upload_route_rejects_a_bad_client(self, client):
        res = client.post(
            "/api/jobs",
            files={"file": ("clip.mp4", b"bytes", "video/mp4")},
            data={"language": "en", "preset": "POP", "client": "../x"},
        )
        assert res.status_code == 400


def test_sanitize_client_name_rules():
    assert sanitize_client_name(None) is None
    assert sanitize_client_name("") is None
    assert sanitize_client_name(" Acme Corp ") == "Acme Corp"
    assert sanitize_client_name("a" * MAX_CLIENT_NAME_LENGTH) == "a" * MAX_CLIENT_NAME_LENGTH
    for bad in ("../x", "CON", "Nul", "COM1", "LPT9", "a" * 61, "acme/x", ".git", "Acme.", "x\0y", "Acme?"):
        with pytest.raises(ValueError):
            sanitize_client_name(bad)


# --- GET /api/clients ----------------------------------------------------------


class TestListClients:
    def test_merges_glossary_files_and_recent_jobs(self, client, video, fake_glossary_provider, fake_queue):
        fake_glossary_provider.files["acme"] = "Gazi => Ghazi\n"
        fake_glossary_provider.files["globex"] = ""
        fake_glossary_provider.files["glossary"] = "shared"  # the fake lists whatever it holds
        _submit(client, video, client="Acme")  # the job's spelling wins over the file slug
        video2 = video.with_name("second.mp4")
        video2.write_bytes(b"v")
        _submit(client, video2, client="Initech")

        names = client.get("/api/clients").json()

        assert names[:2] == ["Initech", "Acme"]  # jobs first, newest first
        assert "globex" in names and "acme" not in names
        assert "glossary" in names  # the fake doesn't filter; the real adapter does (below)

    def test_queue_without_known_clients_still_lists_files(self, client, fake_glossary_provider, app):
        class Minimal:
            def list_jobs(self):
                return []

        app.state.queue = Minimal()
        fake_glossary_provider.files["acme"] = ""

        assert client.get("/api/clients").json() == ["acme"]


# --- the glossary routes --------------------------------------------------------


class TestGlossaryRoutes:
    def test_get_is_empty_for_a_client_without_a_file(self, client):
        res = client.get("/api/clients/Acme/glossary")

        assert res.status_code == 200
        assert res.json() == {"client": "Acme", "slug": "acme", "text": ""}

    def test_put_writes_and_get_reads_back(self, client, fake_glossary_provider):
        res = client.put("/api/clients/Acme Corp/glossary", json={"text": "Gazi => Ghazi\nAcme Corp\n"})

        assert res.status_code == 200
        assert res.json()["slug"] == "acme-corp"
        assert res.json()["text"] == "Gazi => Ghazi\nAcme Corp\n"
        assert fake_glossary_provider.files["acme-corp"] == "Gazi => Ghazi\nAcme Corp\n"
        assert client.get("/api/clients/acme-corp/glossary").json()["text"] == "Gazi => Ghazi\nAcme Corp\n"

    def test_put_with_a_broken_line_is_400_naming_the_line(self, client, fake_glossary_provider):
        res = client.put("/api/clients/Acme/glossary", json={"text": "ok => fine\nwrong =>\n"})

        assert res.status_code == 400
        assert "line 2" in res.json()["detail"]
        assert "acme" not in fake_glossary_provider.files  # nothing written

    def test_bad_client_names_never_become_paths(self, client, fake_glossary_provider):
        for bad in ("CON", "a" * 61, "Acme.", ".hidden"):
            assert client.get(f"/api/clients/{bad}/glossary").status_code == 400, bad
            assert client.put(f"/api/clients/{bad}/glossary", json={"text": "x"}).status_code == 400, bad
        # Dot segments are normalised away before routing (404: no such
        # route) or, percent-encoded, refused by the sanitizer (400).
        # Either way nothing reaches the provider.
        assert client.put("/api/clients/../glossary", json={"text": "x"}).status_code in (400, 404)
        assert client.put("/api/clients/..%2Fx/glossary", json={"text": "x"}).status_code in (400, 404)
        assert client.put("/api/clients/%2E%2E/glossary", json={"text": "x"}).status_code in (400, 404)
        assert fake_glossary_provider.writes == []

    def test_put_needs_the_client_header(self, app):
        from fastapi.testclient import TestClient

        foreign = TestClient(app, base_url=LOCAL_BASE_URL)  # no X-ASH-Client
        res = foreign.put("/api/clients/Acme/glossary", json={"text": "Gazi => Ghazi\n"})

        assert res.status_code == 403


# --- the real file-backed provider ------------------------------------------------


class TestClientGlossaryFiles:
    def test_round_trip_and_atomic_write(self, tmp_path):
        from ash_captions.web.glossary_adapter import ClientGlossaryFiles

        files = ClientGlossaryFiles(tmp_path / "glossaries")
        assert files.read_glossary("Acme") == ""

        files.write_glossary("Acme Corp", "Gazi => Ghazi\r\nAcme Corp")

        path = tmp_path / "glossaries" / "acme-corp.txt"
        assert path.read_text(encoding="utf-8") == "Gazi => Ghazi\nAcme Corp\n"
        assert not path.with_name("acme-corp.txt.part").exists()
        assert files.read_glossary("acme-corp") == "Gazi => Ghazi\nAcme Corp\n"
        assert files.slug_for("Acme Corp") == "acme-corp"

    def test_lists_client_files_but_never_the_shared_one(self, tmp_path):
        from ash_captions.web.glossary_adapter import ClientGlossaryFiles

        directory = tmp_path / "glossaries"
        directory.mkdir()
        (directory / "glossary.txt").write_text("shared\n", encoding="utf-8")
        (directory / "acme.txt").write_text("", encoding="utf-8")
        (directory / "globex.txt").write_text("", encoding="utf-8")
        (directory / "notes.md").write_text("", encoding="utf-8")

        assert ClientGlossaryFiles(directory).list_clients() == ["acme", "globex"]
        assert ClientGlossaryFiles(tmp_path / "missing").list_clients() == []

    def test_refuses_lines_the_parser_would_drop_and_leaves_the_file_alone(self, tmp_path):
        from ash_captions.web.glossary_adapter import ClientGlossaryFiles
        from ash_captions.web.interfaces import GlossaryValidationFailedError

        files = ClientGlossaryFiles(tmp_path)
        files.write_glossary("Acme", "Gazi => Ghazi\n")

        with pytest.raises(GlossaryValidationFailedError) as caught:
            files.write_glossary("Acme", "Gazi => Ghazi\n=> nothing\na => b => c\n")

        assert [p.split(":")[0] for p in caught.value.problems] == ["line 2", "line 3"]
        assert (tmp_path / "acme.txt").read_text(encoding="utf-8") == "Gazi => Ghazi\n"

    def test_real_provider_through_the_routes(self, tmp_path, fake_queue, fake_catalogue):
        """The default wiring in one place: create_app with the real adapter
        over a temp folder, a PUT lands on disk, a bad PUT is a 400."""
        from fastapi.testclient import TestClient

        from ash_captions.web.app import create_app
        from ash_captions.web.glossary_adapter import ClientGlossaryFiles

        app = create_app(fake_queue, fake_catalogue, glossary_provider=ClientGlossaryFiles(tmp_path / "g"))
        http = TestClient(app, base_url=LOCAL_BASE_URL, headers={"X-ASH-Client": "1"})

        assert http.put("/api/clients/Acme/glossary", json={"text": "broker => Brokerage\n"}).status_code == 200
        assert (tmp_path / "g" / "acme.txt").read_text(encoding="utf-8") == "broker => Brokerage\n"
        bad = http.put("/api/clients/Acme/glossary", json={"text": "broker =>\n"})
        assert bad.status_code == 400 and "line 1" in bad.json()["detail"]
        assert http.get("/api/clients").json() == ["acme"]
