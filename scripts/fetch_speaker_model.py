"""Fetch the speaker-embedding model so speaker labels work offline.

    .venv/Scripts/python.exe scripts/fetch_speaker_model.py [--dest build/models]

The 26 MB file lands beside the matting model in the directory build.py
bundles as ``models/``, so one ``--model-dir`` still covers everything.
The engine downloads it on first use when it is missing, which is fine
for a source checkout with internet and no use at all to an installed
bundle on an editor's PC -- hence this pre-seed step.

Licence: WeSpeaker is Apache-2.0 (github.com/wenet-e2e/wespeaker), and
the voxceleb-resnet34-LM weights are redistributed unmodified from
huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM. The notice
belongs in NOTICES.md alongside RVM's.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ash_captions.engine.diarise import DiarisationError  # noqa: E402
from ash_captions.engine.diarise_run import (  # noqa: E402
    SPEAKER_MODEL_URL,
    ensure_speaker_model,
)

DEFAULT_DEST = REPO_ROOT / "build" / "models"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", type=Path, default=DEFAULT_DEST, help="Directory to place the .onnx in."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"Fetching {SPEAKER_MODEL_URL}")
    try:
        path = ensure_speaker_model(args.dest, download=True)
    except DiarisationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Speaker model ready: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
