# ASH Captions — Editor's Guide

Everything you need to set the tool up on your PC and use it every day.

Written and tested on a real machine on 2026-09-01. Every screenshot here is
the actual app, and every command was run exactly as written.

---

## What this tool does

You give it a video. It gives you back:

| File | What it's for |
|---|---|
| `.srt` | Plain captions. Drag straight into Premiere or DaVinci Resolve |
| `.ass` | Styled captions — the animated word-by-word look |
| `.txt` | The transcript as plain text, for descriptions and client review |
| `.en.srt` | English translation, if you asked for one |
| `.captioned.mp4` | The video with captions burned in, ready to post |

It runs entirely on your own PC. Nothing is uploaded anywhere, so client footage
never leaves the building.

---

## Part 1 — Setup (once)

You only do this once per machine. It takes about 15 minutes, most of which is
downloading.

### Step 1. Install Python

Download **Python 3.12** from <https://www.python.org/downloads/release/python-3128/>
— pick "Windows installer (64-bit)".

**When the installer opens, tick "Add python.exe to PATH" at the bottom before
clicking Install.** This is the one step that causes problems if you miss it.

Check it worked. Press `Win + R`, type `cmd`, press Enter, then run:

```
python --version
```

You should see `Python 3.12.8` or similar. If you see "not recognised", Python
wasn't added to PATH — re-run the installer, choose Modify, and tick the box.

### Step 2. Get the tool

Ghazi will give you the `ASH Captions` folder — from a USB stick, the shared
drive, or a download link. Put it somewhere sensible like:

```
C:\Users\<your name>\Desktop\ASH Captions
```

### Step 3. Open a terminal in that folder

Open the folder in File Explorer, click the address bar, type `cmd`, press Enter.
A black window opens already pointing at the right place.

### Step 4. Run the setup commands

Copy and paste these **one at a time**, waiting for each to finish.

**4a. Create the environment** (about 10 seconds):

```
python -m venv .venv
```

**4b. Install what the tool needs** (about 3 minutes — it downloads ~200 MB):

```
.venv\Scripts\python.exe -m pip install -e .
```

That single command pulls in everything: the speech engine, the web page, the
folder watcher, all of it.

**4c. Get ffmpeg** (about 2 minutes — it downloads ~230 MB):

```
.venv\Scripts\python.exe scripts\fetch_ffmpeg.py
```

**4d. Get the caption fonts** (about 1 minute):

```
.venv\Scripts\python.exe -m ash_captions.styles.fonts download
```

You should see `wrote 24 font file(s)`. If it says some failed, that's fine —
tell Ghazi which ones and carry on.

You'll also see a yellow `RuntimeWarning` mentioning "unpredictable behaviour".
**Ignore it** — it's a Python quirk about how the command is launched, not a
problem with the download. As long as the last line says it wrote the files,
you're good.

**4e. Get the speech model** (about 5 minutes — it downloads ~480 MB):

```
.venv\Scripts\python.exe scripts\fetch_model.py --model-size small
```

This is the biggest download. Do it once; it's cached forever after.

You can skip this step if you'd rather — the model downloads by itself the
first time you caption something. Doing it now just means your first real job
isn't slowed down by a 480 MB download.

### Step 5. Start the app

```
.venv\Scripts\python.exe -m ash_captions --open
```

Your browser opens at `http://127.0.0.1:8756`. **Leave the black terminal
window open** — closing it stops the app.

That's setup done.

---

## Part 2 — Captioning a video

### Start the app

Every time you want to use it:

```
.venv\Scripts\python.exe -m ash_captions --open
```

Or just click the desktop shortcut if Ghazi set one up.

### Give it your video

![The control page with a video selected](images/control-options.png)

1. In **File Explorer**, find your video. Hold **Shift**, right-click it, and
   choose **"Copy as path"**.
2. Paste that into **Video file location**. The quotes Windows adds are fine —
   the tool strips them.
3. The options appear as soon as it recognises the file.

**Why paste a path instead of uploading?** Your video is already on this
computer. Uploading it would copy several GB to a second place on the same disk
for no reason. Pasting the path means the tool reads it where it sits.

### Pick your options

| Setting | What to choose |
|---|---|
| **Language** | The language *spoken* in the video. English, Spanish, Portuguese and 51 others |
| **Dialect** | Affects spelling, e.g. English (US) gives "color", English (UK) gives "colour". For a US client, pick US |
| **Caption style** | The look. `POP` for short-form, `CLEAN` for client-safe, `ASH BRAND` for our own content |
| **Burn captions into the video** | Tick this for a ready-to-post file. Leave it off if you're taking the `.srt` into Premiere |
| **Also translate to English** | Tick for a non-English video where the client wants English subtitles |

Then click **Start captioning**.

### Watch it work

![A job running](images/queue-running.png)

The bar fills as it goes. A 60-second reel takes about 40 seconds without
burn-in. Longer videos take proportionally longer — roughly a third of the
video's length for transcription.

You can queue more videos while one is running; they process one at a time.

![A finished job](images/queue-done.png)

### Collect your files

They're in:

```
C:\AshCaptions\out\<your video's name>\
```

**Your original video is never moved, changed or deleted.**

---

## Part 3 — Designing caption styles

Click **"Design your own caption styles →"** at the top of the page.

![The style editor](images/style-editor.png)

Nine styles ship with the tool:

| Style | Use it for |
|---|---|
| **CLEAN** | Client work. Deliberately no colour, so it can't clash with a client's brand |
| **POP** | Short-form. The bold box look |
| **ASH BRAND** | Our own content — Ash colours and typeface |
| **HYPE** | Big, centred, two words at a time |
| **KARAOKE** | Colour sweeps through each word |
| **NEON GLOW** | Glowing edge |
| **LOWER THIRD** | Subtle, sits at the bottom |
| **PLAYFUL** | Rounded and friendly |
| **COMIC** | Heavy comic-book impact |

### Changing a style

Pick one on the left, then change anything: font (24 to choose from), size,
letter spacing, ALL CAPS, the four colours, how the active word behaves, how
captions enter, and where they sit.

### The preview — use this

At the bottom, paste a video path and a **start time in seconds**. Pick a moment
where someone is *talking* — a silent moment gives you a preview with no
captions in it.

Click **Render preview**. After a few seconds you get a real 3-second clip of
**your own footage** in that style.

Always preview before running a long job. It takes seconds and saves you
discovering the style was wrong after a 20-minute render.

### Saving

- **Save** — updates the style
- **Save as…** — makes a new style under a new name
- **Duplicate** — copies the current one to edit safely
- **Reset to shipped** — puts a built-in style back to how it came

> **One thing to watch.** If you save a style using a built-in name like `POP`,
> every future job using `POP` gets *your* version — including other people's.
> To make your own look, use **Save as…** with a new name instead. The editor
> marks a changed built-in as "customized locally" so you can tell.

---

## What good output looks like

A frame from a real job in **ASH BRAND** — the spoken word is highlighted in the
Ash accent colour as it's said:

![Burned-in captions](images/burned-example.png)

---

## Problems and fixes

| What you see | What to do |
|---|---|
| `'python' is not recognised` | Python isn't on PATH. Re-run the installer, choose Modify, tick "Add python.exe to PATH" |
| The browser page won't load | The terminal window was closed. Start it again with the command in Part 2 |
| **"ASH Captions is already running"** | It's already open. Look for the icon near the clock, or check for another terminal window |
| A job says **FAILED** | Click it to read the reason. Usually the file was moved or renamed after you pasted the path |
| Captions are in the wrong language | The **Language** dropdown is the language *spoken* in the video, not the one you want out. For translation, tick "Also translate to English" |
| Names or brands spelled wrong | Expected — Whisper doesn't know them. Ask Ghazi to add them to your glossary file and they'll be corrected automatically from then on |
| Captions are a plain font, not the style's | The fonts didn't all download. Re-run step 4d |
| Windows warns about the app on first run | Expected — the app isn't code-signed. Click **More info → Run anyway** |

### If you're stuck

Send Ghazi the log file:

```
C:\AshCaptions\ash-captions.log
```

That file says what actually went wrong. It's far more useful than a screenshot
of the error.

---

## Things worth knowing

**Accuracy.** English, Spanish and Portuguese are excellent. Most European
languages are very good. **Always skim the result before delivering** — names,
brands and technical words are where mistakes hide.

**Speed.** Transcription takes roughly a third of the video's length. Burn-in
takes about the same again. A 60-second reel is about 40 seconds; a 10-minute
video is about 7 minutes.

**4K is not slower to transcribe** — only the audio is read, so a 6 GB 4K file
takes exactly as long as a small 1080p one of the same length. Burning captions
*into* 4K is slow though. For 4K, take the `.srt` or `.ass` into Premiere and
export from there.

**Nothing is uploaded.** All of it runs on your PC. That's why we can sign NDAs
without a conversation about it.

**Old outputs clean themselves up** after 30 days. Move anything you want to
keep into the project folder.
