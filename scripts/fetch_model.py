"""Pre-seed a faster-whisper model into the bundle, once, on the build machine.

Run on Ghazi's build machine only, before `build.py`:

    .venv/Scripts/python.exe scripts/fetch_model.py --model-size small
    .venv/Scripts/python.exe scripts/fetch_model.py --model-size large-v3
    .venv/Scripts/python.exe scripts/build.py --model-dir build/models

Why: spec section 11.3. Six editors pulling multiple GB from HuggingFace over
the office connection on first run is not acceptable given the office
network's bandwidth reality, so the model ships inside the installer instead,
and `config.py`'s `Settings.model_cache_dir` points faster-whisper at the
bundled `models/` directory rather than the default `~/.cache/huggingface`.

Layout -- this is the part that was silently wrong for a whole release:
faster-whisper passes `download_root` straight through to huggingface_hub as
`cache_dir`, which means it expects the *HF cache layout*::

    models/
      models--Systran--faster-whisper-small/
        refs/main                      <- commit hash
        snapshots/<commit hash>/       <- config.json, model.bin, tokenizer.json, vocabulary.txt

A flat `models/small/model.bin` (what `snapshot_download(local_dir=...)`
produces) is invisible to that lookup, so every machine ignored the bundled
model, downloaded its own into `C:\\AshCaptions\\models`, and lost it again
on the next update. This script therefore downloads into a real HF cache
rooted at `build/models/` -- one root, any number of sizes -- and `build.py`
bundles that root as `models/`. The snapshot files are then *materialised*:
huggingface_hub links snapshot files to `blobs/` (symlinks where Windows
allows them), and neither PyInstaller's data collection nor the installer's
robocopy mirror reliably preserves links, so they are replaced by real files
and `blobs/` is dropped. Offline resolution only reads `refs/` and
`snapshots/`; `resolve_offline()` below is the proof, and the test suite
runs it against a bundle laid out exactly as `build.py` produces.

Model size estimates in the spec (tiny ~75MB ... large-v3 ~3.1GB) are marked
there as *unverified*. This script does not trust them either -- it reports
the real size on disk after downloading, which is the number that actually
matters for "does this fit in the installer".
"""

from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_ROOT = REPO_ROOT / "build" / "models"

# faster-whisper's CTranslate2-converted models, one HF repo per size. Same
# names `config.py`'s `ModelSize` uses, so a `--model-size` here is always a
# valid `Settings.model_size` on the other end.
ModelSize = Literal["tiny", "base", "small", "medium", "large-v3"]
MODEL_SIZES: tuple[str, ...] = get_args(ModelSize)
MODEL_REPO_TEMPLATE = "Systran/faster-whisper-{size}"

# The files faster-whisper's own `download_model()` fetches -- and all that
# a snapshot needs to hold for `WhisperModel` to load it.
SNAPSHOT_REQUIRED_FILES = ("config.json", "model.bin", "tokenizer.json")


class FetchModelError(Exception):
    pass


def resolve_repo_id(model_size: str) -> str:
    if model_size not in MODEL_SIZES:
        raise FetchModelError(f"unknown model size {model_size!r}; choose one of {MODEL_SIZES}")
    return MODEL_REPO_TEMPLATE.format(size=model_size)


def repo_cache_dir(cache_root: Path, model_size: str) -> Path:
    """`<cache_root>/models--Systran--faster-whisper-<size>` -- the HF cache
    folder name for this model's repo."""
    return Path(cache_root) / ("models--" + resolve_repo_id(model_size).replace("/", "--"))


def model_info_filename(model_size: str) -> str:
    return f"model-info-{model_size}.txt"


def download_model(model_size: str, cache_root: Path, *, revision: str | None = None) -> Path:
    """Download the model into the HF cache rooted at `cache_root` and return
    the snapshot directory. The only network-touching function in this
    script. Goes through faster-whisper's own `download_model` so the files
    fetched, and the layout they land in, are exactly what `WhisperModel`
    resolves at runtime."""
    from faster_whisper.utils import download_model as fw_download_model

    repo_id = resolve_repo_id(model_size)
    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    snapshot = fw_download_model(repo_id, cache_dir=str(cache_root), revision=revision)
    return Path(snapshot)


def resolve_offline(model_size: str, cache_root: Path, *, revision: str | None = None) -> Path:
    """Resolve the bundled snapshot with the network forbidden -- the exact
    lookup `WhisperModel(download_root=<bundle>/models)` performs. Raises if
    the layout under `cache_root` is not one faster-whisper can find."""
    from faster_whisper.utils import download_model as fw_download_model

    repo_id = resolve_repo_id(model_size)
    return Path(
        fw_download_model(repo_id, cache_dir=str(cache_root), revision=revision, local_files_only=True)
    )


def materialize_snapshots(cache_root: Path) -> int:
    """Replace every symlinked snapshot file under `cache_root` with a real
    copy of its blob, then drop `blobs/`. Returns the number of files
    replaced. Pure filesystem work; idempotent."""
    replaced = 0
    for repo_dir in sorted(Path(cache_root).glob("models--*")):
        snapshots = repo_dir / "snapshots"
        if not snapshots.is_dir():
            continue
        for path in snapshots.rglob("*"):
            if not path.is_symlink():
                continue
            target = path.resolve()
            tmp = path.with_name(path.name + ".materialize.tmp")
            shutil.copyfile(target, tmp)
            path.unlink()
            os.replace(tmp, path)
            replaced += 1
        blobs = repo_dir / "blobs"
        if blobs.is_dir():
            shutil.rmtree(blobs)
    # Download-time lock files -- noise in a bundle, and huggingface_hub
    # recreates them on demand.
    locks = Path(cache_root) / ".locks"
    if locks.is_dir():
        shutil.rmtree(locks)
    return replaced


def find_snapshot_dirs(cache_root: Path) -> list[Path]:
    """Every `models--*/snapshots/<hash>` directory under `cache_root` that
    holds a complete model (see SNAPSHOT_REQUIRED_FILES)."""
    found = []
    for repo_dir in sorted(Path(cache_root).glob("models--*")):
        for snapshot in sorted((repo_dir / "snapshots").glob("*")):
            if all((snapshot / name).is_file() for name in SNAPSHOT_REQUIRED_FILES):
                found.append(snapshot)
    return found


def dir_size_bytes(path: Path) -> int:
    """Sum file sizes under `path`, recursively. Pure filesystem walk,
    testable against a temp directory with no download involved."""
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file() and not p.is_symlink():
            total += p.stat().st_size
    return total


def human_readable_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover -- unreachable, satisfies linters


def write_model_info(
    dest_dir: Path, *, model_size: str, repo_id: str, revision: str | None, size_bytes: int
) -> Path:
    lines = [
        f"fetched: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"model_size: {model_size}",
        f"source_repo: {repo_id}",
        f"revision: {revision or '(default branch)'}",
        f"size_on_disk: {size_bytes} bytes ({human_readable_size(size_bytes)})",
    ]
    out_path = Path(dest_dir) / model_info_filename(model_size)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-size", required=True, choices=MODEL_SIZES)
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="HF cache root to download into (defaults to build/models). Pass the same "
        "directory to build.py --model-dir; several sizes may share it.",
    )
    parser.add_argument("--revision", default=None, help="Pin a specific HF revision/commit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cache_root = args.dest or DEFAULT_CACHE_ROOT
    repo_id = resolve_repo_id(args.model_size)

    print(f"Downloading {repo_id} -> {cache_root}")
    snapshot = download_model(args.model_size, cache_root, revision=args.revision)
    replaced = materialize_snapshots(cache_root)
    if replaced:
        print(f"Materialised {replaced} linked snapshot file(s) as real files")

    resolved = resolve_offline(args.model_size, cache_root, revision=args.revision)
    if resolved.resolve() != snapshot.resolve():
        raise FetchModelError(
            f"offline resolution returned {resolved}, expected {snapshot} -- the cache layout is wrong"
        )
    print(f"Offline resolution OK: {resolved}")

    size_bytes = dir_size_bytes(repo_cache_dir(cache_root, args.model_size))
    info_path = write_model_info(
        cache_root, model_size=args.model_size, repo_id=repo_id, revision=args.revision, size_bytes=size_bytes
    )
    print(f"Model on disk: {human_readable_size(size_bytes)} ({size_bytes} bytes)")
    print(f"Wrote {info_path}")
    print(f"Pass --model-dir {cache_root} to build.py to bundle it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
