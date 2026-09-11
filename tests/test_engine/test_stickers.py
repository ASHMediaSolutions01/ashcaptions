"""Emoji bursts: when they fire, which one, and the graph that draws them.

The numbers here come from a measurement on a real 1080x1920 reel: 1, 10,
50, 150 and 300 timed ``overlay`` filters all build and run (0.8s to 2.3s
for twelve seconds), and a transparent disc composited cleanly over
footage that already had captions burned in. See the module docstring.
"""

import pytest

from ash_captions.engine.stickers import (
    BURST_SECONDS,
    MAX_BURSTS,
    Burst,
    StickerPlan,
    build_plan,
    select_bursts,
)
from ash_captions.engine.transcribe import Word


def words(*pairs):
    return [Word(text, start, start + 0.3) for text, start in pairs]


class TestWhenTheyFire:
    def test_a_keyword_fires_one(self):
        out = select_bursts(
            words(("this", 0.0), ("free", 1.0)), trigger="keyword",
            emoji=["fire"], keywords=["free"],
        )
        assert [(b.time, b.emoji, b.trigger) for b in out] == [(1.0, "fire", "keyword")]

    def test_nothing_fires_without_an_emoji_to_fire(self):
        assert select_bursts(
            words(("free", 1.0)), trigger="keyword", emoji=[], keywords=["free"]
        ) == []

    def test_off_fires_nothing_whatever_else_is_set(self):
        assert select_bursts(
            words(("free", 1.0)), trigger="off", emoji=["fire"], keywords=["free"]
        ) == []

    def test_the_keyword_match_ignores_case_and_punctuation(self):
        out = select_bursts(
            words(("Free!", 1.0)), trigger="keyword", emoji=["fire"], keywords=["free"]
        )
        assert len(out) == 1

    def test_bursts_are_spaced_further_apart_than_sounds_are(self):
        """A noise every third of a second is a rhythm; a picture every
        third of a second is a mess."""
        out = select_bursts(
            words(("free", 0.0), ("free", 0.5), ("free", 1.0), ("free", 5.0)),
            trigger="keyword", emoji=["fire"], keywords=["free"], min_spacing=2.0,
        )
        assert [b.time for b in out] == [0.0, 5.0]

    def test_nothing_fires_past_the_end_of_the_video(self):
        out = select_bursts(
            words(("free", 1.0), ("free", 99.0)), trigger="keyword",
            emoji=["fire"], keywords=["free"], min_spacing=0.1, video_duration=10.0,
        )
        assert [b.time for b in out] == [1.0]

    def test_a_long_file_cannot_ask_for_more_than_the_measured_ceiling(self):
        many = words(*[("free", i * 3.0) for i in range(400)])
        out = select_bursts(
            many, trigger="keyword", emoji=["fire"], keywords=["free"], min_spacing=0.1
        )
        assert len(out) == MAX_BURSTS


class TestWhichOne:
    def test_two_emoji_alternate(self):
        out = select_bursts(
            words(("a", 0.0), ("a", 3.0), ("a", 6.0), ("a", 9.0)),
            trigger="keyword", emoji=["fire", "star"], keywords=["a"],
        )
        assert [b.emoji for b in out] == ["fire", "star", "fire", "star"]

    def test_one_emoji_repeats(self):
        out = select_bursts(
            words(("a", 0.0), ("a", 3.0)), trigger="keyword", emoji=["fire"], keywords=["a"]
        )
        assert [b.emoji for b in out] == ["fire", "fire"]

    def test_consecutive_bursts_land_on_opposite_sides(self):
        """Two close together on the same side would overlap."""
        out = select_bursts(
            words(("a", 0.0), ("a", 3.0), ("a", 6.0)),
            trigger="keyword", emoji=["fire"], keywords=["a"],
        )
        assert [b.side for b in out] == [-1, 1, -1]


class TestBuildPlan:
    def test_a_missing_emoji_costs_a_sticker_not_the_video(self):
        """The rule sounds already follow: a look naming something this
        build does not ship must not fail the burn."""
        bursts = [Burst(1.0, "fire", -1, "keyword"), Burst(4.0, "nope", 1, "keyword")]
        plan = build_plan(bursts, lambda n: "fire.png" if n == "fire" else None,
                          width=1080, height=1920)
        assert [b.emoji for b in plan.bursts] == ["fire"]
        assert plan.files == ("fire.png",)

    def test_nothing_resolving_gives_no_plan_rather_than_an_empty_one(self):
        assert build_plan([Burst(1.0, "fire", -1, "keyword")], lambda n: None,
                          width=1080, height=1920) is None

    def test_no_bursts_gives_no_plan(self):
        assert build_plan([], lambda n: "x.png", width=1080, height=1920) is None

    def test_each_emoji_is_opened_once_however_often_it_fires(self):
        bursts = [Burst(float(i), "fire", -1, "keyword") for i in range(10)]
        plan = build_plan(bursts, lambda n: "fire.png", width=1080, height=1920)
        assert plan.files == ("fire.png",)


class TestFiltergraph:
    def _plan(self, count=2, **kwargs):
        bursts = tuple(
            Burst(float(i) * 3, "fire" if i % 2 == 0 else "star", -1 if i % 2 == 0 else 1, "keyword")
            for i in range(count)
        )
        return StickerPlan(
            files=("fire.png", "star.png"), bursts=bursts,
            emoji_order=("fire", "star"), **{"width": 1080, "height": 1920, **kwargs},
        )

    def test_each_emoji_is_scaled_once_not_once_per_burst(self):
        graph = self._plan(count=20).filtergraph(base_input_index=1, in_label="[v]")
        assert graph.count("scale=") == 2

    def test_every_burst_gets_its_own_timed_overlay(self):
        graph = self._plan(count=5).filtergraph(base_input_index=1, in_label="[v]")
        assert graph.count("overlay=") == 5
        assert graph.count("enable='between(t,") == 5

    def test_a_burst_is_on_for_its_own_moment_only(self):
        graph = self._plan(count=1).filtergraph(base_input_index=1, in_label="[v]")
        assert "between(t,0.000,%.3f)" % BURST_SECONDS in graph

    def test_the_inputs_are_numbered_from_where_the_burn_says(self):
        """The burn owns input numbering; it changes when a matte is in
        play, which is why the plan is told rather than assuming."""
        graph = self._plan().filtergraph(base_input_index=3, in_label="[v]")
        assert "[3:v]scale=" in graph and "[4:v]scale=" in graph

    def test_the_chain_ends_at_the_label_the_caller_asked_for(self):
        graph = self._plan(count=3).filtergraph(
            base_input_index=1, in_label="[v]", out_label="done"
        )
        assert graph.endswith("[done]")
        assert "[done]" not in graph[: -len("[done]")]

    def test_it_starts_from_the_label_the_caller_gave(self):
        graph = self._plan(count=2).filtergraph(base_input_index=1, in_label="[cap]")
        assert "[cap][e0_0]overlay=" in graph

    def test_a_plan_with_no_bursts_draws_nothing(self):
        empty = StickerPlan(files=("a.png",), bursts=(), emoji_order=("fire",))
        assert empty.filtergraph(base_input_index=1, in_label="[v]") is None

    def test_a_burst_naming_an_emoji_not_in_the_plan_is_skipped(self):
        plan = StickerPlan(
            files=("fire.png",), bursts=(Burst(1.0, "ghost", -1, "keyword"),),
            emoji_order=("fire",),
        )
        assert plan.filtergraph(base_input_index=1, in_label="[v]") is None

    def test_the_sticker_rises_rather_than_sitting_still(self):
        graph = self._plan(count=1).filtergraph(base_input_index=1, in_label="[v]")
        assert "min(1,(t-" in graph

    def test_sides_put_the_two_stickers_at_different_x(self):
        graph = self._plan(count=2).filtergraph(base_input_index=1, in_label="[v]")
        xs = [step.split("overlay=")[1].split(":")[0] for step in graph.split(";") if "overlay=" in step]
        assert xs[0] != xs[1]


class TestSize:
    def test_a_sticker_is_a_share_of_the_short_side(self):
        assert StickerPlan(width=1080, height=1920).size_px == pytest.approx(216, abs=2)

    def test_a_tiny_frame_still_gets_a_visible_sticker(self):
        assert StickerPlan(width=160, height=120).size_px >= 48


class TestEveryStreamIsConsumedOnce:
    """An ffmpeg filter output pad feeds exactly one input.

    A graph naming the same scaled emoji twice hands it to the first
    consumer and the second overlay draws *nothing* -- no error, no
    warning, exit code 0. Burning the real reel is what found it: the
    fourth burst reused the first one's emoji, read perfectly in the
    graph, and was simply absent from the video.
    """

    def _graph(self, emoji_per_burst):
        bursts = tuple(
            Burst(float(i) * 3, name, -1, "keyword")
            for i, name in enumerate(emoji_per_burst)
        )
        order = tuple(dict.fromkeys(emoji_per_burst))
        plan = StickerPlan(
            files=tuple("%s.png" % n for n in order), bursts=bursts,
            emoji_order=order, width=1080, height=1920,
        )
        return plan.filtergraph(base_input_index=1, in_label="[0:v]", out_label="out")

    def _labels_consumed(self, graph):
        out = []
        for step in graph.split(";"):
            if "overlay=" not in step:
                continue
            head = step.split("overlay=")[0]
            out += [chunk for chunk in head.replace("[", " [").split() if chunk.startswith("[e")]
        return out

    def test_an_emoji_used_twice_is_split_into_two_streams(self):
        graph = self._graph(["fire", "star", "fire"])
        assert "split=2[e0_0][e0_1]" in graph

    def test_an_emoji_used_once_is_not_split(self):
        assert "split" not in self._graph(["fire", "star"])

    def test_no_scaled_stream_is_consumed_more_than_once(self):
        consumed = self._labels_consumed(self._graph(["fire", "star", "fire", "fire", "star"]))
        assert len(consumed) == len(set(consumed)), consumed

    def test_every_stream_the_graph_makes_is_actually_used(self):
        """A split that produces more outputs than are consumed leaves an
        unconnected pad, which ffmpeg refuses outright."""
        graph = self._graph(["fire", "fire", "star"])
        produced = set()
        for step in graph.split(";"):
            if "overlay=" in step:
                continue
            produced.update(
                chunk for chunk in step.replace("[", " [").split() if chunk.startswith("[e")
            )
        assert produced == set(self._labels_consumed(graph))

    def test_one_emoji_firing_many_times_splits_that_many_ways(self):
        graph = self._graph(["fire"] * 5)
        assert "split=5" in graph
        assert len(set(self._labels_consumed(graph))) == 5
