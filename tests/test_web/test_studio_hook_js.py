"""studio_hook.js under node: a finished job this tab started opens the
Studio, unless it went out as part of a batch -- jumping to the first of
five while the other four are still running would pull the editor off
the page. Run under node (skipped without it) with a stub document."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from ash_captions.web.app import STATIC_DIR

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is needed to run studio_hook.js")

HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const assigned = [];
const finished = [];
const setting = { checked: true, addEventListener() {} };
global.window = { location: { assign: (u) => assigned.push(u) }, AshQueue: { jobFinished: (j) => finished.push(j.id) } };
global.AshQueue = global.window.AshQueue; // a browser global, as the page has it
global.document = { getElementById: (id) => (id === "open-studio-check" ? setting : null) };
global.localStorage = { getItem: () => null, setItem() {} };
new Function("window", "document", "localStorage", src)(global.window, global.document, global.localStorage);
const hook = global.window.AshStudio;
const scenario = JSON.parse(process.argv[2]);
for (const [job, opts] of scenario.submitted) hook.noteSubmitted(job, opts);
hook.onJobs(scenario.jobs);
console.log(JSON.stringify({ assigned, finished }));
"""


def run(submitted, jobs):
    scenario = json.dumps({"submitted": submitted, "jobs": jobs})
    done = subprocess.run(
        [NODE, "-e", HARNESS, str(STATIC_DIR / "studio_hook.js"), scenario],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(done.stdout.strip().splitlines()[-1])


@needs_node
class TestOpeningTheStudio:
    def test_a_single_job_opens_the_studio_when_it_finishes(self):
        out = run([[{"id": "a"}, {"batch": False}]], [{"id": "a", "status": "done", "filename": "a.mp4"}])
        assert out == {"assigned": ["/studio/a"], "finished": ["a"]}

    def test_a_batch_job_is_announced_but_not_followed(self):
        out = run(
            [[{"id": "a"}, {"batch": True}], [{"id": "b"}, {"batch": True}]],
            [{"id": "a", "status": "done", "filename": "a.mp4"}, {"id": "b", "status": "running"}],
        )
        assert out == {"assigned": [], "finished": ["a"]}

    def test_another_tabs_job_is_ignored(self):
        out = run([[{"id": "a"}, {}]], [{"id": "z", "status": "done"}])
        assert out == {"assigned": [], "finished": []}

    def test_a_failed_job_is_announced_and_stays_put(self):
        out = run([[{"id": "a"}, {}]], [{"id": "a", "status": "failed"}])
        assert out == {"assigned": [], "finished": ["a"]}
