"""RelayDeck application tokens, resolved once at import from theme.toml. theme.toml (at the
root of the `relaydeck.client` package) is the single source of the app's look.
"""

from __future__ import annotations

import copy
import os
import re
import tomllib
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from datetime import date
from importlib.resources import files
from types import MappingProxyType
from typing import Any

from relaydeck.client.design_system.fonts import FAMILY_SYMBOLS
from relaydeck.client.models.layout import COMPACT as COMPACT
from relaydeck.client.models.layout import DESKTOP as DESKTOP
from relaydeck.client.models.layout import PHONE as PHONE

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


@dataclass(frozen=True)
class Palette:
    """Chrome colour roles for one appearance (dark or light)."""

    bg: str
    surface: str
    raised: str
    line: str
    outline: str
    text: str
    muted: str
    accent: str
    on_accent: str
    accent_text: str
    teal: str
    amber: str
    danger: str
    success: str
    disabled_text: str
    error_bg: str
    rail_bg: str
    active_row_bg: str
    active_row_border: str
    terminal_bg: str
    terminal_text: str
    terminal_muted: str
    terminal_attention: str


@dataclass(frozen=True)
class ConsoleColors:
    """Console roles of the design system: grounds, text, actions, status and diff colours."""

    ground: str
    dot: str
    card: str
    card_selected: str
    raised: str
    panel: str
    nav: str
    terminal_bg: str
    terminal_canvas: str
    border: str
    border_dashed_offline: str
    text: str
    text_secondary: str
    text_muted: str
    text_terminal_muted: str
    primary_fill: str
    primary_text: str
    primary_text_light: str
    blocked: str
    blocked_text: str
    blocked_small_text: str
    blocked_pill_bg: str
    blocked_pill_text: str
    working: str
    working_text: str
    done: str
    done_text: str
    graph_lane_one: str
    graph_lane_two: str
    graph_lane_three: str
    idle_fill: str
    idle_text: str
    unknown_text: str
    focus_wash: str
    on_primary: str
    chip_ink: str
    live_ink: str
    terminal_text: str
    status_rule: str
    presence_warn: str
    scrim_gradient_start: str
    light_ink: str
    light_surface: str
    scroll_thumb_bg: str
    shell_disabled: str
    toast_action: str
    diff_add_bg: str
    diff_add_mark: str
    diff_add_sign: str
    diff_delete_bg: str
    diff_delete_mark: str
    diff_delete_sign: str
    diff_hunk_bg: str
    diff_hunk_text: str
    diff_gutter_text: str


@dataclass(frozen=True)
class UsageColors:
    """Usage chart palette for one appearance (component tier)."""

    bg: str
    bg_deep: str
    surface: str
    surface_sheet: str
    surface_raised: str
    surface_accent: str
    nav: str
    border: str
    border_strong: str
    text: str
    text_2: str
    text_muted: str
    text_faint: str
    accent: str
    accent_on: str
    link: str
    link_hover: str
    series_1: str
    series_1_dim: str
    series_2: str
    series_other: str
    warn: str
    warn_bg: str
    danger: str
    danger_bg: str


@dataclass(frozen=True)
class ConsoleScales:
    """Console radii, paddings and gaps in logical pixels."""

    radius_card: int
    radius_tile: int
    radius_pane: int
    radius_terminal: int
    radius_key: int
    card_pad_v: int
    card_pad_h: int
    gap_card: int
    gap_section: int
    gap_chip: int
    pad_phone: int
    pad_web: int
    pane_pad: int
    pane_gap: int
    select_outline_width: int


@dataclass(frozen=True)
class TypeStyle:
    """One text style: size in logical pixels (may be fractional), weight, line-height multiple."""

    size: float
    weight: int
    height: float


@dataclass(frozen=True)
class AgentTokens:
    """Agent identity colours: (dark, light, light ink) triples, fallbacks and space tints."""

    triples: tuple[tuple[str, str, str], ...]
    fallback_dark: str
    fallback_light: str
    light_ink_fallback: str
    space_tints: tuple[str, ...]


@dataclass(frozen=True)
class Tokens:
    """Every resolved theme value, grouped by tier. Built once from theme.toml."""

    # primitive
    spacing: tuple[int, ...]
    radius: tuple[int, ...]
    min_touch: int
    console_scales: ConsoleScales
    font_size: Mapping[str, int]
    # semantic
    palette_dark: Palette
    palette_light: Palette
    status_colors: Mapping[str, str]
    banner_tones: Mapping[str, str]
    agent: AgentTokens
    console: ConsoleColors
    type_styles: Mapping[str, TypeStyle]
    surface_light_ref: str
    surface_dark_ref: str
    # component
    usage_dark: UsageColors
    usage_light: UsageColors


def _fail(where: str, problem: str) -> ValueError:
    return ValueError(f"theme.toml [{where}]: {problem}")


def _table(doc: dict[str, Any], *path: str) -> dict[str, Any]:
    node: Any = doc
    for part in path:
        if not isinstance(node, dict) or part not in node:
            raise _fail(".".join(path), "missing section")
        node = node[part]
    if not isinstance(node, dict):
        raise _fail(".".join(path), "section is not a table")
    return node


def _check_keys(table: dict[str, Any], cls: Any, where: str) -> None:
    keys = frozenset(f.name for f in fields(cls))
    if frozenset(table) != keys:
        missing = sorted(keys - frozenset(table))
        extra = sorted(frozenset(table) - keys)
        raise _fail(where, f"key mismatch missing={missing} unknown={extra}")


def _hex(value: Any, where: str, key: str) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        raise _fail(where, f"{key} is not a hex colour: {value!r}")
    return value


def _colours[C](cls: type[C], table: dict[str, Any], where: str) -> C:
    """A colour record whose fields are exactly the table's keys, each a hex colour."""
    _check_keys(table, cls, where)
    construct: Callable[..., C] = cls
    return construct(**{key: _hex(value, where, key) for key, value in table.items()})


def _ints[C](table: dict[str, Any], cls: type[C], where: str) -> C:
    _check_keys(table, cls, where)
    for key, value in table.items():
        if type(value) is not int:
            raise _fail(where, f"{key} must be an int")
    construct: Callable[..., C] = cls
    return construct(**table)


def _int_tuple(table: dict[str, Any], key: str) -> tuple[int, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not value or not all(type(v) is int for v in value):
        raise _fail("primitive.scales", f"{key} must be a non-empty list of ints")
    return tuple(value)


def _type_styles(table: dict[str, Any]) -> Mapping[str, TypeStyle]:
    styles: dict[str, TypeStyle] = {}
    for name, body in table.items():
        where = f"semantic.type.{name}"
        if not isinstance(body, dict):
            raise _fail(where, "section is not a table")
        _check_keys(body, TypeStyle, where)
        if type(body["weight"]) is not int:
            raise _fail(where, "weight must be an int")
        for key in ("size", "height"):
            if type(body[key]) not in (int, float):
                raise _fail(where, f"{key} must be a number")
        styles[name] = TypeStyle(body["size"], body["weight"], body["height"])
    return MappingProxyType(styles)


def _agent(table: dict[str, Any]) -> AgentTokens:
    where = "semantic.agent"
    _check_keys(table, AgentTokens, where)
    triples = table["triples"]
    if not isinstance(triples, list) or not triples:
        raise _fail(where, "triples must be a non-empty list")
    for triple in triples:
        if not isinstance(triple, list) or len(triple) != 3:
            raise _fail(where, f"each triple holds three colours: {triple!r}")
    tints = table["space_tints"]
    if not isinstance(tints, list) or not tints:
        raise _fail(where, "space_tints must not be empty")
    return AgentTokens(
        triples=tuple(tuple(_hex(c, where, "triples") for c in triple) for triple in triples),  # type: ignore[misc]
        fallback_dark=_hex(table["fallback_dark"], where, "fallback_dark"),
        fallback_light=_hex(table["fallback_light"], where, "fallback_light"),
        light_ink_fallback=_hex(table["light_ink_fallback"], where, "light_ink_fallback"),
        space_tints=tuple(_hex(tint, where, "space_tints") for tint in tints),
    )


def build_tokens(doc: dict[str, Any]) -> Tokens:
    """Validate a theme document and resolve it into frozen tokens."""
    scales = _table(doc, "primitive", "scales")
    if type(scales.get("min_touch")) is not int:
        raise _fail("primitive.scales", "min_touch must be an int")
    font_size = _table(doc, "primitive", "font_size")
    if not all(type(v) is int for v in font_size.values()):
        raise _fail("primitive.font_size", "sizes must be ints")
    a11y = _table(doc, "semantic", "a11y")
    status = _table(doc, "semantic", "status")
    return Tokens(
        spacing=_int_tuple(scales, "spacing"),
        radius=_int_tuple(scales, "radius"),
        min_touch=scales["min_touch"],
        console_scales=_ints(
            _table(doc, "primitive", "scales", "console"),
            ConsoleScales,
            "primitive.scales.console",
        ),
        font_size=MappingProxyType(dict(font_size)),
        palette_dark=_colours(
            Palette, _table(doc, "semantic", "palette", "dark"), "semantic.palette.dark"
        ),
        palette_light=_colours(
            Palette, _table(doc, "semantic", "palette", "light"), "semantic.palette.light"
        ),
        status_colors=MappingProxyType(dict(status["colors"])),
        banner_tones=MappingProxyType(dict(status["banner_tones"])),
        agent=_agent(_table(doc, "semantic", "agent")),
        console=_colours(ConsoleColors, _table(doc, "semantic", "console"), "semantic.console"),
        type_styles=_type_styles(_table(doc, "semantic", "type")),
        surface_light_ref=_hex(a11y.get("surface_light_ref"), "semantic.a11y", "surface_light_ref"),
        surface_dark_ref=_hex(a11y.get("surface_dark_ref"), "semantic.a11y", "surface_dark_ref"),
        usage_dark=_colours(
            UsageColors, _table(doc, "component", "usage", "dark"), "component.usage.dark"
        ),
        usage_light=_colours(
            UsageColors, _table(doc, "component", "usage", "light"), "component.usage.light"
        ),
    )


# What an overlay may replace: backgrounds, surfaces, borders and brand fills. Text, status and
# terminal colours stay fixed so meaning and readability never depend on the season.
_OVERLAY_CONSOLE = frozenset(
    {
        "ground",
        "dot",
        "card",
        "card_selected",
        "raised",
        "panel",
        "nav",
        "border",
        "status_rule",
        "primary_fill",
        "primary_text",
        "primary_text_light",
    }
)
_OVERLAY_PALETTE = frozenset(
    {
        "bg",
        "surface",
        "raised",
        "line",
        "accent",
        "on_accent",
        "accent_text",
        "rail_bg",
        "active_row_bg",
    }
)
_MONTH_DAY = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$")
DEFAULT_THEME = "default"


def _overlay_colours(table: Any, allowed: frozenset[str], where: str) -> dict[str, str]:
    if not isinstance(table, dict):
        raise _fail(where, "section is not a table")
    unknown = sorted(frozenset(table) - allowed)
    if unknown:
        raise _fail(where, f"an overlay may not replace {unknown}")
    for key, value in table.items():
        if not isinstance(value, str) or _HEX.fullmatch(value) is None:
            raise _fail(where, f"{key} is not a hex colour: {value!r}")
    return dict(table)


def _overlays(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    themes = _table(doc, "themes")
    active = themes.get("active", "auto")
    found: dict[str, dict[str, Any]] = {}
    for name, body in themes.items():
        if name == "active":
            continue
        where = f"themes.{name}"
        if name in (DEFAULT_THEME, "auto") or not isinstance(body, dict):
            raise _fail(where, "overlay names must be tables other than default/auto")
        if frozenset(body) - {"window", "console", "palette"}:
            raise _fail(where, "an overlay holds only window, console and palette")
        window = body.get("window")
        if window is not None and (
            not isinstance(window, list)
            or len(window) != 2
            or not all(isinstance(day, str) and _MONTH_DAY.fullmatch(day) for day in window)
        ):
            raise _fail(where, 'window must be ["MM-DD", "MM-DD"]')
        palettes = body.get("palette", {})
        if not isinstance(palettes, dict) or frozenset(palettes) - {"dark", "light"}:
            raise _fail(where, "palette holds only dark and light")
        found[name] = {
            "window": window,
            "console": _overlay_colours(body.get("console", {}), _OVERLAY_CONSOLE, where),
            "palette": {
                mode: _overlay_colours(table, _OVERLAY_PALETTE, f"{where}.palette.{mode}")
                for mode, table in palettes.items()
            },
        }
    if active not in ("auto", DEFAULT_THEME, *found):
        raise _fail("themes", f"active {active!r} is not auto, default or an overlay name")
    return found


def _in_window(window: list[str] | None, today: date) -> bool:
    if window is None:
        return False
    start, end = window
    mark = today.strftime("%m-%d")
    # A window may wrap the new year (12-15 to 01-02).
    return start <= mark <= end if start <= end else mark >= start or mark <= end


def resolve_theme_name(choice: str, overlays: dict[str, dict[str, Any]], today: date) -> str:
    """An explicit overlay name, or under "auto" the first overlay whose window holds today."""
    if choice == "auto":
        for name, body in overlays.items():
            if _in_window(body["window"], today):
                return name
        return DEFAULT_THEME
    return choice if choice in overlays else DEFAULT_THEME


def apply_overlay(doc: dict[str, Any], overlay: dict[str, Any] | None) -> dict[str, Any]:
    """A copy of the theme document with the overlay's colours merged in."""
    merged = copy.deepcopy(doc)
    if overlay is None:
        return merged
    merged["semantic"]["console"].update(overlay["console"])
    for mode, table in overlay["palette"].items():
        merged["semantic"]["palette"][mode].update(table)
    return merged


def _scrim_colour(doc: dict[str, Any]) -> str:
    """Resolve the named scrim colour from the bundled theme."""
    table = _table(doc, "semantic", "overlay")
    value = table.get("scrim")
    if (
        set(table) != {"scrim", "context_text_muted"}
        or not isinstance(value, str)
        or not re.fullmatch(r"[a-z][a-z0-9]*", value)
    ):
        raise _fail("semantic.overlay", "scrim must be a named colour")
    return value


def _load() -> dict[str, Any]:
    raw = files("relaydeck.client").joinpath("theme.toml").read_bytes()
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"theme.toml: unparseable ({exc})") from exc
    expected = {"meta", "primitive", "semantic", "component", "themes"}
    if frozenset(doc) != expected:
        raise _fail("top level", f"sections {sorted(doc)} != {sorted(expected)}")
    if doc["meta"] != {"version": 1}:
        raise _fail("meta", "only version = 1 is supported")
    return doc


_BASE_DOC = _load()
OVERLAYS = MappingProxyType(_overlays(_BASE_DOC))
ACTIVE_THEME = resolve_theme_name(
    os.environ.get("RELAYDECK_THEME") or str(_table(_BASE_DOC, "themes").get("active", "auto")),
    dict(OVERLAYS),
    date.today(),
)
_DOC = apply_overlay(_BASE_DOC, OVERLAYS.get(ACTIVE_THEME))
TOKENS = build_tokens(_DOC)

# Immutable records share the resolved token value.
CONSOLE = TOKENS.console
USAGE_DARK = TOKENS.usage_dark
USAGE_LIGHT = TOKENS.usage_light
SPACING: tuple[int, ...] = TOKENS.spacing
RADIUS: tuple[int, ...] = TOKENS.radius
MIN_TOUCH: int = TOKENS.min_touch
CONSOLE_SCALES = TOKENS.console_scales
TYPE_SCALES = TOKENS.type_styles
CONTRAST_SURFACE_LIGHT_REF: str = TOKENS.surface_light_ref
CONTRAST_SURFACE_DARK_REF: str = TOKENS.surface_dark_ref
ACCENT: str = TOKENS.palette_light.accent

PHONE_MAX_WIDTH = 599
WIDE_BREAKPOINT = 1100
COMPACT_MAX_WIDTH = WIDE_BREAKPOINT - 1
RAIL_WIDTH = 96
RAIL_WIDTH_COMPACT = 96
LIST_WIDTH = 380
LIST_WIDTH_COMPACT = 380
SHEET_MAX_WIDTH = 520
SHEET_MIN_WIDTH = 280
SCRIM_OPACITY = 0.5
SCRIM_COLOR: str = _scrim_colour(_DOC)
CONTEXT_TEXT_MUTED: str = _hex(
    _table(_DOC, "semantic", "overlay").get("context_text_muted"),
    "semantic.overlay",
    "context_text_muted",
)


def layout_mode(width: float) -> str:
    """Shared route/presentation classification in logical pixels."""
    if width <= PHONE_MAX_WIDTH:
        return PHONE
    if width < WIDE_BREAKPOINT:
        return COMPACT
    return DESKTOP


# Legacy graphic-only tokens retained for isolated terminal notices. Chrome
# uses palette-aware foregrounds below; every status also carries a word.
STATUS_COLORS: Mapping[str, str] = TOKENS.status_colors
STATUS_LABELS = {
    "idle": "Idle",
    "working": "Working",
    "blocked": "Blocked",
    "done": "Done",
    "unknown": "Unknown",
}
BANNER_TONES: Mapping[str, str] = TOKENS.banner_tones


DARK = TOKENS.palette_dark
LIGHT = TOKENS.palette_light
THEMES: Mapping[str, Palette] = MappingProxyType({"dark": DARK, "light": LIGHT})


def palette(dark: bool = True) -> Palette:
    """Select the immutable palette for one appearance."""
    return DARK if dark else LIGHT


def graph_palette() -> tuple[str, ...]:
    return (
        CONSOLE.primary_text,
        CONSOLE.done,
        CONSOLE.graph_lane_one,
        CONSOLE.graph_lane_two,
        CONSOLE.graph_lane_three,
    )


def status_color(status: str, dark: bool = True) -> str:
    tokens = palette(dark)
    if status == "working":
        return tokens.accent_text
    if status == "blocked":
        return tokens.amber
    if status == "done":
        return tokens.success
    return tokens.muted


@dataclass(frozen=True)
class AgentIdentity:
    label: str
    color: str


# Hash kind names into stable colours without naming kinds in code.
# Light label inks clear contrast against the composited identity wash in every tile state.
_AGENT_PALETTE: tuple[tuple[str, str, str], ...] = TOKENS.agent.triples
_AGENT_FALLBACK_DARK: str = TOKENS.agent.fallback_dark
_AGENT_FALLBACK_LIGHT: str = TOKENS.agent.fallback_light
_AGENT_LIGHT_INK_FALLBACK: str = TOKENS.agent.light_ink_fallback
_SPACE_TINTS: tuple[str, ...] = TOKENS.agent.space_tints


def space_tint(label: str) -> str:
    """Stable chip colour for a space name: the same space always gets the same tint."""
    index = zlib.crc32(label.strip().casefold().encode("utf-8")) % len(_SPACE_TINTS)
    return _SPACE_TINTS[index]


def _palette_entry(kind: str) -> tuple[str, str, str]:
    """Deterministic colour triple for a non-empty kind (stable across runs)."""
    index = zlib.crc32(kind.strip().casefold().encode("utf-8")) % len(_AGENT_PALETTE)
    return _AGENT_PALETTE[index]


def agent_identity(kind: str, dark: bool) -> AgentIdentity:
    """Identity color for a run card: label + theme ink. Case-insensitive; unknown/empty kinds
    fall back to a neutral ink with the capitalized kind (or "Shell").
    """
    name = (kind or "").strip()
    if not name:
        return AgentIdentity("Shell", _AGENT_FALLBACK_DARK if dark else _AGENT_FALLBACK_LIGHT)
    dark_color, light_color, _ink = _palette_entry(name)
    return AgentIdentity(name.capitalize(), dark_color if dark else light_color)


def agent_badge_ink(kind: str, dark: bool) -> str:
    """Choose label ink that clears contrast against the identity wash across tile states.
    Preserve the identity colour for the stripe and wash.
    """
    if dark:
        return agent_identity(kind, True).color
    name = (kind or "").strip()
    if not name:
        return _AGENT_LIGHT_INK_FALLBACK
    return _palette_entry(name)[2]


def agent_subtitle_rest(subtitle: str, agent: str) -> str:
    """Subtitle remainder after the leading agent word. Selectors compose "agent · project ·
    activity"; the card renders the agent as a pill and this remainder as muted text.
    """
    if agent and subtitle == agent:
        return ""
    prefix = f"{agent} · " if agent else ""
    if prefix and subtitle.startswith(prefix):
        return subtitle[len(prefix) :]
    return subtitle


# Foreground/background pairs actually used by the chrome and terminal.
CONTRAST_PAIRS: tuple[tuple[str, str, str, float], ...] = (
    ("text/bg", "text", "bg", 4.5),
    ("text/surface", "text", "surface", 4.5),
    ("text/raised", "text", "raised", 4.5),
    ("text/selected", "text", "active_row_bg", 4.5),
    ("muted/bg", "muted", "bg", 4.5),
    ("muted/surface", "muted", "surface", 4.5),
    ("muted/raised", "muted", "raised", 4.5),
    ("muted/selected", "muted", "active_row_bg", 4.5),
    ("on_accent/accent", "on_accent", "accent", 4.5),
    ("accent/surface", "accent_text", "surface", 4.5),
    ("accent/raised", "accent_text", "raised", 4.5),
    ("accent/selected", "accent_text", "active_row_bg", 4.5),
    ("amber/surface", "amber", "surface", 4.5),
    ("amber/selected", "amber", "active_row_bg", 4.5),
    ("success/surface", "success", "surface", 4.5),
    ("success/selected", "success", "active_row_bg", 4.5),
    ("danger/error", "danger", "error_bg", 4.5),
    ("disabled/raised", "disabled_text", "raised", 4.5),
    ("outline/surface", "outline", "surface", 3.0),
    ("outline/raised", "outline", "raised", 3.0),
    ("focus/surface", "accent_text", "surface", 3.0),
    ("focus/raised", "accent_text", "raised", 3.0),
    ("terminal/text", "terminal_text", "terminal_bg", 4.5),
    ("terminal/muted", "terminal_muted", "terminal_bg", 4.5),
    ("terminal/attention", "terminal_attention", "terminal_bg", 4.5),
)

FONT_SIZE: Mapping[str, int] = TOKENS.font_size
MONO_STACK = ("ui-monospace", "SFMono-Regular", "Consolas", "monospace")
MOTION_HOVER_MS = 140
MOTION_MODAL_MS = 160
FOCUS_RING_WIDTH = 2
FOCUS_RING_OFFSET = 2
SURFACE_RADIUS = 8
# Glyph fallback for UI text: the bundled faces lack the dingbats, shapes and braille agents emit.
# Styles pass list(TEXT_FONT_FALLBACK) as font_family_fallback.
TEXT_FONT_FALLBACK: tuple[str, ...] = (FAMILY_SYMBOLS, "Noto Sans Symbols")
