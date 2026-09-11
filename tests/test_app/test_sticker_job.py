"""Emoji bursts as a *job* sees them (``app/runner_video.build_stickers``).

The engine's own rules -- when a burst fires, which emoji, and the filter
graph -- are tested in ``tests/test_engine/test_stickers.py``. What is
tested here is the seam: the plan comes off the **look** as of v0.9,
where it used to come off settings.json, and every failure path costs the
editor a picture rather than a video.
"""
from __future__ import annotations

import logging

from ash_captions.app.runner_video import build_stickers
from ash_captions.engine import Word


class _Emoji:
    """The shape ``styles.schema.EmojiBursts`` presents."""

    def __init__(self, trigger="keyword", emoji=("fire",), min_spacing_seconds=2.5):
        self.trigger = trigger
        self.emoji = tuple(emoji)
        self.min_spacing_seconds = min_spacing_seconds

    @property
    def enabled(self):
        return self.trigger != "off" and bool(self.emoji)


class _Look:
    def __init__(self, emoji=None):
        self.name = "TEST"
        if emoji is not None:
            self.emoji = emoji


class _Info:
    width, height = 1080, 1920


SPOKEN = (
    Word(text="Look.", start=0.0, end=0.4),
    Word(text="this", start=1.0, end=1.3),
    Word(text="matters.", start=4.0, end=4.6),
)


def plan_for(look, **kwargs):
    return build_stickers(look, SPOKEN, _Info(), duration_seconds=10.0, **kwargs)


def test_a_look_with_no_emoji_block_at_all_plans_nothing():
    """A style loaded from a v0.8 file. It must not raise, and it must
    not silently start throwing pictures."""
    assert plan_for(_Look()) is None


def test_a_bare_look_plans_nothing():
    assert plan_for(_Look(_Emoji(trigger="off"))) is None


def test_a_look_with_no_emoji_picked_plans_nothing():
    assert plan_for(_Look(_Emoji(trigger="sentence", emoji=()))) is None


def test_the_plan_comes_off_the_look_not_the_settings():
    """The whole point of v0.9's move. A settings object carrying the old
    fields must not be able to turn emoji on for a look that is bare."""
    class _OldSettings:
        emoji_trigger = "sentence"
        emoji = ("fire",)
        emoji_min_spacing_seconds = 0.5

    assert plan_for(_OldSettings()) is None


def test_a_look_with_emoji_plans_its_bursts():
    plan = plan_for(_Look(_Emoji(trigger="sentence", emoji=("fire",))))
    assert plan is not None
    assert [round(b.time, 2) for b in plan.bursts] == [0.0, 4.0]
    assert plan.files and plan.files[0].endswith("fire.png")


def test_the_keyword_list_the_caller_passes_is_the_one_that_fires():
    """Still the client's one shared list, handed down by the runner --
    the same list punch-in and the sounds use."""
    plan = plan_for(_Look(_Emoji(trigger="keyword")), keywords=("matters",))
    assert [round(b.time, 2) for b in plan.bursts] == [4.0]


def test_the_looks_own_gap_is_what_spaces_them():
    close = (Word("free", 0.0, 0.3), Word("free", 1.0, 1.3))
    wide = build_stickers(
        _Look(_Emoji(trigger="keyword", min_spacing_seconds=5.0)),
        close, _Info(), keywords=("free",), duration_seconds=10.0,
    )
    tight = build_stickers(
        _Look(_Emoji(trigger="keyword", min_spacing_seconds=0.5)),
        close, _Info(), keywords=("free",), duration_seconds=10.0,
    )
    assert len(wide.bursts) == 1
    assert len(tight.bursts) == 2


def test_the_sticker_is_sized_from_the_probed_frame():
    plan = plan_for(_Look(_Emoji(trigger="sentence")))
    assert plan.width == 1080 and plan.height == 1920


def test_an_emoji_this_build_does_not_ship_costs_the_picture_not_the_job():
    assert plan_for(_Look(_Emoji(trigger="sentence", emoji=("unicorn",)))) is None


def test_anything_that_throws_inside_costs_the_picture_not_the_job(monkeypatch, caplog):
    """The burn is the deliverable; a sticker is a flourish on top of it.
    Every failure path here has to end in None."""
    monkeypatch.setattr(
        "ash_captions.engine.select_bursts",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with caplog.at_level(logging.WARNING):
        assert plan_for(_Look(_Emoji(trigger="sentence"))) is None
    assert "burning without them" in caplog.text


def test_nothing_fires_past_the_end_of_the_video():
    short = build_stickers(
        _Look(_Emoji(trigger="sentence")), SPOKEN, _Info(), duration_seconds=2.0
    )
    assert [round(b.time, 2) for b in short.bursts] == [0.0]
