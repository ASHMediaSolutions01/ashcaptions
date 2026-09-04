from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ash_captions.web.app import create_app

from .fakes import (
    FakeFilePicker,
    FakeGlossaryProvider,
    FakeJobQueue,
    FakeLanguageCatalogue,
    FakePreviewRenderer,
    FakeRevealer,
    FakeStyleProvider,
    FakeUpdateApplier,
    FakeUpdateState,
)


@pytest.fixture
def fake_queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def fake_catalogue() -> FakeLanguageCatalogue:
    return FakeLanguageCatalogue()


@pytest.fixture
def fake_style_provider() -> FakeStyleProvider:
    return FakeStyleProvider()


@pytest.fixture
def fake_preview_renderer(fake_style_provider) -> FakePreviewRenderer:
    return FakePreviewRenderer(style_provider=fake_style_provider)


@pytest.fixture
def fake_update_applier() -> FakeUpdateApplier:
    return FakeUpdateApplier()


@pytest.fixture
def fake_update_state() -> FakeUpdateState:
    # No update available by default -- individual tests call .set(...).
    return FakeUpdateState()


@pytest.fixture
def fake_glossary_provider() -> FakeGlossaryProvider:
    return FakeGlossaryProvider()


@pytest.fixture
def fake_file_picker() -> FakeFilePicker:
    return FakeFilePicker()


@pytest.fixture
def fake_revealer() -> FakeRevealer:
    return FakeRevealer()


@pytest.fixture
def sse_poll_interval() -> float:
    """Override in a test module to speed up SSE heartbeat tests."""
    return 1.0


@pytest.fixture
def updates_supported():
    """Default: behave like an installed build so the update tests can
    exercise the banner/apply flow. `test_updates.py` overrides this to
    prove the source-checkout gate."""
    return lambda: True


@pytest.fixture
def app(
    fake_queue,
    fake_catalogue,
    fake_style_provider,
    fake_preview_renderer,
    fake_update_applier,
    fake_update_state,
    fake_glossary_provider,
    fake_file_picker,
    fake_revealer,
    sse_poll_interval,
    updates_supported,
    tmp_path,
):
    built = create_app(
        fake_queue,
        fake_catalogue,
        style_provider=fake_style_provider,
        preview_renderer=fake_preview_renderer,
        update_applier=fake_update_applier,
        glossary_provider=fake_glossary_provider,
        file_picker=fake_file_picker,
        revealer=fake_revealer,
        incoming_dir=tmp_path / "uploads",
        sse_poll_interval=sse_poll_interval,
        updates_supported=updates_supported,
    )
    # Mirrors production exactly: app/__main__.py sets app.state.update_state
    # itself, after create_app() returns, rather than through it -- see
    # create_app()'s own docstring on why this isn't a constructor param.
    built.state.update_state = fake_update_state
    return built


# What the app's own pages send: a loopback Host and the client header
# every mutating request must carry (see web/security.py). A client built
# without these -- see test_security.py -- is a foreign page.
LOCAL_BASE_URL = "http://127.0.0.1:8756"
CLIENT_HEADERS = {"X-ASH-Client": "1"}


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, base_url=LOCAL_BASE_URL, headers=CLIENT_HEADERS)
