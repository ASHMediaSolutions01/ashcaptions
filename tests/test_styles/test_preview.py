"""Tests for ash_captions.styles.preview.

Pure argument construction -- no ffmpeg process is ever started here, so
these tests need neither ffmpeg nor network access (spec 7A.3).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.styles.preview import (
    DEFAULT_PREVIEW_DURATION_SECONDS,
    build_preview_command,
)


def test_builds_a_trim_and_burn_command():
    command = build_preview_command(
        "C:/videos/clip.mp4", "C:/out/style.ass", "C:/out/preview.mp4", start_seconds=12.0
    )

    assert command[0].endswith("ffmpeg.exe") or command[0].endswith("ffmpeg")
    assert "-i" in command
    # Path() normalises the separator, so just check it's the same path,
    # not a particular slash direction.
    assert Path(command[command.index("-i") + 1]) == Path("C:/videos/clip.mp4")


def test_defaults_to_about_three_seconds():
    command = build_preview_command("v.mp4", "s.ass", "out.mp4", start_seconds=0)
    assert command[command.index("-t") + 1] == "3"
    assert DEFAULT_PREVIEW_DURATION_SECONDS == 3.0


def test_custom_duration_is_used():
    command = build_preview_command("v.mp4", "s.ass", "out.mp4", start_seconds=1, duration_seconds=5.5)
    assert command[command.index("-t") + 1] == "5.5"


def test_seek_uses_input_seeking_before_dash_i_for_speed():
    command = build_preview_command("v.mp4", "s.ass", "out.mp4", start_seconds=8.25)
    ss_index = command.index("-ss")
    i_index = command.index("-i")
    assert ss_index < i_index
    assert command[ss_index + 1] == "8.25"


def test_subtitles_filter_references_the_ass_file():
    command = build_preview_command("v.mp4", "C:/styles/pop.ass", "out.mp4", start_seconds=0)
    vf = command[command.index("-vf") + 1]
    assert "subtitles=" in vf
    assert "styles" in vf and "pop.ass" in vf


def test_fontsdir_is_wired_into_the_filter_by_default():
    command = build_preview_command("v.mp4", "s.ass", "out.mp4", start_seconds=0)
    vf = command[command.index("-vf") + 1]
    assert "fontsdir=" in vf


def test_custom_fonts_dir_is_respected():
    command = build_preview_command(
        "v.mp4", "s.ass", "out.mp4", start_seconds=0, fonts_dir="D:/custom-fonts"
    )
    vf = command[command.index("-vf") + 1]
    assert "custom-fonts" in vf


def test_rejects_negative_start():
    with pytest.raises(ValueError):
        build_preview_command("v.mp4", "s.ass", "out.mp4", start_seconds=-1)


def test_rejects_non_positive_duration():
    with pytest.raises(ValueError):
        build_preview_command("v.mp4", "s.ass", "out.mp4", start_seconds=0, duration_seconds=0)


def test_does_not_execute_anything():
    # If this module ever imports subprocess and calls it, this test's
    # existence is the reminder that preview.py must stay pure argv
    # construction (spec 7A.3: "Return the command; do not execute it").
    import ash_captions.styles.preview as preview_module

    assert not hasattr(preview_module, "subprocess")
