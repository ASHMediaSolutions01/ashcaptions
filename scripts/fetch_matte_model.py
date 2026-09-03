"""Fetch the person-matting model (Robust Video Matting, MobileNetV3, ONNX)
so the bundle ships it and "captions behind the speaker" works offline.

    .venv/Scripts/python.exe scripts/fetch_matte_model.py [--dest build/models]

The 15 MB file lands in the same directory build.py bundles as ``models/``
(the Whisper cache root), so one ``--model-dir`` covers both. The engine
also downloads it on first use when it is missing, which is fine for a
source checkout with internet but not for an installed bundle on an
editor's PC, hence this pre-seed step.

Licence: RVM is released under GPL-3.0 (github.com/PeterL1n/RobustVideoMatting);
the model file is redistributed unmodified and the notice lives in NOTICES.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ash_captions.engine.matte import MATTE_MODEL_URL, MatteError, ensure_matte_model  # noqa: E402

DEFAULT_DEST = REPO_ROOT / "build" / "models"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="Directory to place the .onnx in.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Fetching {MATTE_MODEL_URL}")
    try:
        path = ensure_matte_model(args.dest, download=True)
    except MatteError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Matting model ready: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
