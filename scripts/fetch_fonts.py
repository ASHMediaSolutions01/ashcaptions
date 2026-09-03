"""Download the bundled caption fonts (and their licence texts).

    .venv/Scripts/python.exe scripts/fetch_fonts.py

Same as ``python -m ash_captions.styles.fonts download``, without the Python
RuntimeWarning that ``-m`` on a submodule prints (the package imports the
module before runpy executes it as ``__main__``). The guide used to tell
editors to ignore that warning; better not to show it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ash_captions.styles.fonts import assets_fonts_dir, download_fonts  # noqa: E402


def main() -> int:
    paths = download_fonts()
    print(f"wrote {len(paths)} font file(s) to {assets_fonts_dir()}")
    return 0 if paths else 1


if __name__ == "__main__":
    raise SystemExit(main())
