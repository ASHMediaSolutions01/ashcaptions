"""Tests for ash_captions.engine.burn: command construction and encoder choice.

No real ffmpeg or nvidia-smi is invoked. Running a burn (process handling,
cancellation, the .part rename) is covered in test_burn_process.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from ash_captions.engine import burn, encoders
from ash_captions.engine.burn import (
    BITRATE_1080P,
    BITRATE_4K,
    CAPTIONS_FILENAME,
    FILTER_SCRIPT_FILENAME,
    MIN_BITRATE,
    BurnInError,
    _escape_path_for_filtergraph,
    _parse_progress_line,
    audio_args,
    build_burn_command,
    build_filtergraph,
    detect_nvenc,
    encoder_args,
    filter_file_option,
    part_path_for,
    software_bitrate,
)


@pytest.fixture(autouse=True)
def _clear_caches():
    encoders._encoder_cache.clear()
    encoders._version_cache.clear()
    encoders._nvenc_test_cache.clear()
    yield
    encoders._encoder_cache.clear()
    encoders._version_cache.clear()
    encoders._nvenc_test_cache.clear()


def _ass(tmp_path: Path, name: str = "subs.ass") -> Path:
    path = tmp_path / name
    path.write_text("[Script Info]\nTitle: t\n", encoding="utf-8")
    return path


def _command(tmp_path: Path, **overrides) -> list[str]:
    options = dict(
        work_dir=tmp_path / "work",
        ffmpeg_path=tmp_path / "ffmpeg.exe",  # does not exist: probing fails, defaults apply
    )
    options.update(overrides)
    return build_burn_command(tmp_path / "in.mp4", _ass(tmp_path), tmp_path / "out.mp4", **options)


# ---------------------------------------------------------------------------
# detect_nvenc
# ---------------------------------------------------------------------------


def test_detect_nvenc_true_when_nvidia_smi_succeeds():
    with patch("ash_captions.engine.encoders.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert detect_nvenc() is True


def test_detect_nvenc_false_when_nvidia_smi_fails():
    with patch("ash_captions.engine.encoders.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")
        assert detect_nvenc() is False


def test_detect_nvenc_false_when_nvidia_smi_missing():
    with patch("ash_captions.engine.encoders.subprocess.run", side_effect=FileNotFoundError()):
        assert detect_nvenc() is False


def test_detect_nvenc_false_on_timeout():
    with patch("ash_captions.engine.encoders.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)
        assert detect_nvenc() is False


# ---------------------------------------------------------------------------
# encoder selection
# ---------------------------------------------------------------------------


def test_falls_back_to_libx264_when_the_binary_cannot_be_probed(tmp_path):
    """Not "by default": a missing binary means probing failed, and the
    historical default is the best guess left."""
    args = _command(tmp_path)
    assert args[args.index("-c:v") + 1] == "libx264"


def test_trusts_an_nvenc_request_when_the_binary_cannot_be_probed(tmp_path):
    args = _command(tmp_path, use_nvenc=True)
    assert args[args.index("-c:v") + 1] == "h264_nvenc"


def test_nvenc_request_falls_back_to_software_when_the_test_encode_fails(monkeypatch, tmp_path):
    """nvidia-smi being present says there is a driver, not that this ffmpeg
    can drive it; that mismatch used to fail at the start of every burn."""
    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(encoders, "nvenc_encode_works", lambda _p: False)
    assert burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=True) == "libx264"


def test_nvenc_is_used_when_the_test_encode_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"h264_nvenc", "libx264"}))
    monkeypatch.setattr(encoders, "nvenc_encode_works", lambda _p: True)
    assert burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=True) == "h264_nvenc"


def test_nvenc_encode_test_result_is_cached_per_binary(tmp_path):
    with patch("ash_captions.engine.encoders.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        assert burn.nvenc_encode_works(tmp_path / "ffmpeg.exe") is True
        assert burn.nvenc_encode_works(tmp_path / "ffmpeg.exe") is True
    assert mock_run.call_count == 1
    argv = mock_run.call_args[0][0]
    assert "h264_nvenc" in argv and "-nostdin" in argv and "-frames:v" in argv
    assert mock_run.call_args[1]["timeout"] == 20


def test_selects_a_software_encoder_the_build_actually_has(monkeypatch, tmp_path):
    """An LGPL ffmpeg has no libx264 -- x264 is GPL, so the LGPL build is
    configured --disable-libx264. h264_mf (High profile) beats libopenh264
    (baseline only) as the fallback."""
    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"libopenh264", "h264_mf"}))
    assert burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=False) == "h264_mf"


def test_prefers_libx264_when_the_build_has_it(monkeypatch, tmp_path):
    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"libx264", "libopenh264", "h264_mf"}))
    assert burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=False) == "libx264"


def test_raises_a_named_error_when_no_h264_encoder_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(encoders, "available_encoders", lambda _p: frozenset({"vp9", "aac"}))
    with pytest.raises(BurnInError, match="no usable H.264 encoder"):
        burn.select_video_encoder(tmp_path / "ffmpeg.exe", use_nvenc=False)


def test_probe_failure_falls_back_rather_than_blocking_a_burn(tmp_path):
    assert burn.available_encoders(tmp_path / "definitely-not-here.exe") == frozenset()
    assert burn.select_video_encoder(tmp_path / "definitely-not-here.exe") == "libx264"


# ---------------------------------------------------------------------------
# encoder / audio flags
# ---------------------------------------------------------------------------


def test_libx264_gets_explicit_quality_settings():
    assert encoder_args("libx264") == ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]


def test_nvenc_gets_explicit_quality_settings():
    args = encoder_args("h264_nvenc")
    assert args[:2] == ["-c:v", "h264_nvenc"]
    for flag, value in (("-preset", "p5"), ("-rc", "vbr"), ("-cq", "19"), ("-b:v", "0"), ("-pix_fmt", "yuv420p")):
        assert args[args.index(flag) + 1] == value


@pytest.mark.parametrize("encoder", ["h264_mf", "libopenh264"])
def test_bitrate_encoders_get_a_bitrate_scaled_by_pixels(encoder):
    at_1080p = encoder_args(encoder, width=1920, height=1080)
    at_4k = encoder_args(encoder, width=3840, height=2160)
    assert at_1080p[at_1080p.index("-b:v") + 1] == str(BITRATE_1080P)
    assert at_4k[at_4k.index("-b:v") + 1] == str(BITRATE_4K)
    assert at_1080p[-2:] == ["-pix_fmt", "yuv420p"]


def test_bitrate_is_linear_in_pixels_and_never_silly():
    assert software_bitrate(0, 0) == BITRATE_1080P  # unknown size: assume 1080p
    assert BITRATE_1080P < software_bitrate(2560, 1440) < BITRATE_4K
    assert software_bitrate(1280, 720) < BITRATE_1080P
    assert software_bitrate(320, 240) == MIN_BITRATE


@pytest.mark.parametrize("codec", [None, "aac", "AAC", "mp3"])
def test_mp4_friendly_audio_is_copied(codec):
    assert audio_args(codec) == ["-c:a", "copy"]


@pytest.mark.parametrize("codec", ["pcm_s16le", "opus", "flac", "vorbis"])
def test_other_audio_is_reencoded_to_aac(codec):
    assert audio_args(codec) == ["-c:a", "aac", "-b:a", "192k"]


def test_burn_command_reencodes_audio_from_the_probe(tmp_path):
    args = _command(tmp_path, audio_codec="pcm_s16le")
    assert args[args.index("-c:a") + 1] == "aac"


# ---------------------------------------------------------------------------
# filtergraph file and work directory
# ---------------------------------------------------------------------------


def test_filtergraph_is_written_to_a_file_and_referenced_not_passed(tmp_path):
    args = _command(tmp_path)
    assert "-vf" not in args
    assert args[args.index("-/filter:v") + 1] == FILTER_SCRIPT_FILENAME
    script = tmp_path / "work" / FILTER_SCRIPT_FILENAME
    assert script.read_text(encoding="utf-8") == f"ass={CAPTIONS_FILENAME}"


def test_ass_file_is_copied_under_a_fixed_name(tmp_path):
    source = _ass(tmp_path, "Client's take 1, part=2 [final].ass")
    build_burn_command(
        tmp_path / "in.mp4", source, tmp_path / "out.mp4", work_dir=tmp_path / "work", ffmpeg_path=tmp_path / "f.exe"
    )
    assert (tmp_path / "work" / CAPTIONS_FILENAME).read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    "name",
    ["Client's reel (v2)", "take 1, part=2; [final] 100%", "Espa\u00f1ol \u00f1 \u65e5\u672c", "colon: in dir"],
)
def test_user_paths_never_reach_the_filtergraph(tmp_path, name):
    """An apostrophe closes the quoted filename and turns the rest of the
    path into filter syntax; a comma or colon splits it. None of it may
    be in the graph at all."""
    folder = tmp_path / name.replace(":", "_")
    folder.mkdir()
    ass = _ass(folder, f"{name.replace(':', '_')}.ass")
    video = folder / f"{name.replace(':', '_')}.mp4"
    args = build_burn_command(
        video, ass, folder / "out.mp4", work_dir=tmp_path / "work", ffmpeg_path=tmp_path / "ffmpeg.exe"
    )
    script = (tmp_path / "work" / FILTER_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert script == f"ass={CAPTIONS_FILENAME}"
    assert str(video) in args  # the input path is an argv element, never filter text


def test_fontsdir_is_escaped_in_the_script(tmp_path):
    _command(tmp_path, fontsdir=r"C:\Program Files\Ash Captions\fonts")
    script = (tmp_path / "work" / FILTER_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert script == r"ass=captions.ass:fontsdir='C\:/Program Files/Ash Captions/fonts'"


def test_apostrophe_in_fontsdir_survives_both_parsing_levels():
    """The graph parser strips quotes and keeps backslashes; the option
    parser then applies them. An apostrophe therefore closes the quote,
    is written as an escaped backslash plus escaped quote, and reopens."""
    assert _escape_path_for_filtergraph(Path(r"C:\Users\O'Brien\fonts")) == r"C\:/Users/O'\\\''Brien/fonts"


def test_punch_filter_comes_before_the_subtitles(tmp_path):
    _command(tmp_path, punch_filter="scale=w='iw':h='ih':eval=frame,crop=w=iw:h=ih")
    script = (tmp_path / "work" / FILTER_SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert script == "scale=w='iw':h='ih':eval=frame,crop=w=iw:h=ih,ass=captions.ass"
    assert build_filtergraph(punch_filter=None) == "ass=captions.ass"


def test_command_shape(tmp_path):
    args = _command(tmp_path)
    assert args[0] == str((tmp_path / "ffmpeg.exe").resolve())
    for flag in ("-nostdin", "-hide_banner", "-y"):
        assert flag in args
    assert args[args.index("-i") + 1] == str((tmp_path / "in.mp4").resolve())
    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    assert maps == ["0:v:0", "0:a:0?"]
    assert args[args.index("-progress") + 1] == "pipe:1"
    assert "-nostats" in args
    assert "faststart" not in " ".join(args)


def test_output_is_the_part_file_not_the_final_name(tmp_path):
    args = _command(tmp_path)
    assert args[-1] == str(part_path_for((tmp_path / "out.mp4").resolve()))
    assert args[-1].endswith("out.part.mp4")


def test_part_path_for_keeps_directory_and_container():
    assert part_path_for(Path("C:/jobs/talk.captioned.mp4")) == Path("C:/jobs/talk.captioned.part.mp4")
    assert part_path_for(Path("C:/jobs/noext")) == Path("C:/jobs/noext.part.mp4")


def test_missing_ass_raises_before_staging(tmp_path):
    with pytest.raises(BurnInError, match="Subtitle file not found"):
        build_burn_command(
            tmp_path / "in.mp4", tmp_path / "nope.ass", tmp_path / "out.mp4",
            work_dir=tmp_path / "work", ffmpeg_path=tmp_path / "ffmpeg.exe",
        )
    assert not (tmp_path / "work").exists()


def test_bare_ffmpeg_name_is_left_for_path_lookup(tmp_path):
    args = build_burn_command(
        tmp_path / "in.mp4", _ass(tmp_path), tmp_path / "out.mp4", work_dir=tmp_path / "work", ffmpeg_path="ffmpeg"
    )
    assert args[0] == "ffmpeg"


# ---------------------------------------------------------------------------
# filter file option per ffmpeg version
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("major", "expected"), [(None, "-/filter:v"), (7, "-/filter:v"), (8, "-/filter:v"), (6, "-filter_script:v")])
def test_filter_file_option_follows_the_ffmpeg_version(monkeypatch, major, expected):
    monkeypatch.setattr(encoders, "ffmpeg_major_version", lambda _p: major)
    assert filter_file_option("ffmpeg") == expected


@pytest.mark.parametrize(
    ("banner", "expected"),
    [("ffmpeg version 6.1.1-full_build Copyright", 6), ("ffmpeg version n7.0 Copyright", 7),
     ("ffmpeg version N-126386-gc27482a18d-20260901 Copyright", None), ("", None)],
)
def test_ffmpeg_major_version_parses_release_and_git_banners(banner, expected):
    with patch("ash_captions.engine.encoders.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout=banner, stderr="")
        assert burn.ffmpeg_major_version("ffmpeg-x") == expected


# ---------------------------------------------------------------------------
# _parse_progress_line
# ---------------------------------------------------------------------------


def test_parse_progress_line_out_time_us():
    assert _parse_progress_line("out_time_us=5000000", duration_seconds=10.0) == 50.0


def test_parse_progress_line_out_time_ms():
    assert _parse_progress_line("out_time_ms=5000000", duration_seconds=10.0) == 50.0


def test_parse_progress_line_clamps_to_100():
    assert _parse_progress_line("out_time_us=999999999", duration_seconds=10.0) == 100.0


def test_parse_progress_line_ignores_unrelated_lines():
    assert _parse_progress_line("frame=120", duration_seconds=10.0) is None


def test_parse_progress_line_returns_none_for_zero_duration():
    assert _parse_progress_line("out_time_us=1000000", duration_seconds=0.0) is None
