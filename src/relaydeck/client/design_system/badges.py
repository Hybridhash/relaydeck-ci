"""Decorative badges with caller-owned content and token values."""

import flet as ft


def Badge(
    *,
    content: ft.Control,
    padding: ft.PaddingValue | None = None,
    border_radius: ft.BorderRadiusValue,
    bgcolor: ft.ColorValue | None = None,
    key: str | None = None,
    width: float | None = None,
    height: float | None = None,
    border: ft.Border | None = None,
    alignment: ft.Alignment | None = None,
) -> ft.Container:
    """A native frame; actions and text remain owned by its caller."""
    return ft.Container(
        key=key,
        width=width,
        height=height,
        padding=padding,
        border_radius=border_radius,
        bgcolor=bgcolor,
        border=border,
        alignment=alignment,
        content=content,
    )
