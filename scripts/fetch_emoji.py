"""Fetch the emoji artwork used by emoji bursts, so the bundle ships it.

    .venv/Scripts/python.exe scripts/fetch_emoji.py [--dest assets/emoji]

Emoji are the one caption treatment libass cannot draw -- ASS has no
colour glyphs -- so a burst is an image composited over the burned frame.
That means shipping pictures, and pictures carry a licence.

OPENMOJI, AND THE LICENCE DECISION THAT COMES WITH IT
The artwork is OpenMoji (openmoji.org), CC BY-SA 4.0. It is redistributed
*unmodified*, the same posture the bundle already takes with Robust Video
Matting's GPL-3.0 weights, and the attribution belongs in NOTICES.md.
ShareAlike binds adaptations of the artwork, not the program that
displays it, so it does not reach ASH Captions' own code -- but it is a
licence choice on a client product and Ghazi should confirm it rather
than inherit it from a script.

The alternative considered was Twemoji (CC-BY 4.0, a simpler licence),
rejected on a measurement: it ships PNGs at 72x72 only, and a burst on a
1080-wide reel is about 130px, so every sticker would be a 1.8x upscale.
OpenMoji ships 618x618, which is a downscale at every size we draw.
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "assets" / "emoji"

BASE_URL = "https://cdn.jsdelivr.net/gh/hfg-gmuend/openmoji@master/color/618x618/"

# A small, deliberately boring set: the ones short-form editors actually
# reach for. Names are what the style editor shows and what a look
# stores, so they are stable even if the artwork is ever replaced.
EMOJI = {
    "fire": "1F525",
    "star": "2B50",
    "heart": "2764",
    "clap": "1F44F",
    "hundred": "1F4AF",
    "thinking": "1F914",
    "eyes": "1F440",
    "point-down": "1F447",
    "laugh": "1F602",
    "rocket": "1F680",
    "money": "1F4B0",
    "warning": "26A0",
}

# The real files are 8-40 KB; anything tiny is an error page, the failure
# the model fetchers already learned to catch.
MIN_BYTES = 2_000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    return parser.parse_args(argv)


def fetch_one(name: str, code: str, dest: Path, timeout: float = 60) -> Path:
    path = dest / f"{name}.png"
    if path.is_file() and path.stat().st_size >= MIN_BYTES:
        return path
    url = BASE_URL + code + ".png"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"could not fetch {name} ({url}): {exc}") from exc
    if len(data) < MIN_BYTES:
        raise RuntimeError(f"{name} came back {len(data)} bytes -- not a PNG")
    path.write_bytes(data)
    return path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, code in sorted(EMOJI.items()):
        try:
            written.append(fetch_one(name, code, dest))
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    total = sum(p.stat().st_size for p in written)
    print(f"{len(written)} emoji in {dest} ({total / 1024:.0f} KB)")
    print("Attribution (CC BY-SA 4.0, openmoji.org) belongs in NOTICES.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
