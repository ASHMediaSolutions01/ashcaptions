"""The captioning engine: audio extraction, transcription, caption rules,
output writers, and optional burn-in.

Public interface -- other packages should import from here rather than
reaching into submodules directly, so the internal layout can shift
without breaking callers:

    from ash_captions.engine import (
        extract_audio, AudioExtractionError,
        Word, Segment, TranscriptionResult, Transcriber, WhisperTranscriber, TranscriptionError,
        Card, build_cards,
        render_srt, write_srt, render_ass, write_ass, render_txt, write_txt, AssPreset, CLEAN, POP,
        detect_nvenc, build_burn_command, burn_captions, BurnInError,
    )
"""
from .audio import AudioExtractionError, DEFAULT_FFMPEG_PATH, extract_audio
from .probe import ProbeError, VideoInfo, probe_video
from .punch import (
    MAX_DURATION_SECONDS,
    MIN_DURATION_SECONDS,
    PunchMode,
    PunchMoment,
    build_zoompan_filter,
    select_punch_moments,
)
from .burn import (
    BurnInError,
    available_encoders,
    build_burn_command,
    burn_captions,
    detect_nvenc,
    select_video_encoder,
)
from .rules import Card, build_cards
from .transcribe import (
    Segment,
    Transcriber,
    TranscriptionError,
    TranscriptionResult,
    WhisperTranscriber,
    Word,
)
from .writers import (
    CLEAN,
    POP,
    AssPreset,
    render_ass,
    render_srt,
    render_txt,
    write_ass,
    write_srt,
    write_txt,
)

__all__ = [
    "extract_audio",
    "AudioExtractionError",
    "DEFAULT_FFMPEG_PATH",
    "Word",
    "Segment",
    "TranscriptionResult",
    "Transcriber",
    "WhisperTranscriber",
    "TranscriptionError",
    "Card",
    "build_cards",
    "render_srt",
    "write_srt",
    "render_ass",
    "write_ass",
    "render_txt",
    "write_txt",
    "AssPreset",
    "CLEAN",
    "POP",
    "detect_nvenc",
    "probe_video",
    "VideoInfo",
    "ProbeError",
    "PunchMode",
    "PunchMoment",
    "select_punch_moments",
    "build_zoompan_filter",
    "available_encoders",
    "select_video_encoder",
    "build_burn_command",
    "burn_captions",
    "BurnInError",
]
