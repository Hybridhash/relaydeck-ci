"""Render caller-supplied destinations as a bottom bar below 600 dp or a side rail at wider
sizes. Selection and navigation actions remain caller-owned.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE
from relaydeck.client.design_system.primitives import action_button, text_style


def _destination_button(
    dest_id: str,
    label: str,
    icon: ft.IconData,
    selected: bool,
    on_select: Callable[[str], Any],
    indicator_size: tuple[int, int],
    width: float | None,
    key_prefix: str,
    dot: bool = False,
) -> Any:
    active = selected
    ink = T.TEXT if active else T.TEXT_MUTED
    indicator: Any = ft.Container(
        width=indicator_size[0],
        height=indicator_size[1],
        border_radius=ft.BorderRadius.all(indicator_size[1] / 2),
        bgcolor=T.PRIMARY_FILL if active else None,
        alignment=ft.Alignment.CENTER,
        content=ft.Icon(icon, size=20, color=T.ON_PRIMARY if active else ink),
    )
    if dot:
        indicator = ft.Stack(
            controls=[
                indicator,
                ft.Container(
                    key=f"{key_prefix}-{dest_id}-dot",
                    width=8,
                    height=8,
                    right=2,
                    top=0,
                    border_radius=ft.BorderRadius.all(4),
                    bgcolor=T.PRIMARY_TEXT,
                ),
            ]
        )
    return action_button(
        key=f"{key_prefix}-{dest_id}",
        width=width,
        expand=True if width is None else False,
        radius=T.RADIUS_KEY,
        tooltip=f"{label} · update available" if dot else label,
        on_press=lambda: on_select(dest_id),
        content=ft.Container(
            padding=ft.Padding.only(top=10),
            content=ft.Column(
                spacing=4,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    indicator,
                    ft.Text(
                        label,
                        style=text_style(
                            FAMILY_INTERFACE,
                            T.TYPE_NAV_LABEL_ACTIVE if active else T.TYPE_NAV_LABEL,
                            ink,
                        ),
                        no_wrap=True,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
            ),
        ),
    )


def ConsoleBottomBar(
    selected: str,
    on_select: Callable[[str], Any],
    *,
    destinations: Sequence[tuple[str, str, ft.IconData]],
    settings_id: str,
    settings_dot: bool = False,
) -> Any:
    """76px bottom navigation bar for widths below 600.

    `destinations` are (id, label, icon) in display order; the update dot sits on `settings_id`.
    """
    return ft.Container(
        key="console-nav-bar",
        height=76,
        bgcolor=T.NAV,
        padding=ft.Padding.symmetric(horizontal=8),
        border=ft.Border.only(top=ft.BorderSide(1, T.BORDER)),
        content=ft.Row(
            spacing=0,
            controls=[
                _destination_button(
                    d,
                    label,
                    icon,
                    d == selected,
                    on_select,
                    (60, 30),
                    None,
                    "console-nav",
                    dot=settings_dot and d == settings_id,
                )
                for d, label, icon in destinations
            ],
        ),
    )


def ConsoleRail(
    selected: str,
    on_select: Callable[[str], Any],
    *,
    destinations: Sequence[tuple[str, str, ft.IconData]],
    settings_id: str,
    settings_dot: bool = False,
) -> Any:
    """96px side rail for widths at/above 600.

    `destinations` are (id, label, icon) in display order; the update dot sits on `settings_id`.
    """
    return ft.Container(
        key="console-nav-rail",
        width=T.RAIL_WIDTH,
        bgcolor=T.NAV,
        padding=ft.Padding.symmetric(vertical=24, horizontal=8),
        content=ft.Column(
            spacing=6,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                _destination_button(
                    d,
                    label,
                    icon,
                    d == selected,
                    on_select,
                    (56, 32),
                    76,
                    "console-rail",
                    dot=settings_dot and d == settings_id,
                )
                for d, label, icon in destinations
            ],
        ),
    )
