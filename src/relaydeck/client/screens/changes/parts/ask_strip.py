"""Origin context above the retained terminal; composer rows own delivery status."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.features.ask_agent.api import PendingAsk


def ask_strip(
    request: PendingAsk,
    *,
    wide: bool,
    on_back: Callable[[], None],
    on_replace: Callable[[], None],
    on_dismiss: Callable[[], None],
) -> ft.Control:
    conflict = request.state == "draft_conflict"
    if conflict:
        label = "Draft in the way"
    elif request.state == "staged":
        label = "Question ready · Send from Composer"
    elif request.state == "settled":
        label = f"Asked from Changes · {request.scope.label}"
    elif request.state == "uncertain":
        label = "Delivery not confirmed · Check the agent"
    else:
        label = request.reason or f"Asking {request.title}…"
    return ft.Row(
        key="changes-origin",
        spacing=4,
        controls=[
            Button(
                "‹ Back to Changes" if wide else "‹ Changes",
                variant="text",
                key="changes-origin-back",
                height=t.MIN_TOUCH,
                on_click=lambda _: on_back(),
            ),
            ft.Text(
                label,
                key="changes-origin-label",
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                color=t.TEXT,
                tooltip=label,
            ),
            *(
                [
                    Button(
                        ft.Text(
                            "Replace draft with question",
                            width=None if wide else 112,
                            max_lines=2,
                            text_align=ft.TextAlign.CENTER,
                            size=12,
                        ),
                        variant="text",
                        tooltip="Replace draft with question",
                        key="changes-replace-draft",
                        height=t.MIN_TOUCH,
                        on_click=lambda _: on_replace(),
                    )
                ]
                if conflict
                else []
            ),
            ft.IconButton(
                ft.Icons.CLOSE,
                key="changes-origin-dismiss",
                tooltip="Dismiss",
                width=t.MIN_TOUCH,
                height=t.MIN_TOUCH,
                on_click=lambda _: on_dismiss(),
            ),
        ],
    )
