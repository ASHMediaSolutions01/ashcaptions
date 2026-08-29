"""Pure, network-free logic shared by the build/release scripts.

Kept separate from `build.py` / `fetch_ffmpeg.py` / `fetch_model.py` /
`release.py` specifically so it can be imported by tests without pulling in
PyInstaller, network calls, or subprocess side effects. Nothing in this
subpackage performs I/O beyond reading/writing the small JSON/text files it is
explicitly given.
"""
