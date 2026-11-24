"""Render shared surfaces from immutable themed palette roles. Callers provide display values
and actions without owning application lifetimes.
"""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.theme import Palette


def frame_border(tokens: Palette) -> ft.Border:
    """Reference 1px line border (theme-selected)."""
    side = ft.BorderSide(width=1, color=tokens.line)
    return ft.Border(left=side, top=side, right=side, bottom=side)


def SurfaceFrame(
    key: str,
    content: Any,
    *,
    tokens: Palette,
    disabled: bool = False,
) -> Any:
    """Themed surface frame: surface bg, line border, radius, padding."""
    return ft.Container(
        key=key,
        disabled=disabled,
        bgcolor=tokens.surface,
        border=frame_border(tokens),
        border_radius=T.SURFACE_RADIUS,
        padding=12,
        content=content,
    )


def MutedText(
    value: str,
    *,
    key: str | None = None,
    tokens: Palette,
    size: float = 13,
) -> Any:
    """Muted palette text (the shell "Connecting…" line)."""
    return ft.Text(value, key=key, color=tokens.muted, size=size)


def selected_outline(selected: bool) -> list[ft.BoxShadow] | None:
    """3px light selection ring that never changes layout dimensions."""
    if not selected:
        return None
    return [
        ft.BoxShadow(
            color=T.TEXT,
            spread_radius=T.SELECT_OUTLINE_WIDTH,
            blur_radius=0,
            offset=ft.Offset(0, 0),
        )
    ]
