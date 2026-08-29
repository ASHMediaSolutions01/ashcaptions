from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ash_captions.web.app import create_app

from .fakes import FakeJobQueue, FakeLanguageCatalogue


@pytest.fixture
def fake_queue() -> FakeJobQueue:
    return FakeJobQueue()


@pytest.fixture
def fake_catalogue() -> FakeLanguageCatalogue:
    return FakeLanguageCatalogue()


@pytest.fixture
def app(fake_queue, fake_catalogue, tmp_path):
    return create_app(fake_queue, fake_catalogue, incoming_dir=tmp_path / "uploads")


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)
