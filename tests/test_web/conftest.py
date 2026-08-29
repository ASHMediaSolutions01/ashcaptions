from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ash_captions.web.app import create_app

from .fakes import (
    FakeJobQueue,
    FakeLanguageCatalogue,
    FakePreviewRenderer,
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
def app(
    fake_queue,
    fake_catalogue,
    fake_style_provider,
    fake_preview_renderer,
    fake_update_applier,
    fake_update_state,
    tmp_path,
):
    built = create_app(
        fake_queue,
        fake_catalogue,
        style_provider=fake_style_provider,
        preview_renderer=fake_preview_renderer,
        update_applier=fake_update_applier,
        incoming_dir=tmp_path / "uploads",
    )
    # Mirrors production exactly: app/__main__.py sets app.state.update_state
    # itself, after create_app() returns, rather than through it -- see
    # create_app()'s own docstring on why this isn't a constructor param.
    built.state.update_state = fake_update_state
    return built


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)
