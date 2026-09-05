"""v0.6 track D, task 2: wires the Node test for look_card_ass.js's sample
.ass builder (tests/test_web/js/look_card_ass.test.js) into `pytest tests
-q`, so it's exercised the same way as everything else in this suite. No
JS framework: Node 24 ships its own test runner (`node --test`).

Skips (rather than fails) when `node` isn't on PATH -- this repo's real
dev environment has Node 24 (scripts/guide_screenshots.py's Playwright
tooling already assumes a JS-capable machine), but a stripped-down CI
image might not, and a missing interpreter is an environment fact, not a
regression in this code."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

JS_TEST = Path(__file__).parent / "js" / "look_card_ass.test.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")


def test_look_card_ass_js_suite_passes():
    result = subprocess.run(
        ["node", "--test", str(JS_TEST)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
    assert result.returncode == 0, "node --test look_card_ass.test.js failed -- see captured output above"
