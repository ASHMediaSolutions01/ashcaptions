"""``styles.render_word`` -- the per-word override primitives, as pure
functions (v0.6 design, section 2).

The rule these all serve: no override, no bytes. Every helper returns
empty strings for ``None``, which is what keeps ``render_ass`` byte-
identical for a transcript that has no overrides.
"""
from __future__ import annotations

from ash_captions.engine.transcribe import Word
from ash_captions.styles.render_word import (
    POP_HALF_MS,
    apply_case,
    apply_punctuation,
    face_tags,
    karaoke_override_tags,
    override_tags,
    scale_pct,
    prepare_word_text,
    scale_transform_tags,
    scaled_transform_tags,
    word_style_for,
)
from ash_captions.styles.schema import Style, WordStyle

WORD = Word(text="herramienta", start=10.82, end=11.66)
AMBER = WordStyle(colour="#FFD166")


def a_style(**over) -> Style:
    return Style.from_dict({"name": "T", **over}, check_font=False)


# ---------------------------------------------------------------------------
# looking a word up
# ---------------------------------------------------------------------------


def test_a_word_is_found_by_its_own_timings():
    assert word_style_for(WORD, {(10.82, 11.66): AMBER}) is AMBER


def test_no_mapping_and_no_match_are_both_none():
    assert word_style_for(WORD, None) is None
    assert word_style_for(WORD, {}) is None
    assert word_style_for(WORD, {(0.0, 1.0): AMBER}) is None


def test_an_override_that_sets_nothing_is_no_override():
    # An empty {} from the API must render as the look, not as a diff.
    assert word_style_for(WORD, {(10.82, 11.66): WordStyle()}) is None


# ---------------------------------------------------------------------------
# scale
# ---------------------------------------------------------------------------


def test_scale_pct_is_100_without_an_override():
    assert scale_pct(None) == 100
    assert scale_pct(WordStyle()) == 100
    assert scale_pct(AMBER) == 100
    assert scale_pct(WordStyle(scale=1.25)) == 125


def test_the_based_transform_matches_the_plain_one_at_100():
    # ...apart from the leading \fscx100\fscy100, which is why render.py
    # keeps using the plain one when the word has no size of its own.
    assert scaled_transform_tags(1.12, 100, POP_HALF_MS) == (
        "\\fscx100\\fscy100" + scale_transform_tags(1.12)
    )


def test_the_pop_runs_from_the_word_s_own_size_and_back_to_it():
    assert scaled_transform_tags(1.12, 125, 90) == (
        "\\fscx125\\fscy125\\t(0,90,\\fscx140\\fscy140)\\t(90,180,\\fscx125\\fscy125)"
    )


# ---------------------------------------------------------------------------
# the tags themselves
# ---------------------------------------------------------------------------


def test_nothing_at_all_for_no_override():
    assert face_tags(None) == ("", "")
    assert override_tags(None) == ("", "")
    assert override_tags(None, restore_colour="&HFFFFFF&") == ("", "")
    assert karaoke_override_tags(None, a_style()) == ("", "")


def test_weight_and_slant_open_and_close():
    assert face_tags(WordStyle(bold=True)) == ("\\b1", "\\b0")
    assert face_tags(WordStyle(bold=False)) == ("\\b0", "\\b0")
    assert face_tags(WordStyle(italic=True)) == ("\\i1", "\\i0")
    assert face_tags(WordStyle(bold=True, italic=True)) == ("\\b1\\i1", "\\b0\\i0")


def test_size_is_a_face_tag_unless_the_caller_baked_it_in():
    ws = WordStyle(scale=1.25)
    assert face_tags(ws) == ("\\fscx125\\fscy125", "\\fscx100\\fscy100")
    assert face_tags(ws, include_scale=False) == ("", "")


def test_colour_is_bgr_and_restores_only_when_asked():
    assert override_tags(AMBER) == ("\\c&H66D1FF&", "")
    assert override_tags(AMBER, restore_colour="&HFFFFFF&") == ("\\c&H66D1FF&", "\\c&HFFFFFF&")


def test_a_full_override_emits_colour_then_weight_then_slant_then_size():
    ws = WordStyle(colour="#FFD166", scale=1.5, bold=True, italic=True)
    assert override_tags(ws, restore_colour="&HFFFFFF&") == (
        "\\c&H66D1FF&\\b1\\i1\\fscx150\\fscy150",
        "\\c&HFFFFFF&\\b0\\i0\\fscx100\\fscy100",
    )


def test_free_placement_is_not_this_renderer_s_business():
    # x/y belong to track F's free-placement look; the line renderer emits
    # nothing for them.
    assert override_tags(WordStyle(x=0.2, y=0.8)) == ("", "")


def test_karaoke_colours_both_halves_of_the_sweep_and_restores_the_look_s():
    style = a_style(colors={"text": "#FFFFFF", "active": "#00E28A"})
    open_tags, close_tags = karaoke_override_tags(AMBER, style)
    assert open_tags == "\\c&H66D1FF&\\2c&H66D1FF&"
    assert close_tags == "\\c&H8AE200&\\2c&HFFFFFF&"


def test_karaoke_carries_weight_slant_and_size_like_any_other_look():
    style = a_style()
    assert karaoke_override_tags(WordStyle(bold=True, scale=0.75), style) == (
        "\\b1\\fscx75\\fscy75",
        "\\b0\\fscx100\\fscy100",
    )


# ---------------------------------------------------------------------------
# the look's treatment of the words it was given
# ---------------------------------------------------------------------------


class TestApplyCase:
    def test_as_written_is_the_identity(self):
        assert apply_case("Hello, World.", "as_written") == "Hello, World."

    def test_upper_and_lower(self):
        assert apply_case("Hello", "upper") == "HELLO"
        assert apply_case("Hello", "lower") == "hello"

    def test_german_eszett_uppercases_to_two_letters(self):
        """German orthography requires it, and ``str.upper`` already does
        it. A title-caser would not, which is why this is not one."""
        assert apply_case("Straße", "upper") == "STRASSE"

    def test_accents_survive_both_directions(self):
        assert apply_case("café", "upper") == "CAFÉ"
        assert apply_case("ÄÖÜ", "lower") == "äöü"


class TestApplyPunctuation:
    def test_keep_is_the_identity(self):
        for word in ("Hello,", "really?", "—", "don't"):
            assert apply_punctuation(word, "keep") == word

    def test_no_stops_drops_the_separators(self):
        assert apply_punctuation("Hello,", "no_stops") == "Hello"
        assert apply_punctuation("end...", "no_stops") == "end"
        assert apply_punctuation("so:", "no_stops") == "so"

    def test_no_stops_keeps_the_marks_that_carry_tone(self):
        """A caption reading "really" and one reading "really?" are
        different lines. That is the whole reason for the middle mode."""
        assert apply_punctuation("really?", "no_stops") == "really?"
        assert apply_punctuation("wow!", "no_stops") == "wow!"
        assert apply_punctuation("¿Qué?", "no_stops") == "¿Qué?"

    def test_no_stops_reaches_other_scripts(self):
        assert apply_punctuation("你好。", "no_stops") == "你好"
        assert apply_punctuation("مرحبا،", "no_stops") == "مرحبا"

    def test_none_drops_tone_and_quotes_too(self):
        assert apply_punctuation("really?", "none") == "really"
        assert apply_punctuation("“quoted”", "none") == "quoted"
        assert apply_punctuation("wow!!", "none") == "wow"

    def test_none_keeps_an_apostrophe_or_hyphen_inside_a_word(self):
        """Stripping these makes the word wrong rather than plainer."""
        assert apply_punctuation("don't", "none") == "don't"
        assert apply_punctuation("l’ami", "none") == "l’ami"
        assert apply_punctuation("twenty-five", "none") == "twenty-five"

    def test_none_still_drops_one_at_the_edge_of_a_word(self):
        assert apply_punctuation("-dash", "none") == "dash"
        assert apply_punctuation("'quoted'", "none") == "quoted"

    def test_a_token_that_is_only_punctuation_comes_back_empty(self):
        """Left empty on purpose: dropping the word would slide every
        later word onto the wrong moment, because the word list drives
        the active-word index and the karaoke timings."""
        assert apply_punctuation("—", "none") == ""
        assert apply_punctuation("...", "no_stops") == ""

    def test_empty_input_is_safe_in_every_mode(self):
        for mode in ("keep", "no_stops", "none"):
            assert apply_punctuation("", mode) == ""


class TestPrepareWordText:
    def _style(self, **kw):
        return Style.from_dict({"name": "X", **kw}, check_font=False)

    def test_a_default_look_only_escapes(self):
        assert prepare_word_text("Hello,", self._style()) == "Hello,"

    def test_both_treatments_compose(self):
        style = self._style(case_mode="upper", punctuation="no_stops")
        assert prepare_word_text("Hello,", style) == "HELLO"

    def test_escaping_still_happens_after_the_treatment(self):
        """The brace and backslash substitutions must survive, or a word
        containing one would break the .ass it is written into."""
        style = self._style(case_mode="upper")
        assert prepare_word_text("a{b}c", style) == "A｛B｝C"
