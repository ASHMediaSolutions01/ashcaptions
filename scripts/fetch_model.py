"""Pre-seed a faster-whisper model into the bundle, once, on the build machine.

Run on Ghazi's build machine only, before `build.py`:

    .venv/Scripts/python.exe scripts/fetch_model.py --model-size small
    .venv/Scripts/python.exe scripts/fetch_model.py --model-size large-v3 --dest build/models/large-v3

Why: spec section 11.3. Six editors pulling multiple GB from HuggingFace over
the office connection on first run is not acceptable given the office
network's bandwidth reality, so the model ships inside the installer instead,
and `config.py`'s `Settings.model_cache_dir` points faster-whisper at the
bundled `models/` directory rather than the default `~/.cache/huggingface`.

Model size estimates in the spec (tiny ~75MB ... large-v3 ~3.1GB) are marked
there as *unverified*. This script does not trust them either -- it reports
the real size on disk after downloading, which is the number that actually
matters for "does this fit in the installer".
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, get_args

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST_ROOT = REPO_ROOT / "build" / "models"

# faster-whisper's CTranslate2-converted models, one HF repo per size. Same
# names `config.py`'s `ModelSize` uses, so a `--model-size` here is always a
# valid `Settings.model_size` on the other end.
ModelSize = Literal["tiny", "base", "small", "medium", "large-v3"]
MODEL_SIZES: tuple[str, ...] = get_args(ModelSize)
MODEL_REPO_TEMPLATE = "Systran/faster-whisper-{size}"

MODEL_INFO_FILE_NAME = "model-info.txt"


class FetchModelError(Exception):
    pass


def resolve_repo_id(model_size: str) -> str:
    if model_size not in MODEL_SIZES:
        raise FetchModelError(f"unknown model size {model_size!r}; choose one of {MODEL_SIZES}")
    return MODEL_REPO_TEMPLATE.format(size=model_size)


def download_model(model_size: str, dest_dir: Path, *, revision: str | None = None) -> Path:
    """Download the model snapshot into `dest_dir`. The only network-touching
    function in this script."""
    from huggingface_hub import snapshot_download

    repo_id = resolve_repo_id(model_size)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(repo_id=repo_id, local_dir=str(dest_dir), revision=revision)
    return dest_dir


def dir_size_bytes(path: Path) -> int:
    """Sum file sizes under `path`, recursively. Pure filesystem walk,
    testable against a temp directory with no download involved."""
    total = 0
    for p in Path(path).rglob("*"):
        if p.is_file():
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
    out_path = Path(dest_dir) / MODEL_INFO_FILE_NAME
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-size", required=True, choices=MODEL_SIZES)
    parser.add_argument("--dest", type=Path, default=None, help="Defaults to build/models/<size>.")
    parser.add_argument("--revision", default=None, help="Pin a specific HF revision/commit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    dest_dir = args.dest or (DEFAULT_DEST_ROOT / args.model_size)
    repo_id = resolve_repo_id(args.model_size)

    print(f"Downloading {repo_id} -> {dest_dir}")
    download_model(args.model_size, dest_dir, revision=args.revision)

    size_bytes = dir_size_bytes(dest_dir)
    info_path = write_model_info(
        dest_dir, model_size=args.model_size, repo_id=repo_id, revision=args.revision, size_bytes=size_bytes
    )
    print(f"Model on disk: {human_readable_size(size_bytes)} ({size_bytes} bytes)")
    print(f"Wrote {info_path}")
    print(f"Pass --model-dir {dest_dir} to build.py to bundle it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
