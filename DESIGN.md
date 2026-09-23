---
name: ASH Captions
description: Neutral dark chrome around footage, one ember accent, Segoe UI on a Windows desk.
colors:
  bg: "#1b1b1b"
  panel: "#212121"
  raised: "#2a2a2a"
  raised-hover: "#323232"
  mat: "#242424"
  line: "#363636"
  line-strong: "#454545"
  ink: "#f0f0f0"
  ink-soft: "#c8c8c8"
  muted: "#969696"
  on-accent: "#ffffff"
  accent: "#f0906a"
  accent-fill: "#c24e24"
  accent-fill-hover: "#b8481f"
  accent-soft: "rgba(194, 78, 36, 0.18)"
  ok: "#4ec98a"
  ok-soft: "rgba(78, 201, 138, 0.14)"
  run: "#6aa6f5"
  run-soft: "rgba(106, 166, 245, 0.14)"
  warn: "#e8b44f"
  warn-soft: "rgba(232, 180, 79, 0.14)"
  bad: "#ff8080"
  bad-soft: "rgba(255, 128, 128, 0.14)"
  pending: "#a0a0a0"
  pending-soft: "rgba(160, 160, 160, 0.14)"
typography:
  headline:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "22px"
    fontWeight: 600
    lineHeight: 1.25
  title:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.25
  subtitle:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.25
  body:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "13px"
    fontWeight: 600
    lineHeight: 1.45
  meta:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
  badge:
    fontFamily: "\"Segoe UI Variable Text\", \"Segoe UI\", system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.45
  mono:
    fontFamily: "Consolas, \"Cascadia Mono\", \"Courier New\", monospace"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.45
rounded:
  xs: "4px"
  sm: "6px"
  md: "10px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "6px"
  md: "8px"
  lg: "10px"
  xl: "12px"
  2xl: "14px"
  3xl: "16px"
  4xl: "20px"
  page: "24px"
components:
  button:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "6px 14px"
    height: "34px"
  button-hover:
    backgroundColor: "{colors.raised-hover}"
  button-primary:
    backgroundColor: "{colors.accent-fill}"
    textColor: "{colors.on-accent}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "6px 14px"
    height: "34px"
  button-primary-hover:
    backgroundColor: "{colors.accent-fill-hover}"
  button-primary-disabled:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.muted}"
  button-large:
    padding: "8px 18px"
    height: "40px"
  button-small:
    padding: "3px 10px"
    height: "28px"
  button-quiet:
    backgroundColor: "transparent"
    textColor: "{colors.accent}"
  button-quiet-hover:
    backgroundColor: "{colors.accent-soft}"
  button-subtle:
    backgroundColor: "transparent"
    textColor: "{colors.ink-soft}"
  button-subtle-hover:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
  button-danger:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.bad}"
  button-danger-hover:
    backgroundColor: "{colors.bad-soft}"
  field:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "7px 10px"
    height: "36px"
  field-search:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "7px 36px 7px 34px"
    height: "36px"
  card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "18px 20px"
  job-card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "12px"
  job-thumb:
    backgroundColor: "{colors.mat}"
    rounded: "{rounded.sm}"
    width: "160px"
    height: "90px"
  drawer:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.md}"
    padding: "16px"
    width: "320px"
  dropzone:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.ink-soft}"
    rounded: "{rounded.sm}"
    padding: "20px 14px 16px"
  nav:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    padding: "0 20px"
    height: "52px"
  nav-link:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: "6px 12px"
  nav-link-active:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
  badge:
    backgroundColor: "{colors.pending-soft}"
    textColor: "{colors.pending}"
    typography: "{typography.badge}"
    rounded: "{rounded.pill}"
    padding: "2px 9px"
  badge-running:
    backgroundColor: "{colors.run-soft}"
    textColor: "{colors.run}"
  badge-done:
    backgroundColor: "{colors.ok-soft}"
    textColor: "{colors.ok}"
  badge-failed:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad}"
  notice-error:
    backgroundColor: "{colors.bad-soft}"
    textColor: "{colors.bad}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  notice-warning:
    backgroundColor: "{colors.warn-soft}"
    textColor: "{colors.warn}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  notice-info:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.accent}"
    rounded: "{rounded.sm}"
    padding: "10px 14px"
  chip:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    rounded: "{rounded.pill}"
    padding: "5px 12px"
  chip-active:
    backgroundColor: "{colors.accent-soft}"
    textColor: "{colors.ink}"
  progress-track:
    backgroundColor: "{colors.raised}"
    rounded: "{rounded.pill}"
    height: "6px"
  toast:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "12px 14px"
    width: "min(420px, calc(100vw - 40px))"
  look-card:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "0"
---

# Design System: ASH Captions

## Overview

**Creative North Star: "The Neutral Mat"**

The interface is the mat a framer cuts around a picture: a flat, hueless grey that exists so the thing inside it can be judged. ASH Captions is a captioning station on six editors' Windows PCs, and every screen in it holds footage, a caption drawn on footage, or a card led by a frame of footage. A caption's colour is read against the shot it sits on, so the chrome must not tint or compete with the video. Every grey has equal red, green and blue; the only hue in the chrome is one ember accent taken from the client's own ASH BRAND look, and the five state colours that say running, done, failed, pending and warning. Nothing in the chrome has a hue for its own sake.

The world is dense and quiet, the density of a desk tool that sits 12 pixels from a frame and is used all day next to Premiere. Type is Segoe UI Variable Text at 14 pixels, the face Windows itself uses, with no web font loaded; the 24 bundled caption fonts are the subject of the tool, never its chrome. Surfaces are flat panels separated by hairlines and by three tonal steps of grey. Depth appears only under things that float over the page. Motion is one authored moment per page, exponential ease-out, and interaction feedback; all of it is off under reduced motion. The tool reads as one dark thing: the Queue, the Studio, Styles and Help share the same nav, buttons, fields, badges, cards and focus ring from a single stylesheet.

The Queue is where the world was carbonised: a stream of frame-led job cards, newest on top, with the intake drawer open beside it. With the words removed the page still reads as a strip of clips with a drawer of controls at its side. That is the test every new surface is held to: take the words away and it should still read as footage, a mat, and a few controls.

**Key Characteristics:**
- Neutral chrome: every grey is R = G = B, from the #1b1b1b page to the #f0f0f0 ink.
- One ember accent from the brand's own look: lifted for text (#f0906a), darkened for fills (#c24e24) so white on it clears 4.5:1.
- Footage leads: a 160x90 frame opens every job card, and the video sits on a lighter neutral mat (#242424) rather than on black.
- System type, no web font: Segoe UI Variable Text at 14px/1.45, tabular figures on anything that counts.
- Flat surfaces, hairline borders, tonal layering; shadows only on toasts, menus and popups.
- One authored motion per page (the Queue's card-enter, 420ms exponential ease-out) plus interaction feedback; none under reduced motion.
- Colour never carries state alone: a dot and a word, a sentence, a stage.
- Browser surfaces are themed: dark scrollbar, selection in the accent, placeholder at muted, `color-scheme: dark`.

## Colors

Three neutral greys for surfaces, two hairlines, three inks, one ember accent in three strengths, and five state colours each with a 14% wash.

### Primary
- **Ember** (`accent`, #f0906a): the one hue in the chrome, the ASH BRAND look's own accent lifted for 6.8:1 contrast on the dark surfaces. Links, quiet buttons, disclosure summaries, the selected look's border, the search caret and icon when the field has focus, the dot on a word that carries an override, and the focus ring at 55% alpha.
- **Ember Fill** (`accent-fill`, #c24e24): the brand value darkened until white on it reads at 4.76:1. Filled primary buttons (Start captioning, Open in Studio, Update now), the brand mark in the nav, text selection, native checkbox and radio fill, and the splitter while it is being dragged.
- **Ember Fill, Pressed** (`accent-fill-hover`, #b8481f): the primary button on hover. Darker, never lighter, because no lighter orange keeps white text at 4.5:1.
- **Ember Wash** (`accent-soft`, rgba(194, 78, 36, 0.18)): the tint under a selected thing: the active transcript chip, the selected pane tab, the selected style row, a toggled word-toolbar button, the active check row, the drawer while a file is dragged over it, and the ring around the current look.

### Neutral
- **Bench** (`bg`, #1b1b1b): the page, and the inside of every field, so an input reads as a cut into the panel rather than a box on it.
- **Panel** (`panel`, #212121): the nav, cards, the drawer form, the looks column, the transcript list, the export menu's hover row. One step up from the bench.
- **Raised** (`raised`, #2a2a2a): default buttons, chosen-file rows, the confirm strip on a card, toasts, menus and popups, the progress track, and the active nav link. Two steps up.
- **Raised, Hover** (`raised-hover`, #323232): a default button on hover. The last of the greys, and neutral like the others.
- **Mat** (`mat`, #242424): the surround for footage: the Studio stage, the job thumbnail's backing, and the look sample. Lighter than the panel so a letterbox stops reading as part of the picture and a light caption stops looking brighter than it is against black.
- **Hairline** (`line`, #363636): card, panel and nav borders, the Earlier rule, list dividers, the Studio splitters at rest.
- **Strong Hairline** (`line-strong`, #454545): button and field borders, the dashed dropzone, a running card's border, the scrollbar thumb, the spinner's track.
- **Ink** (`ink`, #f0f0f0): headings, names, body text, button labels.
- **Soft Ink** (`ink-soft`, #c8c8c8): field labels, the client name on a card, subtle buttons, the Earlier heading, prose in the guide.
- **Muted** (`muted`, #969696): meta lines, hints, placeholders, nav links at rest, the "when" on a card, the select chevron. Still 5.8:1 on the bench.
- **On Accent** (`on-accent`, #ffffff): text on an ember fill, and the bar in the brand mark.

### State
- **Running Blue** (`run`, #6aa6f5, wash `run-soft`): the running badge, the running stage line, the progress fill.
- **Done Green** (`ok`, #4ec98a, wash `ok-soft`): the done badge and stage, the worker dot, a finished progress fill, a saved glossary, the guide's progress bar and step checkboxes.
- **Warning Amber** (`warn`, #e8b44f, wash `warn-soft`): the lost-contact banner, the unsure-word underline, the caption-check chip, the dot on a tab worth opening, the update banner's reason line.
- **Failed Red** (`bad`, #ff8080, wash `bad-soft`): the failed badge and stage, the error notice, the reason block on a failed card, danger buttons (Remove), the bad-word underline.
- **Pending Grey** (`pending`, #a0a0a0, wash `pending-soft`): the pending badge and stage. A state, so it has its own token even though it is a grey.

### Named Rules
**The R = G = B Rule.** Every grey in the chrome has equal red, green and blue. A caption's colour is judged against footage, and a blue-lifted grey is a tint on the surround of the one thing the editor is looking at. Hue belongs to the footage and to state.

**The One Ember Rule.** There is one accent and it is the client's own, the ASH BRAND look's #C8542A, lifted for text and darkened for fills. Hover goes darker, never lighter, so white on the fill never drops below 4.5:1. No second accent, no gradient, no tinted panel.

**The Colour Plus a Word Rule.** State is never colour alone. A badge carries a dot and a word, a failed card carries a sentence in the editor's words, a progress bar carries a stage, an unsure word carries an underline and a count.

## Typography

**Display Font:** none. The largest type in the tool is the guide's 22px heading.
**Body Font:** Segoe UI Variable Text (with Segoe UI, system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif)
**Label/Mono Font:** Consolas (with Cascadia Mono, Courier New, monospace)

**Character:** the face the desk already reads all day, at the size Windows sets it. One family, two weights (400 and 600, with 500 for secondary labels and links), sizes between 11 and 22 pixels. Hierarchy comes from weight and from ink versus muted, not from size jumps. The 24 bundled caption fonts (Anton, Bebas Neue, Montserrat, Poppins, Noto Naskh Arabic and the rest) are what the tool is for; they appear only in the look samples, the Styles sample and the rendered captions, never in the chrome.

### Hierarchy
- **Headline** (600, 22px, 1.25): the guide's page title. The only h1 in the tool.
- **Title** (600, 16px, 1.25): card and drawer headings ("Caption a video"), the empty state's lead line, the guide's section heads at 20px.
- **Subtitle** (600, 14px, 1.25): h3, the Earlier rule at 15px in soft ink, a look group's name at 12px in muted with a hairline running out of it.
- **Body** (400, 14px, 1.45): everything read; the guide's prose at 1.6 within 72ch. A job's name is body at 600.
- **Label** (600, 13px): field labels in soft ink, the stage line on a card, button labels (600 at 14px; 500 at 13px for small and subtle buttons), disclosure summaries in ember.
- **Meta** (400, 12.5px): the client, language and look line under a job's name in muted, the "when", the worker line, hints at 13px.
- **Badge** (600, 12px): the state pill, the status pill, the check chip. Never uppercase; the word is the word.
- **Mono** (400, 12.5px): glossary entries, paths, technical details on a failed card at 12px, keyboard hints at 11px.

### Named Rules
**The Windows Face Rule.** The chrome is set in Segoe UI Variable Text and nothing is downloaded to draw it. The caption fonts are content, not chrome: a caption face in a heading or a button would make the tool look like one of its own looks.

**The Steady Digits Rule.** Anything that counts (timecodes, durations, elapsed time, dB, percentages, the "when" on a card) is set in tabular figures so a number does not change width as it ticks.

## Layout

Four pages share a 52px nav (brand mark, Queue, Studio, Styles, Help, a status line on the right) and then lay themselves out in their own stylesheet. Density is the desk's: 14px type, 34px buttons, 36px fields, 10px between cards, 12px inside a card, 20px page gutters.

**The Queue** (index.html) is a two-column grid, `minmax(0, 1fr) 320px`, 24px gap, capped at 1440px and padded 20px 20px 48px. Notices span both columns above. The stream on the left starts with the search field (up to 520px) and the worker line, then one card per live or recent job, then the Earlier rule (a heading, the count, and a hairline running to the edge) with the same cards paged twenty at a time. The drawer on the right is sticky at 16px and never taller than the window (`calc(100vh - 68px)`), scrolling inside itself so Start is always reachable. Under 1100px the drawer becomes a bar above the stream: dropzone, language, client and Start on one row in four columns, with More opening the dialect, glossary, speaker names and settings; the gap drops to 18px and the padding to 14px. Under 640px the bar stacks in two columns, the card becomes one column, and the thumbnail spans the width at 16:9. Under 600px the nav drops the brand word and the status line.

**The Studio** (studio.html) fills the viewport and does not scroll: a 56px top bar for the job, then a three-column workspace of stage, words and looks (`minmax(0, 1fr) 7px 480px 7px 280px`) whose two column widths are custom properties a splitter drags and the browser remembers. The stage keeps a 180px floor so no panel can squeeze the video to a line; anything that appears grows the middle column, which scrolls. Over 1700px the words column widens to 640px and the looks to 300px; under 1200px they narrow to 340px and 240px; under 980px the workspace is one scrolling column with no splitters.

**Styles** (style_editor.html) is `240px minmax(0, 1fr) 340px` with a 16px gap: a sticky picker, the editor with its tabs and 118px live sample, the preview. Under 1100px the preview drops under the editor; under 760px everything is one column.

**Help** (guide.html) is a reading layout: a sticky 220px contents column and a 72ch main, 40px apart, capped at 1180px; one column under 860px.

Spacing steps observed across the pages: 4, 6, 8, 10, 12, 14, 16, 20, 24. Gaps inside a component are 6 to 10; between components 10 to 14; between regions 16 to 24.

### Named Rules
**The Frame Leads Rule.** A job is led by a 160x90 frame of its own footage on a neutral mat, with everything the editor reads beside it. A card with no frame shows the mat and the words "No preview", never a placeholder graphic.

**The Stage Floor Rule.** The video is the thing being judged, so nothing that appears may shrink it: the stage has a minimum height and new panels grow a scrolling column instead.

## Elevation & Depth

Flat by default, layered by tone. The page is the bench; a panel sits one step up, a raised element two, and a 1px hairline draws the edge of each. Nothing casts a shadow at rest. A shadow appears only under an element that floats over the page rather than sitting in it: a toast, the export menu, the word-edit popup. Selection and focus are rings, not lifts.

### Shadow Vocabulary
- **Float** (`box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45)`): toasts and the export menu. The same value on both so floating things look like one family.
- **Popup** (`box-shadow: 0 10px 28px rgba(0, 0, 0, 0.45)`): the transcript word-edit popup, a touch tighter because it sits closer to what it edits.
- **Focus ring** (`box-shadow: 0 0 0 3px rgba(240, 144, 106, 0.55)`): the one ring, on every focusable element, keyboard only (`:focus-visible`). Fields also turn their border ember.
- **Current ring** (`box-shadow: 0 0 0 3px var(--accent-soft)` with a 2px ember border): the look currently on the footage.

### Named Rules
**The Flat Desk Rule.** Surfaces are flat and separated by hairlines and tonal steps. A shadow is a statement that something is not part of the page; it appears under toasts, menus and popups and nowhere else.

## Shapes

Two radii and a pill. Containers (cards, the drawer form, toasts, menus, the stage, look cards, guide figures) are gently rounded at 10px. Controls (buttons, fields, the dropzone, chosen-file rows, notices, the reason block on a failed card, the thumbnail) are tighter at 6px. Small inline things (code, kbd, the brand mark's bar, the technical-details block, colour swatches) are 4px. Badges, status pills, transcript chips, pane tabs, the choice pills on Styles and the progress track are full pills at 999px. Transport buttons and the spinner are circles.

Borders are 1px hairlines everywhere, strong hairlines on interactive edges. Two dashed borders exist and both mean "drop or drag here": the dropzone and the caption drag handle. The selected look is a 2px ember border, the only 2px border in the tool. The brand mark is a 22px ember square with a white bar near its base, the caption on its frame.

### Named Rules
**The Two Radii Rule.** 10px for a container, 6px for a control, 999px for a pill. A new component picks one of the three; it does not bring its own.

## Components

Everything here is drawn by theme.css and placed by the page. The character is restrained and tactile: solid fills, hairline edges, no gradients, states told by tone and by ember.

### Buttons
- **Shape:** tight-rounded (6px), 34px tall, 6px 14px padding, 600 weight, 7px gap to an icon or spinner. Large is 40px and 15px type; small is 28px, 13px type, 500 weight.
- **Default:** raised (#2a2a2a) with a strong hairline, ink text. Hover lifts to #323232. Export, Open folder, Copy path, Browse.
- **Primary:** ember fill (#c24e24) with white text and no visible border; hover darkens to #b8481f. One per region: Start captioning in the drawer, Open in Studio on a finished card, Update now on the banner. Disabled keeps its shape and reads muted-on-raised (5.8:1) rather than fading to a ghost.
- **Subtle:** transparent with a hairline and soft-ink text at 500; hover raises and sharpens. Secondary actions on a card and the Earlier "Show 20 more".
- **Quiet:** no fill, no border, ember text; hover shows the ember wash. Clear, Check for updates.
- **Danger:** a default button in failed red; hover shows the red wash with a red border. Remove.
- **Focus:** the one ring. **Done flash:** border and text turn done green for a moment after a copy or save.
- **Toggled:** any small button with `aria-pressed="true"` shows the ember wash, an ember border and ember text, the same in the Studio top bar, the word toolbar and the framing choices.

### Inputs / Fields
- **Style:** bench background (#1b1b1b) cut into the panel, strong hairline, 6px radius, 36px tall, 7px 10px padding, ink text, placeholder in muted. Labels sit above at 13px/600 in soft ink with 5px below.
- **Focus:** border turns ember, plus the ring. Hover keeps the strong hairline.
- **Select:** native chrome removed, a 12x8 muted chevron drawn inline at the right, 30px right padding.
- **Search:** the same field with a 16px inline-SVG magnifier at the left (34px left padding) and a `/` key hint in a kbd at the right that hides on focus; the caret and the icon turn ember while the field has focus.
- **Checkbox / radio:** native, 16px, `accent-color` ember fill, 9px from their label; the small variant is 13px muted text.
- **Range:** native, `accent-color` ember. **Colour:** a 44x32 swatch in a field frame.
- **Dropzone:** bench background, dashed strong hairline, 6px radius, centred text in soft ink with a default Browse button; while a file is over the drawer the border and text turn ember on the ember wash.
- **Disabled:** 50% opacity, not-allowed cursor.

### Cards / Containers
- **Corner Style:** 10px.
- **Background:** panel (#212121) on the bench; a hairline border.
- **Shadow Strategy:** none (see Elevation).
- **Internal Padding:** 18px 20px for a general card; 12px for a job card; 16px for the drawer form.
- **Card head:** title and any control on one baseline, 12px below.

### Job Card (signature)
The unit of the Queue. A two-column grid, `160px minmax(0, 1fr)` with a 14px gap: the frame on the left (160x90, 6px radius, on the mat, `object-fit: cover`; "No preview" in muted 11px when there is none), and on the right, 6px apart: the name (14px/600) with the state badge at the far end; the meta line (client in soft ink 600, then language and look in muted, dotted); the stage line (13px/600, coloured by state) with elapsed time in muted and the "when" pushed right in tabular figures; a progress track while running; on a failed card a reason block (red wash, red text, 8px 10px, 6px radius) whose technical details fold under a summary; then the actions (6px apart, wrapping). A running card sharpens its border to the strong hairline. A card that has just been submitted enters with `card-enter`: 420ms, `cubic-bezier(0.16, 1, 0.3, 1)`, sliding 28px in from the drawer's side; under the fold it drops 12px from above instead (`card-enter-down`); none under reduced motion. Under 640px the frame spans the card at 16:9 above the words.

### Badges
- **Style:** a pill (999px), 2px 9px, 12px/600, a 7px dot in the current colour before the word, on the matching 14% wash. Pending grey by default; running blue, done green, failed red.
- **Neutral:** the same pill with no dot for a label that is not a state.
- **Status pill (Studio):** raised fill, hairline border, muted text; ok, busy and bad tint the text and border.
- **Check chip:** a warning-amber pill with a count; disabled it goes muted on a hairline.

### Notices
- **Style:** 10px 14px, 6px radius, 500 weight, a 1px border in the state colour on the state wash. Error in red, warning in amber, info in ember (border in ember fill). The lost-contact banner is a warning; an app error is an error with `role="alert"`.
- **Update banner:** a card with an ember-fill border, the text on the left and the primary action with its consequence ("This restarts the app.") on the right.

### Progress
- **Track:** 6px pill in raised; **fill:** running blue, done green or failed red. A live fill carries a 45-degree white stripe at 30% moving 23px per second, and a minimum width of 18px so something is always visible; the stripe stops under reduced motion.
- **Spinner:** 16px circle, 2.5px strong-hairline track with an ember quarter, 0.8s spin; 14px inside a button.

### Toasts
Fixed at the bottom right, 20px in, up to 420px wide, stacked 8px apart. Raised fill, strong hairline, 10px radius, 12px 14px padding, the float shadow, entering with an 8px rise over 180ms. A done toast has a green-tinted border, a failed toast a red-tinted one. Text wraps anywhere; actions sit right; a 26px round close button in muted.

### Navigation
- **Style:** 52px panel bar with a hairline below. The brand is the ember mark plus "ASH Captions" at 15px/600 in ink, 18px before the links.
- **Links:** 6px 12px, 6px radius, 500 weight, muted at rest; hover and the current page are ink on raised. A page that cannot open yet (Studio with no job) sits at 45% and takes no pointer.
- **Status:** a 12.5px muted line at the far right. Under 600px the bar tightens to 10px gutters and drops the brand word and the status.

### Chips and Tabs
- **Transcript chip (Studio):** a pill in panel with a hairline, muted 13px text; hover sharpens; active is the ember wash with an ember border and ink text.
- **Pane tab (Studio):** a borderless pill, 5px 12px, 13px/600 muted; selected is the ember wash with an ember border; an amber dot after the label marks a pane worth opening.
- **Underline tab (Styles):** 8px 14px, 600, muted; the selected tab is ink with a 2px ember underline on the tab bar's hairline.
- **Choice pills (Styles):** radio labels as pills on the bench with a strong hairline; checked is the ember wash, ember border, 600.

### Look Card
A button the width of its column: raised fill, 2px transparent border, 10px radius, clipped. A 60px sample on the mat shows three words in the look's own font and colours (the poster; a caption canvas overlays it once it scrolls into view), then a foot with the name at 13px/600, a tag in 11px muted, and a 14x24 position glyph (a hairline 9:16 frame with an ember bar where the caption sits). Hover sharpens the border; focused is ember; current is ember with the 3px wash ring.

### Disclosure
A summary in ember at 13px/600 with a 6px chevron drawn from two borders that turns 90 degrees in 120ms. Used for the glossary, technical details and the guide's fixes.

### Menus and Popups
The export menu and the word-edit popup: raised fill, strong hairline, the float or popup shadow, 6px inner padding, rows at 7px 8px with a 6px radius that hover to panel. They are the only things that float.

### Earlier Rule
A subtitle in soft ink, the count in a hint, and a hairline that runs from the count to the edge. The one divider in the Queue; the same hairline-out-of-a-heading appears on a look group in the Studio.

### Splitter (Studio)
A 7px hit area drawn as the 1px hairline it replaces, with a 3px grip that shows on focus; hover and drag fill it ember. Closed, the looks splitter widens to 16px and becomes a raised rail so the column can be reopened.

### Named Rules
**The One Authored Moment Rule.** Each page has at most one authored motion (the Queue's card-enter). Everything else is interaction feedback under 200ms: a toast rising, a chevron turning, a progress stripe. All of it stops under `prefers-reduced-motion`.

**The Inline Icon Rule.** Icons are authored inline SVG at 12 to 16px, stroked in `currentColor`: the search magnifier, the select chevron, the transport glyphs. No icon library, no icon font.

## Do's and Don'ts

### Do:
- **Do** take every colour from the tokens on `:root` in theme.css; a page stylesheet places components, it does not recolour them.
- **Do** keep every grey neutral (R = G = B) and check a new one against the footage it will surround.
- **Do** use the ember fill (#c24e24) for one primary action per region and the ember text tint (#f0906a) for links, quiet buttons and selection.
- **Do** darken on hover (#b8481f), never lighten, so white on ember stays above 4.5:1.
- **Do** pair every state colour with a word, a dot and a word, or a sentence.
- **Do** lead a job with a 160x90 frame of its own footage on the mat (#242424).
- **Do** set the chrome in Segoe UI Variable Text at 14px/1.45 with weights 400, 500 and 600, and tabular figures on anything that counts.
- **Do** keep surfaces flat: hairlines and tonal steps for edges, the float shadow only under toasts, menus and popups.
- **Do** use 10px for containers, 6px for controls, 999px for pills.
- **Do** author at most one motion per page, 420ms `cubic-bezier(0.16, 1, 0.3, 1)` for an arrival, and turn it off under reduced motion.
- **Do** draw icons as inline SVG in `currentColor`.
- **Do** theme the browser's own surfaces: `scrollbar-color`, `::selection` in the ember fill, placeholders in muted, `color-scheme: dark`.
- **Do** keep tokens and shared components in theme.css and each page's layout in its own stylesheet, every file under 500 lines (a build rule, not a token).

### Don't:
- **Don't** add a hue to any grey; the greys this system replaced carried a blue lift and the whole point of the palette is that they no longer do.
- **Don't** introduce a second accent, a gradient, or a tinted panel; a page that needs "something to look at" has footage for that.
- **Don't** put a caption font in the chrome; the 24 bundled fonts appear only in look samples, the Styles sample and rendered captions.
- **Don't** load a web font or an icon library.
- **Don't** let colour carry state alone, and don't uppercase a badge; the word is the word.
- **Don't** shadow a card, a panel or a button; a shadow means "floating over the page" and nothing else.
- **Don't** shrink the Studio stage to make room for a panel; grow the scrolling column instead.
- **Don't** fade a disabled primary button to a ghost; keep its shape and set muted text on raised.
- **Don't** add motion beyond the page's one authored moment and sub-200ms feedback, and don't ship any of it without the reduced-motion switch.
- **Don't** reach for #0c0d11: the two blue-lifted blacks that remain (the Styles sample backing and the guide's command block) predate the neutral palette; use the mat or the bench.
- **Don't** put a settings form above a status list; the Queue's intake asks for the file, the language and the client, and every decision that needs the footage is made in the Studio.
