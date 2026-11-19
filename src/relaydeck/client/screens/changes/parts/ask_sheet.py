"""Question panel with frozen context and live recipient eligibility."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.inputs import TextInput
from relaydeck.client.features.ask_agent.api import PRESETS, AskScope
from relaydeck.client.features.composer.ui import use_draft_value


@ft.component
def AskSheet(
    scope: AskScope,
    question: str,
    title: str,
    *,
    status: str,
    reason: str,
    older: bool,
    on_question: Callable[[str], None],
    on_send: Callable[[str], None],
    on_close: Callable[[], None],
    wide: bool = False,
) -> ft.Control:
    reset, set_reset = ft.use_state(0)
    previous_reset = ft.use_ref(reset)
    field: Any = ft.use_ref(None)
    value, remember = use_draft_value(question, focusable=previous_reset.current == reset)
    previous_reset.current = reset

    def preset(text: str) -> None:
        set_reset(lambda n: n + 1)
        on_question(text)

    editor = TextInput(
        value=value,
        key="changes-ask-question",
        multiline=True,
        min_lines=2,
        max_lines=3,
        on_change=lambda e: on_question(str(e.control.value or "")),
    )
    remember(editor)
    field.current = editor
    working = status == "working"
    controls: list[ft.Control] = [
        ft.Text(f"Ask about this {scope.kind}", key="changes-ask-title", color=t.TEXT, size=18),
        ft.Text(
            scope.label,
            key="changes-ask-context",
            color=t.TEXT_SECONDARY,
            max_lines=2,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
        editor,
        ft.Row(
            spacing=4,
            wrap=True,
            controls=[
                Button(
                    text,
                    variant="text",
                    key=f"changes-ask-preset-{i}",
                    height=t.MIN_TOUCH,
                    on_click=lambda _, text=text: preset(text),
                )
                for i, text in enumerate(PRESETS)
            ],
        ),
    ]
    if older:
        controls.append(
            ft.Text(
                "Newer changes are available; this question uses the review shown.",
                key="changes-ask-older",
                color=t.TEXT_SECONDARY,
            )
        )
    if working:
        controls.extend(
            [
                ft.Text(f"{title} is busy", key="changes-ask-busy", color=t.TEXT),
                ft.Text(
                    "It's in the middle of a task. Your question is sent now and the agent takes "
                    "it when it can; nothing is interrupted on this side.",
                    color=t.TEXT_SECONDARY,
                ),
            ]
        )
    controls.extend(
        [
            ft.Text(reason, key="changes-ask-reason", visible=bool(reason), color=t.TEXT_SECONDARY),
            ft.Row(
                controls=[
                    Button(
                        "Send anyway" if working else f"Send to {title}",
                        variant="filled",
                        key="changes-ask-send",
                        disabled=bool(reason),
                        height=t.MIN_TOUCH,
                        on_click=lambda _: on_send(str(field.current.value or "")),
                    ),
                    Button(
                        "Cancel",
                        variant="text",
                        key="changes-ask-cancel",
                        height=t.MIN_TOUCH,
                        on_click=lambda _: on_close(),
                    ),
                ]
            ),
        ]
    )
    return ft.Container(
        key="changes-ask-sheet",
        padding=16,
        bgcolor=t.RAISED,
        width=560 if wide else None,
        height=400,
        content=ft.Column(scroll=ft.ScrollMode.AUTO, controls=controls),
    )
