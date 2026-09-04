"""Stand-in for the PyAV package inside the built bundle.

faster-whisper imports ``av`` at module level but only *uses* it to decode
audio files; ASH Captions always hands it a numpy array of the WAV that
ffmpeg.exe already extracted, so the real PyAV (whose wheel bundles a
GPL-built FFmpeg with libx264/libx265) is excluded from the bundle and
this module satisfies the import. Anything that actually calls into it
fails loudly rather than silently.
"""

__version__ = "0.0.0-ash-stub"


class _Unavailable(RuntimeError):
    pass


def __getattr__(name: str):
    raise _Unavailable(
        f"PyAV ({name}) is not bundled with ASH Captions; audio is decoded by ffmpeg.exe instead."
    )
