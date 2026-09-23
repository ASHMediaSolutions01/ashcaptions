# ASH Captions — Status

Last verified: **2026-09-12**, in a freshly built bundle rather than from
source. Everything under "verified" below was checked by running it, not
inferred.

- Repo: `github.com/ASHMediaSolutions01/ashcaptions` (**public** from
  2026-09-03; the code stays proprietary, see `LICENSE`)
- Tests: **2429 passing, 50 skipped** (the skips are the real-ffmpeg and
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

**What this is, since it decides design questions:** a captions generator
in the Veed / Submagic category, not a video editor. Ghazi, 2026-09-10.
The chrome stays neutral because a caption's colour is judged against
footage, not because the tool wants to look like an edit suite.

**v0.10.0: the Queue reshaped, after the first design critique.** The
critique of the web UI (2026-09-23, 23/40) said the Studio was the product
and everything around it was generic tooling. Since then: a failed job
says what happened in one sentence, with Retry leading only where it can
help; the transcript panel is reachable by keyboard; and the Queue is a
stream of frame-led cards with the intake in a drawer beside it. The
drawer asks for the file, the language and the client. The look, the
burn, the 9:16 reel and captions behind the speaker are decided in the
Studio, on the footage. Browse takes several files, one Start sends the
batch, and the search reaches every job ever run, by file name or client.
The structure decision page closed unanswered, so the build took the
dealt lead ("card stream + submit drawer"); the surface brief under
`.impeccable/surfaces/` records that, and `DESIGN.md` now records the
visual world the pages share.

**v0.9.0 before it: names and emoji.** A podcast or two-person
interview can have the `.srt` say who is speaking, and a look can throw an
emoji up beside the caption -- picked from a grid in the style editor, not
from a settings file. The emoji artwork is now rendered from an OFL font, so
a client's reel carries no licence obligation of its own.

Two things were found by running the built thing rather than by testing it,
and both had been sitting on master: the emoji burn **never finished**
(`-loop 1` made the image input infinite, so ffmpeg sat at a 0-byte file
forever, with no error), and the guide's sidebar had drifted a part out of
step with its own headings when the 9:16 part was inserted, so every link
after 13 pointed at the wrong one.

**v0.8.0 before it: the reel release.** Landscape footage becomes a
9:16 reel that follows whoever is on screen, and the editor can argue with
who that is. Alongside it, the four features that had been sitting on
master since v0.7.0: case and punctuation modes, draggable Studio columns
with a neutral palette and a sound-volume slider, and box and shadow as
real controls.

v0.7.0 before it: sound effects locked to the caption word, a wider
animation vocabulary, per-word animation, the Studio layout and behaviour
fixes, and an updater that says where you stand.

**Box and shadow are real controls** (v0.6 spec, held item 4).
Box: colour, opacity, size. Shadow: colour, opacity, angle, distance --
seven of the nine properties the spec named. The shadow colour had round-
tripped untouched through Save for four versions because the editor had
nowhere to put it.

Everything was measured before it was offered, and two measurements
changed the design:

- **A Style-level `Shadow: N` and an inline `\xshadN\yshadN` are
  byte-identical** at 48/90/140px and at distance 1, 2, 4 and 8. That is
  the whole reason the shadow could move out of the Style column -- where
  it can only ever fall down-right -- and gain an angle without restyling
  a single shipped look. Proven twice: once on synthetic text, then again
  by burning all seven committed goldens old-against-new, 24 frames each.
  **Text changed, pixels identical**, which is what made re-baselining the
  goldens honest rather than convenient.
- **The default distance is 2.83, not 2.** ASS's `Shadow: 2` offsets by 2
  on *both* axes, whose true distance is 2 root 2. A default of 2 would
  have quietly pulled every look's shadow in to (1.41, 1.41) while the
  commit message claimed nothing had moved.

**Two of the nine are not built, and the reasons are measured, not
guessed:**

- **Corner radius** needs a drawn shape sized to the words. libass
  measures them; we cannot. PIL reading the same font file at the same
  size disagrees with the burn by 29 to 553px, and the ratio differs per
  face (Inter 0.68, Archivo Black 0.72, Montserrat ExtraBold 0.63), so a
  box drawn from that measurement would visibly miss.
- **Shadow blur** blurs the letters with it: in one event the letterform's
  solid pixels drop to zero. Two events -- the shadow as its own layer
  underneath, the way `render_glow` builds a halo -- keep the text sharp
  and the shadow soft (2220 white px against 0). That is a real route, but
  a second event on every caption in four emission paths is its own change.

**A shipped comment was wrong and is fixed.** `render_anim.py` said
`\blur` is "byte-identical" with a non-zero Outline. Re-measured: that was
taken with a *black outline on a black background*, where a softened black
edge is invisible to the eye and to a pixel diff alike. With a visible
outline blur does change the picture -- but the letterform's solid core
never softens (1424 -> 1441 -> 1432 solid px at radius 0, 6, 18, against
1413 -> 0 -> 0 with no outline). The v0.7 fix was right; its stated reason
was not.

**Landscape to 9:16 was measured before it was designed** (`engine/reframe.py`,
v0.6 held list item 1, shipped in v0.8.0). Five measurements on the studio's
own 1920x1080 Spanish interview shaped it, and three of them contradicted the
obvious design:

- **Nobody moves inside a shot.** The matte's centroid drifts 13-31px over
  twelve seconds in a 1920-wide frame. So the crop is decided once per shot
  and *held*. There is no path to smooth and no jitter to filter, because
  there is no per-frame tracking at all -- which is also what makes it
  affordable: 120 inferences (~3.6s) for a 4:48 clip against 8658 (~260s) to
  matte every frame.
- **The "head" estimator is worse than the centroid**, not better. Of three
  tried, the centroid of the top third of the blob drifted 56-131px against
  the mass centroid's 13-31px. The intuitive choice lost, so the code uses
  mass.
- **A centroid over the whole matte frames nobody on a two-shot.** The two
  people sat 1167px apart; a 9:16 crop of a 1080-tall frame is 606px, so
  they cannot both fit, and the centroid of both lands in the empty sofa
  between them. The alpha is split into blobs and one is chosen.
- **The choice is common, not a corner case**: 10 of 24 shots had more than
  one person. `choose_subject` takes the largest by mass and accepts an
  editor override, because nothing here listens -- telling the *speaker*
  apart needs diarisation, which is a separate model and a separate download.
- **Cut detection misses dissolves.** ffmpeg's scene score found 24 hard cuts
  but never spiked at the title card cross-dissolving into the first two-shot
  at ~5.5s, at any threshold down to 0.10. A shot list from the scene score
  alone would hold one framing across two unrelated pictures, so the samples
  subdivide a shot when they disagree with each other.

Verified by running it: the real interview rendered to a real 1080x1920 reel,
and then the *output* frames were matted again to ask where the person
actually landed. 34 of 36 windows framed the subject within a quarter-width
of centre; the two that did not are the title card and the end card, which
contain no person. Known and not yet solved: a 1080p landscape source gives
606px of real detail, so a 1080-wide reel is a 1.8x upscale, and wide title
cards lose their text to the crop.

**The reel is wired end to end and testable**: a "9:16 reel" toggle in the
Studio topbar (shown only for landscape footage), a matching checkbox on the
upload form, a `reframe` job option, a "Framing the reel" stage, and a queue
label. The crop is chained *before* the punch and the captions, because it is
the one filter that changes the frame's shape -- so the .ass is written at the
reel's size, and everything past the crop works in reel pixels. Behind-the-
speaker composes with it: the matte is cropped identically, or it would mask
the wrong part of a reframed picture.

Driven in a built bundle on the 4:48 interview: 1920x1080 in, a 1080x1920
reel out with captions at PlayRes 1080x1920. **Running it found a bug three
suites of unit tests had not**: `JobStore.set_stage` validates against a
whitelist, so the new "reframe" stage failed every reel job at run time with
"unknown stage". A test now walks the runner's own source and asserts every
stage it sets is one the database accepts and the queue page can label.

**The editor can now argue with the choice.** A **Framing** tab appears in
the Studio beside Words and Check once a reel has been burned, listing only
the shots where there was more than one person -- four or five rows, not the
thirty-six the plan holds. Click the timecode to play that shot, click the
other person, re-burn.

The correction is cheap for a reason worth keeping: the saved plan carries
every candidate's x, so re-aiming a window needs no footage, no model and no
second scan. **A corrected re-burn skips the scan entirely and is faster than
the first one.** The plan is written beside the deliverable as
`<stem>.reframe.json`; every way that file can be bad -- missing, truncated, a
version this build does not know, made for footage of a different frame size --
reads as "measure it again" rather than as a plausible-looking wrong plan.

It still does not know who is *speaking*: nothing in the pipeline listens to
the audio, and telling speakers apart needs diarisation, which is its own
model and its own download. The default follows the largest person, which on
the measured interview is consistently the guest and never the interviewer.

The last three blue-tinted greys are gone from the chrome (`#2b303c`,
`#4a5162`, `#6e7686` -- B+17 to B+24 over R), which is the tint the neutral
palette exists to remove.

**The updater refuses an archive that would write outside its staging
directory.** `zipfile.extractall` happily honours a member named
`../../evil`, and the app mirrors the extracted tree over its own install
directory straight afterwards. Reaching that point already means the
artifact matched the manifest's sha256, so an archive that does this is a
compromised manifest rather than a corrupt download -- which is exactly
when "the hash matched" is not a reason to trust it.

**Speaker names in the .srt** (v0.6 held list, the podcast item),
shipped in v0.9.0. Tick "Name who is speaking" and the transcript names the
voice each time it changes, the way a podcast transcript is written. 5
seconds for a 4:48 file. The 26 MB model ships in the bundle, beside the
matte model, so no editor waits for a download.

Measured on the reference interview before it was built:

- **A voice matches itself across the halves of one turn at cosine 0.655,
  against 0.486 for halves of different turns.** That margin of 0.169 is
  what says the hand-written Kaldi fbank front-end is *right* rather than
  merely plausible -- wrong features still produce embeddings, those still
  cluster, and the result still looks like an answer.
- **Clustered into two: 0.828 similarity within a speaker against 0.270
  across, a separation of 0.557.** One voice diced in half scores near
  zero, so a monologue is detected and gets no labels at all rather than a
  speaker change in the middle of somebody talking.
- The turn structure came out as an interview really is: one voice with 4
  turns and 48s, the other with 4 turns and 229s.

**Running it found a defect the numbers had not.** The first .srt read
"Speaker 1: Son una herramienta para / Speaker 2: autores que deciden
liberar" -- one sentence with two people's names on it. The clustering was
right; the VAD's boundaries are breaths, so a turn can begin mid-clause. A
speaker change is now held until the previous card has finished a
sentence. After that, every change lands at a full stop, and the voice the
model calls "Speaker 1" asks both of the questions -- which is the
interviewer, and is the check that the labels are the right way round.

**FOLLOWING THE SPEAKER WITH THE REEL IS NOT BUILDABLE THIS WAY, and that
is measured.** Diarisation says *when* a speaker talks, never *where* they
are on screen. The cheap link -- frame differencing on each face -- does
not work: mouth motion against speech loudness correlated at **+0.07**,
measured three times, the last with head boxes confirmed by eye on a shot
with no cut in it. Two faces in a two-shot move *together* (+0.65),
because what frame differencing mostly sees is the camera and the
lighting. A real audio-visual active-speaker model would be needed, which
is a much heavier proposition than this was. The reel still follows the
largest person, and the Framing tab is still how that gets corrected.

**Emoji bursts** (v0.6 held list, item 2), shipped in v0.9.0. The one
caption treatment ASS cannot draw -- there are no colour glyphs -- so a burst
is an image composited over the burned frame. It began settings-driven, the
way punch-in did; it is a property of the **look** now, with its own tab in
the style editor beside Sound. The keyword list stays shared with punch-in
and the sounds, because "the words that matter to this client" is one list
and nobody should keep three of them in step.

Measured before it was designed, on a real 1080x1920 reel:

- **A chain of timed `overlay` filters is the right shape.** 1, 10, 50, 150
  and 300 of them all build and run: 0.8s to 2.3s for twelve seconds of
  1080x1920. Cost tracks how many are *on* at once, not how many exist,
  because `overlay` with a false `enable` passes the frame through.
- **The alpha survives**, checked on pixels rather than assumed.

**Then burning it found a bug no amount of reading would have.** An ffmpeg
filter output pad feeds exactly one input. A graph naming the same scaled
emoji twice hands it to the first consumer and the second overlay draws
**nothing at all** -- no error, no warning, exit code 0. Reproduced on its
own afterwards to be sure it was real: two overlays, one reused pad, first
drew 12,629 px and second drew 0. Each emoji is now `split` into one
stream per time it fires, and a test asserts no scaled stream is consumed
twice.

The first burn also came out with the emoji about one caption letter tall,
which reads as a glyph in the text rather than a sticker over it; the size
is now 0.20 of the frame's short side instead of 0.12.

**The burn never finished, and every mocked test passed.** `-loop 1` on
the emoji input makes that input *infinite*: ffmpeg never reaches the end of
it and the encode sits at a 0-byte part file with no error and no progress.
Found by enqueueing a real job through the running app and watching it stay
at 1% for ten minutes on a twenty-second clip. The comment justifying the
flag was wrong on its own terms -- `overlay` defaults to `eof_action=repeat`,
which already holds the last frame of a finished input for as long as the
main input runs. The real-ffmpeg suite now burns a clip with two bursts, and
the timeout is the assertion: a regression there hangs rather than fails.

`look_card_ass.js` crossed the 500-line ceiling, so the ports of
`ass_format.py` were split into `look_card_style.js` -- a real seam:
everything there answers "what does the Python turn this style into?" and
nothing knows a card exists.

**The Studio's columns are the editor's to set, and the chrome stopped
tinting the footage.** Unreleased, on master. Ghazi: "the studio was
getting cramped and I couldn't change the size of transcript and fixes",
plus "choose a better colour scheme" and "-8 is okay but we should be able
to control it".

Measured first, at the six sizes the editors run. At 1440x900 the video
was 646x363 inside a 648x745 stage -- **382px of dead black**, nearly half
the widest column -- while the words were fixed at 480px by a media query
with no way to ask for more. Three columns, no handles.

- **Two drag handles**, either side of the words column, with the widths
  written as inline custom properties so a dragged width beats every
  breakpoint. Double-click resets one; arrow keys move it 24px; the widths
  are remembered per browser and re-clamped against the *current* window,
  so a width saved on a 1920 monitor cannot leave a 1024 laptop with no
  video. The stage keeps a 320px floor no drag can cross -- the same
  failure the three-column layout was built to prevent.
- **The looks column collapses** when dragged past 120px, and this is
  where the first version was wrong: it hid the handle with the column,
  which is a one-way door. The rail now stays, widens to 16px, retitles
  itself "Click to bring the looks back", and reopens on one click. A
  second bug found the same way: the click that ends a drag reopened what
  the drag had just closed, so a drag that moved now suppresses it.
- **The palette is neutral.** Every grey is R = G = B. The old ones carried
  a blue lift (`#1c1f26` is B+10 over R) and a periwinkle accent, sitting
  12px from footage whose colour the editor is judging. The one accent is
  now the ASH ember (`#c24e24` filled, `#f0906a` for text) -- the client's
  own brand colour out of `ash_brand.json`, not a borrowed default. The
  stage's `#000` became a neutral mat, so the letterbox stops reading as
  part of the picture.
- **Volume was invisible.** `settings.hidden = trigger === "off"` hid it on
  every silent look -- which is all 39 -- while the same panel showed Play
  buttons and the line "the volume you set here is the volume you hear".
  It now follows the Play buttons rather than the trigger, sits above the
  sound list where it is reachable without scrolling, and is a slider with
  a dB readout beside a number field: level is judged by ear while a sound
  plays. Ghazi settled the default at **-8 dB**.
- **The word popup was clamped to the window, not to its column**, so at
  1024 it ran out of a 340px words column across the looks list. It is
  clamped to `.edit-column` now, and capped to its width.
- **"Reset all overrides on this job"** became "Reset all" with the
  sentence moved to the tooltip: that one label was what wrapped the word
  toolbar to five rows. The toolbar is **255px -> 161px** at 1024.

Verified by running it: every splitter behaviour driven with a real mouse
(drag both ways, the floor, collapse, one-click restore, reload, arrow
keys, double-click reset), and a sweep of all four pages at all six sizes
-- **24/24 clean**: no horizontal scroll, nothing off screen, no interface
text under 4.5:1, no chrome surface carrying a hue, no console errors.

The contrast harness lied twice before it was trusted, which is the usual
lesson: first it scored every coloured badge at exactly 1.00 because it
never composited translucent backgrounds over what was behind them, and
then it flagged the caption-look previews -- which are drawn in the look's
own colours on purpose and are product output, not chrome.

**Case and punctuation modes** (v0.6 spec, held item 5) are in on master,
unreleased. `uppercase: true/false` became `case_mode` -- **Aa** as
transcribed, **AA** ALL CAPS, **aa** all lower case -- next to a new
`punctuation`: keep everything, drop full stops and commas while keeping
`?` and `!`, or drop every mark. The spec asked for "three punctuation
modes" without naming them; that middle one is the short-form house style
and the reason it exists is that `?` and `!` carry tone where `.` and `,`
only separate clauses. Both treat the **burned caption only** -- the
`.srt` stays as transcribed, which is the same rule uppercase always
followed.

Two decisions worth knowing:

- **`uppercase` still loads, and is now a read-only view of `case_mode`.**
  Every look already on an editor's PC says `uppercase`, so reading it is
  not a courtesy. `to_dict` writes `case_mode`; a file setting both to
  contradict each other is refused by name rather than silently resolved.
  The 39 shipped looks were migrated in place, two lines each, with no
  reformatting of the hand-written compact JSON around them.
- **The still poster applies punctuation too.** There are only four JASSUB
  slots and the Styles page holds forty cards, so the editor's own sample
  routinely loses the draw and the CSS poster is what a person is actually
  looking at -- measured in a real browser, where the sample had no canvas
  at all. Case came free from `text-transform`; punctuation had to be
  applied to the text, or clicking "No full stops or commas" appeared to
  do nothing.

Verified by running it: all nine combinations burned with the bundled
ffmpeg and read off the pixels (`WELL REALLY? STRASSE` -- comma and full
stop gone, question mark kept, eszett to SS); all nine driven in a real
browser and read back off the sample; and the whole editor path through
the running app -- Save as, restyle job 5, read the `.ass` the app wrote
-- giving 728 ALL CAPS events with no stops and an untouched `.srt`.

The drift test grew a text half. It previously compared tag *formulas*
only, so a look card could have dropped a comma the burn kept. It now
runs every word through both implementations, and its word list is built
from `_STOP_MARKS` itself: the first version was typed by hand, contained
no colon, and stayed green when a colon was deleted from the JavaScript's
copy of the set.

**The guide now documents what shipped.** With v0.7 out, the note below
about leaving the guide until the release was cut had expired: the six
editors had the animation vocabulary and the per-word animation control,
and the guide had zero mentions of bounce, blink or Animation. There is now
a **Part 6, "How captions move"** -- the Motion tab, all eight entrances and
exits with what each is for, the speed control, and two things that would
otherwise read as bugs (blur has no outline while it softens; blink goes
dark exactly twice). Part 10 gained the per-word **Animation** control and
the fact that it *replaces* the look's own movement on that word rather
than adding to it. Both copies were then read in a browser: 19 parts
numbered in order, every side-nav link resolving to a real section, all 11
figures loading, no console errors.

Two things the guide was quietly wrong about are fixed: it said **36 looks**
when the library has 39, and it never said that **none of the 39 built-in
looks uses zoom, bounce, blur or blink** -- they are for looks you make
yourself, which is otherwise a control an editor hunts for and never finds.
All eleven screenshots were recaptured at v0.7 (`scripts/guide_screenshots.py
--job 5`), including a new `motion-tab.png`, and
`docs/ASH-Captions-Guide.html` was regenerated.

**The updater worked and looked broken.** "Check the upgrade feature, it
doesn't work" -- driven end to end on the real frozen bundle, every step
completed: check, consent, 662MB download, sha256 verify, extract, detached
helper, robocopy /MIR, relaunch. What did not work was finding that out.
Its whole surface was a banner that exists only while an update exists, so
"you are on the newest version", "this copy cannot update itself" and "the
check failed" all rendered as an empty page. There is now a line at the
foot of the queue page that always says something, a "Check for updates"
button, and -- the part that matters -- a failed check reports itself as
unknown rather than being dressed up as "up to date".

**In the animation vocabulary:**
The v0.6 spec's held item 2, and the first half of item 3. Four new
entrances and exits -- **zoom, blur, blink and bounce** -- on top of
fade/rise/slide, and any single word can now carry its own animation and
duration from the Studio's word toolbar.

Nothing here rests on what libass documents. Each effect was burned onto
black at 50fps with the bundled ffmpeg and measured frame by frame before
a line of it reached `schema.py`, and two of those measurements changed
the design:

- **`\blur` is a complete no-op whenever the Style's Outline is non-zero.**
  Not weakened -- byte-identical output, at outline 0.5 and at 6 alike.
  Every legible caption look has an outline, so the obvious implementation
  would have shipped a control that does nothing at all. Blur now drops the
  border for the duration and restores it at the end. The first version
  animated the border back *alongside* the blur, which measured as one
  blurred frame and then a snap: the border reaches 1.2 within 40ms and
  that is already enough to make the rest inert.
- **An inline `\fscx` cancels a line-level one from that point in the text
  onward.** With `entrance=zoom` under `active_word=pop`, the burn showed
  word one scaling 40->100 while words two and three stood still -- the
  pop's closing `\fscx100` overrode the entrance for everything after it.
  On an event carrying a line-level zoom or bounce the active word now
  emits colour only.

Three defects found by driving the running app, none of which any test
could see:

- **The transcript API refused the new fields.** Its allowed-key list is
  derived from the dataclass, so `animation` and `duration_ms` passed the
  whitelist and were then rejected by a fall-through branch as out-of-range
  *frame fractions*. The schema accepted them and the renderer honoured
  them; only the step between the browser and the record refused them.
- **The word toolbar never saw any override.** It read `word.style` off the
  transcript response, and `TranscriptWord` has no `style` key -- the
  override lives in `meta[i].style`. So: no override dot on any word,
  "Reset all overrides" permanently disabled, and a styled word re-opened
  showing the *look's* values, which the next change then wrote back as the
  word's own. Bolding an amber word turned it white. Every flow passed
  because the local commit sets the key by hand; only a reload shows it.
- **The toolbar closed itself the moment you touched it.** `closePopup`
  cleared it, and the pointerdown that dismisses the fix-this-word popup
  fires for any click outside a word -- so the pointerdown reaching for a
  toolbar control closed the toolbar the control was in. `render` also
  called `closePopup` on every re-draw of the word list, and a re-draw
  follows every commit. Bolding a word looked like it did nothing, and a
  `<select>` could not be used at all: opening the list closed the list.

Verified by running it: 4/4 effects animate on measured pixels; 10/10
through the shipped renderer; 3/3 on a reel look (a per-word zoom on a
2.2x slot ran 111px -> 277px, a blur softened over its full 300ms);
5/5 through the real app including a burn that keeps the animation; 7/7
in the Studio with a real mouse; 8/8 on the Styles page.

The look-card drift test earned itself twice in one afternoon: it caught
the JavaScript falling behind the moment the four effects landed, and then
caught a regression where moving two tag builders into a new module
silently changed `\move`'s coordinate formatting from `283.50` to `283.5`.
Its effect list now reads the enum instead of a list typed by hand, so it
cannot quietly stop covering what it is for.

Left alone deliberately: `docs/EDITOR-GUIDE.md` and `/guide` still describe
the v0.6 vocabulary. They are written and screenshotted per release, and
v0.7 is not cut. *(Done once it was — see the top of this file.)*

`studio_word.js` and `studio_edit.js` are both at 499 lines, the ceiling
the tests enforce. The next change to either should extract its pure
helpers, the way `studio_edit_pop.js` was split out of `studio_edit.js`.

**Before that: what the Studio does when you use it.**
Ghazi's verdict on the layout fix was "I didn't like a lot of things", so
this pass drove every flow an editor runs -- play, pick a look, fix a word,
style a word, retime, compare, filter, export, burn -- and measured each
against the server instead of eyeballing a screenshot. Found and fixed:

- **Every burn threw away the editor's per-word colours and sizes, and
  their line breaks.** The runner rebuilt the cards and wrote the `.ass`
  from the saved record without its meta, so the deliverable was the one
  place the editor's work did not show, while the Words panel kept showing
  the dots. v0.6 had followed an override through the edit path only.
- **Picking a look did the same** (`QueueAdapter.restyle` rendered on its
  own, without the meta), and left the `.srt` on the previous look's line
  breaks. Every path that writes a job's outputs from its record now goes
  through one function, `rewrite_outputs`.
- **Colour a word, then drag its edge: 409 "Somebody else changed this
  transcript."** The word toolbar and the Words panel each held their own
  revision and never told the other. They do now, both ways.
- **Fix a word on Words, open Check: the old word.** The check panel never
  heard about edits. It subscribes now.
- **Picking a look showed the previous one.** A look that brings a new font
  makes the renderer fetch it first; the last paused-redraw fired at 900ms
  and the canvas kept the old track until the next pick. It redraws for 6s.
- The status pill said **"Burn queued"** until the next restyle, whatever
  the queue did.

Not reproduced after the fixes: a single 409 seen once during a burn with a
word open. Not looked at: whether any of it is to Ghazi's taste -- that is
his to say, and the reason this pass happened.

**Before that: the Studio gets its picture back.**
Reported from real use, not by a test: "the studio was too short with
caption and correction I couldn't see", and "when clicking on the captions
it takes them out of the screen". Both were one fault. The left column
held the stage, the transport, the word toolbar, the editable transcript
and the caption check, and the stage was the only row that could give way,
so every panel took its height from the video. Measured on a 1366x768
laptop: **the picture was 105px tall, and clicking a word cut it to 11** --
a black line where the thing being judged should be. Two panels were also
showing the same lines twice, taking 491 of the column's 677 pixels to do
it.

The words now have a column of their own, and Words and Check are two tabs
of one panel rather than two stacked. The video is **613px** on that same
laptop and 925px on a desktop; nothing that appears can shrink it, the
stage has a floor, and the tabs sit above the word toolbar so that
revealing it cannot move them (it could, and a click on Check then landed
on whatever slid under the pointer). The word popup flips above its word
rather than off the foot of the window, and follows the column when it
scrolls. Checked at 1024, 1366, 1600 and 1920 with the console watched.

The lesson is the one this file already carries, and it caught us again:
the sound work below was verified by burning files and driving the Styles
page, and the Studio was never opened. Nothing failed. Six editors would
have found it in a minute. `tests/test_web/test_studio.py` now fails if
anything but the video and its transport is put in that column.

**Also on master: sound effects locked to the caption word.**
The first piece of v0.7, and the one idea in all the competitor research
nobody else has. A look can fire a short sound on the word its caption
lands on -- on each sentence, on the client's keywords, or on every word.
Five sounds ship (pop, click, whoosh, impact, riser), **synthesised by
`scripts/make_sounds.py`** rather than sampled, so there is no third-party
licence travelling with the product and the whole library is 339 KB. The
Styles page gains a Sound tab where each one can be played before it is
chosen, at the volume it will be burned at.

Verified by burning, not by asserting: on a synthetic silent clip the hits
land **0 ms** from the word, measured off the decoded waveform; on the
client's real reel through the real queue, every sentence start came out
6-9x louder while the dialogue between them was untouched and the file kept
its exact length. Sound belongs to the look, like the font -- so every
shipped look defaults to `trigger: "off"`, and a test fails if one ever
does not.

One judgement is still Ghazi's and cannot be made from here: **the default
volume.** It is -8 dB under a sound normalised to -1.5 dBFS, which on the
test reel put the impact well above the dialogue. It needs an ear.

**Also on master: the look cards can no longer drift from the burn.**
`web/static/look_card_ass.js` is a hand-kept JavaScript port of the tag
formulas in `render.py`, `ass_format.py` and `render_word.py` -- the
Styles page has to animate 36 cards without a server round trip per
keystroke. Its existing test asserted against tag strings a person typed
into it, so a change on the Python side would have left it passing while
every card previewed something the burn would not produce.
`tests/test_web/test_look_card_drift.py` now runs the same inputs through
both and demands the same answer, over the whole matrix of effects,
durations, positions, alignments and colours. **It found a real
divergence on its first run**: Python's `round()` breaks an exact .5 tie
to even, JavaScript's `Math.round` breaks it upwards, so at 12.345s the
card and the burn disagreed by a centisecond. The JavaScript now rounds
the way Python does.

**v0.6.0 is what master is now, and what is published** (2026-09-05). It is
the build the six editors install and test. Built by five agents in five
worktrees from `docs/superpowers/specs/2026-09-05-v0.6-design.md`; the six
defects that mattered were all found after the merge, by running it.

- **Fix a word.** The transcript panel is editable: correct one occurrence
  or every one of them, add the correction to the client's glossary, split
  and merge lines, drag a word's timing. Nothing re-transcribes; the
  `.srt`, `.ass` and `.txt` are rewritten in about a quarter of a second.
  A second tab open on the same video is offered a reload rather than
  clobbering the first. Before this, fixing one misheard word cost a full
  re-run of the job.
- **Style one word.** Click a word and give it its own colour, size, weight
  or slant, with the scope named where you change it ("this word only") and
  a mark on every word you have overridden. Font and outline stay
  properties of the look, deliberately -- that is where per-word styling
  turns into a mess.
- **The reel look.** Three looks -- REEL ESTATE, QUIET SPLIT, BIG NUMBER --
  place each word at its own spot, at its own size and colour, and leave it
  there while the next arrives. Positions run down the frame in the order
  the words are spoken; sizes follow which word matters. An intensity dial
  takes a look from a tidy stack to the full scatter. Measured against
  Ghazi's own reference reels, frame by frame.
- **Export.** Real downloads of the video, `.srt`, `.ass`, `.txt` and the
  English file, with sizes, from the Studio, every finished queue row and
  the Styles page. Before this the product had no download anywhere: every
  route to a finished file was "Open folder".
- **The Styles page tells the truth.** It says that saving a look changes
  every job that uses it, including old ones restyled or re-burned later.
  The look cards animate instead of showing a still picture -- you could
  previously choose a motion effect having never seen motion. Exit
  animations and speeds appear, having been in the format since v0.3 and
  hidden from the UI all along.

**v0.5.1 was the build before it** (2026-09-05). It is

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

- ~~Turning a landscape interview into a 9:16 reel~~ **shipped in v0.8.0**,
  with one honest gap: it follows the largest person, not the one speaking.
  That gap is now known to be **permanent by this route**. Diarisation
  shipped in v0.9.0 and it does not close it: it says *when* a voice
  speaks, never *where* that person is in the frame, and the cheap visual
  link measured at +0.07 (see above). Closing it needs an audio-visual
  active-speaker model, which is a much larger proposition than either of
  these was.
- ~~Emoji and sticker bursts~~ **shipped in v0.9.0** (a compositing pass;
  not possible in ASS), with a picker in the style editor and the artwork
  rendered from an OFL font so the reel carries nothing.
- ~~Speaker names for podcasts~~ **shipped in v0.9.0**. Two voices, named
  in the `.srt` only, with the 26 MB model in the bundle rather than
  downloaded.
- Arabic and Urdu styled captions (right-to-left ASS; Noto Naskh is
  bundled). The karaoke looks sweep the wrong way in Arabic today, which
  the guide says. **This is the next one.**
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

## Licensing: what "internal only" fixes, and what it does not

Ghazi, 2026-09-11: "we will be using only internally". That helps, but it
answers one of two questions and the other one is the sharper one. Facts
checked rather than recalled; none of this is legal advice, and the two
starred items are worth twenty minutes of a solicitor's time before the
emoji feature is used on paid work.

**Both repositories are public right now.** `ashcaptions` and
`ashcaptions-releases` are both PUBLIC, and the 663 MB v0.8.0 artifact
downloads with an unauthenticated `curl` -- verified by doing it during this
session. So the bundle is being distributed to the world today, whatever the
intent. Every redistribution obligation is live.

**Making the releases repo private is the single highest-leverage action.**
Copyleft binds on conveying a copy to another party; handing software to your
own employees within one company is not that. Private repo + installer from a
share would retire almost all of the tool-side obligations at once, including
the awkward ones:

- **ffmpeg is GPL-2.0-or-later** and we ship the binaries. Public
  distribution means owing *corresponding source for that exact build*.
  NOTICES points at ffmpeg.org and BtbN, which is common practice but is
  the weakest link in the current set-up.
- **The matting model is GPL-3.0** (`rvm_mobilenetv3_fp32.onnx`), bundled
  inside a proprietary product that the public can download. The "separate
  onnxruntime session, mere aggregation" argument in NOTICES is reasonable
  and it is not airtight. Private distribution makes the question go away.

**None of that touches the client's video, and one thing does.*** The burned
`.mp4` leaves the building by design, so anything whose pixels land in it is
distributed no matter how private the repo is. Exactly one bundled asset does:
the **OpenMoji emoji artwork, CC BY-SA 4.0**.

- CC BY-SA §3(a) requires attribution wherever the material is *Shared* --
  and a reel handed to a client and posted is Sharing. Attribution on every
  reel is not practical.
- Whether a scaled emoji composited into a video is "Adapted Material" (which
  would put the reel itself under CC BY-SA) is a real question. The licence's
  automatic "synched in timed relation with a moving image" rule covers only
  music and sound recordings, not images -- so it is not automatic -- but the
  general test is whether the work was "altered, arranged, transformed"; we
  scale it and composite it.

**I picked OpenMoji on the wrong axis, and that was my mistake.** The choice
was made on image quality -- OpenMoji ships 618x618 where Twemoji ships only
72x72 -- when the licence difference matters far more, because ShareAlike can
reach the deliverable and a 1.8x upscale cannot.

**The clean fix was an OFL emoji font, not another PNG set -- and it is
done.*** The SIL Open Font License says the requirement for fonts to stay
under it "does not apply to any document created using the fonts", so a reel
made with one carries nothing. `scripts/fetch_emoji.py` now fetches
`googlefonts/noto-emoji` (OFL-1.1) at a pinned commit, checks it by SHA-256,
and renders the twelve emoji with Pillow's `embedded_color=True`. The font is
a build input in `build/fonts/`; it is never bundled. Twemoji would only have
swapped ShareAlike for a still-impractical attribution-on-every-reel.

Three things were measured before accepting it, and one of them reversed the
original reason for choosing OpenMoji:

- Noto's colour glyphs are CBDT bitmaps with a **single** strike. Pillow opens
  the font at ppem 109 and refuses every other size outright with "invalid
  pixel size". A glyph lands at about 122px, so a 216px sticker on a 1080 reel
  is a 1.77x upscale -- the same objection that ruled Twemoji out.
- **The upscale is invisible, and the artwork is better.** Side by side at the
  real 216px draw size, the mean alpha-edge gradient came out *higher* for
  Noto on all six emoji compared. OpenMoji's outline style loses more to a
  2.9x downscale than Noto's flat shapes lose to a 1.8x upscale. The quality
  axis I picked OpenMoji on pointed the other way.
- `Noto-COLRv1.ttf` is vector and would have removed the question entirely.
  Pillow renders it **empty** at every size tried: its FreeType binding
  handles CBDT and sbix colour bitmaps, not COLRv1 paint graphs.

OpenMoji's art filled 0.57-0.89 of its canvas, so the rendered squares
reproduce that margin (`CANVAS_FILL = 0.86`) rather than cropping tight --
`stickers.SIZE_FRACTION` was tuned by eye against those files, and a tight
crop would have made every sticker about a seventh larger for no reason.

Also corrected this session: the speaker-embedding **weights are CC BY 4.0,
not Apache-2.0** as NOTICES first claimed -- Apache-2.0 is the WeSpeaker
*code*. Attribution only, no ShareAlike, so it does not reach the output. It
is trained on VoxCeleb2, whose own terms read as research-oriented; an
upstream question, but worth knowing.

---

## Things Ghazi needs to do

1. **Roll the installer out.** v0.6.0 is published at
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
