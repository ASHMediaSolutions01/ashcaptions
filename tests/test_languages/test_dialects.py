"""Tests for ash_captions.languages.dialects."""
from __future__ import annotations

import pytest

from ash_captions.languages.catalog import get_language
from ash_captions.languages.dialects import default_dialect, get_dialect, list_dialects

# Spec section 7.2's preset table, as (language_code, {preset_ids}).
_EXPECTED_PRESETS: dict[str, set[str]] = {
    "en": {"us", "uk", "au", "ca", "in", "ie", "za"},
    "es": {"mx", "es", "ar", "co", "cl", "us"},
    "pt": {"br", "pt"},
    "fr": {"fr", "ca"},
    "de": {"de", "at", "ch"},
    "nl": {"nl", "be"},
}


@pytest.mark.parametrize("language_code", sorted(_EXPECTED_PRESETS))
def test_every_spec_preset_is_present(language_code):
    preset_ids = {d.preset_id for d in list_dialects(language_code)}
    assert preset_ids == _EXPECTED_PRESETS[language_code]


@pytest.mark.parametrize("language_code", sorted(_EXPECTED_PRESETS))
def test_every_dialect_resolves_to_a_valid_catalogue_language_code(language_code):
    for preset in list_dialects(language_code):
        assert get_language(preset.language_code) is not None
        assert preset.language_code == language_code


def test_every_dialect_has_a_nonempty_initial_prompt():
    for language_code in _EXPECTED_PRESETS:
        for preset in list_dialects(language_code):
            assert preset.initial_prompt.strip()
            assert preset.label.strip()


def test_get_dialect_is_case_insensitive():
    us_lower = get_dialect("en", "us")
    us_upper = get_dialect("EN", "US")
    assert us_lower is not None
    assert us_lower is us_upper


def test_get_dialect_unknown_returns_none():
    assert get_dialect("en", "not-a-real-preset") is None
    assert get_dialect("not-a-real-language", "us") is None
    assert get_dialect("", "us") is None
    assert get_dialect("en", "") is None


def test_list_dialects_empty_for_language_without_presets():
    # Italian is Flagship but has no dialect presets in the spec table.
    assert list_dialects("it") == ()


def test_list_dialects_empty_for_unknown_language():
    assert list_dialects("not-a-real-language") == ()


@pytest.mark.parametrize(
    ("language_code", "expected_default_preset_id"),
    [
        ("en", "us"),
        ("es", "mx"),
        ("pt", "br"),
        ("fr", "fr"),
        ("de", "de"),
        ("nl", "nl"),
    ],
)
def test_default_dialect_matches_spec_bolded_preset(language_code, expected_default_preset_id):
    default = default_dialect(language_code)
    assert default is not None
    assert default.preset_id == expected_default_preset_id
    assert default.is_default is True


def test_default_dialect_none_for_language_without_presets():
    assert default_dialect("it") is None


def test_each_language_has_exactly_one_default_dialect():
    for language_code in _EXPECTED_PRESETS:
        defaults = [d for d in list_dialects(language_code) if d.is_default]
        assert len(defaults) == 1
