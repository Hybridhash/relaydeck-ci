"""Session identity pills and chips (agent, host, space) and the console header."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.fonts import (
    FAMILY_INTERFACE,
)
from relaydeck.client.design_system.headers import SectionHeader
from relaydeck.client.design_system.primitives import RoundIconButton, action_button, text_style
from relaydeck.client.design_system.theme import TypeStyle

_AGENT_BADGE_TYPE = TypeStyle(size=11, weight=600, height=1.0)


def AgentPill(
    label: str,
    color: str,
    key: str | None = None,
    ink: str | None = None,
) -> Any:
    """Agent identity pill: 18px badge, 11px semibold label in identity ink
    on a 16% identity wash. `ink` overrides the text color where the
    identity color cannot read (solid orange blocked cards)."""
    return Badge(
        key=key,
        height=18,
        padding=ft.Padding.symmetric(horizontal=6),
        border_radius=ft.BorderRadius.all(9),
        bgcolor=ft.Colors.with_opacity(0.16, color),
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            label,
            style=text_style(FAMILY_INTERFACE, _AGENT_BADGE_TYPE, ink or color),
            no_wrap=True,
        ),
    )


def SpaceChip(label: str, color: str, key: str | None = None, ink: str | None = None) -> Any:
    """Space chip beside the agent pill: same 18px size, a dot and a softer wash, so the
    agent identity stays the strongest colour on the card."""
    return Badge(
        key=key,
        height=18,
        padding=ft.Padding.symmetric(horizontal=6),
        border_radius=ft.BorderRadius.all(9),
        bgcolor=ft.Colors.with_opacity(0.12, color),
        alignment=ft.Alignment.CENTER,
        content=ft.Row(
            tight=True,
            spacing=4,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(width=6, height=6, border_radius=3, bgcolor=ink or color),
                ft.Text(
                    label,
                    style=text_style(FAMILY_INTERFACE, _AGENT_BADGE_TYPE, ink or color),
                    no_wrap=True,
                    overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ],
        ),
    )


def HostChip(
    host_id: str,
    label: str,
    selected: bool,
    on_select: Callable[[str], Any],
    key: str | None = None,
    compact: bool = False,
) -> Any:
    """Host filter chip; selected chip carries the primary fill. Default: 48px."""
    visual = 34 if compact else None
    return action_button(
        key=key or f"host-chip-{host_id}",
        height=T.MIN_TOUCH,
        radius=22,
        tooltip=label,
        on_press=lambda: on_select(host_id),
        content=Badge(
            height=visual,
            padding=ft.Padding.symmetric(horizontal=14 if compact else 16),
            border_radius=ft.BorderRadius.all(17 if compact else 22),
            bgcolor=T.PRIMARY_FILL if selected else T.CARD,
            border=None if selected else ft.Border.all(1, T.BORDER),
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                label,
                style=text_style(
                    FAMILY_INTERFACE,
                    (T.TYPE_CARD_LINE_STRONG if compact else T.TYPE_BUTTON)
                    if selected
                    else (T.TYPE_CARD_LINE if compact else T.TYPE_BUTTON_IDLE),
                    T.ON_PRIMARY if selected else T.TEXT_SECONDARY,
                ),
                no_wrap=True,
            ),
        ),
    )


def ConsoleHeader(
    eyebrow: str,
    title: str,
    on_search: Callable[[], Any],
    on_new: Callable[[], Any],
    on_toggle_collapse: Callable[[], Any] | None = None,
    collapsed: bool = False,
    on_refresh: Callable[[], Any] | None = None,
    count: str | None = None,
) -> Any:
    """Console title row: mono eyebrow + 34px title + round actions.

    `count` ("5 of 5") is small faint text beside the title, and `on_refresh`
    is an icon-only button in the action row (the bottom Refresh button is gone)."""
    actions: list[Any] = []
    if on_refresh is not None:
        actions.append(
            RoundIconButton(ft.Icons.REFRESH, "Refresh agents", on_refresh, key="console-refresh")
        )
    actions += [
        RoundIconButton(ft.Icons.SEARCH, "Search agents", on_search, key="console-search"),
        RoundIconButton(ft.Icons.ADD, "New agent", on_new, tone="lime", key="console-new-agent"),
    ]
    if on_toggle_collapse is not None:
        actions.append(
            RoundIconButton(
                ft.Icons.KEYBOARD_DOUBLE_ARROW_RIGHT
                if collapsed
                else ft.Icons.KEYBOARD_DOUBLE_ARROW_LEFT,
                "Expand agent list" if collapsed else "Collapse agent list",
                on_toggle_collapse,
                key="console-collapse-toggle",
            )
        )
    return ft.Row(
        key="console-header",
        spacing=T.GAP_CARD,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            SectionHeader(
                eyebrow.upper(),
                title,
                key=None,
                title_key=None,
                expand=True,
                eyebrow_no_wrap=True,
                title_no_wrap=True,
                title_overflow=ft.TextOverflow.ELLIPSIS,
                after_title=(
                    [
                        ft.Text(
                            count,
                            key="agents-count",
                            size=12,
                            color=T.TEXT_MUTED,
                            no_wrap=True,
                            overflow=ft.TextOverflow.ELLIPSIS,
                        )
                    ]
                    if count
                    else []
                ),
            ),
            *actions,
        ],
    )
