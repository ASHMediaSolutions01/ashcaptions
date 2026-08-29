# ASH Captions — Design Spec

**Date:** 2026-08-29
**Status:** Approved for implementation
**Owner:** Ghazi / Ash Media Solutions

---

## 1. One-liner

A captioning tool installed on every editor's own PC: drop a video in, get back
accurate, styled, correctly-timed captions in ~50 languages — free, private, and
running on hardware we already own.

## 2. The problem

Short-form clients expect styled captions on every edit. Today that means either
hand-typing them (20–40 minutes of editor time per video), paying per-seat or
per-minute subscriptions, or uploading client footage to a third-party cloud. At
our volume, caption time is one of the largest silent costs in every project.

## 3. What this enhances

This is an internal tool. It is not a product. It earns its keep three ways:

1. **Margin.** Caption prep drops from ~30 minutes to ~5 (skim + import). Against
   a 48-hour first-cut promise, that time is real money.
2. **Reach.** We can accept Spanish, Portuguese and other-language jobs we would
   otherwise decline or subcontract. This is revenue, not just cost saving.
3. **Trust.** Client footage never leaves the building, which keeps NDAs easy.
   "In-house multilingual captioning pipeline" goes on the service list as a
   credibility signal — the same thing larger agencies do with proprietary AI
   tooling. It is a soft asset, not a proven acquisition lever. We do not build
   the roadmap around it.

## 4. Decisions already taken

These were settled by research on 2026-08-29 and are not open questions. They are
recorded here with reasons so we do not relitigate them in three months.

### 4.1 We are not productizing this

The "underserved South Asian captioning market" thesis is false as of 2026. Three
credible competitors already occupy that exact wedge:

| Product | Notes |
|---|---|
| **Kalakar.io** (Mumbai) | Beta Apr 2025, ~16 months in market, claims 100k+ users, ships Premiere **and** Resolve plugins, 20+ languages, ~$6.99–$24.99/mo |
| **Bayaan** (bayaanai.com) | Hindi/Urdu/Punjabi/Bengali/Tamil, freemium |
| **Bolti AI** (bolti.ai) | Urdu/Hindi/Punjabi/Pashto/Sindhi, Desi templates |

Compute economics would be fine (transcription runs ~$0.36–0.46/audio-hour on
commodity APIs, under $0.05 self-hosted). The blocker is distribution and
opportunity cost: a six-person studio would be funding onboarding, support,
multi-tenant infrastructure and security out of client-services capacity, against
an incumbent with a head start and a $6.99 price ceiling.

*Kalakar's pricing page 404'd on direct fetch; tiers above are reconstructed from
their homepage and should be treated as approximate.*

### 4.2 No DaVinci Resolve plugin

Resolve's Python scripting API and its Workflow Integration Plugins are both
**Studio-only** since Resolve 19.1. On free Resolve the connection fails
*silently* — no error, scripts simply never connect. Our editors are on free
Resolve, so this is not a "later" item; it is off the roadmap until someone buys
Studio (~$295/seat).

Even with Studio it would be unattractive: there is no native SRT-import call
(`ImportTimelineFromFile()` takes AAF/EDL/XML/FCPXML/DRT/ADL only, and
`CreateSubtitlesFromAudio()` runs Resolve's *own* transcription, not ours). We
would be parsing SRT and building Text+ items by hand against an API widely
reported to fail silently.

**Resolve's permanent path is `.srt` / `.ass` drag-in.** This is fine.

### 4.3 No Premiere panel in v1 — and the local service is its prerequisite anyway

Adobe UXP is GA for Premiere and CEP dies within a year of Premiere 25.6, so UXP
would be the only sane target. But **a UXP panel cannot spawn our Python process**
— the `shell` permission only opens a file in its default app. The documented
pattern is to run a local HTTP service and have the panel `fetch()` it.

Which means the local service is not the cheap alternative to a panel. It is the
thing a panel would wrap. Building it first is strictly ordered work, not a
compromise. A panel becomes a thin client later if we ever want one.

Note also that Premiere's built-in auto-captions are free with CC and cover ~17
languages, decent on clean English. Our edge must be accuracy, styling control
and batch speed — not the mere existence of captions.

### 4.4 Runs on each editor's PC, not a central station

Originally specced as a watch-folder service on one shared office PC. Changed
because the shared drive is not ready and every editor has a capable machine.

This deletes three problems outright:

- **No LAN transfer.** The tool runs where the footage already is. The multi-GB
  4K copy problem in `ASH-OFFICE-NETWORK.md` simply does not arise.
- **Burn-in becomes practical.** Editors' machines have real GPUs.
- **No single point of failure**, no queue contention between six people.

It introduces one hard problem the old design did not have: **distribution**. One
station meant installing everything once. Six machines means six installs plus
every future update. That is now the main engineering risk in this project, and
it is a packaging problem, not an AI problem. See §9.

## 5. Non-goals

- Not part of Ash OS. Separate tool, separate job, separate repo.
- Not a replacement for editor review. Captions ship after a 60-second skim,
  especially names.
- Not multi-user, not networked, not internet-facing. One editor, one machine.
- Not a product. See §4.1.

## 6. Users and workflow

Six editors, non-technical, on Windows, working in Premiere Pro, DaVinci Resolve
(free) and CapCut.

**The 80% path — no UI at all.** Drag a video onto the desktop shortcut, or drop
it in `C:\AshCaptions\in\`. Defaults fire. Outputs appear in
`C:\AshCaptions\out\<video name>\`.

**The 20% path — the control page.** Click the tray icon. A page opens in the
default browser. Pick language, dialect, preset, burn-in; watch the queue;
retry a failed job; read the error when one fails.

That two-path split is deliberate. The watch folder keeps the "entire manual is
three lines" promise. The page exists because a folder cannot express "this one
in Mexican Spanish, burned in" without magic filenames, and because a tool that
silently does nothing for twenty minutes is a tool people stop using.

## 7. Languages

### 7.1 Coverage

Whisper carries 99 languages. We expose the Latin-script ones plus Arabic, banded
by real-world accuracy. Adding a language is a config entry, not a code path.

**Flagship — ship without caveats (WER ≲ 8%)**
English · Spanish · Portuguese · Italian · German · French · Dutch · Catalan ·
Polish · Indonesian

**Strong — fine for client delivery after the normal skim**
Romanian · Czech · Slovak · Croatian · Bosnian · Slovenian · Hungarian · Finnish ·
Swedish · Norwegian · Danish · Turkish · Malay · Vietnamese · Tagalog · Galician ·
Afrikaans · Estonian · Latvian · Lithuanian · Icelandic · Welsh · Swahili ·
Azerbaijani

**Works — usable, expect more correction**
Maltese · Basque · Albanian · Luxembourgish · Occitan · Javanese · Sundanese ·
Somali · Hausa · Yoruba · Lingala · Māori · Haitian Creole · Breton · Faroese ·
Turkmen · Shona · Norwegian Nynorsk · Latin

Priority languages (English, Spanish, Portuguese) all sit in the top band.
Spanish and Portuguese are among Whisper's strongest languages generally.

### 7.2 Dialects

**Whisper has one code per language, not per dialect.** There is no `es-MX`
model. Mexican, Argentine and Castilian Spanish are handled by accent robustness,
and handled well — no work is needed for a Mexican client to get accurate
Spanish.

Dialect still shows up in *output* in ways clients notice, and that we control
with three cheap levers stacked on the language code:

1. **`initial_prompt` priming** — biases vocabulary and phrasing toward the variant.
2. **Spelling convention** — the client-visible one. US "color" vs UK "colour",
   "organize" vs "organise". A US client receiving British spellings looks sloppy.
3. **Per-dialect glossary**, stacked under the existing per-client `glossary.txt`.

Presets shipped:

| Language | Presets |
|---|---|
| English | US · UK · Australian · Canadian · Indian · Irish · South African |
| Spanish | **Mexico** · Spain · Argentina · Colombia · Chile · US Latino |
| Portuguese | **Brazil** · Portugal |
| French | France · Canada (Québec) |
| German | Germany · Austria · Switzerland |
| Dutch | Netherlands · Belgium (Flemish) |

Each preset is a config entry: language code + prompt + spelling table + glossary.
Adding "Peruvian Spanish" later is a config line, not a release.

### 7.3 Arabic

Arabic is rare in our work but does occur. It is right-to-left, and it shares its
script with Urdu, so the work is shared.

| Output | v1? | Reason |
|---|---|---|
| `.srt`, `.txt`, `.en.srt` | **Yes** | Plain text. Premiere and Resolve do their own RTL rendering |
| Styled `.ass` (POP/CLEAN) | **v2** | Needs a bundled Arabic font and BiDi-aware rendering, plus eyes-on testing |

So a rare Arabic job still gets a working transcript, subtitle file and English
translation on day one. Only the styled preset waits. **Urdu inherits the same
treatment** when the v2 RTL pass happens.

Expectation to set: Arabic is good for Modern Standard, notably weaker for
Egyptian/Gulf/Levantine conversational dialect.

## 7A. Caption styling

Styled captions are a headline feature, not a formatting detail. The output an
editor burns into a client reel has to stand next to what Submagic and Veed
produce. Two static presets do not clear that bar.

This section supersedes the earlier "two solid presets, not a template
marketplace" non-goal, removed on 2026-08-29 at the owner's direction.

### 7A.1 What ASS can actually do

Almost every effect in those tools' templates is reachable with libass override
tags. This is capability we already have and were not using:

| Effect | Tag | Used for |
|---|---|---|
| Word pop / scale | `\t(0,120,\fscx118\fscy118)` | Active word punching forward |
| Karaoke fill | `\kf` | Colour sweeping through a word |
| Box behind active word | `BorderStyle=3` + `BackColour`, or `\p1` vector | Hormozi-style highlight blocks |
| Entrance | `\fad`, `\move` | Cards rising, sliding, fading in |
| Shake / emphasis | `\t` chain on `\frz` | Hype words |
| Glow / soft edge | `\blur`, `\be` | Neon and soft-shadow looks |
| Letter spacing, all-caps | `\fsp`, text transform | The wide bold short-form look |
| Position variants | `\an` + margins | Lower-third, centred, top |

**The one real limitation is colour emoji.** libass renders whatever the font
provides, and colour-emoji font support (CBDT/COLR) is unreliable. Submagic's
emoji bursts are composited, not typeset. Emoji and sticker overlays therefore
need an ffmpeg overlay pass with PNG assets — a separate rendering pipeline,
deferred to v3. Everything else in the table above ships.

### 7A.2 Styles are data

A style is a JSON file in `styles/`, never a branch in the renderer. Adding a
look is a file, not a release.

```json
{ "name": "POP BOLD",
  "font": "Montserrat ExtraBold", "size": 78, "uppercase": true,
  "letter_spacing": 1.5,
  "colors": { "text": "#FFFFFF", "active": "#00E28A", "outline": "#000000" },
  "active_word": { "effect": "scale_box", "scale": 1.18, "box": true },
  "entrance": { "effect": "rise", "duration_ms": 140 },
  "layout": { "position": "center", "max_words": 3 } }
```

Ship 8–10 looks spanning what the studio actually sells: client-safe clean,
Hormozi box, neon glow, minimal lower-third, karaoke fill, big centred hype,
rounded playful, comic. `CLEAN` and `POP` remain as two of them so nothing that
already references them breaks.

### 7A.3 The style editor

A second page in the control UI: pick a style, adjust font, size, case, colours,
active-word effect, entrance and position — then render a **~3 second preview
from the editor's actual video**, not a generic sample, and show it inline.

The preview is the point. It is what makes this feel like Veed rather than a
config file, and it is what stops an editor burning a 20-minute job before
discovering the style was wrong.

### 7A.4 Fonts are bundled

Styles are worthless if the font resolves differently on each of six machines —
that is the classic caption-styling support call. The installer therefore ships
a broad set of SIL Open Font License / Apache 2.0 faces, which permit
redistribution, covering the range short-form work actually uses: heavy display
(Anton, Archivo Black, Bebas Neue, Titan One, Alfa Slab One), modern sans
(Montserrat, Poppins, Inter, Rubik, Outfit, Manrope, Figtree, Space Grotesk),
rounded and playful (Fredoka, Baloo 2, Nunito), and hand/comic (Bangers,
Luckiest Guy, Permanent Marker, Caveat). Noto Sans covers broad Latin
diacritics; Noto Naskh Arabic is bundled ready for the v2 RTL work.

No style may reference a font that is not bundled. Validation must reject one
that does, at load time, with a message naming the missing font.

## 8. Architecture

One Python process per machine. Five components, each independently testable.

```
tray icon (pystray)
   │  starts / stops / opens UI / quits
   ▼
FastAPI + Uvicorn (in-process, 127.0.0.1, no --reload)
   ├── GET  /            → the single control page
   ├── GET  /api/jobs    → queue state as JSON
   ├── POST /api/jobs    → submit a job with options
   ├── GET  /api/events  → SSE progress stream
   └── static assets
   │
   ├── watcher (watchdog + stability check)  → enqueues dropped files
   │
   ├── queue (SQLite table, single worker thread)
   │
   └── engine
        ├── audio extract      (ffmpeg)
        ├── transcribe         (faster-whisper, word timestamps)
        ├── caption rules      (card splitting, timing, gaps)
        ├── writers            (srt / ass / txt)
        └── burn-in            (ffmpeg, optional)
```

### 8.1 Stack decisions

| Layer | Choice | Reason |
|---|---|---|
| Backend | **FastAPI + Uvicorn**, in-process | Least ceremony for one page + JSON API; async makes SSE natural. No `--reload` — its watcher subprocess fights tray lifecycle |
| Progress | **SSE** via `StreamingResponse` | One-way push is all a progress bar needs. WebSockets are duplex overkill; polling adds latency for nothing |
| Window | **Default browser at `127.0.0.1`** + pystray tray icon | Electron = a second runtime. Tauri = Rust, and PyTauri is experimental. pywebview is decent on Windows/WebView2 but is one more native binding a Windows Update can break, and nobody here is on call for that |
| Queue | **SQLite table + one worker thread** | Must survive close-and-reopen. In-memory `queue.Queue` loses everything queued on restart |
| Watching | **watchdog** + size/mtime stability | Still the standard in 2026 |
| Lifecycle | **Task Scheduler "run at logon"** + tray icon | NSSM is wrong here — a tray/GUI app cannot run as a true Windows service (no desktop session) |

### 8.2 The partial-file problem

**This is the defect most likely to ship if we are careless.** Watchdog fires
multiple `modified` events while a large file is still being copied. There is no
"copy finished" event. Naively, dropping a 6 GB 4K file in `in\` starts
transcribing a half-written file.

Required behaviour:

- On each event, record size + mtime.
- Consider the file ready only after **size and mtime are unchanged across 3
  consecutive checks, ~1.5 s apart**.
- Then confirm by opening the file with exclusive access; a `PermissionError`
  means the OS still holds a handle — wait and retry.
- **Poll the folder on a timer as a backstop**, because Windows drops some
  creation events during bulk copies. Do not trust events alone.

### 8.3 SSE detail

Check `await request.is_disconnected()` inside the generator loop. Without it,
closing a browser tab mid-job leaves a zombie generator running.

## 9. The engine

### 9.1 Model

**faster-whisper** (CTranslate2), with the model size chosen by hardware:

- CPU-only machines: `small` by default, `medium` if the machine is strong.
- GPU machines: `large-v3`.

### 9.2 Word timing

Word-level timestamps drive the POP preset, so timing quality is the feature.

**v1 uses faster-whisper's own word timestamps**, which use DTW over attention
weights — meaningfully better than vanilla Whisper's segment interpolation.

**The timing layer sits behind one interface.** If real POP captions on real
client work show visible drift against the audio, we swap in **WhisperX**
(wav2vec2 forced alignment, sub-100ms). We do not do this pre-emptively: WhisperX
pulls in PyTorch, roughly 2.5 GB with CUDA, across six machines — and install
weight is the main risk in this project. Swap on evidence, not on theory.

### 9.3 Caption rules

What makes timing feel professional, applied after transcription:

- 3–4 word caption cards
- minimum 0.5 s on screen (no flicker)
- gap-snapping between adjacent cards
- punctuation-aware line breaks
- voice-activity filtering so silence and music produce no phantom captions
- per-client `glossary.txt` force-corrects names and brand terms
- dialect spelling table applied last

## 10. Outputs

Every job produces, in `out\<video name>\`:

| File | Purpose |
|---|---|
| `*.srt` | Clean line captions. Imports directly into Premiere and Resolve |
| `*.ass` | Styled word-by-word captions, active word highlighted. Presets **CLEAN** (client-safe) and **POP** (short-form) |
| `*.txt` | Plain transcript for descriptions, blogs, client review |
| `*.en.srt` | English translation. Optional, any source language |
| `*.captioned.mp4` | Burned-in captions, ready to post. Optional |

**Input:** anything ffmpeg reads — MP4, MOV, MKV, MXF; 1080p, 4K, 60fps, HDR.
4K costs nothing extra to transcribe: only the audio track is read, so a 6 GB 4K
file transcribes as fast as a 200 MB 1080p file of the same length.

**Burn-in** is the only place resolution costs time. On a GPU machine, NVENC
makes 1080p burn-in fast and 4K viable. On CPU-only machines, 4K burn-in is slow
— take the `.srt`/`.ass` into Premiere and export from there instead.

**Retention:** outputs kept 30 days, then auto-cleaned. Inputs deleted after
successful processing (the source stays with the editor).

## 11. Packaging, install and updates

The main engineering risk. Six machines, non-technical users, no shared drive.

### 11.1 Build

**PyInstaller `onedir`** — not `onefile` (which unpacks to temp on every launch,
painful with a multi-GB payload) and **not the Python embeddable zip**, which
lacks pip and `site-packages` by default and cannot reliably install wheels with
compiled extensions — exactly what CTranslate2, numpy and av are.

`uv` manages the build venv on the developer machine only. It is a build tool
here, not a distribution mechanism; its install path surfaces terminal windows.

**ffmpeg:** ship `ffmpeg.exe` and `ffprobe.exe` in `bin\` beside the app, called
by full path. No system PATH entry. Use **BtbN's LGPL static Windows build**.

*Licensing note:* burning captions into a client deliverable triggers nothing
either way — compiled media output is not a derivative work of the encoder. The
GPL/LGPL question only concerns redistributing **our app**, which we do not do
outside the company. LGPL is still the cleaner choice at no real cost; we lose
only GPL-only encoders, which a captions tool does not need.

### 11.2 CPU first, GPU as an opt-in second step

`ctranslate2` ≥ 4.5.0 requires **cuDNN 9 + CUDA ≥ 12.3**. Older combinations need
pinned older versions (CUDA 11 + cuDNN 8 → ctranslate2 3.24.0; CUDA 12 + cuDNN 8
→ ctranslate2 4.4.0). The classic failure is `cudnn_ops64_9.dll is not found`.

Across six PCs with six different GPUs and driver vintages, that matrix is the
single most likely thing to turn rollout into a support week. Therefore:

**The installer ships CPU-only by default. GPU is a separate opt-in step, run
per-machine by Ghazi, on the machines where it is worth it.**

A CPU install that works on all six beats a GPU install that breaks on three.
Editors get a working tool on day one; GPU gets enabled afterwards where the
driver version can be seen and a mismatch fixed on the spot. For EN/ES/PT on
short-form clips, CPU with `small`/`medium` is genuinely fine — GPU mainly buys
large-v3 and fast burn-in.

GPU detection: `nvidia-smi` presence is a reliable signal (it ships with the
display driver, not only the CUDA toolkit). The GPU build pins **one fixed**
CUDA/cuDNN/ctranslate2 combination rather than matching wheels per driver.

### 11.3 Model pre-seeding

faster-whisper caches models at `%USERPROFILE%\.cache\huggingface\hub\`,
overridable via `cache_dir` or `HUGGINGFACE_HUB_CACHE`.

Approximate sizes — *unverified, confirm before finalising the build*: tiny ~75 MB,
base ~145 MB, small ~484 MB, medium ~1.5 GB, large-v3 ~3.1 GB.

**Ship the chosen model inside the installer** and point `cache_dir` at it. Six
machines pulling multiple GB from HuggingFace over the office connection is not
acceptable given the bandwidth reality in `ASH-OFFICE-NETWORK.md`.

### 11.4 Distribution and updates — two repos

- **`ash-captions`** (private) — source. Never contains client data or glossaries.
- **`ash-captions-releases`** (public) — built artifacts and a version manifest
  only. No source, no secrets.

The updater hits **unauthenticated public GitHub Release URLs**, so there is no
token to distribute across six PCs and none to rotate or leak. Source stays
private. This is the pattern to use.

*(Fallback if artifacts must also be private: a fine-grained, repo-scoped,
read-only, expiring PAT on each machine. Avoid if possible.)*

Update mechanism: **tufup** (maintained successor to the archived PyUpdater,
built on python-tuf) gives signed updates and works with PyInstaller onedir.
Signing is worth having when auto-pulling onto unattended machines.

First install is a double-click **Inno Setup** installer. Zero terminal, zero
PATH, zero Python exposed.

**Expect SmartScreen and antivirus warnings on first run** — the exe is unsigned.
Six editors, six "Windows protected your PC" dialogs. Plan for it on rollout day.

## 12. Failure handling

- A failed job stays in the queue marked `failed`, with its error visible on the
  page. It is never silently dropped.
- The input file is **not** deleted unless the job succeeded.
- Any job left `running` when the app starts is reset to `pending` — it was
  interrupted by a crash or a restart, and ffmpeg output from a killed job is not
  trusted.
- Logs go to a file reachable from the tray menu. Editors send that file, not a
  screenshot of a console.

## 13. Success criteria

1. Caption prep per short-form video drops from ~30 min to under 5.
2. Editors use it without asking for help after one demo.
3. Zero monthly captioning subscription cost for EN/ES/PT work.
4. We accept at least one Spanish or Portuguese job we would previously have
   declined or subcontracted.
5. "In-house multilingual captioning pipeline" appears on the service list.

## 14. Roadmap

**v1** — per-PC install, watch folder + control page, SRT/ASS/TXT/translate/burn,
the style library and style editor with live preview (§7A), bundled fonts,
glossary, dialect presets, CPU default with opt-in GPU.

**v2** — Arabic and Urdu RTL styling using the bundled Noto Naskh face;
per-client style presets; 1080p proof burns.

**v3** — emoji, stickers and image overlays via an ffmpeg compositing pass
(§7A.1: not achievable in ASS, needs its own pipeline).

**Later, only if justified** — WhisperX timing (if drift is observed); a Premiere
UXP panel as a thin client to this service; vocal isolation for music-heavy clips;
speaker labels for podcasts.

**Explicitly not planned** — a Resolve plugin (§4.2); productization (§4.1).

## 15. Open risks

| Risk | Mitigation |
|---|---|
| Install weight and dependency breakage across six machines | CPU-only default; pinned versions; pre-seeded model; test on one editor's PC before rolling out |
| GPU cuDNN mismatch | Opt-in, per-machine, done by Ghazi with eyes on the driver version |
| Unsigned binary triggers AV/SmartScreen | Expected; brief the team. Code signing only if it becomes a real obstacle |
| Whisper model sizes unverified | Confirm with a `pip download --no-deps` dry run before finalising the installer |
| Windows-specific cuDNN pip-wheel path unverified | Evidence found was clearer for Linux. Test on an actual Windows GPU box before committing to the GPU build |
| Editors dislike a browser tab | pywebview is a drop-in alternative if it becomes a real complaint. Not worth pre-empting |

## 16. Research provenance

Findings dated 2026-08-29, gathered by four parallel research agents.

- **Market:** Submagic, Captions.app, ZapCap, Opus Clip, Veed, Descript, CapCut,
  Vizard pricing and feature survey; Kalakar/Bayaan/Bolti competitive analysis.
- **NLE plugins:** Resolve scripting API and Workflow Integration SDK licensing
  gate; Adobe CEP→UXP transition and UXP subprocess limits; effort estimates.
- **Business:** captioning SaaS unit economics; agency-to-SaaS productization
  literature; agency AI positioning.
- **Stack:** FastAPI vs Flask; SSE vs WebSockets vs polling; browser vs
  pywebview/Tauri/Electron; queue options; watchdog partial-file pattern; Windows
  process lifecycle.
- **Packaging:** PyInstaller vs embeddable zip vs uv; ctranslate2 CUDA/cuDNN
  matrix; ffmpeg build sources and licensing; GitHub auto-update auth patterns;
  HuggingFace model cache.

Items flagged unverified by the researchers are marked as such in §11 and §15.
