"""Write docs/ASH-Captions-Guide.html: the in-app editor's guide as one
file that opens from anywhere.

    .venv\\Scripts\\python.exe scripts\\export_guide.py

The guide is served inside the app at /guide with its stylesheets, its
script and its screenshots as separate requests. The team gets this copy
before they install (spec v0.5 section 5), so everything is inlined:
theme.css and guide.css into a <style>, guide.js into a <script> (the
setup checklist and the Copy buttons keep working from a file), every
screenshot as a data: URI, the favicon likewise. The app nav and nav.js
are dropped -- their links only work inside the app -- and a note at the
top says where the live guide lives.

Run it after scripts/guide_screenshots.py, and commit the result.
"""
from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATIC_DIR = REPO / "src" / "ash_captions" / "web" / "static"
DEFAULT_OUT = REPO / "docs" / "ASH-Captions-Guide.html"

NOTE = (
    '<p class="note">This is a copy of the guide for reading before the app is installed. '
    "Once ASH Captions is running, the same guide lives inside it at "
    "<code>http://127.0.0.1:8756/guide</code> (the <strong>Help</strong> link), "
    "and that copy always matches the version you are running.</p>"
)


def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def build(static_dir: Path) -> str:
    html = (static_dir / "guide.html").read_text(encoding="utf-8")
    html = html.replace("?v=__VERSION__", "")

    # Stylesheets -> one <style>.
    css = "\n".join((static_dir / name).read_text(encoding="utf-8") for name in ("theme.css", "guide.css"))
    html = re.sub(r'<link rel="stylesheet" href="/static/theme\.css">\s*', "", html)
    html = re.sub(r'<link rel="stylesheet" href="/static/guide\.css">', "<style>\n" + css + "\n</style>", html, count=1)

    # Favicon -> data URI.
    html = html.replace('href="/static/favicon.svg"', 'href="' + _data_uri(static_dir / "favicon.svg", "image/svg+xml") + '"')

    # The app nav and its script do not work from a file.
    html = re.sub(r'<nav class="app-nav".*?</nav>\s*', "", html, count=1, flags=re.S)
    html = re.sub(r'<script src="/static/nav\.js"></script>\s*', "", html)

    # guide.js -> inline.
    script = (static_dir / "guide.js").read_text(encoding="utf-8")
    html = html.replace('<script src="/static/guide.js"></script>', "<script>\n" + script + "\n</script>")

    # Screenshots -> data URIs.
    def inline_img(match: re.Match) -> str:
        return 'src="' + _data_uri(static_dir / "guide" / match.group(1), "image/png") + '"'

    html = re.sub(r'src="/static/guide/([^"]+)"', inline_img, html)

    # Say what this file is, right under the title.
    html = html.replace("</header>", NOTE + "\n</header>", 1)

    leftover = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
    if leftover:
        raise RuntimeError(f"export_guide: still referencing {leftover}")
    return html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--static", type=Path, default=STATIC_DIR)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    html = build(args.static)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
