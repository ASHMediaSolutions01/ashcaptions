---
version: 1
slug: "src-ash-captions-web-static-index-html"
primary_target: "src/ash_captions/web/static/index.html"
related_targets: ["src/ash_captions/web/static/app.js","src/ash_captions/web/static/queue.js","src/ash_captions/web/static/style.css"]
---

# Queue (index.html)

Mode: Operate. The editor checks the state of the desk and drops the next video in without leaving it.

## Direction contract

THESIS: The Queue is a stream of footage, newest on top, with the intake beside it. It refuses the job-runner arrangement of a settings form above a status list: the look, the burn, the reel and behind-the-speaker are decided in the Studio where the footage is visible, so the intake asks only for the file, the language and the client.

OWN-WORLD: Neutral chrome, three greys (#1b1b1b page, #212121 panel, #2a2a2a raised) and one accent (#c24e24 fill, #f0906a text), Segoe UI Variable. Every card is led by a 160x90 frame of its own footage; with the words removed the page still reads as a strip of clips with a drawer of controls at its side.

STORY: The editor sees at once what is working, what is ready and what failed, drops or browses for the next files, and reaches anything older by typing part of its name. A finished card leads to the Studio; a failed card says why in one sentence.

FIRST VIEWPORT: At 1440 wide, a 320px drawer on the right holds a drop zone whose button is Browse, then language, dialect, client, "name who is speaking", and Start; the chosen files list under the zone. The stream on the left starts with a search field and the worker line, then one card per live or recent job (frame, name, client, language, state with progress, when, actions), then an "Earlier" rule with the count and the same cards paged twenty at a time. Below 1100px the drawer folds to a bar above the stream. Signature interaction: a submitted job's card enters the top of the stream from the drawer's side, the one authored motion on the page, exponential ease-out, none under reduced motion.

FORM: Card stream + submit drawer, the dealt lead (THE ROLL) of the three structures; seed a38fd851, decision key 4a018969. Built code-led: the runtime has no image generation.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
