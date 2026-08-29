"""Tests for ash_captions.languages.spelling."""
from __future__ import annotations

import pytest

from ash_captions.languages.spelling import (
    EN_UK,
    EN_US,
    PT_BR,
    PT_PT,
    normalize_spelling,
)


# -- basic US <-> UK conversion -------------------------------------------


def test_us_to_uk_converts_color_to_colour():
    assert normalize_spelling("The color is nice.", EN_UK) == "The colour is nice."


def test_uk_to_us_converts_colour_to_color():
    assert normalize_spelling("The colour is nice.", EN_US) == "The color is nice."


@pytest.mark.parametrize(
    ("word", "convention", "expected"),
    [
        ("organize", EN_UK, "organise"),
        ("center", EN_UK, "centre"),
        ("traveled", EN_UK, "travelled"),
        ("catalog", EN_UK, "catalogue"),
        ("defense", EN_UK, "defence"),
        ("organise", EN_US, "organize"),
        ("centre", EN_US, "center"),
        ("travelled", EN_US, "traveled"),
        ("catalogue", EN_US, "catalog"),
        ("defence", EN_US, "defense"),
    ],
)
def test_common_convention_categories(word, convention, expected):
    assert normalize_spelling(f"a {word} word", convention) == f"a {expected} word"


# -- case preservation ------------------------------------------------------


def test_case_preservation_title_case():
    assert normalize_spelling("Color", EN_UK) == "Colour"


def test_case_preservation_upper_case():
    assert normalize_spelling("COLOR", EN_UK) == "COLOUR"


def test_case_preservation_lower_case():
    assert normalize_spelling("color", EN_UK) == "colour"


def test_case_preservation_sentence_context():
    text = "Color, COLOR, and color all appear here."
    assert normalize_spelling(text, EN_UK) == "Colour, COLOUR, and colour all appear here."


# -- word boundaries --------------------------------------------------------


def test_word_boundary_does_not_corrupt_substrings():
    # "colorful" is its own explicit entry; "discolor" is not in the table
    # and must be left alone rather than partially rewritten.
    assert normalize_spelling("discolor", EN_UK) == "discolor"


def test_word_boundary_matches_whole_word_only():
    text = "recolor the colorway"  # neither is the exact word "color"
    assert normalize_spelling(text, EN_UK) == text


# -- no-op cases --------------------------------------------------------------


def test_none_convention_is_noop():
    text = "The color is nice."
    assert normalize_spelling(text, None) == text


def test_empty_text_is_noop():
    assert normalize_spelling("", EN_UK) == ""


def test_unrelated_text_is_unchanged():
    text = "This sentence has no dialect-specific spelling in it."
    assert normalize_spelling(text, EN_UK) == text


# -- protected terms -----------------------------------------------------------


def test_protected_term_is_not_rewritten():
    text = "Color Labs is our vendor for color grading."
    result = normalize_spelling(text, EN_UK, protected={"Color Labs"})
    assert result == "Color Labs is our vendor for colour grading."


def test_protected_term_is_case_insensitive_match():
    text = "COLOR LABS shipped the color swatches."
    result = normalize_spelling(text, EN_UK, protected={"Color Labs"})
    assert result == "COLOR LABS shipped the colour swatches."


def test_no_protected_terms_behaves_like_default():
    text = "The color is nice."
    assert normalize_spelling(text, EN_UK, protected=()) == "The colour is nice."


# -- Portuguese BR <-> PT --------------------------------------------------


def test_pt_br_to_pt_converts_onibus_to_autocarro():
    assert normalize_spelling("Pegue o ônibus", PT_PT) == "Pegue o autocarro"


def test_pt_pt_to_br_converts_autocarro_to_onibus():
    assert normalize_spelling("Pegue o autocarro", PT_BR) == "Pegue o ônibus"


def test_pt_multiword_phrase_conversion_preserves_case():
    assert normalize_spelling("Café Da Manhã", PT_PT) == "Pequeno-Almoço"
