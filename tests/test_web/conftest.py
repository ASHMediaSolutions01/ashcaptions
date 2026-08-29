from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ash_captions.web.app import create_app

from .fakes import FakeJobQueue, FakeLanguageCatalogue, FakePreviewRenderer, FakeStyleProvider


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
def app(fake_queue, fake_catalogue, fake_style_provider, fake_preview_renderer, tmp_path):
    return create_app(
        fake_queue,
        fake_catalogue,
        style_provider=fake_style_provider,
        preview_renderer=fake_preview_renderer,
        incoming_dir=tmp_path / "uploads",
    )


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)
