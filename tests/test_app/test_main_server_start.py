"""Startup refuses to sit there as a tray icon with no control page: if the
server thread dies or never binds, `_wait_for_port` says so."""
from __future__ import annotations

import socket
import threading

from ash_captions.app.__main__ import _wait_for_port


def test_dead_server_thread_is_reported_immediately() -> None:
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    assert _wait_for_port(1, timeout=5.0, thread=dead) is False


def test_listening_port_is_found() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        alive = threading.Thread(target=lambda: threading.Event().wait(2))
        alive.start()
        try:
            assert _wait_for_port(port, timeout=5.0, thread=alive) is True
        finally:
            alive.join()


def test_unbound_port_times_out() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    alive = threading.Thread(target=lambda: threading.Event().wait(1))
    alive.start()
    try:
        assert _wait_for_port(free_port, timeout=0.5, thread=alive) is False
    finally:
        alive.join()
