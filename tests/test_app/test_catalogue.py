"""Tests for LanguageCatalogue: the ash_captions.languages -> web.models
mapping (spec section 7), and dialect_preset_id()'s round trip back to
the bare preset id languages.resolve() expects.
"""

from __future__ import annotations

from ash_captions.app.catalogue import LanguageCatalogue, dialect_preset_id
from ash_captions.languages import QualityBand


class TestListLanguages:
    def test_returns_every_catalogued_language(self) -> None:
        result = LanguageCatalogue().list_languages()
        codes = {lang.code for lang in result}
        assert "en" in codes
        assert "es" in codes
        assert "ar" in codes  # spec 7.3

    def test_band_is_the_lowercase_string_web_expects(self) -> None:
        result = LanguageCatalogue().list_languages()
        by_code = {lang.code: lang for lang in result}
        assert by_code["en"].band == "flagship"
        assert by_code["en"].band == QualityBand.FLAGSHIP.value
        assert by_code["mt"].band == "works"  # Maltese, Works tier

    def test_label_shows_native_name_alongside_when_it_differs(self) -> None:
        by_code = {lang.code: lang for lang in LanguageCatalogue().list_languages()}
        assert by_code["es"].label == "Spanish (Español)"

    def test_label_omits_native_name_when_identical(self) -> None:
        by_code = {lang.code: lang for lang in LanguageCatalogue().list_languages()}
        assert by_code["en"].label == "English"

    def test_dialects_use_language_prefixed_uppercase_preset_codes(self) -> None:
        by_code = {lang.code: lang for lang in LanguageCatalogue().list_languages()}
        spanish_dialect_codes = {d.code for d in by_code["es"].dialects}
        assert "es-MX" in spanish_dialect_codes
        assert "es-ES" in spanish_dialect_codes

    def test_dialect_label_is_human_readable(self) -> None:
        by_code = {lang.code: lang for lang in LanguageCatalogue().list_languages()}
        by_dialect_code = {d.code: d for d in by_code["es"].dialects}
        assert by_dialect_code["es-MX"].label == "Spanish (Mexico)"

    def test_language_with_no_dialect_presets_has_empty_dialect_list(self) -> None:
        by_code = {lang.code: lang for lang in LanguageCatalogue().list_languages()}
        assert by_code["it"].dialects == []  # Italian has no dialect presets


class TestDialectPresetId:
    def test_recovers_bare_lowercase_preset_id(self) -> None:
        assert dialect_preset_id("es", "es-MX") == "mx"
        assert dialect_preset_id("pt", "pt-BR") == "br"

    def test_none_dialect_code_returns_none(self) -> None:
        assert dialect_preset_id("en", None) is None

    def test_empty_dialect_code_returns_none(self) -> None:
        assert dialect_preset_id("en", "") is None

    def test_dialect_code_for_a_different_language_returns_none(self) -> None:
        assert dialect_preset_id("es", "en-US") is None

    def test_round_trips_through_the_catalogue_for_every_dialect(self) -> None:
        """Every dialect code the catalogue emits must map back to the
        bare preset id `languages.resolve()` was built to accept."""
        for language in LanguageCatalogue().list_languages():
            for dialect in language.dialects:
                preset_id = dialect_preset_id(language.code, dialect.code)
                assert preset_id is not None
                assert preset_id == preset_id.lower()
