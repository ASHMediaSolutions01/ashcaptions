"""Who is speaking: turning voices into speaker turns.

The decisions here are pure, so they can be tested without an audio file
or a model; ``diarise_run.py`` does the loading, the VAD and the ONNX.

WHAT THIS DOES AND DOES NOT DO, because the difference was measured and
it matters. Diarisation says *when* each speaker talks. It says nothing
about *where* they are on screen, so it cannot aim the 9:16 reel's crop
at whoever is speaking. That link needs active-speaker detection, and
the cheap version of it does not work: on the studio's own interview,
frame-to-frame mouth motion correlated with speech loudness at **+0.07**
-- measured three times, the last with head boxes confirmed by eye on a
shot with no cut in it. Two faces in a two-shot move *together* (+0.65),
because what frame differencing mostly sees is the camera and the
lighting. So the reel still follows the largest person, and this module
is for labelling a podcast, not for steering a crop.

Measured on the same interview, 180 seconds, with the WeSpeaker
voxceleb-resnet34 embedding:

  * a voice matches itself across the halves of one turn at cosine
    0.655, against 0.486 for halves of different turns -- a margin of
    0.169, which is what says the hand-written fbank front-end is right
    rather than plausible-looking;
  * clustered into two, within-speaker similarity is 0.828 against 0.270
    across, a separation of 0.557;
  * the turn structure came out as an interview really looks -- one
    voice with 4 turns and 23s, the other with 13 turns and 147s.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000

# Kaldi's frame geometry, which is what the embedding model was trained
# on: 25 ms windows every 10 ms.
FRAME_LENGTH = 400
FRAME_SHIFT = 160
N_FFT = 512
NUM_MEL = 80
MEL_LOW_HZ = 20.0
MEL_HIGH_HZ = 7600.0

# A turn shorter than this does not carry enough voice to place. Measured:
# below about two seconds the split-half margin collapses, which is the
# model saying it cannot tell either.
MIN_TURN_SECONDS = 2.0

# Frames of fbank below which the model gets too little to work with
# (25 frames is a quarter of a second).
MIN_FRAMES = 25


class DiarisationError(Exception):
    """Speakers could not be told apart."""


@dataclass(frozen=True, slots=True)
class Turn:
    """One stretch of one voice, in seconds from the start."""

    start: float
    end: float
    speaker: int

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True, slots=True)
class Diarisation:
    turns: tuple[Turn, ...] = ()

    @property
    def speakers(self) -> tuple[int, ...]:
        return tuple(sorted({t.speaker for t in self.turns}))

    def speaker_at(self, when: float) -> int | None:
        """Who is talking at ``when``, or None in the gaps between turns."""
        for turn in self.turns:
            if turn.start <= when < turn.end:
                return turn.speaker
        return None

    def seconds_by_speaker(self) -> dict[int, float]:
        totals: dict[int, float] = {}
        for turn in self.turns:
            totals[turn.speaker] = totals.get(turn.speaker, 0.0) + turn.duration
        return totals


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------


def mel_filters(
    num_mel: int = NUM_MEL,
    n_fft: int = N_FFT,
    rate: int = SAMPLE_RATE,
    low: float = MEL_LOW_HZ,
    high: float = MEL_HIGH_HZ,
) -> np.ndarray:
    """Triangular mel filterbank, Kaldi's mel scale (1127 ln(1 + f/700))."""

    def to_mel(hz: float | np.ndarray):
        return 1127.0 * np.log(1.0 + np.asarray(hz) / 700.0)

    def to_hz(mel: np.ndarray):
        return 700.0 * (np.exp(mel / 1127.0) - 1.0)

    points = to_hz(np.linspace(to_mel(low), to_mel(high), num_mel + 2))
    bins = np.floor((n_fft + 1) * points / rate).astype(int)
    filters = np.zeros((num_mel, n_fft // 2 + 1), dtype=np.float32)
    for i in range(num_mel):
        left, centre, right = int(bins[i]), int(bins[i + 1]), int(bins[i + 2])
        centre = max(centre, left + 1)
        right = max(right, centre + 1)
        if left >= filters.shape[1]:
            break
        centre = min(centre, filters.shape[1])
        right = min(right, filters.shape[1])
        filters[i, left:centre] = (np.arange(left, centre) - left) / max(centre - left, 1)
        filters[i, centre:right] = (right - np.arange(centre, right)) / max(right - centre, 1)
    return filters


def fbank(signal: np.ndarray, filters: np.ndarray | None = None) -> np.ndarray | None:
    """80-bin log-mel features, mean-normalised over time.

    This is the piece most able to fail quietly: wrong features still
    produce embeddings, and embeddings still cluster into something. The
    split-half check in the tests is what keeps it honest -- a voice has
    to match itself better than it matches anyone else.
    """
    signal = np.asarray(signal, dtype=np.float32)
    if signal.size < FRAME_LENGTH:
        return None
    emphasised = np.append(signal[0], signal[1:] - 0.97 * signal[:-1])
    count = 1 + (len(emphasised) - FRAME_LENGTH) // FRAME_SHIFT
    if count < 1:
        return None
    index = np.arange(FRAME_LENGTH)[None, :] + FRAME_SHIFT * np.arange(count)[:, None]
    frames = emphasised[index]
    frames = frames - frames.mean(axis=1, keepdims=True)
    # Povey window: Kaldi's default, and a hamming raised to 0.85.
    window = np.hamming(FRAME_LENGTH) ** 0.85
    spectrum = np.abs(np.fft.rfft(frames * window, N_FFT)) ** 2
    if filters is None:
        filters = mel_filters()
    energies = np.maximum(spectrum @ filters.T, 1e-10)
    feats = np.log(energies)
    return (feats - feats.mean(axis=0, keepdims=True)).astype(np.float32)


def normalise(vector: np.ndarray) -> np.ndarray:
    """Unit length, so a dot product is a cosine."""
    vector = np.asarray(vector, dtype=np.float32)
    return vector / (float(np.linalg.norm(vector)) + 1e-9)


# ---------------------------------------------------------------------------
# clustering
# ---------------------------------------------------------------------------


def split_two(vectors: np.ndarray) -> np.ndarray:
    """Two speakers, by the leading axis of the similarity matrix.

    Spectral clustering's first step and nothing more: for "which of two
    people is this", the sign of the top eigenvector of the centred
    cosine-similarity matrix is the split. Full agglomerative clustering
    would mean another dependency to decide one bit per turn.
    """
    vectors = np.asarray(vectors, dtype=np.float32)
    if vectors.ndim != 2 or len(vectors) < 2:
        return np.zeros(len(vectors), dtype=int)
    similarity = vectors @ vectors.T
    centred = similarity - similarity.mean(axis=0, keepdims=True)
    _u, _s, vt = np.linalg.svd(centred, full_matrices=False)
    labels = (vt[0] > 0).astype(int)
    # The sign of an eigenvector is arbitrary; name the speaker who talks
    # in the first turn "0" so runs are repeatable and a label means the
    # same thing between them.
    return labels if labels[0] == 0 else 1 - labels


def separation(vectors: np.ndarray, labels: np.ndarray) -> float:
    """How much better a speaker matches themselves than the other one.

    The number that says whether a split is real. Measured at 0.557 on
    the reference interview; a split of near zero means one voice was cut
    in half arbitrarily rather than two voices being told apart.
    """
    vectors = np.asarray(vectors, dtype=np.float32)
    labels = np.asarray(labels)
    within, across = [], []
    for i in range(len(vectors)):
        for j in range(i + 1, len(vectors)):
            bucket = within if labels[i] == labels[j] else across
            bucket.append(float(vectors[i] @ vectors[j]))
    if not within or not across:
        return 0.0
    return float(np.mean(within) - np.mean(across))


def one_speaker_only(separation_score: float, threshold: float = 0.15) -> bool:
    """Whether a "two speaker" split is really one person.

    A monologue still gets split in two by any clustering that is told to
    find two, and the result is one voice cut arbitrarily down the middle
    -- which would put a speaker change in the captions of a solo video.
    The measured real split was 0.557 and the split-half margin of a
    single voice against itself was 0.169, so a threshold in between
    separates "two people" from "one person diced up".
    """
    return separation_score < threshold


def turns_from_labels(
    spans: list[tuple[float, float]] | tuple[tuple[float, float], ...],
    labels: np.ndarray | list[int],
) -> tuple[Turn, ...]:
    """Speech spans plus their speaker, with neighbours of one voice joined.

    Two consecutive spans by the same person are one turn: the VAD splits
    on breath, not on who is talking, and a caption reading "Ana: ... /
    Ana: ..." across a pause is noise.
    """
    turns: list[Turn] = []
    for (start, end), speaker in zip(spans, list(labels), strict=True):
        speaker = int(speaker)
        if turns and turns[-1].speaker == speaker:
            turns[-1] = Turn(turns[-1].start, float(end), speaker)
        else:
            turns.append(Turn(float(start), float(end), speaker))
    return tuple(turns)


def label_words(words, diarisation: Diarisation) -> tuple[int | None, ...]:
    """A speaker per word, by where the word's middle falls.

    The middle rather than the start: a word straddling a speaker change
    belongs to whoever was talking for most of it, and the start would
    hand every first word to the previous speaker.
    """
    out: list[int | None] = []
    for word in words:
        middle = (float(word.start) + float(word.end)) / 2.0
        out.append(diarisation.speaker_at(middle))
    return tuple(out)


def speaker_name(index: int, names: dict[int, str] | None = None) -> str:
    """What to print. Editors can rename; the default is honest about
    being a number rather than inventing "Host" and "Guest"."""
    if names and index in names:
        return names[index]
    return f"Speaker {index + 1}"
