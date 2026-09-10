"""Reframing as a job: the option, the caption size, and the filter chain.

The engine's own decisions are tested in ``test_engine/test_reframe.py``.
What matters here is the wiring, and specifically the two things that
would produce a plausible-looking wrong deliverable rather than an error:

* captions written at the *source's* size and then burned into a reel
  (they come out the wrong size, and nothing complains);
* the crop chained after the punch or after the captions (the captions
  get cropped, or magnified, instead of drawn on the reel).
"""

import re
from pathlib import Path

import pytest

from ash_captions.app import runner_video
from ash_captions.engine.burn import build_filtergraph
from ash_captions.engine.matte import composite_filtergraph
from ash_captions.pipeline.db import JobOptions


class _Info:
    def __init__(self, width, height, duration=10.0, fps=30.0):
        self.width, self.height = width, height
        self.duration_seconds, self.fps = duration, fps


def options(**kwargs):
    base = dict(language="es", dialect=None, preset="POP", burn=True, translate=False)
    base.update(kwargs)
    return JobOptions(**base)


class TestTheOption:
    def test_it_is_off_unless_asked_for(self):
        assert options().reframe is False

    def test_it_survives_a_round_trip_through_the_database(self):
        restored = JobOptions.from_json(options(reframe=True).to_json())
        assert restored.reframe is True

    def test_a_row_written_before_the_option_existed_still_loads(self):
        raw = '{"language": "es", "preset": "POP", "burn": true, "translate": false}'
        assert JobOptions.from_json(raw).reframe is False


class TestOnlyOnABurn:
    """The crop lives in the burned video, so a job that burns nothing
    must not have its captions written for a reel that never exists."""

    def test_asking_for_a_reel_without_a_burn_does_nothing(self):
        assert not runner_video.wants_reframe(options(reframe=True, burn=False))

    def test_asking_for_both_reframes(self):
        assert runner_video.wants_reframe(options(reframe=True, burn=True))

    def test_a_plain_burn_does_not_reframe(self):
        assert not runner_video.wants_reframe(options(burn=True))


class TestCaptionSize:
    def test_a_reel_job_writes_its_captions_at_the_reels_size(self):
        assert runner_video.caption_play_res(
            options(reframe=True), _Info(1920, 1080)
        ) == (1080, 1920)

    def test_an_ordinary_burn_still_uses_the_sources_size(self):
        assert runner_video.caption_play_res(
            options(), _Info(1920, 1080)
        ) == (1920, 1080)

    def test_a_failed_probe_leaves_the_caller_with_no_playres(self):
        assert runner_video.caption_play_res(options(reframe=True), None) is None

    def test_a_source_already_vertical_is_unchanged(self):
        assert runner_video.caption_play_res(
            options(reframe=True), _Info(1080, 1920)
        ) == (1080, 1920)


class TestNoPointReframing:
    def test_a_vertical_source_is_left_alone_rather_than_rescanned(self, tmp_path):
        """A no-op crop still costs a decode pass and a few hundred
        inferences. `build_reframe` returns None before any of that, and
        the assert proves it never reached the scan (which would have
        raised on a file that does not exist)."""
        assert runner_video.build_reframe(
            options(reframe=True),
            tmp_path / "nope.mp4",
            _Info(1080, 1920),
            duration_seconds=10.0,
            models_dir=tmp_path,
            ffmpeg_path="ffmpeg",
        ) is None

    def test_a_job_that_did_not_ask_never_scans(self, tmp_path):
        assert runner_video.build_reframe(
            options(),
            tmp_path / "nope.mp4",
            _Info(1920, 1080),
            duration_seconds=10.0,
            models_dir=tmp_path,
            ffmpeg_path="ffmpeg",
        ) is None

    def test_a_failed_probe_fails_the_job_rather_than_burning_the_original(self, tmp_path):
        from ash_captions import engine

        with pytest.raises(engine.ReframeScanError):
            runner_video.build_reframe(
                options(reframe=True),
                tmp_path / "nope.mp4",
                None,
                duration_seconds=10.0,
                models_dir=tmp_path,
                ffmpeg_path="ffmpeg",
            )


class TestFilterOrder:
    CROP = "crop=w=606:h=1080:x='0':y=0,scale=1080:1920:flags=lanczos"
    PUNCH = "scale=w='iw*1.1':h='ih*ow/iw':eval=frame,crop=w=iw:h=ih:x='0':y='0'"

    def test_the_crop_comes_before_the_captions(self):
        graph = build_filtergraph(reframe_filter=self.CROP)
        assert graph.index("crop=w=606") < graph.index("ass=captions.ass")

    def test_the_crop_comes_before_the_punch(self):
        """Reframe reshapes the frame; the punch works inside that shape.
        The other order zooms the landscape frame and then crops it, which
        is a different picture."""
        graph = build_filtergraph(reframe_filter=self.CROP, punch_filter=self.PUNCH)
        assert graph.index("crop=w=606") < graph.index("eval=frame")
        assert graph.index("eval=frame") < graph.index("ass=captions.ass")

    def test_a_burn_without_reframing_is_unchanged(self):
        assert build_filtergraph(punch_filter=self.PUNCH) == f"{self.PUNCH},ass=captions.ass"
        assert build_filtergraph() == "ass=captions.ass"

    def test_the_matte_is_cropped_the_same_way_as_the_picture(self):
        """Behind-the-speaker plus a reel: a matte left at the source's
        shape would mask the wrong part of a reframed picture."""
        graph = composite_filtergraph(
            caption_filter="ass=captions.ass", width=1920, height=1080, fps=30.0,
            reframe_filter=self.CROP,
        )
        assert graph.count(self.CROP) == 2

    def test_the_matte_is_reframed_only_after_it_is_back_at_source_size(self):
        graph = composite_filtergraph(
            caption_filter="ass=captions.ass", width=1920, height=1080, fps=30.0,
            reframe_filter=self.CROP,
        )
        matte_branch = graph.split("[1:v]")[1]
        assert matte_branch.index("scale=1920:1080") < matte_branch.index("crop=w=606")

    def test_behind_the_speaker_alone_is_unchanged(self):
        plain = composite_filtergraph(
            caption_filter="ass=captions.ass", width=1920, height=1080, fps=30.0
        )
        assert "crop=w=606" not in plain
        assert "[cap][fg]overlay=0:0:format=auto,format=yuv420p[out]" in plain


class TestStagesAreRegistered:
    """Every stage the runner sets must be one the database accepts.

    ``JobStore.set_stage`` validates against ``db.STAGES`` and raises on
    anything else, so a new stage has to be added in two places. Missing
    the second one fails the job at run time with "unknown stage", which
    no unit test noticed and a real burn in a built bundle did. This walks
    the runner's own source so the next stage cannot repeat it.
    """

    def _stages_the_runner_sets(self):
        source = Path(runner_video.__file__).with_name("runner.py").read_text(encoding="utf-8")
        return set(re.findall(r'_stage\(report,\s*"([a-z_]+)"\)', source)) | set(
            re.findall(r'set_stage\("([a-z_]+)"\)', source)
        )

    def test_the_runner_sets_at_least_the_stages_we_know_about(self):
        found = self._stages_the_runner_sets()
        assert {"reframe", "matte", "burn"} <= found, found

    def test_every_stage_the_runner_sets_is_one_the_database_accepts(self):
        from ash_captions.pipeline.db import STAGES

        unknown = self._stages_the_runner_sets() - set(STAGES)
        assert not unknown, f"the runner sets stages the database rejects: {sorted(unknown)}"

    def test_every_stage_has_a_label_on_the_queue_page(self):
        """An unlabelled stage falls back to showing the raw key."""
        from ash_captions.pipeline.db import STAGES

        queue_js = (
            Path(runner_video.__file__).parents[1] / "web" / "static" / "queue.js"
        ).read_text(encoding="utf-8")
        labelled = set(re.findall(r"^\s*([a-z_]+):\s*\"", queue_js, re.M))
        missing = set(STAGES) - labelled
        assert not missing, f"no label on the queue page for: {sorted(missing)}"
