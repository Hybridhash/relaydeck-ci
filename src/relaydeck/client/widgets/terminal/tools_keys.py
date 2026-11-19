"""Declarative terminal tools-key dock."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Any

import flet as ft

from relaydeck.client.design_system.appearance import chrome_style
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.controls import PillButton, SegmentedSwitch
from relaydeck.client.design_system.input_chrome import mode_button
from relaydeck.client.design_system.theme import MIN_TOUCH, palette
from relaydeck.client.models.input.keys import MOD_ALT as MOD_ALT
from relaydeck.client.models.input.keys import MOD_CTRL as MOD_CTRL
from relaydeck.client.models.input.keys import MOD_SHIFT as MOD_SHIFT
from relaydeck.client.models.input.keys import MODIFIERS as MODIFIERS
from relaydeck.client.models.input.keys import SHIFT_ENTER as SHIFT_ENTER
from relaydeck.client.models.input.keys import SHIFT_TAB as SHIFT_TAB
from relaydeck.client.platform.flet.events import click
from relaydeck.client.widgets.terminal.window import KEY_ROW, KeyButton

# -- declarative tools-keys dock -----------------------------------------------

KEYS_DOCK_KEY = "tools-keys-dock"
KEYS_TITLE_KEY = "tools-keys-title"
KEYS_MOD_ROW_KEY = "tools-keys-modifiers"
KEYS_DISMISS_KEY = "tools-keys-dismiss"

DOCK_TITLE = "Keys"
TEXT_LABEL = "Text"

_MOD_LABELS: dict[str, str] = {MOD_CTRL: "Ctrl", MOD_ALT: "Alt", MOD_SHIFT: "Shift"}
_MOD_ACCESSIBILITY: dict[str, str] = {
    MOD_CTRL: "Control modifier",
    MOD_ALT: "Option modifier",
    MOD_SHIFT: "Shift modifier",
}

DOCK_KEYS: tuple[tuple[str, str, str], ...] = (
    # Keep canonical key names, visible labels and accessibility labels together.
    # Use ASCII where bundled fonts lack key symbols.
    ("escape", "Esc", "Escape"),
    ("tab", "Tab", "Tab"),
    (SHIFT_TAB, "Shift Tab", "Shift Tab"),
    (SHIFT_ENTER, "Shift Enter", "Shift Enter"),
    ("left", "\u2190", "Left Arrow"),
    ("up", "\u2191", "Up Arrow"),
    ("down", "\u2193", "Down Arrow"),
    ("right", "\u2192", "Right Arrow"),
    ("enter", "Enter", "Enter"),
    ("backspace", "Backspace", "Backspace"),
    ("home", "Home", "Home"),
    ("end", "End", "End"),
    ("insert", "Ins", "Insert"),
    ("delete", "Del", "Forward Delete"),
    ("page-up", "PgUp", "Page Up"),
    ("page-down", "PgDn", "Page Down"),
    ("f1", "F1", "F1"),
    ("f2", "F2", "F2"),
    ("f3", "F3", "F3"),
    ("f4", "F4", "F4"),
    ("f5", "F5", "F5"),
    ("f6", "F6", "F6"),
    ("f7", "F7", "F7"),
    ("f8", "F8", "F8"),
    ("f9", "F9", "F9"),
    ("f10", "F10", "F10"),
    ("f11", "F11", "F11"),
    ("f12", "F12", "F12"),
)

_DOCK_ROWS: tuple[tuple[str, ...], ...] = (
    ("escape", "tab", SHIFT_TAB, SHIFT_ENTER),
    ("home", "end", "insert", "delete", "page-up", "page-down"),
    ("f1", "f2", "f3", "f4", "f5", "f6"),
    ("f7", "f8", "f9", "f10", "f11", "f12"),
    ("left", "up", "down", "right", "enter", "backspace"),
)

_DOCK_LABELS: dict[str, tuple[str, str]] = {
    name: (label, access) for name, label, access in DOCK_KEYS
}


def key_button_key(name: str) -> str:
    return f"tools-key-{name}"


def modifier_button_key(modifier: str) -> str:
    return f"tools-mod-{modifier}"


def modifier_button_label(modifier: str, armed: bool) -> str:
    """Armed modifiers show a filled marker."""
    label = _MOD_LABELS[modifier]
    return f"\u25cf {label}" if armed else label


@ft.component
def ToolsKeysDock(
    armed: frozenset[str],
    enabled: bool,
    on_key: Callable[[str], Any],
    on_toggle_modifier: Callable[[str], Any],
    on_dismiss: Callable[[], Any],
    presentation: str = "full",
    dark: bool = True,
    max_height: float = 192,
    dedupe_inline: bool = False,
    compact: bool = False,
) -> Any:
    """Fully controlled tools keyboard: immutable props plus callbacks, no sockets, no timers,
    no direct session access. Keys target the live terminal even when a composer draft
    exists; the session (not this component) binds generations and clears one-shot modifiers.
    """
    page, set_page = ft.use_state("agent" if presentation == "agent" else "terminal")
    tokens = palette(dark)
    if presentation not in ("full", "agent", "terminal"):
        raise ValueError("unknown tools presentation")
    if presentation != "full":
        # The primary row, in the same order as the phone/expanded key row.
        agent_keys = (
            "escape",
            "tab",
            "shift-tab",
            "left",
            "up",
            "down",
            "right",
            "backspace",
            "enter",
        )
        names = (
            agent_keys
            if presentation == "agent" and page == "agent"
            else tuple(name for name, _, _ in DOCK_KEYS)
        )
        if dedupe_inline or compact:
            inline = frozenset() if compact else frozenset(name for name, _, _ in KEY_ROW)
            extra = tuple(name for name, _, _ in DOCK_KEYS if name not in inline)
            if presentation == "agent" and page == "agent":
                # The agent quick set duplicates the inline row key for
                # key; fall back to the same deduped set so the page is
                # never empty (modifiers stay terminal-only below).
                names = tuple(name for name in agent_keys if name not in inline) or extra
            else:
                names = extra
            # Flet 1.0.3: design-system buttons directly inside a Wrap break
            # layout (grey error box). Like KeyRow, the Wrap holds plain
            # tight Row groups; the buttons live inside those groups.
            buttons = [
                KeyButton(name, _DOCK_LABELS[name][0], None, on_key, enabled) for name in names
            ]
            groups: list[Any] = [
                ft.Row(tight=True, spacing=6, controls=buttons[i : i + 4])
                for i in range(0, len(buttons), 4)
            ]
            grid: list[Any] = [
                ft.Row(
                    key="tools-keys-deduped", wrap=True, spacing=10, run_spacing=8, controls=groups
                )
            ]
            if page == "terminal" or presentation == "terminal":
                grid.append(
                    ft.Row(
                        key=KEYS_MOD_ROW_KEY,
                        tight=True,
                        spacing=6,
                        controls=[
                            PillButton(
                                modifier_button_label(mod, mod in armed),
                                partial(on_toggle_modifier, mod),
                                key=modifier_button_key(mod),
                                tooltip=_MOD_ACCESSIBILITY[mod],
                                disabled=not enabled,
                                selected=mod in armed,
                            )
                            for mod in (MOD_CTRL, MOD_ALT, MOD_SHIFT)
                        ],
                    )
                )
            grid.append(PillButton(TEXT_LABEL, on_dismiss, key=KEYS_DISMISS_KEY))
            tabs: list[ft.Control] = (
                [
                    SegmentedSwitch(
                        (
                            ("agent", "Agent", "tools-page-agent"),
                            ("terminal", "Terminal", "tools-page-terminal"),
                        ),
                        page,
                        set_page,
                        key="tools-page-switch",
                    )
                ]
                if presentation == "agent"
                else []
            )
        else:
            grid = []
            for offset in range(0, len(names), 3):
                grid.append(
                    ft.Row(
                        spacing=6,
                        controls=[
                            Button(
                                _DOCK_LABELS[name][0],
                                key=key_button_key(name),
                                tooltip=_DOCK_LABELS[name][1],
                                disabled=not enabled,
                                height=MIN_TOUCH,
                                expand=True,
                                style=chrome_style(dark),
                                on_click=click(on_key, name),
                            )
                            for name in names[offset : offset + 3]
                        ],
                    )
                )
            if page == "terminal" or presentation == "terminal":
                grid.append(
                    ft.Row(
                        spacing=6,
                        controls=[
                            Button(
                                modifier_button_label(mod, mod in armed),
                                key=modifier_button_key(mod),
                                tooltip=_MOD_ACCESSIBILITY[mod],
                                disabled=not enabled,
                                height=MIN_TOUCH,
                                expand=True,
                                style=chrome_style(dark),
                                on_click=click(on_toggle_modifier, mod),
                            )
                            for mod in (MOD_CTRL, MOD_ALT, MOD_SHIFT)
                        ],
                    )
                )
            grid.append(
                Button(
                    TEXT_LABEL,
                    key=KEYS_DISMISS_KEY,
                    height=MIN_TOUCH,
                    style=chrome_style(dark),
                    on_click=click(on_dismiss),
                )
            )
            tabs = (
                [
                    ft.Row(
                        spacing=6,
                        controls=[
                            mode_button(
                                "Agent",
                                "tools-page-agent",
                                page == "agent",
                                lambda e: set_page("agent"),
                                dark,
                            ),
                            mode_button(
                                "Terminal",
                                "tools-page-terminal",
                                page == "terminal",
                                lambda e: set_page("terminal"),
                                dark,
                            ),
                        ],
                    )
                ]
                if presentation == "agent"
                else []
            )
        if compact and not dedupe_inline:
            # Overlay presentation: natural height; the overlay scrolls.
            return ft.Container(
                key=KEYS_DOCK_KEY,
                bgcolor=tokens.surface,
                padding=6,
                content=ft.Column(
                    key="tools-grid-scroll", tight=True, spacing=6, controls=[*grid, *tabs]
                ),
            )
        return ft.Container(
            key=KEYS_DOCK_KEY,
            bgcolor=tokens.surface,
            height=max_height if dedupe_inline or compact else max(48, max_height),
            padding=6,
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Column(
                        key="tools-grid-scroll",
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                        spacing=6,
                        controls=grid,
                    ),
                    *tabs,
                ],
            ),
        )
    rows: list[Any] = [ft.Text(DOCK_TITLE, key=KEYS_TITLE_KEY, color=tokens.text)]
    for row in _DOCK_ROWS:
        controls: list[Any] = []
        for name in row:
            label, access = _DOCK_LABELS[name]
            controls.append(
                Button(
                    label,
                    key=key_button_key(name),
                    tooltip=access,
                    disabled=not enabled,
                    height=MIN_TOUCH,
                    style=chrome_style(dark),
                    on_click=click(on_key, name),
                )
            )
        rows.append(ft.Row(key=f"tools-keys-row-{row[0]}", wrap=True, controls=controls))
    mod_controls: list[Any] = []
    for modifier in (MOD_CTRL, MOD_ALT, MOD_SHIFT):
        mod_controls.append(
            Button(
                modifier_button_label(modifier, modifier in armed),
                key=modifier_button_key(modifier),
                tooltip=_MOD_ACCESSIBILITY[modifier],
                disabled=not enabled,
                height=MIN_TOUCH,
                style=chrome_style(dark),
                on_click=click(on_toggle_modifier, modifier),
            )
        )
    mod_controls.append(
        Button(
            TEXT_LABEL,
            key=KEYS_DISMISS_KEY,
            height=MIN_TOUCH,
            style=chrome_style(dark),
            on_click=click(on_dismiss),
        )
    )
    rows.append(ft.Row(key=KEYS_MOD_ROW_KEY, wrap=True, controls=mod_controls))
    return ft.Column(key=KEYS_DOCK_KEY, controls=rows)
