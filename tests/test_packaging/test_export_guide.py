"""scripts/export_guide.py: docs/ASH-Captions-Guide.html is the in-app guide
with its stylesheets, script and screenshots inlined so it opens from a
file (or an e-mail attachment) with no app and no network."""
from __future__ import annotations

import re
from pathlib import Path

import export_guide

STATIC_DIR = Path(__file__).resolve().parents[2] / "src" / "ash_captions" / "web" / "static"


def test_export_is_self_contained():
    html = export_guide.build(STATIC_DIR)
    assert "__VERSION__" not in html
    assert "/static/" not in html
    assert "<link rel=\"stylesheet\"" not in html
    assert "<script src=" not in html
    assert 'class="app-nav"' not in html  # links into the app do not work from a file
    for src in re.findall(r'<img[^>]+src="([^"]+)"', html):
        assert src.startswith("data:image/png;base64,"), src[:40]
    assert re.search(r'<link rel="icon"[^>]+href="data:image/svg\+xml', html)


def test_export_keeps_the_guide_content_and_its_script():
    html = export_guide.build(STATIC_DIR)
    for section_id in ("what", "setup", "starting", "caption", "moving", "check", "problems", "uninstall", "worth"):
        assert f'<section id="{section_id}">' in html, section_id
    assert ".guide-main" in html and ":root {" in html  # guide.css and theme.css inlined
    assert 'var KEY = "ashguide.done.v1";' in html  # guide.js inlined: checklist and Copy buttons work
    assert "This is a copy of the guide" in html


def test_main_writes_the_docs_file(tmp_path):
    out = tmp_path / "guide.html"
    assert export_guide.main(["--out", str(out)]) == 0
    assert out.stat().st_size > 100_000  # the screenshots are inside
    assert "data:image/png;base64," in out.read_text(encoding="utf-8")
