"""Accessible hunk selection and variable row geometry for the diff reader."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as t


def height(row: Any, normal: float, selectable: bool) -> float:
    return float(t.MIN_TOUCH) if selectable and row.kind == "hunk" else normal


def offset(rows: Sequence[Any], index: int, normal: float, selectable: bool) -> float:
    return sum(height(row, normal, selectable) for row in rows[:index])


def selectable_header(
    control: ft.Control,
    row: Any,
    on_hunk: Callable[[tuple[int, int]], None] | None,
    selected: tuple[int, int] | None,
) -> ft.Control:
    if row.kind != "hunk" or on_hunk is None:
        return control
    return ft.Container(
        key=f"diff-hunk-{row.page}-{row.hunk}",
        height=t.MIN_TOUCH,
        alignment=ft.Alignment.CENTER_LEFT,
        bgcolor=t.CARD_SELECTED if selected == (row.page, row.hunk) else t.DIFF_HUNK_BG,
        tooltip="Selected hunk" if selected == (row.page, row.hunk) else "Select this hunk",
        data={"selected": selected == (row.page, row.hunk)},
        on_click=lambda _: on_hunk((row.page, row.hunk)),
        ink=True,
        padding=control.padding if isinstance(control, ft.Container) else None,
        content=control.content if isinstance(control, ft.Container) else control,
    )
