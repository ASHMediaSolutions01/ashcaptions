"""Errors raised by the burn-in path (kept apart so the encoder probing
module can raise them without importing the burn module)."""

from __future__ import annotations


class BurnInError(Exception):
    """Raised when captions cannot be burned into the video."""

    def __init__(self, message: str, *, stderr: str = "", returncode: int | None = None) -> None:
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


class BurnCancelled(BurnInError):
    """Raised when ``should_stop`` asked for the burn to end early. The part
    file has already been removed when this is raised."""
