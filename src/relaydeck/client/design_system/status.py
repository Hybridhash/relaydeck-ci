"""The status dot: a colour signal that always sits beside a text label."""

from __future__ import annotations

from typing import Any

import flet as ft

from relaydeck.client.design_system.theme import STATUS_LABELS, status_color


@ft.component
def StatusDot(status: str, dark: bool = True) -> Any:
    return ft.Container(
        key=f"dot-{status}",
        width=12,
        height=12,
        border_radius=6,
        bgcolor=status_color(status, dark),
        tooltip=STATUS_LABELS.get(status, status),
    )


__all__ = ["StatusDot"]
