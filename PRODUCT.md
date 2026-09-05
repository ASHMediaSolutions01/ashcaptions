# ASH Captions — what it is

**One line:** a captioning station that runs on each editor's own Windows PC.
Give it a video, get back accurate, styled, correctly-timed captions in 54
languages, with nothing uploaded anywhere.

## The problem

Short-form clients expect styled captions on every edit. That means either
typing them by hand, 20 to 40 minutes of editor time per video, or paying a
per-seat subscription and uploading client footage to somebody else's cloud.
At our volume caption time is one of the biggest silent costs in a project,
and the upload is the part a client NDA makes awkward.

## What an editor does

1. Open ASH Captions from the Desktop icon (it is already running in the
   tray) and click **Browse…** for the video. Or drop the file into
   `C:\AshCaptions\in\`, or into `in\<Client>\` to use that client's glossary.
2. Pick the language and a look, then **Start captioning**.
3. When it finishes the **Studio** opens: the video plays with the captions
   drawn on it, and every look can be tried on the real footage before
   anything is burned.
4. Collect the files from `C:\AshCaptions\out\<video name>\`.

## What every job produces

| File | What it is for |
|---|---|
| `.srt` | Plain captions. Drags straight into Premiere or DaVinci Resolve. |
| `.ass` | The styled, word-by-word look, ready to burn or import. |
| `.txt` | The transcript as text, for descriptions and client review. |
| `.en.srt` | The English translation, when asked for. |
| `.captioned.mp4` | The video with the captions burned in, ready to post. |

## What it does that the paid tools do

- **39 looks**, word-by-word with the spoken word highlighted, grouped by
  where they sit: bottom, centre, top, lower third, with left and right
  variants. Three of them place each word at its own spot on the frame and
  leave it there while the next arrives -- the treatment on the reels that
  get shared around. All of them are JSON files, so a new look needs no code.
- **The Studio.** The video plays in the browser with the captions rendered
  by the same engine that burns them, so what you see is what you get.
  Clicking a look re-renders it in about a quarter of a second.
- **Put the caption anywhere.** Drag it off a face or a logo. The position
  belongs to the job, survives changing the look, and is what gets burned.
- **Fix a wrong word without re-running anything.** Click it in the
  transcript, type the right one, and the caption files are rewritten in a
  quarter of a second. Fix every occurrence at once, or teach the client's
  glossary so the next job gets it right while it is still transcribing.
- **Make one word stand out.** Its own colour, size, weight or slant,
  without touching the look or any other video.
- **Captions behind the speaker.** A person mask is rendered from the frame
  so words pass behind the speaker's head and shoulders. Aimed at reels.
- **Punch-in**, an optional slow zoom on sentence starts or keywords.
- **Clients and glossaries.** A client on every job, with its own list of
  names and brands that force-correct in the transcript.

## What it does that they do not

- **Nothing leaves the building.** Transcription, styling and burn-in all run
  on the editor's PC. Every competitor except DaVinci Resolve uploads the
  footage; CapCut's terms also claim broad rights over what you upload.
- **Checking a language nobody on the desk speaks.** For a Spanish or Arabic
  job the transcript panel shows the English line under each source line, and
  underlines the words the speech model was unsure of, with a count and a
  jump-to-next. An editor who speaks none of the language can still tell
  whether the captions are safe to deliver.
- **No subscription, no per-minute fee, no seat count.**

## Languages

Excellent: English, Spanish, Portuguese, French, German, Italian and the
other Roman-script languages. Good, not flagship: Urdu and Hindi. Any
language can also be translated to English in the same run. Arabic and Urdu
transcribe and render, but the karaoke-style looks sweep the wrong way for
right-to-left text, so use a plain look or the `.srt` for those.

## Speed and limits, measured on the office CPU

| Stage | Measured |
|---|---|
| Transcription | 10.4 minutes of audio in 110 seconds, about 5.7x realtime |
| Burn-in, 1080x1920 | About 3.4x realtime |
| Captions behind the speaker | Roughly realtime on top of the burn |
| A 20 second reel, start to finished MP4 | About 15 seconds |

- **Input:** anything ffmpeg reads, at any resolution. 4K costs nothing extra
  to transcribe, because only the audio is read. It does cost real time to
  burn in, so for 4K take the `.srt` or `.ass` into Premiere and export from
  there.
- **Length:** an hour-long file is fine and stays within about 1.6 GB of
  memory. The longest real file put through end to end so far is 10 minutes;
  the first hour-long client job is worth watching.
- **Disk:** outputs are kept for 30 days and then cleaned up. A file dropped
  into the watch folder is deleted after it succeeds; the editor's original
  is never touched.

## What it costs

Nothing per month. It is built on open-source components (faster-whisper,
ffmpeg, libass, open fonts); the ASH Captions code itself is proprietary,
see `LICENSE`. The comparable tools run 3 to 25 US dollars per seat per
month, which for six editors is a subscription we do not pay.

## What it is not

- Not a product for sale. The regional captioning market is occupied and
  priced near seven dollars a month; this exists because it is ours and the
  footage stays here.
- Not a video editor. Only captions: no timeline, no effects, no auto B-roll.
- Not a replacement for a skim before delivery, especially for names.

## Where it is going

Shipped: everything above, as v0.6.0. Next, in the order an editor hits the
gap: sound effects landing on the caption word; a wider animation vocabulary
(zoom, blur, blink, bounce); turning a landscape interview into a 9:16 reel
with the crop following the speaker; emoji bursts; right-to-left looks for
Arabic and Urdu. `docs/STATUS.md` holds the current state and the full
roadmap.
