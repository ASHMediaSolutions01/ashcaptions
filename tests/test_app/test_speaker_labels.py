"""Speaker names as a job: the option, the degrading, and the .srt.

The engine's own decisions are tested in ``test_engine/test_diarise.py``.
What matters here is that asking for names never damages a transcript
that was correct without them -- the stage degrades where the reel stage
fails, and the reason is in ``runner_speakers``'s docstring.
"""

from ash_captions.app import runner_speakers
from ash_captions.engine import diarise as D
from ash_captions.engine.writers import render_srt
from ash_captions.pipeline.db import JobOptions


class Card:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def options(**kwargs):
    base = dict(language="es", dialect=None, preset="POP", burn=False, translate=False)
    base.update(kwargs)
    return JobOptions(**base)


CARDS = [Card(0.0, 2.0, "hola a todos"), Card(6.0, 8.0, "gracias por venir")]


class TestTheOption:
    def test_it_is_off_unless_asked_for(self):
        assert options().speaker_labels is False

    def test_it_survives_the_database(self):
        assert JobOptions.from_json(options(speaker_labels=True).to_json()).speaker_labels is True

    def test_a_row_written_before_it_existed_still_loads(self):
        raw = '{"language": "es", "preset": "POP", "burn": false, "translate": false}'
        assert JobOptions.from_json(raw).speaker_labels is False


class TestItDegrades:
    """A transcript is correct without names. Nothing here may break one."""

    def test_a_job_that_did_not_ask_gets_no_names_and_no_work(self, tmp_path):
        assert runner_speakers.card_speakers(
            options(), tmp_path / "nope.mp4", CARDS,
            models_dir=tmp_path, ffmpeg_path="ffmpeg",
        ) is None

    def test_a_model_that_will_not_download_writes_the_transcript_anyway(self, tmp_path):
        """Offline, or a blocked host. The .srt still has to come out."""
        assert runner_speakers.card_speakers(
            options(speaker_labels=True), tmp_path / "nope.mp4", CARDS,
            models_dir=tmp_path / "no-model-here", ffmpeg_path="definitely-not-ffmpeg",
        ) is None

    def test_no_cards_is_not_an_error(self, tmp_path):
        assert runner_speakers.card_speakers(
            options(speaker_labels=True), tmp_path / "nope.mp4", [],
            models_dir=tmp_path, ffmpeg_path="ffmpeg",
        ) is None


class TestCardsGetTheRightName:
    def _names(self, cards, turns):
        result = D.Diarisation(turns=tuple(turns))
        names = []
        for card in cards:
            who = result.speaker_at((card.start + card.end) / 2.0)
            names.append(None if who is None else D.speaker_name(who))
        return runner_speakers._carry_across_gaps(names)

    def test_each_card_takes_the_voice_that_was_talking(self):
        names = self._names(CARDS, [D.Turn(0.0, 4.0, 0), D.Turn(5.0, 9.0, 1)])
        assert names == ["Speaker 1", "Speaker 2"]

    def test_a_card_in_a_pause_keeps_the_previous_name(self):
        """The VAD trims silence, so the tail of a sentence can fall
        outside every turn. Left unnamed, the next card re-announces a
        speaker who never stopped."""
        cards = [Card(0.0, 2.0, "one"), Card(4.2, 4.6, "two"), Card(5.2, 6.0, "three")]
        names = self._names(cards, [D.Turn(0.0, 4.0, 0), D.Turn(5.0, 9.0, 0)])
        assert names == ["Speaker 1", "Speaker 1", "Speaker 1"]

    def test_a_card_before_anyone_speaks_has_no_name_to_carry(self):
        names = self._names([Card(0.0, 0.5, "x")], [D.Turn(5.0, 9.0, 0)])
        assert names == [None]


class TestTheSrt:
    def test_a_name_is_printed_when_the_speaker_turns(self):
        out = render_srt(CARDS, ["Speaker 1", "Speaker 2"])
        assert "Speaker 1: hola a todos" in out
        assert "Speaker 2: gracias por venir" in out

    def test_a_name_is_not_repeated_on_every_card(self):
        """Four hundred cards each prefixed "Ana:" is unreadable, and no
        podcast transcript is written that way."""
        cards = [Card(i, i + 1, "line %d" % i) for i in range(4)]
        out = render_srt(cards, ["Ana"] * 4)
        assert out.count("Ana:") == 1
        assert "line 3" in out

    def test_it_names_the_speaker_again_when_they_come_back(self):
        cards = [Card(i, i + 1, "line %d" % i) for i in range(4)]
        out = render_srt(cards, ["Ana", "Luis", "Ana", "Ana"])
        assert out.count("Ana:") == 2
        assert out.count("Luis:") == 1

    def test_no_names_leaves_the_srt_exactly_as_it_was(self):
        """Every existing transcript must be byte-identical."""
        assert render_srt(CARDS, None) == render_srt(CARDS)
        assert "Speaker" not in render_srt(CARDS)

    def test_an_unnamed_card_among_named_ones_is_left_alone(self):
        out = render_srt(CARDS, [None, "Speaker 2"])
        assert out.splitlines()[2] == "hola a todos"


class TestNoChangeMidSentence:
    """The defect this prevents was real, and in the .srt:

        Speaker 1: Son una herramienta para
        Speaker 2: autores que deciden liberar

    One sentence, two people's names on it. The voice clustering is
    right -- the reference interview separated at 0.557 -- but the VAD's
    boundaries are breaths, so a turn can start mid-clause.
    """

    def _hold(self, names, texts):
        return runner_speakers.hold_until_a_sentence_ends(
            list(names), [Card(i, i + 1, t) for i, t in enumerate(texts)]
        )

    def test_a_change_mid_sentence_is_held_back(self):
        assert self._hold(
            ["Speaker 1", "Speaker 2"], ["Son una herramienta para", "autores que deciden"]
        ) == ["Speaker 1", "Speaker 1"]

    def test_a_change_after_a_full_stop_is_allowed(self):
        assert self._hold(
            ["Speaker 1", "Speaker 2"], ["a todos los derechos.", "Sino que comparte"]
        ) == ["Speaker 1", "Speaker 2"]

    def test_a_question_mark_ends_a_sentence_too(self):
        assert self._hold(
            ["Speaker 1", "Speaker 2"], ["por que un autor?", "Uno de los"]
        ) == ["Speaker 1", "Speaker 2"]

    def test_a_comma_does_not_end_one(self):
        assert self._hold(
            ["Speaker 1", "Speaker 2"], ["su obra,", "propone una"]
        ) == ["Speaker 1", "Speaker 1"]

    def test_trailing_space_does_not_hide_the_full_stop(self):
        assert self._hold(
            ["Speaker 1", "Speaker 2"], ["derechos.   ", "Sino que"]
        ) == ["Speaker 1", "Speaker 2"]

    def test_the_change_lands_at_the_next_sentence_end_instead_of_vanishing(self):
        assert self._hold(
            ["Speaker 1", "Speaker 2", "Speaker 2"],
            ["una herramienta para", "autores liberan su obra.", "Por que un autor"],
        ) == ["Speaker 1", "Speaker 1", "Speaker 2"]

    def test_the_first_card_is_never_held_back(self):
        assert self._hold(["Speaker 2"], ["sin punto"]) == ["Speaker 2"]

    def test_an_empty_transcript_is_not_an_error(self):
        assert self._hold([], []) == []
