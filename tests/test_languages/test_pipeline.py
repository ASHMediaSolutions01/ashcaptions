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
