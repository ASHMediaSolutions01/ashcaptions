"""The bridge from an edited transcript record to the renderer
(``app/runner_util.py``, v0.6 section 1): the ``word_styles`` mapping track
B consumes, the line-break markers ``build_cards`` takes, and the rewrite of
``.ass``/``.srt``/``.txt`` the transcript PATCH runs."""
from __future__ import annotations

import logging
from pathlib import Path

from ash_captions import engine
from ash_captions.app import runner_util
from ash_captions.app.runner_util import plan_sound_effects
from ash_captions.app.transcript import TranscriptRecord, merge, set_style, set_text, split
from ash_captions.engine import Segment, Word

WORDS = (
    Word("Coge", 0.0, 0.40, 0.95),
    Word("la", 0.40, 0.55, 0.99),
    Word("haramienta", 0.55, 1.10, 0.41),
    Word("grande", 1.10, 1.60, 0.97),
    Word("de", 1.60, 1.75, 0.98),
    Word("la", 1.75, 1.90, 0.98),
    Word("mesa", 1.90, 2.40, 0.96),
)


def a_record(**kwargs) -> TranscriptRecord:
    return TranscriptRecord(
        language="es",
        words=WORDS,
        segments=(Segment(" ".join(w.text for w in WORDS), 0.0, 2.40, WORDS),),
        **kwargs,
    )


class TestWordStyleMap:
    def test_it_is_keyed_by_the_words_own_timings(self):
        record = set_style(a_record(), 2, {"colour": "#FFD166", "scale": 1.25})
        mapping = runner_util.word_style_map(record)
        assert list(mapping) == [(0.55, 1.10)]
        assert mapping[(0.55, 1.10)].colour == "#FFD166"

    def test_a_record_nobody_styled_maps_to_nothing(self):
        assert runner_util.word_style_map(a_record()) == {}
        assert runner_util.word_style_map(split(a_record(), 3)) == {}


class TestCardBreaks:
    def test_none_until_a_break_is_moved(self):
        assert runner_util.card_breaks(a_record()) is None
        assert runner_util.card_breaks(set_style(a_record(), 2, {"bold": True})) is None

    def test_both_sets_reach_the_engine(self):
        breaks = runner_util.card_breaks(merge(split(a_record(), 3), 5))
        assert breaks.before == frozenset({3})
        assert breaks.not_before == frozenset({5})


class TestRewriteOutputs:
    def test_it_writes_all_three_files_and_returns_the_card_count(self, tmp_path: Path):
        count = runner_util.rewrite_outputs(a_record(), output_dir=tmp_path, stem="clip", preset="CLEAN")
        assert count > 0
        for suffix in (".ass", ".srt", ".txt"):
            assert (tmp_path / f"clip{suffix}").is_file()
        assert len(engine.build_cards(WORDS)) >= 1

    def test_the_srt_and_txt_carry_the_edit(self, tmp_path: Path):
        record = set_text(a_record(), 2, "herramienta")
        runner_util.rewrite_outputs(record, output_dir=tmp_path, stem="clip", preset="CLEAN")
        assert "herramienta" in (tmp_path / "clip.srt").read_text(encoding="utf-8")
        assert (tmp_path / "clip.txt").read_text(encoding="utf-8").strip().endswith("mesa")
        assert "herramienta" in (tmp_path / "clip.txt").read_text(encoding="utf-8")

    def test_a_forced_break_reaches_the_srt(self, tmp_path: Path):
        runner_util.rewrite_outputs(a_record(), output_dir=tmp_path, stem="a", preset="CLEAN")
        assert (tmp_path / "a.srt").read_text(encoding="utf-8").splitlines()[2] == "Coge la haramienta grande"
        runner_util.rewrite_outputs(split(a_record(), 1), output_dir=tmp_path, stem="b", preset="CLEAN")
        assert (tmp_path / "b.srt").read_text(encoding="utf-8").splitlines()[2] == "Coge"

    def test_an_unknown_look_falls_back_instead_of_failing_the_edit(self, tmp_path: Path):
        assert runner_util.rewrite_outputs(a_record(), output_dir=tmp_path, stem="c", preset="NO SUCH LOOK") > 0
        assert (tmp_path / "c.ass").is_file()

    def test_a_dragged_caption_position_is_kept(self, tmp_path: Path):
        runner_util.rewrite_outputs(
            a_record(play_res=(1080, 1920)), output_dir=tmp_path, stem="d", preset="CLEAN", position=(0.5, 0.25)
        )
        assert "\\pos(" in (tmp_path / "d.ass").read_text(encoding="utf-8")

    def test_word_styles_are_passed_only_to_a_renderer_that_takes_them(self, tmp_path: Path, monkeypatch):
        seen: dict = {}

        def fake_write_ass(cards, path, style, *, play_res=None, anchor=None):
            seen.update(play_res=play_res, anchor=anchor)
            Path(path).write_text("[Script Info]\n", encoding="utf-8")
            return Path(path)

        monkeypatch.setattr(engine, "write_ass", fake_write_ass)
        record = set_style(a_record(), 2, {"bold": True})
        runner_util.rewrite_outputs(record, output_dir=tmp_path, stem="e", preset="CLEAN")
        assert "word_styles" not in seen  # the old renderer never sees the keyword

        def newer_write_ass(cards, path, style, *, play_res=None, anchor=None, word_styles=None):
            seen["word_styles"] = word_styles
            Path(path).write_text("[Script Info]\n", encoding="utf-8")
            return Path(path)

        monkeypatch.setattr(engine, "write_ass", newer_write_ass)
        runner_util.rewrite_outputs(record, output_dir=tmp_path, stem="f", preset="CLEAN")
        assert list(seen["word_styles"]) == [(0.55, 1.10)]


# ---------------------------------------------------------------------------
# sound effects (v0.7 section 1)
# ---------------------------------------------------------------------------


class _Sound:
    def __init__(self, **kwargs):
        self.trigger = kwargs.get("trigger", "sentence")
        self.sounds = kwargs.get("sounds", ("pop",))
        self.gain_db = kwargs.get("gain_db", -8.0)
        self.offset_ms = kwargs.get("offset_ms", 0)
        self.min_spacing_seconds = kwargs.get("min_spacing_seconds", 0.35)

    @property
    def enabled(self):
        return self.trigger != "off" and bool(self.sounds)


class _Look:
    def __init__(self, sound=None):
        self.name = "TEST"
        if sound is not None:
            self.sound = sound


SPOKEN = (
    Word(text="Look.", start=0.0, end=0.4),
    Word(text="this", start=1.0, end=1.3),
    Word(text="matters.", start=1.4, end=2.0),
)


def test_a_look_with_no_sound_block_at_all_plans_nothing():
    """A style loaded from a v0.6 file. It must not raise, and it must
    not silently start making noise."""
    assert plan_sound_effects(_Look(), SPOKEN) is None


def test_a_silent_look_plans_nothing():
    assert plan_sound_effects(_Look(_Sound(trigger="off")), SPOKEN) is None


def test_a_look_with_sound_plans_its_hits():
    plan = plan_sound_effects(_Look(_Sound(trigger="sentence", sounds=("pop",))), SPOKEN)
    assert plan is not None
    assert [round(hit.time, 2) for hit in plan.hits] == [0.0, 1.0]
    assert plan.gain_db == -8.0
    assert plan.files and plan.files[0].endswith("pop.wav")


def test_the_keyword_list_the_caller_passes_is_the_one_that_fires():
    plan = plan_sound_effects(
        _Look(_Sound(trigger="keyword", sounds=("impact",))), SPOKEN, keywords=("matters",)
    )
    assert [round(hit.time, 2) for hit in plan.hits] == [1.4]


def test_a_sound_this_build_does_not_carry_costs_the_sound_not_the_job(caplog):
    with caplog.at_level(logging.WARNING):
        assert plan_sound_effects(_Look(_Sound(sounds=("airhorn",))), SPOKEN) is None
    assert "does not carry" in caplog.text


def test_anything_that_throws_inside_costs_the_sound_not_the_job(monkeypatch, caplog):
    """The burn is the deliverable; sound is a flourish. Every failure
    path here has to end in None."""
    monkeypatch.setattr(
        "ash_captions.engine.select_sfx_hits",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with caplog.at_level(logging.WARNING):
        assert plan_sound_effects(_Look(_Sound()), SPOKEN) is None
    assert "burning without them" in caplog.text


def test_an_engine_without_sound_support_plans_nothing(monkeypatch, caplog):
    monkeypatch.delattr("ash_captions.engine.select_sfx_hits", raising=False)
    with caplog.at_level(logging.WARNING):
        assert plan_sound_effects(_Look(_Sound()), SPOKEN) is None
    assert "no sound support" in caplog.text
