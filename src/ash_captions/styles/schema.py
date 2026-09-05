"""The style model (spec 7A.2).

A style is data -- a JSON file in ``styles/`` -- never a branch in the
renderer. This module defines exactly what a valid style looks like and
validates one eagerly, at load time, with an error that says precisely
what was wrong. A bad style file must never crash a job (spec 7A.4): the
caller is expected to catch ``StyleValidationError`` and fall back to
the default style -- see ``library.resolve_style``.

Field reference (spec 7A.2's example, extended per the fields the style
editor needs to expose -- spec 7A.3):

    {
      "name": "POP BOLD",
      "font": "Montserrat ExtraBold", "size": 78, "uppercase": true,
      "letter_spacing": 1.5,
      "colors": {"text": "#FFFFFF", "active": "#00E28A",
                 "outline": "#000000", "shadow": "#00000090",
                 "box": "#00000000"},
      "active_word": {"effect": "scale_box", "scale": 1.18, "box": true},
      "entrance": {"effect": "rise", "duration_ms": 140},
      "exit": {"effect": "fade", "duration_ms": 100},
      "layout": {"position": "center", "max_words": 3,
                 "margin_l": 60, "margin_r": 60, "margin_v": 120}
    }

Every field has a default, so a minimal ``{"name": "MY STYLE"}`` is a
valid (if plain) style -- useful for the style editor building one up
incrementally.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from typing import Any

from .fonts import is_font_bundled

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class StyleValidationError(ValueError):
    """A style file or dict failed validation.

    Always carries a message naming exactly which field was wrong and
    why -- spec 7A.4 requires this for a missing font specifically, and
    the same standard is applied to every other field so a mistake in
    the style editor (spec 7A.3) is actionable, not a generic reject.
    """


# ---------------------------------------------------------------------------
# enums (as plain string sets -- effects are values the renderer branches
# on, never style names; see engine/render.py)
# ---------------------------------------------------------------------------

# "box"/"scale_box" box the active word alone (one word on screen at a
# time -- a box is a Style-level property in ASS). "card_box" boxes the
# whole caption as one bar and marks the active word by colour: the
# news lower-third / corner-tag look.
ACTIVE_WORD_EFFECTS = frozenset({"none", "pop", "box", "scale_box", "card_box", "karaoke", "shake", "glow"})
TRANSITION_EFFECTS = frozenset({"none", "fade", "rise", "slide"})
POSITIONS = frozenset({"bottom", "center", "top", "lower_third"})
ALIGNS = frozenset({"left", "center", "right"})

# Free placement (design 2026-09-05, section 5). "line" is every look
# shipped up to v0.5: one band, words laid out as a sentence. "free"
# gives each word its own slot and keeps it on screen while the next
# arrives. A slot's colour is a *role* -- the name of a key in the look's
# own ``colors`` block -- so a slot never carries a raw hex value.
LAYOUT_MODES = frozenset({"line", "free"})
SLOT_COLOUR_ROLES = frozenset({"text", "active", "outline", "shadow", "box"})
# Both entrances were measured off the owner's own reference frames:
# stretch_collapse enters ~180% wide and snaps to 100% over ~120ms;
# fade_settle fades in over ~240ms while shrinking from ~110% and
# dropping a few pixels. See render_free.
FREE_ENTRANCES = frozenset({"none", "stretch_collapse", "fade_settle"})

_HEX_COLOUR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")

_MIN_SIZE, _MAX_SIZE = 10, 300
_MIN_SCALE, _MAX_SCALE = 0.5, 3.0
_MIN_LETTER_SPACING, _MAX_LETTER_SPACING = -5.0, 30.0
_MIN_DURATION_MS, _MAX_DURATION_MS = 0, 2000
_MIN_MAX_WORDS, _MAX_MAX_WORDS = 1, 8
_MIN_MARGIN, _MAX_MARGIN = 0, 2000
# Fractions of the frame: a per-word free position (v0.6 section 5).
_MIN_FRACTION, _MAX_FRACTION = 0.0, 1.0
_MIN_SLOT_SCALE, _MAX_SLOT_SCALE = 0.2, 3.0
_MIN_SLOT_BORDER, _MAX_SLOT_BORDER = 0.0, 3.0
_MIN_INTENSITY, _MAX_INTENSITY = 0.0, 1.0
_MAX_SLOTS = _MAX_MAX_WORDS  # a card can never be wider than max_words


def _require_hex_colour(path: str, value: Any) -> str:
    if not isinstance(value, str) or not _HEX_COLOUR_RE.match(value):
        raise StyleValidationError(
            f"{path}: {value!r} is not a valid hex colour "
            "(expected '#RRGGBB' or '#RRGGBBAA')"
        )
    return value


def _require_number(path: str, value: Any, *, lo: float, hi: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StyleValidationError(f"{path}: {value!r} is not a number")
    if not (lo <= value <= hi):
        raise StyleValidationError(f"{path}: {value} is out of range (expected {lo}-{hi})")
    return value


def _require_bool(path: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise StyleValidationError(f"{path}: {value!r} is not true/false")
    return value


def _require_choice(path: str, value: Any, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise StyleValidationError(
            f"{path}: unknown value {value!r} (expected one of {sorted(choices)})"
        )
    return value


def _require_dict(path: str, value: Any) -> dict:
    if not isinstance(value, dict):
        raise StyleValidationError(f"{path}: expected an object, got {type(value).__name__}")
    return value


def _reject_unknown_keys(path: str, data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        names = ", ".join(sorted(unknown))
        raise StyleValidationError(f"{path}: unknown field(s) {names}")


# ---------------------------------------------------------------------------
# sub-models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Colors:
    text: str = "#FFFFFF"
    active: str = "#FFD166"
    outline: str = "#000000"
    shadow: str = "#00000090"
    box: str = "#00000000"

    @classmethod
    def from_dict(cls, data: dict) -> "Colors":
        _reject_unknown_keys("colors", data, {"text", "active", "outline", "shadow", "box"})
        defaults = cls()
        kwargs = {}
        for name in ("text", "active", "outline", "shadow", "box"):
            value = data.get(name, getattr(defaults, name))
            kwargs[name] = _require_hex_colour(f"colors.{name}", value)
        return cls(**kwargs)


@dataclass(frozen=True, slots=True)
class ActiveWord:
    effect: str = "pop"
    scale: float = 1.12
    box: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "ActiveWord":
        _reject_unknown_keys("active_word", data, {"effect", "scale", "box"})
        defaults = cls()
        effect = _require_choice(
            "active_word.effect", data.get("effect", defaults.effect), ACTIVE_WORD_EFFECTS
        )
        scale = _require_number(
            "active_word.scale", data.get("scale", defaults.scale), lo=_MIN_SCALE, hi=_MAX_SCALE
        )
        box = _require_bool("active_word.box", data.get("box", defaults.box))
        return cls(effect=effect, scale=float(scale), box=box)


@dataclass(frozen=True, slots=True)
class Transition:
    """Shared shape for ``entrance`` and ``exit``."""

    effect: str = "fade"
    duration_ms: int = 120

    @classmethod
    def from_dict(cls, path: str, data: dict, *, default_effect: str, default_duration_ms: int) -> "Transition":
        _reject_unknown_keys(path, data, {"effect", "duration_ms"})
        effect = _require_choice(
            f"{path}.effect", data.get("effect", default_effect), TRANSITION_EFFECTS
        )
        duration_ms = _require_number(
            f"{path}.duration_ms",
            data.get("duration_ms", default_duration_ms),
            lo=_MIN_DURATION_MS,
            hi=_MAX_DURATION_MS,
        )
        return cls(effect=effect, duration_ms=int(duration_ms))


@dataclass(frozen=True, slots=True)
class Slot:
    """One landing spot in a free-placement look (design 2026-09-05, 5).

    A slot is a *treatment*, not a word: a point on the frame, how much
    bigger or smaller than the look's own size the word is drawn, which
    of the look's colours it takes, whether it leans, which bundled face
    it uses, and how it arrives. ``render_free.assign_slots`` decides
    which word of a card gets which slot.

    ``x``/``y`` are fractions of the frame, so one look renders correctly
    at any PlayRes; ``role`` names a key of the look's own ``colors``
    block rather than carrying a hex value, so re-colouring a look moves
    every slot with it.
    """

    x: float
    y: float
    scale: float = 1.0
    role: str = "text"
    italic: bool = False
    font: str | None = None  # None: the look's own font
    entrance: str = "stretch_collapse"
    # Multiplier on the look's outline width. 0.0 draws no outline at
    # all, which a slot whose colour role *is* the outline colour needs:
    # a black word wearing a black border renders as an unreadable slab.
    border: float = 1.0

    @classmethod
    def from_dict(cls, path: str, data: dict, *, check_font: bool = True) -> "Slot":
        data = _require_dict(path, data)
        _reject_unknown_keys(
            path, data, {"x", "y", "scale", "role", "italic", "font", "entrance", "border"}
        )
        defaults = cls(x=0.0, y=0.0)
        for name in ("x", "y"):
            if name not in data:
                raise StyleValidationError(f"{path}.{name}: a slot needs both x and y (fractions of the frame)")
        x = _require_number(f"{path}.x", data["x"], lo=0.0, hi=1.0)
        y = _require_number(f"{path}.y", data["y"], lo=0.0, hi=1.0)
        scale = _require_number(
            f"{path}.scale", data.get("scale", defaults.scale), lo=_MIN_SLOT_SCALE, hi=_MAX_SLOT_SCALE
        )
        role = _require_choice(f"{path}.role", data.get("role", defaults.role), SLOT_COLOUR_ROLES)
        italic = _require_bool(f"{path}.italic", data.get("italic", defaults.italic))
        entrance = _require_choice(
            f"{path}.entrance", data.get("entrance", defaults.entrance), FREE_ENTRANCES
        )
        border = _require_number(
            f"{path}.border", data.get("border", defaults.border), lo=_MIN_SLOT_BORDER, hi=_MAX_SLOT_BORDER
        )
        font = data.get("font", defaults.font)
        if font is not None:
            if not isinstance(font, str) or not font.strip():
                raise StyleValidationError(f"{path}.font: {font!r} is not a valid font name")
            if check_font and not is_font_bundled(font):
                raise StyleValidationError(
                    f"{path}.font: {font!r} is not a bundled font -- see assets/fonts/manifest.json "
                    "for the available faces"
                )
        return cls(
            x=float(x),
            y=float(y),
            scale=float(scale),
            role=role,
            italic=italic,
            font=font,
            entrance=entrance,
            border=float(border),
        )

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(Slot)}


@dataclass(frozen=True, slots=True)
class Layout:
    position: str = "bottom"
    max_words: int = 4
    margin_l: int = 80
    margin_r: int = 80
    margin_v: int = 120
    # Horizontal anchor within the position band: "bottom-left" captions
    # sit at align=left, margin_l from the edge. Combined with position
    # this maps onto the ASS numpad alignment (1-9).
    align: str = "center"
    # "line" is every look shipped before v0.6: words laid out as a
    # sentence in one band. "free" places each word at its own slot and
    # keeps it there while the next arrives -- see Slot and render_free.
    mode: str = "line"
    slots: tuple[Slot, ...] = ()
    # How much of the look's drama to use, 0.0-1.0. 1.0 is the slot list
    # as its author drew it; 0.0 is a tidy centred stack at one size.
    # Free mode only -- it is the one dial that makes three free looks
    # feel like a family rather than three fixed pictures.
    intensity: float = 1.0

    @classmethod
    def from_dict(cls, data: dict, *, check_font: bool = True) -> "Layout":
        _reject_unknown_keys(
            "layout",
            data,
            {
                "position", "max_words", "margin_l", "margin_r", "margin_v", "align",
                "mode", "slots", "intensity",
            },
        )
        defaults = cls()
        position = _require_choice("layout.position", data.get("position", defaults.position), POSITIONS)
        align = _require_choice("layout.align", data.get("align", defaults.align), ALIGNS)
        max_words = _require_number(
            "layout.max_words",
            data.get("max_words", defaults.max_words),
            lo=_MIN_MAX_WORDS,
            hi=_MAX_MAX_WORDS,
        )
        margin_l = _require_number(
            "layout.margin_l", data.get("margin_l", defaults.margin_l), lo=_MIN_MARGIN, hi=_MAX_MARGIN
        )
        margin_r = _require_number(
            "layout.margin_r", data.get("margin_r", defaults.margin_r), lo=_MIN_MARGIN, hi=_MAX_MARGIN
        )
        margin_v = _require_number(
            "layout.margin_v", data.get("margin_v", defaults.margin_v), lo=_MIN_MARGIN, hi=_MAX_MARGIN
        )
        mode = _require_choice("layout.mode", data.get("mode", defaults.mode), LAYOUT_MODES)
        slots = _slots_from_list(data.get("slots", []), mode=mode, max_words=int(max_words), check_font=check_font)
        intensity = _require_number(
            "layout.intensity", data.get("intensity", defaults.intensity), lo=_MIN_INTENSITY, hi=_MAX_INTENSITY
        )
        if mode != "free" and intensity != defaults.intensity:
            raise StyleValidationError('layout.intensity: intensity is only used by mode "free"')
        return cls(
            position=position,
            max_words=int(max_words),
            margin_l=int(margin_l),
            margin_r=int(margin_r),
            margin_v=int(margin_v),
            align=align,
            mode=mode,
            slots=slots,
            intensity=float(intensity),
        )

    def to_dict(self) -> dict:
        """The JSON shape ``from_dict`` accepts. ``slots`` needs its own
        pass because a tuple of dataclasses is not JSON."""
        return {
            f.name: [s.to_dict() for s in self.slots] if f.name == "slots" else getattr(self, f.name)
            for f in fields(Layout)
        }


def _slots_from_list(value: Any, *, mode: str, max_words: int, check_font: bool) -> tuple[Slot, ...]:
    """Validate ``layout.slots`` against ``layout.mode``.

    Two cross-field rules, both boundary checks rather than renderer
    surprises: a free look with no slots has nowhere to put a word, and a
    free look with fewer slots than ``max_words`` would stack two words
    on one point (``assign_slots`` cycles rather than crashing, but a
    shipped look should never reach that)."""
    if not isinstance(value, list):
        raise StyleValidationError(f"layout.slots: expected a list, got {type(value).__name__}")
    if len(value) > _MAX_SLOTS:
        raise StyleValidationError(f"layout.slots: {len(value)} slots is more than the maximum of {_MAX_SLOTS}")
    slots = tuple(
        Slot.from_dict(f"layout.slots[{i}]", item, check_font=check_font) for i, item in enumerate(value)
    )
    if mode == "free":
        if not slots:
            raise StyleValidationError('layout.slots: a layout with mode "free" needs at least one slot')
        if len(slots) < max_words:
            raise StyleValidationError(
                f"layout.slots: {len(slots)} slot(s) cannot hold a card of layout.max_words={max_words} "
                "words -- give the layout at least as many slots as max_words"
            )
    elif slots:
        raise StyleValidationError('layout.slots: slots are only used by mode "free"')
    return slots


# ---------------------------------------------------------------------------
# per-word overrides (v0.6 design, section 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WordStyle:
    """One word's own colour, size, weight or slant, overriding the look.

    Deliberately *not* font family and *not* outline: those stay
    properties of the look (v0.6 design, section 2 -- the combinatorial
    surface is where per-word styling turns into a mess). Every field is
    optional and ``None`` means "whatever the look does", so an empty
    ``WordStyle`` renders byte-identically to no override at all.

    ``x``/``y`` are free placement (fractions of the frame), used only by
    a free-placement look -- v0.6 section 5, track F. They are declared
    here because the transcript record and the API carry one ``style``
    key per word for both features; the line renderer ignores them.

    Lives in ``styles`` rather than ``app`` so the renderer never imports
    from the application layer (spec Interfaces, "Per-word style").
    """

    colour: str | None = None   # "#RRGGBB" or "#RRGGBBAA"
    scale: float | None = None  # multiplier on the look's size, 0.5-3.0
    bold: bool | None = None
    italic: bool | None = None
    x: float | None = None      # free placement only, fraction of the frame
    y: float | None = None

    @classmethod
    def from_dict(cls, data: dict, *, path: str = "style") -> "WordStyle":
        """Build and validate one override. Raises ``StyleValidationError``
        naming the exact field, like every other model here."""
        data = _require_dict(path, data)
        _reject_unknown_keys(path, data, set(_WORD_STYLE_FIELDS))

        colour = data.get("colour")
        if colour is not None:
            colour = _require_hex_colour(f"{path}.colour", colour)
        scale = data.get("scale")
        if scale is not None:
            scale = float(_require_number(f"{path}.scale", scale, lo=_MIN_SCALE, hi=_MAX_SCALE))
        bold = data.get("bold")
        if bold is not None:
            bold = _require_bool(f"{path}.bold", bold)
        italic = data.get("italic")
        if italic is not None:
            italic = _require_bool(f"{path}.italic", italic)
        x = data.get("x")
        if x is not None:
            x = float(_require_number(f"{path}.x", x, lo=_MIN_FRACTION, hi=_MAX_FRACTION))
        y = data.get("y")
        if y is not None:
            y = float(_require_number(f"{path}.y", y, lo=_MIN_FRACTION, hi=_MAX_FRACTION))
        return cls(colour=colour, scale=scale, bold=bold, italic=italic, x=x, y=y)

    def to_dict(self) -> dict:
        """The JSON shape ``from_dict`` accepts, with unset fields left
        out -- one override rides on a word in the transcript record, so
        the absent keys should not be written as nulls."""
        return {name: getattr(self, name) for name in _WORD_STYLE_FIELDS if getattr(self, name) is not None}

    def is_empty(self) -> bool:
        """True when nothing is overridden -- renders as the look itself."""
        return all(getattr(self, name) is None for name in _WORD_STYLE_FIELDS)


_WORD_STYLE_FIELDS = ("colour", "scale", "bold", "italic", "x", "y")


# ---------------------------------------------------------------------------
# the style itself
# ---------------------------------------------------------------------------

_TOP_LEVEL_FIELDS = {
    "name",
    "font",
    "size",
    "uppercase",
    "letter_spacing",
    "colors",
    "active_word",
    "entrance",
    "exit",
    "layout",
}


@dataclass(frozen=True, slots=True)
class Style:
    name: str
    font: str = "Inter"
    size: int = 72
    uppercase: bool = False
    letter_spacing: float = 0.0
    colors: Colors = field(default_factory=Colors)
    active_word: ActiveWord = field(default_factory=ActiveWord)
    entrance: Transition = field(default_factory=lambda: Transition(effect="fade", duration_ms=120))
    exit: Transition = field(default_factory=lambda: Transition(effect="none", duration_ms=0))
    layout: Layout = field(default_factory=Layout)

    @classmethod
    def from_dict(cls, data: dict, *, check_font: bool = True) -> "Style":
        """Build and validate a ``Style`` from parsed JSON.

        Raises ``StyleValidationError`` naming the exact field at fault --
        including, per spec 7A.4, the font name itself when it is not in
        the bundled manifest. ``check_font=False`` is for tests of the
        rest of the schema that don't want a manifest dependency; real
        callers (``library.py``) always leave it on.
        """
        data = _require_dict("style", data)
        _reject_unknown_keys("style", data, _TOP_LEVEL_FIELDS)

        if "name" not in data or not isinstance(data["name"], str) or not data["name"].strip():
            raise StyleValidationError("name: a non-empty style name is required")
        name = data["name"]

        defaults = cls(name=name)

        font = data.get("font", defaults.font)
        if not isinstance(font, str) or not font.strip():
            raise StyleValidationError(f"font: {font!r} is not a valid font name")
        if check_font and not is_font_bundled(font):
            raise StyleValidationError(
                f"font: {font!r} is not a bundled font -- see assets/fonts/manifest.json "
                "for the available faces"
            )

        size = _require_number("size", data.get("size", defaults.size), lo=_MIN_SIZE, hi=_MAX_SIZE)
        uppercase = _require_bool("uppercase", data.get("uppercase", defaults.uppercase))
        letter_spacing = _require_number(
            "letter_spacing",
            data.get("letter_spacing", defaults.letter_spacing),
            lo=_MIN_LETTER_SPACING,
            hi=_MAX_LETTER_SPACING,
        )

        colors = Colors.from_dict(_require_dict("colors", data.get("colors", {})))
        active_word = ActiveWord.from_dict(_require_dict("active_word", data.get("active_word", {})))
        entrance = Transition.from_dict(
            "entrance", _require_dict("entrance", data.get("entrance", {})),
            default_effect=defaults.entrance.effect,
            default_duration_ms=defaults.entrance.duration_ms,
        )
        exit_ = Transition.from_dict(
            "exit", _require_dict("exit", data.get("exit", {})),
            default_effect=defaults.exit.effect,
            default_duration_ms=defaults.exit.duration_ms,
        )
        layout = Layout.from_dict(_require_dict("layout", data.get("layout", {})), check_font=check_font)

        return cls(
            name=name,
            font=font,
            size=int(size),
            uppercase=uppercase,
            letter_spacing=float(letter_spacing),
            colors=colors,
            active_word=active_word,
            entrance=entrance,
            exit=exit_,
            layout=layout,
        )

    def to_dict(self) -> dict:
        """Round-trip back to the JSON shape ``from_dict`` accepts --
        used by the style editor (spec 7A.3) to save an edited style."""
        return {
            "name": self.name,
            "font": self.font,
            "size": self.size,
            "uppercase": self.uppercase,
            "letter_spacing": self.letter_spacing,
            "colors": {f.name: getattr(self.colors, f.name) for f in fields(Colors)},
            "active_word": {f.name: getattr(self.active_word, f.name) for f in fields(ActiveWord)},
            "entrance": {f.name: getattr(self.entrance, f.name) for f in fields(Transition)},
            "exit": {f.name: getattr(self.exit, f.name) for f in fields(Transition)},
            "layout": self.layout.to_dict(),
        }


DEFAULT_STYLE = Style(name="CLEAN")
