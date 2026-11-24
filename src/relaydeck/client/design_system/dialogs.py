"""Controlled dialogs with explicit presentation and action slots."""

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system.headers import ScreenHeader


def Dialog(
    *,
    content: ft.Control,
    key: str,
    open: bool,
    on_dismiss: Callable[[Any], Any],
    title: ft.Control | None = None,
    actions: list[ft.Control] | None = None,
    modal: bool = False,
    bgcolor: str | None = None,
    radius: float | None = None,
    content_padding: ft.Padding | int | None = None,
) -> ft.AlertDialog:
    """Native dialog retaining the caller's modality, geometry and callbacks."""
    return ft.AlertDialog(
        key=key,
        open=open,
        modal=modal,
        bgcolor=bgcolor,
        shape=ft.RoundedRectangleBorder(radius=radius) if radius is not None else None,
        title=ScreenHeader(title) if title is not None else None,
        content=content,
        content_padding=content_padding,
        actions=[] if actions is None else actions,
        on_dismiss=on_dismiss,
    )
