"""Controlled pairing panel and command presentation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, FAMILY_MONO
from relaydeck.client.design_system.headers import ScreenHeader
from relaydeck.client.design_system.inputs import TextInput
from relaydeck.client.design_system.notices import Notice
from relaydeck.client.design_system.primitives import ActionButton, text_style
from relaydeck.client.design_system.theme import TypeStyle
from relaydeck.client.features.pairing.api import (
    OPEN_COMMAND,
    PHASE_APPROVED,
    PHASE_CONNECTING,
    PHASE_FAILED,
    PHASE_READY,
    PHASE_SETUP,
    PHASE_WAITING,
    ConnectState,
    approve_command,
    valid_request,
)
from relaydeck.client.platform.flet.events import event_text

_PAIR_TITLE = TypeStyle(size=26, weight=800, height=30 / 26)
_STEP_TITLE = TypeStyle(size=16, weight=700, height=22 / 16)
_FIELD_LABEL = TypeStyle(size=13, weight=600, height=18 / 13)
_CODE_TEXT = TypeStyle(size=22, weight=700, height=26 / 22)
_NOTE_TEXT = TypeStyle(size=13.5, weight=400, height=19 / 13.5)
_PHASE_STATUS = {
    PHASE_READY: "Request open. Complete the steps above.",
    PHASE_WAITING: "Waiting for Mac approval.",
    PHASE_APPROVED: "Approved by your Mac.",
    PHASE_CONNECTING: "Connecting.",
}

_KNOWN_PHASES = frozenset(
    {PHASE_SETUP, PHASE_READY, PHASE_WAITING, PHASE_APPROVED, PHASE_CONNECTING, PHASE_FAILED}
)


def _copy_button(text: str, key: str, label: str) -> Any:
    # Run inside the browser's tap gesture (required by iOS Safari),
    # without a server round trip or executing the displayed command.
    return Button(
        variant="text",
        key=key,
        height=T.MIN_TOUCH,
        width=88,
        tooltip=f"Copy {label}",
        action=ft.CopyToClipboard(text),
        content=ft.Text(
            "Copy", style=text_style(FAMILY_INTERFACE, T.TYPE_BUTTON, T.PRIMARY_TEXT_LIGHT)
        ),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=12),
            overlay_color={ft.ControlState.FOCUSED: T.FOCUS_WASH},
            side={ft.ControlState.FOCUSED: ft.BorderSide(2, T.TEXT)},
        ),
    )


def _command_box(text: str, key: str) -> Any:
    """Terminal-style box; full command wraps and stays selectable."""
    return ft.Container(
        key=key,
        padding=ft.Padding.symmetric(horizontal=14, vertical=12),
        border_radius=ft.BorderRadius.all(T.RADIUS_TERMINAL),
        bgcolor=T.TERMINAL_BG,
        border=ft.Border.all(1, T.BORDER),
        content=ft.Column(
            spacing=4,
            controls=[
                ft.Text(
                    text,
                    key=f"{key}-text",
                    style=text_style(FAMILY_MONO, T.TYPE_META, T.TEXT_SECONDARY),
                    selectable=True,
                ),
                ft.Row(
                    alignment=ft.MainAxisAlignment.END,
                    controls=[_copy_button(text, f"{key}-copy", "command")],
                ),
            ],
        ),
    )


def _step(number: str, title: str, body: Any) -> Any:
    return ft.Column(
        spacing=10,
        controls=[
            ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    Badge(
                        width=26,
                        height=26,
                        border_radius=ft.BorderRadius.all(13),
                        bgcolor=T.DONE,
                        alignment=ft.Alignment.CENTER,
                        content=ft.Text(
                            number,
                            style=text_style(FAMILY_INTERFACE, T.TYPE_BUTTON, T.DONE_TEXT),
                        ),
                    ),
                    ft.Text(
                        title,
                        style=text_style(FAMILY_INTERFACE, _STEP_TITLE, T.TEXT),
                        expand=True,
                    ),
                ],
            ),
            body,
        ],
    )


def connect_hint_example() -> str:
    """Placeholder for the private-address box: worded as an example so an
    empty field is never mistaken for a prefilled value."""
    return "e.g. https://your-mac.tailnet.ts.net:8445"


def _field(
    label: str,
    key: str,
    value: str,
    placeholder: str,
    on_change: Callable[[str], Any],
    disabled: bool,
) -> Any:
    return ft.Column(
        spacing=6,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        controls=[
            ft.Text(label, style=text_style(FAMILY_INTERFACE, _FIELD_LABEL, T.TEXT_SECONDARY)),
            TextInput(
                key=key,
                value=value,
                hint_text=placeholder,
                color=T.TEXT,
                text_size=13,
                hint_style=ft.TextStyle(color=T.TEXT_MUTED, font_family=FAMILY_MONO, size=13),
                filled=True,
                fill_color=T.TERMINAL_BG,
                cursor_color=T.PRIMARY_TEXT,
                border={
                    ft.ControlState.DEFAULT: ft.OutlineInputBorder(
                        border_radius=T.RADIUS_KEY, side=ft.BorderSide(1, T.BORDER)
                    ),
                    ft.ControlState.FOCUSED: ft.OutlineInputBorder(
                        border_radius=T.RADIUS_KEY, side=ft.BorderSide(2, T.PRIMARY_TEXT)
                    ),
                },
                content_padding=ft.Padding.symmetric(horizontal=14, vertical=14),
                disabled=disabled,
                on_change=lambda e: on_change(event_text(e)),
            ),
        ],
    )


def _panel_button(
    label: str,
    key: str,
    on_press: Callable[[], Any],
    *,
    primary: bool,
    disabled: bool,
) -> Any:
    return ActionButton(label, key=key, on_press=on_press, primary=primary, disabled=disabled)


def _summary_row(
    label: str, value: str, key: str, *, large: bool = False, copy: bool = False
) -> Any:
    value_text = ft.Text(
        value or "—",
        key=key,
        style=text_style(FAMILY_MONO, _CODE_TEXT if large else T.TYPE_META, T.TEXT),
        selectable=True,
        expand=True if copy else None,
    )
    return ft.Column(
        spacing=2,
        controls=[
            ft.Text(
                label.upper(),
                style=text_style(FAMILY_MONO, T.TYPE_META, T.TEXT_MUTED, 0.08),
            ),
            ft.Row(controls=[value_text, _copy_button(value, f"{key}-copy", label.lower())])
            if copy
            else value_text,
        ],
    )


def ConnectPanel(
    state: ConnectState,
    *,
    on_endpoint: Callable[[str], Any],
    on_host_id: Callable[[str], Any],
    on_label: Callable[[str], Any],
    on_begin: Callable[[], Any],
    on_cancel: Callable[[], Any],
    on_retry: Callable[[], Any],
) -> Any:
    """Declarative pairing panel; construction is not a connected product. Ready without a
    validated request keeps the input form (no empty approval placeholders); the summary and
    approve command render only with validated ids.
    """
    known = state.phase in _KNOWN_PHASES
    has_request = valid_request(state)
    show_form = (
        (not known)
        or state.phase == PHASE_SETUP
        or (state.phase == PHASE_READY and not has_request)
    )
    show_summary = known and has_request and state.phase != PHASE_SETUP
    body: list[Any] = [
        ScreenHeader("Pair a host", style=text_style(FAMILY_INTERFACE, _PAIR_TITLE, T.TEXT, -0.02)),
        _step(
            "1",
            "Open a pairing window on your Mac",
            _command_box(OPEN_COMMAND, "connect-open-command"),
        ),
    ]
    if show_form:
        body.extend(
            [
                _field(
                    "Private address",
                    "connect-endpoint",
                    state.endpoint,
                    connect_hint_example(),
                    on_endpoint,
                    state.busy,
                ),
                _field(
                    "Host ID",
                    "connect-host-id",
                    state.host_id,
                    "home-mac",
                    on_host_id,
                    state.busy,
                ),
                _field(
                    "This device label",
                    "connect-label",
                    state.device_label,
                    "pixel-8",
                    on_label,
                    state.busy,
                ),
                _panel_button(
                    "Begin pairing",
                    "connect-begin",
                    on_begin,
                    primary=True,
                    disabled=state.busy or not known,
                ),
            ]
        )
    if show_summary:
        body.extend(
            [
                _step(
                    "2",
                    "Compare the code on both devices",
                    ft.Container(
                        padding=ft.Padding.all(14),
                        border_radius=ft.BorderRadius.all(T.RADIUS_TILE),
                        bgcolor=T.CARD,
                        border=ft.Border.all(1, T.BORDER),
                        content=ft.Column(
                            spacing=10,
                            controls=[
                                _summary_row(
                                    "Request", state.request_id, "connect-request", copy=True
                                ),
                                _summary_row(
                                    "Matching code",
                                    state.matching_code,
                                    "connect-code",
                                    large=True,
                                    copy=True,
                                ),
                                _summary_row(
                                    "Host fingerprint", state.fingerprint, "connect-fingerprint"
                                ),
                                _summary_row("Scope", state.scope, "connect-scope"),
                                _summary_row("Expires", state.expiry, "connect-expiry"),
                            ],
                        ),
                    ),
                ),
                _step(
                    "3",
                    "Approve on your Mac, then connect",
                    _command_box(approve_command(state), "connect-approve-command"),
                ),
            ]
        )
    if known and state.phase == PHASE_FAILED:
        body.append(
            _panel_button(
                "Retry pairing",
                "connect-retry",
                on_retry,
                primary=True,
                disabled=state.busy,
            )
        )
    if known and state.phase == PHASE_CONNECTING:
        body.append(ft.ProgressBar(key="connect-progress"))
    if known and state.phase in _PHASE_STATUS and (show_summary or state.phase != PHASE_READY):
        body.append(
            ft.Text(
                _PHASE_STATUS[state.phase],
                key="connect-status",
                style=text_style(FAMILY_INTERFACE, T.TYPE_CARD_LINE, T.TEXT_SECONDARY),
            )
        )
    if (
        known
        and state.phase != PHASE_SETUP
        and (
            has_request
            or state.phase in (PHASE_WAITING, PHASE_APPROVED, PHASE_CONNECTING, PHASE_FAILED)
        )
    ):
        body.append(
            _panel_button(
                "Cancel",
                "connect-cancel",
                on_cancel,
                primary=False,
                disabled=state.busy and state.phase != PHASE_FAILED,
            )
        )
    if state.message:
        body.append(
            ft.Text(
                state.message,
                key="connect-message",
                style=text_style(FAMILY_INTERFACE, T.TYPE_CARD_LINE, T.TEXT_SECONDARY),
            )
        )
    body.append(
        Notice(
            key="connect-note",
            padding=ft.Padding.all(14),
            border_radius=ft.BorderRadius.all(16),
            bgcolor=T.CARD,
            border=ft.Border.all(1, T.BORDER),
            content=ft.Row(
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Icon(ft.Icons.INFO, size=22, color=T.WORKING),
                    ft.Text(
                        "Pairing grants this device scoped access to one host. "
                        "Approve only a code you compare on your Mac. Closing "
                        "the pairing window invalidates unused approvals.",
                        style=text_style(FAMILY_INTERFACE, _NOTE_TEXT, T.TEXT_SECONDARY),
                        expand=True,
                    ),
                ],
            ),
        )
    )
    # The page owns scrolling. A second scrollable here leaves the form
    # unconstrained inside the web/Android page's scrolling column.
    return ft.Container(
        key="connect-panel-frame",
        alignment=ft.Alignment.TOP_CENTER,
        padding=ft.Padding.all(16),
        content=ft.Container(
            key="connect-form",
            width=680,
            content=ft.Column(
                key="connect-panel",
                controls=body,
                spacing=18,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        ),
    )
