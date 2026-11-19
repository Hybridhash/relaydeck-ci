"""Synthetic props and callbacks for existing design-system controls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from relaydeck.client.design_system import controls as C
from relaydeck.client.design_system import primitives as P
from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.accessibility.announcer import LiveRegion
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.buttons import Button, ButtonVariant
from relaydeck.client.design_system.dialogs import Dialog
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE
from relaydeck.client.design_system.headers import ScreenHeader, SectionHeader
from relaydeck.client.design_system.input_chrome import mode_button
from relaydeck.client.design_system.inputs import FilterField, SearchField, TextInput
from relaydeck.client.design_system.lists.virtual_list import PlacedListView, Row, VirtualList
from relaydeck.client.design_system.loading import LoadingBars
from relaydeck.client.design_system.loading_spinner import Spinner
from relaydeck.client.design_system.navigation import ConsoleBottomBar, ConsoleRail
from relaydeck.client.design_system.notices import Notice, message_box
from relaydeck.client.design_system.sheets import Sheet
from relaydeck.client.design_system.stat_tiles import StatTile
from relaydeck.client.design_system.status import StatusDot
from relaydeck.client.design_system.surfaces import MutedText, SurfaceFrame
from relaydeck.client.design_system.theme import palette
from relaydeck.client.design_system.toast import ActionToast
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.design_system.typography import ui
from relaydeck.devtools.catalog_model import Case


@dataclass(frozen=True)
class Sample:
    case: Case
    value: str
    checked: bool
    selected: str
    count: int
    on_value: Callable[[str], Any]
    on_checked: Callable[[bool], Any]
    on_selected: Callable[[str], Any]
    on_step: Callable[[int], Any]
    on_action: Callable[[], Any]

    @property
    def button(self) -> Any:
        return C.PillButton("Continue", self.on_action, key="sample-action")


def build(sample: Sample) -> Any:
    c = sample.case
    key, label = "sample-control", c.label
    disabled, error = c.state == "disabled", c.state == "error"
    t = palette(c.dark)
    statuses = {
        "normal": "working",
        "focus": "done",
        "error": "blocked",
        "disabled": "unknown",
        "loading": "idle",
        "long-text": "working",
    }
    destinations = (
        ("overview", label, ft.Icons.HOME_OUTLINED),
        ("history", "History", ft.Icons.HISTORY),
        ("preferences", "Preferences", ft.Icons.SETTINGS_OUTLINED),
    )
    match c.entry:
        case "spinner":
            return Spinner(
                key=key,
                value=0.5,
                semantics_label=label,
                semantics_value=50,
                color=t.accent_text,
                width=28,
                height=28,
                stroke_width=2,
            )
        case "button":
            variants: tuple[ButtonVariant, ...] = ("standard", "text", "filled")
            return ft.Column(
                key=key,
                controls=[
                    Button(
                        label,
                        variant=button_variant,
                        key=f"sample-{button_variant}",
                        disabled=disabled,
                        height=T.MIN_TOUCH,
                        tooltip=label,
                        style=ft.ButtonStyle(
                            color=t.text,
                            bgcolor=t.surface,
                            shape=ft.RoundedRectangleBorder(radius=T.RADIUS_KEY),
                            side={
                                ft.ControlState.DEFAULT: ft.BorderSide(1, t.line),
                                ft.ControlState.FOCUSED: ft.BorderSide(3, t.accent_text),
                            },
                        ),
                        on_click=lambda event: sample.on_action(),
                    )
                    for button_variant in variants
                ],
            )
        case "text-input" | "search-field":
            editor_builder = TextInput if c.entry == "text-input" else SearchField
            return editor_builder(
                key=key,
                value=sample.value,
                label=label,
                hint_text="Search examples",
                color=t.text,
                fill_color=t.surface,
                filled=True,
                cursor_color=t.accent_text,
                label_style=ft.TextStyle(color=t.muted),
                hint_style=ft.TextStyle(color=t.muted),
                content_padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                size_constraints=ft.BoxConstraints(min_height=48),
                border=ft.OutlineInputBorder(border_radius=T.RADIUS_KEY),
                prefix_icon=ft.Icons.SEARCH if c.entry == "search-field" else None,
                disabled=disabled,
                error="Example validation error" if error else None,
                error_style=ft.TextStyle(color=t.danger),
                on_change=lambda event: sample.on_value(str(event.control.value or "")),
                on_submit=lambda event: sample.on_action(),
            )
        case "notice":
            return Notice(
                key=key,
                content=ft.Column(
                    spacing=10,
                    controls=[
                        ft.Text(label, color=t.text),
                        C.PillButton(
                            "Continue", sample.on_action, key="sample-action", disabled=disabled
                        ),
                    ],
                ),
                padding=16,
                border_radius=T.RADIUS_TILE,
                bgcolor=t.surface,
                border=ft.Border.all(1, t.line),
            )
        case "stat-tile":
            return StatTile(
                key=key,
                content=ft.Column(
                    spacing=4,
                    controls=[ft.Text(label, color=t.muted), ft.Text("123", color=t.text)],
                ),
                padding=ft.Padding.all(12),
                border_radius=T.RADIUS_TILE,
                bgcolor=t.surface,
                border=ft.Border.all(1, t.line),
            )
        case "badge":
            return Badge(
                key=key,
                content=ft.Text(label, color=t.text, no_wrap=True),
                padding=ft.Padding.symmetric(horizontal=10, vertical=4),
                border_radius=12,
                bgcolor=t.surface,
                border=ft.Border.all(1, t.line),
                alignment=ft.Alignment.CENTER,
            )
        case "screen-header":
            return ScreenHeader(
                label, key=key, style=P.text_style(FAMILY_INTERFACE, T.TYPE_TITLE, t.text, -0.02)
            )
        case "dialog":
            return Dialog(
                key=key,
                open=True,
                title=ft.Text(label),
                content=ft.Semantics(
                    label="Catalog specimen " + c.key,
                    content=ft.Text("Example supporting text"),
                ),
                bgcolor=t.surface,
                radius=24,
                actions=[
                    C.PillButton(
                        "Continue", sample.on_action, key="sample-action", disabled=disabled
                    )
                ],
                on_dismiss=lambda event: sample.on_action(),
            )
        case "sheet":
            return Sheet(
                ft.Semantics(
                    label="Catalog specimen " + c.key,
                    content=ft.Column(
                        controls=[
                            ft.Text(label),
                            C.PillButton(
                                "Continue", sample.on_action, key="sample-action", disabled=disabled
                            ),
                        ],
                        tight=True,
                    ),
                ),
                key=key,
                open=True,
                tokens=usage_palette(c.dark),
                padding=ft.Padding.symmetric(horizontal=16, vertical=12),
                on_dismiss=lambda event: sample.on_action(),
                scrollable=True,
            )
        case "state-pill":
            state = {
                "normal": "in-control",
                "focus": "watching",
                "error": "disconnected",
                "disabled": "exited",
                "loading": "connecting",
                "long-text": label,
            }[c.state]
            return C.StatePill(state, key=key)
        case "pill-button":
            tone = "primary" if c.state == "normal" else "outline" if error else "ghost"
            return C.PillButton(
                label,
                sample.on_action,
                key=key,
                tone=tone,
                disabled=disabled,
                selected=c.state == "focus",
                icon=ft.Icons.ADD,
            )
        case "segmented-switch":
            return C.SegmentedSwitch(
                (("first", label, "sample-first"), ("second", "Second", "sample-second")),
                sample.selected,
                sample.on_selected,
                key=key,
                disabled=disabled,
            )
        case "prompt-body":
            return C.prompt_body(ft.Text(label), [sample.button], c.width == 320)
        case "notice-panel":
            return C.NoticePanel(
                title=label,
                message="Example supporting text",
                actions=[sample.button],
                key=key,
                phone=c.width == 320,
            )
        case "message-box":
            return ft.Container(
                key="sample-message-host",
                height=240,
                content=message_box(label, [sample.button], key=key),
            )
        case "toggle-switch":
            return C.ToggleSwitch(sample.checked, sample.on_checked, label=label, key=key)
        case "setting-row":
            return C.SettingRow(label, sample.button, help_text="Example supporting text", key=key)
        case "settings-panel":
            return C.SettingsPanel(
                label,
                [C.SettingRow("Example setting", sample.button, first=True)],
                key=key,
                phone=c.width == 320,
            )
        case "stepper":
            value = label if c.state == "long-text" else str(sample.count)
            return C.Stepper(
                value,
                lambda: sample.on_step(-1),
                lambda: sample.on_step(1),
                key=key,
                can_minus=not disabled,
                can_plus=not disabled,
            )
        case "action-carrier":
            return P.action_button(
                key=key,
                content=ft.Text(label),
                on_press=sample.on_action,
                radius=12,
                height=48,
                tooltip=label,
            )
        case "status-pill":
            return P.StatusPill(
                statuses[c.state],
                label=label if c.state == "long-text" else None,
                key=key,
                inverted=error,
            )
        case "count-tile":
            return P.CountTile(statuses[c.state], sample.count, key=key)
        case "round-icon-button":
            return P.RoundIconButton(
                ft.Icons.ADD,
                label,
                sample.on_action,
                key=key,
                tone="lime" if c.state == "normal" else "ghost",
            )
        case "dotted-ground":
            return ft.Container(height=180, content=P.DottedGround(ft.Text(label)))
        case "section-header":
            return SectionHeader("Example section", label, key=key)
        case "action-button":
            return P.ActionButton(
                label,
                key=key,
                on_press=sample.on_action,
                primary=c.state == "normal",
                disabled=disabled,
            )
        case "filter-field":
            field = FilterField(sample.value, sample.on_value, hint="Search examples", key=key)
            field.error_text = "Example validation error" if error else None
            return field
        case "mode-button":
            return mode_button(
                label, key, sample.checked, lambda _e: sample.on_checked(not sample.checked), c.dark
            )
        case "loading-bars":
            return LoadingBars(color=t.accent_text, reduce_motion=True, key=key)
        case "bottom-bar" | "console-rail":
            builder = ConsoleBottomBar if c.entry == "bottom-bar" else ConsoleRail
            return builder(
                sample.selected,
                sample.on_selected,
                destinations=destinations,
                settings_id="preferences",
                settings_dot=error,
            )
        case "status-dot":
            return StatusDot(statuses[c.state], dark=c.dark)
        case "surface-frame":
            return SurfaceFrame(key, ft.Text(label), tokens=t, disabled=disabled)
        case "muted-text":
            return MutedText(label, key=key, tokens=t)
        case "action-toast":
            toast = ActionToast(
                key, label, "Retry", sample.on_action, sample.on_action, error=error
            )
            toast.disabled = disabled
            toast.action.key = "sample-toast-action"
            return toast
        case "interface-text":
            return ui(label, color=t.text, key=key, label=label)
        case "live-region":
            return LiveRegion(label)
        case "placed-list" | "virtual-list":

            def row(index: int) -> Any:
                return P.action_button(
                    key=f"sample-row-{index}",
                    content=ft.Text(f"{label} {index + 1}"),
                    on_press=sample.on_action,
                    radius=8,
                    height=48,
                    tooltip=f"Open example {index + 1}",
                )

            if c.entry == "placed-list":
                return PlacedListView(
                    key=key,
                    height=180,
                    item_extents=[56.0] * 4,
                    controls=[row(i) for i in range(4)],
                )
            return VirtualList(
                name=key,
                count=20,
                height=180,
                context=c.key,
                offsets=tuple(float(i * 56) for i in range(21)),
                expand=False,
                row=lambda i: Row(f"example-{i}", (label, i), lambda: row(i)),
            )
    raise ValueError("Unknown catalog control")
