"""Tests for ash_captions.styles.library.

Covers: loading the shipped styles/ directory, user-directory overrides
by name (spec 7A.2), and spec 7A.4's "a bad style file must never crash
a job" -- an invalid shipped or user file is skipped, and asking for a
style that doesn't validate falls back to the default rather than
raising.
"""
from __future__ import annotations

import json

import pytest

from ash_captions.styles.library import (
    DEFAULT_STYLE,
    delete_user_style,
    is_shipped_style,
    list_styles,
    load_style_file,
    resolve_style,
    save_user_style,
    shipped_styles_dir,
    validate_style_dict,
)
from ash_captions.styles.schema import Style, StyleValidationError

EXPECTED_SHIPPED_NAMES = {
    "CLEAN", "POP", "NEON GLOW", "LOWER THIRD", "KARAOKE", "HYPE", "PLAYFUL", "COMIC",
}


@pytest.fixture
def empty_user_dir(tmp_path):
    directory = tmp_path / "no_user_styles"
    directory.mkdir()
    return directory


def test_shipped_styles_dir_points_at_repo_root_styles():
    assert shipped_styles_dir().is_dir()
    assert (shipped_styles_dir() / "clean.json").is_file()


def test_ships_eight_to_ten_genuinely_distinct_looks(empty_user_dir):
    styles = list_styles(user_dir=empty_user_dir)
    assert EXPECTED_SHIPPED_NAMES <= set(styles)
    assert 8 <= len(styles) <= 10


def test_clean_and_pop_are_both_still_shipped(empty_user_dir):
    styles = list_styles(user_dir=empty_user_dir)
    assert "CLEAN" in styles
    assert "POP" in styles
    assert styles["CLEAN"] != styles["POP"]


def test_shipped_styles_span_distinct_active_word_effects(empty_user_dir):
    # A cheap proxy for "genuinely distinct looks", not a copy-pasted set.
    styles = list_styles(user_dir=empty_user_dir)
    effects = {s.active_word.effect for s in styles.values()}
    assert len(effects) >= 4


def test_every_shipped_style_loads_and_validates_cleanly():
    for path in shipped_styles_dir().glob("*.json"):
        style = load_style_file(path)
        assert isinstance(style, Style)


def test_user_style_overrides_shipped_style_by_name(tmp_path):
    user_dir = tmp_path / "user_styles"
    user_dir.mkdir()
    override = {"name": "CLEAN", "font": "Poppins", "size": 99}
    (user_dir / "clean.json").write_text(json.dumps(override), encoding="utf-8")

    styles = list_styles(user_dir=user_dir)

    assert styles["CLEAN"].font == "Poppins"
    assert styles["CLEAN"].size == 99


def test_list_styles_skips_invalid_file_without_raising(tmp_path):
    user_dir = tmp_path / "user_styles"
    user_dir.mkdir()
    (user_dir / "broken.json").write_text("{not valid json", encoding="utf-8")
    (user_dir / "bad_effect.json").write_text(
        json.dumps({"name": "BAD", "active_word": {"effect": "nonsense"}}), encoding="utf-8"
    )
    (user_dir / "good.json").write_text(json.dumps({"name": "GOOD"}), encoding="utf-8")

    styles = list_styles(user_dir=user_dir)

    assert "GOOD" in styles
    assert "BAD" not in styles


def test_resolve_style_falls_back_to_default_for_unknown_name(empty_user_dir):
    style = resolve_style("DOES-NOT-EXIST", user_dir=empty_user_dir)
    assert style == DEFAULT_STYLE


def test_resolve_style_falls_back_to_default_when_only_file_is_invalid(tmp_path):
    user_dir = tmp_path / "user_styles"
    user_dir.mkdir()
    (user_dir / "broken.json").write_text(
        json.dumps({"name": "BROKEN", "size": 99999}), encoding="utf-8"
    )

    style = resolve_style("BROKEN", user_dir=user_dir)

    assert style == DEFAULT_STYLE


def test_resolve_style_returns_a_valid_shipped_style(empty_user_dir):
    style = resolve_style("POP", user_dir=empty_user_dir)
    assert style.name == "POP"


def test_validate_style_dict_raises_for_the_editor_to_show_inline():
    with pytest.raises(StyleValidationError):
        validate_style_dict({"name": "X", "font": "Not Bundled"})


def test_save_user_style_writes_a_file_that_round_trips(tmp_path):
    user_dir = tmp_path / "user_styles"
    style = Style.from_dict({"name": "My Custom Look", "font": "Rubik"})

    path = save_user_style(style, user_dir=user_dir)

    assert path.is_file()
    reloaded = load_style_file(path)
    assert reloaded == style


def test_delete_user_style_removes_the_file_and_returns_true(tmp_path):
    user_dir = tmp_path / "user_styles"
    style = Style.from_dict({"name": "Temp Look", "font": "Rubik"})
    path = save_user_style(style, user_dir=user_dir)
    assert path.is_file()

    result = delete_user_style("Temp Look", user_dir=user_dir)

    assert result is True
    assert not path.is_file()


def test_delete_user_style_returns_false_when_not_found(tmp_path):
    user_dir = tmp_path / "user_styles"
    user_dir.mkdir()

    assert delete_user_style("NOPE", user_dir=user_dir) is False


def test_delete_user_style_returns_false_for_missing_directory(tmp_path):
    assert delete_user_style("NOPE", user_dir=tmp_path / "does-not-exist") is False


def test_delete_user_style_never_touches_shipped_styles(tmp_path):
    empty_user_dir = tmp_path / "user_styles"
    empty_user_dir.mkdir()

    result = delete_user_style("CLEAN", user_dir=empty_user_dir)

    assert result is False
    assert (shipped_styles_dir() / "clean.json").is_file()


def test_is_shipped_style_true_for_a_built_in_look():
    assert is_shipped_style("CLEAN") is True
    assert is_shipped_style("POP") is True


def test_is_shipped_style_false_for_a_user_only_style(tmp_path):
    assert is_shipped_style("Some User Style") is False
