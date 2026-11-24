"""Provide controlled presentation primitives with accessible keyboard actions and minimum touch
targets.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.badges import Badge
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.fonts import (
    FAMILY_INTERFACE,
    FAMILY_SYMBOLS,
    dot_tile_src,
    face,
)
from relaydeck.client.design_system.stat_tiles import StatTile
from relaydeck.client.design_system.theme import TypeStyle

_WEIGHTS = {
    400: ft.FontWeight.NORMAL,
    500: ft.FontWeight.W_500,
    600: ft.FontWeight.W_600,
    700: ft.FontWeight.BOLD,
    800: ft.FontWeight.W_800,
}

# Keyboard focus wash uses a translucent white role.
_FOCUS_WASH = T.FOCUS_WASH


def action_button(
    *,
    key: str | None,
    content: Any,
    on_press: Callable[[], Any],
    radius: float,
    width: float | None = None,
    height: float | None = None,
    expand: bool = False,
    tooltip: str | None = None,
    focus_ring: str = T.TEXT,
) -> ft.TextButton:
    """Wrap content in keyboard-actionable focus and click behavior without adding a second
    visual frame. Await asynchronous actions and keep focus-ring geometry stable.
    """

    async def _on_click(event: Any) -> Any:
        del event
        result = on_press()
        if inspect.isawaitable(result):
            return await result
        return result

    return Button(
        variant="text",
        key=key,
        width=width,
        height=height,
        expand=expand,
        tooltip=tooltip,
        style=ft.ButtonStyle(
            padding=0,
            shape=ft.RoundedRectangleBorder(radius=radius),
            bgcolor=ft.Colors.TRANSPARENT,
            overlay_color={
                ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                ft.ControlState.FOCUSED: _FOCUS_WASH,
            },
            side={
                ft.ControlState.DEFAULT: ft.BorderSide(3, ft.Colors.TRANSPARENT),
                ft.ControlState.FOCUSED: ft.BorderSide(3, focus_ring),
            },
        ),
        on_click=_on_click,
        content=content,
    )


def text_style(
    family: str,
    spec: TypeStyle,
    color: str,
    letter_spacing_em: float = 0.0,
) -> ft.TextStyle:
    """Design text style: bundled family alias + size/weight/height/spacing.

    Every style carries the symbols fallback from the bundled fonts.
    """
    size = spec.size
    return ft.TextStyle(
        font_family=face(family, int(spec.weight)),
        font_family_fallback=[FAMILY_SYMBOLS],
        size=size,
        weight=_WEIGHTS[int(spec.weight)],
        height=spec.height,
        letter_spacing=letter_spacing_em * size if letter_spacing_em else None,
        color=color,
    )


def _pill_colors(status: str, inverted: bool) -> tuple[str, str, str | None]:
    """(fill, text, border) for a status pill.

    `inverted` renders on the solid orange blocked card (dark pill);
    otherwise the pill carries the status fill (or outline for Unknown).
    """
    if status == T.STATUS_BLOCKED and inverted:
        return T.BLOCKED_PILL_BG, T.BLOCKED_PILL_TEXT, None
    if status == T.STATUS_BLOCKED:
        return T.BLOCKED, T.BLOCKED_TEXT, None
    if status == T.STATUS_WORKING:
        return T.WORKING, T.WORKING_TEXT, None
    if status == T.STATUS_DONE:
        return T.DONE, T.DONE_TEXT, None
    if status == T.STATUS_IDLE:
        return T.IDLE_FILL, T.IDLE_TEXT, None
    return T.PANEL, T.UNKNOWN_TEXT, T.UNKNOWN_TEXT


def StatusPill(
    status: str,
    label: str | None = None,
    inverted: bool = False,
    key: str | None = None,
) -> Any:
    """Status pill: 11.5px bold uppercase label on the status fill.

    22px tall at 100% scale via the 22px text line; no fixed height,
    so the pill grows with text scale instead of clipping (A1).
    """
    fill, ink, border = _pill_colors(status, inverted)
    word = (label or T.STATUS_LABELS.get(status, T.STATUS_LABELS[T.STATUS_UNKNOWN])).upper()
    return Badge(
        key=key,
        padding=ft.Padding.symmetric(horizontal=10),
        border_radius=ft.BorderRadius.all(11),
        bgcolor=fill,
        border=ft.Border.all(1, border) if border else None,
        alignment=ft.Alignment.CENTER,
        content=ft.Text(
            word,
            style=text_style(FAMILY_INTERFACE, T.TYPE_PILL, ink, 0.05),
            no_wrap=True,
        ),
    )


COUNT_TILE_HEIGHT = 52
_COUNT_STRIP_NUMBER = replace(T.TYPE_COUNT_NUMBER, size=24, height=1.0)


def CountTile(status: str, count: int, key: str | None = None) -> Any:
    """Compact count tile: the number and an uppercase label side by side, 52px tall."""
    label = T.STATUS_LABELS.get(status, T.STATUS_LABELS[T.STATUS_UNKNOWN]).upper()
    if status == T.STATUS_BLOCKED:
        bg: str | None = T.BLOCKED
        border = None
        number_ink, label_ink = T.BLOCKED_TEXT, T.BLOCKED_TEXT
    else:
        bg = T.CARD
        border = ft.Border.all(1, T.BORDER)
        number_ink = {
            T.STATUS_WORKING: T.WORKING,
            T.STATUS_DONE: T.DONE,
        }.get(status, T.TEXT_MUTED)
        label_ink = T.TEXT_SECONDARY
    return StatTile(
        key=key or f"count-{status}",
        height=COUNT_TILE_HEIGHT,
        padding=ft.Padding.symmetric(horizontal=14),
        border_radius=ft.BorderRadius.all(T.RADIUS_TILE),
        bgcolor=bg,
        border=border,
        alignment=ft.Alignment.CENTER_LEFT,
        content=ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(
                    str(count), style=text_style(FAMILY_INTERFACE, _COUNT_STRIP_NUMBER, number_ink)
                ),
                ft.Text(
                    label, style=text_style(FAMILY_INTERFACE, T.TYPE_COUNT_LABEL, label_ink, 0.05)
                ),
            ],
        ),
    )


def RoundIconButton(
    icon: ft.IconData,
    label: str,
    on_click: Callable[[], Any],
    tone: str = "ghost",
    key: str | None = None,
) -> Any:
    """48px round header button: ghost (card/border) or lime (primary new)."""
    lime = tone == "lime"
    return action_button(
        key=key,
        width=T.MIN_TOUCH,
        height=T.MIN_TOUCH,
        radius=24,
        tooltip=label,
        focus_ring=T.DONE_TEXT if lime else T.TEXT,
        on_press=on_click,
        content=ft.Container(
            border_radius=ft.BorderRadius.all(24),
            bgcolor=T.DONE if lime else T.CARD,
            border=None if lime else ft.Border.all(1, T.BORDER),
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(icon, size=20, color=T.DONE_TEXT if lime else T.TEXT),
        ),
    )


def DottedGround(content: Any, asset_base: str = "design_system") -> Any:
    """Page ground: #130F26 with the repeated 18px dot tile."""
    return ft.Container(
        key="console-ground",
        expand=True,
        bgcolor=T.GROUND,
        image=ft.DecorationImage(src=dot_tile_src(asset_base), repeat=ft.ImageRepeat.REPEAT),
        content=content,
    )


_CTA_TEXT = TypeStyle(size=16, weight=700, height=1.2)


def ActionButton(
    label: str,
    *,
    key: str,
    on_press: Callable[[], Any],
    primary: bool = False,
    disabled: bool = False,
) -> Any:
    """48px+ CTA: primary fill or ghost outline, focus wash and ring."""
    return Button(
        variant="text",
        key=key,
        disabled=disabled,
        style=ft.ButtonStyle(
            padding=0,
            shape=ft.RoundedRectangleBorder(radius=26),
            bgcolor=T.PRIMARY_FILL if primary else ft.Colors.TRANSPARENT,
            overlay_color={
                ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT,
                ft.ControlState.FOCUSED: T.FOCUS_WASH,
            },
            side={
                ft.ControlState.DEFAULT: (
                    ft.BorderSide(1, T.PRIMARY_TEXT)
                    if not primary
                    else ft.BorderSide(3, ft.Colors.TRANSPARENT)
                ),
                ft.ControlState.FOCUSED: ft.BorderSide(3, T.TEXT),
            },
        ),
        on_click=lambda e: on_press(),
        content=ft.Container(
            height=52,
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                label,
                style=text_style(
                    FAMILY_INTERFACE,
                    _CTA_TEXT,
                    T.ON_PRIMARY if primary else T.PRIMARY_TEXT_LIGHT,
                ),
            ),
        ),
    )
