"""Tests for ash_captions.languages.catalog."""
from __future__ import annotations

import pytest

from ash_captions.languages.catalog import (
    QualityBand,
    ScriptDirection,
    get_language,
    languages_by_band,
    list_languages,
)


def test_list_languages_nonempty_and_covers_priority_languages():
    codes = {lang.code for lang in list_languages()}
    assert {"en", "es", "pt"}.issubset(codes)
    assert len(codes) == len(list_languages()), "language codes must be unique"


def test_list_languages_includes_arabic_as_rtl():
    arabic = get_language("ar")
    assert arabic is not None
    assert arabic.direction is ScriptDirection.RTL


def test_all_non_arabic_languages_are_ltr():
    for lang in list_languages():
        if lang.code != "ar":
            assert lang.direction is ScriptDirection.LTR


def test_get_language_is_case_insensitive():
    assert get_language("EN") is get_language("en")
    assert get_language("Es") is get_language("es")


def test_get_language_unknown_code_returns_none():
    assert get_language("xx-not-real") is None
    assert get_language("") is None


def test_priority_languages_are_flagship():
    for code in ("en", "es", "pt"):
        lang = get_language(code)
        assert lang is not None
        assert lang.band is QualityBand.FLAGSHIP


def test_languages_by_band_partitions_the_catalogue():
    flagship = languages_by_band(QualityBand.FLAGSHIP)
    strong = languages_by_band(QualityBand.STRONG)
    works = languages_by_band(QualityBand.WORKS)

    assert len(flagship) + len(strong) + len(works) == len(list_languages())
    assert all(lang.band is QualityBand.FLAGSHIP for lang in flagship)
    assert all(lang.band is QualityBand.STRONG for lang in strong)
    assert all(lang.band is QualityBand.WORKS for lang in works)


def test_every_language_has_a_non_empty_native_name():
    for lang in list_languages():
        assert lang.native_name.strip()
        assert lang.english_name.strip()
        assert lang.code.strip()


# Native names must carry their real diacritics/script, not an ASCII
# transliteration -- this is what an editor sees in the language picker.
# A regression here (e.g. "Espanol" instead of "Español") must fail loudly.
@pytest.mark.parametrize(
    ("code", "expected_native_name"),
    [
        ("es", "Español"),
        ("pt", "Português"),
        ("fr", "Français"),
        ("ca", "Català"),
        ("ro", "Română"),
        ("cs", "Čeština"),
        ("sk", "Slovenčina"),
        ("sl", "Slovenščina"),
        ("tr", "Türkçe"),
        ("vi", "Tiếng Việt"),
        ("is", "Íslenska"),
        ("az", "Azərbaycanca"),
        ("lv", "Latviešu"),
        ("lt", "Lietuvių"),
        ("lb", "Lëtzebuergesch"),
        ("yo", "Yorùbá"),
        ("mi", "Māori"),
        ("ht", "Kreyòl Ayisyen"),
        ("fo", "Føroyskt"),
        ("tk", "Türkmençe"),
        ("ar", "العربية"),
    ],
)
def test_native_name_preserves_diacritics_and_script(code, expected_native_name):
    lang = get_language(code)
    assert lang is not None
    assert lang.native_name == expected_native_name


def test_arabic_native_name_is_arabic_script_not_a_transliteration():
    arabic = get_language("ar")
    assert arabic is not None
    # every character should be outside the ASCII range
    assert all(ord(ch) > 127 for ch in arabic.native_name)
