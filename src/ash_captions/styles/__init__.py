"""The caption styling system (spec 7A): the style model, the shipped
style library, the animated ASS renderer, bundled fonts, and the style
preview command builder.

Public interface -- other packages should import from here:

    from ash_captions.styles import (
        Style, Colors, ActiveWord, Transition, Layout, StyleValidationError, DEFAULT_STYLE,
        list_styles, resolve_style, validate_style_dict, load_style_file, save_user_style,
        shipped_styles_dir, user_styles_dir,
        render_ass, write_ass,
        is_font_bundled, list_font_families, fontsdir_arg, download_fonts,
        build_preview_command,
    )
"""
from .fonts import download_fonts, fontsdir_arg, is_font_bundled, list_font_families
from .library import (
    DEFAULT_STYLE,
    list_styles,
    load_style_file,
    resolve_style,
    save_user_style,
    shipped_styles_dir,
    user_styles_dir,
    validate_style_dict,
)
from .preview import DEFAULT_PREVIEW_DURATION_SECONDS, build_preview_command
from .render import DEFAULT_PLAY_RES, render_ass, write_ass
from .schema import ActiveWord, Colors, Layout, Style, StyleValidationError, Transition

__all__ = [
    "Style",
    "Colors",
    "ActiveWord",
    "Transition",
    "Layout",
    "StyleValidationError",
    "DEFAULT_STYLE",
    "list_styles",
    "resolve_style",
    "validate_style_dict",
    "load_style_file",
    "save_user_style",
    "shipped_styles_dir",
    "user_styles_dir",
    "render_ass",
    "write_ass",
    "DEFAULT_PLAY_RES",
    "is_font_bundled",
    "list_font_families",
    "fontsdir_arg",
    "download_fonts",
    "build_preview_command",
    "DEFAULT_PREVIEW_DURATION_SECONDS",
]
