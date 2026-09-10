"""Landscape -> 9:16 crop planning.

The numbers these tests assert against are measurements from the
studio's own 1920x1080 interview, not invented examples -- see the module
docstring in ``engine/reframe.py``. Where a test encodes a measured
value it says so, because that is the difference between a test that
guards a decision and one that just repeats the code.
"""

from fractions import Fraction

import pytest

from ash_captions.engine.reframe import (
    Blob,
    CropPlan,
    CropWindow,
    ReframeError,
    Sample,
    Shot,
    blobs_from_columns,
    build_crop_filter,
    choose_subject,
    clamp_centre,
    crop_size,
    plan_crops,
    shots_from_cuts,
    split_on_subject_change,
    subject_centre,
)

# The measured two-shot: interviewer at 373, guest at 1540, in a 1920
# frame. They are 1167 px apart and the crop is 606 px wide.
LEFT_X, RIGHT_X = 373.0, 1540.0
SRC_W, SRC_H = 1920, 1080


def columns(*people, width=854, spread=60):
    """A frame's column sums with a bump per person, in inference columns."""
    cols = [0.0] * width
    for centre_src, mass in people:
        centre = int(centre_src * width / SRC_W)
        for i in range(max(0, centre - spread), min(width, centre + spread)):
            cols[i] += mass
    return cols


class TestCropSize:
    def test_a_1080p_landscape_crops_to_606_wide(self):
        # 1080 * 9/16 = 607.5, and an odd crop is illegal in yuv420p.
        assert crop_size(1920, 1080) == (606, 1080)

    def test_a_portrait_source_is_limited_by_its_width(self):
        assert crop_size(1080, 1920) == (1080, 1920)

    def test_both_sides_come_back_even(self):
        w, h = crop_size(1921, 1081)
        assert w % 2 == 0 and h % 2 == 0

    def test_a_sizeless_source_is_refused_rather_than_divided_by_zero(self):
        with pytest.raises(ReframeError):
            crop_size(0, 1080)


class TestBlobs:
    def test_two_people_come_back_as_two_blobs(self):
        blobs = blobs_from_columns(columns((LEFT_X, 5.0), (RIGHT_X, 5.0)), source_width=SRC_W)
        assert len(blobs) == 2
        assert blobs[0].centre == pytest.approx(LEFT_X, abs=20)
        assert blobs[1].centre == pytest.approx(RIGHT_X, abs=20)

    def test_the_centroid_of_both_would_land_between_them_on_nobody(self):
        """Why blobs exist at all: the measured failure this prevents."""
        cols = columns((LEFT_X, 5.0), (RIGHT_X, 5.0))
        whole = sum(v * i for i, v in enumerate(cols)) / sum(cols) * SRC_W / len(cols)
        assert 900 < whole < 1100  # the empty sofa between the two
        blobs = blobs_from_columns(cols, source_width=SRC_W)
        assert all(abs(b.centre - whole) > 400 for b in blobs)

    def test_an_empty_frame_yields_no_blobs(self):
        assert blobs_from_columns([0.0] * 854, source_width=SRC_W) == ()

    def test_no_columns_at_all_is_not_an_error(self):
        assert blobs_from_columns([], source_width=SRC_W) == ()

    def test_a_narrow_speck_is_not_a_person(self):
        cols = [0.0] * 854
        cols[400] = 9.0  # one column: noise, not a body
        assert blobs_from_columns(cols, source_width=SRC_W) == ()

    def test_results_are_in_source_pixels_whatever_the_inference_width(self):
        wide = blobs_from_columns(columns((RIGHT_X, 5.0), width=854), source_width=SRC_W)
        narrow = blobs_from_columns(
            columns((RIGHT_X, 5.0), width=427, spread=30), source_width=SRC_W
        )
        assert wide[0].centre == pytest.approx(narrow[0].centre, abs=25)


class TestChoice:
    def test_the_largest_person_wins_by_default(self):
        blobs = (Blob(LEFT_X, 100, 50.0), Blob(RIGHT_X, 200, 500.0))
        assert choose_subject(blobs) == 1

    def test_an_editor_override_beats_the_default(self):
        blobs = (Blob(LEFT_X, 100, 50.0), Blob(RIGHT_X, 200, 500.0))
        assert choose_subject(blobs, prefer=0) == 0

    def test_an_override_pointing_at_nobody_is_ignored_rather_than_crashing(self):
        blobs = (Blob(LEFT_X, 100, 50.0),)
        assert choose_subject(blobs, prefer=7) == 0

    def test_an_empty_frame_chooses_nobody(self):
        assert choose_subject(()) is None

    def test_the_centre_is_the_mass_centroid(self):
        # Measured: the centroid drifts 13-31 px over a shot where the
        # top-third "head" estimate drifts 56-131 px. The obvious guess
        # was the worse one.
        assert subject_centre(Blob(centre=812.5, width=300, mass=9.0)) == 812.5


class TestClamp:
    def test_a_subject_at_the_edge_slides_the_crop_back_inside(self):
        assert clamp_centre(20.0, crop_width=606, source_width=SRC_W) == 303.0
        assert clamp_centre(1900.0, crop_width=606, source_width=SRC_W) == 1617.0

    def test_a_centred_subject_is_left_alone(self):
        assert clamp_centre(960.0, crop_width=606, source_width=SRC_W) == 960.0

    def test_a_crop_no_narrower_than_the_source_sits_in_the_middle(self):
        assert clamp_centre(10.0, crop_width=1920, source_width=SRC_W) == 960.0


class TestShots:
    def test_cuts_become_the_gaps_between_them(self):
        shots = shots_from_cuts([10.0, 25.0], 40.0)
        assert [(s.start, s.end) for s in shots] == [(0.0, 10.0), (10.0, 25.0), (25.0, 40.0)]

    def test_a_flash_shorter_than_half_a_second_is_dropped(self):
        shots = shots_from_cuts([10.0, 10.2], 40.0)
        assert all(s.duration >= 0.5 for s in shots)

    def test_footage_with_no_cuts_is_one_shot(self):
        assert shots_from_cuts([], 40.0) == (Shot(0.0, 40.0),)

    def test_cuts_outside_the_file_are_ignored(self):
        shots = shots_from_cuts([-5.0, 10.0, 900.0], 40.0)
        assert [(s.start, s.end) for s in shots] == [(0.0, 10.0), (10.0, 40.0)]


class TestDissolve:
    """The measured case cut detection cannot see.

    A white title card cross-dissolves into a two-shot at ~5.5s. ffmpeg's
    scene score never spikes at any threshold down to 0.10, because each
    frame barely differs from the last, so the shot list says one shot
    from 0 to 10.4s. The samples inside it disagree, and that is what
    has to split it.
    """

    def _samples(self):
        two = (Blob(LEFT_X, 300, 400.0), Blob(RIGHT_X, 300, 500.0))
        return [
            Sample(time=1.6, blobs=()),
            Sample(time=3.4, blobs=()),
            Sample(time=5.2, blobs=()),
            Sample(time=7.0, blobs=two),
            Sample(time=8.8, blobs=two),
        ]

    def test_a_shot_that_changes_subject_mid_way_is_split(self):
        pieces = split_on_subject_change(
            Shot(0.0, 10.4), self._samples(), source_width=SRC_W
        )
        assert len(pieces) == 2
        (first, _), (second, _) = pieces
        assert first.start == 0.0 and second.end == 10.4
        # The change is placed between the samples that disagree.
        assert 5.2 <= first.end <= 7.0

    def test_the_empty_half_keeps_only_its_own_samples(self):
        pieces = split_on_subject_change(
            Shot(0.0, 10.4), self._samples(), source_width=SRC_W
        )
        assert all(s.empty for s in pieces[0][1])
        assert all(not s.empty for s in pieces[1][1])

    def test_a_steady_shot_is_not_split(self):
        one = (Blob(1090.0, 900, 500.0),)
        samples = [Sample(time=t, blobs=one) for t in (1.0, 3.0, 5.0, 7.0)]
        pieces = split_on_subject_change(Shot(0.0, 8.0), samples, source_width=SRC_W)
        assert len(pieces) == 1

    def test_measured_within_shot_drift_does_not_count_as_a_change(self):
        """31 px was the worst drift measured inside a shot, in 1920."""
        samples = [
            Sample(time=1.0, blobs=(Blob(1090.0, 900, 500.0),)),
            Sample(time=5.0, blobs=(Blob(1121.0, 900, 500.0),)),
        ]
        pieces = split_on_subject_change(Shot(0.0, 8.0), samples, source_width=SRC_W)
        assert len(pieces) == 1

    def test_the_dissolves_450px_jump_does_count(self):
        samples = [
            Sample(time=1.0, blobs=(Blob(1090.0, 900, 500.0),)),
            Sample(time=5.0, blobs=(Blob(1540.0, 900, 500.0),)),
        ]
        pieces = split_on_subject_change(Shot(0.0, 8.0), samples, source_width=SRC_W)
        assert len(pieces) == 2

    def test_a_single_sample_cannot_disagree_with_itself(self):
        pieces = split_on_subject_change(
            Shot(0.0, 8.0), [Sample(time=4.0, blobs=())], source_width=SRC_W
        )
        assert len(pieces) == 1


class TestPlan:
    def _plan(self, **kwargs):
        one = (Blob(1090.0, 900, 500.0),)
        two = (Blob(LEFT_X, 300, 400.0), Blob(RIGHT_X, 300, 500.0))
        shots = [Shot(0.0, 10.0), Shot(10.0, 20.0), Shot(20.0, 30.0)]
        samples = [
            [Sample(time=t, blobs=one) for t in (2.0, 5.0, 8.0)],
            [Sample(time=t, blobs=two) for t in (12.0, 15.0, 18.0)],
            [Sample(time=t, blobs=()) for t in (22.0, 25.0, 28.0)],
        ]
        return plan_crops(shots, samples, source_width=SRC_W, source_height=SRC_H, **kwargs)

    def test_one_window_per_shot_when_nothing_is_subdivided(self):
        assert len(self._plan().windows) == 3

    def test_a_single_subject_is_centred_on(self):
        assert self._plan().windows[0].centre == pytest.approx(1090.0, abs=1)

    def test_a_two_shot_takes_the_larger_person(self):
        assert self._plan().windows[1].centre == pytest.approx(RIGHT_X, abs=1)

    def test_a_frame_with_nobody_falls_back_to_the_middle(self):
        """Measured: on the title card the matte's own centroid said 1176.

        Following that would frame 216 px off centre on a shot that has
        nothing to frame.
        """
        empty = self._plan().windows[2]
        assert empty.chosen is None
        assert empty.centre == 960.0

    def test_a_contested_window_is_reported_as_such(self):
        plan = self._plan()
        assert not plan.windows[0].contested
        assert plan.windows[1].contested
        assert len(plan.contested_windows) == 1

    def test_an_override_reframes_without_re_running_anything(self):
        plan = self._plan(overrides={1: 0})
        assert plan.windows[1].chosen == 0
        assert plan.windows[1].centre == pytest.approx(LEFT_X, abs=1)
        # and the shots nobody corrected are untouched
        assert plan.windows[0].centre == self._plan().windows[0].centre

    def test_the_plan_records_how_much_real_detail_survives(self):
        assert self._plan().detail_width == 606


class TestFilter:
    def _plan(self, windows):
        return CropPlan(
            source_width=SRC_W, source_height=SRC_H,
            crop_width=606, crop_height=1080, windows=tuple(windows),
        )

    def test_the_crop_is_the_planned_size(self):
        graph = build_crop_filter(self._plan([CropWindow(0.0, 10.0, 960.0)]))
        assert "crop=w=606:h=1080" in graph

    def test_the_offset_is_the_centre_less_half_the_crop(self):
        graph = build_crop_filter(self._plan([CropWindow(0.0, 10.0, 1540.0)]))
        assert "1237.00" in graph  # 1540 - 303

    def test_the_x_is_clamped_inside_the_frame_by_the_expression_itself(self):
        graph = build_crop_filter(self._plan([CropWindow(0.0, 10.0, 1900.0)]))
        assert "max(0,min(1314," in graph  # 1920 - 606

    def test_every_window_appears_as_its_own_time_range(self):
        graph = build_crop_filter(self._plan([
            CropWindow(0.0, 5.0, 500.0), CropWindow(5.0, 9.0, 1500.0),
        ]))
        assert "between(t,0.000,5.000)" in graph
        assert "between(t,5.000,9.000)" in graph

    def test_an_empty_plan_is_refused_rather_than_cropping_to_the_corner(self):
        with pytest.raises(ReframeError):
            build_crop_filter(self._plan([]))

    def test_the_output_size_is_appended_when_asked_for(self):
        graph = build_crop_filter(
            self._plan([CropWindow(0.0, 10.0, 960.0)]), output=(1080, 1920)
        )
        assert graph.endswith("scale=1080:1920:flags=lanczos")

    def test_a_nonsense_output_size_is_refused(self):
        with pytest.raises(ReframeError):
            build_crop_filter(self._plan([CropWindow(0.0, 1.0, 960.0)]), output=(0, 1920))

    def test_many_windows_do_not_nest_deeper_than_ffmpeg_will_parse(self):
        """ffmpeg's expression parser refuses to recurse past 100 levels.

        punch.py hit this at about 80 flat '+' terms; the same balanced
        sum is used here, so a long documentary does not fail to render.
        """
        windows = [CropWindow(float(i), float(i + 1), 960.0) for i in range(400)]
        graph = build_crop_filter(self._plan(windows))
        depth = worst = 0
        for char in graph:
            if char == "(":
                depth += 1
                worst = max(worst, depth)
            elif char == ")":
                depth -= 1
        assert worst < 100


class TestRatio:
    def test_a_different_ratio_is_honoured(self):
        assert crop_size(1920, 1080, Fraction(1, 1)) == (1080, 1080)
        assert crop_size(1920, 1080, Fraction(4, 5)) == (864, 1080)
