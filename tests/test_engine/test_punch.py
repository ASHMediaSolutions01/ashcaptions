"""Tests for punch-in moment selection and the zoompan filter.

Pure logic: no ffmpeg, no video, no probing.
"""

from __future__ import annotations

import pytest

from ash_captions.engine.punch import (
    _SUM_LEAF_TERMS,
    PunchMode,
    PunchMoment,
    _balanced_sum,
    build_punch_filter,
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
    """Returning None rather than an identity filter matters: a filter pass
    touches every frame of a 90-minute master."""
    assert build_punch_filter([]) is None
    assert build_zoompan_filter([], width=1080, height=1920, fps=30) is None


def test_zoom_of_one_means_no_filter() -> None:
    moments = [PunchMoment(0.0, 1.0, "sentence")]
    assert build_punch_filter(moments, zoom=1.0) is None


def test_filter_scales_per_frame_and_crops_back_to_the_source_size() -> None:
    """The size is taken from the stream itself (``iw``/``ih`` at config
    time), never from a probe: a rotated phone video swaps the probed
    width and height on decode and would otherwise be cropped wrongly."""
    vf = build_punch_filter([PunchMoment(1.0, 2.0, "sentence")])
    assert vf.startswith("scale=w='iw*(1+0.1200*max(0,")
    assert ":h='ih*ow/iw':eval=frame," in vf
    assert ",crop=w=iw:h=ih:x='iw*(0.1200*max(0," in vf
    assert vf.endswith(":y='x*ih/iw'")
    assert "zoompan" not in vf


def test_filter_uses_frame_timestamps_not_a_frame_counter() -> None:
    """zoompan regenerated timestamps from a constant fps and drifted from
    the copied audio on variable-frame-rate recordings."""
    vf = build_punch_filter([PunchMoment(1.5, 2.5, "sentence")])
    assert "between(t,1.500,2.500)" in vf
    assert "fps=" not in vf and "between(it," not in vf


def test_every_moment_appears_in_the_expression() -> None:
    moments = [
        PunchMoment(1.0, 2.0, "sentence"),
        PunchMoment(8.0, 9.0, "keyword"),
        PunchMoment(15.0, 16.0, "sentence"),
    ]
    vf = build_punch_filter(moments)
    # once in scale's width, once in crop's x offset
    assert vf.count("between(t,") == 6


def test_compat_wrapper_ignores_fps_and_validates_dimensions() -> None:
    moments = [PunchMoment(1.0, 2.0, "sentence")]
    assert build_zoompan_filter(moments, width=1080, height=1920, fps=0) == build_punch_filter(moments)
    with pytest.raises(ValueError, match="positive"):
        build_zoompan_filter(moments, width=0, height=1920, fps=30)


def test_expression_never_goes_below_one() -> None:
    """A zoom below 1.0 would show black bars around the frame."""
    vf = build_punch_filter([PunchMoment(5.0, 6.0, "sentence")])
    assert "max(0," in vf  # the envelope is clamped at zero before scaling
    assert vf.startswith("scale=w='iw*(1+")


def _paren_depth(text: str) -> int:
    depth = deepest = 0
    for ch in text:
        if ch == "(":
            depth += 1
            deepest = max(deepest, depth)
        elif ch == ")":
            depth -= 1
    return deepest


@pytest.mark.parametrize("count", [1, 16, 17, 100, 900, 3000])
def test_long_envelopes_are_summed_in_a_balanced_tree(count: int) -> None:
    """ffmpeg's expression parser recurses once per ``+`` and stops at
    depth 100: a flat sum failed to parse past ~80 moments. Every term
    must still be present, and no ``+`` chain may run long."""
    terms = [f"t{i}" for i in range(count)]
    total = _balanced_sum(terms)
    assert total.count("+") == count - 1
    assert all(term in total for term in terms)
    assert _paren_depth(total) <= 9
    longest_chain = max(len(chunk.split("+")) for chunk in total.replace("(", "|").replace(")", "|").split("|"))
    assert longest_chain <= _SUM_LEAF_TERMS


def test_nine_hundred_moments_build_without_a_flat_chain() -> None:
    moments = [PunchMoment(i * 5.0, i * 5.0 + 1.2, "sentence") for i in range(900)]
    vf = build_punch_filter(moments)
    assert vf.count("between(t,") == 1800
    assert _paren_depth(vf) < 20
