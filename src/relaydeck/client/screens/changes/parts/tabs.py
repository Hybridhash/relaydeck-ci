"""Scope and graph tabs with Branch manifest loading."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

import relaydeck.client.features.changes.api as m
from relaydeck.client.design_system import tokens as t
from relaydeck.client.features.changes.api import ChangesActions
from relaydeck.contracts.git import WorktreeSummary


def changes_tabs(
    model: m.ChangesModel,
    actions: ChangesActions,
    summary: WorktreeSummary | None,
    *,
    compact: bool = False,
) -> ft.Control:
    """Uncommitted · N | Branch · M | Graph · K, equal width; Branch and Graph need a base."""
    working = summary.working.files if summary is not None else None
    branch_files = summary.branch_scope.files if summary is not None else None
    commits = (
        model.graph.page.ahead
        if model.graph.page is not None
        else summary.branch_scope.commits
        if summary is not None
        else None
    )
    has_base = bool(model.base_name or (summary is not None and summary.base_name))
    tabs: list[tuple[str, str, int | None, Callable[[], None]]] = [
        ("changes-scope-working", "Working", working, lambda: actions.set_scope("working")),
    ]
    if has_base:
        tabs.append(
            ("changes-scope-branch", "Branch", branch_files, lambda: _branch(actions, "files"))
        )
        tabs.append(("changes-view-graph", "Graph", commits, lambda: _branch(actions, "graph")))
    else:
        tabs.append(("changes-choose-base-tab", "Choose base", None, actions.open_bases))
    current = (
        "changes-view-graph"
        if model.scope == "branch" and model.view == "graph"
        else "changes-scope-branch"
        if model.scope == "branch"
        else "changes-scope-working"
    )
    return ft.Container(
        key="changes-tabs",
        padding=4,
        border_radius=12,
        bgcolor=t.CARD,
        border=ft.Border.all(1, t.BORDER),
        content=ft.Row(
            spacing=4,
            controls=[
                _tab(key, label, count, press, selected=key == current, compact=compact)
                for key, label, count, press in tabs
            ],
        ),
    )


def _branch(actions: ChangesActions, view: str) -> None:
    actions.set_scope("branch")
    actions.set_view("graph" if view == "graph" else "files")


def _tab(
    key: str,
    label: str,
    count: int | None,
    press: Callable[[], None],
    *,
    selected: bool,
    compact: bool = False,
) -> ft.Control:
    text = f"{label} · {count:,}" if count is not None else label
    return ft.Container(
        key=key,
        data={"selected": selected},
        expand=None if compact else True,  # a short screen sizes the tabs to their labels
        padding=ft.Padding.symmetric(horizontal=12) if compact else None,
        height=t.MIN_TOUCH,
        border_radius=9,
        bgcolor=t.PRIMARY_FILL if selected else None,
        alignment=ft.Alignment.CENTER,
        on_click=None if selected else (lambda _: press()),
        content=ft.Text(
            text,
            size=12.5 if compact else 14,
            weight=ft.FontWeight.W_700 if selected else ft.FontWeight.W_600,
            color=t.ON_PRIMARY if selected else t.TEXT_SECONDARY,
            no_wrap=True,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        ),
    )
