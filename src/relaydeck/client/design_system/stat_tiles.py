"""Metric surfaces with caller-owned values, labels and trends."""

import flet as ft


def StatTile(
    *,
    content: ft.Control,
    padding: ft.PaddingValue | None,
    border_radius: ft.BorderRadiusValue | None,
    bgcolor: ft.ColorValue | None,
    border: ft.Border | None,
    key: ft.KeyValue | None = None,
    height: float | None = None,
    expand: bool | int | None = None,
    alignment: ft.Alignment | None = None,
) -> ft.Container:
    """Preserve the supplied metric layout inside a native tile frame."""
    return ft.Container(
        key=key,
        height=height,
        expand=expand,
        padding=padding,
        border_radius=border_radius,
        bgcolor=bgcolor,
        border=border,
        alignment=alignment,
        content=content,
    )
