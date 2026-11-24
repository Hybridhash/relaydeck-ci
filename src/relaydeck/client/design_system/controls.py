"""Generic controls: setting rows and panels, toggles, stepper, segmented switch, pill button,
state pill and notice panel, with the type styles and helpers they share.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, FAMILY_MONO
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.design_system.notices import Notice
from relaydeck.client.design_system.primitives import action_button, text_style
from relaydeck.client.design_system.theme import TypeStyle

# -- window state words -------------------------------------------------------
STATE_WATCHING = "watching"
STATE_IN_CONTROL = "in-control"
STATE_CONNECTING = "connecting"
STATE_DISCONNECTED = "disconnected"
STATE_EXITED = "exited"

STATE_LABELS = {
    STATE_WATCHING: "Watching",
    STATE_IN_CONTROL: "Live · in control",
    STATE_CONNECTING: "Connecting",
    STATE_DISCONNECTED: "Disconnected",
    STATE_EXITED: "Exited",
}
TYPE_STATE_PILL = TypeStyle(size=12, weight=800, height=28 / 12)
TYPE_ROW_TITLE = TypeStyle(size=15, weight=600, height=20 / 15)
TYPE_ROW_HELP = TypeStyle(size=13, weight=400, height=18 / 13)
TYPE_PANEL_TITLE = TypeStyle(size=20, weight=800, height=26 / 20)
TYPE_PROMPT_TITLE = TypeStyle(size=18, weight=800, height=24 / 18)
TYPE_BODY = TypeStyle(size=14, weight=400, height=20 / 14)

# "#E9E5FA" is the mock's terminal foreground and Live label.
TERMINAL_TEXT = T.TERMINAL_TEXT
WHITE = T.ON_PRIMARY


def disable[ControlT: ft.Control](control: ControlT, disabled: bool) -> ControlT:
    control.disabled = disabled
    return control


def StatePill(state: str, key: str | None = None, *, compact: bool = False) -> Any:
    """Header pill for the window state: lime when in control, outlined
    otherwise. Dark text on lime (never white on lime)."""
    label = STATE_LABELS.get(state, state)
    if compact and state == STATE_IN_CONTROL:
        label = "Live"
    live = state == STATE_IN_CONTROL
    return Badge(
        key=key,
        padding=ft.Padding.symmetric(horizontal=8 if compact else 12),
        border_radius=ft.BorderRadius.all(14),
        bgcolor=T.DONE if live else T.RAISED,
        border=None if live else ft.Border.all(1, T.BORDER_DASHED_OFFLINE),
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            label.upper(),
            style=text_style(
                FAMILY_INTERFACE, TYPE_STATE_PILL, T.DONE_TEXT if live else TERMINAL_TEXT, 0.05
            ),
            no_wrap=True,
        ),
    )


def PillButton(
    label: str,
    on_press: Callable[[], Any],
    *,
    key: str | None = None,
    tone: str = "ghost",
    icon: ft.IconData | None = None,
    disabled: bool = False,
    tooltip: str | None = None,
    selected: bool = False,
) -> Any:
    """Fully rounded 48px button. tones: "ghost" (card fill, border), "outline" (violet outline,
    light violet text: Disconnect, Copy), "primary" (violet fill, white text).
    """
    if tone == "primary":
        fill, ink, border = T.PRIMARY_FILL, WHITE, None
    elif tone == "outline":
        fill, ink, border = None, T.PRIMARY_TEXT_LIGHT, ft.Border.all(1, T.PRIMARY_TEXT)
    else:
        fill = T.RAISED if selected else T.CARD
        ink = T.TEXT
        border = ft.Border.all(1, T.PRIMARY_TEXT if selected else T.BORDER)
    if disabled:
        ink = T.TEXT_TERMINAL_MUTED
    weight = T.TYPE_BUTTON if tone != "ghost" else T.TYPE_BUTTON_IDLE
    body: list[Any] = []
    if icon is not None:
        body.append(ft.Icon(icon, size=18, color=ink))
    body.append(ft.Text(label, style=text_style(FAMILY_INTERFACE, weight, ink), no_wrap=True))
    button = action_button(
        key=key,
        height=T.MIN_TOUCH,
        radius=24,
        tooltip=tooltip or label,
        on_press=on_press,
        content=ft.Container(
            padding=ft.Padding.symmetric(horizontal=16),
            border_radius=ft.BorderRadius.all(24),
            bgcolor=fill,
            border=border,
            alignment=ft.Alignment.CENTER,
            content=ft.Row(tight=True, spacing=8, controls=body),
        ),
    )
    return disable(button, disabled)


def SegmentedSwitch(
    options: tuple[tuple[str, str, str], ...],
    value: str,
    on_change: Callable[[str], Any],
    *,
    key: str | None = None,
    well: str = T.CARD,
    disabled: bool = False,
) -> ft.Container:
    """Compose | Direct style switch: 16px well, violet selected segment. `options` are (value,
    label, control key).
    """

    def press(option: str, selected: bool) -> Callable[[], Any]:
        if selected:
            return lambda: None
        return lambda: on_change(option)

    segments: list[ft.Control] = []
    for option, label, option_key in options:
        selected = option == value
        segment = action_button(
            key=option_key,
            height=T.MIN_TOUCH,
            radius=12,
            tooltip=f"{label} selected" if selected else label,
            on_press=press(option, selected),
            content=ft.Container(
                padding=ft.Padding.symmetric(horizontal=18),
                border_radius=ft.BorderRadius.all(12),
                bgcolor=T.PRIMARY_FILL if selected else None,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    label,
                    style=text_style(
                        FAMILY_INTERFACE,
                        T.TYPE_BUTTON if selected else T.TYPE_BUTTON_IDLE,
                        WHITE if selected else T.TEXT_SECONDARY,
                    ),
                    no_wrap=True,
                ),
            ),
        )
        segment.data = {"selected": selected}
        # Disable the selected segment while preserving its active styling.
        segments.append(disable(segment, disabled or selected))
    return ft.Container(
        key=key,
        padding=4,
        border_radius=ft.BorderRadius.all(16),
        bgcolor=well,
        border=ft.Border.all(1, T.BORDER),
        content=ft.Row(tight=True, spacing=4, controls=segments),
    )


def prompt_body(texts: Any, actions: list[Any], phone: bool) -> Any:
    """Text block + actions: side by side on web, stacked on a phone.
    (No flex child inside a wrapping row: Flutter's Wrap rejects it.)"""
    buttons = ft.Row(key="terminal-prompt-actions", tight=True, spacing=8, controls=actions)
    if phone:
        return ft.Column(spacing=14, tight=True, controls=[texts, buttons])
    return ft.Row(
        spacing=20,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[ft.Container(expand=True, content=texts), buttons],
    )


def NoticePanel(
    *,
    title: str,
    message: str,
    actions: list[Any],
    key: str,
    phone: bool = False,
) -> Any:
    """Bottom-area notice (Disconnected / Exited / Close confirm): same
    frame as the take-over panel, neutral border."""
    return Notice(
        key=key,
        padding=ft.Padding.symmetric(horizontal=18, vertical=16),
        border_radius=ft.BorderRadius.all(20),
        bgcolor=T.CARD,
        border=ft.Border.all(1, T.BORDER),
        content=prompt_body(
            ft.Column(
                spacing=4,
                tight=True,
                controls=[
                    ft.Text(
                        title,
                        key=f"{key}-title",
                        style=text_style(FAMILY_INTERFACE, TYPE_PROMPT_TITLE, T.TEXT),
                    ),
                    *(
                        [
                            ft.Text(
                                message,
                                key=f"{key}-message",
                                style=text_style(FAMILY_INTERFACE, TYPE_BODY, T.TEXT_SECONDARY),
                            )
                        ]
                        if message
                        else []
                    ),
                ],
            ),
            actions,
            phone,
        ),
    )


# -- settings controls --------------------------------------------------------


def ToggleSwitch(
    value: bool,
    on_change: Callable[[bool], Any],
    *,
    label: str,
    key: str,
) -> ft.TextButton:
    """52x32 switch: lime track + dark knob right when on; dark bordered
    track + muted knob left when off."""
    knob = ft.Container(
        width=24,
        height=24,
        border_radius=ft.BorderRadius.all(12),
        bgcolor=T.DONE_TEXT if value else T.TEXT_MUTED,
    )
    track = ft.Container(
        width=52,
        height=32,
        padding=4 if value else 3,
        border_radius=ft.BorderRadius.all(16),
        bgcolor=T.DONE if value else T.RAISED,
        border=None if value else ft.Border.all(1, T.BORDER_DASHED_OFFLINE),
        alignment=ft.Alignment.CENTER_RIGHT if value else ft.Alignment.CENTER_LEFT,
        content=knob,
    )
    button = action_button(
        key=key,
        width=56,
        height=T.MIN_TOUCH,
        radius=16,
        tooltip=f"{label}: {'on' if value else 'off'}",
        on_press=lambda: on_change(not value),
        content=ft.Container(alignment=ft.Alignment.CENTER, content=track),
    )
    button.data = {"checked": value}
    return button


def SettingRow(
    title: str,
    control: Any,
    *,
    help_text: str = "",
    first: bool = False,
    key: str | None = None,
    mono_help: bool = False,
) -> Any:
    """One settings row: title (+ helper text) left, control right."""
    texts: list[Any] = [
        ft.Text(title, style=text_style(FAMILY_INTERFACE, TYPE_ROW_TITLE, T.TEXT)),
    ]
    if help_text:
        texts.append(
            ft.Text(
                help_text,
                style=text_style(
                    FAMILY_MONO if mono_help else FAMILY_INTERFACE,
                    T.TYPE_META if mono_help else TYPE_ROW_HELP,
                    T.TEXT_MUTED,
                ),
                max_lines=2,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
        )
    return ft.Container(
        key=key,
        padding=ft.Padding.symmetric(vertical=8),
        border=None if first else ft.Border(top=ft.BorderSide(1, T.RAISED)),
        content=ft.Row(
            spacing=16,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Column(spacing=2, tight=True, expand=True, controls=texts),
                control,
            ],
        ),
    )


def SettingsPanel(title: str, rows: list[Any], *, key: str, phone: bool = False) -> Any:
    """Web: titled panel. Phone: small mono label over a stacked card."""
    if phone:
        return ft.Column(
            key=key,
            spacing=6,
            controls=[
                ScreenHeader(
                    title.upper(),
                    style=text_style(FAMILY_MONO, T.TYPE_META, T.TEXT_MUTED, 0.08),
                ),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=14, vertical=4),
                    border_radius=ft.BorderRadius.all(T.RADIUS_TILE),
                    bgcolor=T.CARD,
                    border=ft.Border.all(1, T.BORDER),
                    content=ft.Column(spacing=0, controls=rows),
                ),
            ],
        )
    return ft.Container(
        key=key,
        padding=ft.Padding.only(left=22, right=22, top=20, bottom=12),
        border_radius=ft.BorderRadius.all(T.RADIUS_PANE),
        bgcolor=T.PANEL,
        border=ft.Border.all(1, T.BORDER),
        content=ft.Column(
            spacing=0,
            controls=[
                ft.Container(
                    padding=ft.Padding.only(bottom=8),
                    content=ScreenHeader(
                        title, style=text_style(FAMILY_INTERFACE, TYPE_PANEL_TITLE, T.TEXT)
                    ),
                ),
                *rows,
            ],
        ),
    )


def Stepper(
    value_label: str,
    on_minus: Callable[[], Any],
    on_plus: Callable[[], Any],
    *,
    key: str,
    can_minus: bool = True,
    can_plus: bool = True,
) -> ft.Row:
    """Minus / value / plus stepper (terminal text size)."""

    def round_button(
        icon: ft.IconData, label: str, action: Callable[[], Any], ok: bool
    ) -> ft.TextButton:
        button = action_button(
            key=f"{key}-{'minus' if icon == ft.Icons.REMOVE else 'plus'}",
            width=T.MIN_TOUCH,
            height=T.MIN_TOUCH,
            radius=24,
            tooltip=label,
            on_press=action,
            content=ft.Container(
                border_radius=ft.BorderRadius.all(24),
                bgcolor=T.CARD,
                border=ft.Border.all(1, T.BORDER),
                alignment=ft.Alignment.CENTER,
                content=ft.Icon(icon, size=18, color=T.TEXT if ok else T.TEXT_TERMINAL_MUTED),
            ),
        )
        return disable(button, not ok)

    return ft.Row(
        key=key,
        tight=True,
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            round_button(ft.Icons.REMOVE, "Smaller text", on_minus, can_minus),
            ft.Container(
                width=56,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    value_label,
                    key=f"{key}-value",
                    style=text_style(FAMILY_MONO, T.TYPE_BUTTON, T.TEXT),
                ),
            ),
            round_button(ft.Icons.ADD, "Larger text", on_plus, can_plus),
        ],
    )


__all__ = [
    "STATE_CONNECTING",
    "STATE_DISCONNECTED",
    "STATE_EXITED",
    "STATE_IN_CONTROL",
    "STATE_LABELS",
    "STATE_WATCHING",
    "NoticePanel",
    "PillButton",
    "SegmentedSwitch",
    "SettingRow",
    "SettingsPanel",
    "StatePill",
    "Stepper",
    "ToggleSwitch",
]
