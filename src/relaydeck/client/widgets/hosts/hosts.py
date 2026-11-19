"""Hosts presentation: header, group labels, host cards. Values come from the design."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, FAMILY_MONO
from relaydeck.client.design_system.headers import SectionHeader
from relaydeck.client.design_system.primitives import action_button, text_style
from relaydeck.client.design_system.surfaces import selected_outline
from relaydeck.client.design_system.theme import TypeStyle

__all__ = ["HostCard", "HostGroupLabel", "HostsHeader"]

# Local presentation dimensions and theme-derived colours.
_HOST_NAME = TypeStyle(size=18, weight=700, height=22 / 18)
_CHIP_TEXT = TypeStyle(size=12.5, weight=600, height=1.5)
_CHIP_INK = T.CHIP_INK  # mock chip label (theme.toml [console] chip_ink)
_CARD_PAD = 14
_ICON_SIZE = 48  # mock draws 44px; 48px is the hit floor


def HostsHeader(eyebrow: str, title: str) -> Any:
    """Title hierarchy: mono eyebrow over the 34px title, no actions."""
    return SectionHeader(
        eyebrow.upper(), title, key="hosts-header", title_key=None, eyebrow_no_wrap=True
    )


def HostGroupLabel(label: str, key: str | None = None) -> Any:
    """Mono uppercase group header ("Connected · N" / "Offline · N")."""
    return ft.Text(
        label.upper(),
        key=key,
        style=text_style(FAMILY_MONO, T.TYPE_META, T.TEXT_MUTED, 0.08),
        no_wrap=True,
    )


def _host_chip(entry: str) -> Any:
    """26px raised chip; height comes from text + padding (grows at scale)."""
    return Badge(
        padding=ft.Padding.symmetric(horizontal=10, vertical=4),
        border_radius=ft.BorderRadius.all(13),
        bgcolor=T.RAISED,
        content=ft.Text(
            entry,
            style=text_style(FAMILY_INTERFACE, _CHIP_TEXT, _CHIP_INK),
            no_wrap=True,
        ),
    )


def HostCard(
    host_id: str,
    name: str,
    line: str,
    reachable: bool,
    chips: tuple[str, ...],
    selected: bool,
    on_select: Callable[[str], Any],
    compact: bool = False,
) -> Any:
    """Host card: 48px icon square, 18px name, mono detail line. Reachable cards are dark with a
    lime icon square; offline cards use the panel surface, dimmed name and muted icon.
    """
    body: list[Any] = [
        ft.Row(
            spacing=12,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    width=_ICON_SIZE,
                    height=_ICON_SIZE,
                    border_radius=ft.BorderRadius.all(14),
                    bgcolor=T.DONE if reachable else T.RAISED,
                    alignment=ft.Alignment.CENTER,
                    content=ft.Icon(
                        ft.Icons.STORAGE,
                        size=22,
                        color=T.DONE_TEXT if reachable else T.TEXT_MUTED,
                    ),
                ),
                ft.Column(
                    spacing=1,
                    expand=True,
                    controls=[
                        ft.Text(
                            name,
                            style=text_style(
                                FAMILY_INTERFACE,
                                _HOST_NAME,
                                T.TEXT if reachable else T.TEXT_SECONDARY,
                            ),
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                        ft.Text(
                            line,
                            style=text_style(FAMILY_MONO, T.TYPE_META, T.TEXT_MUTED),
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        ),
                    ],
                ),
            ],
        )
    ]
    if chips:
        body.append(
            ft.Wrap(
                spacing=8,
                run_spacing=8,
                controls=[_host_chip(entry) for entry in chips],
            )
        )
    return action_button(
        key=f"host-{host_id}",
        radius=T.RADIUS_TILE,
        tooltip=None if compact else name,
        on_press=lambda: on_select(host_id),
        content=ft.Container(
            padding=ft.Padding.all(_CARD_PAD),
            border_radius=ft.BorderRadius.all(T.RADIUS_TILE),
            bgcolor=T.CARD if reachable else T.PANEL,
            border=ft.Border.all(1, T.BORDER if reachable else T.BORDER_DASHED_OFFLINE),
            shadow=selected_outline(selected),
            content=ft.Column(spacing=12, controls=body),
        ),
    )
