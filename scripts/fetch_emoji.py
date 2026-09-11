"""Render the emoji artwork used by emoji bursts, so the bundle ships it.

    .venv/Scripts/python.exe scripts/fetch_emoji.py [--dest assets/emoji]

Emoji are the one caption treatment libass cannot draw -- ASS has no
colour glyphs -- so a burst is an image composited over the burned frame.
That means shipping pictures, and pictures carry a licence.

WHY THIS GOES THROUGH A FONT INSTEAD OF DOWNLOADING PICTURES
The burned .mp4 leaves the building. Whatever artwork is composited into
it travels with it, so its licence follows the client's reel rather than
staying in the repo -- which is why the first version of this script, a
download of OpenMoji PNGs under CC BY-SA 4.0, was the wrong choice. The
SIL Open Font License settles it in one sentence:

    "The requirement for fonts to remain under this license does not
     apply to any document created using the fonts or their derivatives."

A rendered frame is such a document. Rasterising from an OFL font means
the reel carries no obligation at all, and the font itself is a build
input that is never redistributed -- it is fetched here and left in
build/, outside the bundle.

MEASURED BEFORE IT WAS ACCEPTED, not assumed
* Noto's colour glyphs are CBDT bitmaps with a **single** strike, so
  Pillow opens the font at ppem 109 and refuses every other size with
  "invalid pixel size". A glyph comes out about 122px square.
* A sticker draws at 0.20 of the reel's short side -- 216px on a 1080
  reel -- so that is a 1.77x upscale, which is the reason this was
  checked at all. Side by side against OpenMoji's 618px downscaled to the
  same 216px, the upscale is not visible; the mean alpha-edge gradient
  came out *higher* for Noto on all six emoji compared.
* ``Noto-COLRv1.ttf`` is vector and would scale to any size, which would
  have removed the question. Pillow renders it **empty** at every size
  tried: its FreeType binding handles CBDT and sbix colour bitmaps, not
  COLRv1 paint graphs.
* OpenMoji's art filled 0.57-0.89 of its 618px canvas, about 0.86 on the
  longer side for most. ``CANVAS_FILL`` reproduces that margin, because
  ``stickers.SIZE_FRACTION`` was tuned by eye against those files and a
  tight crop would silently make every sticker a seventh larger.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "assets" / "emoji"
DEFAULT_FONT = REPO_ROOT / "build" / "fonts" / "NotoColorEmoji.ttf"

# Pinned to a commit rather than a branch: the artwork is what was looked
# at, and "main" would silently redraw it on a later build.
FONT_COMMIT = "8998f5dd683424a73e2314a8c1f1e359c19e8742"
FONT_URL = (
    "https://raw.githubusercontent.com/googlefonts/noto-emoji/"
    + FONT_COMMIT
    + "/fonts/NotoColorEmoji.ttf"
)
FONT_SHA256 = "72a635cb3d2f3524c51620cdde406b217204e8a6a06c6a096ff8ed4b5fd6e27b"

# The font's only bitmap strike. Anything else raises "invalid pixel size".
PPEM = 109

# Share of the square canvas the art fills, matching what it replaces.
CANVAS_FILL = 0.86

# A glyph this small is a .notdef box or an empty cell, not an emoji.
MIN_GLYPH_PX = 80

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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument(
        "--font",
        type=Path,
        default=DEFAULT_FONT,
        help="where the build input font is cached (never bundled)",
    )
    return parser.parse_args(argv)


def ensure_font(path: Path, *, timeout: float = 120) -> Path:
    """The font, fetched once and checked by hash.

    The hash is the point: a redirect to an error page or a silently
    updated file would otherwise be discovered as redrawn emoji in a
    release, which is the kind of thing nobody looks at twice.
    """
    if path.is_file() and sha256_of(path) == FONT_SHA256:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # noqa S310: the URL is the constant above with a pinned commit;
        # nothing user-supplied reaches it.
        with urllib.request.urlopen(FONT_URL, timeout=timeout) as response:  # noqa: S310
            data = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"could not fetch the emoji font ({FONT_URL}): {exc}") from exc
    got = hashlib.sha256(data).hexdigest()
    if got != FONT_SHA256:
        raise RuntimeError(
            f"the emoji font came back with sha256 {got}, expected {FONT_SHA256}"
        )
    path.write_bytes(data)
    return path


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render(font: ImageFont.FreeTypeFont, codepoints: str) -> Image.Image:
    """One emoji on a square transparent canvas.

    Square matters as much as the drawing does: ``StickerPlan.filtergraph``
    scales every sticker to ``size:size``, so a 126x112 glyph handed over
    untouched would come out stretched.
    """
    text = "".join(chr(int(part, 16)) for part in codepoints.split("-"))
    scratch = Image.new("RGBA", (PPEM * 4, PPEM * 4), (0, 0, 0, 0))
    ImageDraw.Draw(scratch).text(
        (PPEM, PPEM), text, font=font, embedded_color=True
    )
    box = scratch.getchannel("A").getbbox()
    if box is None:
        raise RuntimeError("nothing was drawn")
    art = scratch.crop(box)
    if max(art.size) < MIN_GLYPH_PX:
        raise RuntimeError(f"drew only {art.width}x{art.height} -- not a glyph")
    side = round(max(art.size) / CANVAS_FILL)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(art, ((side - art.width) // 2, (side - art.height) // 2))
    return canvas


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        font_path = ensure_font(Path(args.font))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    font = ImageFont.truetype(str(font_path), PPEM)

    written = []
    for name, code in sorted(EMOJI.items()):
        path = dest / f"{name}.png"
        try:
            render(font, code).save(path, optimize=True)
        except (RuntimeError, OSError) as exc:
            print(f"ERROR: {name} (U+{code}): {exc}", file=sys.stderr)
            return 1
        written.append(path)
    total = sum(p.stat().st_size for p in written)
    print(f"{len(written)} emoji rendered into {dest} ({total / 1024:.0f} KB)")
    print("Noto Color Emoji, SIL OFL 1.1 -- the rendered frames carry nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
