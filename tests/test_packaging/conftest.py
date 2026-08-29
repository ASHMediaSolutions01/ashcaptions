"""Shared fixtures for the packaging test suite.

Puts `scripts/` on `sys.path` so tests can `import build`, `import release`,
`import fetch_ffmpeg`, `import fetch_model` and `from pkgtools import ...`
the same way those scripts import each other -- without turning `scripts/`
into an installed package.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
