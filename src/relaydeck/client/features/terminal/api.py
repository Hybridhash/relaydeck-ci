"""Expose terminal sessions, input capabilities and explicit stream recovery."""

from relaydeck.client.features.terminal.agent_session import AGENT_LIVE, AgentSession
from relaydeck.client.features.terminal.callbacks import view_actions
from relaydeck.client.features.terminal.connection_status import connection_status
from relaydeck.client.features.terminal.controller import TerminalController
from relaydeck.client.features.terminal.foreground_recovery import ForegroundRecovery
from relaydeck.client.features.terminal.geometry import DEFAULT_GEOMETRY, page_geometry
from relaydeck.client.features.terminal.input_modes import ROUTE_DROPPED, ROUTE_PTY
from relaydeck.client.features.terminal.keys import KeyInputSession, encode_key
from relaydeck.client.features.terminal.layout_budget import (
    COMPOSER_HEIGHT,
    CONTEXT_STRIP_HEIGHT,
    FIELD_LINE_HEIGHT,
    PHONE_DOCK_HEIGHT,
    PHONE_FALLBACK_WIDTH,
    PICTURE_HEIGHT,
    TYPING_SPLIT_FALLBACK_WIDTH,
    TYPING_SPLIT_GAP,
    TYPING_SPLIT_HEADER_HEIGHT,
    WINDOW_PADDING,
    DetailBudget,
    keyboard_overlap,
    measured_row_height,
    plan_detail,
    typing_split_active,
    typing_split_lines,
    typing_split_widths,
)
from relaydeck.client.features.terminal.models import SelectionState, TerminalViewState
from relaydeck.client.features.terminal.retention import TerminalRegistry
from relaydeck.client.features.terminal.screen_cache import ScreenCache, ScreenSnapshot
from relaydeck.client.features.terminal.selection_area import selected_offsets
from relaydeck.client.features.terminal.selection_model import (
    copy_text,
    display_width,
    normalize_range,
    resolved_range,
    set_selection_error,
)
from relaydeck.client.features.terminal.sessions import TAKEOVER_DISCLOSURE_AGENT, drive_recovery
from relaydeck.client.features.terminal.shell_session import (
    CLOSE_MESSAGE,
    CLOSE_TITLE,
    SHELL_CONFLICT,
    SHELL_CONNECTING,
    SHELL_DISCONNECTED,
    SHELL_EXITED,
    SHELL_LIVE,
    ShellSession,
    ShellViewState,
)
from relaydeck.client.features.terminal.stream import EMULATOR_FRESH
from relaydeck.client.features.terminal.switcher import (
    ACTIVE,
    SwitcherRow,
    switcher_rows,
    working_count,
)
from relaydeck.client.features.terminal.ui_resume import TerminalUIResumes, terminal_transition
from relaydeck.client.features.terminal.visual_inset import publish, register, unregister
from relaydeck.client.features.terminal.warm_watch import fence_hidden_writer, watch_only

__all__ = [
    "ACTIVE",
    "AGENT_LIVE",
    "AgentSession",
    "CLOSE_MESSAGE",
    "CLOSE_TITLE",
    "COMPOSER_HEIGHT",
    "CONTEXT_STRIP_HEIGHT",
    "DEFAULT_GEOMETRY",
    "DetailBudget",
    "EMULATOR_FRESH",
    "FIELD_LINE_HEIGHT",
    "ForegroundRecovery",
    "KeyInputSession",
    "PHONE_DOCK_HEIGHT",
    "PHONE_FALLBACK_WIDTH",
    "PICTURE_HEIGHT",
    "ROUTE_DROPPED",
    "ROUTE_PTY",
    "SHELL_CONFLICT",
    "SHELL_CONNECTING",
    "SHELL_DISCONNECTED",
    "SHELL_EXITED",
    "SHELL_LIVE",
    "ScreenCache",
    "ScreenSnapshot",
    "SelectionState",
    "ShellSession",
    "ShellViewState",
    "SwitcherRow",
    "TAKEOVER_DISCLOSURE_AGENT",
    "TYPING_SPLIT_FALLBACK_WIDTH",
    "TYPING_SPLIT_GAP",
    "TYPING_SPLIT_HEADER_HEIGHT",
    "TerminalController",
    "TerminalRegistry",
    "TerminalUIResumes",
    "TerminalViewState",
    "WINDOW_PADDING",
    "connection_status",
    "copy_text",
    "display_width",
    "drive_recovery",
    "encode_key",
    "fence_hidden_writer",
    "keyboard_overlap",
    "measured_row_height",
    "normalize_range",
    "page_geometry",
    "plan_detail",
    "publish",
    "register",
    "resolved_range",
    "selected_offsets",
    "set_selection_error",
    "switcher_rows",
    "terminal_transition",
    "typing_split_active",
    "typing_split_lines",
    "typing_split_widths",
    "unregister",
    "view_actions",
    "watch_only",
    "working_count",
]
