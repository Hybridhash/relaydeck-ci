"""Controlled sheets with caller-owned content and dismissal."""

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system.theme import UsageColors


def Sheet(
    content: ft.Control,
    *,
    key: str,
    open: bool,
    tokens: UsageColors,
    padding: ft.Padding | int,
    on_dismiss: Callable[[Any], Any],
    scrollable: bool = False,
) -> ft.BottomSheet:
    """Rounded sheet with an explicit content inset and scrolling policy."""
    return ft.BottomSheet(
        key=key,
        open=open,
        scrollable=scrollable,
        bgcolor=tokens.surface_sheet,
        shape=ft.RoundedRectangleBorder(radius=24),
        show_drag_handle=True,
        content=ft.Container(padding=padding, content=content),
        on_dismiss=on_dismiss,
    )
