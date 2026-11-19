"""Shared interface text with bundled fonts and accessibility labels."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.design_system.fonts import FAMILY_INTERFACE, face
from relaydeck.client.design_system.theme import TypeStyle

TYPE_ROW = TypeStyle(size=13, weight=400, height=18 / 13)


def ui(
    value: str,
    *,
    color: str,
    size: float = 15,
    weight: int = 700,
    key: str | None = None,
    max_lines: int | None = 1,
    italic: bool = False,
    align: Any = None,
    label: str | None = None,
) -> ft.Text:
    """UI text. Long values ellipsize unless `max_lines` allows wrapping (XXL text reflows)."""
    return ft.Text(
        value,
        key=key,
        color=color,
        size=size,
        weight=ft.FontWeight.W_700 if weight >= 700 else ft.FontWeight.W_500,
        font_family=face(FAMILY_INTERFACE, weight),
        max_lines=max_lines,
        overflow=ft.TextOverflow.ELLIPSIS if max_lines else ft.TextOverflow.CLIP,
        italic=italic,
        text_align=align,
        semantics_label=label,
    )
