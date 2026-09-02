"""Tests for the private boundary/casing helpers in
ash_captions.languages._textmatch, through their public consumers where
it matters (glossary, spelling)."""
from __future__ import annotations

import pytest

from ash_captions.languages._textmatch import (
    build_alternation,
    is_sentence_initial,
    match_case,
)
from ash_captions.languages.glossary import GlossaryEntry, apply_glossary


def test_alternation_uses_lookarounds_only_on_word_char_edges():
    pattern = build_alternation(["C++", "#hashtag", "colour"])
    assert pattern is not None
    assert [m.group(0) for m in pattern.finditer("C++ is fine; #hashtag too; colours no")] == ["C++", "#hashtag"]


def test_glossary_term_with_non_word_edges_matches():
    entries = (GlossaryEntry("c++", "C++"), GlossaryEntry("#ashcaptions", "#AshCaptions"))
    text, _ = apply_glossary("we wrote it in c++ and tagged #ashcaptions.", entries)
    assert text == "we wrote it in C++ and tagged #AshCaptions."


def test_glossary_term_still_needs_a_boundary_on_word_edges():
    entries = (GlossaryEntry("cat", "CAT"),)
    text, _ = apply_glossary("concatenate the cat's category", entries)
    assert text == "concatenate the CAT's category"


def test_alternation_is_cached_per_term_set():
    a = build_alternation(["colour", "flavour"])
    b = build_alternation(["flavour", "colour"])
    assert a is b


def test_alternation_empty_is_none():
    assert build_alternation([]) is None
    assert build_alternation([""]) is None


@pytest.mark.parametrize(
    ("source", "target", "expected"),
    [
        ("COLOR", "colour", "COLOUR"),
        ("Color", "colour", "Colour"),
        ("color", "colour", "colour"),
        ("Banheiro", "casa de banho", "Casa de banho"),
        ("Café Da Manhã", "pequeno-almoço", "Pequeno-almoço"),
        ("CoLoR", "colour", "Colour"),
        ("cOLOR", "colour", "colour"),
        ("", "colour", "colour"),
    ],
)
def test_match_case(source, target, expected):
    assert match_case(source, target) == expected


@pytest.mark.parametrize(
    ("text", "index", "expected"),
    [
        ("Center stage.", 0, True),
        ("The Center.", 4, False),
        ("Done. Center stage.", 6, True),
        ("Really? Center stage.", 8, True),
        ('He said "Center stage".', 9, False),  # quoted mid-sentence: leave it alone
        ('"Center stage," he said.', 1, True),
        ("line one\nCenter stage", 9, True),
        ("the (Center) thing", 5, False),
    ],
)
def test_is_sentence_initial(text, index, expected):
    assert is_sentence_initial(text, index) is expected
