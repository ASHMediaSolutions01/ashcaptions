"""Proves every bundled family resolves to its bundled file in the REAL
libass, via the real ffmpeg -- the check that would have caught PLAYFUL
rendering in Arial.

Skipped unless ASH_REAL_FFMPEG is set (to "1", or to the path of an
ffmpeg.exe). Also skipped if any manifest font file is missing from
assets/fonts/ (they are fetched, not committed). Run it on the build box:

    set ASH_REAL_FFMPEG=1
    .venv\\Scripts\\python.exe -m pytest tests/test_styles/test_fontselect_real.py -v

One ASS script declares one Style per manifest family and one Dialogue per
Style, all on the first frame; ffmpeg renders that single frame with
`-loglevel verbose`, and libass logs one

    fontselect: (Family, weight, italic) -> <PostScript name>, <index>, <name>

line per lookup. Fonts loaded from `fontsdir` are memory fonts, so libass
reports the face it chose by its PostScript name (OpenType name ID 6), not
a path -- and a system fallback shows up the same way ("ArialMT"). So each
bundled file's own name table is read here, and the family must resolve
to exactly that file's PostScript name.
"""
from __future__ import annotations

import os
import re
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from ash_captions.styles.fonts import assets_fonts_dir, load_manifest

_ENV = os.environ.get("ASH_REAL_FFMPEG", "")
pytestmark = pytest.mark.skipif(not _ENV, reason="set ASH_REAL_FFMPEG=1 to run against the real ffmpeg")

_FONTSELECT = re.compile(
    r"fontselect: \((?P<family>.+?), (?P<bold>\d+), (?P<italic>\d+)\) -> (?P<face>.+?), (?P<index>\d+), (?P<name>.*)$"
)


def _ffmpeg_path() -> Path:
    if _ENV not in ("", "1", "true") and Path(_ENV).is_file():
        return Path(_ENV)
    from ash_captions.config import find_binary

    found = find_binary("ffmpeg")
    if found is None:
        pytest.skip("no ffmpeg.exe found (bin/ffmpeg.exe or PATH)")
    return found


def _escape(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def _sample_text(family: str) -> str:
    return "سلام عليكم" if "Arabic" in family else "Ag 12"


def _name_table(data: bytes) -> bytes:
    """The raw `name` table of a TrueType/OpenType (or WOFF 1) file."""
    if data[:4] == b"wOFF":
        count = struct.unpack(">H", data[12:14])[0]
        for i in range(count):
            off = 44 + 20 * i
            toff, comp_len, orig_len = struct.unpack(">III", data[off + 4:off + 16])
            if data[off:off + 4] == b"name":
                raw = data[toff:toff + comp_len]
                return zlib.decompress(raw) if comp_len < orig_len else raw
        raise ValueError("no name table")
    count = struct.unpack(">H", data[4:6])[0]
    for i in range(count):
        off = 12 + 16 * i
        toff, tlen = struct.unpack(">II", data[off + 8:off + 16])
        if data[off:off + 4] == b"name":
            return data[toff:toff + tlen]
    raise ValueError("no name table")


def font_names(path: Path) -> dict[int, str]:
    """OpenType name records by name ID (Windows/English preferred)."""
    table = _name_table(path.read_bytes())
    _fmt, count, str_off = struct.unpack(">HHH", table[:6])
    out: dict[int, str] = {}
    for i in range(count):
        rec = 6 + 12 * i
        pid, _eid, lid, nid, length, offset = struct.unpack(">HHHHHH", table[rec:rec + 12])
        raw = table[str_off + offset:str_off + offset + length]
        if pid == 3 and lid == 0x409:
            out[nid] = raw.decode("utf-16-be", errors="replace")
        elif pid == 1 and nid not in out:
            out[nid] = raw.decode("mac-roman", errors="replace")
    return out


@pytest.fixture(scope="module")
def resolved_by_family(tmp_path_factory) -> dict[str, str]:
    fonts_dir = assets_fonts_dir()
    entries = load_manifest()
    missing = [e.file for e in entries if not (fonts_dir / e.file).is_file()]
    if missing:
        pytest.skip(f"font files not fetched: {missing}")

    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 640", "PlayResY: 360", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    for i, entry in enumerate(entries):
        lines.append(
            f"Style: F{i},{entry.family},20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
            f"0,0,0,0,100,100,0,0,1,1,0,5,10,10,10,1"
        )
    lines += ["", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for i, entry in enumerate(entries):
        lines.append(f"Dialogue: 0,0:00:00.00,0:00:00.20,F{i},,0,0,0,,{_sample_text(entry.family)}")

    work = tmp_path_factory.mktemp("fontselect")
    ass_path = work / "families.ass"
    ass_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    command = [
        str(_ffmpeg_path()), "-hide_banner", "-loglevel", "verbose", "-nostdin",
        "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25",
        "-vf", f"subtitles='{_escape(ass_path)}':fontsdir='{_escape(fonts_dir)}'",
        "-frames:v", "1", "-f", "null", "-",
    ]
    proc = subprocess.run(command, capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-2000:]

    resolved: dict[str, str] = {}
    for line in proc.stderr.splitlines():
        match = _FONTSELECT.search(line)
        if match and match.group("family") not in resolved:
            resolved[match.group("family")] = match.group("face")
    assert resolved, f"no fontselect lines in ffmpeg output:\n{proc.stderr[-2000:]}"
    return resolved


@pytest.mark.parametrize("entry", load_manifest(), ids=lambda e: e.family)
def test_family_resolves_to_its_bundled_file(entry, resolved_by_family):
    assert entry.family in resolved_by_family, (
        f"libass never looked up {entry.family!r}; seen: {sorted(resolved_by_family)}"
    )
    names = font_names(assets_fonts_dir() / entry.file)
    postscript_name = names.get(6)
    assert postscript_name, f"{entry.file} has no PostScript name record"
    # The manifest family must be the file's own family name (ID 1) -- that
    # is what libass matched on to get here.
    assert names.get(1) == entry.family, f"{entry.file}: name ID 1 is {names.get(1)!r}, manifest says {entry.family!r}"
    chosen = resolved_by_family[entry.family]
    assert chosen == postscript_name, (
        f"{entry.family!r} resolved to {chosen!r} (a system face?), expected {postscript_name!r} from {entry.file}"
    )


def test_no_bundled_family_resolves_to_a_system_face(resolved_by_family):
    bundled = {font_names(assets_fonts_dir() / e.file).get(6) for e in load_manifest()}
    outside = {family: face for family, face in resolved_by_family.items() if face not in bundled}
    assert not outside, f"families resolved outside the bundle: {outside}"
