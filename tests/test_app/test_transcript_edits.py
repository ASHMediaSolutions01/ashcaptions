"""Hand edits to the saved transcript (v0.6 section 1): the record grows a
parallel ``meta`` tuple and a revision, and the five operations over it are
pure functions -- no server, no files, except where the file format itself
is under test."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ash_captions.app.transcript import (
    FORMAT_VERSION,
    MIN_WORD_SECONDS,
    TranscriptError,
    TranscriptRecord,
    WordMeta,
    WordStyle,
    WordStyleError,
    apply_case,
    break_indexes,
    load_transcript,
    merge,
    occurrences,
    parse_word_style,
    retime,
    save_transcript,
    set_style,
    set_text,
    split,
    split_word_text,
    styled_words,
    transcript_path,
)
from ash_captions.engine import Segment, Word

WORDS = (
    Word("Coge", 0.0, 0.40, 0.95),
    Word("la", 0.40, 0.55, 0.99),
    Word("haramienta,", 0.55, 1.10, 0.41),
    Word("la", 1.20, 1.35, 0.98),
    Word("HARAMIENTA", 1.35, 1.90, 0.52),
    Word("grande", 1.90, 2.40, 0.97),
)


def a_record(**kwargs) -> TranscriptRecord:
    words = kwargs.pop("words", WORDS)
    segments = kwargs.pop(
        "segments", (Segment(" ".join(w.text for w in words), words[0].start, words[-1].end, words),)
    )
    return TranscriptRecord(language="es", words=words, segments=segments, **kwargs)


# --- the file format -------------------------------------------------------


class TestFormat:
    def test_a_version_1_file_loads_with_no_meta_and_revision_zero(self, tmp_path: Path):
        legacy = {
            "format": 1,
            "language": "es",
            "words": [{"t": "hola", "s": 0.0, "e": 0.4, "p": 0.9}],
            "segments": [{"text": "hola", "s": 0.0, "e": 0.4, "words": []}],
        }
        path = tmp_path / "clip.transcript.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        record = load_transcript(path)
        assert record.meta is None
        assert record.revision == 0
        assert record.words[0].text == "hola"

    def test_new_files_are_version_2_and_untouched_records_carry_no_meta(self, tmp_path: Path):
        save_transcript(transcript_path(tmp_path, "clip"), a_record())
        payload = json.loads((tmp_path / "clip.transcript.json").read_text(encoding="utf-8"))
        assert payload["format"] == FORMAT_VERSION == 2
        assert payload["meta"] is None
        assert payload["revision"] == 0

    def test_meta_and_styles_survive_a_round_trip(self, tmp_path: Path):
        record = set_style(split(set_text(a_record(), 2, "herramienta,"), 3), 4, {"colour": "#FFD166", "scale": 1.25})
        path = save_transcript(transcript_path(tmp_path, "clip"), record)
        back = load_transcript(path)
        assert back.revision == record.revision == 3
        assert back.meta == record.meta
        assert back.meta_at(2).edited and back.meta_at(3).break_before
        assert back.meta_at(4).style == WordStyle(colour="#FFD166", scale=1.25)
        assert back.extra["en_stale"] is True

    def test_a_meta_that_does_not_match_the_words_cannot_be_constructed(self):
        with pytest.raises(ValueError, match="1 entries for 6 words"):
            a_record(meta=(WordMeta(),))

    def test_a_file_whose_meta_does_not_match_its_words_is_a_transcript_error(self, tmp_path: Path):
        path = save_transcript(transcript_path(tmp_path, "clip"), a_record())
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["meta"] = [{"edited": True}]
        path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(TranscriptError, match="malformed"):
            load_transcript(path)

    def test_an_unknown_format_is_still_refused(self, tmp_path: Path):
        path = tmp_path / "clip.transcript.json"
        path.write_text(json.dumps({"format": 99, "words": []}), encoding="utf-8")
        with pytest.raises(TranscriptError):
            load_transcript(path)


# --- the word-style boundary ----------------------------------------------


class TestWordStyle:
    def test_every_key_is_optional_and_none_means_no_override(self):
        assert parse_word_style(None) is None
        assert parse_word_style({}) is None
        assert parse_word_style({"bold": True}) == WordStyle(bold=True)

    @pytest.mark.parametrize(
        "bad",
        [
            {"colour": "red"},
            {"colour": "#FFF"},
            {"scale": 4.0},
            {"scale": 0.1},
            {"bold": "yes"},
            {"x": 1.5},
            {"font": "Montserrat"},
            "not a dict",
        ],
    )
    def test_bad_values_are_refused_at_the_boundary(self, bad):
        with pytest.raises(WordStyleError):
            parse_word_style(bad)

    def test_the_message_names_the_field(self):
        with pytest.raises(WordStyleError, match="style.scale"):
            parse_word_style({"scale": 9})


# --- set_text --------------------------------------------------------------


class TestSetText:
    def test_one_word_is_retyped_exactly_as_typed(self):
        record = set_text(a_record(), 2, "herramienta,")
        assert record.words[2].text == "herramienta,"
        assert record.words[4].text == "HARAMIENTA"  # untouched
        assert record.revision == 1

    def test_an_edited_word_loses_its_confidence(self):
        record = set_text(a_record(), 2, "herramienta")
        assert record.words[2].probability == 0.0
        assert record.meta_at(2).edited is True

    def test_a_bulk_fix_keeps_each_occurrences_own_case_and_punctuation(self):
        record = set_text(a_record(), 2, "herramienta", all_occurrences=True)
        assert record.words[2].text == "herramienta,"
        assert record.words[4].text == "HERRAMIENTA"
        assert [record.meta_at(i).edited for i in range(6)] == [False, False, True, False, True, False]

    def test_the_count_the_popup_shows_is_the_set_the_bulk_fix_changes(self):
        record = a_record()
        assert occurrences(record, 2) == (2, 4)
        assert occurrences(record, 1) == (1, 3)  # "la" twice

    def test_a_capitalised_occurrence_stays_capitalised(self):
        words = (Word("Haramienta", 0.0, 0.5, 0.4), Word("haramienta.", 0.5, 1.0, 0.4))
        record = set_text(a_record(words=words), 0, "herramienta", all_occurrences=True)
        assert [w.text for w in record.words] == ["Herramienta", "herramienta."]

    def test_the_edit_reaches_the_segment_text_the_txt_is_written_from(self):
        record = set_text(a_record(), 2, "herramienta,")
        assert record.segments[0].text == "Coge la herramienta, la HARAMIENTA grande"

    def test_a_segment_whose_text_is_not_just_its_words_keeps_that_text(self):
        record = a_record(segments=(Segment("Coge la haramienta -- la grande.", 0.0, 2.4, WORDS),))
        edited = set_text(record, 2, "herramienta,")
        assert edited.segments[0].text == "Coge la haramienta -- la grande."
        assert edited.segments[0].words[2].text == "herramienta,"

    def test_a_text_edit_marks_the_saved_english_stale_and_leaves_it_alone(self):
        english = (Word("take", 0.0, 0.4), Word("the", 0.4, 0.5))
        record = set_text(a_record(en_words=english), 2, "herramienta")
        assert record.en_stale is True
        assert record.en_words == english

    @pytest.mark.parametrize("bad", ["", "   ", 7, None])
    def test_an_empty_or_non_text_word_is_refused(self, bad):
        with pytest.raises(ValueError):
            set_text(a_record(), 2, bad)

    @pytest.mark.parametrize("index", [-1, 6, 99, True])
    def test_a_bad_index_is_refused(self, index):
        with pytest.raises(ValueError):
            set_text(a_record(), index, "x")


class TestCaseAndCore:
    @pytest.mark.parametrize(
        "text,parts",
        [
            ("haramienta,", ("", "haramienta", ",")),
            ("¿qué?", ("¿", "qué", "?")),
            ("don't", ("", "don't", "")),
            ("...", ("...", "", "")),
        ],
    )
    def test_punctuation_is_split_off_the_core(self, text, parts):
        assert split_word_text(text) == parts

    @pytest.mark.parametrize(
        "sample,expected",
        [("haramienta", "herramienta"), ("Haramienta", "Herramienta"), ("HARAMIENTA", "HERRAMIENTA")],
    )
    def test_the_occurrences_own_capitalisation_wins(self, sample, expected):
        assert apply_case(sample, "herramienta") == expected

    def test_a_word_that_is_only_punctuation_matches_only_itself(self):
        words = (Word("--", 0.0, 0.2, 0.5), Word("--", 0.3, 0.5, 0.5))
        assert occurrences(a_record(words=words), 0) == (0,)


# --- retime ----------------------------------------------------------------


class TestRetime:
    def test_a_word_is_moved_and_marked(self):
        record = retime(a_record(), 3, start=1.15, end=1.30)
        assert (record.words[3].start, record.words[3].end) == (1.15, 1.30)
        assert record.meta_at(3).retimed is True
        assert record.meta_at(3).edited is False

    def test_the_start_cannot_cross_the_word_in_front(self):
        record = retime(a_record(), 3, start=0.10)
        assert record.words[3].start == pytest.approx(1.10)  # word 2 ends there

    def test_the_end_cannot_cross_the_word_after(self):
        record = retime(a_record(), 3, end=9.0)
        assert record.words[3].end == pytest.approx(1.35)  # word 4 starts there

    def test_a_word_is_never_squeezed_below_the_minimum(self):
        record = retime(a_record(), 5, start=2.399)
        assert record.words[5].end - record.words[5].start >= MIN_WORD_SECONDS

    def test_when_the_neighbours_leave_less_than_the_minimum_they_still_win(self):
        words = (Word("a", 0.0, 0.50, 1.0), Word("b", 0.50, 0.53, 1.0), Word("c", 0.53, 1.0, 1.0))
        record = retime(a_record(words=words), 1, start=0.0, end=9.0)
        assert (record.words[1].start, record.words[1].end) == (0.50, 0.53)

    def test_no_room_at_all_is_a_refusal_not_a_zero_length_word(self):
        words = (Word("a", 0.0, 0.5, 1.0), Word("b", 0.5, 0.5, 1.0), Word("c", 0.5, 1.0, 1.0))
        with pytest.raises(ValueError, match="no room"):
            retime(a_record(words=words), 1, start=0.2)

    def test_a_retime_needs_at_least_one_edge(self):
        with pytest.raises(ValueError, match="start, an end"):
            retime(a_record(), 1)

    @pytest.mark.parametrize("bad", ["soon", -1.0, float("inf")])
    def test_a_bad_time_is_refused(self, bad):
        with pytest.raises(ValueError):
            retime(a_record(), 1, start=bad)

    def test_a_retime_does_not_touch_the_english_or_the_confidence(self):
        record = retime(a_record(), 3, end=1.30)
        assert record.en_stale is False
        assert record.words[3].probability == 0.98

    def test_the_new_timing_reaches_the_segment(self):
        record = retime(a_record(), 3, end=1.30)
        assert record.segments[0].words[3].end == 1.30


# --- split / merge / set_style --------------------------------------------


class TestLineBreaks:
    def test_split_marks_the_word_as_starting_a_line(self):
        record = split(a_record(), 3)
        assert record.meta_at(3).break_before is True
        assert break_indexes(record) == (frozenset({3}), frozenset())

    def test_merge_marks_the_word_as_not_starting_one(self):
        record = merge(a_record(), 3)
        assert record.meta_at(3).no_break_before is True
        assert break_indexes(record) == (frozenset(), frozenset({3}))

    def test_they_are_opposites_on_the_same_index(self):
        record = merge(split(a_record(), 3), 3)
        assert record.meta_at(3) == WordMeta(no_break_before=True)
        back = split(record, 3)
        assert back.meta_at(3) == WordMeta(break_before=True)

    def test_the_first_word_cannot_be_split_off_from_nothing(self):
        with pytest.raises(ValueError, match="first word"):
            split(a_record(), 0)

    def test_an_unedited_record_has_no_markers(self):
        assert break_indexes(a_record()) == (frozenset(), frozenset())


class TestSetStyle:
    def test_a_style_is_stored_and_read_back(self):
        record = set_style(a_record(), 4, {"colour": "#FFD166", "bold": True})
        assert record.meta_at(4).style == WordStyle(colour="#FFD166", bold=True)
        assert [w.text for w, _ in styled_words(record)] == ["HARAMIENTA"]

    def test_none_clears_it(self):
        record = set_style(set_style(a_record(), 4, {"bold": True}), 4, None)
        assert record.meta_at(4).style is None
        assert record.meta is None  # nothing left to remember

    def test_a_style_survives_a_split_of_another_word(self):
        record = split(set_style(a_record(), 4, {"scale": 1.4}), 3)
        assert record.meta_at(4).style == WordStyle(scale=1.4)

    def test_a_bad_style_is_refused_and_nothing_changes(self):
        record = a_record()
        with pytest.raises(WordStyleError):
            set_style(record, 4, {"scale": 9})
        assert record.meta is None


class TestRevision:
    def test_every_operation_bumps_it_by_one(self):
        record = a_record()
        for step in (
            lambda r: set_text(r, 2, "herramienta"),
            lambda r: retime(r, 3, end=1.30),
            lambda r: split(r, 3),
            lambda r: merge(r, 4),
            lambda r: set_style(r, 4, {"bold": True}),
        ):
            before = record.revision
            record = step(record)
            assert record.revision == before + 1

    def test_operations_do_not_mutate_the_record_they_were_given(self):
        record = a_record()
        set_text(record, 2, "herramienta", all_occurrences=True)
        assert record.words == WORDS
        assert record.meta is None
        assert record.revision == 0
