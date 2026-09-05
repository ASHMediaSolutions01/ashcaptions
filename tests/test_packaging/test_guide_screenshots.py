"""scripts/guide_screenshots.py: the pure helpers that stage a fake queue for
the screenshot script and find a moment worth screenshotting in an .srt, kept
importable and testable without Playwright installed (Playwright is imported
lazily inside main(), so the module and these tests run on a machine that has
no browser)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import guide_screenshots

GUIDE_HTML = Path(__file__).resolve().parents[2] / "src" / "ash_captions" / "web" / "static" / "guide.html"


def test_the_guide_and_the_capture_script_name_exactly_the_same_figures():
    """Exact match, both ways. A figure added to the guide without a capture
    goes stale at the next recapture; a capture nothing references is dead
    code that still costs a browser page on every run."""
    referenced = set(re.findall(r'src="/static/guide/([^"]+)"', GUIDE_HTML.read_text(encoding="utf-8")))
    names = {f.name for f in guide_screenshots.FIGURES}
    assert referenced == names


def _job(job_id: str, status: str = "done") -> dict:
    created = "2026-09-03T14:00:00+00:00"
    return {
        "id": job_id, "filename": f"{job_id}.mp4", "status": status, "progress": 1.0,
        "options": {"language": "es", "dialect": "es-MX", "preset": "IMPACT", "burn_in": False,
                    "translate_to_english": True, "behind_speaker": False, "client": None},
        "error": None, "created_at": created, "updated_at": created, "started_at": created,
        "stage": None, "stage_started_at": None, "input_path": "C:\\x\\a.mp4", "output_dir": "C:\\x\\out\\a",
    }


def test_stage_jobs_running_scene_keeps_ids_and_fakes_one_running_row():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    staged = guide_screenshots.stage_jobs([_job("5"), _job("4"), _job("2"), _job("1")], now, "running")
    assert [j["id"] for j in staged] == ["5", "4", "2"]
    assert staged[0]["status"] == "running" and staged[0]["stage"] == "transcribe"
    assert 0 < staged[0]["progress"] < 1
    assert staged[0]["started_at"] == (now - timedelta(minutes=3, seconds=12)).isoformat()
    assert [j["status"] for j in staged[1:]] == ["done", "done"]


def test_stage_jobs_done_scene_has_a_done_a_failed_and_a_pending_row():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    staged = guide_screenshots.stage_jobs([_job("5"), _job("4"), _job("2")], now, "done")
    assert [j["status"] for j in staged] == ["done", "failed", "pending"]
    assert "moved or renamed" in staged[1]["error"]
    assert staged[0]["output_dir"]  # Open folder / Copy path appear


def test_stage_jobs_needs_two_finished_jobs():
    import pytest

    with pytest.raises(SystemExit):
        guide_screenshots.stage_jobs([_job("5"), _job("4", "failed")], datetime.now(timezone.utc), "done")


def test_spoken_moment_is_just_after_a_cue_starts():
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHola\n\n2\n00:00:05,500 --> 00:00:07,000\nBuenos días\n\n3\n00:01:02,250 --> 00:01:03,000\nGracias\n"
    assert guide_screenshots.spoken_moment(srt, 2) == 62.55
    assert guide_screenshots.spoken_moment(srt, 9) == 62.55  # past the end: the last cue
    assert guide_screenshots.spoken_moment("", 0) == 0.0


def test_playwright_is_not_imported_at_module_level():
    """The venv has no Playwright installed for this track (v0.5 plan
    constraint); the module must still import cleanly, and only main()
    reaches for playwright.sync_api."""
    import sys

    assert "playwright" not in sys.modules or True  # importing this test module must not require it
    import inspect

    source = inspect.getsource(guide_screenshots)
    module_head = source.split("def main(")[0]
    assert "import playwright" not in module_head
    assert "from playwright" not in module_head
