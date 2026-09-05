"""Generate the bundled caption sound effects into ``assets/sounds/``.

Why synthesise rather than source: every sample library worth using
carries a licence that would have to travel with the product, be audited,
and be defended if a client's video is monetised. These five are made by
ffmpeg from the expressions in this file, so they are ours outright, they
are byte-identical on every machine that runs this script, and the whole
set is under 300 KB.

Run it when a sound changes; the WAVs it writes are committed, so a fresh
checkout has them without needing ffmpeg. ``tests/test_engine/test_sfx_assets.py``
checks every manifest entry against the file on disk.

    python scripts/make_sounds.py            # write assets/sounds/
    python scripts/make_sounds.py --check    # regenerate to a temp dir and
                                             # compare; non-zero if they drifted
"""
from __future__ import annotations

import argparse
import filecmp
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOUNDS_DIR = REPO_ROOT / "assets" / "sounds"
FFMPEG = REPO_ROOT / "bin" / "ffmpeg.exe"
MANIFEST_FILENAME = "manifest.json"

SAMPLE_RATE = 48_000
# Every sound is normalised to this peak, so a look's ``gain_db`` means the
# same thing whichever sound it names. -1.5 dBFS rather than 0 leaves room
# for the mix bus and for a codec's overshoot.
TARGET_PEAK_DBFS = -1.5


@dataclass(frozen=True)
class Recipe:
    """One sound: how it is made, and what an editor should expect."""

    name: str
    label: str
    description: str
    duration: float
    source: str  # a lavfi source filter, before normalisation


def _tone(expr: str, duration: float) -> str:
    """A mono expression rendered to stereo -- both channels identical. A
    caption hit is a point event; a stereo image on one would fight
    whatever the footage already has in the field.

    Commas are escaped because a filter's option list is split on them
    before the expression parser ever sees it: an unescaped ``pow(x,2)``
    ends the ``exprs`` option and ffmpeg then complains about the
    sample rate, several tokens away from the real mistake.
    """
    escaped = expr.replace(",", "\\,")
    return f"aevalsrc=exprs={escaped}|{escaped}:s={SAMPLE_RATE}:d={duration:g}"


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        name="pop",
        label="Pop",
        description=(
            "A short bright blip that drops in pitch. The default: it reads "
            "on a phone speaker without covering the voice."
        ),
        duration=0.16,
        # An exponential decay on a falling tone. The drop in pitch is what
        # makes it a pop rather than a beep.
        source=_tone("0.9*exp(-t*24)*sin(2*PI*t*(1400-4000*t))", 0.16),
    ),
    Recipe(
        name="click",
        label="Click",
        description=(
            "A tick, barely there. For a look where every word fires and "
            "anything longer would turn into a drum solo."
        ),
        duration=0.05,
        source=_tone("exp(-t*160)*sin(2*PI*t*2600)", 0.05),
    ),
    Recipe(
        name="whoosh",
        label="Whoosh",
        description=(
            "Filtered noise that swells and goes. The transition sound; best "
            "on a look where captions arrive a line at a time."
        ),
        duration=0.45,
        # Pink noise, band-limited to what a phone can reproduce, run through
        # a flanger for movement, under a raised-sine swell. The seed is
        # fixed so the file is reproducible.
        source=(
            f"anoisesrc=d=0.45:c=pink:a=0.9:s=7:r={SAMPLE_RATE}"
            ",highpass=f=500,lowpass=f=7000"
            ",flanger=delay=6:depth=6:speed=1.5"
            ",volume=volume=pow(sin(PI*min(1\\,t/0.45))\\,2.2):eval=frame"
            ",aformat=channel_layouts=stereo"
        ),
    ),
    Recipe(
        name="impact",
        label="Impact",
        description=(
            "A low thump with a transient on the front. Reserve it for "
            "keywords; on every sentence it becomes exhausting."
        ),
        duration=0.5,
        source=_tone(
            "0.95*exp(-t*8)*sin(2*PI*t*(90-70*t))"
            "+0.30*exp(-t*55)*sin(2*PI*t*430)"
            "+0.25*exp(-t*90)*(random(1)-0.5)",
            0.5,
        ),
    ),
    Recipe(
        name="riser",
        label="Riser",
        description=(
            "A tone that climbs. It points at the word after it, so it "
            "belongs on the last line before a punchline, not everywhere."
        ),
        duration=0.6,
        source=_tone("0.55*pow(t/0.6,2)*sin(2*PI*t*(180+1600*pow(t/0.6,2)))", 0.6),
    ),
)


class SoundBuildError(RuntimeError):
    """Raised for anything that would leave a broken or silent sound."""


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _peak_dbfs(path: Path) -> float:
    """The file's true peak, from ffmpeg's own astats."""
    result = _run([
        str(FFMPEG), "-hide_banner", "-nostdin", "-i", str(path),
        "-af", "astats=measure_overall=Peak_level:measure_perchannel=none",
        "-f", "null", "-",
    ])
    match = re.search(r"Peak level dB:\s*(-?[\d.]+|-inf)", result.stderr)
    if not match:
        raise SoundBuildError(f"astats reported no peak for {path.name}:\n{result.stderr[-800:]}")
    text = match.group(1)
    if text == "-inf":
        raise SoundBuildError(f"{path.name} came out silent")
    return float(text)


def render(recipe: Recipe, dest_dir: Path) -> Path:
    """Render one recipe, then normalise it to ``TARGET_PEAK_DBFS``.

    Two passes rather than a limiter: measuring the peak and applying the
    exact reciprocal gain is lossless and leaves the shape of the envelope
    alone, which is the whole character of a 160-millisecond sound.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    raw = dest_dir / f".{recipe.name}.raw.wav"
    final = dest_dir / f"{recipe.name}.wav"

    made = _run([
        str(FFMPEG), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", recipe.source,
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2",
        str(raw),
    ])
    if made.returncode != 0 or not raw.is_file():
        raise SoundBuildError(f"ffmpeg could not render {recipe.name}:\n{made.stderr[-800:]}")

    gain = TARGET_PEAK_DBFS - _peak_dbfs(raw)
    normalised = _run([
        str(FFMPEG), "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
        "-i", str(raw),
        "-af", f"volume={gain:.4f}dB",
        "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", "2",
        str(final),
    ])
    raw.unlink(missing_ok=True)
    if normalised.returncode != 0 or not final.is_file():
        raise SoundBuildError(f"ffmpeg could not normalise {recipe.name}:\n{normalised.stderr[-800:]}")
    return final


def write_manifest(dest_dir: Path) -> Path:
    """The committed index the app reads.

    Same shape as the font manifest, for the same reason: the app never
    scans the directory, so a stray file cannot become a selectable sound.
    """
    entries = []
    for recipe in RECIPES:
        path = dest_dir / f"{recipe.name}.wav"
        entries.append({
            "name": recipe.name,
            "label": recipe.label,
            "description": recipe.description,
            "file": path.name,
            "duration_seconds": round(recipe.duration, 3),
            "bytes": path.stat().st_size,
            "sample_rate": SAMPLE_RATE,
            "peak_dbfs": TARGET_PEAK_DBFS,
        })
    manifest = {
        "generated_by": "scripts/make_sounds.py",
        "license": "Synthesised for ASH Captions; distributed under the app's own licence.",
        "sounds": entries,
    }
    path = dest_dir / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def build(dest_dir: Path) -> list[Path]:
    if not FFMPEG.is_file():
        raise SoundBuildError(f"ffmpeg not found at {FFMPEG}; run scripts/fetch_ffmpeg.py first")
    written = [render(recipe, dest_dir) for recipe in RECIPES]
    written.append(write_manifest(dest_dir))
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate assets/sounds/.")
    parser.add_argument("--check", action="store_true",
                        help="regenerate into a temp dir and diff against assets/sounds/")
    args = parser.parse_args(argv)

    if not args.check:
        for path in build(SOUNDS_DIR):
            print(f"wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size:,} bytes)")
        return 0

    with tempfile.TemporaryDirectory(prefix="ash-sounds-") as temp:
        fresh = Path(temp)
        build(fresh)
        drifted = [
            recipe.name for recipe in RECIPES
            if not filecmp.cmp(fresh / f"{recipe.name}.wav", SOUNDS_DIR / f"{recipe.name}.wav", shallow=False)
        ]
    if drifted:
        print("these sounds no longer match this script: " + ", ".join(drifted), file=sys.stderr)
        return 1
    print(f"all {len(RECIPES)} sounds match scripts/make_sounds.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
