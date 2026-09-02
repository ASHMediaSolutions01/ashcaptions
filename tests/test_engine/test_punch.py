"""Tests for punch-in moment selection and the zoompan filter.

Pure logic: no ffmpeg, no video, no probing.
"""

from __future__ import annotations

import pytest

from ash_captions.engine.punch import (
    PunchMode,
    PunchMoment,
    build_zoompan_filter,
    select_punch_moments,
)
from ash_captions.engine.transcribe import Word


def _words(*specs: tuple[str, float, float]) -> list[Word]:
    return [Word(text=t, start=s, end=e, probability=1.0) for t, s, e in specs]


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------


def test_off_mode_selects_nothing() -> None:
    words = _words(("Hello.", 0.0, 0.5), ("World.", 1.0, 1.5))
    assert select_punch_moments(words, mode=PunchMode.OFF) == []


def test_no_words_selects_nothing() -> None:
    assert select_punch_moments([], mode=PunchMode.SENTENCE) == []


def test_first_word_always_starts_a_sentence() -> None:
    moments = select_punch_moments(_words(("Running", 0.0, 0.3)), mode="sentence")
    assert len(moments) == 1
    assert moments[0].start == 0.0
    assert moments[0].trigger == "sentence"


def test_full_stop_starts_a_new_sentence() -> None:
    words = _words(
        ("One.", 0.0, 0.4),
        ("Two", 10.0, 10.4),  # far enough apart that spacing does not suppress
    )
    moments = select_punch_moments(words, mode="sentence", min_spacing=5.0)
    assert [m.start for m in moments] == [0.0, 10.0]


@pytest.mark.parametrize("punctuation", [".", "!", "?"])
def test_every_sentence_ending_counts(punctuation: str) -> None:
    words = _words((f"End{punctuation}", 0.0, 0.4), ("Next", 10.0, 10.4))
    assert len(select_punch_moments(words, mode="sentence", min_spacing=5.0)) == 2


def test_a_long_pause_starts_a_sentence_without_punctuation() -> None:
    """Whisper often transcribes speech without a full stop; a real pause
    is still the start of a new thought."""
    words = _words(("so", 0.0, 0.3), ("anyway", 10.0, 10.5))  # 9.7s gap, no full stop
    moments = select_punch_moments(
        words, mode="sentence", sentence_gap=0.6, min_spacing=5.0
    )
    assert [m.start for m in moments] == [0.0, 10.0]


def test_min_spacing_prevents_nauseating_repetition() -> None:
    """Punching on every sentence in fast dialogue is unwatchable."""
    words = _words(
        ("A.", 0.0, 0.2), ("B.", 1.0, 1.2), ("C.", 2.0, 2.2), ("D.", 20.0, 20.2)
    )
    moments = select_punch_moments(words, mode="sentence", min_spacing=5.0)
    assert [m.start for m in moments] == [0.0, 20.0]


def test_keyword_mode_ignores_sentence_starts() -> None:
    words = _words(("Hello.", 0.0, 0.4), ("free", 10.0, 10.4))
    moments = select_punch_moments(
        words, mode="keyword", keywords=("free",), min_spacing=1.0
    )
    assert [m.trigger for m in moments] == ["keyword"]
    assert moments[0].start == 10.0


def test_keyword_matching_ignores_case_and_punctuation() -> None:
    words = _words(("FREE!", 0.0, 0.4),)
    assert len(select_punch_moments(words, mode="keyword", keywords=("free",))) == 1


def test_both_mode_takes_sentences_and_keywords() -> None:
    """`bonus` follows `some` closely enough not to be a sentence start, so
    the only thing that can trigger it is the keyword rule."""
    words = _words(("Start.", 0.0, 0.4), ("some", 1.0, 1.2), ("bonus", 1.3, 1.6))
    moments = select_punch_moments(
        words, mode="both", keywords=("bonus",), min_spacing=0.2, sentence_gap=0.6
    )
    assert [m.trigger for m in moments] == ["sentence", "sentence", "keyword"]


def test_sentence_wins_when_a_keyword_is_also_a_sentence_start() -> None:
    """A word cannot be punched twice; the sentence rule is checked first."""
    words = _words(("Free", 0.0, 0.4),)
    moments = select_punch_moments(words, mode="both", keywords=("free",))
    assert [m.trigger for m in moments] == ["sentence"]


def test_moment_is_clamped_to_the_end_of_the_video() -> None:
    words = _words(("Last", 9.5, 9.8))
    moments = select_punch_moments(words, mode="sentence", duration=3.0, video_duration=10.0)
    assert moments[0].end == 10.0


def test_a_moment_too_short_to_read_is_dropped() -> None:
    """A punch at the very last instant would be a flicker, not an edit."""
    words = _words(("Last", 9.99, 10.0))
    assert select_punch_moments(
        words, mode="sentence", duration=1.2, video_duration=10.0
    ) == []


def test_duration_is_clamped_to_a_sane_range() -> None:
    words = _words(("Hi", 0.0, 0.3))
    long_punch = select_punch_moments(words, mode="sentence", duration=99.0)
    assert long_punch[0].duration <= 4.0
    short_punch = select_punch_moments(words, mode="sentence", duration=0.001)
    assert short_punch[0].duration >= 0.25


# ---------------------------------------------------------------------------
# filter construction
# ---------------------------------------------------------------------------


def test_no_moments_means_no_filter_at_all() -> None:
    """Returning None rather than an identity filter matters: a zoompan pass
    re-encodes every frame even at zoom 1.0."""
    assert build_zoompan_filter([], width=1080, height=1920, fps=30) is None


def test_zoom_of_one_means_no_filter() -> None:
    moments = [PunchMoment(0.0, 1.0, "sentence")]
    assert build_zoompan_filter(moments, width=1080, height=1920, fps=30, zoom=1.0) is None


def test_filter_carries_the_real_output_size_and_rate() -> None:
    """zoompan must be told its size explicitly; getting it wrong silently
    rescales the whole video."""
    moments = [PunchMoment(1.0, 2.0, "sentence")]
    vf = build_zoompan_filter(moments, width=1080, height=1920, fps=30)
    assert "s=1080x1920" in vf
    assert "fps=30" in vf


def test_filter_uses_input_time_not_frame_count() -> None:
    moments = [PunchMoment(1.5, 2.5, "sentence")]
    vf = build_zoompan_filter(moments, width=720, height=1280, fps=25)
    assert "between(it,1.500,2.500)" in vf


def test_every_moment_appears_in_the_expression() -> None:
    moments = [
        PunchMoment(1.0, 2.0, "sentence"),
        PunchMoment(8.0, 9.0, "keyword"),
        PunchMoment(15.0, 16.0, "sentence"),
    ]
    vf = build_zoompan_filter(moments, width=1080, height=1920, fps=30)
    assert vf.count("between(it,") == 3


def test_zero_fps_falls_back_rather_than_producing_a_broken_filter() -> None:
    """ffprobe reports 0/0 for some streams; fps=0 would be rejected."""
    moments = [PunchMoment(1.0, 2.0, "sentence")]
    vf = build_zoompan_filter(moments, width=1080, height=1920, fps=0)
    assert "fps=30" in vf


def test_bad_dimensions_raise_rather_than_emit_a_broken_filter() -> None:
    moments = [PunchMoment(1.0, 2.0, "sentence")]
    with pytest.raises(ValueError, match="positive"):
        build_zoompan_filter(moments, width=0, height=1920, fps=30)


def test_expression_never_goes_below_one() -> None:
    """A zoom below 1.0 would show black bars around the frame."""
    moments = [PunchMoment(5.0, 6.0, "sentence")]
    vf = build_zoompan_filter(moments, width=1080, height=1920, fps=30)
    assert "max(0," in vf  # the envelope is clamped at zero before scaling
    assert vf.startswith("zoompan=z='1+")
