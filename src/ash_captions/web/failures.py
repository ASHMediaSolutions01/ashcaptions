"""What to tell an editor when a job fails, and whether Retry can help.

A failed job stores one string: the engine's message plus the tail of
ffmpeg's stderr (``pipeline.queue.format_job_error``). That text is right
for Ghazi and wrong for an editor — "ffmpeg failed extracting audio from
x.mp4 (exit 3199971767)" followed by a Temp path says nothing about what
to *do*. The critique of 2026-09-23 scored error recovery 1/4 on exactly
this, and it is the one moment an editor needs the tool most.

So the technical text stays where it was (the Job's ``error``), and the
browser also gets a ``reason`` — one plain sentence naming the problem
and the recovery — and ``retryable``: whether pressing Retry can change
anything. A file that is not a video fails the same way every time, and
offering Retry as the primary action there is how an editor ends up
retrying three times and then escalating.

The rules match on the stored text rather than on exception classes,
because by the time the text reaches the browser the exception is gone,
and because it means a job that failed under an older build still gets
an explanation. Every phrase here is one an engine module actually
raises; a new failure mode gets a rule when it has been seen for real.
"""

from __future__ import annotations

from dataclasses import dataclass

# (phrases, what to tell the editor, whether Retry can help). First match
# wins, so the more specific phrases come before the ones they contain --
# "behind the speaker" carries "frame size" and must be tried first.
_RULES: tuple[tuple[tuple[str, ...], str, bool], ...] = (
    (
        ("failed extracting audio", "invalid data found", "moov atom not found"),
        "This file couldn't be read as a video. It may still be exporting or "
        "copying — re-export it, then submit it again.",
        False,
    ),
    (
        ("has no audio", "no audio to tell"),
        "This video has no audio track, so there is nothing to caption.",
        False,
    ),
    (
        ("input video not found", "video not found"),
        "The file has been moved or renamed since it was submitted. Put it "
        "back, or submit it again from where it is now.",
        False,
    ),
    (
        ("subtitle file not found",),
        "A caption file went missing part-way through. Retry runs the job again.",
        True,
    ),
    (
        ("not enough free space",),
        "Not enough free space on the output drive. Clear some space, then retry.",
        True,
    ),
    (
        ("behind the speaker",),
        "Captions behind the speaker couldn't be rendered for this file. Untick "
        "that option and submit again.",
        False,
    ),
    (
        ("reframing to 9:16", "frame size"),
        "The video's frame size couldn't be read, so it can't be cropped to a "
        "9:16 reel. Untick that option and submit again.",
        False,
    ),
    (
        ("speaker model",),
        "The speaker model isn't available. Check the connection and retry, or "
        "untick 'Name who is speaking'.",
        True,
    ),
    (
        ("cancelled",),
        "This job was stopped before it finished, usually because the app "
        "closed. Retry runs it again.",
        True,
    ),
    (
        ("failed burning captions", "could not be moved to"),
        "Burning the captions into the video failed. Retry once; if it fails "
        "again, send Ghazi the technical details below.",
        True,
    ),
    (
        ("ffmpeg executable not found", "failed to launch ffmpeg"),
        "The app's own ffmpeg is missing or blocked. Reinstall ASH Captions, "
        "or check whether the antivirus quarantined it.",
        False,
    ),
)

STDERR_MARKER = "--- ffmpeg stderr"


@dataclass(frozen=True, slots=True)
class Explanation:
    reason: str
    retryable: bool


def explain_failure(error_text: str | None) -> Explanation:
    """The editor's sentence for a stored failure, and whether Retry helps.

    Unknown failures fall back to the message's own first line, which is
    the engine's wording rather than an editor's, with Retry left on:
    guessing that a retry is pointless is worse than letting one happen.
    """
    text = (error_text or "").strip()
    if not text:
        return Explanation("Something went wrong while captioning it.", True)
    lowered = text.lower()
    for phrases, reason, retryable in _RULES:
        if any(phrase in lowered for phrase in phrases):
            return Explanation(reason, retryable)
    first_line = text.split(STDERR_MARKER, 1)[0].strip().splitlines()[0]
    return Explanation(first_line, True)
