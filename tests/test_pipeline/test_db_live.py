"""Tests for the one-live-job-per-input rule, the stage columns, tolerant
option decoding, and the migration that collapses duplicates an older
database may already contain. Real SQLite files throughout.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ash_captions.pipeline.db import STAGES, DuplicateJobError, JobOptions, JobStatus, JobStore


def make_options(**overrides: object) -> JobOptions:
    defaults: dict[str, object] = dict(language="en", dialect="US", preset="POP", burn=False, translate=False)
    defaults.update(overrides)
    return JobOptions(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


class TestOneLiveJobPerInput:
    def test_second_insert_for_a_pending_input_returns_the_existing_job(self, store: JobStore) -> None:
        first = store.insert_job("in/clip.mp4", "out/clip", make_options())
        second = store.insert_job("in/clip.mp4", "out/clip (2)", make_options())

        assert second == first
        assert len(store.list_jobs()) == 1

    def test_running_input_is_also_refused(self, store: JobStore) -> None:
        first = store.insert_job("in/clip.mp4", "out/clip", make_options())
        store.mark_running(first)

        assert store.insert_job("in/clip.mp4", "out/clip", make_options()) == first

    def test_done_or_failed_input_may_be_queued_again(self, store: JobStore) -> None:
        first = store.insert_job("in/clip.mp4", "out/clip", make_options())
        store.mark_running(first)
        store.mark_failed(first, "boom")

        second = store.insert_job("in/clip.mp4", "out/clip", make_options())
        assert second != first
        store.mark_running(second)
        store.mark_done(second)
        assert store.insert_job("in/clip.mp4", "out/clip", make_options()) not in (first, second)

    def test_find_live_job(self, store: JobStore) -> None:
        assert store.find_live_job("in/clip.mp4") is None
        clip = Path("in") / "clip.mp4"
        job_id = store.insert_job(clip, "out/clip", make_options())
        live = store.find_live_job(clip)
        assert live is not None and live.id == job_id
        assert store.find_live_job(str(clip)) is not None

    def test_requeue_refuses_when_another_live_job_covers_the_file(self, store: JobStore) -> None:
        first = store.insert_job("in/clip.mp4", "out/clip", make_options())
        store.mark_running(first)
        store.mark_failed(first, "boom")
        store.insert_job("in/clip.mp4", "out/clip (2)", make_options())  # a fresh live job

        with pytest.raises(DuplicateJobError):
            store.requeue(first)
        assert store.get_job(first).status == JobStatus.FAILED  # type: ignore[union-attr]

    def test_index_survives_reopening(self, tmp_path: Path) -> None:
        db = tmp_path / "jobs.sqlite3"
        JobStore(db).insert_job("in/clip.mp4", "out/clip", make_options())
        reopened = JobStore(db)
        assert len(reopened.list_jobs()) == 1
        assert reopened.insert_job("in/clip.mp4", "out/clip", make_options()) == 1


_OLD_SCHEMA = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_path TEXT NOT NULL,
    output_dir TEXT NOT NULL,
    status TEXT NOT NULL,
    options_json TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
"""


class TestMigrationFromAnOlderDatabase:
    def _old_db_with_duplicates(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.executescript(_OLD_SCHEMA)
        rows = [
            ("in/a.mp4", "pending"), ("in/a.mp4", "pending"), ("in/a.mp4", "running"),
            ("in/b.mp4", "pending"), ("in/b.mp4", "done"),
        ]
        for input_path, status in rows:
            conn.execute(
                "INSERT INTO jobs (input_path, output_dir, status, options_json, created_at) "
                "VALUES (?, 'out', ?, '{\"language\": \"en\", \"dialect\": null, \"preset\": \"POP\", "
                "\"burn\": false, \"translate\": false}', '2024-01-01T00:00:00+00:00')",
                (input_path, status),
            )
        conn.commit()
        conn.close()

    def test_duplicate_live_rows_collapse_to_the_oldest(self, tmp_path: Path) -> None:
        db = tmp_path / "jobs.sqlite3"
        self._old_db_with_duplicates(db)

        store = JobStore(db)

        a_rows = [j for j in store.list_jobs() if j.input_path == "in/a.mp4"]
        live = [j for j in a_rows if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]
        assert [j.id for j in live] == [1]
        failed = sorted((j for j in a_rows if j.status == JobStatus.FAILED), key=lambda j: j.id)
        assert [j.error for j in failed] == ["duplicate of job 1", "duplicate of job 1"]
        # b's single pending row and its done row are untouched.
        b_rows = {j.id: j.status for j in store.list_jobs() if j.input_path == "in/b.mp4"}
        assert b_rows == {4: JobStatus.PENDING, 5: JobStatus.DONE}

    def test_stage_columns_are_added_to_an_old_schema(self, tmp_path: Path) -> None:
        db = tmp_path / "jobs.sqlite3"
        self._old_db_with_duplicates(db)

        store = JobStore(db)
        store.mark_running(4)
        store.mark_stage(4, "transcribe")

        job = store.get_job(4)
        assert job is not None
        assert job.stage == "transcribe"
        assert job.stage_started_at is not None

    def test_reopening_a_migrated_database_is_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "jobs.sqlite3"
        self._old_db_with_duplicates(db)
        JobStore(db)
        JobStore(db)  # must not raise on the second ALTER/index pass
        assert len(JobStore(db).list_jobs()) == 5


class TestStage:
    def test_mark_stage_records_name_and_time_and_clears_on_finish(self, store: JobStore) -> None:
        job_id = store.insert_job("in/clip.mp4", "out/clip", make_options())
        store.mark_running(job_id)
        for stage in STAGES:
            store.mark_stage(job_id, stage)
            assert store.get_job(job_id).stage == stage  # type: ignore[union-attr]
        store.mark_done(job_id)
        assert store.get_job(job_id).stage is None  # type: ignore[union-attr]

    def test_unknown_stage_is_rejected(self, store: JobStore) -> None:
        job_id = store.insert_job("in/clip.mp4", "out/clip", make_options())
        with pytest.raises(ValueError):
            store.mark_stage(job_id, "dancing")


class TestJobOptionsDecoding:
    def test_missing_fields_get_defaults_and_unknown_fields_are_ignored(self) -> None:
        options = JobOptions.from_json('{"language": "es", "preset": "CLEAN", "future_field": 1}')
        assert options == JobOptions(language="es", dialect=None, preset="CLEAN", burn=False, translate=False)

    def test_old_row_without_a_newer_field_still_loads(self, tmp_path: Path) -> None:
        db = tmp_path / "jobs.sqlite3"
        conn = sqlite3.connect(db)
        conn.executescript(_OLD_SCHEMA)
        conn.execute(
            "INSERT INTO jobs (input_path, output_dir, status, options_json, created_at) "
            "VALUES ('in/a.mp4', 'out', 'done', '{\"language\": \"en\", \"preset\": \"POP\"}', 't')"
        )
        conn.commit()
        conn.close()

        job = JobStore(db).get_job(1)
        assert job is not None
        assert job.options.translate is False


class TestListLimit:
    def test_limit_returns_the_newest_rows(self, store: JobStore) -> None:
        ids = [store.insert_job(f"in/{n}.mp4", "out", make_options()) for n in range(5)]
        assert [j.id for j in store.list_jobs(limit=2)] == ids[-1:-3:-1]
        assert len(store.list_jobs()) == 5

    def test_negative_limit_is_rejected(self, store: JobStore) -> None:
        with pytest.raises(ValueError):
            store.list_jobs(limit=-1)

    def test_list_live_jobs_is_oldest_first(self, store: JobStore) -> None:
        a = store.insert_job("in/a.mp4", "out/a", make_options())
        b = store.insert_job("in/b.mp4", "out/b", make_options())
        store.mark_running(a)
        assert [j.id for j in store.list_live_jobs()] == [a, b]
        assert store.output_dir_in_use("out/a") is True
        assert store.output_dir_in_use("out/zzz") is False
