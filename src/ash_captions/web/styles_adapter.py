"""Bridges web's `StyleProvider` protocol onto `ash_captions.styles` (spec
7A). Consumes that package's public interface (`ash_captions.styles`'s
`__init__.py`) only -- never its submodules -- the same relationship
`app.adapter.QueueAdapter` has to `pipeline.JobStore`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ash_captions.styles import (
    StyleValidationError,
    delete_user_style,
    is_shipped_style,
    list_font_families,
    list_styles as _list_styles,
    save_user_style,
    validate_style_dict,
)

from .interfaces import StyleIsShippedError, StyleNotFoundError, StyleValidationFailedError
from .models import StyleSummary

# `get_style(shipped_only=True)` needs `list_styles()` to see *only* the
# shipped directory. Passing a path that can never exist as `user_dir`
# reuses that public function rather than reaching into `styles.library`'s
# private `_load_directory`.
_NO_USER_DIR = Path("__ash_captions_no_user_styles__")


class StylesPackageAdapter:
    """Implements `StyleProvider` over the real `ash_captions.styles` package."""

    def list_styles(self) -> list[StyleSummary]:
        return [
            StyleSummary(name=name, shipped=is_shipped_style(name), definition=style.to_dict())
            for name, style in _list_styles().items()
        ]

    def get_style(self, name: str, *, shipped_only: bool = False) -> StyleSummary:
        styles = _list_styles(user_dir=_NO_USER_DIR) if shipped_only else _list_styles()
        style = styles.get(name)
        if style is None:
            raise StyleNotFoundError(name)
        return StyleSummary(name=name, shipped=is_shipped_style(name), definition=style.to_dict())

    def save_style(self, name: str, definition: dict[str, Any]) -> StyleSummary:
        payload = dict(definition)
        payload["name"] = name  # the URL path segment is always the identity
        try:
            style = validate_style_dict(payload)
        except StyleValidationError as exc:
            raise StyleValidationFailedError(str(exc)) from exc

        save_user_style(style)
        return StyleSummary(name=style.name, shipped=is_shipped_style(style.name), definition=style.to_dict())

    def delete_style(self, name: str) -> None:
        # is_shipped_style() is true "regardless of whether a user override
        # of the same name also exists" (its own docstring) -- refusing the
        # whole name, not just an override, is the deliberately conservative
        # choice: the built-in library must never look deletable from here.
        if is_shipped_style(name):
            raise StyleIsShippedError(name)
        if not delete_user_style(name):
            raise StyleNotFoundError(name)

    def list_fonts(self) -> list[str]:
        return list(list_font_families())


# `get_style(shipped_only=True)` needs `list_styles()` to see *only* the
# shipped directory. Passing a path that can never exist as `user_dir`
# reuses that public function rather than reaching into `styles.library`'s
# private `_load_directory`.
from pathlib import Path  # noqa: E402 - kept near its one use

_NO_USER_DIR = Path("__ash_captions_no_user_styles__")
