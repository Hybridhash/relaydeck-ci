"""File navigation, searchable file choice and branch-base choice."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.controls import PillButton
from relaydeck.client.design_system.fonts import FAMILY_MONO
from relaydeck.client.features.changes.api import ChangesActions, ReadingActions
from relaydeck.client.screens.changes.parts.file_list_view import picker_sheet
from relaydeck.contracts.git import FileEntry


def file_pager(
    model: m.ChangesModel, actions: ReadingActions, open_picker: Callable[[], None]
) -> ft.Control:
    ids = [f.file_id for f in model.files]
    position = ids.index(model.selected_file) + 1 if model.selected_file in ids else 0
    return ft.Container(
        bgcolor=t.NAV,
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        content=ft.Row(
            spacing=10,
            controls=[
                ft.Container(
                    expand=True,
                    content=PillButton(
                        "Prev",
                        lambda: actions.step_file(-1),
                        key="changes-prev",
                        disabled=position <= 1,
                    ),
                ),
                Button(
                    f"File {position} of {len(ids)}",
                    variant="text",
                    key="changes-picker-open",
                    height=t.MIN_TOUCH,
                    tooltip="Choose a file",
                    on_click=lambda _: open_picker(),
                ),
                ft.Container(
                    expand=True,
                    content=PillButton(
                        "Next",
                        lambda: actions.step_file(1),
                        key="changes-next",
                        tone="outline",
                        disabled=position >= len(ids),
                    ),
                ),
            ],
        ),
    )


def base_picker(model: m.ChangesModel, actions: ChangesActions) -> ft.Control:
    options: list[ft.Control] = [
        ft.Container(
            key=f"changes-base-{index}",
            height=t.MIN_TOUCH,
            padding=ft.Padding.symmetric(horizontal=12),
            alignment=ft.Alignment.CENTER_LEFT,
            on_click=lambda _, base_id=base.base_id: actions.save_base(base_id),
            content=ft.Text(
                base.name + (" (default)" if base.kind == "remote_default" else ""),
                font_family=FAMILY_MONO,
                size=13,
                color=t.TEXT,
            ),
        )
        for index, base in enumerate(model.bases)
    ]
    if model.bases_load.status == "loading":
        options.append(ft.Text("Loading branches…", color=t.TEXT_MUTED))
    elif model.bases_next is not None:
        failed = model.bases_load.status == "error"
        options.append(
            Button(
                "Could not load more. Try again" if failed else "Show more branches",
                variant="text",
                key="changes-more-bases",
                height=t.MIN_TOUCH,
                on_click=lambda _: actions.more_bases(),
            )
        )
    return ft.Container(
        key="changes-base-picker",
        bgcolor=t.RAISED,
        padding=12,
        height=320,
        content=ft.Column(
            spacing=4,
            controls=[
                ft.Row(
                    controls=[
                        ft.Text(
                            "Compare with",
                            size=16,
                            weight=ft.FontWeight.W_700,
                            color=t.TEXT,
                            expand=True,
                        ),
                        PillButton("Close", actions.close_bases, key="changes-base-close"),
                    ]
                ),
                ft.ListView(expand=True, spacing=0, controls=options),
            ],
        ),
    )


def file_picker(
    model: m.ChangesModel,
    actions: ReadingActions,
    query: str,
    *,
    seen: Callable[[FileEntry], bool],
    generated_open: bool,
    on_query: Callable[[str], None],
    on_toggle_generated: Callable[[], None],
    on_pick: Callable[[str], None],
    on_close: Callable[[], None],
) -> ft.Control:
    return picker_sheet(
        model.files,
        query,
        selected=model.selected_file,
        seen=seen,
        generated_open=generated_open,
        on_query=on_query,
        on_toggle_generated=on_toggle_generated,
        on_pick=on_pick,
        on_close=on_close,
        more=actions.more_files if model.files_next is not None else None,
    )
