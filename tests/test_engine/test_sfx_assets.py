"""The bundled sounds themselves: are they there, and are they audible?

``scripts/make_sounds.py`` writes these and they are committed, so this is
the guard against the two ways that goes wrong silently -- a manifest
entry whose .wav was never generated, and a .wav that renders as silence
because an ffmpeg expression stopped parsing the way it used to.

Nobody on this project can listen to a test run, so "is it the right
sound" is checked by shape: how long it is, where its energy sits in
time, and how bright it is. Those three distinguish a pop from a riser.
"""
from __future__ import annotations

import array
import wave
from pathlib import Path

import pytest

from ash_captions.styles import sounds as sounds_module

SOUNDS_DIR = Path(__file__).resolve().parents[2] / "assets" / "sounds"
MANIFEST = SOUNDS_DIR / "manifest.json"

pytestmark = pytest.mark.skipif(
    not MANIFEST.is_file(),
    reason="assets/sounds is not generated in this checkout (scripts/make_sounds.py)",
)


def manifest():
    return sounds_module.load_manifest(path=MANIFEST)


def samples_of(name: str) -> tuple[array.array, int]:
    entry = next(e for e in manifest() if e.name == name)
    with wave.open(str(SOUNDS_DIR / entry.file), "rb") as handle:
        raw = array.array("h")
        raw.frombytes(handle.readframes(handle.getnframes()))
        return raw[0::handle.getnchannels()], handle.getframerate()


def rms(chunk) -> float:
    return (sum(float(s) * s for s in chunk) / max(1, len(chunk))) ** 0.5


def zero_crossings_per_second(chunk, rate: float) -> float:
    """A crude brightness measure: high for noise or a high tone, low for
    a bass thump. Enough to tell a click from an impact."""
    if len(chunk) < 2:
        return 0.0
    crossings = sum(1 for a, b in zip(chunk, chunk[1:], strict=False) if (a >= 0) != (b >= 0))
    return crossings / (len(chunk) / rate)


def test_the_manifest_lists_the_five_sounds_the_looks_can_name():
    assert sorted(e.name for e in manifest()) == ["click", "impact", "pop", "riser", "whoosh"]


@pytest.mark.parametrize("name", ["pop", "click", "whoosh", "impact", "riser"])
def test_every_listed_sound_is_on_disk_and_resolvable(name):
    assert sounds_module.is_sound_bundled(name, path=MANIFEST)
    path = sounds_module.sound_path(name, directory=SOUNDS_DIR)
    assert path is not None and path.is_file()


@pytest.mark.parametrize("name", ["pop", "click", "whoosh", "impact", "riser"])
def test_no_sound_is_silent_and_none_clips(name):
    data, _ = samples_of(name)
    peak = max(abs(s) for s in data) / 32767
    # -1.5 dBFS is what make_sounds.py normalises to; anything quieter
    # means the render fell through, anything louder means it clipped.
    assert 0.80 < peak < 0.87, peak


@pytest.mark.parametrize("name", ["pop", "click", "whoosh", "impact", "riser"])
def test_each_sound_is_the_length_the_manifest_promises(name):
    entry = next(e for e in manifest() if e.name == name)
    data, rate = samples_of(name)
    assert abs(len(data) / rate - entry.duration_seconds) < 0.01
    assert rate == entry.sample_rate


@pytest.mark.parametrize("name", ["pop", "click", "impact"])
def test_the_percussive_sounds_start_loud_and_decay(name):
    """A hit has to be heard at the instant the word lands, so its energy
    belongs at the front. If one of these ever comes out back-loaded, it
    has stopped being a hit."""
    data, _ = samples_of(name)
    third = len(data) // 3
    assert rms(data[:third]) > 2 * rms(data[-third:])


def test_the_riser_climbs_instead():
    """The one deliberate exception: it points at the word after it."""
    data, _ = samples_of("riser")
    third = len(data) // 3
    assert rms(data[-third:]) > 2 * rms(data[:third])


def test_the_whoosh_swells_in_the_middle():
    data, _ = samples_of("whoosh")
    third = len(data) // 3
    assert rms(data[third:2 * third]) > rms(data[:third])
    assert rms(data[third:2 * third]) > rms(data[-third:])


def test_the_impact_is_low_and_the_click_is_bright():
    """The two ends of the library. If these ever swap, a look built for
    one is firing the other."""
    impact, rate = samples_of("impact")
    click, _ = samples_of("click")
    assert zero_crossings_per_second(impact, rate) < 400
    assert zero_crossings_per_second(click, rate) > 2000


def test_the_pop_falls_in_pitch():
    """What makes it a pop rather than a beep."""
    data, rate = samples_of("pop")
    half = len(data) // 2
    assert zero_crossings_per_second(data[:half], rate) > 1.5 * zero_crossings_per_second(data[half:], rate)


def test_a_missing_library_is_no_sounds_rather_than_an_exception(tmp_path):
    """An old bundle, or a checkout that has never run make_sounds.py. A
    look naming a sound must still load; it simply burns without one."""
    assert sounds_module.load_manifest(path=tmp_path / "manifest.json") == ()
    assert sounds_module.sound_path("pop", directory=tmp_path) is None


def test_a_manifest_entry_whose_file_vanished_resolves_to_nothing(tmp_path):
    (tmp_path / "manifest.json").write_text(
        '{"sounds": [{"name": "pop", "file": "pop.wav", "duration_seconds": 0.16}]}',
        encoding="utf-8",
    )
    assert sounds_module.is_sound_bundled("pop", path=tmp_path / "manifest.json")
    assert sounds_module.sound_path("pop", directory=tmp_path) is None
