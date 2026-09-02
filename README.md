# ASH Captions

In-house captioning for Ash Media Solutions. Drop in a video, get back accurate,
styled, correctly-timed captions in 54 languages — running entirely on your own
PC, so client footage never leaves the building.

Outputs `.srt` for Premiere and Resolve, styled `.ass` captions, a plain
transcript, an optional English translation, and an optional burned-in MP4.

---

## Start here

| If you are… | Read |
|---|---|
| **An editor setting this up on your PC** | **[docs/EDITOR-GUIDE.md](docs/EDITOR-GUIDE.md)** — setup commands, daily use, screenshots |
| Building or releasing a version | [docs/INSTALL.md](docs/INSTALL.md) |
| Picking up the project | [docs/STATUS.md](docs/STATUS.md) — what works, what's open, known limits |
| Asking *why* something is built this way | [the design spec](docs/superpowers/specs/2026-08-29-ash-captions-design.md) |

## Quick start (from a source checkout)

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe scripts\fetch_ffmpeg.py
.venv\Scripts\python.exe -m ash_captions.styles.fonts download
.venv\Scripts\python.exe -m ash_captions --open
```

The control page opens at `http://127.0.0.1:8756`. The full walkthrough,
including the speech model and troubleshooting, is in the editor guide.

## What's in here

```
src/ash_captions/
  engine/     audio extraction, transcription, caption timing rules,
              SRT/ASS/TXT writers, burn-in, punch-in
  styles/     9 caption looks as JSON data, 24 bundled fonts, ASS renderer
  languages/  54 languages, 22 dialect presets, spelling, glossaries
  pipeline/   SQLite job queue, crash recovery, watch folder
  web/        the local control page and style editor
  app/        wiring, tray icon, updater, lifecycle
styles/       the shipped caption looks (edit these, no code change needed)
scripts/      build, release, and one-time fetch tooling
```

Tests: `.venv\Scripts\python.exe -m pytest tests -q`
