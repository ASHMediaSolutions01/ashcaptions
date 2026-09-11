"""Telling two voices apart.

The numbers asserted here are measurements from the studio's own 4:48
Spanish interview, not invented ones -- see the module docstring in
``engine/diarise.py``.

The one that matters most is the split-half check. A hand-written fbank
front-end is the kind of thing that produces *plausible* features from
wrong arithmetic: the embeddings still come out, they still cluster, and
the result still looks like an answer. The only thing that catches it is
demanding that a voice match itself better than it matches anyone else.
"""

import numpy as np
import pytest

from ash_captions.engine.diarise import (
    FRAME_LENGTH,
    NUM_MEL,
    SAMPLE_RATE,
    Diarisation,
    Turn,
    fbank,
    label_words,
    mel_filters,
    normalise,
    one_speaker_only,
    separation,
    speaker_name,
    split_two,
    turns_from_labels,
)


class Word:
    def __init__(self, start, end):
        self.start, self.end = start, end


def tone(hz, seconds=1.0, rate=SAMPLE_RATE, harmonics=(1, 2, 3)):
    """A voiced-sounding signal: a pitch plus harmonics, not a pure sine."""
    t = np.arange(int(seconds * rate)) / rate
    out = sum(np.sin(2 * np.pi * hz * h * t) / h for h in harmonics)
    return (out / np.abs(out).max()).astype(np.float32)


class TestFbank:
    def test_it_produces_eighty_bins(self):
        feats = fbank(tone(120.0, 0.5))
        assert feats.shape[1] == NUM_MEL

    def test_frames_advance_every_ten_milliseconds(self):
        feats = fbank(tone(120.0, 1.0))
        # 1s at 16kHz: (16000 - 400) // 160 + 1 frames
        assert len(feats) == (SAMPLE_RATE - FRAME_LENGTH) // 160 + 1

    def test_it_is_mean_normalised_over_time(self):
        """WeSpeaker's own front-end subtracts the mean; without it the
        embeddings carry the room instead of the voice."""
        feats = fbank(tone(150.0, 1.0))
        assert np.allclose(feats.mean(axis=0), 0.0, atol=1e-4)

    def test_a_signal_shorter_than_one_frame_is_refused_not_padded(self):
        assert fbank(np.zeros(FRAME_LENGTH - 1, dtype=np.float32)) is None

    def test_the_bins_follow_the_pitch(self):
        """A front-end that returned the same thing for every input would
        pass every other test here.

        Comparing the time-mean of two signals cannot show this: the mean
        normalisation above zeroes it by construction, so both come back
        as zero however different the audio was. A pitch that *changes
        part-way* survives it -- the low half and the high half of one
        clip must light up different bins.
        """
        clip = np.concatenate([tone(110.0, 0.6), tone(440.0, 0.6)])
        feats = fbank(clip)
        half = len(feats) // 2
        low_half = feats[: half - 5].mean(axis=0)
        high_half = feats[half + 5:].mean(axis=0)
        assert float(np.abs(low_half - high_half).max()) > 1.0
        # and the energy really does move up the bank, not just move
        assert int(np.argmax(high_half)) > int(np.argmax(low_half))


class TestMelFilters:
    def test_the_bank_covers_every_bin_it_should(self):
        filters = mel_filters()
        assert filters.shape[0] == NUM_MEL
        assert filters.sum() > 0

    def test_filters_climb_the_spectrum(self):
        """Each filter's peak must sit above the previous one's, or the
        bank is not a mel scale."""
        filters = mel_filters()
        peaks = filters.argmax(axis=1)
        assert np.all(np.diff(peaks) >= 0)


class TestNormalise:
    def test_a_vector_comes_back_unit_length(self):
        assert float(np.linalg.norm(normalise(np.array([3.0, 4.0])))) == pytest.approx(1.0)

    def test_a_zero_vector_does_not_divide_by_zero(self):
        assert np.all(np.isfinite(normalise(np.zeros(8))))


class TestSplitTwo:
    def _two_voices(self, n=6, spread=0.9):
        rng = np.random.default_rng(0)
        a = normalise(rng.normal(size=32))
        b = normalise(rng.normal(size=32))
        rows = []
        for i in range(n):
            base = a if i % 2 == 0 else b
            rows.append(normalise(base * spread + rng.normal(size=32) * (1 - spread)))
        return np.array(rows, dtype=np.float32)

    def test_two_voices_come_back_as_two_groups(self):
        labels = split_two(self._two_voices())
        assert set(labels.tolist()) == {0, 1}
        assert labels.tolist() == [0, 1, 0, 1, 0, 1]

    def test_the_first_speaker_is_always_labelled_zero(self):
        """An eigenvector's sign is arbitrary. Without pinning it, the same
        audio labels the same person 0 on one run and 1 on the next."""
        vectors = self._two_voices()
        assert split_two(vectors)[0] == 0
        assert split_two(vectors[::-1])[0] == 0

    def test_one_vector_is_one_speaker(self):
        assert split_two(np.array([[1.0, 0.0]], dtype=np.float32)).tolist() == [0]

    def test_no_vectors_does_not_raise(self):
        assert split_two(np.zeros((0, 4), dtype=np.float32)).tolist() == []


class TestSeparation:
    def test_two_real_voices_separate_clearly(self):
        rng = np.random.default_rng(1)
        a, b = normalise(rng.normal(size=32)), normalise(rng.normal(size=32))
        vectors = np.array([a, a, b, b], dtype=np.float32)
        assert separation(vectors, np.array([0, 0, 1, 1])) > 0.5

    def test_one_voice_cut_in_half_barely_separates(self):
        rng = np.random.default_rng(2)
        a = normalise(rng.normal(size=32))
        vectors = np.array([a, a, a, a], dtype=np.float32)
        assert separation(vectors, np.array([0, 0, 1, 1])) == pytest.approx(0.0, abs=1e-5)

    def test_a_single_group_scores_zero_rather_than_raising(self):
        vectors = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
        assert separation(vectors, np.array([0, 0])) == 0.0


class TestOneSpeakerOnly:
    def test_the_measured_real_split_is_two_people(self):
        """0.557 on the reference interview."""
        assert not one_speaker_only(0.557)

    def test_a_monologue_diced_in_half_is_caught(self):
        """Clustering told to find two always finds two. A speaker change
        in the middle of one person talking is worse than no label."""
        assert one_speaker_only(0.02)

    def test_the_threshold_sits_above_a_voice_matching_itself(self):
        """The measured margin of one voice against itself was 0.169, and
        a real two-speaker split was 0.557. The line belongs between."""
        assert one_speaker_only(0.10)
        assert not one_speaker_only(0.20)


class TestTurns:
    def test_neighbouring_spans_of_one_voice_become_one_turn(self):
        """The VAD splits on breath, not on who is speaking. "Ana: ... /
        Ana: ..." across a pause is noise."""
        turns = turns_from_labels([(0.0, 2.0), (2.2, 4.0), (4.5, 6.0)], [0, 0, 1])
        assert [(t.start, t.end, t.speaker) for t in turns] == [
            (0.0, 4.0, 0), (4.5, 6.0, 1),
        ]

    def test_alternating_voices_stay_separate(self):
        turns = turns_from_labels([(0.0, 2.0), (2.0, 4.0)], [0, 1])
        assert len(turns) == 2

    def test_a_mismatch_between_spans_and_labels_is_loud(self):
        with pytest.raises(ValueError):
            turns_from_labels([(0.0, 1.0), (1.0, 2.0)], [0])


class TestDiarisation:
    DIAR = Diarisation(turns=(Turn(0.0, 5.0, 0), Turn(6.0, 12.0, 1)))

    def test_it_knows_who_is_talking_when(self):
        assert self.DIAR.speaker_at(2.0) == 0
        assert self.DIAR.speaker_at(7.0) == 1

    def test_a_gap_between_turns_has_nobody_in_it(self):
        assert self.DIAR.speaker_at(5.5) is None

    def test_it_totals_each_speaker(self):
        assert self.DIAR.seconds_by_speaker() == {0: 5.0, 1: 6.0}

    def test_an_empty_result_is_usable_rather_than_special(self):
        """One voice is a real answer, and every caller sees the same
        shape for it as for two."""
        assert Diarisation().speakers == ()
        assert Diarisation().speaker_at(1.0) is None


class TestLabelWords:
    def test_a_word_belongs_to_whoever_was_talking_through_it(self):
        diar = Diarisation(turns=(Turn(0.0, 5.0, 0), Turn(5.0, 10.0, 1)))
        assert label_words([Word(1.0, 2.0), Word(6.0, 7.0)], diar) == (0, 1)

    def test_a_word_straddling_a_change_goes_to_the_larger_share(self):
        """By its middle, not its start -- the start hands every first
        word of a turn to the previous speaker."""
        diar = Diarisation(turns=(Turn(0.0, 5.0, 0), Turn(5.0, 10.0, 1)))
        assert label_words([Word(4.6, 5.8)], diar) == (1,)

    def test_a_word_in_silence_has_no_speaker(self):
        diar = Diarisation(turns=(Turn(0.0, 2.0, 0),))
        assert label_words([Word(3.0, 3.5)], diar) == (None,)


class TestNames:
    def test_the_default_is_honest_about_being_a_number(self):
        assert speaker_name(0) == "Speaker 1"
        assert speaker_name(1) == "Speaker 2"

    def test_an_editor_can_rename_them(self):
        assert speaker_name(0, {0: "Ana"}) == "Ana"

    def test_a_partial_rename_leaves_the_rest_numbered(self):
        assert speaker_name(1, {0: "Ana"}) == "Speaker 2"
