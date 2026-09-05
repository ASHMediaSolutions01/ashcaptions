# ASH Captions — Status

Last verified: **2026-09-05**. Everything under "verified" below was checked by
running it, not inferred.

- Repo: `github.com/ASHMediaSolutions01/ashcaptions` (**public** from
  2026-09-03; the code stays proprietary, see `LICENSE`)
- Tests: **1536 passing, 30 skipped** (the skips are the real-ffmpeg and
  real-font suites, which run with `ASH_REAL_FFMPEG=1` and all pass)
- Every push runs the suite and `ruff check` on Windows:
  `.github/workflows/ci.yml`. Green there is the floor; a release is still
  only real once the built bundle has been launched and driven by hand.
- Design decisions and their reasoning: `docs/superpowers/specs/2026-08-29-ash-captions-design.md`
- Editor instructions: `docs/EDITOR-GUIDE.md`, and inside the app at `/guide`
- Build/release instructions: `docs/INSTALL.md`
- The independent audit that shaped v0.2: `docs/audits/2026-09-02-codex-audit.md`
- The release-readiness scan that shaped 0.4.1/0.4.2: `docs/audits/2026-09-04-readiness-scan.md`
- What v0.5 is and why: `docs/superpowers/specs/2026-09-04-v0.5-design.md`

---

## Where the project is

**v0.5.1 is what master is now, and what is published** (2026-09-05). It is
the build the six editors install and test.

0.5.1 is 0.5.0 plus what opening the pages on a 1366x768 laptop turned up: a
dashed rectangle was drawn around every video in the Studio (two of the v0.5
work tracks used one class name for different elements, and the losing
draft, 110 lines of it, was dead or wrong); the caption-check panel used 589
of its 1034 pixels, and now fills them with the English beside the source
rather than under it; and a finished queue row no longer carries an empty
band where its progress bar used to be. Guide screenshots recaptured.
PRODUCT.md, which still described a nine-look watch-folder service on a
shared office PC, now describes what shipped.

On top of v0.4.2, v0.5 brought:

- **Move the caption anywhere.** Drag it on the video in the Studio and the
  captions redraw there in about a second; arrow keys nudge by 1% of the
  frame, Shift by 5%, and **Reset position** puts it back where the look
  wants it. The position belongs to the job, not the look, so changing look
  keeps it, and it is what gets burned. Stored as a fraction of the frame,
  so it lands in the same place at any video size. Verified in the real
  Studio: dragged to 50% across and 25% down, all 1456 caption events
  pinned to (962, 236) in the `.ass`, position kept when the look changed,
  and Reset returned it to the look's own placement.
  Until now a caption could only sit at one of twelve fixed spots, which is
  the thing every competitor has had for years.
- **Checking captions in a language nobody on the desk speaks.** The
  transcript panel under the video shows the English line under each source
  line, and underlines the words the speech model was unsure of, amber under
  0.5 confidence and red under 0.3, with a chip that counts them and jumps
  to the next one. When a job was never translated, **Translate to check**
  runs only the English pass from the saved transcript, in seconds, without
  transcribing again. Verified on the Spanish interview: 728 Spanish words,
  760 English, 25 flagged. The confidence numbers were always saved and
  never shown; now they are the answer to "is this Spanish caption right?".
- **The glow looks are readable.** GLOW MINT, NEON GLOW and OCEAN drew the
  highlighted word's halo in the same colour as its fill, so the word turned
  into a blob. The glow is now a blurred halo on its own layer under crisp
  text. Verified in the burn, in a real-libass pixel test, and in the
  browser; switching looks still takes about 230 ms.
- **The installer checks the PC first.** 64-bit Windows or it stops, the
  Windows build, 4 GB free on the install drive with the real figure in the
  message, TLS 1.2 for the download, and a warning when long paths are off.
  Download failures say to check the connection or ask about a proxy instead
  of printing a .NET stack trace. `-CheckOnly` reports all of it without
  installing.
- **An uninstaller.** `Uninstall-AshCaptions.bat` quits the app, removes the
  logon task and both shortcuts, and deletes the program folder. It keeps
  `C:\AshCaptions` (the editors' captions) unless asked otherwise, and says
  so. Verified by running it against a scratch install: program folder gone,
  captions kept, and a second run is a clean no-op.
- **Polish and the guide.** One primary action per queue row, no green bar
  on finished jobs, a readable disabled Start button, the job thumbnail as
  the Studio video's poster. The guide gained "Starting and stopping",
  "Moving a caption", "Checking captions in a language you don't speak" and
  "Uninstalling", every screenshot recaptured from this build, and a
  standalone `docs/ASH-Captions-Guide.html` that opens from a file with the
  pictures inside it, to send the team before they install.
- **Two bugs found by clicking, not by tests.** Two Studio tabs restyling
  one job could still fail one of them on Windows, because two renames onto
  the same file collide; the rename now retries. And picking a look while
  the video was paused appeared to do nothing, because the caption renderer
  only draws on a video frame; it now repaints the current moment.

**v0.4.2 is underneath it** (2026-09-04). It is
the v0.4 feature set below plus one UX pass and the fixes from a deep
readiness scan, and it is the first build the editors should install:

- **UX pass.** Dark theme across every page, a Browse... button that opens
  the Windows file picker, thumbnails and durations on queue rows, Remove /
  Clear finished / Open folder / Copy path actions, a tray balloon and a
  page toast when a job finishes, Studio polish (filter looks, keyboard
  navigation, transcript strip that follows the playhead).
- **Phone footage.** A portrait reel shot on a phone carries a rotation tag
  and decodes to 1080x1920 while ffprobe reports 1920x1080; captions came out
  ~1.8x too big and "behind the speaker" failed outright. The probe now
  honours the rotation. Verified through the installed exe on a rotated
  copy of the client reel: output 1920x1080 (as decoded), `.ass` PlayRes to
  match, behind-the-speaker burn done in 36 s.
- **The windowed build, for real.** 0.4.0 shipped as a console exe by
  mistake (black window at every logon, closing it killed the job). 0.4.1
  was windowed, and its rehearsal passed, and it was still broken: launched
  the way the logon task launches it (no stdout at all), uvicorn's default
  logging probed `sys.stdout.isatty()` and the web server thread died --
  a tray icon with no page behind it. The rehearsal had attached a stdout
  file to the exe and never saw it. 0.4.2 configures uvicorn without that
  probe, and startup now waits for the port and fails loudly (log, message
  box, exit 1) if the page never binds. Verified with a `Start-Process`
  launch of the installed exe: page up in 1 s.
- **The in-app update, for real.** The first real update ever run (0.4.0 ->
  0.4.1 through `/api/update/apply`) downloaded 662 MB, verified the hash,
  extracted, handed off to the helper -- and nothing came back.
  `powershell.exe` started with `DETACHED_PROCESS` exits immediately without
  running the script. 0.4.2 spawns the helper with `CREATE_NO_WINDOW` and a
  real regression test spawns the actual helper template from inside the
  kill-on-close job object, lets the parent exit, and checks the mirror and
  relaunch. Verified in the installed bundle against a local manifest
  (`ASH_CAPTIONS_MANIFEST_URL`): apply, old process gone in 10 s, relaunched
  page up 3 s later, `data\updates` swept clean on the relaunch. **0.4.0
  installs cannot self-update** (their spawn is the broken one); they need
  the installer run once more, which the rollout does anyway.
- **Licences shipped.** `scripts/collect_licenses.py` gathers every
  dependency's licence text into the bundle (PyInstaller keeps dist-info for
  only a handful), the GPL-3.0 text for the matting weights included; PyAV
  is no longer bundled (it linked a GPL ffmpeg in-process; faster-whisper
  gets the audio as a numpy array instead); `NOTICES.md` corrected.
- **Smaller fixes from the scan.** `tmp_dir` sweep only removes entries the
  app created; matte and source frames aligned with `setpts` for sources
  whose first timestamp is not 0; two Studio tabs restyling one job no
  longer collide on a temp file; `.wmv` accepted by the page like the watch
  folder; update leftovers removed on start; oversized modules split.
  Still open from the scan: the port probe/bind race and the preview/update
  job dicts (both minor, listed in the scan), Arabic karaoke looks (v2, the
  guide says use `.srt` or a plain look), and whether the ruflo/claude
  tooling files should stay in the public repo.

**v0.4, "short-form effects and clients", is underneath it** (2026-09-03).
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
alignment and the card-box effect. The style editor exposes `align` and
`card_box` since v0.4.

### v0.4 — short-form effects and clients (done)
Behind-the-speaker captions, clients with per-client glossaries, the style
editor gaps, the release rehearsal.

### v0.4.1 / v0.4.2 — the UX pass and the readiness scan (done, this release)
Dark theme, Browse, thumbnails, job actions, notifications; phone rotation;
the windowed build that actually serves its page; the update helper that
actually relaunches; licence texts. See "Where the project is". v0.4.1 is
published but superseded the same day (its page never came up when launched
by the logon task); v0.4.2 is the one to install.

### v0.5 — move it, check it, install it anywhere (done, this release)
Caption placement anywhere on the frame, the caption check for languages
nobody on the desk speaks, the glow fix, installer preflight and an
uninstaller, the polish pass and the shareable guide. See "Where the
project is".

### v0.6 and later
Ranked by how often an editor would hit the gap, from the 2026-09-04
competitor scan (Veed, CapCut, Submagic, Captions.ai, Opus Clip, Descript,
Premiere, Resolve, and the regional tools Kalakar, Bayaan and Bolti):

- Turning a landscape interview into a 9:16 reel, cropping to follow the
  speaker. Every short-form competitor does this; the matting model already
  in the bundle can supply the tracking signal.
- Emoji and sticker bursts (a compositing pass; not possible in ASS).
- Arabic and Urdu styled captions (right-to-left ASS; Noto Naskh is
  bundled). The karaoke looks sweep the wrong way in Arabic today, which
  the guide says.
- Speaker names for podcasts, which needs a diarisation model and the
  install weight that comes with it.
- A GPU bundle variant, once there is an NVIDIA machine to test on. Until
  then `enable_gpu.ps1` refuses by design and the engine falls back to CPU.
- Review-page video for watch-folder jobs (the input is deleted on success,
  so the review route can only stream by-path and uploaded inputs).

Deliberately not chasing: auto B-roll and the "virality score" features.
They are a second product, and the thing this tool has that none of the
cloud ones do is that client footage never leaves the building.

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

1. **Roll the installer out.** v0.5.0 is published at
   `github.com/ASHMediaSolutions01/ashcaptions-releases` (verified: the real
   installer downloaded it from the manifest, hash-checked it, installed it,
   the installed exe ran a behind-the-speaker client job and a rotated
   phone reel, and an in-app update relaunched it). Send each editor
   `docs\ASH-Captions-Guide.html` to read first, then
   `installer\Install-AshCaptions.bat` and `installer\install.ps1` from
   this repo; the installer pulls the release itself. Anyone who already
   has 0.4.0 must run the installer again (0.4.0 cannot self-update); from
   0.4.2 on, the tray's update item works. Source installs keep working
   with `git pull`.
2. **Run the first real hour-long client file** with the page open, and send
   the log if anything looks wrong. This is the one thing no synthetic test
   replaces.
3. **Decide about code signing.** On a PC where IT has set AppLocker, or
   SmartScreen to block rather than warn, an unsigned exe will not run at
   all and no installer check can fix that. A signing certificate is a few
   hundred dollars a year and also removes the blue "protected your PC"
   box every editor sees on first run.
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
on 2026-09-03, punch-in silently disabled by a keyword the new filter
builder did not accept, and on 2026-09-04 two more in a release that had
passed its own rehearsal: a windowed exe whose web server died on
`sys.stdout.isatty()` because the rehearsal had given it a stdout, and an
update helper that never ran because `DETACHED_PROCESS` makes powershell.exe
exit at once. None were catchable by the unit suite. All were found by
running the thing: building the bundle and looking inside it, installing
into a clean venv, putting a real file through, opening the real page in a
real browser, launching the exe the way the logon task launches it (no
console, no stdout), and running the update for real. Do that before each
release, and once more on the weakest editor machine.
