"""Saving a crop plan, and an editor correcting it.

The correction path is worth testing carefully because it is the one
that runs *instead of* measuring. A plan re-aimed from stale or foreign
data would frame a reel confidently and wrongly, with no scan to catch
it, so every way the stored file can be bad has to come back as "measure
it again" rather than as a plausible-looking plan.
"""

import json

import pytest

from ash_captions.engine.reframe import CropPlan, CropWindow, ReframeError
from ash_captions.engine.reframe_store import (
    PLAN_VERSION,
    apply_overrides,
    load_plan,
    matches_source,
    plan_from_dict,
    plan_path_for,
    plan_to_dict,
    save_plan,
)

# The measured two-shot: interviewer at 373, guest at 1540, guest chosen
# because the matte gives it more mass.
LEFT, RIGHT = 373.0, 1540.0


def plan(*windows):
    return CropPlan(
        source_width=1920, source_height=1080, crop_width=606, crop_height=1080,
        windows=windows or (CropWindow(0.0, 10.0, RIGHT, 1, (LEFT, RIGHT)),),
    )


class TestApplyOverrides:
    def test_choosing_the_other_person_moves_the_crop_to_them(self):
        out = apply_overrides(plan(), {0: 0})
        assert out.windows[0].centre == LEFT
        assert out.windows[0].chosen == 0

    def test_the_candidates_are_kept_so_the_choice_can_be_changed_again(self):
        out = apply_overrides(plan(), {0: 0})
        assert out.windows[0].candidates == (LEFT, RIGHT)

    def test_no_overrides_returns_the_plan_untouched(self):
        original = plan()
        assert apply_overrides(original, {}) is original
        assert apply_overrides(original, None) is original

    def test_windows_nobody_corrected_are_left_alone(self):
        out = apply_overrides(
            plan(
                CropWindow(0.0, 5.0, RIGHT, 1, (LEFT, RIGHT)),
                CropWindow(5.0, 9.0, RIGHT, 1, (LEFT, RIGHT)),
            ),
            {1: 0},
        )
        assert out.windows[0].centre == RIGHT
        assert out.windows[1].centre == LEFT

    def test_a_correction_near_the_edge_is_pulled_back_inside_the_frame(self):
        """A person 20px from the edge cannot be centred in a 606px crop."""
        out = apply_overrides(plan(CropWindow(0.0, 5.0, 960.0, 1, (20.0, 960.0))), {0: 0})
        assert out.windows[0].centre == 303.0

    def test_a_correction_naming_a_window_that_is_gone_is_ignored(self):
        """The page and the file can disagree after a re-scan. A stale
        correction must fall back to the default framing, not fail."""
        out = apply_overrides(plan(), {99: 0})
        assert out.windows[0].centre == RIGHT

    def test_a_correction_naming_a_person_who_is_not_there_is_ignored(self):
        out = apply_overrides(plan(), {0: 7})
        assert out.windows[0].centre == RIGHT

    def test_a_negative_choice_does_not_index_from_the_end(self):
        """Python would read -1 as "the last one", which is a silent wrong
        answer rather than an ignored one."""
        out = apply_overrides(plan(), {0: -1})
        assert out.windows[0].centre == RIGHT


class TestRoundTrip:
    def test_a_plan_survives_being_written_and_read(self):
        original = plan(
            CropWindow(0.0, 5.0, RIGHT, 1, (LEFT, RIGHT)),
            CropWindow(5.0, 9.0, 960.0, None, ()),
        )
        back = plan_from_dict(plan_to_dict(original))
        assert back == original

    def test_a_window_that_framed_nobody_stays_that_way(self):
        back = plan_from_dict(plan_to_dict(plan(CropWindow(0.0, 5.0, 960.0, None, ()))))
        assert back.windows[0].chosen is None
        assert back.windows[0].candidates == ()

    def test_saving_writes_beside_the_deliverable(self, tmp_path):
        out = tmp_path / "clip.captioned.mp4"
        path = save_plan(plan(), out)
        assert path == tmp_path / "clip.captioned.reframe.json"
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == PLAN_VERSION

    def test_loading_returns_what_was_saved(self, tmp_path):
        out = tmp_path / "clip.captioned.mp4"
        save_plan(plan(), out)
        assert load_plan(out) == plan()

    def test_the_path_is_derived_from_the_deliverables_name(self, tmp_path):
        assert plan_path_for(tmp_path / "a.captioned.mp4").name == "a.captioned.reframe.json"


class TestBadStoredPlans:
    """Every one of these must read as "scan again", never as a plan."""

    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert load_plan(tmp_path / "never-burned.captioned.mp4") is None

    def test_a_file_that_is_not_json_is_not_an_error(self, tmp_path):
        out = tmp_path / "clip.captioned.mp4"
        plan_path_for(out).write_text("not json at all", encoding="utf-8")
        assert load_plan(out) is None

    def test_a_plan_from_a_future_version_is_refused(self, tmp_path):
        out = tmp_path / "clip.captioned.mp4"
        data = plan_to_dict(plan())
        data["version"] = PLAN_VERSION + 1
        plan_path_for(out).write_text(json.dumps(data), encoding="utf-8")
        assert load_plan(out) is None

    def test_a_hand_edited_plan_missing_a_field_is_refused(self, tmp_path):
        out = tmp_path / "clip.captioned.mp4"
        data = plan_to_dict(plan())
        del data["windows"][0]["centre"]
        plan_path_for(out).write_text(json.dumps(data), encoding="utf-8")
        assert load_plan(out) is None

    def test_a_plan_with_no_windows_is_refused(self, tmp_path):
        out = tmp_path / "clip.captioned.mp4"
        data = plan_to_dict(plan())
        data["windows"] = []
        plan_path_for(out).write_text(json.dumps(data), encoding="utf-8")
        assert load_plan(out) is None

    def test_from_dict_says_why_rather_than_returning_something_wrong(self):
        with pytest.raises(ReframeError):
            plan_from_dict({"version": 99})


class TestMatchesSource:
    def test_a_plan_for_this_footage_is_reusable(self):
        assert matches_source(plan(), 1920, 1080)

    def test_a_plan_made_for_a_different_frame_size_is_not(self):
        """Plans hold source pixels. Replaced or re-encoded footage would
        otherwise be cropped by numbers measured against another picture."""
        assert not matches_source(plan(), 3840, 2160)
        assert not matches_source(plan(), 1080, 1920)
