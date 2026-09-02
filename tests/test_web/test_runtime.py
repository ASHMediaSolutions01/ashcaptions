"""web/runtime.py: the "may this process apply an update" gate."""
from __future__ import annotations

import sys

from ash_captions.web import runtime


def test_source_checkout_is_never_update_capable(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert runtime.updates_supported() is False


def test_frozen_build_outside_git_is_update_capable(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime, "app_root", lambda: tmp_path)
    assert runtime.updates_supported() is True


def test_frozen_build_inside_a_git_checkout_is_refused(monkeypatch, tmp_path):
    """A bundle that somehow sits inside a repository must not be mirrored
    over either -- robocopy /MIR would delete .git and everything else the
    bundle doesn't contain."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(runtime, "app_root", lambda: tmp_path)
    assert runtime.updates_supported() is False


def test_app_version_falls_back_to_dev_when_metadata_is_missing(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    def _missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(runtime, "version", _missing)
    assert runtime.app_version() == "dev"


def test_app_version_reads_package_metadata(monkeypatch):
    monkeypatch.setattr(runtime, "version", lambda name: "3.2.1")
    assert runtime.app_version() == "3.2.1"


def test_guide_page_is_served_with_its_images(client):
    """The editor's guide lives inside the app at /guide, with the asset
    cache-buster stamped in and every screenshot it references reachable."""
    import re

    page = client.get("/guide")
    assert page.status_code == 200
    assert "Editor's guide" in page.text
    assert "__VERSION__" not in page.text
    images = set(re.findall(r'src="(/static/guide/[^"]+)"', page.text))
    assert images, "the guide should reference its screenshots"
    for src in images:
        assert client.get(src).status_code == 200, src
