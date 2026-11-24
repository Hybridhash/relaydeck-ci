"""Agent card gestures for opening the rename menu."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as T
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.platform.diagnostics.client import failure_category

LOG = logging.getLogger("relaydeck.inventory")
SWIPE_THRESHOLD = 0.3


def _reveal(tokens: UsageColors) -> Any:
    """What a left swipe uncovers behind the card."""
    return ft.Container(
        bgcolor=tokens.accent,
        border_radius=T.RADIUS_CARD,
        alignment=ft.Alignment.CENTER_RIGHT,
        padding=ft.Padding.only(right=24),
        content=ft.Row(
            tight=True,
            spacing=8,
            alignment=ft.MainAxisAlignment.END,
            controls=[
                ft.Icon(ft.Icons.EDIT, size=20, color=tokens.accent_on),
                ft.Text("Rename", size=15, weight=ft.FontWeight.W_800, color=tokens.accent_on),
            ],
        ),
    )


def AgentGestures(
    key: str,
    tile: Any,
    label: str,
    dark: bool,
    on_menu: Callable[[str], Any],
    on_rename: Callable[[], Any],
    on_select: Callable[[], Any],
) -> Any:
    """Wrap a card: a left swipe past the threshold, F2, or the long-press menu opens Rename.

    The swipe never dismisses the card: the pending dismissal is always declined, so the card
    springs back while the sheet opens.
    """
    tokens = usage_palette(dark)

    async def confirm(event: Any) -> None:
        # Settle the pending dismissal while this control is still mounted, before the render
        # that opens the sheet replaces it. The call's own timeout cleans up a missing answer.
        try:
            await event.control._invoke_method("confirm_dismiss", {"dismiss": False}, timeout=2)
        except (TimeoutError, RuntimeError) as exc:
            LOG.debug("swipe reset not acknowledged: category=%s", failure_category(exc))
        on_rename()

    def on_key(event: Any) -> None:
        if getattr(event, "key", "") == "F2":
            on_rename()

    gesture = ft.GestureDetector(
        key=f"agent-gesture-{key}",
        on_long_press_start=lambda _e: on_menu("long_press"),
        on_secondary_tap=lambda _e: on_menu("secondary"),
        content=tile,
    )
    keyed = ft.KeyboardListener(key=f"agent-keys-{key}", content=gesture, on_key_down=on_key)
    return ft.Dismissible(
        key=f"agent-swipe-{key}",
        content=ft.Semantics(
            label=label,
            button=True,
            on_tap=lambda _e: on_select(),
            on_long_press=lambda _e: on_menu("semantics"),
            on_long_press_hint_text="Rename and more",
            exclude_semantics=True,
            content=keyed,
        ),
        background=_reveal(tokens),
        dismiss_direction=ft.DismissDirection.END_TO_START,
        dismiss_thresholds={ft.DismissDirection.END_TO_START: SWIPE_THRESHOLD},
        on_confirm_dismiss=confirm,
    )
