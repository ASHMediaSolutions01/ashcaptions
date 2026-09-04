"""Collect the licence texts of everything the bundle redistributes.

    .venv/Scripts/python.exe scripts/collect_licenses.py [--dest build/licenses]

Walks the build venv's installed distributions, copies each package's
licence file(s) into ``<dest>/<package>/`` and writes
``<dest>/THIRD_PARTY_LICENSES.txt`` (name, version, declared licence, files).
``build.py`` bundles ``<dest>`` as ``licenses/`` and refuses to build
without it: PyInstaller keeps ``*.dist-info`` for only a handful of
packages, so without this step most licence texts silently do not ship.

Also fetches the GPL-3.0 text for the Robust Video Matting weights into
``<dest>/robust-video-matting/COPYING`` (the weights themselves are GPL-3.0
and only the upstream URL was recorded before).
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import re
import shutil
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = REPO_ROOT / "build" / "licenses"
GPL3_URLS = (
    "https://raw.githubusercontent.com/PeterL1n/RobustVideoMatting/master/LICENSE",
    "https://www.gnu.org/licenses/gpl-3.0.txt",
)
LICENSE_FILE_RE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)([._-].*)?$", re.I)
# Packages that exist only on the build box, never in the bundle.
SKIP = {"pip", "setuptools", "wheel", "pyinstaller", "pyinstaller-hooks-contrib", "pytest", "pytest-timeout",
        "iniconfig", "pluggy", "packaging", "altgraph", "pefile", "pywin32-ctypes", "git-filter-repo", "yt-dlp",
        "ash-captions", "playwright", "pyproject-hooks", "build", "av", "pytest-asyncio"}


def declared_license(dist: md.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    lic = meta.get("License")
    if lic and len(lic) < 80:
        return lic
    for classifier in meta.get_all("Classifier") or []:
        if classifier.startswith("License ::"):
            return classifier.split("::")[-1].strip()
    return lic[:60] + "..." if lic else "(not declared)"


def collect(dest: Path, *, fetch_gpl3: bool = True) -> list[tuple[str, str, str, list[str]]]:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    rows: list[tuple[str, str, str, list[str]]] = []
    for dist in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = dist.metadata["Name"]
        if name.lower() in SKIP:
            continue
        files = []
        for f in dist.files or []:
            fname = Path(str(f)).name
            if LICENSE_FILE_RE.match(fname) and ".dist-info" in str(f) or (LICENSE_FILE_RE.match(fname) and "licenses" in str(f).lower()):
                src = Path(dist.locate_file(f))
                if src.is_file():
                    out = dest / name.lower() / fname
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, out)
                    files.append(fname)
        rows.append((name, dist.version, declared_license(dist), files))
    if fetch_gpl3:
        rvm = dest / "robust-video-matting"
        rvm.mkdir(parents=True, exist_ok=True)
        try:
            text = None
            last_exc: Exception | None = None
            for url in GPL3_URLS:
                try:
                    with urllib.request.urlopen(url, timeout=60) as resp:
                        text = resp.read()
                    break
                except Exception as exc:  # noqa: BLE001 - try the next mirror
                    last_exc = exc
            if text is None:
                raise RuntimeError(last_exc)
            (rvm / "COPYING").write_bytes(text)
            (rvm / "README.txt").write_text(
                "rvm_mobilenetv3_fp32.onnx is Robust Video Matting (github.com/PeterL1n/RobustVideoMatting),\n"
                "redistributed unmodified under the GPL-3.0 (COPYING in this folder).\n", encoding="utf-8")
            rows.append(("Robust Video Matting weights", "1.0.0", "GPL-3.0", ["COPYING"]))
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: could not fetch the GPL-3.0 text for RVM: {exc}", file=sys.stderr)
    lines = ["Third-party licences shipped with ASH Captions", "", "package | version | declared licence | files in this folder", ""]
    for name, version, lic, files in rows:
        lines.append(f"{name} | {version} | {lic} | {', '.join(files) if files else '(no licence file in the wheel; see the declared licence)'}")
    (dest / "THIRD_PARTY_LICENSES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--no-fetch", action="store_true", help="Do not download the GPL-3.0 text.")
    args = parser.parse_args(argv)
    rows = collect(args.dest, fetch_gpl3=not args.no_fetch)
    with_files = sum(1 for r in rows if r[3])
    print(f"{len(rows)} packages recorded, {with_files} with licence texts, in {args.dest}")
    missing = [r[0] for r in rows if not r[3]]
    if missing:
        print("no licence file in the wheel for: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
