---
target: the ASH Captions web UI (src/ash_captions/web/static)
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 3
target_identity: "file:C:\\Users\\mbila\\Desktop\\ASH Captions\\src\\ash_captions\\web\\static"
timestamp: 2026-09-23T11-56-33Z
slug: src-ash-captions-web-static
---
Method: dual-agent (A: critique-a · B: critique-b)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Live worker status, Ready pill, named stages. The Studio opens as three blank grey columns for ~2 s with no placeholder. |
| 2 | Match System / Real World | 2 | A failed job shows raw ffmpeg output ("exit 3199971767", a Temp path). "Upload a copy" sits under "Nothing leaves this PC". |
| 3 | User Control and Freedom | 3 | Reset word / Reset all / Reset to built-in and a confirm on Remove exist. Clicking a look silently re-styles the job with no undo. |
| 4 | Consistency and Standards | 2 | One concept, three names: "Caption style" (Queue), "Look" (Studio), "Styles" (nav). Export appears in three places at three weights. |
| 5 | Error Prevention | 2 | Retry is the primary action on a job whose file cannot be read. The nav's Studio link and the Styles preview picker both point at a failed job. |
| 6 | Recognition Rather Than Recall | 3 | Looks show a rendered sample, a position glyph and a group. Three queue cards all read "ig reel.mp4" with nothing to tell them apart. |
| 7 | Flexibility and Efficiency | 2 | The Studio has arrows, Enter, C, Space. The Queue has no shortcuts, no batch submit. Every transcript word is a Tab stop. |
| 8 | Aesthetic and Minimalist Design | 2 | Choosing HYPE adds an amber warning under every transcript line. One click on a word opens 12 controls. |
| 9 | Error Recovery | 1 | The failed-job message is a stderr dump with no cause and no next step; the failed Studio shows Burn, Export and looks that cannot work. |
| 10 | Help and Documentation | 3 | Thorough guide, "Start here" card. The guide opens on a Python install that installer users never need. |
| **Total** | | **23/40** | **Acceptable** |

## Design Specificity Verdict

**The Studio is authored for this product. The Queue and the Styles page could belong to any job runner.**

**LLM assessment.** The Studio's three columns (video, words, looks) with no timeline, the neutral chrome whose reasoning is written into `theme.css:17-32`, the single accent taken from the ASH BRAND look, and the position glyph on every look card would not survive a transplant to another product — that is the test, and the Studio passes it. The Queue is a form on the left and status cards on the right, the layout of every render farm and CI dashboard; nothing on it says "captions" except the words. The core promise — judge the caption against the footage — is only kept inside the player: every look card previews "Pick this look" on a flat slab, and the Styles page's main preview is text on a black box, while a frame of the job's own footage is one thumbnail away. The look samples sit on `#1a1d24` (`studio.css:233`, `look_card.css:53`), which is exactly the blue lift `theme.css` says was removed — the product's one stated visual commitment, broken on the element the editor stares at longest.

**Deterministic scan.** 32 static findings across the four pages: `side-tab` ×18 (17 in `guide.html`, 1 in `guide.css:48` — the callout style), `repeating-stripes-gradient` ×4 (one per page), `low-contrast` ×4 (white on the `#d65b2d` primary hover, 3.9:1 against 4.5:1 needed — `index.html`, `style_editor.html` ×3), `layout-transition` ×3 (`transition: width` at `guide.css:19` and `theme.css:333`), `broken-image` ×2, `tight-leading` ×1 (`guide.html`, 1.17×). Live overlays in the running app: Queue 13 (`text-occlusion` ×4, `gpt-thin-border-wide-shadow` ×3, `layout-transition` ×4), Studio 11 (`ai-color-palette` ×4, `dark-glow` ×3, `low-contrast`, `body-text-viewport-edge`), Styles 8 (`dark-glow` ×3, `ai-color-palette` ×4), Guide 29 (`side-tab` ×13, `line-length` ×8, `first-viewport-column-overflow`). Where the two assessments agree: the tint — A found the blue slab by reading the CSS, B's overlay flagged `ai-color-palette` and `dark-glow` on the Studio and Styles pages without being told. What the detector caught that the review missed: the primary orange hover at 3.9:1, and four `text-occlusion` hits on the Queue. False positives: `broken-image` ×2 are `#job-thumb` and `#wait-thumb`, hidden placeholders filled at runtime; `repeating-stripes-gradient` ×4 is the progress bar's stripe, a legitimate affordance.

**Visual overlays.** Injection succeeded on all four pages; the overlays were visible in the **[Human]** tab during the run and the live server has been stopped.

## Overall Impression

The Studio is the product and it is good: fast, purpose-built, honest about uncertainty. Everything around it — the Queue, the Styles page, the failure states — is generic tooling that hasn't been held to the same standard. The single biggest opportunity: **a failed job is the one moment an editor needs the tool most, and it is the moment the tool is at its worst.** Second: the look samples break the product's own neutral-chrome rule and preview on a slab instead of the footage that is right there.

## What's Working

1. **Word-fix scope escalates the way the mistake does.** "Fix this one" → "Fix every 'marketing' (4)" → "Always spell it this way" maps onto typo, mishearing and client name, without a settings page.
2. **The looks list is built for scanning.** Rendered samples, a position glyph, groups with counts, a filter, arrow/Enter/C navigation.
3. **The Check tab serves a language nobody on the desk speaks.** A dot on the tab, "5 uncertain words", wavy underlines — shape and number, not colour alone.

## Priority Issues

1. **[P1] A failed job gets no diagnosis.** Queue card and failed Studio. `queue.js:177` prints `job.error` raw (ffmpeg stderr, an exit code, a Temp path) and `queue.js:213` makes Retry primary regardless of cause. **Why:** an editor cannot tell a corrupt export from a tool bug, retries, then escalates. **Fix:** map known engine failures to one plain sentence ("This file isn't a complete video; it may still be copying. Re-export it."), put stderr behind "Technical details", demote Retry when the cause won't clear on its own, and hide Burn/Export/looks in a failed Studio. Suggested: `/impeccable clarify`, then `/impeccable harden`.
2. **[P1] Line-length warning spam.** Studio Words tab. `studio_edit.js:114` emits "N words on this line — HYPE shows 2" per line, so choosing a one-word look doubles the column and buries the uncertain-word underlines. **Fix:** regroup the transcript to the look's real lines, or show one summary and flag only lines that split badly. Suggested: `/impeccable distill`.
3. **[P1] Keyboard blockers in the Studio.** `studio.js:468` exempts INPUT/SELECT/TEXTAREA/A from the Space handler but not BUTTON, so Space on a focused Burn or word button plays the video. Every transcript word is a Tab stop; the timing grips are mouse-only (`studio_edit.js:240`). **Fix:** exempt buttons, make the transcript one Tab stop with arrow keys, give grips `role=slider` and arrow nudges. Suggested: `/impeccable audit`, then `/impeccable harden`.
4. **[P2] The look slab breaks the neutral-chrome commitment and previews on nothing.** `#1a1d24` at `studio.css:233` / `look_card.css:53`; the detector's `ai-color-palette` and `dark-glow` hits land on the same surfaces. **Fix:** render samples on a neutral grey or, better, on a frame of this job's footage. Suggested: `/impeccable polish` (colour), `/impeccable shape` (footage preview).
5. **[P2] Primary button hover fails contrast and stale targets point at failed jobs.** White on `#d65b2d` hover is 3.9:1 (four hits, Queue and Styles). `nav.js:41` follows a remembered job id without checking its status; `style_editor_preview.js:52` defaults the preview source to the newest job even when it failed. **Fix:** darken the hover, offer only `done` jobs. Suggested: `/impeccable audit`, `/impeccable harden`.

## Persona Red Flags

**Alex (power user):** Browse and the path field take one file — no batch submit; the queue has no multi-select, shortcuts or filter; each finished card shows 5 equal-weight buttons; hundreds of word Tab stops; 12 controls appear to fix one letter.

**Sam (screen reader, keyboard):** the worker status (`role=status`) re-renders every second (`app.js:316`) and chatters; the job list is `aria-live` with text churn; the looks `listbox` contains `h3` children (invalid ARIA); option names include the sample text ("Pick this look HEADLINE"); three identical "Open in Studio"/"Remove" names; the Check tab is announced as "Check•"; the disabled Framing tab gives no reason; Space is hijacked on buttons; the only copy-as-path instruction is a `#767676` placeholder at ~3.8:1, truncated.

**Riley (stress tester):** three identical "ig reel.mp4" cards with no time or folder; no paging or search; a refresh loses the Studio auto-open; the stale Studio nav link; at 1024 px card actions wrap into two rows; a finished upload job shows "No preview" plus four console 404s; the Studio is blank while loading.

## Minor Observations

- The preview renderer logged font fallbacks (Montserrat ExtraBold, Anton → LiberationSans) — verify against a burned frame; if real, WYSIWYG is broken for those looks.
- Guide: 18 `side-tab` callouts and 8 over-long lines; `line-height 1.17` on one block; the first viewport overflows its column.
- `transition: width` on the guide sidebar and the queue progress bar (`theme.css:333`) — animate `transform` instead.
- Grammar: "Saving changes "ASH BRAND" for every job". "Aa ALL CAPS" renders as "AA". "BOTTOM LEFT BO…" truncates at 1440 px with room to spare. "Show English" is offered on an English job. "Always spell it this way" is styled as a link among buttons. Export appears three ways. The guide opens on the Python install.

## Questions to Consider

1. Why judge looks on a tinted slab when every job has footage? What if each look card rendered on a frame of *this* video?
2. Should the transcript show the lines the chosen look will actually produce, instead of warning about the ones it won't?
3. Does the Queue need a permanently open form, or should it be a drop target plus the list, with the options moving into the Studio before burn?
