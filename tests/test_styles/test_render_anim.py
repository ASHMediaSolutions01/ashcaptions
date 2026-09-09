"""``styles.render_anim`` -- the v0.7 vocabulary (spec "Held for v0.7", 2).

Every effect here was measured before it was written: one word burned onto
black at 50fps with the bundled ffmpeg, glyphs measured per frame. Two of
those measurements are *rules about libass*, not preferences, and the tests
that guard them are marked as such below -- if either is ever "simplified"
away, the control it protects goes back to doing nothing.
"""
from __future__ import annotations

from ash_captions.engine.rules import Card
from ash_captions.engine.transcribe import Word
from ash_captions.styles.ass_format import outline_width
from ash_captions.styles.render import render_ass
from ash_captions.styles.render_anim import (
    ANIMATED_EFFECTS,
    BLUR_RADIUS,
    BOUNCE_PEAK_PCT,
    SCALE_EFFECTS,
    ZOOM_FROM_PCT,
    entrance_tag,
    exit_tag,
    tag_kind,
    word_animation_tags,
)
from ash_captions.styles.schema import TRANSITION_EFFECTS, WORD_ANIMATIONS, Style, WordStyle

WORDS = (Word("AAA", 0.0, 0.4), Word("BBB", 0.4, 0.8), Word("CCC", 0.8, 1.2))
CARD = Card(words=WORDS, start=0.0, end=1.2)


def a_style(**over) -> Style:
    base = {
        "name": "T",
        "active_word": {"effect": "none"},
        "entrance": {"effect": "none", "duration_ms": 0},
        "exit": {"effect": "none", "duration_ms": 0},
    }
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return Style.from_dict(base, check_font=False)


def events_of(ass: str) -> list[str]:
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]


# ---------------------------------------------------------------------------
# the four, as tags
# ---------------------------------------------------------------------------


def test_the_schema_offers_exactly_what_this_module_can_build():
    """A control the renderer cannot honour is a control that lies."""
    assert ANIMATED_EFFECTS <= TRANSITION_EFFECTS
    assert ANIMATED_EFFECTS - {"blur", "blink"} | {"zoom", "bounce"} >= SCALE_EFFECTS


def test_nothing_is_emitted_for_a_zero_duration_or_an_unknown_effect():
    for effect in ANIMATED_EFFECTS:
        assert entrance_tag(effect, 0) == ""
        assert exit_tag(effect, 0, 400) == ""
    assert entrance_tag("rise", 200) == ""  # \move belongs to render.py's half


def test_zoom_starts_small_and_lands_on_the_words_own_size():
    assert entrance_tag("zoom", 200) == (
        f"\\fscx{ZOOM_FROM_PCT}\\fscy{ZOOM_FROM_PCT}\\t(0,200,\\fscx100\\fscy100)"
    )
    # a word already drawn at 130% has to land on 130, not snap back to 100
    tag = entrance_tag("zoom", 200, base_pct=130)
    assert tag.endswith("\\t(0,200,\\fscx130\\fscy130)")
    assert tag.startswith(f"\\fscx{round(ZOOM_FROM_PCT * 1.3)}")


def test_bounce_overshoots_and_then_settles():
    tag = entrance_tag("bounce", 200)
    assert f"\\fscx{BOUNCE_PEAK_PCT}\\fscy{BOUNCE_PEAK_PCT}" in tag
    assert tag.endswith("\\fscx100\\fscy100)")
    # two segments with different acceleration: that is what makes it bounce
    assert tag.count("\\t(") == 2
    assert ",0.6," in tag and ",1.7," in tag


def test_blink_goes_dark_and_always_comes_back():
    tag = entrance_tag("blink", 240)
    assert tag.count("\\alpha&HFF&") == 2  # BLINK_PULSES
    assert tag.endswith("\\alpha&H00&)")   # never leaves the word invisible


def test_an_exit_is_pinned_to_the_tail_of_its_event():
    tag = exit_tag("zoom", 200, 500)
    assert tag == f"\\t(300,500,\\fscx{ZOOM_FROM_PCT}\\fscy{ZOOM_FROM_PCT})"
    # and is clamped when the event is shorter than the effect
    assert exit_tag("zoom", 900, 300).startswith("\\t(0,300,")


# ---------------------------------------------------------------------------
# THE MEASURED RULE: \blur is inert unless the border is gone
# ---------------------------------------------------------------------------


def test_blur_always_drops_the_border_and_puts_it_back():
    """Measured, not assumed: in this libass build ``\\blur`` and ``\\be``
    are a COMPLETE no-op whenever the Style's Outline is non-zero --
    byte-identical output at outline 0.5 and at 6. ``\\bord0`` in the same
    block is the only thing that brings it back. Every legible caption look
    has an outline, so without this the blur effect renders *nothing*.
    """
    tag = entrance_tag("blur", 200, outline=4)
    assert tag.startswith("\\bord0"), "no \\bord0 means no blur at all"
    assert f"\\blur{BLUR_RADIUS}" in tag
    # ...and the border must stay 0 for the WHOLE ramp. Animating it back
    # alongside the blur measured as one blurred frame and then a snap: the
    # border reached 1.2 within 40ms and a non-zero border makes the rest
    # of the blur inert. It is restored in a millisecond at the end.
    assert "\\t(0,200,\\blur0)" in tag
    assert "\\t(200,201,\\bord4)" in tag, "the look's own outline must come back"

    out = exit_tag("blur", 200, 500, outline=4)
    assert "\\bord0" in out and f"\\blur{BLUR_RADIUS}" in out


def test_a_blurred_look_really_carries_bord0_all_the_way_to_the_ass():
    style = a_style(entrance={"effect": "blur", "duration_ms": 200})
    first = events_of(render_ass([CARD], style, play_res=(1080, 1920)))[0]
    assert "\\bord0" in first
    assert f"\\bord{outline_width(style)})" in first


# ---------------------------------------------------------------------------
# THE MEASURED RULE: an inline \fscx cancels a line-level one
# ---------------------------------------------------------------------------


def test_a_scale_entrance_suppresses_the_pop_that_would_cancel_it():
    """With entrance=zoom under active_word=pop, the burn showed word one
    scaling 40->100 while words two and three stood still: the pop's
    closing ``\\fscx100`` applies from its point in the text onward and
    overrides the line-level animation for everything after it. On such an
    event the active word emits colour only.
    """
    style = a_style(
        entrance={"effect": "zoom", "duration_ms": 200},
        active_word={"effect": "pop", "scale": 1.3},
    )
    events = events_of(render_ass([CARD], style, play_res=(1080, 1920)))
    body = events[0].split(",", 9)[-1]
    leading, _, inline = body.partition("}")
    assert "\\fscx40" in leading, "the entrance itself must survive"
    # The leading block's own \fscx100 is the zoom's target and belongs
    # there; what must not appear is a second one in the text, which would
    # take effect from that word onward.
    assert "\\fscx" not in inline, "an inline \\fscx would cancel the zoom mid-line"
    # the middle event has no entrance, so the pop is untouched there
    assert "\\fscx130" in events[1]


def test_the_pop_is_left_alone_when_the_entrance_does_not_scale():
    style = a_style(
        entrance={"effect": "fade", "duration_ms": 200},
        active_word={"effect": "pop", "scale": 1.3},
    )
    first = events_of(render_ass([CARD], style, play_res=(1080, 1920)))[0]
    assert "\\fscx130" in first and "\\fad(200,0)" in first


# ---------------------------------------------------------------------------
# per-word animation (design item 3)
# ---------------------------------------------------------------------------


def test_a_word_animation_is_inline_and_restores_the_line_after_it():
    for animation in sorted(WORD_ANIMATIONS - {"none"}):
        open_tags, close_tags = word_animation_tags(animation, 200, 400, outline=4)
        assert open_tags, animation
        assert close_tags, f"{animation} leaves the line changed for the words after it"
    # a scale animation restores the LINE's 100%, not the word's own size
    assert word_animation_tags("zoom", 200, 400, base_pct=130)[1] == "\\fscx100\\fscy100"
    assert word_animation_tags("blur", 200, 400, outline=4)[1] == "\\blur0\\bord4"


def test_only_the_named_word_animates():
    style = a_style()
    ws = {(WORDS[1].start, WORDS[1].end): WordStyle(animation="zoom", duration_ms=200)}
    events = events_of(render_ass([CARD], style, play_res=(1080, 1920), word_styles=ws))
    # BBB is the active word of the second event, and only there
    assert "\\fscx40" in events[1]
    assert "\\fscx40" not in events[0] and "\\fscx40" not in events[2]
    # ...and it is wrapped around BBB, not the whole line
    assert events[1].index("\\fscx40") > events[1].index("AAA")


def test_a_word_animation_yields_to_a_line_level_scale():
    """Same collision, same answer: the entrance owns \\fscx for that event."""
    style = a_style(entrance={"effect": "zoom", "duration_ms": 200})
    ws = {(WORDS[0].start, WORDS[0].end): WordStyle(animation="bounce")}
    body = events_of(render_ass([CARD], style, play_res=(1080, 1920), word_styles=ws))[0]
    assert f"\\fscx{BOUNCE_PEAK_PCT}" not in body
    # a non-scale animation on the same event is fine, because it does not clash
    ws = {(WORDS[0].start, WORDS[0].end): WordStyle(animation="blink")}
    body = events_of(render_ass([CARD], style, play_res=(1080, 1920), word_styles=ws))[0]
    assert "\\alpha&HFF&" in body


def test_a_word_with_no_animation_changes_nothing():
    style = a_style(active_word={"effect": "pop"})
    plain = render_ass([CARD], style, play_res=(1080, 1920))
    ws = {(WORDS[1].start, WORDS[1].end): WordStyle(animation="none")}
    assert render_ass([CARD], style, play_res=(1080, 1920), word_styles=ws) == plain


# ---------------------------------------------------------------------------
# composition
# ---------------------------------------------------------------------------


def test_the_new_effects_share_one_kind_so_they_compose():
    """``\\fad`` and ``\\move`` clash when two land on one event; the v0.7
    effects are ``\\t`` chains over disjoint windows and do not."""
    assert tag_kind("\\fad(120,0)") == "fad"
    assert tag_kind("\\move(1,2,3,4,0,120)") == "move"
    assert tag_kind(entrance_tag("zoom", 120)) == "t"
    assert tag_kind(entrance_tag("blink", 120)) == "t"


def test_an_entrance_and_an_exit_of_the_same_new_effect_both_survive():
    style = a_style(
        entrance={"effect": "zoom", "duration_ms": 120},
        exit={"effect": "zoom", "duration_ms": 120},
    )
    one_word = Card(words=(WORDS[0],), start=0.0, end=0.4)
    body = events_of(render_ass([one_word], style, play_res=(1080, 1920)))[0]
    assert body.count("\\t(") == 2, "one event must carry both the arrival and the exit"
