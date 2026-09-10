"""The animation vocabulary: zoom, blur, blink and bounce.

v0.6 shipped three transitions -- fade, rise, slide -- built from ``\\fad``
and ``\\move``. The v0.7 design (2026-09-05 spec, "Held for v0.7", item 2)
adds four more, and they differ from the first three in a way that decides
this module's shape: **they are all built from ``\\t``**, so unlike
``\\fad`` and ``\\move`` two of them compose on one event as long as their
time windows do not overlap. That is why entrance and exit no longer need
the "second one silently wins" rule for anything but the original pair.

Every constant here was measured, not read out of a tag reference. One
word burned onto black at 50fps with the bundled ffmpeg, glyphs measured
per frame (``scratchpad/anim_probe.py``):

  zoom    \\fscx40\\fscy40 -> 100 over 160ms   67px -> 166px
  blur    \\blur12 -> 0 over 200ms             grey-to-solid ratio 8028 -> 0.16
  blink   \\alpha toggled with 10ms ramps      frames go fully dark, not dim
  bounce  \\t accel 0.6 out, 1.7 back          67px -> 195px (117%) -> 166px

Two facts from that run shape the code below. A blurred glyph's bounding
box is ~9px WIDER than its sharp one, because the blur bleeds outward --
so blur is never the effect that decides a margin. And ``\\t``'s third
argument really is an acceleration exponent in this build: below 1 the
motion starts fast and eases out, above 1 it starts slow, which is the
whole difference between a bounce and a linear scale.
"""

from __future__ import annotations

from .ass_format import outline_width
from .schema import Style

# Measured starting scale for zoom and bounce: small enough to read as an
# arrival, large enough that a one-frame event still shows a word.
ZOOM_FROM_PCT = 40
# The overshoot that makes a bounce a bounce. 118% measured as 117% of the
# settled width -- libass rounds the scale, so the tag and the pixels
# agree to within a percent.
BOUNCE_PEAK_PCT = 118
# Fast out, slow back: the two exponents that separate a bounce from a
# linear zoom. Both are \t's third argument.
BOUNCE_OUT_ACCEL = 0.6
BOUNCE_BACK_ACCEL = 1.7
# \blur's radius. 12 is soft enough to be unmistakably a blur at 1080p
# without the word smearing into its neighbours.
BLUR_RADIUS = 12
# MEASURED, and the whole reason blur is not a one-tag effect: with a
# non-zero Outline, \blur never softens the GLYPH. Re-measured 2026-09-10
# with a visible outline on black, counting solid (>200) pixels in the
# letterform at radius 0, 6 and 18:
#
#   outline 0   solid 1413 -> 0    -> 0      the word really does dissolve
#   outline 3   solid 1424 -> 1441 -> 1432   the core never softens at all
#   outline 6   solid 1424 -> 1426 -> 1440   nor here
#
# The outline's own edge does spread (lit 3575 -> 5284 at outline 3), so
# the earlier note here -- "byte-identical output" -- was wrong: it was
# measured with a BLACK outline on a BLACK background, where a softened
# black edge is invisible to the eye and to a pixel diff alike. The
# conclusion it drew is still the right one, for a better reason: an
# entrance that has to open soft and resolve sharp needs the letterform
# itself to soften, and that only happens at \bord0.
BLUR_NEEDS_BORD0 = True
# How many times a blink goes dark. Two reads as a deliberate flash; more
# reads as a fault in the file.
BLINK_PULSES = 2
# The ramp on each blink edge. \t always interpolates, so a hard toggle is
# a very short interpolation -- 10ms is half a frame at 50fps and reads as
# instant, while 0 would collapse the tag.
BLINK_EDGE_MS = 10

# The four added in v0.7. The originals (fade, rise, slide) are resolved
# by the transition_* functions at the foot of this file, which is also
# where their different collision rules live.
ANIMATED_EFFECTS = frozenset({"zoom", "blur", "blink", "bounce"})

# The two that animate \fscx/\fscy, and so cannot share an event with
# the active-word pop: the pop animates the same property on one word's
# span and closes with a hard \fscx100, which cancels a line-level scale
# for every word after it. See render_word.active_word_tags.
SCALE_EFFECTS = frozenset({"zoom", "bounce"})

# What each effect animates. Two tags of the same kind on one event do not
# compose in libass unless they are \t blocks over disjoint windows, which
# is exactly what every effect here emits -- so "t" is a single kind and
# render.py concatenates rather than dropping one of the pair.
KIND_BY_EFFECT = {
    "fade": "fad",
    "rise": "move",
    "slide": "move",
    "zoom": "t",
    "blur": "t",
    "blink": "t",
    "bounce": "t",
}


def _accel(value: float) -> str:
    """``\\t``'s acceleration exponent: 0.6, not 0.60."""
    return f"{value:g}"


def _num(value: float) -> str:
    """A coordinate, formatted exactly as render.py has always formatted
    one. Kept byte-identical on purpose: ``\\move``'s x/y come through here,
    and ``:g`` would write 283.5 where every previous release wrote
    283.50. The look-card drift test caught that within a minute of the
    two builders moving into this module."""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.2f}"


def _blink_chain(start_ms: int, duration_ms: int) -> str:
    """Alpha toggled fully off and back, ``BLINK_PULSES`` times.

    Each edge is a short interpolation rather than a step, because ``\\t``
    has no step form; ``BLINK_EDGE_MS`` is short enough that the measured
    frames are either lit or black, never half-lit.
    """
    steps = BLINK_PULSES * 2
    span = max(1, duration_ms // steps)
    edge = min(BLINK_EDGE_MS, max(1, span // 3))
    parts = []
    for i in range(steps):
        at = start_ms + i * span
        # even step hides, odd step shows -- so the chain always ends visible
        alpha = "&HFF&" if i % 2 == 0 else "&H00&"
        parts.append(f"\\t({at},{at + edge},\\alpha{alpha})")
    # Guarantee the word is opaque once the effect is over, whatever the
    # rounding above did to the last window.
    end = start_ms + duration_ms
    parts.append(f"\\t({end},{end + edge},\\alpha&H00&)")
    return "".join(parts)


def entrance_tag(
    effect: str, duration_ms: int, *, base_pct: int = 100, outline: int = 0
) -> str:
    """The leading tag for one of the four, arriving over ``duration_ms``.

    ``base_pct`` is the word's own size when a per-word scale is in play:
    a zoom on a word already drawn at 130% has to land on 130%, not snap
    back to 100% -- the same reason ``render_word.scaled_transform_tags``
    exists for the pop.
    """
    if effect not in ANIMATED_EFFECTS or duration_ms <= 0:
        return ""
    if effect == "zoom":
        start = max(1, round(ZOOM_FROM_PCT * base_pct / 100))
        return (
            f"\\fscx{start}\\fscy{start}"
            f"\\t(0,{duration_ms},\\fscx{base_pct}\\fscy{base_pct})"
        )
    if effect == "blur":
        # \bord0 up front or nothing blurs at all (see BLUR_NEEDS_BORD0),
        # and it has to STAY 0 for the whole ramp. Animating the border
        # back alongside the blur looked right and measured wrong: the
        # border reached 1.2 within 40ms, and any non-zero border makes
        # the remaining blur inert, so a 300ms soften rendered as a
        # single blurred frame and then a snap. The border is restored in
        # one millisecond at the end instead, by which point the glyph is
        # already sharp and the change is the outline arriving, not the
        # word jumping.
        return (
            f"\\bord0\\blur{BLUR_RADIUS}"
            f"\\t(0,{duration_ms},\\blur0)"
            f"\\t({duration_ms},{duration_ms + 1},\\bord{outline})"
        )
    if effect == "blink":
        return _blink_chain(0, duration_ms)
    # bounce: out past the target, then back onto it
    start = max(1, round(ZOOM_FROM_PCT * base_pct / 100))
    peak = max(1, round(BOUNCE_PEAK_PCT * base_pct / 100))
    out_ms = max(1, round(duration_ms * 0.55))
    return (
        f"\\fscx{start}\\fscy{start}"
        f"\\t(0,{out_ms},{_accel(BOUNCE_OUT_ACCEL)},\\fscx{peak}\\fscy{peak})"
        f"\\t({out_ms},{duration_ms},{_accel(BOUNCE_BACK_ACCEL)},"
        f"\\fscx{base_pct}\\fscy{base_pct})"
    )


def exit_tag(
    effect: str, duration_ms: int, event_ms: int, *, base_pct: int = 100, outline: int = 0
) -> str:
    """The same four, leaving over the last ``duration_ms`` of the event.

    ``\\t``'s times are relative to the event's own start, so the tail is
    pinned by computing ``event_ms - duration_ms`` here -- the same
    reasoning as ``render._exit_tag``'s ``\\move``.
    """
    if effect not in ANIMATED_EFFECTS or duration_ms <= 0 or event_ms <= 0:
        return ""
    duration_ms = min(duration_ms, event_ms)
    t1 = max(0, event_ms - duration_ms)
    if effect == "zoom":
        end = max(1, round(ZOOM_FROM_PCT * base_pct / 100))
        return f"\\t({t1},{event_ms},\\fscx{end}\\fscy{end})"
    if effect == "blur":
        # The border has to be gone before any blur can show, and a plain
        # tag cannot be scheduled -- only \t can. So drop the border over
        # one millisecond at t1, then blur up across the rest of the tail.
        return f"\\t({t1},{t1 + 1},\\bord0)\\t({t1},{event_ms},\\blur{BLUR_RADIUS})"
    if effect == "blink":
        return _blink_chain(t1, duration_ms)
    peak = max(1, round(BOUNCE_PEAK_PCT * base_pct / 100))
    end = max(1, round(ZOOM_FROM_PCT * base_pct / 100))
    swell_ms = max(1, round(duration_ms * 0.4))
    mid = t1 + swell_ms
    return (
        f"\\t({t1},{mid},{_accel(BOUNCE_OUT_ACCEL)},\\fscx{peak}\\fscy{peak})"
        f"\\t({mid},{event_ms},{_accel(BOUNCE_BACK_ACCEL)},\\fscx{end}\\fscy{end})"
    )


def word_animation_tags(
    animation: str,
    duration_ms: int,
    event_ms: int,
    *,
    base_pct: int = 100,
    outline: int = 0,
) -> tuple[str, str]:
    """One word's own animation, as inline ``(open, close)`` tags.

    This is the per-word half of the design's item 3. It is an inline
    chain on the word's text span, exactly as ``active_word`` "shake"
    already is -- not a line-level tag -- so it animates the one word
    rather than the whole caption.

    The close half restores whatever the chain disturbed, so the words
    after it are unaffected: a scale animation has to put ``\\fscx``/
    ``\\fscy`` back, and a blur has to put ``\\blur`` back, or every
    following word inherits it. Note it restores the *line's* 100%, not
    ``base_pct`` -- ``base_pct`` is where this one word lands, and the
    words after it were never scaled.
    """
    if animation in ("", "none") or duration_ms <= 0:
        return "", ""
    if animation == "fade":
        # \fad is line-level and cannot apply to a span; \alpha animated
        # from opaque is the inline equivalent.
        d = min(duration_ms, event_ms) if event_ms > 0 else duration_ms
        return f"\\alpha&HFF&\\t(0,{max(1, d)},\\alpha&H00&)", "\\alpha&H00&"
    open_tags = entrance_tag(animation, duration_ms, base_pct=base_pct, outline=outline)
    if not open_tags:
        return "", ""
    if animation == "blur":
        # restore the look's own border as well as its sharpness
        return open_tags, f"\\blur0\\bord{outline}"
    if animation == "blink":
        return open_tags, "\\alpha&H00&"
    return open_tags, "\\fscx100\\fscy100"


# ---------------------------------------------------------------------------
# the look-level transitions: the original \fad/\move three, plus the four
# above, resolved from a Style. Kept here rather than in render.py so the
# whole vocabulary is in one file and render.py stays about events.
# ---------------------------------------------------------------------------

_RISE_OFFSET_PX = 46
_SLIDE_OFFSET_PX = 160


def tag_kind(tag: str) -> str:
    """Which ASS mechanism a transition tag uses.

    Only ``\\fad`` and ``\\move`` clash when two of a kind land on one
    event. The v0.7 effects are all ``\\t`` chains over disjoint windows,
    so an entrance and an exit of kind "t" compose and must not be sent
    down the merge path in ``render._leading_override``.
    """
    if tag.startswith("\\fad("):
        return "fad"
    return "move" if tag.startswith("\\move(") else "t"


def transition_entrance_tag(style: Style, x: float, y: float, event_ms: int) -> str:
    effect = style.entrance.effect
    # The first per-word event of a card is often shorter than the
    # entrance (a 120ms word under a 160ms rise): \fad/\move past the
    # event's end are simply cut off, so the word never reaches full
    # opacity or its resting position. Clamp, as _exit_tag does.
    duration_ms = min(style.entrance.duration_ms, event_ms)
    if effect == "fade" and duration_ms:
        return f"\\fad({duration_ms},0)"
    if effect in ("rise", "slide") and duration_ms:
        dx, dy = (0, _RISE_OFFSET_PX) if effect == "rise" else (_SLIDE_OFFSET_PX, 0)
        x1, y1 = x + dx, y + dy
        return f"\\move({_num(x1)},{_num(y1)},{_num(x)},{_num(y)},0,{duration_ms})"
    return entrance_tag(effect, duration_ms, outline=outline_width(style))


def transition_exit_tag(style: Style, x: float, y: float, event_ms: int) -> str:
    effect = style.exit.effect
    duration_ms = min(style.exit.duration_ms, event_ms)
    if effect == "fade" and duration_ms:
        return f"\\fad(0,{duration_ms})"
    if effect not in ("rise", "slide"):
        return exit_tag(effect, duration_ms, event_ms, outline=outline_width(style))
    if duration_ms:
        # \move's t1/t2 are relative to *this event's own* start, so the
        # motion is pinned to the tail of the event regardless of how
        # long the event runs -- see the module docstring on why exit only
        # applies to a card's last Dialogue event.
        dx, dy = (0, -_RISE_OFFSET_PX) if effect == "rise" else (-_SLIDE_OFFSET_PX, 0)
        x2, y2 = x + dx, y + dy
        t1 = max(0, event_ms - duration_ms)
        return f"\\move({_num(x)},{_num(y)},{_num(x2)},{_num(y2)},{t1},{event_ms})"
    return ""
