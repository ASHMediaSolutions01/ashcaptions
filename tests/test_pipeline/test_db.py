"""Tests for the SQLite job store: schema, lifecycle transitions, and the
crash-recovery / failed-job-persistence guarantees from spec section 12.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.pipeline.db import Job, JobOptions, JobStatus, JobStore


def make_options(**overrides: object) -> JobOptions:
    defaults: dict[str, object] = {
        "language": "en",
        "dialect": "US",
        "preset": "POP",
        "burn": False,
        "translate": False,
    }
    defaults.update(overrides)
    return JobOptions(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite3")


def test_insert_and_get_job(store: JobStore) -> None:
    job_id = store.insert_job(r"C:\AshCaptions\in\clip.mp4", r"C:\AshCaptions\out\clip", make_options())

    job = store.get_job(job_id)

    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.progress == 0
    assert job.error is None
    assert job.options.language == "en"
    assert job.started_at is None
    assert job.finished_at is None
    assert job.created_at


def test_get_job_missing_returns_none(store: JobStore) -> None:
    assert store.get_job(999) is None


def test_insert_job_rejects_empty_paths(store: JobStore) -> None:
    with pytest.raises(ValueError):
        store.insert_job("", "out", make_options())
    with pytest.raises(ValueError):
        store.insert_job("in.mp4", "  ", make_options())


def test_insert_job_rejects_non_options(store: JobStore) -> None:
    with pytest.raises(TypeError):
        store.insert_job("in.mp4", "out", {"language": "en"})  # type: ignore[arg-type]


def test_fetch_oldest_pending_is_fifo(store: JobStore) -> None:
    first = store.insert_job("a.mp4", "out/a", make_options())
    second = store.insert_job("b.mp4", "out/b", make_options())

    oldest = store.fetch_oldest_pending()

    assert oldest is not None
    assert oldest.id == first
    assert oldest.id != second


def test_fetch_oldest_pending_ignores_non_pending(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(job_id)

    assert store.fetch_oldest_pending() is None


def test_mark_running_then_done_lifecycle(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())

    store.mark_running(job_id)
    running = store.get_job(job_id)
    assert running is not None
    assert running.status == JobStatus.RUNNING
    assert running.started_at is not None

    store.mark_progress(job_id, 42)
    assert store.get_job(job_id).progress == 42  # type: ignore[union-attr]

    store.mark_done(job_id)
    done = store.get_job(job_id)
    assert done is not None
    assert done.status == JobStatus.DONE
    assert done.progress == 100
    assert done.finished_at is not None


def test_mark_progress_validates_range(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    with pytest.raises(ValueError):
        store.mark_progress(job_id, 101)
    with pytest.raises(ValueError):
        store.mark_progress(job_id, -1)


def test_failed_job_persists_with_its_error(store: JobStore) -> None:
    """A failed job stays visible with its error; it is never silently dropped."""
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(job_id)

    store.mark_failed(job_id, "ffmpeg exited with code 1: no such file")

    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error == "ffmpeg exited with code 1: no such file"
    assert job.finished_at is not None
    # Still enumerable via list_jobs -- never dropped from the table.
    failed_jobs = store.list_jobs(status=JobStatus.FAILED)
    assert [j.id for j in failed_jobs] == [job_id]


def test_reset_stale_running_on_startup(store: JobStore) -> None:
    """Crash-recovery: a job left 'running' is reset to 'pending' on startup."""
    stuck = store.insert_job("a.mp4", "out/a", make_options())
    also_stuck = store.insert_job("b.mp4", "out/b", make_options())
    still_pending = store.insert_job("c.mp4", "out/c", make_options())
    already_done = store.insert_job("d.mp4", "out/d", make_options())

    store.mark_running(stuck)
    store.mark_progress(stuck, 55)
    store.mark_running(also_stuck)
    store.mark_running(already_done)
    store.mark_done(already_done)

    reset_ids = store.reset_stale_running()

    assert sorted(reset_ids) == sorted([stuck, also_stuck])

    for job_id in (stuck, also_stuck):
        job = store.get_job(job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.progress == 0
        assert job.started_at is None
        assert job.error is None

    assert store.get_job(still_pending).status == JobStatus.PENDING  # type: ignore[union-attr]
    assert store.get_job(already_done).status == JobStatus.DONE  # type: ignore[union-attr]


def test_reset_stale_running_is_idempotent_when_nothing_stuck(store: JobStore) -> None:
    store.insert_job("a.mp4", "out/a", make_options())
    assert store.reset_stale_running() == []


def test_requeue_resets_a_failed_job(store: JobStore) -> None:
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(job_id)
    store.mark_progress(job_id, 30)
    store.mark_failed(job_id, "ffmpeg exploded")

    store.requeue(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.progress == 0
    assert job.error is None
    assert job.started_at is None
    assert job.finished_at is None


def test_requeue_resets_a_done_job(store: JobStore) -> None:
    """Requeuing works regardless of current status -- including 'done',
    e.g. to re-run a job after a glossary change."""
    job_id = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(job_id)
    store.mark_done(job_id)

    store.requeue(job_id)

    job = store.get_job(job_id)
    assert job is not None
    assert job.status == JobStatus.PENDING
    assert job.progress == 0
    assert job.finished_at is None


def test_requeue_only_affects_the_target_job(store: JobStore) -> None:
    target = store.insert_job("a.mp4", "out/a", make_options())
    other = store.insert_job("b.mp4", "out/b", make_options())
    store.mark_running(target)
    store.mark_failed(target, "boom")
    store.mark_running(other)

    store.requeue(target)

    assert store.get_job(other).status == JobStatus.RUNNING  # type: ignore[union-attr]


def test_requeue_unknown_id_raises_key_error(store: JobStore) -> None:
    with pytest.raises(KeyError):
        store.requeue(999)


def test_delete_job_removes_only_finished_rows(store: JobStore) -> None:
    """"Remove from list" forgets the row and nothing else; a live job
    can't be deleted from under the worker."""
    done = store.insert_job("a.mp4", "out/a", make_options())
    store.mark_running(done)
    store.mark_done(done)
    failed = store.insert_job("b.mp4", "out/b", make_options())
    store.mark_running(failed)
    store.mark_failed(failed, "boom")
    pending = store.insert_job("c.mp4", "out/c", make_options())
    running = store.insert_job("d.mp4", "out/d", make_options())
    store.mark_running(running)

    store.delete_job(done)
    store.delete_job(failed)
    assert store.get_job(done) is None and store.get_job(failed) is None

    for live in (pending, running):
        with pytest.raises(ValueError):
            store.delete_job(live)
        assert store.get_job(live) is not None
    with pytest.raises(KeyError):
        store.delete_job(999)
    assert [j.id for j in store.list_jobs()] == [running, pending]


def test_wal_mode_enabled(store: JobStore, tmp_path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(store.db_path)
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


# --- the client field (per-client glossaries) ----------------------------------


def test_client_round_trips_through_the_row(store: JobStore) -> None:
    job_id = store.insert_job(r"C:\in\Acme\clip.mp4", r"C:\out\clip", make_options(client="Acme Corp"))

    assert store.get_job(job_id).options.client == "Acme Corp"
    assert store.get_job(store.insert_job(r"C:\in\b.mp4", r"C:\out\b", make_options())).options.client is None


def test_rows_written_before_client_existed_read_as_no_client(store: JobStore, tmp_path: Path) -> None:
    """An older build's options_json has no `client` key; a hand-edited one
    may hold junk. Both must read back as None, never raise."""
    import sqlite3

    old_json = '{"language": "en", "dialect": null, "preset": "POP", "burn": false, "translate": false}'
    junk_json = '{"language": "en", "dialect": null, "preset": "POP", "burn": false, "translate": false, "client": 42}'
    blank_json = '{"language": "en", "dialect": null, "preset": "POP", "burn": false, "translate": false, "client": "  "}'
    conn = sqlite3.connect(store.db_path)
    for n, raw in enumerate((old_json, junk_json, blank_json)):
        conn.execute(
            "INSERT INTO jobs (input_path, output_dir, status, options_json, progress, created_at) "
            "VALUES (?, ?, 'done', ?, 100, '2026-01-01T00:00:00+00:00')",
            (rf"C:\in\old{n}.mp4", rf"C:\out\old{n}", raw),
        )
    conn.commit()
    conn.close()

    jobs = store.list_jobs()
    assert len(jobs) == 3
    assert all(job.options.client is None for job in jobs)
    assert JobOptions.from_json(old_json).client is None
    assert JobOptions.from_json(old_json) == make_options(dialect=None)


def test_known_clients_is_distinct_newest_first_case_insensitive(store: JobStore) -> None:
    for n, client in enumerate(["Acme", None, "globex", "ACME", "Initech", "Globex"]):
        store.insert_job(rf"C:\in\{n}.mp4", rf"C:\out\{n}", make_options(client=client))

    assert store.known_clients() == ["Globex", "Initech", "ACME"]
    assert store.known_clients(limit=1) == ["Globex"]
    assert JobStore(store.db_path).known_clients(limit=0) == []


def test_caption_position_round_trips_through_json_and_the_store(store: JobStore) -> None:
    opts = make_options(caption_x=0.5, caption_y=0.25)
    assert opts.caption_position == (0.5, 0.25)
    assert JobOptions.from_json(opts.to_json()).caption_position == (0.5, 0.25)
    assert make_options().caption_position is None
    job_id = store.insert_job(r"C:\in\pos.mp4", r"C:\out\pos", opts)
    stored = store.get_job(job_id)
    assert stored is not None
    assert (stored.options.caption_x, stored.options.caption_y) == (0.5, 0.25)


@pytest.mark.parametrize(
    "raw",
    [
        '"caption_x": 0.5',
        '"caption_x": 0.5, "caption_y": null',
        '"caption_x": 1.5, "caption_y": 0.5',
        '"caption_x": 0.5, "caption_y": -0.1',
        '"caption_x": "left", "caption_y": 0.5',
        '"caption_x": true, "caption_y": 0.5',
    ],
)
def test_a_half_or_out_of_range_position_reads_as_none(raw: str) -> None:
    """A hand-edited or foreign row must read as "no position", never raise."""
    options = JobOptions.from_json('{"language": "en", "preset": "POP", ' + raw + "}")
    assert options.caption_position is None
    assert options.caption_x is None and options.caption_y is None


def test_rows_from_before_the_position_existed_read_as_none() -> None:
    old_json = '{"language": "en", "dialect": null, "preset": "POP", "burn": false, "translate": false}'
    assert JobOptions.from_json(old_json).caption_position is None
    assert JobOptions.from_json(old_json) == make_options(dialect=None)
