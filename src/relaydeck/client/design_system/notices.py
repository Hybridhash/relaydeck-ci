"""Notice frames and centred messages with optional actions."""

from __future__ import annotations

from collections.abc import Sequence

import flet as ft

from relaydeck.client.design_system import tokens as t


def Notice(
    *,
    content: ft.Control,
    key: ft.KeyValue | None = None,
    padding: ft.PaddingValue | None = None,
    margin: ft.MarginValue | None = None,
    border_radius: ft.BorderRadiusValue | None = None,
    bgcolor: ft.ColorValue | None = None,
    border: ft.Border | None = None,
    alignment: ft.Alignment | None = None,
    expand: bool | int | None = None,
) -> ft.Container:
    """A native notice surface with caller-owned text, semantics and actions."""
    return ft.Container(
        key=key,
        padding=padding,
        margin=margin,
        border_radius=border_radius,
        bgcolor=bgcolor,
        border=border,
        alignment=alignment,
        expand=expand,
        content=content,
    )


def message_box(message: str, extra: Sequence[ft.Control] = (), *, key: str) -> ft.Control:
    return Notice(
        key=key,
        expand=True,
        padding=24,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            tight=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(message, color=t.TEXT_SECONDARY, size=14, text_align=ft.TextAlign.CENTER),
                *extra,
            ],
        ),
    )
