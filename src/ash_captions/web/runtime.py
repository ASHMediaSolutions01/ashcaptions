"""Facts about the process the control page is running in.

Two questions the web layer needs answered and must never guess at:

* ``updates_supported()`` -- may the in-app update flow (spec 11.4) be
  offered at all? The updater's apply step ends in a ``robocopy /MIR`` of
  the downloaded bundle over ``app_root()``. On an installed (PyInstaller
  frozen) build that is exactly right. On a source checkout it would mirror
  a release bundle over the git repository -- deleting everything not in
  the bundle, ``.git`` included. So the answer is "only a frozen build, and
  never anything that has a ``.git`` directory at its root", and the
  routes/banner are gated on it.

* ``app_version()`` -- the running package's version, used as the static
  asset cache-buster so a shipped update can never serve a stale
  ``app.js`` against a new ``index.html``.
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ash_captions.config import app_root

DEV_VERSION = "dev"


def is_frozen_build() -> bool:
    """True under a PyInstaller bundle (``sys.frozen`` is set by its bootloader)."""
    return bool(getattr(sys, "frozen", False))


def is_source_checkout(root: Path | None = None) -> bool:
    """True when ``root`` (default ``app_root()``) is a git working tree."""
    return ((root or app_root()) / ".git").exists()


def updates_supported() -> bool:
    """Whether "Update now" may be offered and applied in this process.

    Both conditions are required: a frozen build that somehow sits inside a
    git checkout is still not safe to mirror over.
    """
    return is_frozen_build() and not is_source_checkout()


def app_version() -> str:
    """Installed package version, or ``"dev"`` when metadata is unavailable
    (e.g. running from an un-installed source tree)."""
    try:
        return version("ash-captions")
    except PackageNotFoundError:
        return DEV_VERSION
