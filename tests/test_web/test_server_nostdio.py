"""The web server must start in the windowed build, where sys.stdout and
sys.stderr are None. uvicorn's default logging config probes
sys.stdout.isatty() and killed the server thread in 0.4.1."""
from __future__ import annotations

import sys

import pytest
from fastapi import FastAPI

from ash_captions.web.app import server_config


def test_uvicorn_default_config_is_the_trap(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    # logging.config wraps the AttributeError from sys.stdout.isatty() in a ValueError.
    with pytest.raises((ValueError, AttributeError)):
        uvicorn.Config(FastAPI(), host="127.0.0.1", port=1)


def test_server_config_builds_without_stdout_and_logs_to_the_root_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    config = server_config(FastAPI(), port=1)
    assert config.log_config is None
    assert config.access_log is False
    assert config.host == "127.0.0.1"
