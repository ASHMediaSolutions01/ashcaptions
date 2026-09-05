"""Tests for ash_captions.engine.sfx: which words fire, and the graph.

No ffmpeg here -- ``tests/test_engine/test_real_ffmpeg.py`` renders one of
these graphs for real, and the timing was measured against a burned file
before this module was written.
"""
from __future__ import annotations

import pytest

from ash_captions.engine import Word
from ash_captions.engine.sfx import (
    MAX_HITS,
    MIX_FANIN,
    SfxHit,
    SfxPlan,
    SfxTrigger,
    build_plan,
    build_sfx_filter,
    select_sfx_hits,
)


def words(*specs: tuple[str, float]) -> tuple[Word, ...]:
    return tuple(Word(text=text, start=at, end=at + 0.3) for text, at in specs)


SPEECH = words(
    ("Look.", 0.0),
    ("this", 1.0),
    ("is", 1.4),
    ("huge.", 1.8),
    ("Nobody", 3.0),
    ("expected", 3.5),
    ("it.", 4.0),
)


# ---------------------------------------------------------------------------
# choosing the moments
# ---------------------------------------------------------------------------


def test_off_and_an_empty_sound_list_both_mean_no_hits():
    assert select_sfx_hits(SPEECH, trigger="off", sounds=("pop",)) == []
    assert select_sfx_hits(SPEECH, trigger="sentence", sounds=()) == []
    assert select_sfx_hits((), trigger="sentence", sounds=("pop",)) == []


def test_sentence_fires_on_the_first_word_of_each_sentence():
    hits = select_sfx_hits(SPEECH, trigger="sentence", sounds=("pop",))
    assert [round(hit.time, 2) for hit in hits] == [0.0, 1.0, 3.0]
    assert {hit.trigger for hit in hits} == {"sentence"}


def test_keyword_fires_on_the_clients_own_words_only():
    hits = select_sfx_hits(SPEECH, trigger="keyword", sounds=("impact",), keywords=("huge", "nobody"))
    assert [round(hit.time, 2) for hit in hits] == [1.8, 3.0]
    assert {hit.trigger for hit in hits} == {"keyword"}


def test_keyword_matching_ignores_case_and_punctuation():
    """The transcript carries "huge." with its full stop and "Nobody"
    capitalised; a client's keyword list will carry neither."""
    hits = select_sfx_hits(SPEECH, trigger="keyword", sounds=("impact",), keywords=("HUGE", "nobody!"))
    assert [round(hit.time, 2) for hit in hits] == [1.8, 3.0]


def test_both_prefers_sentence_when_a_word_is_also_a_keyword():
    hits = select_sfx_hits(SPEECH, trigger="both", sounds=("pop",), keywords=("nobody",))
    at_three = next(hit for hit in hits if hit.time == 3.0)
    assert at_three.trigger == "sentence"


def test_word_fires_on_every_word():
    hits = select_sfx_hits(SPEECH, trigger="word", sounds=("click",), min_spacing=0.05)
    assert len(hits) == len(SPEECH)
    assert {hit.trigger for hit in hits} == {"word"}


def test_spacing_drops_a_hit_that_crowds_the_one_before():
    crowded = words(("a", 0.0), ("b", 0.1), ("c", 0.2), ("d", 2.0))
    hits = select_sfx_hits(crowded, trigger="word", sounds=("click",), min_spacing=0.5)
    assert [round(hit.time, 2) for hit in hits] == [0.0, 2.0]


def test_the_sound_list_cycles_in_order():
    hits = select_sfx_hits(SPEECH, trigger="word", sounds=("pop", "whoosh"), min_spacing=0.05)
    assert [hit.sound for hit in hits] == ["pop", "whoosh"] * 3 + ["pop"]


def test_offset_moves_every_hit_and_never_before_zero():
    late = select_sfx_hits(SPEECH, trigger="sentence", sounds=("pop",), offset_ms=120)
    assert [round(hit.time, 3) for hit in late] == [0.12, 1.12, 3.12]
    early = select_sfx_hits(SPEECH, trigger="sentence", sounds=("pop",), offset_ms=-200)
    # The first word starts at 0.0, so its hit clamps rather than going negative.
    assert [round(hit.time, 3) for hit in early] == [0.0, 0.8, 2.8]


def test_the_offset_does_not_change_which_words_fire():
    """Spacing is judged on the word's own time. Otherwise nudging the
    whole track by 200ms could silently drop or add a hit, which is the
    opposite of what a fine-tuning control should do."""
    plain = select_sfx_hits(SPEECH, trigger="word", sounds=("click",), min_spacing=0.9)
    nudged = select_sfx_hits(SPEECH, trigger="word", sounds=("click",), min_spacing=0.9, offset_ms=400)
    assert [hit.trigger for hit in plain] == [hit.trigger for hit in nudged]
    assert len(plain) == len(nudged)


def test_a_hit_past_the_end_of_the_video_is_dropped():
    hits = select_sfx_hits(SPEECH, trigger="sentence", sounds=("pop",), video_duration=2.0)
    assert [round(hit.time, 2) for hit in hits] == [0.0, 1.0]


def test_the_number_of_hits_is_capped():
    many = words(*((f"w{n}", n * 0.1) for n in range(MAX_HITS + 50)))
    hits = select_sfx_hits(many, trigger="word", sounds=("click",), min_spacing=0.05)
    assert len(hits) == MAX_HITS


# ---------------------------------------------------------------------------
# the filtergraph
# ---------------------------------------------------------------------------


def graph_for(hits, **kwargs) -> str:
    names = {hit.sound for hit in hits}
    inputs = {name: 1 + n for n, name in enumerate(sorted(names))}
    return build_sfx_filter(hits, sound_inputs=inputs, **kwargs) or ""


def test_no_hits_is_no_graph_at_all():
    """``None`` rather than a pass-through: it is what keeps a burn with
    no sound on ``-c:a copy`` and its dialogue untouched."""
    assert build_sfx_filter([], sound_inputs={"pop": 1}) is None
    assert build_sfx_filter([SfxHit(1.0, "pop", "word")], sound_inputs={}) is None


def test_each_hit_becomes_a_delay_in_milliseconds():
    graph = graph_for([SfxHit(0.0, "pop", "word"), SfxHit(1.234, "pop", "word")])
    assert "adelay=0:all=1" in graph
    assert "adelay=1234:all=1" in graph


def test_a_sound_used_more_than_once_is_split_that_many_times():
    """An ffmpeg input feeds exactly one filter input, so five hits on one
    .wav need five copies of it."""
    hits = [SfxHit(n * 1.0, "pop", "word") for n in range(5)]
    graph = graph_for(hits)
    assert "asplit=5" in graph
    assert graph.count("adelay=") == 5


def test_a_sound_used_once_is_not_split():
    graph = graph_for([SfxHit(1.0, "pop", "word")])
    assert "asplit" not in graph
    assert "[1:a]aformat" in graph


def test_the_gain_is_applied_per_hit_and_clamped():
    assert "volume=-6.00dB" in graph_for([SfxHit(0.0, "pop", "word")], gain_db=-6)
    assert "volume=-40.00dB" in graph_for([SfxHit(0.0, "pop", "word")], gain_db=-999)
    assert "volume=6.00dB" in graph_for([SfxHit(0.0, "pop", "word")], gain_db=99)


def test_the_dialogue_decides_the_length_of_the_track():
    """``duration=first`` on the final mix, with the dialogue first: a
    riser fired on the last word must not stretch the deliverable past
    its own video."""
    graph = graph_for([SfxHit(0.0, "pop", "word")])
    assert graph.endswith("[0:a][sfxbed]amix=inputs=2:duration=first:normalize=0[aout]")


def test_a_video_with_no_audio_gets_a_generated_silence_to_mix_into():
    graph = graph_for([SfxHit(0.0, "pop", "word")], source_audio=False, duration_seconds=12.0)
    assert "anullsrc=r=48000:cl=stereo,atrim=end=12.000[silence]" in graph
    assert graph.endswith("[silence][sfxbed]amix=inputs=2:duration=first:normalize=0[aout]")


def test_a_video_with_no_audio_and_no_known_length_gets_no_graph():
    assert build_sfx_filter(
        [SfxHit(0.0, "pop", "word")], sound_inputs={"pop": 1}, source_audio=False
    ) is None


def test_many_hits_mix_as_a_tree_rather_than_one_enormous_node():
    """One ``amix`` with a thousand inputs allocates a thousand buffers up
    front. The tree keeps any single node at ``MIX_FANIN``."""
    hits = [SfxHit(n * 0.5, "pop", "word") for n in range(MIX_FANIN * 3)]
    graph = graph_for(hits)
    for node in graph.split(";"):
        if "amix=" in node:
            count = int(node.split("amix=inputs=")[1].split(":")[0])
            assert count <= MIX_FANIN, node
    assert graph.count("amix=") > 1
    assert graph.count("adelay=") == MIX_FANIN * 3


def test_every_split_output_is_consumed():
    """A dangling asplit output makes ffmpeg refuse the whole graph."""
    hits = [SfxHit(n * 0.5, "pop", "word") for n in range(7)]
    graph = graph_for(hits)
    produced = {label for label in graph.split("[") if label.startswith("c1_")}
    for label in produced:
        name = label.split("]")[0]
        assert graph.count(f"[{name}]") == 2, name


# ---------------------------------------------------------------------------
# the plan handed to the burn
# ---------------------------------------------------------------------------


def test_a_plan_drops_hits_whose_sound_is_not_on_this_machine():
    hits = [SfxHit(0.0, "pop", "word"), SfxHit(1.0, "gone", "word")]
    plan = build_plan(hits, lambda name: "C:/s/pop.wav" if name == "pop" else None)
    assert [hit.sound for hit in plan.hits] == ["pop"]
    assert plan.files == ("C:/s/pop.wav",)


def test_a_plan_whose_sounds_all_went_missing_is_no_plan():
    """The look asks for a sound this bundle does not carry. That must
    cost the sound, never the client's video."""
    assert build_plan([SfxHit(0.0, "gone", "word")], lambda name: None) is None
    assert build_plan([], lambda name: "x.wav") is None


def test_the_plan_numbers_its_inputs_from_where_the_burn_says():
    plan = SfxPlan(
        hits=(SfxHit(0.0, "pop", "word"), SfxHit(1.0, "whoosh", "word")),
        sounds=(("pop", "a.wav"), ("whoosh", "b.wav")),
    )
    plain = plan.filtergraph(base_input_index=1)
    assert "[1:a]" in plain and "[2:a]" in plain
    # A matte took input 1, so the sounds start at 2.
    behind = plan.filtergraph(base_input_index=2)
    assert "[2:a]" in behind and "[3:a]" in behind


@pytest.mark.parametrize("trigger", sorted(t.value for t in SfxTrigger))
def test_every_trigger_the_schema_allows_is_one_this_module_accepts(trigger):
    from ash_captions.styles.schema import SOUND_TRIGGERS

    assert trigger in SOUND_TRIGGERS
    select_sfx_hits(SPEECH, trigger=trigger, sounds=("pop",), keywords=("huge",))
