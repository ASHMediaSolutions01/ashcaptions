"""Capture every figure in the editor's guide from a running ASH Captions.

Run on the dev machine against a real app so the screenshots always show
the UI of the version being shipped (spec v0.5 section 5). Headless
Chromium at 1440x900, dark colour scheme, device scale 1.

    set ASH_CAPTIONS_ROOT=C:\\Users\\mbila\\AppData\\Local\\Temp\\ashlive
    .venv\\Scripts\\python.exe -m ash_captions            (its own terminal; tray-only)
    .venv\\Scripts\\python.exe scripts\\guide_screenshots.py --job 5

Writes PNGs into src/ash_captions/web/static/guide/ and copies them to
docs/images/ (the markdown guide's copies). ``--only NAME`` captures one
figure; ``--base`` and ``--job`` override the app URL and the Studio job.

What is real and what is staged: the queue figures render the live job
list through ``AshQueue.render`` with statuses rewritten (one running row,
one failed row) so the guide shows every state without waiting for a job;
ids and filenames stay real so the thumbnails load. The Studio figures are
real. ``moving-caption.png`` and ``check-captions.png`` use the real
elements when tracks A and B are merged (``.caption-drag`` /
``.transcript-panel`` present) and otherwise inject the markup from the
plan's Interfaces block, printing "staged" so the run after integration
is not forgotten.

Note (track E, v0.5): this script was written and unit-tested but was not
run for this build. Tracks A and B (caption drag, caption check) were not
yet merged, and the plan asked this track not to `pip install playwright`
or touch pyproject.toml, so no browser capture happened here. The existing
PNGs under web/static/guide/ and docs/images/ (from the pre-v0.5 build)
were left in place so the in-app guide keeps rendering. Once Playwright is
installed and A/B are merged, run this with `--job 5` to refresh every
figure, including the two new ones, then scripts/export_guide.py.

Playwright is imported inside ``main`` so the pure helpers (``FIGURES``,
``stage_jobs``, ``spoken_moment``) are testable without it.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO / "src" / "ash_captions" / "web" / "static"
GUIDE_DIR = STATIC_DIR / "guide"
DOCS_IMAGES = REPO / "docs" / "images"
DEFAULT_BASE = "http://127.0.0.1:8756"
DEFAULT_JOB = "5"
VIEWPORT = {"width": 1440, "height": 900}
CLIENT_HEADERS = {"X-ASH-Client": "1"}
FAILED_ERROR = "The video file could not be found. It was moved or renamed after the job was queued."

# The look every reader knows from the guide's burned example.
EXAMPLE_LOOK = "ASH BRAND"

# ---- pure helpers (tested) ----


def stage_jobs(jobs: list[dict[str, Any]], now: datetime, scene: str) -> list[dict[str, Any]]:
    """Rewrite the live job list into the states one guide figure shows.

    ``running``: the newest finished job becomes a running one (62 %,
    transcribing, 3 min 12 s in), the next two stay done.
    ``done``: a finished row, a failed row, and a waiting row.
    Ids, filenames and output folders are kept, so ``/api/jobs/{id}/thumb``
    still answers for every row.
    """
    done = [dict(j) for j in jobs if j.get("status") == "done"]
    if len(done) < 2:
        raise SystemExit("guide_screenshots: the queue needs at least two finished jobs")
    rows = [dict(j, status="done", progress=1.0, stage=None, error=None) for j in done[:3]]
    for row in rows:
        row["started_at"] = (now - timedelta(minutes=9, seconds=40)).isoformat()
        row["updated_at"] = (now - timedelta(minutes=7, seconds=25)).isoformat()
    if scene == "running":
        rows[0].update(
            status="running", progress=0.62, stage="transcribe",
            started_at=(now - timedelta(minutes=3, seconds=12)).isoformat(),
            updated_at=now.isoformat(),
        )
        return rows
    if scene == "done":
        rows[1].update(
            status="failed", progress=0.0, error=FAILED_ERROR,
            started_at=(now - timedelta(minutes=1, seconds=30)).isoformat(),
            updated_at=(now - timedelta(seconds=49)).isoformat(),
        )
        if len(rows) > 2:
            rows[2].update(status="pending", progress=0.0, started_at=None, created_at=(now - timedelta(seconds=20)).isoformat())
        return rows
    raise ValueError(f"unknown scene {scene!r}")


_SRT_TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)")


def spoken_moment(srt_text: str, cue_index: int) -> float:
    """A moment 0.3 s into the ``cue_index``-th cue of an .srt (the last
    cue when there are fewer), so the frame shows a caption being spoken."""
    starts = []
    for line in srt_text.splitlines():
        if "-->" in line:
            m = _SRT_TIME.match(line.strip())
            if m:
                h, mi, s, ms = (int(x) for x in m.groups())
                starts.append(h * 3600 + mi * 60 + s + ms / 1000)
    if not starts:
        return 0.0
    return round(starts[min(cue_index, len(starts) - 1)] + 0.3, 3)


# ---- browser helpers ----


@dataclass
class Session:
    base: str
    job: str
    page: Any
    original_look: str = ""
    spoken_at: float = 0.0


def api(session: Session, suffix: str) -> str:
    return f"{session.base}/api/jobs/{session.job}{suffix}"


def get_json(session: Session, url: str) -> Any:
    res = session.page.request.get(url, headers=CLIENT_HEADERS)
    if not res.ok:
        raise SystemExit(f"guide_screenshots: GET {url} -> {res.status}")
    return res.json()


def get_text(session: Session, url: str) -> str:
    res = session.page.request.get(url, headers=CLIENT_HEADERS)
    return res.text() if res.ok else ""


def open_control_page(session: Session) -> None:
    page = session.page
    page.goto(f"{session.base}/", wait_until="networkidle")
    page.wait_for_function("document.querySelectorAll('#preset-select option').length > 0")
    page.evaluate("document.getElementById('start-here').hidden = true")
    page.evaluate("document.fonts.ready")


def render_staged_queue(session: Session, scene: str) -> None:
    jobs = get_json(session, f"{session.base}/api/jobs")
    staged = stage_jobs(jobs, datetime.now(timezone.utc), scene)
    # Freeze the queue on the staged rows: the page's own refreshes and the
    # event stream would otherwise put the real statuses back mid-shot.
    session.page.evaluate(
        "(jobs) => { const real = AshQueue.render; AshQueue.render = () => {}; real(jobs); }", staged
    )
    session.page.wait_for_function(
        "Array.from(document.querySelectorAll('img.job-thumb')).every((i) => i.complete)", timeout=15000
    )
    session.page.wait_for_timeout(400)


def shoot(session: Session, name: str, selector: str | None = None, clip: dict | None = None) -> None:
    target = GUIDE_DIR / name
    if selector:
        session.page.locator(selector).first.screenshot(path=str(target))
    else:
        session.page.screenshot(path=str(target), clip=clip)
    print(f"wrote {target.relative_to(REPO)}")


def open_studio(session: Session) -> None:
    page = session.page
    if page.url != f"{session.base}/studio/{session.job}":
        page.goto(f"{session.base}/studio/{session.job}", wait_until="networkidle")
    page.wait_for_function("document.getElementById('status-pill').textContent === 'Ready'", timeout=90000)
    page.wait_for_function("document.querySelectorAll('.look').length > 0")
    page.evaluate("document.fonts.ready")
    if not session.original_look:
        session.original_look = get_json(session, api(session, ""))["options"]["preset"]
        session.spoken_at = spoken_moment(get_text(session, api(session, "/srt")), 2)


def seek_to_spoken_moment(session: Session) -> None:
    page = session.page
    page.evaluate(
        "(t) => { const v = document.getElementById('video'); v.pause(); v.currentTime = t; }", session.spoken_at
    )
    page.wait_for_function(
        "(t) => { const v = document.getElementById('video'); return v.readyState >= 2 && Math.abs(v.currentTime - t) < 0.25; }",
        arg=session.spoken_at,
        timeout=30000,
    )
    page.wait_for_timeout(900)  # JASSUB draws on its own worker; give it a beat


def apply_look(session: Session, name: str) -> None:
    page = session.page
    page.click(f'.look[data-name="{name}"]')
    page.wait_for_function("document.getElementById('status-pill').textContent === 'Ready'", timeout=30000)
    page.wait_for_timeout(900)


STAGED_DRAG_MARKUP = """
() => {
  const frame = document.getElementById('frame');
  const r = document.getElementById('video').getBoundingClientRect();
  const el = document.createElement('div');
  el.className = 'caption-drag';
  el.setAttribute('role', 'button');
  el.tabIndex = 0;
  el.setAttribute('aria-label', 'Caption position');
  el.style.left = Math.round(r.width * 0.18) + 'px';
  el.style.top = Math.round(r.height * 0.72) + 'px';
  el.style.width = Math.round(r.width * 0.64) + 'px';
  el.style.height = Math.round(r.height * 0.14) + 'px';
  const label = document.createElement('span');
  label.className = 'caption-drag-label';
  label.textContent = 'Drag to move · arrow keys nudge · Esc resets';
  el.appendChild(label);
  frame.appendChild(el);
  el.focus();
}
"""

STAGED_CHECK_MARKUP = """
(lines) => {
  const old = document.getElementById('transcript');
  const panel = document.createElement('div');
  panel.className = 'transcript-panel';
  panel.id = 'transcript-panel';
  panel.innerHTML =
    '<div class="transcript-head">' +
    '<button type="button" class="chip-warn" id="uncertain-chip">12 uncertain words</button>' +
    '<label class="toggle"><input type="checkbox" id="show-english" checked><span>Show English</span></label>' +
    '</div><div class="transcript-lines" id="transcript-lines"></div>';
  const list = panel.querySelector('#transcript-lines');
  lines.forEach((line, i) => {
    const row = document.createElement('div');
    row.className = 'line' + (i === 1 ? ' active' : '');
    const t = document.createElement('span'); t.className = 't'; t.textContent = line.t;
    const body = document.createElement('div');
    const src = document.createElement('p'); src.className = 'src';
    line.words.forEach((w, k) => {
      const span = document.createElement('span');
      span.className = 'word' + (w.mark ? ' uncertain-' + w.mark : '') + (i === 1 && k === 2 ? ' current' : '');
      span.textContent = w.text;
      src.appendChild(span);
      src.appendChild(document.createTextNode(' '));
    });
    const en = document.createElement('p'); en.className = 'en'; en.textContent = line.en;
    body.append(src, en);
    row.append(t, body);
    list.appendChild(row);
  });
  old.replaceWith(panel);
}
"""


def staged_check_lines(session: Session) -> list[dict]:
    """Three real source lines from the job's .srt (and its .en.srt when the
    job was translated), with two words marked uncertain for the figure.

    There is no route for the English .srt (GET /api/jobs/{id}/files lists
    names and sizes only), so it is read from the job's output folder on
    disk: the script runs on the machine the app runs on."""
    src = _cues(get_text(session, api(session, "/srt")))[:3]
    english = ["", "", ""]
    output_dir = get_json(session, api(session, "")).get("output_dir")
    if output_dir:
        en_files = sorted(Path(output_dir).glob("*.en.srt"))
        if en_files:
            english = [c["text"] for c in _cues(en_files[0].read_text(encoding="utf-8", errors="replace"))[:3]] or english
    out = []
    for i, cue in enumerate(src):
        words = [{"text": w, "mark": None} for w in cue["text"].split()]
        if i == 1 and len(words) > 3:
            words[1]["mark"] = "amber"
            words[3]["mark"] = "red"
        m, s = divmod(int(cue["start"]), 60)
        out.append({"t": f"{m}:{s:02d}", "words": words, "en": english[i] if i < len(english) else ""})
    return out


def _cues(srt_text: str) -> list[dict]:
    cues = []
    for block in srt_text.replace("\r", "").split("\n\n"):
        lines = [l for l in block.split("\n") if l.strip()]
        idx = next((i for i, l in enumerate(lines) if "-->" in l), -1)
        if idx < 0:
            continue
        m = _SRT_TIME.match(lines[idx].strip())
        start = (int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3]) + int(m[4]) / 1000) if m else 0.0
        text = " ".join(lines[idx + 1 :]).strip()
        if text:
            cues.append({"start": start, "text": text})
    return cues


# ---- the figures ----


def fig_control_idle(s: Session) -> None:
    open_control_page(s)
    render_staged_queue(s, "done")
    shoot(s, "control-idle.png")


def fig_queue_running(s: Session) -> None:
    open_control_page(s)
    render_staged_queue(s, "running")
    shoot(s, "queue-running.png", selector=".queue-card")


def fig_queue_done(s: Session) -> None:
    open_control_page(s)
    render_staged_queue(s, "done")
    shoot(s, "queue-done.png", selector=".queue-card")


def fig_lost_contact(s: Session) -> None:
    open_control_page(s)
    render_staged_queue(s, "done")
    s.page.evaluate("document.getElementById('connection-banner').hidden = false")
    shoot(s, "lost-contact.png", clip={"x": 0, "y": 0, "width": 1440, "height": 460})


def fig_studio(s: Session) -> None:
    open_studio(s)
    seek_to_spoken_moment(s)
    shoot(s, "studio.png")


def fig_moving_caption(s: Session) -> None:
    open_studio(s)
    seek_to_spoken_moment(s)
    if s.page.locator(".caption-drag").count():
        s.page.focus(".caption-drag")
        s.page.hover(".caption-drag")
    else:
        print("moving-caption.png: staged (.caption-drag not on the page; rerun after track A is merged)")
        s.page.evaluate(STAGED_DRAG_MARKUP)
    s.page.wait_for_timeout(200)
    shoot(s, "moving-caption.png", selector="#stage")


def fig_check_captions(s: Session) -> None:
    open_studio(s)
    seek_to_spoken_moment(s)
    if s.page.locator(".transcript-panel").count():
        toggle = s.page.locator("#show-english")
        if toggle.count() and not toggle.is_checked():
            toggle.check()
            s.page.wait_for_timeout(400)
    else:
        print("check-captions.png: staged (.transcript-panel not on the page; rerun after track B is merged)")
        s.page.evaluate(STAGED_CHECK_MARKUP, staged_check_lines(s))
    s.page.wait_for_timeout(200)
    shoot(s, "check-captions.png", selector=".stage-column")


def fig_burned_example(s: Session) -> None:
    open_studio(s)
    apply_look(s, EXAMPLE_LOOK)
    seek_to_spoken_moment(s)
    shoot(s, "burned-example.png", selector="#frame")
    apply_look(s, s.original_look)  # leave the job as we found it


def fig_style_editor(s: Session) -> None:
    s.page.goto(f"{s.base}/style-editor", wait_until="networkidle")
    s.page.wait_for_function("document.querySelectorAll('#style-list .style-item').length > 0", timeout=30000)
    s.page.evaluate("document.fonts.ready")
    s.page.wait_for_timeout(500)
    shoot(s, "style-editor.png")


@dataclass(frozen=True)
class Figure:
    name: str
    capture: Callable[[Session], None]


FIGURES: list[Figure] = [
    Figure("control-idle.png", fig_control_idle),
    Figure("queue-running.png", fig_queue_running),
    Figure("queue-done.png", fig_queue_done),
    Figure("lost-contact.png", fig_lost_contact),
    Figure("studio.png", fig_studio),
    Figure("moving-caption.png", fig_moving_caption),
    Figure("check-captions.png", fig_check_captions),
    Figure("burned-example.png", fig_burned_example),
    Figure("style-editor.png", fig_style_editor),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--job", default=DEFAULT_JOB, help="finished job to open in the Studio (default 5)")
    parser.add_argument("--only", help="capture one figure by file name")
    parser.add_argument("--no-docs-copy", action="store_true", help="do not copy the PNGs to docs/images")
    args = parser.parse_args(argv)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed in this venv:\n"
              "  .venv\\Scripts\\python.exe -m pip install playwright\n"
              "  .venv\\Scripts\\python.exe -m playwright install chromium", file=sys.stderr)
        return 2
    wanted = [f for f in FIGURES if not args.only or f.name == args.only]
    if not wanted:
        print(f"no figure named {args.only!r}; known: {', '.join(f.name for f in FIGURES)}", file=sys.stderr)
        return 2
    GUIDE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, color_scheme="dark", device_scale_factor=1, reduced_motion="reduce")
        page = context.new_page()
        health = page.request.get(f"{args.base}/api/jobs", headers=CLIENT_HEADERS)
        if not health.ok:
            print(f"guide_screenshots: {args.base} is not answering ({health.status}); start the app first", file=sys.stderr)
            return 1
        session = Session(base=args.base, job=args.job, page=page)
        for figure in wanted:
            figure.capture(session)
        browser.close()
    if not args.no_docs_copy:
        DOCS_IMAGES.mkdir(parents=True, exist_ok=True)
        for figure in wanted:
            shutil.copyfile(GUIDE_DIR / figure.name, DOCS_IMAGES / figure.name)
        print(f"copied {len(wanted)} file(s) to {DOCS_IMAGES.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
