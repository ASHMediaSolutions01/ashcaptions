"""A failed job explains itself to the editor.

The stored error text is the engine's, plus ffmpeg's stderr tail. What the
browser needs on top of it is one plain sentence and a yes/no on whether
Retry can change anything -- the critique of 2026-09-23 scored error
recovery 1/4 because the card showed the raw text with Retry as the
primary button on a file that was not a video.
"""
from __future__ import annotations

import pytest

from ash_captions.web.failures import explain_failure
from ash_captions.web.models import JobStatus

from .test_jobs import _submit


class TestTheRules:
    def test_a_file_that_is_not_a_video_says_so_and_is_not_retryable(self):
        stored = (
            "ffmpeg failed extracting audio from not-really-a-video.mp4 (exit 3199971767)\n"
            "--- ffmpeg stderr (tail) ---\n"
            "[mov,mp4,m4a,3gp,3g2,mj2 @ 0000] moov atom not found\n"
            "C:\\Users\\x\\AppData\\Local\\Temp\\ash\\in\\not-really-a-video.mp4: Invalid data found"
        )
        out = explain_failure(stored)
        assert "couldn't be read as a video" in out.reason
        assert "re-export" in out.reason
        assert out.retryable is False
        assert "3199971767" not in out.reason
        assert "Temp" not in out.reason

    def test_a_moved_file_is_not_retryable_until_it_is_put_back(self):
        out = explain_failure("Input video not found: D:\\client\\reel.mp4")
        assert "moved or renamed" in out.reason
        assert out.retryable is False

    def test_a_full_drive_is_retryable_once_cleared(self):
        out = explain_failure("Not enough free space on D: \u2014 need about 2.1 GB, have 0.4 GB")
        assert "free space" in out.reason
        assert out.retryable is True

    def test_a_job_cut_off_by_shutdown_is_retryable(self):
        out = explain_failure("Transcription of reel.wav was cancelled")
        assert "stopped before it finished" in out.reason
        assert out.retryable is True

    def test_behind_the_speaker_wins_over_the_frame_size_phrase_it_contains(self):
        out = explain_failure(
            "Captions behind the speaker need the video's frame size (probe failed)"
        )
        assert "behind the speaker" in out.reason.lower()
        assert "9:16" not in out.reason
        assert out.retryable is False

    def test_the_reel_scan_names_the_option_to_untick(self):
        out = explain_failure(
            "Reframing to 9:16 needs the video's frame size, and ffprobe could not read it."
        )
        assert "9:16" in out.reason and "Untick" in out.reason
        assert out.retryable is False

    def test_a_burn_failure_asks_for_one_retry_then_the_details(self):
        out = explain_failure("ffmpeg failed burning captions into reel.mp4 (exit 1)\n--- ffmpeg stderr (tail) ---\nx")
        assert "Burning" in out.reason and "technical details" in out.reason
        assert out.retryable is True

    def test_a_missing_ffmpeg_points_at_the_install(self):
        out = explain_failure("ffmpeg executable not found at C:\\x\\bin\\ffmpeg.exe")
        assert "Reinstall" in out.reason
        assert out.retryable is False

    def test_matching_ignores_case(self):
        assert explain_failure("FFMPEG FAILED EXTRACTING AUDIO from x").retryable is False


class TestTheFallback:
    def test_an_unknown_failure_shows_its_own_first_line_without_the_stderr(self):
        out = explain_failure("Something nobody has seen yet\n--- ffmpeg stderr (tail) ---\nnoise")
        assert out.reason == "Something nobody has seen yet"
        assert out.retryable is True

    @pytest.mark.parametrize("text", [None, "", "   "])
    def test_no_text_at_all_still_gets_a_sentence(self, text):
        out = explain_failure(text)
        assert out.reason
        assert out.retryable is True


class TestOnTheWire:
    """The browser reads ``reason`` and ``retryable`` off the Job itself, so
    every producer -- the real adapter, the test fake, the SSE snapshot --
    carries them without each having to know the rules."""

    def test_a_failed_job_carries_a_reason_and_whether_retry_helps(self, client, fake_queue):
        job = _submit(client).json()
        fake_queue.force_status(
            job["id"], JobStatus.FAILED,
            error="ffmpeg failed extracting audio from x.mp4 (exit 3199971767)",
        )
        body = client.get(f"/api/jobs/{job['id']}").json()
        assert body["reason"].startswith("This file couldn't be read as a video")
        assert body["retryable"] is False
        assert body["error"].startswith("ffmpeg failed")  # the technical text is still there

    def test_a_job_that_did_not_fail_has_neither(self, client):
        body = _submit(client).json()
        assert body["reason"] is None
        assert body["retryable"] is None

    def test_the_list_carries_them_too(self, client, fake_queue):
        job = _submit(client).json()
        fake_queue.force_status(job["id"], JobStatus.FAILED, error="Not enough free space on D:")
        rows = client.get("/api/jobs").json()
        mine = next(r for r in rows if r["id"] == job["id"])
        assert mine["retryable"] is True
        assert "free space" in mine["reason"]


class TestRetryRefusals:
    def test_a_retry_refused_because_the_file_is_already_queued_says_that(self, client, fake_queue, monkeypatch):
        """The route used to swallow the real reason behind a generic 'not in
        a retryable state', which is what an editor saw when the watch
        folder had already picked the same file up."""
        from ash_captions.web.interfaces import JobNotRetryableError

        job = _submit(client).json()
        fake_queue.force_status(job["id"], JobStatus.FAILED, error="x")

        def refuse(job_id):
            raise JobNotRetryableError(
                f"job {job_id} cannot be requeued: another job for the same file is already queued"
            )

        monkeypatch.setattr(fake_queue, "retry", refuse)
        res = client.post(f"/api/jobs/{job['id']}/retry")
        assert res.status_code == 409
        assert "same file is already queued" in res.json()["detail"]

    def test_a_plain_refusal_keeps_the_generic_message(self, client):
        job = _submit(client).json()  # pending, so not retryable
        res = client.post(f"/api/jobs/{job['id']}/retry")
        assert res.status_code == 409
        assert "not in a retryable state" in res.json()["detail"]
