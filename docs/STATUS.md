# ASH Captions — Status

Last verified: **2026-09-03**. Everything under "verified" below was checked by
running it, not inferred.

- Repo: `github.com/ASHMediaSolutions01/ashcaptions` (**public** from
  2026-09-03; the code stays proprietary, see `LICENSE`)
- Tests: **1314 passing, 28 skipped** (the skips are the real-ffmpeg and
  real-font suites, which run with `ASH_REAL_FFMPEG=1` and all pass)
- Design decisions and their reasoning: `docs/superpowers/specs/2026-08-29-ash-captions-design.md`
- Editor instructions: `docs/EDITOR-GUIDE.md`, and inside the app at `/guide`
- Build/release instructions: `docs/INSTALL.md`
- The independent audit that shaped this release: `docs/audits/2026-09-02-codex-audit.md`

---

## Where the project is

**v0.4, "short-form effects and clients", is what master is now** (2026-09-03).
On top of v0.3:

- **Captions behind the speaker.** A person matte from Robust Video Matting
  (MobileNetV3, ONNX, onnxruntime on the CPU) is rendered once per job and
  composited in the burn: captions drawn on the frame, the original frame
  masked by the matte laid back on top. Measured on the real reel: matte at
  480x854 in about real time (20 s for a 20 s clip), burn 6.9 s instead of
  4.4 s, and the words vanish behind her head and hair in the frames. A
  per-job option, off by default, aimed at reels.
- **Clients and per-client glossaries.** A client on every job, a glossary
  per client merged over the shared one (client wins), editable from the
  control page, and `in\<Client>\` in the watch folder.
- **The style editor** exposes alignment and the card-box effect.
- **The release path was rehearsed for real**: `fetch_model.py`, `build.py`
  (which had a real bug: a relative `--model-dir` resolved against
  PyInstaller's build folder), the real installer into a scratch location,
  the installed exe up in 2 s, guide and Studio served from the bundle, and
  a real burn job done in 15 s through it. The public
  `ashcaptions-releases` repo exists and is seeded.

**v0.3, "pick your look", is underneath it** (2026-09-03). On top of the
v0.2 hardening below, the team's actual request shipped:

- **The Studio** (`/studio/<job>`, opens when a job finishes): the video plays
  in the browser with the captions drawn on it by libass compiled to
  WebAssembly (JASSUB, vendored, offline), so what you see is what burns. A
  strip of looks on the right, grouped by position; clicking one re-renders
  the `.ass` from the saved transcript in ~30 ms and the overlay reloads
  without touching the playhead. **Burn this look** enqueues a burn-only job
  that starts at the burn stage.
- **Transcripts are saved beside the outputs** (`<stem>.transcript.json`, with
  the source file's size and mtime). Re-styling, burning and retrying no
  longer transcribe; a changed file is transcribed again.
- **36 looks** (was 9) across bottom, centre, top and lower third, with left
  and right variants (`layout.align`), plus a `card_box` effect that puts the
  whole caption on one bar for news and tag styles.
- **Spanish verified**: a 4:48 Creative Commons interview produced Spanish
  `.srt`, English `.en.srt`, transcript and burned MP4 in 139 s; the Studio
  rendered it live and burned it from the chosen look. Found and fixed on
  the way: the translate pass was being primed with the Spanish dialect
  prompt and left chunks untranslated.

**v0.2, "long-form safe", is underneath it.** On 2026-09-02 four
independent reviews found thirteen verified critical failures that only
appear on hour-long files or in the real bundle, and a separate read-only
audit found eight more. All are fixed, each with a regression test, and the
whole thing was re-run on real footage afterwards.

What that means in practice:

| Was | Now |
|---|---|
| Punch-in failed the job past ~40 minutes (Windows command-line limit) | Filter graph goes through a file; 900 moments verified with real ffmpeg |
| Transcription needed a ~5 GB RAM burst at 90 minutes | Batched pipeline, bounded memory, and faster (5.7x realtime here vs 3.1x) |
| Any apostrophe in a filename broke burn-in | Filenames never enter the filter graph; `Client's reel, v2.mp4` verified |
| Progress bar frozen for the whole transcription | Per-batch progress, stage label, a ticking elapsed clock, a worker health line |
| Progress stream dropped every second and reconnected | One connection with heartbeats; verified 70 s in a real browser |
| Restart re-queued every job; a same-named file was ignored until restart | Live-row dedupe in SQLite; paths forgotten when they leave the folder |
| Worker thread could die silently | Loop survives store errors, backs off, reports `worker_alive` |
| ffmpeg outlived the app on Quit or crash | Windows Job Object kills children; cooperative cancel on Quit |
| Any website open in another tab could push jobs into the queue | Host check, Origin check, and a required client header |
| PLAYFUL rendered in Arial; two other fonts too | Manifest names match the font files; real-libass test over all 24 fonts |
| Bundled speech model was never used; re-downloaded after every update | Hugging Face cache layout, verified offline |
| Portrait PlayRes on every video (16:9 captions at 56% size) | Probed once per job and passed through; verified 45 px vs 26 px |
| Half-written outputs under the final name after a crash | `.part` + rename for every output |
| Update could `robocopy /MIR` over a git checkout | Update flow refuses on source installs and non-frozen builds |

## What works, verified on real footage (2026-09-03, CPU only)

| Stage | Measured |
|---|---|
| Transcription (`small`, batched) | 10.4 min of audio in 110 s: **5.7x realtime** |
| Burn-in 1080x1920 (`libx264 veryfast crf 18`) | ~3.4x realtime |
| Punch-in cost | ~zero |
| 20 s clip, transcribe + burn + punch, apostrophe in the name | 14.3 s end to end |

Also verified today: the real app started from a source checkout, driven over
HTTP exactly as the browser does (health, guide page, foreign-origin request
refused, submit by path, one long-lived event stream), killed hard during a
burn with its ffmpeg child gone within seconds, restarted with the same job
re-run from `pending` and no duplicate row.

Honest limits of that verification: the longest real file run through the
whole pipeline is **10 minutes**. The 90-minute case is covered by synthetic
tests (900 punch moments, the memory measurement, timestamp formatting past
10 hours) and by the fact that nothing time-based can kill a job. The first
real hour-long client file should be watched by a person.

---

## Roadmap

### v0.2 — long-form safe (done, this release)
Everything above. Remaining to close it out: publish a build so editors get
the one-click installer (`scripts/release.py` has still never been run; the
public `ashcaptions-releases` repo needs a seed commit first, see INSTALL.md).

### v0.3 — pick your look (done, this release)
The Studio, saved transcripts, burn-only jobs, 36 looks with left/right
alignment and the card-box effect. Still open from v0.3: the published
installer and the update banner in real use; the style editor does not yet
expose `align` and `card_box` (edit the JSON, or pick one of the shipped
looks).

### v0.4 — short-form effects and clients (done, this release)
Behind-the-speaker captions, clients with per-client glossaries, the style
editor gaps, the release rehearsal. See "Where the project is".

### v0.5 and later
- Arabic and Urdu styled captions (right-to-left ASS; Noto Naskh is bundled).
- Emoji and sticker bursts (a compositing pass; not possible in ASS).
- A GPU bundle variant, once there is an NVIDIA machine to test on. Until
  then `enable_gpu.ps1` refuses by design and the engine falls back to CPU.
- Review-page video for watch-folder jobs (the input is deleted on success,
  so the review route can only stream by-path and uploaded inputs).

---

## Known limitations (deliberate)

- **Updates are not cryptographically signed.** sha256 from the manifest is
  verified; an explicit click is required. Reasoning in `docs/INSTALL.md`.
- **Punch-in is off by default.** It reframes a client's video.
- **A user style with a shipped name overrides it** for every job on that PC.
  The editor marks it "customized locally" and "Reset to shipped" now removes
  the override rather than saving a frozen copy.
- **Transcription progress moves per batch** (about every 2.5 minutes of
  audio). The elapsed clock and health line show it is alive in between.
- **Supply-chain inputs are mutable**: the ffmpeg fetcher takes BtbN's rolling
  `latest` and the model fetch takes the upstream revision. The versions
  actually shipped are recorded in `bin/ffmpeg-build-info.txt` and
  `build/models/model-info-<size>.txt`; pinning them is a v0.3 item.
- **Source runs must be editable installs** (`pip install -e .`): styles and
  fonts live at the repo root.

---

## Things Ghazi needs to do

1. **Seed the public `ashcaptions-releases` repo** with one commit, then run
   the four build/publish commands in `docs/INSTALL.md`. Until then editors run
   from source with the guide, which works.
2. **Run the first real hour-long client file** with the page open, and send
   the log if anything looks wrong. This is the one thing no synthetic test
   replaces.
3. **Validate three numbers against real client work**, all tunable without a
   release, in `C:\AshCaptions\settings.json`:
   - `silence_gap_seconds` (1.5)
   - whether `POP` should stay boxed one-word-at-a-time
   - whether `CLEAN`'s hue-free look reads right on client footage

---

## The pattern worth remembering

Every serious bug in this project was built correctly, fully tested, and still
broken in the real product: the styling system blocked at three layers, a
package that installed but would not import, an encoder the shipped ffmpeg
could not contain, a pipe deadlock, a progress stream that killed itself, a
model cache in the wrong layout, fonts whose names did not match their files,
and, on 2026-09-03, punch-in silently disabled by a keyword the new filter
builder did not accept. None were catchable by the unit suite. All were found
by running the thing: building the bundle and looking inside it, installing
into a clean venv, putting a real file through, opening the real page in a
real browser. Do that before each release, and once more on the weakest
editor machine.
