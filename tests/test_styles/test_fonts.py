"""Tests for ash_captions.styles.fonts.

All of these read the committed manifest.json -- no network access, and
none of the actual .ttf files need to exist on disk (spec 7A.4:
validation works whether or not `download_fonts()` has been run).
"""
from __future__ import annotations

from ash_captions.styles.fonts import (
    find_font_entry,
    is_font_bundled,
    list_font_families,
    load_manifest,
    manifest_path,
)


def test_manifest_path_exists_and_is_used():
    assert manifest_path().is_file()


def test_load_manifest_covers_the_spec_7a4_families():
    families = set(list_font_families())
    expected = {
        "Anton", "Archivo Black", "Bebas Neue", "Titan One", "Alfa Slab One",
        "Montserrat", "Poppins", "Inter", "Rubik", "Outfit", "Manrope",
        "Figtree", "Space Grotesk",
        "Fredoka", "Baloo 2", "Nunito",
        "Bangers", "Luckiest Guy", "Permanent Marker", "Caveat",
        "Noto Sans", "Noto Naskh Arabic",
    }
    assert expected <= families


def test_every_manifest_entry_is_ofl_or_apache():
    for entry in load_manifest():
        assert entry.license.upper().startswith("OFL") or entry.license.upper().startswith("APACHE"), (
            f"{entry.family} has non-redistributable license {entry.license!r}"
        )


def test_is_font_bundled_true_for_exact_family():
    assert is_font_bundled("Inter") is True
    assert is_font_bundled("Bangers") is True


def test_is_font_bundled_true_for_family_plus_weight_suffix():
    # "Montserrat ExtraBold" has its own manifest row.
    assert is_font_bundled("Montserrat ExtraBold") is True
    # "Inter Black" has no dedicated row but should resolve to base "Inter".
    assert is_font_bundled("Inter Black") is True


def test_is_font_bundled_false_for_unknown_font():
    assert is_font_bundled("Comic Sans MS") is False
    assert is_font_bundled("") is False


def test_find_font_entry_returns_none_for_unknown_font():
    assert find_font_entry("Papyrus") is None
