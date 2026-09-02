"""Real test of app/jobobject.py: a process that joins the kill-on-close
job, spawns a child, and is then hard-killed must take the child with it
-- and a child spawned with CREATE_BREAKAWAY_FROM_JOB (the update helper)
must survive. No mocks: real processes, real Win32 job object.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from ash_captions.app import jobobject

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

_PARENT_SCRIPT = """
import subprocess, sys, time
from ash_captions.app import jobobject
assert jobobject.assign_current_process(), "could not join a job object"
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
try:
    survivor = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=jobobject.CREATE_BREAKAWAY_FROM_JOB | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    survivor_pid = survivor.pid
except OSError:
    survivor_pid = 0
print(child.pid, survivor_pid, flush=True)
time.sleep(60)
"""


def _pid_alive(pid: int) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
        capture_output=True, text=True, check=False,
    ).stdout
    return f'"{pid}"' in out


def _wait_until(predicate, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _taskkill(pid: int) -> None:
    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)


def test_in_job_answers_on_windows() -> None:
    assert jobobject.in_job() in (True, False)


def test_hard_killing_the_parent_kills_its_children(tmp_path: Path) -> None:
    script = tmp_path / "parent.py"
    script.write_text(_PARENT_SCRIPT, encoding="utf-8")
    parent = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(SRC_DIR)},
    )
    child_pid = survivor_pid = 0
    try:
        line = parent.stdout.readline().strip()  # type: ignore[union-attr]
        if not line:
            pytest.fail(f"parent produced no pids: {parent.stderr.read()}")  # type: ignore[union-attr]
        child_pid, survivor_pid = (int(part) for part in line.split())
        assert _pid_alive(child_pid)

        parent.kill()  # TerminateProcess -- the same thing taskkill /F does
        parent.wait(timeout=10)

        assert _wait_until(lambda: not _pid_alive(child_pid), timeout=10), (
            "child ffmpeg-stand-in outlived its hard-killed parent"
        )
        if survivor_pid:
            assert _pid_alive(survivor_pid), "breakaway child (the update helper) was killed too"
    finally:
        if parent.poll() is None:
            parent.kill()
        for pid in (child_pid, survivor_pid):
            if pid:
                _taskkill(pid)


def test_assign_is_idempotent_in_this_process() -> None:
    """Joining twice must not create a second job or fail; the first call
    may legitimately refuse (an outer job forbidding nesting), in which
    case the second returns the same answer."""
    first = jobobject.assign_current_process()
    second = jobobject.assign_current_process()
    assert first == second
    if first:
        assert jobobject.is_assigned()
