"""The frame-led card (second critique, P1 #2): the frame takes the
footage's own shape, a burn shows its burned frame, the accent stays on
the recent group, and the housekeeping folds into one More menu so a
finished card is three Tab stops. The drawer comes first in source
order so a keyboard reaches Browse right after the nav."""
from __future__ import annotations

from pathlib import Path

from ash_captions.web import thumbs
from ash_captions.web.app import STATIC_DIR
from ash_captions.web.models import JobOptions, JobStatus

JPEG = b"\xff\xd8\xff\xe0" + b"J" * 200 + b"\xff\xd9"


def _static(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


class TestTheCardShape:
    def test_the_frame_is_a_box_that_takes_the_footage_shape(self):
        queue_js = _static("queue.js")
        assert 'el("div", "job-frame")' in queue_js
        assert "thumb.width = 160" not in queue_js
        css = _static("style.css")
        assert ".job-frame {" in css
        assert "max-height: 176px" in css  # a reel stands tall inside the box

    def test_housekeeping_folds_into_one_more_menu(self):
        queue_js = _static("queue.js")
        assert 'details.className = "card-more"' in queue_js
        for label in ("Open folder", "Copy path", "Remove"):
            assert f'button("{label}", "quiet"' in queue_js, label
        assert 'out.push(button("Open folder", "subtle"' not in queue_js

    def test_earlier_cards_keep_the_accent_off(self):
        assert 'quiet ? " subtle" : " primary"' in _static("queue.js")
        assert "AshQueue.renderInto(earlierList, shown, { quiet: true })" in _static("queue_stream.js")

    def test_a_finished_burn_asks_for_its_own_frame(self):
        assert "becameDone && job.options && job.options.burn_in" in _static("queue.js")

    def test_the_drawer_comes_before_the_stream_in_source_order(self):
        index = _static("index.html")
        assert index.index('class="drawer"') < index.index('class="stream"')


class _Completed:
    def __init__(self, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


class TestABurnsFrame:
    def test_a_burn_job_is_framed_from_its_burned_output_in_its_own_file(self, client, fake_queue, tmp_path, monkeypatch):
        video = tmp_path / "footage" / "reel.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"v" * 4096)
        out = tmp_path / "out" / "reel"
        out.mkdir(parents=True)
        source = fake_queue.submit(video, JobOptions(language="en", preset="POP"))
        burn = fake_queue.submit(video, JobOptions(language="en", preset="POP", burn_in=True))
        # A burn shares its source's output folder, as the real adapter does.
        for job in (source, burn):
            fake_queue.force_status(job.id, JobStatus.DONE, progress=1.0, output_dir=str(out))
        burned = out / "reel.captioned.mp4"
        burned.write_bytes(b"b" * 4096)
        sources: list[str] = []

        def fake_run(command, timeout):
            if "ffprobe" in Path(command[0]).name:
                return _Completed(0, "40.0\n")
            sources.append(command[command.index("-i") + 1])
            Path(command[-1]).write_bytes(JPEG)
            return _Completed(0, "")

        monkeypatch.setattr(thumbs, "find_binary", lambda name: Path(f"C:/bin/{name}.exe"))
        monkeypatch.setattr(thumbs, "_run", fake_run)

        assert client.get(f"/api/jobs/{burn.id}/thumb").status_code == 200
        assert sources == [str(burned)]
        assert (out / thumbs.BURNED_THUMB_NAME).is_file()
        assert not (out / thumbs.THUMB_NAME).is_file()
        # The source job keeps its own frame, from the footage.
        assert client.get(f"/api/jobs/{source.id}/thumb").status_code == 200
        assert sources == [str(burned), str(video)]
        assert (out / thumbs.THUMB_NAME).is_file()

    def test_a_burn_still_running_shows_the_footage(self, client, fake_queue, tmp_path, monkeypatch):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"v" * 4096)
        out = tmp_path / "out" / "clip"
        out.mkdir(parents=True)
        burn = fake_queue.submit(video, JobOptions(language="en", preset="POP", burn_in=True))
        fake_queue.force_status(burn.id, JobStatus.RUNNING, output_dir=str(out))
        sources: list[str] = []

        def fake_run(command, timeout):
            if "ffprobe" in Path(command[0]).name:
                return _Completed(0, "10.0\n")
            sources.append(command[command.index("-i") + 1])
            Path(command[-1]).write_bytes(JPEG)
            return _Completed(0, "")

        monkeypatch.setattr(thumbs, "find_binary", lambda name: Path(f"C:/bin/{name}.exe"))
        monkeypatch.setattr(thumbs, "_run", fake_run)
        assert client.get(f"/api/jobs/{burn.id}/thumb").status_code == 200
        assert sources == [str(video)]
