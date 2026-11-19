"""Expose theme configuration as design constants for controlled client presentation."""

from __future__ import annotations

from relaydeck.client.design_system import theme as _theme
from relaydeck.client.design_system.theme import TOKENS as TOKENS
from relaydeck.client.design_system.theme import Tokens as Tokens

# All values refer to the resolved immutable records.
_CONSOLE = _theme.CONSOLE
_SCALES = _theme.CONSOLE_SCALES

# -- page, text, actions, status (values in theme.toml [semantic.console]) -------
GROUND = _CONSOLE.ground
DOT = _CONSOLE.dot
CARD = _CONSOLE.card
CARD_SELECTED = _CONSOLE.card_selected
RAISED = _CONSOLE.raised
PANEL = _CONSOLE.panel
NAV = _CONSOLE.nav
TERMINAL_BG = _CONSOLE.terminal_bg
TERMINAL_CANVAS = _CONSOLE.terminal_canvas
BORDER = _CONSOLE.border
BORDER_DASHED_OFFLINE = _CONSOLE.border_dashed_offline
TEXT = _CONSOLE.text
TEXT_SECONDARY = _CONSOLE.text_secondary
TEXT_MUTED = _CONSOLE.text_muted
TEXT_TERMINAL_MUTED = _CONSOLE.text_terminal_muted
PRIMARY_FILL = _CONSOLE.primary_fill
PRIMARY_TEXT = _CONSOLE.primary_text
PRIMARY_TEXT_LIGHT = _CONSOLE.primary_text_light
BLOCKED = _CONSOLE.blocked
BLOCKED_TEXT = _CONSOLE.blocked_text
BLOCKED_SMALL_TEXT = _CONSOLE.blocked_small_text
BLOCKED_PILL_BG = _CONSOLE.blocked_pill_bg
BLOCKED_PILL_TEXT = _CONSOLE.blocked_pill_text
WORKING = _CONSOLE.working
WORKING_TEXT = _CONSOLE.working_text
DONE = _CONSOLE.done
DONE_TEXT = _CONSOLE.done_text
IDLE_FILL = _CONSOLE.idle_fill
IDLE_TEXT = _CONSOLE.idle_text
UNKNOWN_TEXT = _CONSOLE.unknown_text
# Roles that replaced scattered literals (values in theme.toml [semantic.console]).
FOCUS_WASH = _CONSOLE.focus_wash
ON_PRIMARY = _CONSOLE.on_primary
CHIP_INK = _CONSOLE.chip_ink
LIVE_INK = _CONSOLE.live_ink
TERMINAL_TEXT = _CONSOLE.terminal_text
STATUS_RULE = _CONSOLE.status_rule
PRESENCE_WARN = _CONSOLE.presence_warn
SCRIM_GRADIENT_START = _CONSOLE.scrim_gradient_start
LIGHT_INK = _CONSOLE.light_ink
LIGHT_SURFACE = _CONSOLE.light_surface
SCROLL_THUMB_BG = _CONSOLE.scroll_thumb_bg
SHELL_DISABLED = _CONSOLE.shell_disabled
TOAST_ACTION = _CONSOLE.toast_action
DIFF_ADD_BG = _CONSOLE.diff_add_bg
DIFF_ADD_MARK = _CONSOLE.diff_add_mark
DIFF_ADD_SIGN = _CONSOLE.diff_add_sign
DIFF_DELETE_BG = _CONSOLE.diff_delete_bg
DIFF_DELETE_MARK = _CONSOLE.diff_delete_mark
DIFF_DELETE_SIGN = _CONSOLE.diff_delete_sign
DIFF_HUNK_BG = _CONSOLE.diff_hunk_bg
DIFF_HUNK_TEXT = _CONSOLE.diff_hunk_text
DIFF_GUTTER_TEXT = _CONSOLE.diff_gutter_text

STATUS_BLOCKED = "blocked"
STATUS_WORKING = "working"
STATUS_DONE = "done"
STATUS_IDLE = "idle"
STATUS_UNKNOWN = "unknown"

STATUS_LABELS = {
    STATUS_BLOCKED: "Blocked",
    STATUS_WORKING: "Working",
    STATUS_DONE: "Done",
    STATUS_IDLE: "Idle",
    STATUS_UNKNOWN: "Unknown",
}

# -- shape -------------------------------------------------------------------
SURFACE_RADIUS = _theme.SURFACE_RADIUS

RADIUS_CARD = _SCALES.radius_card
RADIUS_TILE = _SCALES.radius_tile
RADIUS_PANE = _SCALES.radius_pane
RADIUS_TERMINAL = _SCALES.radius_terminal
RADIUS_KEY = _SCALES.radius_key
SELECT_OUTLINE_WIDTH = _SCALES.select_outline_width

# -- spacing ------------------------------------------------------------------
PAD_PHONE = _SCALES.pad_phone
PAD_WEB = _SCALES.pad_web
GAP_CARD = _SCALES.gap_card
GAP_SECTION = _SCALES.gap_section
GAP_CHIP = _SCALES.gap_chip
CARD_PAD_V = _SCALES.card_pad_v
CARD_PAD_H = _SCALES.card_pad_h
PANE_PAD = _SCALES.pane_pad
PANE_GAP = _SCALES.pane_gap

# -- touch targets ------------------------------------------------------------
# Canonical 48px hit-region floor, defined here (not in client.theme)
# so this package has no downward dependency on the app theme module
# (theme imports these tokens; the reverse import cycled).
MIN_TOUCH = _theme.MIN_TOUCH

# -- responsive shell  ---------------------------
BREAKPOINT_RAIL = 600  # <600: bottom nav; >=600: side rail
BREAKPOINT_SPLIT = 1100  # >=1100: rail + list + live detail
RAIL_WIDTH = 96
LIST_WIDTH = 380
COLLAPSED_WIDTH = 80
MIN_DETAIL_WIDTH = 320

LAYOUT_STACKED = "stacked"  # one screen at a time (phone + mid widths)
LAYOUT_SPLIT = "split"  # list + live detail side by side


def layout_class(width: float) -> str:
    """One component set at all widths; only the shell arrangement changes."""
    return LAYOUT_SPLIT if width >= BREAKPOINT_SPLIT else LAYOUT_STACKED


def has_rail(width: float) -> bool:
    """Side rail at >=600, bottom navigation below."""
    return width >= BREAKPOINT_RAIL


# -- type scale ---------------------------------------------------------------
TYPE_TITLE = _theme.TYPE_SCALES["type_title"]
TYPE_AGENT_TITLE = _theme.TYPE_SCALES["type_agent_title"]
TYPE_CARD_TITLE = _theme.TYPE_SCALES["type_card_title"]
TYPE_CARD_LINE = _theme.TYPE_SCALES["type_card_line"]
TYPE_CARD_LINE_STRONG = _theme.TYPE_SCALES["type_card_line_strong"]
TYPE_META = _theme.TYPE_SCALES["type_meta"]
TYPE_EYEBROW = _theme.TYPE_SCALES["type_eyebrow"]
TYPE_NAV_LABEL = _theme.TYPE_SCALES["type_nav_label"]
TYPE_NAV_LABEL_ACTIVE = _theme.TYPE_SCALES["type_nav_label_active"]
TYPE_BUTTON = _theme.TYPE_SCALES["type_button"]
TYPE_BUTTON_IDLE = _theme.TYPE_SCALES["type_button_idle"]
TYPE_PILL = _theme.TYPE_SCALES["type_pill"]
TYPE_COUNT_NUMBER = _theme.TYPE_SCALES["type_count_number"]
TYPE_COUNT_LABEL = _theme.TYPE_SCALES["type_count_label"]
TYPE_TILE_INITIALS = _theme.TYPE_SCALES["type_tile_initials"]


def initials(name: str) -> str:
    """Return the first two display-name characters in uppercase, or ?? for an empty name."""
    return (name[:2] or "??").upper()


# -- Usage screen ---------------------------------------------------------
# Usage-scoped tokens only: no existing token above is recoloured, and no other screen reads these.
# The light set's text pairs are chosen so that
# every text pair in it is checked to clear 4.5:1 (tests/ui/test_usage_redesign_tokens.py).
USAGE_DARK = _theme.USAGE_DARK
USAGE_LIGHT = _theme.USAGE_LIGHT


def usage_palette(dark: bool = True) -> _theme.UsageColors:
    """Select the immutable Usage colors for one appearance."""
    return USAGE_DARK if dark else USAGE_LIGHT
