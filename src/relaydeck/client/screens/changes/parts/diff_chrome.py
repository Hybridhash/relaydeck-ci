"""Diff loading state and paging controls."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.notices import Notice


def _page_button(label: str, key: str, action: Callable[[], None]) -> ft.Control:
    return Button(label, variant="text", key=key, height=t.MIN_TOUCH, on_click=lambda _: action())


def _message(
    message: str, on_load: Callable[[], None] | None, extra: Sequence[ft.Control] = ()
) -> ft.Control:
    controls: list[ft.Control] = [ft.Text(message, color=t.TEXT_SECONDARY, size=14), *extra]
    if on_load is not None:
        controls.append(
            Button(
                "Load diff",
                variant="filled",
                key="diff-load",
                on_click=lambda _: on_load(),
                height=t.MIN_TOUCH,
            )
        )
    return Notice(
        expand=True,
        padding=16,
        alignment=ft.Alignment.CENTER,
        content=ft.Column(
            tight=True, horizontal_alignment=ft.CrossAxisAlignment.CENTER, controls=controls
        ),
    )
