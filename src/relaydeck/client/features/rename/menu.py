"""Agent menu with explicit rename and close callbacks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system.dialogs import Dialog
from relaydeck.client.design_system.theme import UsageColors
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.rename.rename_presentation import use_close_gate
from relaydeck.client.platform.diagnostics.terminal import diagnostics_logger

MENU_WIDTH = 250
MENU_ROW = 50


def _menu_row(
    key: str, label: str, icon: Any, tokens: UsageColors, on_click: Callable[[], Any], lead: bool
) -> Any:
    return ft.Container(
        key=key,
        height=MENU_ROW,
        padding=ft.Padding.symmetric(horizontal=16),
        bgcolor=tokens.border if lead else None,
        on_click=lambda _e: on_click(),
        ink=True,
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(label, size=16, weight=ft.FontWeight.W_600, color=tokens.text),
                ft.Icon(icon, size=20, color=tokens.text_2),
            ],
        ),
    )


@ft.component
def AgentMenu(
    agent_key: str,
    on_rename: Callable[[], Any],
    on_open: Callable[[], Any],
    on_close: Callable[[], Any],
    dark: bool,
    gone: bool = False,
    pinned: bool = False,
    on_pin: Callable[[], Any] | None = None,
) -> Any:
    """Rename first and highlighted, then Open terminal.

    A choice, a dismissal or the agent leaving closes the dialog natively before it unmounts.
    """
    tokens = usage_palette(dark)
    after: Any = ft.use_ref(None)

    def closed() -> None:
        on_close()
        action, after.current = after.current, None
        if action is not None:
            action()

    gate = use_close_gate(True, closed)
    ft.use_effect(lambda: diagnostics_logger().info("agent_menu shown"), dependencies=[])
    ft.use_effect(lambda: gate.request() if gone else None, dependencies=[gone])

    def choose(action: Callable[[], Any], *, after_close: bool = False) -> Callable[[], None]:
        def pick() -> None:
            if after_close:
                # Navigating away unmounts the menu, so it closes natively first.
                after.current = action
                gate.request()
            else:
                gate.request()
                action()

        return pick

    return Dialog(
        key=f"agent-menu-{agent_key}",
        open=not gate.closing,
        modal=False,
        bgcolor=tokens.surface_raised,
        radius=16,
        content_padding=0,
        content=ft.Container(
            width=MENU_WIDTH,
            border_radius=16,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                spacing=0,
                tight=True,
                controls=[
                    _menu_row(
                        f"agent-rename-{agent_key}",
                        "Rename",
                        ft.Icons.EDIT,
                        tokens,
                        choose(on_rename),
                        True,
                    ),
                    *(
                        [
                            _menu_row(
                                f"agent-pin-{agent_key}",
                                "Unpin" if pinned else "Pin to top",
                                ft.Icons.PUSH_PIN_OUTLINED if pinned else ft.Icons.PUSH_PIN,
                                tokens,
                                choose(on_pin),
                                False,
                            )
                        ]
                        if on_pin is not None
                        else []
                    ),
                    _menu_row(
                        f"agent-open-{agent_key}",
                        "Open terminal",
                        ft.Icons.TERMINAL,
                        tokens,
                        choose(on_open, after_close=True),
                        False,
                    ),
                ],
            ),
        ),
        on_dismiss=lambda _e: gate.reported() if gate.closing else on_close(),
    )
