"""Tests for ash_captions.styles.fonts.

All of these read the committed manifest.json -- no network access, and
none of the actual .ttf files need to exist on disk (spec 7A.4:
validation works whether or not `download_fonts()` has been run).
"""
from __future__ import annotations

import json

import pytest

from ash_captions.styles.fonts import (
    FontEntry,
    _google_family,
    _license_url,
    assets_fonts_dir,
    find_font_entry,
    is_font_bundled,
    list_font_families,
    load_manifest,
    manifest_path,
)
from ash_captions.styles.library import list_styles, shipped_styles_dir


def test_manifest_path_exists_and_is_used():
    assert manifest_path().is_file()


def test_load_manifest_covers_the_spec_7a4_families():
    families = set(list_font_families())
    expected = {
        "Anton", "Archivo Black", "Bebas Neue", "Titan One", "Alfa Slab One",
        "Montserrat", "Montserrat ExtraBold", "Poppins", "Poppins SemiBold", "Inter",
        "Rubik", "Outfit", "Manrope", "Figtree", "Space Grotesk",
        "Fredoka Medium", "Baloo 2 SemiBold", "Nunito",
        "Bangers", "Luckiest Guy", "Permanent Marker", "Caveat Medium",
        "Noto Sans", "Noto Naskh Arabic",
    }
    assert expected <= families


def test_every_manifest_entry_is_ofl_or_apache():
    for entry in load_manifest():
        assert entry.license.upper().startswith("OFL") or entry.license.upper().startswith("APACHE"), (
            f"{entry.family} has non-redistributable license {entry.license!r}"
        )


def test_every_manifest_licence_file_is_committed():
    """The manifest promises a licence text per family; that text must
    actually exist in the checkout (and so in the bundle), not just be a
    filename that nothing ever wrote -- which is what shipped before."""
    fonts_dir = assets_fonts_dir()
    for entry in load_manifest():
        path = fonts_dir / entry.license_file
        assert path.is_file(), f"{entry.family}: {entry.license_file} missing"
        text = path.read_text(encoding="utf-8")
        marker = "SIL OPEN FONT LICENSE" if entry.license.upper().startswith("OFL") else "Apache License"
        assert marker.lower() in text.lower(), f"{entry.family}: {path} is not a {entry.license} text"


def test_instanced_weight_variants_are_named_by_their_face_name():
    """Google serves weight variants as instanced files whose family name
    (name ID 1) carries the weight -- 'Baloo 2 SemiBold', not 'Baloo 2'.
    libass matches on that name only, so a manifest row for a non-400
    weight must use it, or every style naming the family renders as
    Arial (the shipped PLAYFUL bug)."""
    for entry in load_manifest():
        if entry.weight != 400 and entry.family != "Nunito":
            # Nunito-Bold's own family name is plain "Nunito" (weight in
            # the subfamily), so it is the one legitimate exception.
            assert entry.family.split()[-1] in {"ExtraBold", "SemiBold", "Medium", "Bold"}, entry


def test_is_font_bundled_true_for_exact_family():
    assert is_font_bundled("Inter") is True
    assert is_font_bundled("Bangers") is True
    assert is_font_bundled("Montserrat ExtraBold") is True
    assert is_font_bundled("Baloo 2 SemiBold") is True


def test_is_font_bundled_is_exact_match_only():
    """No prefix or base-family fallback: 'Inter Black' is not a bundled
    face and libass would fall back to Arial for it, so validation must
    say no rather than pass it through as 'Inter'."""
    assert is_font_bundled("Inter Black") is False
    assert is_font_bundled("Baloo 2") is False
    assert is_font_bundled("Montserrat Extra") is False
    assert is_font_bundled("inter") is False


def test_is_font_bundled_false_for_unknown_font():
    assert is_font_bundled("Comic Sans MS") is False
    assert is_font_bundled("") is False


def test_find_font_entry_returns_none_for_unknown_font():
    assert find_font_entry("Papyrus") is None


def test_every_shipped_style_names_a_bundled_face_exactly():
    styles = list_styles(user_dir=shipped_styles_dir().parent / "does-not-exist")
    for name, style in styles.items():
        assert find_font_entry(style.font) is not None, f"{name} uses non-bundled {style.font!r}"


def test_playful_uses_the_semibold_face_name():
    playful = json.loads((shipped_styles_dir() / "playful.json").read_text(encoding="utf-8"))
    assert playful["font"] == "Baloo 2 SemiBold"


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        (FontEntry("Baloo 2 SemiBold", "rounded", 600, "normal", "x.ttf", "OFL-1.1", "licenses/Baloo2.txt", "https://fonts.google.com/specimen/Baloo+2"), "https://raw.githubusercontent.com/google/fonts/main/ofl/baloo2/OFL.txt"),
        (FontEntry("Luckiest Guy", "comic", 400, "normal", "x.ttf", "Apache-2.0", "licenses/LuckiestGuy.txt", "https://fonts.google.com/specimen/Luckiest+Guy"), "https://raw.githubusercontent.com/google/fonts/main/apache/luckiestguy/LICENSE.txt"),
        (FontEntry("Ubuntu", "sans", 400, "normal", "x.ttf", "UFL-1.0", "licenses/Ubuntu.txt", ""), "https://raw.githubusercontent.com/google/fonts/main/ufl/ubuntu/UFL.txt"),
    ],
)
def test_license_url_follows_google_fonts_repo_layout(entry, expected):
    assert _license_url(entry) == expected


def test_google_family_comes_from_specimen_url_not_face_name():
    entry = find_font_entry("Montserrat ExtraBold")
    assert entry is not None
    assert _google_family(entry) == "Montserrat"
