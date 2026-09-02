# ASH Captions — Status

Last verified: **2026-09-02**. Everything below was checked by running it, not
inferred.

- Repo: `github.com/ASHMediaSolutions01/ashcaptions` (private)
- Tests: **783 passing**
- Design decisions and their reasoning: `docs/superpowers/specs/2026-08-29-ash-captions-design.md`
- Editor instructions: `docs/EDITOR-GUIDE.md`
- Build/release instructions: `docs/INSTALL.md`

---

## What works, verified on real footage

Tested against a genuine 62-second 1080x1920 client reel, not a fixture.

| Stage | Measured on a CPU-only machine |
|---|---|
| Audio extraction | 0.6 s |
| Transcription (`small`, CPU) | 20.3 s for 62.4 s of audio — **3.1x realtime** |
| Burn-in 1080x1920 (`libopenh264`) | 16.9 s — **3.7x realtime** |
| Punch-in cost | **~zero** (15 s burn: 4.468 s without, 4.488 s with) |

A 60-second reel goes from drop to burned output in roughly 40 seconds without
a GPU. GPU remains optional, as the spec always said.

Confirmed working end to end: the control page, live SSE progress, all nine
styles, the style editor, the live preview (renders a real 3-second clip from
the editor's own footage), burn-in with bundled fonts, and punch-in.

**The source file is never touched** on submit-by-path — verified in production,
not only in tests.

---

## Open work

### 1. Publish a build so editors get the one-click installer

The only genuinely unfinished thing. `docs/EDITOR-GUIDE.md` currently documents
the **run-from-source** path, because that is what actually works today.

Everything needed exists and has been run:

```
.venv\Scripts\python.exe scripts\fetch_ffmpeg.py
.venv\Scripts\python.exe -m ash_captions.styles.fonts download
.venv\Scripts\python.exe scripts\fetch_model.py --model-size small
.venv\Scripts\python.exe scripts\build.py
.venv\Scripts\python.exe scripts\release.py
```

`build.py` has been run for real and produces a correct bundle (flat layout,
`styles/`, `assets/fonts/`, `scripts/pkgtools/`, `web/static/` all present).
`release.py` has **not** been run — the public `ashcaptions-releases` repo does
not exist yet. Until it does, the in-app update check silently finds nothing,
which is by design (every failure is a silent no-op).

### 2. Emoji and sticker overlays — v3

Not possible in ASS: libass colour-font support is unreliable, so Submagic-style
emoji bursts need a separate ffmpeg compositing pass. Spec section 7A.1.

### 3. Arabic/Urdu styled captions — v2

Transcription, `.srt` and English translation already work for Arabic. Only the
styled `.ass` output needs RTL work and the bundled Noto Naskh face. Spec 7A.3.

---

## Known limitations (deliberate, not oversights)

- **`libx264` is not available.** We ship BtbN's **LGPL** ffmpeg, and x264 is
  GPL, so that build excludes it. The encoder is now probed at run time and
  falls back to `libopenh264` (Cisco, BSD) on CPU or `h264_nvenc` on an NVIDIA
  machine. `scripts/fetch_ffmpeg.py --variant gpl` would get `libx264` back
  (better quality per bitrate) and is defensible since we never redistribute
  the app outside the company — an open call, not a bug.
- **Updates are not cryptographically signed.** sha256 from the manifest is
  verified, which covers a corrupted download but not a compromised release
  repo. Mitigated by requiring an explicit click: nothing auto-applies. Reasoning
  in `docs/INSTALL.md`.
- **Glossaries are not per-client yet.** Nothing in a job carries a client
  identity, so there is one shared `glossary.txt`. Spec 9.3 promises per-client;
  it needs a client field on the job first.
- **A user style silently overrides a shipped one of the same name.** Intended
  and recoverable ("Reset to shipped"), and the UI now marks it "customized
  locally" — but saving a style called `POP` does change what every future job
  means by `POP`, including the watch-folder default.
- **Punch-in is off by default.** It reframes a client's video; that should
  never happen without someone choosing it. See `punch_*` in `config.py`.
- **Two fonts need a special case.** `Montserrat ExtraBold` and
  `Poppins SemiBold` are weight variants, not Google families; the downloader
  derives the real family from each manifest entry's specimen URL.

---

## Things Ghazi needs to do

1. **Add the editor's GitHub account to the private repo**, or step 2 of the
   editor guide fails with "repository not found".
2. **Decide the ffmpeg variant** — stay LGPL/`libopenh264`, or switch to the GPL
   build for `libx264`.
3. **Validate three numbers against real client work**, all tunable without a
   release, in `C:\AshCaptions\settings.json`:
   - `silence_gap_seconds` (1.5) — when a caption card may not span a pause
   - whether `POP` should stay boxed one-word-at-a-time
   - whether `CLEAN`'s deliberately hue-free look reads right on client footage

---

## The pattern worth remembering

Four separate times, a feature was built correctly, fully tested, and still
unreachable or broken in the real product:

- The styling system was blocked at three layers in turn — the job runner, the
  API's preset validation, and the build, which shipped without `styles/` or
  `assets/fonts/` at all while 710 tests passed.
- `pip install -e .` reported success but left the package unimportable; the
  test suite hid it because pytest puts `src/` on the path itself.
- Burn-in hardcoded `libx264`, which the LGPL ffmpeg build cannot contain.
- `burn_captions` deadlocked against ffmpeg's stderr pipe and hung every real
  burn — invisible because burn-in is off by default and a mocked process never
  fills a pipe.

None of these were catchable by the test suite. All were found by **running the
thing**: building the bundle and looking inside it, installing into a clean
venv, and putting a real video through. Do that before each release.
