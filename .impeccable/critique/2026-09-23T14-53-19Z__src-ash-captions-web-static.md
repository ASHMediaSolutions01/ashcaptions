---
target: the ASH Captions web UI after the Queue reshape
total_score: 28
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 2
target_identity: "file:C:\\Users\\mbila\\Desktop\\ASH Captions\\src\\ash_captions\\web\\static"
timestamp: 2026-09-23T14-53-19Z
slug: src-ash-captions-web-static
---
Method: dual-agent (A: critique2-a · B: critique2-b)

Target: src/ash_captions/web/static (Queue, Studio, Styles, Help), second run, after the failed-job work and the Queue reshape. Live app on a seeded scratch root.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Stages, percent, "Finished in", live pill. The health line is three developer facts ("Worker: running · checked just now · live") where one word would do. |
| 2 | Match System / Real World | 3 | Plain copy. A burn is a second card with the same file name; a failed Studio still says "Look: POP"; "Translate to check" is offered on an English job. |
| 3 | User Control and Freedom | 3 | Clear, Keep/Remove, Escape, Reset word / Reset all, Compare. "Fix every x" rewrites the caption files with no undo. |
| 4 | Consistency and Standards | 2 | Styles / looks / Looks; Export in three places; "Retry" and "Try again" run the same handler; the accent fill sits on 28 identical "Open in Studio" buttons so it no longer means "the next thing". |
| 5 | Error Prevention | 3 | Retry demoted when it cannot help, Start disabled until a file, duplicate toast, failed Studio hides Burn. Styles Save changes every job on the PC behind a notice, not a confirm. |
| 6 | Recognition Rather Than Recall | 3 | Frames, look samples, glyphs, remembered client, visible "/" key. Identical frames on identical names; a burned card's frame does not show the look. |
| 7 | Flexibility and Efficiency | 3 | Batch submit, drop anywhere, "/", arrows/Enter/C, roving transcript, [ and ] grips. Browse is Tab stop 148 on a full desk. |
| 8 | Aesthetic and Minimalist Design | 2 | Neutral chrome held. Five controls per card times 28; one click on a word opens 13 controls; the card is 60% empty; the drawer explains copy speed before a file is chosen. |
| 9 | Error Recovery | 3 | One sentence, folded stderr, demoted Retry, a clean failed Studio. The sentence says "re-export it" while the button under it says "Try again". |
| 10 | Help and Documentation | 3 | Thorough guide, empty-state link. The guide opens on installing Python; a failed card does not link to "Problems and fixes". |
| **Total** | | **28/40** | **Solid** (was 23/40 on 2026-09-23 morning) |

## Design Specificity Verdict

**The Studio is authored for this product. The Queue is half-authored: the frame is on the card but does not lead. The Styles page could still ship in any caption tool.**

**LLM assessment.** The Studio's three columns, the position glyph on every look, samples set in the real fonts, the "POP shows 3 words at a time" line and the Check tab's "4 uncertain words" chip would not survive a transplant. The Queue keeps the neutral greys and the one accent, and the drawer speaks in the product's voice ("Name who is speaking", "Nothing leaves this PC"), but the card is a generic row: name, badge, meta, elapsed, when, five buttons, repeated 28 times. The frame is 160 of 1056 pixels (`style.css:73`) and 9:16 footage is cropped to a letterbox slice by `object-fit: cover` (`style.css:86`), so three "reel one.mp4" cards show the same sliver of the same face. Cover the thumbnails and this is a render-farm list. The Styles page is a settings form with a tinted sample slab.

**Deterministic scan.** `impeccable detect --json` on the four pages: 7 findings, 3 rules: repeating-stripes-gradient x4 (one source, the animated `.progress-fill.live` at `theme.css:343`, reported once per page), broken-image x2 (`studio.html:30` and `:62`, hidden placeholders filled at runtime), tight-leading x1 (`guide.html`, "1.17x", source not located; the nearest is `theme.css:82` h1-h3 at 1.25). Live overlays in the running app: Queue 26 (all `div.export-menu`, hidden at scan time), Studio 10 and Styles 11 (the look-card posters: cyan text and glows on four saved looks, one 2.4:1 pair on a look sample, plus two long-line hints on the Styles page at 95 and 100 characters), Guide 1. What the detector caught that A missed: the two Styles-page hints past 75 characters (`#scope-notice-text`, the fourth type-panel hint) and the h3 line-height at 1.25 in the guide's step heads. False positives: every hidden export menu and edit popup, the progress stripes, the two placeholder images, an amber "glow" that is the detector reading its own overlay, and two "occluded text" hits on its own labels. The look-poster findings are the editors' own caption looks rendering, not chrome. Real console errors: the Queue logs a 404 for every job whose source has gone (twice each, plain and retried); the other three pages are clean.

Contrast, computed in page: body 15.1:1 on the page and 14.1:1 on a panel; muted and placeholder text 5.8:1 and 5.4:1. The folded drawer at 1000x700 is 86px tall and the first card sits inside the first viewport.

**Visual overlays.** Injection succeeded on all four pages through the live server (since stopped); the overlays were captured to `.playwright-mcp/critique-b-*.png`, not left open in a browser tab.

## Overall Impression

The failure path and the transcript are now genuinely good, and the Queue's structure is right: the stream leads, the intake is beside it, search reaches everything. What is left is the card. It carries the product's identity (the frame) at thumbnail size and spends the accent on 28 identical buttons, so a full desk reads as a list of rows rather than a strip of clips. The single biggest opportunity: **let the frame lead the card and give a finished card one action**, with the burn shown as a state of its footage rather than a second lookalike card.

## What's Working

1. **The failure path is honest end to end.** Plain reason, folded stderr, Retry demoted by `retryable`, and the failed Studio strips Burn, Export, tabs and looks.
2. **The transcript is a real instrument.** One Tab stop with roving focus (`studio_edit.js:172-178`), grips as sliders with arrow nudges, one summary line instead of a warning per row.
3. **The chrome commitment is in the tokens and mostly kept** (`theme.css:16-45`): R=G=B greys, one accent from the client's own look, a hover that clears contrast, tabular numerals on every clock.

## Priority Issues

1. **[P1] The intake is the last thing a keyboard reaches.** `index.html:48-149` places the drawer after the stream; on the seeded desk Browse is Tab stop 148 of 178 and "/" is the only shortcut. **Why:** the page's one job is dropping the next video in, and a keyboard user must pass every button on every card to do it. **Fix:** put the aside before the stream in source order (the grid areas already place it visually), add a skip link, give the drawer a key, and cut each card to two stops by folding Open folder, Copy path and Remove into the Export menu. **Suggested:** /impeccable harden.

2. **[P1] The frame does not lead, so the stream is 28 identical rows.** `style.css:71-88` gives the frame 160x90 in a 1056px card and crops 9:16 footage to a slice; `queue.js:255-279` puts a filled primary and four buttons on every finished card, Earlier included. **Why:** the direction contract's own test ("with the words removed the page still reads as a strip of clips") fails; nothing distinguishes today's job from last month's, or a burn from its source. **Fix:** show the frame at the footage's own aspect (a reel as a tall thumb), draw the chosen look's sample over a burned job's frame, keep the filled primary only in the live/recent group, and give Earlier cards a quiet Open plus the menu. **Suggested:** /impeccable layout, then /impeccable distill.

3. **[P2] A burn is a second card with the same name.** Job 37 "reel one.mp4 · POP burned in · behind speaker" sits above job 35 "reel one.mp4"; `export.js:248-256` already knows they are related. **Why:** the end of the journey is collecting the file and it lands on ambiguity; a 40-job day becomes 80 cards. **Fix:** render the burn as a line inside its source card ("Burned in POP · 20 min ago · Open folder"), or at minimum name the card "Burned: reel one.mp4" with the look on its frame. **Suggested:** /impeccable shape.

4. **[P2] One click on a word opens thirteen controls.** `studio_edit.js:153` selects the word and `:251` also opens the style toolbar. **Why:** the common case is a typo; the styling surface is the rare case and it costs two lines of transcript under the popup. **Fix:** the popup handles text and gains one item "Style this word" that opens the toolbar. **Suggested:** /impeccable distill.

5. **[P2] Two leftovers.** The Styles sample slab is `#0c0d11` (`style_editor.css:53`), a blue lift the tokens say was removed (DESIGN.md lists it under Don'ts); the failed Studio hides content but keeps the three-column grid and both splitters (`studio.js:374-379` vs `studio.css:58-68`). **Fix:** `background: var(--mat)` on `.sample-wrap`; `.workspace.failed { grid-template-columns: 1fr }` and hide the splitters. **Suggested:** /impeccable polish.

6. **[P3] Developer vocabulary on the editor's page.** The health line, the footer's "source checkout -- pull the latest code", "Or paste a path" as a third input, the copy-speed sentence first under the drop zone, and two hints past 75 characters on the Styles page. **Fix:** one word on the health line; the copy note only when a dropped file is in the list; the path field behind a small "Paste a path" link; wrap the two hints. **Suggested:** /impeccable clarify.

## Persona Red Flags

**A new editor on day one:** the first sentence under the drop zone is about copying and a 2 GB limit; Language and Dialect must both be right before Start; "Worker" means nothing; the first click on a word produces two panels; Help opens on installing Python.

**A power user with 40 jobs a day (Alex):** forty cards times five buttons, plus a burn card for every burn; search is good but there is no filter by state or client and no multi-remove; Export is two clicks per file; every job whose source moved retries its thumbnail and logs a 404 on every visit (`queue.js:126-129`).

**Ghazi debugging an editor's report:** Technical details is exactly what is needed, but no job id is visible on a card and there is no "Copy details"; the editor will say "reel one failed" and there are three reel ones. Fix: show the job id and time inside Technical details and add a copy button.

## Minor Observations

- At 1000px with More open, the drop zone stretches to 117px and the fields share no baseline (`style.css:218-236`).
- Typing in search removes the page scrollbar and the layout shifts 15px.
- "Try again" and "Retry" differ only in class; the reason is invisible.
- The Check tab shows "Translate to check" on an English source.
- The failed Studio subtitle still names a look, and the top bar still offers Open folder and Copy path.
- The Styles preview card carries its own Export button.
- The footer uses a double hyphen where the rest of the copy uses a dash.
- Guide step headings sit at line-height 1.25 (`theme.css:82`); the detector's floor is 1.3.

## Questions to Consider

1. If the Queue is a strip of clips, why does every clip need five buttons? What would the page be if a finished card were the frame with its look drawn on and one action?
2. Is a burn a job in the editor's eyes, or a state of the footage? What if "reel one.mp4" were one card that says "Transcribed · Burned in POP · Behind speaker" and Export listed both?
3. The drawer asks for the language before the tool has heard a word, and the transcriber can detect it. What if the intake asked only for the file and the client, and the language were confirmed on the card while it runs?
