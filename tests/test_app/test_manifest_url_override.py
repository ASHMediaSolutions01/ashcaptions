"""ASH_CAPTIONS_MANIFEST_URL points the update check at a test manifest."""
import pytest

from ash_captions.app.__main__ import MANIFEST_URL_ENV, _manifest_url
from ash_captions.app.updater import MANIFEST_URL


def test_defaults_to_the_published_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(MANIFEST_URL_ENV, raising=False)
    assert _manifest_url() == MANIFEST_URL
    monkeypatch.setenv(MANIFEST_URL_ENV, "   ")
    assert _manifest_url() == MANIFEST_URL


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MANIFEST_URL_ENV, "file:///C:/tmp/manifest.json")
    assert _manifest_url() == "file:///C:/tmp/manifest.json"
