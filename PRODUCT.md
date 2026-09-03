# ASH Captions — Product Description

**One-liner:** An in-house captioning station for Ash Media Solutions: drop any video into a folder, get back accurate, styled, perfectly-timed captions in 54 languages — free, private, and running on our own office PC.

## The problem
Short-form clients expect styled captions on every edit. Today that means either hand-typing them (20–40 minutes per video of editor time), or paying monthly per-seat/per-minute subscriptions (Submagic, Captions.app, ZapCap), or uploading client footage to third-party clouds. At our volume, caption time is one of the biggest silent costs in every project.

## The product
ASH Captions is a watch-folder service running on the office PC ("the caption station"), powered by faster-whisper (OpenAI's open-source Whisper model) and ffmpeg.

**How an editor uses it — the entire manual:**
1. Copy your video into `captions\in\` on the shared drive.
2. Wait a few minutes.
3. Collect your outputs from `captions\out\<video name>\`.

**Every job produces:**
- `*.srt` — clean line captions, imports directly into Premiere Pro / DaVinci Resolve
- `*.ass` — styled word-by-word "pop" captions (active word highlighted), nine shipped looks (CLEAN, POP, ASH BRAND, HYPE, KARAOKE, NEON GLOW, LOWER THIRD, PLAYFUL, COMIC) plus an editor for your own
- `*.txt` — plain transcript (for descriptions, blogs, client review)
- `*.en.srt` — English translation (optional, any source language)
- `*.captioned.mp4` — captions burned in, ready to post (optional)

## Languages
Best-in-class: English, Spanish, Portuguese, French, German, Italian and other Roman-script languages. Also works for Urdu/Hindi (good, not flagship). Any language → English translation built in.

## What makes the timing feel professional
Whisper provides start/end times for every individual word. ASH Captions then applies editorial rules: 3–4 word caption cards, minimum 0.5 s on screen (no flicker), gap-snapping between cards, punctuation-aware breaks, and a voice-activity filter so silence and music never produce phantom captions. A per-client `glossary.txt` force-corrects names and brand terms.

## File support & limits (including 4K)
- **Input:** any format/resolution ffmpeg reads — MP4, MOV, MKV, MXF; 1080p, 4K, 60fps, HDR. **4K is fine**: transcription only reads the *audio* track, so a 6 GB 4K file transcribes exactly as fast as a 200 MB 1080p file of the same length.
- **Where 4K does cost time:**
  - *Copying to the station:* a multi-GB file over office Wi-Fi is slow — the caption station should be on a wired/ethernet connection, or editors copy while grabbing chai.
  - *Burn-in:* re-encoding 4K on the office PC's CPU is slow (can be several× the video length). Recommended 4K workflow: take the `.srt`/`.ass` into Premiere (seconds) and export from there; reserve burn-in for 1080p social cuts, or use the `--burn-proof` option (1080p downscaled preview burn for client approval).
- **Length:** tested target up to 30-minute videos; longer works, just slower.
- **Disk:** station keeps 30 days of outputs, then auto-cleans; inputs deleted after successful processing (source stays with the editor).

## What it costs
- Software: Rs 0 — built in-house on open-source components (faster-whisper, ffmpeg, open fonts); the ASH Captions code itself is proprietary, see `LICENSE`. No subscriptions, no per-minute fees, no seat licences.
- Hardware: the existing office PC. (A future ~modest GPU would unlock the largest model at high speed — optional.)
- Privacy: nothing is uploaded anywhere; client NDAs stay easy to sign.

## What it is not (non-goals)
- Not part of Ash OS — separate tool, separate job.
- Not a template marketplace — a curated set of looks the team actually uses, extended on request.
- Not a replacement for editor review — captions ship after a 60-second skim, especially names.

## Success criteria
1. Caption prep time per short-form video drops from ~30 min to under 5 (skim + import).
2. Editors use it without asking for help after one demo.
3. Zero monthly captioning subscription cost for EN/ES/PT work.
4. "In-house multilingual captioning pipeline" appears on the agency's service list.

## Roadmap
- **v1:** CLI + watch folder, SRT/ASS/TXT/translate/burn, CLEAN + POP styles, glossary.
- **v2:** local web page on the office LAN (language/style dropdowns, job queue view), per-client style presets, 1080p proof burns.
- **Later (only if needed):** vocal isolation for music-heavy clips (Demucs), GPU upgrade for large-v3 everywhere, speaker labels for podcasts.
