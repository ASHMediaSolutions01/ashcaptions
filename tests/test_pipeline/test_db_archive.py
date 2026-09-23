"""JobStore.list_jobs as the Queue's archive: searched by the input path
(file name and client folder) and paged, newest first.

The search has to survive the characters real file names carry: Windows
backslashes, spaces, apostrophes, percent signs and underscores (the last
two are LIKE wildcards and must match themselves, not anything).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ash_captions.pipeline.db import JobStore

from .test_db import make_options

NAMES = [
    r"C:\AshCaptions\in\Acme\ig reel.mp4",
    r"C:\AshCaptions\in\Acme\100% done_final.mp4",
    r"C:\AshCaptions\in\Bright Co\Client's cut.mov",
    r"C:\AshCaptions\in\Bright Co\entrevista.mp4",
]


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    s = JobStore(tmp_path / "jobs.sqlite3")
    for name in NAMES:
        s.insert_job(name, tmp_path / "out", make_options())
    return s


def names(jobs) -> list[str]:
    return [Path(j.input_path).name for j in jobs]


def test_search_reaches_the_client_typed_in_the_drawer(store: JobStore, tmp_path: Path) -> None:
    store.insert_job(r"D:\footage\interview.mp4", tmp_path / "out", make_options(client="Northwind"))
    assert names(store.list_jobs(query="northwind")) == ["interview.mp4"]
    assert names(store.list_jobs(query="north_wind")) == []


def test_search_is_case_insensitive_and_matches_the_client_folder(store: JobStore) -> None:
    assert names(store.list_jobs(query="REEL")) == ["ig reel.mp4"]
    assert names(store.list_jobs(query="bright co")) == ["entrevista.mp4", "Client's cut.mov"]


def test_wildcard_characters_in_the_query_match_themselves(store: JobStore) -> None:
    assert names(store.list_jobs(query="100%")) == ["100% done_final.mp4"]
    assert names(store.list_jobs(query="done_final")) == ["100% done_final.mp4"]
    # An underscore as a wildcard would match "ig reel"; escaped it must not.
    assert store.list_jobs(query="ig_reel") == []


def test_a_backslash_and_an_apostrophe_are_ordinary_text(store: JobStore) -> None:
    assert names(store.list_jobs(query=r"Acme\ig")) == ["ig reel.mp4"]
    assert names(store.list_jobs(query="Client's")) == ["Client's cut.mov"]


def test_offset_and_limit_page_newest_first(store: JobStore) -> None:
    assert names(store.list_jobs(limit=2)) == ["entrevista.mp4", "Client's cut.mov"]
    assert names(store.list_jobs(limit=2, offset=2)) == ["100% done_final.mp4", "ig reel.mp4"]
    assert names(store.list_jobs(offset=3)) == ["ig reel.mp4"]
    assert store.list_jobs(offset=10) == []


def test_a_negative_offset_is_refused(store: JobStore) -> None:
    with pytest.raises(ValueError):
        store.list_jobs(offset=-1)
