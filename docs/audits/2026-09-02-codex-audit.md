# ASH Captions — Deep Read-Only Audit

**Audit date:** 2026-09-02  
**Audited revision:** `34e5870` (`master`)  
**Audit mode:** Read-only analysis. No application, test, configuration, or documentation files were changed. This report is the only added file.

## Executive summary

ASH Captions has a notably strong test-to-code ratio, clear module boundaries, careful ffmpeg process handling, and thoughtful Windows-specific packaging work. The real ffmpeg and bundled-font integration checks pass. However, the repository is **not release-ready in its current state**.

The main reasons are:

1. The normal watch-folder workflow silently stops accepting a reused filename until the app restarts.
2. Production transcription does not enable the offline-only model-loading option that was added specifically to prevent network checks and re-downloads.
3. Web-uploaded client footage is retained indefinitely, while the advertised retention process deletes only output folders.
4. Jobs with the same filename stem share an output directory and can overwrite/mix one another's deliverables.
5. The full automated suite is red: **2 failed, 1044 passed, 27 skipped**.
6. The no-admin installer aborts when Task Scheduler registration is denied; this happened in the audit environment after it had already copied the application and created other state.
7. Settings are deserialized without runtime type or range validation, so a syntactically valid settings file can crash startup or create unsafe values.
8. Update application allows concurrent submissions and reuses a non-clean staging directory, creating race and stale-file risks.

There are no obvious remote-code-execution or LAN-exposure defects in the normal server configuration: the server is hard-gated to `127.0.0.1`, mutating browser requests require the app header, subprocesses use argument arrays instead of `shell=True`, and downloaded application updates are size/hash checked before extraction. The supply-chain model remains intentionally weaker than signed updates and has several additional hardening opportunities described below.

## Scope and evidence

Reviewed:

- 60 Python source/build files, approximately 9,651 lines.
- 57 test files, approximately 8,451 lines.
- FastAPI routes, browser JavaScript, SQLite queue, watch-folder lifecycle, transcription, timing rules, ASS/SRT/TXT writers, ffmpeg burn-in, style library, updater, installer, build/release tooling, configuration, retention, and product/operations documentation.
- Git working tree and recent history.

Executed without changing repository behavior:

| Check | Result |
|---|---|
| Full test suite | **2 failed, 1044 passed, 27 skipped** |
| Real ffmpeg + real libass/font integration suite | **27 passed** |
| `pip check` | Passed: no broken installed requirements |
| Python bytecode compilation | Passed |
| Build dry-run without model | Passed and produced a valid PyInstaller command |
| Build dry-run with documented `build\models` | Failed: model cache directory is absent |
| Dependency vulnerability scan | Not run; `pip-audit` is not installed and this read-only audit did not mutate the environment |
| Ruff / mypy | Not run; neither is installed |

The full test failures were reproduced twice. The 27 normally skipped tests were explicitly run against `bin\ffmpeg.exe`; all passed.

## Release decision

**Recommendation: block release until the P1 findings and the two failing tests are resolved.**

The watch-folder deduplication bug alone breaks the product's primary workflow in a common editing pattern: exporting `final.mp4`, processing it, revising the edit, and exporting another `final.mp4`. The second export is ignored until ASH Captions restarts.

## P1 — High-priority findings

### P1-1. A reused watch-folder filename is ignored until app restart

**Evidence:** `src/ash_captions/pipeline/watcher.py:135-136`, `:165-167`, `:188-192`, `:197-201`; input deletion at `src/ash_captions/app/runner.py:294-298`.

`Watcher` adds a path to `_enqueued` before invoking the callback. Entries are never removed from `_enqueued`, including after the processed input is deleted. The stale-state cleanup explicitly excludes paths already in `_enqueued`. A local reproduction during this audit processed `same.mp4` once, deleted it, recreated it, and received only one callback.

The same mechanism also strands a file if the enqueue callback fails: `_enqueue_watch_file` catches and logs the exception, so the watcher considers the path permanently handled even though no job was created.

**Impact:** Silent missed work in the default, no-UI workflow; filenames such as `final.mp4`, `reel.mp4`, or repeated camera export names work only once per process lifetime.

**Recommended improvement:** Deduplicate by file identity/generation rather than permanent path membership. Remove a path from `_enqueued` after it disappears, or track `(resolved path, size, mtime/file ID)` and allow a new generation. Only mark it enqueued after a successful callback, or make the callback return success and retry failures. Add regression tests for delete/recreate and callback failure.

### P1-2. Production model loading does not actually request offline-only behavior

**Evidence:** `src/ash_captions/engine/transcribe.py:168-170`, `:190`, `:207-214`; production constructors at `src/ash_captions/app/runner.py:153-158` and `src/ash_captions/web/preview_adapter.py:134-142`.

`WhisperTranscriber` supports `local_files_only=True` and documents that the default `False` contacts Hugging Face and may re-download. Both production construction paths pass `download_root` but omit `local_files_only=True`, leaving it false.

This contradicts the recent commit intent, the bundled-cache design, and the offline product promise. Tests prove the option reaches faster-whisper when explicitly supplied, but do not assert that production wiring supplies it.

**Impact:** Startup/model-load network checks, possible model drift or re-downloads on each workstation, slow failure when offline, and behavior that differs from the documented packaged-cache design.

**Recommended improvement:** Set offline-only behavior in both production constructors when a bundled/managed cache is expected. If source installs should be allowed to download missing models, make that an explicit setting and produce a clear missing-model error in installed builds. Test the actual `build_run_job` and preview wiring.

### P1-3. Web-uploaded client footage is retained indefinitely and the size cap is bypassable

**Evidence:** upload destination at `src/ash_captions/web/app.py:57-60`; upload copy at `src/ash_captions/web/routes_jobs.py:98-142`; header-only limit at `:199-226`; deletion policy at `src/ash_captions/app/runner.py:294-298`; retention scope at `src/ash_captions/app/lifecycle.py:47-81`.

Uploaded files land under `C:\AshCaptions\web_uploads\<uuid>\...`. Successful job cleanup deletes only inputs inside the watch directory, and the retention sweeper scans only the output directory. Nothing removes successful uploads or their UUID folders.

The 2 GiB limit checks only `Content-Length`. It occurs after Starlette has already accepted/spooled the multipart body, and `_copy_upload_to_disk` never stops when the actual byte count exceeds the cap. Missing or dishonest lengths bypass the application-level limit.

**Impact:** Unbounded disk growth containing client footage, inconsistent privacy/retention behavior, and local disk exhaustion. A few 2 GiB uploads are enough to matter on editor machines.

**Recommended improvement:** Treat uploaded inputs as explicitly managed ephemeral assets. Delete them after successful processing, retain failed inputs under a bounded policy, and include the upload root in retention cleanup. Enforce the byte ceiling while streaming/copying and delete the partial file immediately when crossed. Consider rejecting large multipart bodies before parsing through server/request middleware.

### P1-4. Output directories collide by filename stem

**Evidence:** `src/ash_captions/app/adapter.py:89-97`; output filenames at `src/ash_captions/app/runner.py:161-229`, `:282-290`.

Every submitted file maps to `out_dir / file_path.stem`. Files from different directories with the same name, or `clip.mp4` and `clip.mov`, therefore use the same output directory and the same `clip.srt`, `clip.ass`, `clip.txt`, and `clip.captioned.mp4` names.

**Impact:** Later jobs overwrite earlier deliverables; simultaneous or failed/retried jobs can leave a directory containing a misleading mix of outputs. The database preserves distinct jobs while both point to the same artifact location.

**Recommended improvement:** Allocate a stable unique output directory per job, for example `<sanitized-stem>_<job-id>` or `<date>/<stem>_<short-id>`, while keeping the visible filenames editor-friendly. Store the allocated path atomically with job creation. Add tests for same stem across extensions and directories.

### P1-5. Settings accept invalid types and unsafe ranges

**Evidence:** `src/ash_captions/config.py:84-133`, `:157-184`, `:186-192`.

Dataclass type hints are not runtime validation. `Settings.load()` accepts any JSON value for a known key and the dataclass constructor does not reject values such as:

```text
port="not-a-port", retention_days="thirty", device="gpu", punch_zoom=-9
```

This exact payload was loaded successfully during the audit. Invalid `port` then reaches `range()` during startup; invalid numeric/string values fail later in unrelated threads or engine calls. A single malformed field also risks losing all configuration when a later constructor-level error triggers the broad fallback.

`save()` writes directly to the live file rather than temp-file + replace, so interruption can produce truncated JSON; the next launch silently resets every setting to defaults.

**Impact:** Startup crashes, silently disabled retention, nonsensical media parameters, surprising resets, and difficult-to-diagnose manual-configuration failures. The documentation explicitly encourages tuning `settings.json` by hand.

**Recommended improvement:** Add a validated schema with per-field bounds and enums; reject or default individual invalid fields while logging exactly which field was repaired. Validate port range, positive timing values, retention bounds, path types, model/device combinations, and punch mode. Save atomically.

### P1-6. Installer aborts on Task Scheduler access denial after partially installing

**Evidence:** failing test `tests/test_packaging/test_install_ps1.py:88-107`; implementation `installer/install.ps1:212-239`, main sequence `:276-294`.

The audit run failed at `Register-ScheduledTask` with `Access is denied`, contradicting the installer's no-admin guarantee. By that point the bundle has already been mirrored, data directories created, and shortcuts written. `$ErrorActionPreference = 'Stop'` aborts the script, so no fallback is attempted and the user never sees the completion guidance.

The existing task is also always left unchanged. If the installation directory changes or an old task points at a removed executable, reinstall does not repair it.

**Impact:** Partial installations and failed rollout on restricted Windows accounts; app may work manually but not at login, with no friendly recovery path.

**Recommended improvement:** Add a per-user Startup-folder fallback or treat auto-start registration failure as a clearly reported non-fatal result. Update/verify an existing task's action instead of assuming existence means correctness. Add rollback or an explicit partial-success summary. Run the integration test under the same account policy as target editor machines.

### P1-7. Update application is not single-flight and reuses dirty staging

**Evidence:** `src/ash_captions/web/update_adapter.py:92-108`; fixed paths at `src/ash_captions/app/updater.py:392-412`; submit route `src/ash_captions/web/routes_updates.py:58-83`.

`UpdaterAdapter.submit_apply()` accepts unlimited concurrent apply jobs. They download into the same destination and `apply_update()` extracts into the fixed `updates/staged_update` directory, then writes the same `updates/apply_update.ps1` helper. There is no active-job guard.

The staging directory is never cleared before `extractall`. Files present from an earlier update but absent from the new zip remain in staging. The helper then mirrors staging into the installation, so stale files can be installed as though they belonged to the newly verified archive.

**Impact:** Double-clicks/multiple tabs can race downloads, extraction, helper creation, status, and shutdown. Stale staged files undermine the guarantee that the installed tree corresponds exactly to the verified artifact.

**Recommended improvement:** Make update apply process-wide single-flight. Extract each verified artifact to a new unique empty directory, validate the expected single-root layout, and clean it after success/failure. Write a unique helper script. Reject a second request with 409 and return the active apply job.

## P2 — Medium-priority findings

### P2-1. The test suite is red and one test exposes a contract drift

**Evidence:** `tests/test_app/test_runner.py:490`; implementation change documented at `src/ash_captions/engine/punch.py:13-18`, `:212-231`.

The punch-in implementation intentionally migrated from `zoompan` to timestamp-driven `scale,crop` for variable-frame-rate correctness, but the wiring test still asserts that the string contains `zoompan=`. The real ffmpeg punch test passes, so this appears to be a stale assertion rather than a broken renderer. It still leaves CI/release status ambiguous.

The second failure is the installer defect in P1-6.

**Recommended improvement:** Update the stale punch assertion to verify the current behavioral contract (`scale`, `crop`, correct filter ordering, and real ffmpeg output). Do not release from a red suite.

### P2-2. Long-running transcription progress support is implemented but unused

**Evidence:** callbacks supported at `src/ash_captions/engine/transcribe.py:107-134`, `:294-317`; runner calls at `src/ash_captions/app/runner.py:187-203`.

The engine can report seconds completed as segments arrive, but the job runner does not pass `on_progress` for transcription or translation. It reports only stage start/end. A long recording can therefore sit at one percentage for most of its runtime even though the lower layer exposes granular progress.

**Impact:** The control page appears stuck during the longest stage and loses much of the value of SSE progress updates.

**Recommended improvement:** Map engine `(seconds_done, total_seconds)` callbacks into the stage budget. Throttle database/SSE writes to meaningful increments or time intervals.

### P2-3. Glossary processing rereads the file per word and misses the available multi-word path

**Evidence:** runner helpers at `src/ash_captions/app/runner.py:91-116`; language API guidance at `src/ash_captions/languages/__init__.py:127-174`, multi-word helper at `:177-210`.

`_postprocess_words` calls `languages.postprocess(..., glossary_path)` once for every timed word. `postprocess` reloads the glossary when preloaded `entries` are not supplied. Long files therefore perform thousands of redundant reads. The language package already provides `load_glossary_entries` and `postprocess_words`, but the runner does not use them.

The runner's own comment accepts that multi-word glossary phrases will not work in timed captions, even though the language layer now implements a timing-preserving multi-word route.

**Impact:** Avoidable I/O and inconsistent correction: a phrase may be fixed in `.txt` but remain wrong in `.srt`/`.ass`.

**Recommended improvement:** Load glossary entries once per job and reuse them for words, segments, and translation. Use the language layer's sequence helper, mapping corrected token text back onto the existing `Word` objects.

### P2-4. English translation is post-processed using the source dialect rules

**Evidence:** `src/ash_captions/app/runner.py:195-228`.

Translated English words are passed through `_postprocess_words(..., resolved, ...)`, where `resolved` is the source language/dialect. Today this is partly masked because Spanish/French/German presets have no spelling maps, but Portuguese source rules and future dialect glossaries can be applied to English output. The shared client glossary may also be language-specific.

**Recommended improvement:** Resolve a dedicated English output convention for translated captions, and define whether the client glossary applies to source, translation, or both. Add tests with Portuguese and language-specific glossary terms.

### P2-5. Queue history and SSE snapshots scale with the entire database

**Evidence:** unbounded DB query at `src/ash_captions/pipeline/db.py:170-180`; adapter at `src/ash_captions/app/adapter.py:78-80`; route cap applied after loading at `src/ash_captions/web/routes_jobs.py:47-56`; SSE publishing at `src/ash_captions/app/adapter.py:149-168`.

The web route detects whether `queue.list_jobs` accepts `limit`; `QueueAdapter.list_jobs` does not, so every GET and every progress notification reads and converts every historical job, then the route/frame truncates to 100. Database rows are never pruned.

Each subscriber uses an unbounded `asyncio.Queue`, and every state/progress change enqueues a full snapshot. A slow or backgrounded tab can accumulate snapshots indefinitely.

**Impact:** Gradual database, CPU, memory, and serialization growth over months; amplified if fine-grained progress is wired in.

**Recommended improvement:** Add `LIMIT` support to `JobStore` and `QueueAdapter`; keep history retention separate from output retention. Make subscriber queues size 1 and replace/drop stale snapshots because only the latest state matters. Coalesce/throttle progress notifications.

### P2-6. Normal Quit does not actually guarantee the current job finishes

**Evidence:** `src/ash_captions/pipeline/queue.py:97-102`; normal shutdown `src/ash_captions/app/__main__.py:363-368`.

`JobWorker.stop()` says it waits for the current job, but defaults to five seconds and then clears its thread reference even if the daemon thread remains alive. Normal Quit uses that default and the process exits, interrupting longer work. Crash recovery requeues it next launch, but the user receives no explicit “this job will restart” decision in the tray flow.

**Recommended improvement:** On Quit, either refuse/confirm while a job is running, offer “quit after current job,” or implement cooperative cancellation with an explicit state. Never discard the thread handle while it is still alive.

### P2-7. Review video is unavailable for the primary watch-folder path

**Evidence:** review source streaming at `src/ash_captions/web/routes_review.py:55-64`; successful watch-input deletion at `src/ash_captions/app/runner.py:294-298`.

The review route streams the original input, but the default watch workflow deletes that input after success. Completed watch-folder jobs therefore cannot use the “finished job” review video endpoint. Uploaded files work only because of the retention leak in P1-3; by-path files work while the external source remains in place.

**Recommended improvement:** Decide the intended product behavior explicitly: retain a bounded proxy for review, disable review with an honest UI reason for consumed watch inputs, or postpone source deletion until review expiry. Do not rely on accidental upload retention.

### P2-8. “Reset to shipped” creates a permanent user override instead of removing it

**Evidence:** user override precedence at `src/ash_captions/styles/library.py:79-103`; deletion capability at `:160-178`; adapter refusal at `src/ash_captions/web/styles_adapter.py:85-92`; UI reset flow at `src/ash_captions/web/static/style_editor.js:283-295`.

The reset button loads the current shipped definition into the form and asks the user to Save. Saving writes it into the user directory under the built-in name. The style remains flagged “customized locally,” and future improvements to the shipped preset stay shadowed by the frozen copied definition. The lower-level library can remove just the user override, but the adapter refuses deletion for any shipped name.

**Recommended improvement:** Add an explicit reset endpoint that deletes only the user override and reveals the shipped style. Preserve the rule that the shipped file itself can never be deleted.

### P2-9. Supply-chain inputs are mutable and not independently verified

**Evidence:** rolling ffmpeg asset at `scripts/fetch_ffmpeg.py:54-59`, download/run at `:122-133`, `:180-196`, `:243-257`; model fetch defaults in `scripts/fetch_model.py`; unhashed Python pins in `scripts/requirements-build.txt`; unsigned update model documented in `docs/INSTALL.md`.

The ffmpeg fetcher downloads a rolling `latest` binary, checks zip integrity, then executes it. It does not verify a maintainer-provided checksum/signature or pin a release. Model fetching defaults to the mutable upstream revision. Python versions are pinned but have no hashes. This limits reproducibility and makes the build workstation trust three mutable distribution channels.

Application update hash verification detects corruption only relative to the manifest hosted in the same release repository; repository compromise can replace both. This limitation is documented and explicit-click is a useful mitigation, but an editor cannot meaningfully inspect a binary before accepting it.

**Recommended improvement:** Pin ffmpeg release/asset digest, pin model repository revision and record it, generate a hash-locked Python requirements file, and produce an auditable SBOM/build provenance record. If updates remain unsigned, at least restrict artifact URLs to HTTPS and the expected GitHub repository and show release metadata to the user.

### P2-10. Update/release immutability claims conflict with `--clobber`

**Evidence:** `scripts/release.py:42-51`, `:131-136`, while URL is described as immutable at `:85-88`.

Existing release tags are republished with `gh release upload --clobber`, so the supposedly immutable version-tagged artifact can change. This weakens reproducibility, incident investigation, and rollback semantics. The documentation acknowledges the replacement but still calls the URL immutable.

`publish_release` also trusts the old hash in `build-info.json` and does not recompute it immediately before upload, so a changed artifact produces a manifest mismatch rather than being caught before publication.

**Recommended improvement:** Make published version tags immutable; require a version bump for any changed build. Recompute and compare artifact size/hash at publish time.

### P2-11. Current documentation is materially stale and contradictory

**Evidence:** `docs/STATUS.md`, `docs/INSTALL.md`, `PRODUCT.md`, `LICENSE`.

Examples:

- `docs/STATUS.md` says 783 tests pass; the current suite has 1073 collected outcomes and 2 failures.
- It says the source repo is private, while `docs/INSTALL.md` says it became public on 2026-09-02.
- It says the bundled ffmpeg is LGPL/libopenh264 and asks for a GPL decision; current fetch defaults and notices use GPL/libx264.
- It says model/build commands were run and the only unfinished task is publishing, but `build\models` is absent and the documented shippable dry-run fails.
- `PRODUCT.md` calls ASH Captions open-source MIT; `LICENSE` explicitly says proprietary, all rights reserved, and not open source.
- `PRODUCT.md` describes two presets/20+ languages; README describes nine styles/54 languages.

**Impact:** Legal/positioning ambiguity, incorrect handoff instructions, and increased chance of shipping the wrong ffmpeg/model/package.

**Recommended improvement:** Choose canonical product, license, release, and status facts, then update all derivative docs from them. Generate test count/version/build inventory where possible instead of hand-maintaining it.

## P3 — Lower-priority improvements

### P3-1. Version parsing is permissive and prerelease ordering is incorrect

`scripts/pkgtools/manifest.py:44-76` accepts leading digits in every component and ignores the rest, so malformed strings such as `1x.2y` parse. `1.0.0-rc1` compares equal to `1.0.0`. Use `packaging.version.Version` or a strict documented grammar.

### P3-2. Health endpoint does not receive real worker/watcher health

`routes_events.read_health` supports `worker_alive` and `last_watcher_poll`, but production `QueueAdapter` exposes neither. The UI health frame therefore cannot distinguish an alive server from a dead worker/watcher. Wire explicit health state from the assembled components.

### P3-3. Port probing has a bind race

`src/ash_captions/app/__main__.py:60-76` binds and closes a probe socket, then uvicorn binds later in another thread. Another local process can take the port in between. Prefer letting the server bind a reserved socket or handling bind failure with a retry before opening the browser.

### P3-4. Preview job metadata grows for the process lifetime

`InProcessPreviewRenderer` deletes the prior files but retains every `PreviewJob` in `_jobs`. Update-apply jobs are likewise kept forever. Bound completed job metadata or expire it.

### P3-5. Source quick-start and test instructions are inconsistent

README quick-start installs `-e .`, but the test command requires dev dependencies. Recommend `-e ".[dev]"` for contributor setup, while keeping editor setup minimal.

### P3-6. Static analysis and vulnerability scanning are not part of the declared workflow

Ruff, mypy, coverage, and pip-audit are absent from the environment/project configuration. The large test suite is valuable, but the settings type defect and several lifecycle issues are examples that lint/type/property/state-machine checks can expose. Add CI gates appropriate to a Windows-only app and a scheduled dependency scan.

## What is working well

- The process execution layer consistently avoids shell interpolation and contains ffmpeg stderr/progress handling.
- Burn-in writes to `.part` and atomically replaces the final file after ffmpeg succeeds.
- Real ffmpeg tests cover apostrophes in paths, very large punch expressions, output codecs/pixel format, and bundled font resolution; all 27 passed in this audit.
- The single-instance OS lock directly addresses the non-atomic SQLite claim path across processes.
- SQLite uses short-lived per-call connections, WAL, parameterized SQL, and a busy timeout.
- The loopback-only bind is enforced in code, not only documented.
- Browser mutation defenses layer Host validation, Origin checking, and a custom header.
- Style writes are atomic and validate fonts, ranges, colors, effects, reserved Windows names, and slug collisions.
- The updater verifies both artifact size and SHA-256 before extraction and requires explicit user action.
- Input deletion is intentionally restricted to paths resolving under the watch folder, which protects by-path source footage.
- Failure states remain visible in the job database rather than silently dropping jobs.
- Packaging validates important runtime assets and licenses instead of assuming PyInstaller collected them.

## Recommended remediation order

1. Fix watch-path generation/deduplication and callback retry semantics; add regression tests.
2. Enable/verify production offline model loading in both runner and preview paths.
3. Give every job a unique output directory.
4. Add uploaded-input cleanup and enforce the upload limit while copying.
5. Add strict settings validation and atomic persistence.
6. Make installer auto-start setup resilient to access denial and repair stale tasks.
7. Make updater application single-flight with clean unique staging.
8. Restore a fully green suite and make real ffmpeg/font checks part of release CI on the build machine.
9. Wire granular but throttled progress, bounded job/SSE history, and real health signals.
10. Reconcile license/product/status/install documentation before publishing.
11. Pin and verify external build inputs; make published release versions immutable.

## Suggested acceptance checks before release

- Drop `same-name.mp4`, process it, recreate it under the same path, and confirm a second distinct job/output is produced without restart.
- Submit two files with the same stem from different directories and confirm neither output can overwrite the other.
- Disconnect the build/test machine from the network and prove both a normal job and live preview load the bundled model without a network delay.
- Upload a file, complete/fail the job, advance retention, and verify the intended input cleanup policy on disk.
- Attempt a chunked or wrong-`Content-Length` upload over the limit and verify disk usage is bounded.
- Load malformed-but-valid `settings.json` values and verify startup repairs/reports individual fields rather than crashing.
- Run the installer as a standard restricted user with Task Scheduler registration denied and confirm a usable, clearly reported result.
- Double-submit Update in separate tabs and confirm only one apply job exists.
- Seed stale files in an old update staging directory and verify they cannot reach the installed tree.
- Run the full suite green, then run the 27 real ffmpeg/font tests, build a model-containing bundle, install it in a clean Windows user profile, process real footage, apply an update, and inspect the final bundle inventory/licenses.

## Final assessment

The architecture is better than the raw failure count suggests: much of the difficult media, Windows, packaging, and browser boundary work is deliberate and well tested. The remaining risks are primarily **state lifecycle** problems—when a path is considered finished, when large client files are deleted, how outputs are named, how updates are serialized, and how configuration survives bad input. Those are exactly the kinds of defects that unit-heavy suites often miss and that show up during repeated daily use.

Addressing the P1 items should materially improve reliability without requiring a redesign. After that, the project is a good candidate for a clean-machine release rehearsal rather than additional feature work.
