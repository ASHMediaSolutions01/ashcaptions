# Installing ASH Captions

Two audiences: **editors** (below) get three steps and nothing else.
**Ghazi** (further down) builds, ships and maintains the thing they're
clicking.

---

## For editors

1. Get `Install-AshCaptions.bat` and `install.ps1` from Ghazi (same folder,
   USB stick, network share -- however you were pointed here) and
   double-click `Install-AshCaptions.bat`.
2. Windows will probably show a blue **"Windows protected your PC"** box the
   first time. That's expected -- this is our own internal tool, not
   something from the Windows Store, so it isn't signed by Microsoft. Click
   **More info**, then **Run anyway**.
3. Wait for the black window to say **"You're all set!"**, then press any
   key to close it.

That's it. You now have:

- An **ASH Captions** icon on your Desktop and in the Start Menu.
- Two folders: `C:\AshCaptions\in` and `C:\AshCaptions\out`.
- The app running quietly in your system tray, and starting automatically
  every time you log in.

**The 80% path:** drop a video into `C:\AshCaptions\in`. A few minutes
later, your captions show up in `C:\AshCaptions\out\<video name>\` -- an
`.srt` you can drag into Premiere or Resolve, a styled `.ass`, a plain-text
transcript, and (if you turned it on) a burned-in `.mp4`.

**The 20% path:** double-click the **ASH Captions** desktop icon to open the
control page in your browser. Pick a language, dialect, style preset or
burn-in, watch the queue, or retry a job that failed.

**Something went wrong?** Right-click the tray icon and choose "Open Logs".
Send Ghazi that file -- not a screenshot of an error box, the actual file --
it has the detail a screenshot doesn't.

You will see the SmartScreen warning again if a fresh update install
happens to trip it. That's normal too; there's nothing wrong with your
computer.

---

## For Ghazi

### One-time setup

The build box runs **Python 3.14** (the interpreter the shipped bundle was
verified with). Install the pinned build environment, not just the loose
lower bounds in `pyproject.toml`, so a rebuild in six months gets the same
faster-whisper / ctranslate2 / PyInstaller that was tested:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r scripts\requirements-build.txt -e ".[dev]"
gh auth login          # once -- release.py relies on gh's own stored auth
```

Source runs must be **editable** (`-e`): `styles/` and `assets/` live at the
repo root and `config.app_root()` resolves relative to the package, so a
non-editable `pip install .` finds no styles and no fonts.

### 1. Fetch ffmpeg (once, or whenever you want to refresh it)

```powershell
.venv\Scripts\python.exe scripts\fetch_ffmpeg.py
```

Downloads BtbN's static **GPL** Windows build (`ffmpeg.exe`, `ffprobe.exe`
and the build's `LICENSE.txt` -- see the licensing note in
`scripts/fetch_ffmpeg.py`'s docstring for why GPL, which is what gets us
`libx264`), verifies each binary actually runs, and writes
`bin\ffmpeg-build-info.txt` recording the exact version banner of what you
shipped -- keep that file, it's the answer to "which ffmpeg build was in the
version we sent out in March" six months from now. `build.py` refuses to
bundle a `bin\` without `LICENSE.txt`: we redistribute a GPL binary, so its
licence text ships beside it, along with the repo's `LICENSE` and
`NOTICES.md`.

### 2. Pre-seed the Whisper model (once per model size you ship)

```powershell
.venv\Scripts\python.exe scripts\fetch_model.py --model-size small
```

Downloads the CTranslate2-converted model into `build\models\` laid out as
a **Hugging Face cache root** (`models--Systran--faster-whisper-small/
snapshots/<sha>/...`, symlinks materialised so PyInstaller and robocopy keep
the files), and writes `model-info-small.txt` in that root recording the
**real** size on disk. That layout is what faster-whisper resolves offline;
the earlier flat `models\small\` layout was silently ignored at run time and
every editor re-downloaded the model on first job and after every update.
`tests/test_packaging/test_fetch_model.py` proves the bundled layout resolves
with the network off. Six editors pulling several GB each over the office
connection is the thing this avoids; see design spec section 11.3.

All sizes share the same root; repeat with `--model-size large-v3` for a GPU
build, if one ever ships.

For a **source run** (an editor following the guide), the same script with
`--dest models` puts the cache at `<repo>\models\`, which the app prefers
over `C:\AshCaptions\models` when it exists.

### 2b. Pre-seed the person-matting model (once)

```powershell
.venv\Scripts\python.exe scripts\fetch_matte_model.py
```

Puts the 15 MB Robust Video Matting ONNX file into the same `build\models\`
root, so "captions behind the speaker" works offline on the installed
bundle. A source checkout downloads it on first use instead.

### 3. Build the bundle

```powershell
.venv\Scripts\python.exe scripts\build.py --model-dir build\models
```

`build.py` validates the model directory is a real HF cache root (it refuses
the flat layout and unresolved symlinks) and warns when `--model-dir` is
omitted, since that ships a bundle that downloads ~480 MB per machine.

Produces a PyInstaller **onedir** bundle (not `onefile` -- see `build.py`'s
docstring) at `dist\AshCaptions\`, zips it to
`dist\AshCaptions-<version>-win64.zip`, and writes `dist\build-info.json`
(version, build date, sha256, size -- release.py's input, not yet a
published manifest since it has no download URL until it's uploaded).

Useful flags:

- `--dry-run` -- print the PyInstaller command without running it. Doesn't
  need PyInstaller installed, ffmpeg fetched, or `__main__.py` to exist yet.
- `--skip-ffmpeg` -- build without ffmpeg, for local iteration only. **Not
  shippable** -- every job will fail without it.
- `--windowed` -- drop the console window, once the tray app owns its own
  logging (spec section 12: errors must stay reachable from the tray menu
  either way).

The build **fails loudly** if `src/ash_captions/web/static/` is missing or
incomplete, rather than shipping a control page that silently 404s -- this
was the single failure mode called out by name in the packaging brief.

### 4. Publish

```powershell
.venv\Scripts\python.exe scripts\release.py
```

`--repo` defaults to `ASHMediaSolutions01/ashcaptions-releases` (the real,
current public artifacts repo) so this needs no flags for the normal case;
pass `--repo <owner>/<name>` to override if the repo is ever renamed or
moved.

Uploads the zip and a signed-looking (hash-verified, not cryptographically
signed -- see "Known gaps" below) `manifest.json` as GitHub Release assets
on the tag `v<version>`, to that **public** repo. Re-running against a
version you already published re-uploads with `--clobber` instead of
failing on "tag already exists".

Never pass a token to this script or set one in its environment for it to
read -- it shells out to `gh`, which uses its own stored login
(`gh auth login`, done once). See "Two-repo model" below for why the
releases repo needs no auth at all downstream.

### 5. Enable GPU on a specific machine (optional, per-machine, after CPU install)

```powershell
scripts\enable_gpu.ps1
```

Run this **on the editor's machine**, after the standard CPU install from
`installer\install.ps1` is already working. It:

1. Reports the detected driver's `nvidia-smi` "CUDA Version".
2. States plainly whether that satisfies ctranslate2 **4.8.2**'s requirement
   (cuDNN 9 + CUDA 12) -- and **refuses**, with the reason printed, if it
   doesn't.
3. Checks that the cuBLAS 12 / cuDNN 9 DLL set ctranslate2 loads at run time
   (the list is `scripts/pkgtools/gpu_matrix.py::REQUIRED_CUDA_DLLS`) is
   present beside `AshCaptions.exe`, in its `ctranslate2\` folder, or on
   PATH -- and **refuses** if any are missing. The CPU bundle does not ship
   them, so today the script refuses on every machine, by design: flipping
   `device=cuda` without them made every job fail at model load.
4. Only on success, flips `C:\AshCaptions\settings.json` to `device=cuda`,
   `model_size=large-v3`.

`-CheckOnly` reports the decision as JSON (including which DLLs are missing)
without changing anything. Shipping GPU support means a build variant that
copies the `bin\` folders of the `nvidia-cublas-cu12` and `nvidia-cudnn-cu12`
wheels beside the exe; that variant does not exist yet. As a safety net, the
engine falls back to CPU (with a logged warning) if a `cuda` model fails to
load.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Control page shows a blank page / 404 | Bundle was built with a stale or missing `web/static/` -- rebuild; `scripts/build.py` should have refused to produce this bundle in the first place. |
| `cudnn_ops64_9.dll is not found` / jobs fall back to CPU after GPU opt-in | The CUDA DLLs are not beside the exe. `enable_gpu.ps1` refuses in that state; a hand-edited `settings.json` saying `cuda` gets the CPU fallback instead. Re-run the script and follow what it says. |
| Six "Windows protected your PC" messages on rollout day | Expected -- the exe is unsigned (spec section 11.4/15). Brief the team ahead of time; code signing is only worth it if this becomes a real recurring complaint, not pre-emptively. |
| `gh` publish fails with an auth error | `gh auth login` on the build machine, not a token in a file -- this repo's scripts never read or write one. |
| Install works but nothing starts at logon | Check `Get-ScheduledTask -TaskName AshCaptionsTray` -- if `install.ps1` was run under a different Windows user than the one who logs in day-to-day, the (per-user) task is registered for the wrong account. |

---

## Release manifest schema

This is the contract between `scripts/release.py` (publishes it) and the
in-app updater (owned elsewhere -- see `src/ash_captions/`, not this
package's scope; the field names below are the ones it should read).
Implemented and validated in `scripts/pkgtools/manifest.py`.

The **stable URL** an updater should poll is tag-independent, always the
newest publish:

```
https://github.com/ASHMediaSolutions01/ashcaptions-releases/releases/latest/download/manifest.json
```

That file's `artifact.url` then points at the immutable, version-tagged
asset for that specific release -- the updater never needs to enumerate
releases or parse tags itself, and the download URL it acts on is always
exactly the one this manifest said, not a guess.

```jsonc
{
  "schema_version": 1,
  "channel": "stable",
  "version": "0.4.0",
  "build_date": "2026-08-29T14:32:00+00:00",
  "artifact": {
    "filename": "AshCaptions-0.4.0-win64.zip",
    "url": "https://github.com/ASHMediaSolutions01/ashcaptions-releases/releases/download/v0.4.0/AshCaptions-0.4.0-win64.zip",
    "sha256": "<64 hex chars>",
    "size_bytes": 1234567890
  },
  "min_supported_version": "0.1.0",   // optional
  "notes": "human-readable changelog line"  // optional
}
```

**Consumption contract for the app-side updater:**

1. On launch (and/or on whatever interval the app decides -- outside this
   doc's scope), `GET` the stable manifest URL above. This is a **check**,
   nothing more.
2. Reject the manifest if `schema_version` isn't one you understand
   (`pkgtools.manifest.validate_manifest` / `read_manifest` do this).
3. Compare `manifest["version"]` against the running version with the same
   numeric-tuple comparison `pkgtools.manifest.compare_versions()` /
   `is_newer()` implement -- **not** a string comparison (`"0.10.0" <
   "0.9.0"` lexicographically, but 0.10.0 is newer).
4. If newer, **tell the editor an update is available and stop.** Do not
   download, verify, or apply anything yet -- see "Updates require an
   explicit click" immediately below for why this line is load-bearing, not
   optional polish.
5. Only once the editor clicks to accept: download `artifact.url`, verify
   both `size_bytes` and `sha256` against the manifest before touching
   anything running (`verify_artifact_against_manifest()` is the exact check
   to mirror -- never unpack an artifact that fails it), then unpack over the
   existing `onedir` install the same way `installer/install.ps1`'s
   `Install-Bundle` does (robocopy `/MIR` from a freshly-extracted copy, not
   an in-place overwrite of a running exe).

### Updates require an explicit click. Never auto-apply unattended.

This is a deliberate decision, made with the reasoning recorded here on
purpose -- not a corner cut and hoped nobody would ask.

The spec (section 11.4) floats `tufup` (TUF-based signed updates) as the
intended mechanism. We are **not** implementing it. Real signing means real
key management: an offline root key, a rotation plan, somewhere safe to keep
it, someone who knows how to use it under pressure. A six-person studio will
not maintain that -- and an unmaintained or lost signing key is *worse* than
no signing, because it manufactures the appearance of a guarantee nobody is
actually upholding.

The threat signing exists to stop is a compromised release repo silently
pushing code onto six machines with no human in the loop. **Removing the
"unattended" removes most of that risk for a fraction of the cost.** A
malicious or corrupted release can still get published, but it cannot
install itself -- an editor has to see "update available" and choose to
accept it, which is also just... normal software behavior editors already
understand, unlike a signing failure they'd have no way to interpret.

What this repo actually ships, so the app-side owner knows exactly what
they're building on:

- The app may **check** for an update on launch and **tell** the editor one
  exists. It must **never** download-and-apply without the editor clicking
  to accept.
- sha256 (and size) from the manifest is still verified before applying --
  that part is unconditional and does not get weaker because signing is
  absent. It protects against a corrupted/truncated download and against
  tampering unless an attacker can also modify the GitHub release itself --
  real protection, just not a cryptographic guarantee back to an offline
  root key.
- If a `tufup` migration ever happens, it sits on top of this same manifest
  shape (the fields TUF metadata wraps are already here) without a schema
  break. Worth revisiting if this ever moves toward unattended auto-apply --
  which, per the above, is not the plan.

`min_supported_version` is advisory only here -- nothing in this package
enforces it. It exists so the updater can refuse to serve an update to a
version too old to understand the new manifest, if that ever becomes
necessary.

---

## Two-repo model (spec section 11.4)

- **`ASHMediaSolutions01/ashcaptions`** (this repo, **public** since
  2026-09-02 so editors can clone it without an account) -- source. Never
  contains client data, glossaries, secrets, or client footage; the history
  was rewritten before the flip to remove a client frame from a screenshot.
  The code is published under the all-rights-reserved `LICENSE`, not open
  source.
- **`ASHMediaSolutions01/ashcaptions-releases`** (public) -- built artifacts
  and `manifest.json` only. No source, no secrets, nothing an attacker gains
  anything from. Because it's public, `install.ps1` and the app-side updater
  hit **unauthenticated** URLs -- there is no token to put on six PCs, and
  therefore none to leak, rotate, or forget to revoke when someone leaves.

If the artifacts repo ever needs to go private (e.g. the built app itself
becomes something worth protecting, not just the source), the documented
fallback is a fine-grained, repo-scoped, **read-only**, expiring PAT stored
on each machine -- avoid this if at all possible; it reintroduces exactly
the distribution problem the public-repo pattern exists to sidestep.

---

## Directory map

```
scripts/
  build.py            # onedir PyInstaller build -> dist/AshCaptions*, build-info.json
  fetch_ffmpeg.py      # BtbN GPL static build -> bin/{ffmpeg,ffprobe}.exe
  fetch_model.py        # pre-seed a faster-whisper model -> build/models/ (HF cache root)
  requirements-build.txt # pip freeze of the verified build environment
  release.py           # publish dist/ to the public releases repo, write manifest.json
  enable_gpu.ps1        # per-machine GPU opt-in, run by Ghazi only
  pkgtools/
    manifest.py          # manifest schema: build / validate / read / compare versions
    gpu_matrix.py         # the CUDA/cuDNN decision table, pure Python mirror of enable_gpu.ps1

LICENSE                  # all rights reserved -- published, not open source
NOTICES.md               # third-party licences shipped in the bundle (ffmpeg, fonts, ...)

installer/
  install.ps1            # the actual installer -- CPU-only, idempotent, per-user
  Install-AshCaptions.bat # double-click wrapper an editor actually clicks
```
