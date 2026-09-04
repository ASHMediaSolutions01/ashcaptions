# ASH Captions v0.4 — release-readiness scan

Date: 2026-09-04. Scanned: `C:\Users\mbila\Desktop\ASH Captions`, master at 5229be3 (tag v0.4.0 = 727a14e, three commits behind HEAD; HEAD moved once during the scan, see F-01). Built bundle `dist\AshCaptions` (v0.4.0, 1022 MB, zip sha256 3d375f75…), installed copies `ashinst\install`, `ashinst2\install`. Read-only on the repo; scratch under `C:\Users\mbila\AppData\Local\Temp\ashscan\`.

## Executive verdict

**Ready with fixes.** Two things will bite the editors in week one and should ship as 0.4.1 before the rollout: raw phone-shot portrait footage (the reels case) gets captions at roughly twice the designed size and fails outright with "captions behind the speaker" (F-04), and the published exe is a console build, so every editor gets a black window at every logon and can kill an hour-long job by closing it (F-05). Everything the 2026-09-02 audit flagged at P1 is closed and holds up in the real bundle: the app came up in 1.1 s, ran a matte+burn reel and a translated Spanish interview end to end, kept peak memory at 1.6 GB, and the security, retention, upload and crash-recovery paths behave as documented; the remaining items are a licence-notice gap that matters because the binary is on a public repo (F-06), an update flow that has never been run for real (F-07), and a handful of medium/low robustness and documentation issues.

## Ranked findings

| id | Sev | Area | Summary | Evidence | Fix | Status |
|---|---|---|---|---|---|---|
| F-04 | **Blocker** | Output / behind-speaker | Rotation tag ignored: phone-shot portrait video gets landscape PlayRes (captions ~1.8x too big) and the behind-the-speaker burn fails | `engine/probe.py:96-129`; repro `ashscan/rot/rot_test.py`: `probe_video` -> 1920x1080 for a 1080x1920 decode; matte burn exit -22 "Nothing was written"; `ashscan/rot/compare.png` | read `stream_side_data=rotation` in `probe_video`, swap w/h for 90/270; regression test with `-display_rotation 90` | VERIFIED |
| F-05 | **High** | Install / first run | Shipped exe is a console-subsystem build; black window at every logon, closing it kills the running job | PE subsystem = 3 in `dist\AshCaptions\AshCaptions.exe` and both installed copies; `scripts/build.py:279` default `console=True`; contradicts `app/tray.py:4-7`, `app/__main__.py:9-12`, INSTALL.md | build with `--windowed`, re-rehearse, republish 0.4.1 | VERIFIED |
| F-06 | **High** (does not block internal testing) | Licences | Bundle redistributes PyAV's FFmpeg with GPL libx264/libx265 in-process; NOTICES.md wrong about PyAV, pystray (LGPL-3.0), certifi (MPL-2.0); only 8 packages ship licence metadata; RVM GPL-3 text not shipped | `dist\AshCaptions\av.libs\libx264-165-*.dll`, `libx265-*.dll`; venv `pystray-0.19.5 License: LGPLv3`; `ls dist\AshCaptions\*.dist-info` = 8 | rebuild `av` LGPL-only or drop PyAV (feed faster-whisper the WAV via numpy); ship generated licence texts; fix NOTICES; add GPL-3 text for RVM | VERIFIED |
| F-07 | Medium | Update flow | In-app update (download -> helper -> restart) has never run end to end: the helper spawn is a mocked seam in tests, STATUS lists "the update banner in real use" open; staged tree (1 GB) + each downloaded zip stay in `C:\AshCaptions\updates` forever | `app/updater.py:286-291` ("cannot be safely exercised by an automated test"), `:416-438`; no cleanup of `updates\` anywhere in `src/` | publish a throwaway 0.4.1 to a test manifest URL on one machine before the fleet; delete `staged_update` and old zips after a successful apply | VERIFIED (never exercised) |
| F-02 | Medium | Robustness | `sweep_tmp_dir` deletes every entry of `settings.tmp_dir` at startup with no ownership or drive-root guard; `tmp_dir` is a free-form hand-edited setting | `app/lifecycle.py:199-220`, `config.py:47,342` | refuse unless under `data_root()` or entries match `job-<n>`; reuse `_is_drive_root` | VERIFIED by code |
| F-03 | Medium | Behind-speaker | Matte composite pairs frames by timestamp; a source whose first video pts is not 0 (MXF/MTS/some camera MOV) shifts the mask and the punch by that offset | `engine/matte.py:281-287` (`[0:v]fps` keeps source pts; `[1:v]` starts at 0) | `setpts=PTS-STARTPTS` on both inputs; real-ffmpeg test with `-output_ts_offset 0.5` | SUSPECTED |
| F-08 | Low | Arabic | Arabic through a shipped look renders shaped and RTL via libass fallback (plain .srt fine); karaoke fill sweeps the wrong way; no look uses the bundled Noto Naskh; UI says nothing either way | `ashscan/ar/ar_compare.png`; `catalog.py:110` marks `ar` RTL, nothing consumes `ScriptDirection` | leave as documented v2; add one line in the guide ("Arabic: use the .srt or CLEAN/POP; karaoke looks are wrong") | VERIFIED render, transcription NOT tested |
| F-09 | Low | Docs vs code | README "9 caption looks"; PRODUCT.md (nine looks, "tested target up to 30-minute videos", `--burn-proof`, `captions\in\` on a shared drive); STATUS v0.3 says the style editor does not expose align/card_box while v0.4 says it does; STATUS 1314 vs 1315 tests; EDITOR-GUIDE "the .srt and .ass are rewritten each time you pick a look" (restyle rewrites only `.ass`, `adapter.py:192-195`); "the job restarts from the beginning" (transcript is reused now); INSTALL "Two folders" (installer makes three) | grep, `app/adapter.py:172-201` | one doc pass; generate the test count | VERIFIED |
| F-01 | Low (closed at HEAD) | Docs | Form-feed byte in the fonts command in README/EDITOR-GUIDE at tag v0.4.0 (`scripts\fetch_fonts.py` rendered as `scriptsetch_fonts.py`); fixed in 5229be3 during this scan | `od -c` on 727a14e | move the tag or tell source-install editors to use `git pull` first | VERIFIED, fixed |
| F-10 | Low | Robustness | Two Studio tabs restyling the same job race on `<stem>.ass.part`; Windows rename with an open handle -> PermissionError -> 500 in one tab | `app/runner_util.py:191-199`, `routes_studio.py:110-119` (threadpool, no per-job lock) | per-job lock around restyle, or unique temp name | SUSPECTED |
| F-11 | Low | Watch folder | `in\<Client>\` folder names that fail `sanitize_client_name` (trailing dot, >60 chars) silently run with the shared glossary only, no log line | `app/runner_util.py:164-181` | log at WARNING | VERIFIED by code |
| F-12 | Low | Inputs | `.wmv` accepted by the watcher, rejected by the control page | `pipeline/watcher.py:59`, `web/models.py:115` | align the two lists | VERIFIED |
| F-13 | Low | Code rules | `app/runner.py` is 524 lines (project rule: 500) | `wc -l` | split `_burn` out | VERIFIED |
| F-14 | Low | Public repo | 244 ruflo/claude tooling files (`.claude/`, `.agents/`, `CLAUDE.md`, `.mcp.json`) are tracked in the public source repo; nothing private in them, but they are not the product | `git ls-files` | untrack or move to a private branch | VERIFIED |
| F-15 | Low | Previous-audit leftovers | Port probe/bind race (P3-3), preview and update job dicts grow for the process lifetime (P3-4), SSE subscriber queue unbounded but throttled to 1 frame/s (P2-5 partial), ffmpeg fetch is rolling `latest` and requirements are not hash-locked (P2-9, documented as deliberate), `--clobber` republish (P2-10, documented) | see closure table | as the audit suggested; none blocks rollout | VERIFIED |

## Details

### F-04 Rotated footage (Blocker)
`probe_video` requests `stream=codec_type,codec_name,width,height,avg_frame_rate` and nothing about rotation. Phones store portrait video as 1920x1080 with a 90-degree display matrix; ffmpeg auto-rotates on decode to 1080x1920. Reproduced with the bundled ffmpeg (`-display_rotation 90 -i in.mp4 -c copy rot90b.mp4`; ffprobe shows `rotation: 90`, `showinfo` shows the decoded frame as 1080x1920, `probe_video` returns `VideoInfo(width=1920, height=1080)`). Through the repo code: the `.ass` header becomes `PlayResX: 1920 / PlayResY: 1080`; libass scales by PlayResY so the POP caption in `ashscan/rot/compare.png` (left) is about twice the size of the same caption with the correct PlayRes (right), which is the exact "56% size" class of bug STATUS says was fixed for landscape. With `behind_speaker`, `working_size(1920,1080)` gives an 854x480 matte and `composite_filtergraph` scales it to 1920x1080 against a 1080x1920 frame; `alphamerge` refuses and ffmpeg exits with "Could not open encoder before EOF … Nothing was written into output file", so the job FAILS with an ffmpeg stderr tail as its error. The rehearsal reel passed because it was an editor export (rotation baked in); raw phone reels from clients are the common case for the very feature this bug breaks. The Studio preview uses the same `play_res` from the transcript, so the browser preview lies the same way. Fix is small: add `stream_side_data=rotation` (and the legacy `tags=rotate`) to the probe and swap width/height when the rotation is ±90/270; `build_punch_filter` already derives sizes from the stream and needs no change.

### F-05 Console build (High)
`build.py` defaults to `--console` and INSTALL.md describes `--windowed` as a future option, yet three docstrings and INSTALL's editor section describe a windowless tray app. Both installed copies have PE subsystem 3. The logon task launches the exe with no arguments at every login, so six editors see a black `AshCaptions.exe` window with four uvicorn lines in it every morning; closing it (or logging off with it open) ends the process; the job is requeued from `pending` on the next launch, but the elapsed time is lost and `EDITOR-GUIDE` Part 8's "ASH Captions is already running" story assumes only a tray. The tray already owns "Open log file" and all logging goes to the rotating file, so the stated precondition for `--windowed` is met. A windowed build must still show the message boxes `main()` uses on fatal startup errors, which it does (ctypes MessageBoxW).

### F-06 Licences (High for a public release)
`NOTICES.md` argues that the app is not linked against ffmpeg. That is true for `bin\ffmpeg.exe` but not for `av.libs\`, the FFmpeg that the PyAV wheel bundles and that faster-whisper imports into the process to decode audio (`decode_audio`). That FFmpeg is built with libx264 and libx265 (GPL-2.0-or-later); NOTICES lists PyAV under "MIT, BSD, Apache 2.0 or HPND". The artifact is published from a public GitHub releases repo under an all-rights-reserved licence, which is public distribution of a proprietary program dynamically linking GPL code. Separately, `pystray` is LGPL-3.0 and `certifi` is MPL-2.0; neither text ships, because PyInstaller only kept `*.dist-info` for eight packages, so NOTICES' sentence about "licence texts … in the bundle's package metadata directories" is false for the majority of dependencies. The RVM weights are GPL-3.0 and NOTICES points at the upstream repo instead of shipping the text. Concrete path: (1) exclude PyAV from the bundle and pass faster-whisper a numpy array decoded from the WAV the pipeline already extracts (or install an LGPL-built `av`); (2) generate `licenses/` with `pip-licenses --with-license-file` at build time and have `build.py` validate it; (3) rewrite NOTICES from that inventory.

### F-07 Update flow (Medium)
The chain is well designed (single-flight, sha256+size verification, wait-until-idle, detached helper breaking out of the job object, robocopy /MIR, relaunch) and unit-tested up to the spawn seam, but `updater.py` itself says the OS handoff "cannot be safely exercised by an automated test" and no rehearsal log shows a real apply. The first real run will be on the editors' machines. Also, after a successful apply nothing removes `C:\AshCaptions\updates\staged_update` (a full 1 GB copy of the bundle) or the downloaded zips (690 MB each, one per version), so disk grows by ~1.7 GB per update. A dry run against a second manifest URL on one machine (the exe has no flag to override `MANIFEST_URL`; a hosts-file or a temporary 0.4.1 release would do) is worth an hour before rollout.

### F-02 tmp_dir sweep (Medium)
`clean_old_outputs` refuses drive roots and only deletes marked folders; `sweep_tmp_dir` has neither guard and runs before any UI at every start. A single mistyped path in `settings.json` (which the guide tells editors to hand-edit) empties a folder. Low likelihood, unrecoverable.

### F-03 Matte timestamp offset (Medium, suspected)
`render_matte` decodes to rawvideo (pts restart at 0) and encodes a matte whose first frame is t=0; `composite_filtergraph` applies `fps=` to `[0:v]` keeping the source's own timestamps. `alphamerge`/`overlay` synchronise by timestamp, so a source with `start_time` 0.5 s pairs frame N of the video with frame N+15 of the matte, and the punch envelope (t-based) shifts the same way. MP4s from Premiere/CapCut/phones start at 0 and are unaffected; MXF, AVCHD .MTS and some trimmed streams do not. `setpts=PTS-STARTPTS` on both inputs before `fps=` is a no-op for the common case.

### F-08 Arabic
No Arabic audio was available to test transcription. Rendering was tested: five Arabic words through POP (one boxed word at a time) and KARAOKE (full line) burn fine, correctly shaped and ordered right-to-left, using libass's glyph fallback since neither look names Noto Naskh. The karaoke fill sweeps left to right, which is backwards for RTL. `str.upper()` on Arabic is a no-op. The control page lists Arabic (Strong band) with no caveat; the guide does not mention Arabic. Acceptable for "plain .srt works", and the UI does not promise styled RTL; one sentence in the guide would prevent a support call.

## Previous audit (2026-09-02) closure check

| Item | Status | Evidence |
|---|---|---|
| P1-1 watch path never forgotten | Closed | `watcher.py:345-353` `_enqueued.intersection_update(seen)`; `tests/test_pipeline/test_watcher.py` |
| P1-2 offline model loading | Closed | `runner.py:204` `local_files_only = (app_root()/"models").is_dir()`; bundle ships `models\` with the HF cache layout; live run loaded it with no network log line |
| P1-3 uploads retained / cap bypassable | Closed | `routes_jobs.py:224-247` streaming cap + rmtree; `runner.py:349-364` deletes consumed uploads; `lifecycle.clean_old_uploads` (1 day) |
| P1-4 output dir collision | Closed | `adapter.unique_output_dir` checks disk and DB; `out\entrevista-es (2)` exists in ashlive |
| P1-5 settings validation | Closed | `config.py:262-363` per-field validators, atomic save, BOM-tolerant load |
| P1-6 installer aborts on Task Scheduler denial | Closed | `install.ps1:214-280` repair + Startup-folder fallback; test suite runs the real script |
| P1-7 update apply not single-flight, dirty staging | Closed | `update_adapter.py:380-389`; `updater.py:419-424` rmtree staging |
| P2-1 red suite | Closed | 1315 passed / 28 skipped; 1343 passed with real ffmpeg |
| P2-2 transcription progress unused | Closed | `runner.py:380-383` `on_progress`; live SSE showed per-batch progress |
| P2-3 glossary re-read per word | Closed | `load_glossary_entries_for` once per job; `postprocess_words` multi-word path |
| P2-4 translation post-processed with source dialect | Closed | `runner.py:305-307` `languages.resolve("en")` |
| P2-5 unbounded history / SSE | Mostly closed | `db.list_jobs(limit)`, adapter limit 200, route cap 100, 1 s publish throttle; subscriber queue still unbounded |
| P2-6 Quit does not finish the job | Accepted design | cooperative cancel + requeue; 30 s wait; documented |
| P2-7 review video for watch-folder jobs | Partial | Studio falls back to the burned output; unburned watch-folder jobs show "Video not available" (roadmap v0.5) |
| P2-8 Reset creates an override | Closed | `POST /api/styles/{name}/reset` deletes the override |
| P2-9 mutable supply chain | Open, documented | STATUS "Known limitations" |
| P2-10 `--clobber` | Partial | `release.py:81-96` recomputes hash before upload; clobber kept, documented |
| P2-11 stale docs | Mostly closed | LICENSE/PRODUCT positioning fixed; leftovers in F-09 |
| P3-1 version parsing | Open | `manifest.py:44-76` still truncates pre-release; harmless while versions are plain |
| P3-2 health not wired | Closed | `adapter.attach_health`; live `/api/health` shows `worker_alive` |
| P3-3 port bind race | Open | `__main__.py:81-97` |
| P3-4 preview/update job dicts grow | Open | `preview_adapter.py:177`, `update_adapter.py:378` |
| P3-5 README dev install | Closed | README tests line uses `-e ".[dev]"` |
| P3-6 no lint/CI | Open | no ruff/mypy config |

## What I verified is solid (with evidence)

- **Test suites green.** `pytest tests -q`: 1315 passed, 28 skipped, 60 s. With `ASH_REAL_FFMPEG=<repo>\bin\ffmpeg.exe`: 1343 passed, 60 s, including `tests/test_styles/test_fontselect_real.py` (all 24 bundled faces resolve in real libass). `python -m compileall src` clean; `pip check` clean.
- **Real bundle runs standalone.** `dist\AshCaptions\AshCaptions.exe` with `ASH_CAPTIONS_ROOT` pointed at a copy of ashlive, port 8798: health in 1.1 s; reel with burn + behind-speaker + client "Acme" done in 33 s (matte stage 20.3 s for a 20 s clip); Spanish interview (4:48) with `es-MX`, translate and burn done in 105 s; a style preview submitted mid-job completed in 27 s on the shared model; outputs 17 MB and 126 MB; `tmp\` peaked at 9 MB and was empty afterwards. Peak RSS 1642 MB (transcribe), 1355 MB (matte), 1370 MB (burn), idle 444-571 MB after model load, 89 MB before.
- **Security layer.** `security.py` rejects every non-GET/HEAD/OPTIONS request without `X-ASH-Client: 1` or with a foreign Origin, so the newest mutating routes (`PUT /api/clients/*/glossary`, `POST /api/jobs/*/restyle|burn`) are covered by construction; `TrustedHostMiddleware` on 127.0.0.1/localhost; `run_server` refuses any other bind host. File routes: `/api/fonts/file/{name}` is a manifest lookup, never a path join; srt/ass/output/files derive from the job row's `output_dir`; glossary names go through `sanitize_client_name` and `client_glossary_path` (belt and braces); upload filenames are basenamed. No client transcript text is logged; input paths are logged (in-house, acceptable).
- **Path handling.** Filenames never enter the filtergraph (`captions.ass` + `filter.txt` in a private cwd); apostrophe/comma filename verified in the rehearsal and again in this run; `_escape_path_for_filtergraph` tested with `O'Brien`.
- **Timing past one hour.** Synthetic 15,000-word / 121-minute transcript: SRT last cue `02:01:16,908 --> 02:01:19,930`; ASS `H:MM:SS.cc` unbounded; restyle server-side 58-268 ms for 15k events (POP 2.0 MB, NEWS 3.2 MB `.ass`).
- **PlayRes on landscape/portrait/4K** for unrotated sources: probed once per job and passed to `write_ass`, the transcript and the Studio (`runner.py:257,320`; es-result shows `PlayResX: 1920`).
- **36 looks.** All validate, all fonts in the manifest, all 24 `.ttf` present; alignment variants map to numpad 1-9 (`ass_alignment`), `card_box` boxes the whole caption via BorderStyle 3 on the base style vs `box` on the companion style.
- **Translate pass** runs with `initial_prompt=None` (`runner.py:290-293`), confirmed by `translate-exp.txt` (0 Spanish segments in the English output) and by the live run's `.en.srt`.
- **Glossary precedence** client over shared on the same match text (`merge_glossary_entries`); tests in `tests/test_languages/test_glossary.py`.
- **Watch folder.** Three-tick stability, exclusive-open / read-only share probe, one level of client subfolders, restart seeding from the DB, path forgotten when the file leaves the folder; `in\<Client>\` verified in the rehearsal.
- **Crash recovery and Quit.** Job object kills ffmpeg on any death (rehearsal: ffmpeg gone 4 s after a hard kill), `reset_stale_running` requeues, cooperative cancel via `should_stop` in transcribe/matte/burn, `.part` + rename for every output.
- **Retention.** Only folders with `.ash-captions-job` markers, never live jobs, never a drive root; uploads swept after a day.
- **Installer.** `Install-AshCaptions.bat` -> `install.ps1`: manifest download, sha256 check, robocopy /MIR, shortcuts, per-user logon task with repair and Startup-folder fallback; rehearsal log `install-rehearsal-040.txt` shows the published 0.4.0 installing and running a job. The bundle needs no Python on the machine (PyInstaller onedir; `pkgtools` is bundled so the update check works in the frozen build).
- **Disk-full behaviour.** Burn refused up front by `check_free_space` (max(1.2x input, 5 GB)); write-stage OSErrors become the job's error; log handler and DB errors are caught by the worker loop with backoff and `worker_last_error`.
- **Public repo.** No client media, secrets or local paths in tracked files or history (74 commits, only the vendored JASSUB COPYRIGHT contains e-mail addresses); guide screenshots show the Creative Commons interview and text-only frames.

## Not verified (and why)

- Browser-side Studio behaviour (JASSUB memory with a 15k-event `.ass`, a 90-minute source through the Range route, seeking): no browser tool was available in this session. Server side is covered (Range tests in `tests/test_web/test_review.py`, restyle timing above).
- A real 60-90 minute file, SmartScreen on a fresh machine, a machine that never had Python, UNC paths, OneDrive placeholders, and the real update apply: not exercised here. `validate_local_path` opens the file in a threadpool, so a placeholder hydrates or fails with a plain "Can't read" 400; UNC works through `Path` and `resolve()`.
- Arabic transcription quality (no Arabic audio).

## Commands run and results

```
python -m pytest tests -q -p no:cacheprovider                     -> 1315 passed, 28 skipped (60 s)
ASH_REAL_FFMPEG=<repo>\bin\ffmpeg.exe python -m pytest tests -q   -> 1343 passed (60 s)
python -m compileall -q src                                       -> OK
python -m pip check                                               -> No broken requirements found
ffmpeg -display_rotation 90 -i rot90.mp4 -c copy rot90b.mp4; ffprobe ... stream_side_data=rotation -> rotation: 90, width 1920, height 1080
python ashscan/rot/rot_test.py     -> probe 1920x1080; plain burn OK (wrong PlayRes); behind-speaker burn FAILED exit 4294967274
python ashscan/live_run.py         -> bundle up 1.1 s; job A done 33 s; preview 27 s; job B done 105 s; peak RSS table above
PE header check                    -> subsystem 3 (console) for dist and installed exe
ls dist\AshCaptions\av.libs        -> libx264-165-*.dll, libx265-*.dll present
grep License site-packages/*.dist-info/METADATA -> pystray LGPLv3, certifi MPL-2.0, tqdm MPL-2.0 AND MIT
15k-word restyle script            -> POP 131 ms / 2.0 MB, KARAOKE 58 ms, NEWS 268 ms / 3.2 MB; last SRT cue 02:01:16,908
Arabic render script               -> POP and KARAOKE burn OK, RTL shaped (ashscan/ar/ar_compare.png)
git status --porcelain (end)       -> see below
```

## Repository state at the end of the scan

Untracked, not created by this scan's commands (all zero bytes; other agents are active in the same checkout): `0` (15:26), `{key}` (15:27), `` PreviewRenderer` `` (15:18), and the pre-existing empty directory `-p` (2026-09-01). The `1` file present at the start had already been removed. Nothing was written into the repo by this scan; all scratch is under `C:\Users\mbila\AppData\Local\Temp\ashscan\` (`rot\`, `ar\`, `big\`, `live\`, logs and CSV). `.playwright-mcp\` is gitignored and was not touched.

---

## Raw findings log (appended during the scan, kept for traceability)

### F-01 [VERIFIED] Source-install fonts command in README.md and docs/EDITOR-GUIDE.md contains a literal form-feed byte
- `grep -c $'\f' README.md docs/EDITOR-GUIDE.md` -> 1 each at 727a14e; `od -c` shows `s c r i p t s \f e t c h _ f o n t s . p y`. Rendered as `scriptsetch_fonts.py`. The in-app `guide.html` is correct. Fixed in commit 5229be3 at 15:18 today, after this scan started; the tagged v0.4.0 docs still carry it.

### Test runs (VERIFIED)
- plain: 1315 passed, 28 skipped in 60.39 s; real ffmpeg: 1343 passed in 59.57 s.

### F-02, F-03, F-04, F-05, F-06 and the performance table: see the sections above (moved from the log into the structured report).
