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
# shipped directory, and computing `customized_locally` needs the inverse --
# *only* the user directory, to tell "a user file exists for this name" apart
# from "the merged, possibly-shipped-only result". Passing a path that can
# never exist as `user_dir`/`shipped_dir` reuses that one public function for
# both rather than reaching into `styles.library`'s private `_load_directory`.
_NO_USER_DIR = Path("__ash_captions_no_user_styles__")
_NO_SHIPPED_DIR = Path("__ash_captions_no_shipped_styles__")


def _user_override_names() -> set[str]:
    """Names with a saved user file -- regardless of whether that name also
    happens to be shipped. Used to compute `StyleSummary.customized_locally`:
    a shipped name shows up here exactly when it has been silently shadowed
    (spec 7A.2's override-by-name behaviour) by an editor's own save."""
    return set(_list_styles(shipped_dir=_NO_SHIPPED_DIR))


class StylesPackageAdapter:
    """Implements `StyleProvider` over the real `ash_captions.styles` package."""

    def list_styles(self) -> list[StyleSummary]:
        user_names = _user_override_names()
        return [
            StyleSummary(
                name=name,
                shipped=is_shipped_style(name),
                customized_locally=is_shipped_style(name) and name in user_names,
                definition=style.to_dict(),
            )
            for name, style in _list_styles().items()
        ]

    def get_style(self, name: str, *, shipped_only: bool = False) -> StyleSummary:
        styles = _list_styles(user_dir=_NO_USER_DIR) if shipped_only else _list_styles()
        style = styles.get(name)
        if style is None:
            raise StyleNotFoundError(name)
        shipped = is_shipped_style(name)
        customized = False if shipped_only else shipped and name in _user_override_names()
        return StyleSummary(name=name, shipped=shipped, customized_locally=customized, definition=style.to_dict())

    def save_style(self, name: str, definition: dict[str, Any]) -> StyleSummary:
        payload = dict(definition)
        payload["name"] = name  # the URL path segment is always the identity
        try:
            style = validate_style_dict(payload)
        except StyleValidationError as exc:
            raise StyleValidationFailedError(str(exc)) from exc

        try:
            save_user_style(style)
        except ValueError as exc:
            # A Windows reserved name (CON, NUL, ...) or a name whose file slug
            # collides with a different existing style: the editor shows this
            # inline like any other validation failure.
            raise StyleValidationFailedError(str(exc)) from exc
        shipped = is_shipped_style(style.name)
        # Just wrote the user file ourselves, so it unconditionally exists now.
        return StyleSummary(name=style.name, shipped=shipped, customized_locally=shipped, definition=style.to_dict())

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
