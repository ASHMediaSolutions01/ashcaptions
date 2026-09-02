"""Integration tests for the public interface in
ash_captions.languages.__init__: resolve() and postprocess().

This is the surface the engine and web UI actually call.
"""
from __future__ import annotations

import pytest

from ash_captions.languages import ResolvedDialect, postprocess, resolve
from ash_captions.languages.catalog import list_languages
from ash_captions.languages.dialects import list_dialects
from ash_captions.languages.spelling import EN_UK, EN_US


# -- resolve() --------------------------------------------------------------


def test_resolve_plain_language_no_dialect_chosen():
    resolved = resolve("it")  # Italian: no dialect presets at all
    assert resolved.whisper_language == "it"
    assert resolved.dialect is None
    assert resolved.initial_prompt == ""
    assert resolved.spelling_convention is None


def test_resolve_uses_default_dialect_when_none_specified():
    resolved = resolve("en")
    assert resolved.dialect is not None
    assert resolved.dialect.preset_id == "us"
    assert resolved.spelling_convention == EN_US


def test_resolve_explicit_preset():
    resolved = resolve("en", "uk")
    assert resolved.dialect is not None
    assert resolved.dialect.preset_id == "uk"
    assert resolved.spelling_convention == EN_UK
    assert resolved.whisper_language == "en"


def test_resolve_unknown_language_raises_value_error():
    with pytest.raises(ValueError, match="unknown language"):
        resolve("not-a-real-language")


def test_resolve_unknown_preset_raises_value_error():
    with pytest.raises(ValueError, match="unknown dialect preset"):
        resolve("en", "not-a-real-preset")


def test_resolve_empty_language_code_raises_value_error():
    with pytest.raises(ValueError):
        resolve("")


@pytest.mark.parametrize("language", list_languages())
def test_every_catalogued_language_resolves_without_error(language):
    resolved = resolve(language.code)
    assert resolved.whisper_language == language.code
    assert resolved.language is language


@pytest.mark.parametrize(
    "language_code",
    ["en", "es", "pt", "fr", "de", "nl"],
)
def test_every_dialect_preset_resolves_cleanly(language_code):
    for preset in list_dialects(language_code):
        resolved = resolve(language_code, preset.preset_id)
        assert resolved.dialect is preset
        assert resolved.whisper_language == language_code
        assert resolved.initial_prompt == preset.initial_prompt


# -- postprocess() ------------------------------------------------------------


def test_postprocess_applies_spelling_normalisation():
    resolved = resolve("en", "uk")
    result = postprocess("The color of the center.", resolved)
    assert result == "The colour of the centre."


def test_postprocess_noop_without_spelling_convention():
    resolved = resolve("it")
    text = "Il colore del centro."
    assert postprocess(text, resolved) == text


def test_postprocess_applies_client_glossary_before_spelling(tmp_path):
    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_text("gazi => Ghazi\n", encoding="utf-8")

    resolved = resolve("en", "uk")
    result = postprocess(
        "gazi mentioned the color scheme.", resolved, client_glossary_path=glossary_path
    )
    assert result == "Ghazi mentioned the colour scheme."


def test_postprocess_glossary_term_is_protected_from_spelling_pass(tmp_path):
    # "Color Labs" is a brand name that must not become "Colour Labs" even
    # though it contains a spelling-convention word.
    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_text("color labs => Color Labs\n", encoding="utf-8")

    resolved = resolve("en", "uk")
    result = postprocess(
        "We hired color labs for the color grading.",
        resolved,
        client_glossary_path=glossary_path,
    )
    assert result == "We hired Color Labs for the colour grading."


def test_postprocess_missing_client_glossary_does_not_crash(tmp_path):
    resolved = resolve("en", "uk")
    missing = tmp_path / "does-not-exist.txt"
    result = postprocess("The color is nice.", resolved, client_glossary_path=missing)
    assert result == "The colour is nice."


def test_postprocess_malformed_client_glossary_does_not_crash(tmp_path):
    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_bytes(b"\xff\xfe not valid utf-8 \x00")

    resolved = resolve("en", "uk")
    result = postprocess(
        "The color is nice.", resolved, client_glossary_path=glossary_path
    )
    assert result == "The colour is nice."


def test_postprocess_empty_text_returns_empty():
    resolved = resolve("en", "uk")
    assert postprocess("", resolved) == ""


def test_resolved_dialect_is_a_plain_dataclass_instance():
    resolved = resolve("en", "us")
    assert isinstance(resolved, ResolvedDialect)


# -- entries= and postprocess_words (review item 4) ---------------------------


def test_postprocess_accepts_preloaded_entries(tmp_path):
    from ash_captions.languages import load_glossary_entries

    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_text("gazi => Ghazi\n", encoding="utf-8")
    entries = load_glossary_entries(glossary_path)
    resolved = resolve("en", "uk")
    assert postprocess("gazi picked the color.", resolved, entries=entries) == "Ghazi picked the colour."


def test_postprocess_entries_win_over_path(tmp_path):
    from ash_captions.languages import GlossaryEntry

    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_text("gazi => WRONG\n", encoding="utf-8")
    resolved = resolve("en", "uk")
    result = postprocess(
        "gazi", resolved, client_glossary_path=glossary_path, entries=(GlossaryEntry("gazi", "Ghazi"),)
    )
    assert result == "Ghazi"


def test_postprocess_empty_entries_means_no_client_glossary(tmp_path):
    glossary_path = tmp_path / "glossary.txt"
    glossary_path.write_text("gazi => Ghazi\n", encoding="utf-8")
    resolved = resolve("en", "uk")
    assert postprocess("gazi", resolved, client_glossary_path=glossary_path, entries=()) == "gazi"


def test_postprocess_words_matches_multi_word_phrase_across_tokens():
    from ash_captions.languages import GlossaryEntry, postprocess_words

    entries = (GlossaryEntry("ash captions", "ASH Captions"),)
    resolved = resolve("en", "uk")
    out = postprocess_words(["welcome", "to", "ash", "captions", "color"], resolved, entries=entries)
    assert out == ("welcome", "to", "ASH", "Captions", "colour")


def test_postprocess_words_falls_back_per_word_when_token_count_changes():
    from ash_captions.languages import GlossaryEntry, postprocess_words

    entries = (GlossaryEntry("new york", "NYC"), GlossaryEntry("gazi", "Ghazi"))
    resolved = resolve("en", "us")
    out = postprocess_words(["gazi", "in", "new", "york"], resolved, entries=entries)
    assert out == ("Ghazi", "in", "new", "york")


def test_postprocess_words_keeps_one_output_per_input():
    from ash_captions.languages import postprocess_words

    resolved = resolve("en", "uk")
    words = ["the", "color", "of", "the", "center"]
    out = postprocess_words(words, resolved)
    assert len(out) == len(words)
    assert out == ("the", "colour", "of", "the", "centre")


def test_postprocess_words_empty_and_whitespace_tokens():
    from ash_captions.languages import postprocess_words

    resolved = resolve("en", "uk")
    assert postprocess_words([], resolved) == ()
    assert postprocess_words(["color", "a b"], resolved) == ("colour", "a b")
