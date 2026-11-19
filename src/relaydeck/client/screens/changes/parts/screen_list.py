"""Changes list, commits and fixed empty/loading states."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.design_system.buttons import Button
from relaydeck.client.design_system.controls import PillButton
from relaydeck.client.design_system.notices import message_box
from relaydeck.client.features.changes.api import ChangesActions
from relaydeck.client.screens.changes.parts.file_list import search_field
from relaydeck.client.screens.changes.parts.file_list_view import FileListView
from relaydeck.client.screens.changes.parts.graph_view import GraphView
from relaydeck.contracts.git import FileEntry

_CONDITIONS = {
    "calculating": "Calculating changes…",
    "no_repo": "This agent's folder is not a git repository.",
    "folder_gone": "This agent's folder no longer exists.",
    "host_offline": "The Mac is not reachable right now.",
    "not_permitted": "This device cannot view changes.",
    "git_unavailable": "Git 2.45 or newer is needed on the Mac.",
    "unsupported_config": "This repository's settings could run programs, so it is not read.",
    "unavailable": "Changes are unavailable right now.",
    "no_base": "Choose the branch to compare with.",
}
# States the reader can ask again about; the others do not change by asking.
_RETRYABLE = frozenset({"host_offline", "unavailable"})
_BUSY = frozenset({"busy", "rate_limited"})


def _list_area(
    model: m.ChangesModel,
    actions: ChangesActions,
    *,
    phone: bool,
    wide: bool,
    query: str,
    on_query: Callable[[str], None],
    generated_open: bool,
    on_toggle_generated: Callable[[], None],
    folded: frozenset[str] | None,
    on_toggle_dir: Callable[[str], None],
    on_recheck: Callable[[], None] | None,
    on_search_focus: Callable[[bool], None],
    seen: Callable[[FileEntry], bool],
    now_ms: int,
    compact: bool = False,
) -> ft.Control:
    waiting = _waiting(model, actions, on_recheck)
    if waiting is not None:
        return waiting
    if model.view == "graph":
        return GraphView(model, actions, phone=phone, wide=wide, now_ms=now_ms, compact=compact)
    if not model.files:
        return _hint(
            "Clean: no changes in the working tree."
            if model.scope == "working"
            else "No changes on this branch."
        )
    footer: list[ft.Control] = []
    if model.files_next is not None:
        footer.append(
            Button(
                "Show more files",
                variant="text",
                key="changes-more-files",
                height=t.MIN_TOUCH,
                on_click=lambda _: actions.more_files(),
            )
        )
    return ft.Column(
        expand=True,
        spacing=0,
        controls=[
            ft.Container(
                key="changes-search-row",
                padding=ft.Padding.only(left=t.PAD_PHONE, right=t.PAD_PHONE, top=8, bottom=4),
                # A field in a column is only as wide as its default; in a row it fills the width.
                content=ft.Row(
                    controls=[
                        search_field(
                            "changes-search", query, on_query, on_search_focus, expand=True
                        )
                    ]
                ),
            ),
            FileListView(
                name="changes-files",
                files=model.files,
                query=query,
                selected=model.selected_file,
                seen=seen,
                on_pick=actions.select_file,
                on_toggle_generated=on_toggle_generated,
                generated_open=generated_open,
                tree=folded,
                on_toggle_dir=on_toggle_dir,
                footer=footer,
            ),
        ],
    )


def _waiting(
    model: m.ChangesModel, actions: ChangesActions, on_recheck: Callable[[], None] | None
) -> ft.Control | None:
    """What stands in for the list until there is one: a state, an error with Retry, or loading."""
    asking = actions.refresh if model.worktree_id is not None else on_recheck
    retry = PillButton("Retry", asking or actions.refresh, key="changes-retry")
    load = model.files_load
    if (
        model.snapshot is None
        and load.status == "error"
        and model.condition in ("calculating", "ok")
    ):
        busy = load.error in _BUSY
        return _hint(
            "The Mac is busy. Trying again shortly." if busy else "Could not load changes.",
            [] if busy else [retry],
        )
    if model.condition != "ok":
        message = _CONDITIONS.get(model.condition, _CONDITIONS["unavailable"])
        extra: list[ft.Control] = []
        if model.condition == "no_base":
            extra.append(PillButton("Choose base", actions.open_bases, key="changes-choose-base"))
        elif model.condition in _RETRYABLE and asking is not None:
            extra.append(retry)
        return _hint(message, extra)
    return _hint("Loading changes…") if model.snapshot is None else None


def _hint(message: str, extra: Sequence[ft.Control] = ()) -> ft.Control:
    return message_box(message, extra, key="changes-state")
