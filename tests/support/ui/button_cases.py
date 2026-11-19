"""Additional native button callers, preserving their existing surrounding controls."""

from dataclasses import fields, replace
from typing import Any

from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.changes.api import ChangesActions, ChangesModel
from relaydeck.client.features.create.shell_chooser import ShellChooser
from relaydeck.client.features.terminal.paste_sheet import PasteReviewSheet
from relaydeck.client.features.terminal.selection_sheet import SelectTextSheet
from relaydeck.client.models.input.paste_policy import PasteReview
from relaydeck.client.screens.changes.parts.graph_view import GraphView
from relaydeck.client.screens.usage.parts.dialogs import CommandDialog
from relaydeck.client.widgets.hosts.host_context import HostContextHeader
from relaydeck.domain.models import Tab
from tests.support.host_harness import render_component

CASES = (
    "host-light",
    "host-dark",
    "chooser-light",
    "chooser-dark",
    "select-all",
    "select-range",
    "select-error",
    "paste",
    "paste-fallback",
    "command-light",
    "command-dark",
    "graph",
    "graph-error",
)


def noop(*args: Any) -> None:
    pass


async def render_buttons(case: str) -> Any:
    if case.startswith("host-"):
        return await render_component(
            HostContextHeader, "Example host", "Connected", True, noop, dark=case.endswith("dark")
        )
    if case.startswith("chooser-"):
        return await render_component(
            ShellChooser,
            (Tab("tab-a", "host-a", "space-a", label="Example shell"),),
            noop,
            noop,
            noop,
            dark=case.endswith("dark"),
        )
    if case.startswith("select-"):
        return await render_component(
            SelectTextSheet,
            "Example terminal output",
            0,
            7 if case == "select-range" else None,
            noop,
            noop,
            error="Example error" if case == "select-error" else None,
        )
    if case.startswith("paste"):
        return await render_component(
            PasteReviewSheet,
            PasteReview(2, 12, "Example\ntext", case == "paste-fallback"),
            noop,
            noop,
        )
    if case.startswith("command-"):
        return await render_component(
            CommandDialog,
            open=True,
            title="Example command",
            lines=("example command",),
            note="Example instructions",
            doctor="Example diagnostics",
            tokens=usage_palette(case.endswith("dark")),
            on_close=noop,
        )
    model = ChangesModel()
    if case == "graph-error":
        model = replace(model, graph_load=replace(model.graph_load, status="error"))
    actions = ChangesActions(
        **{
            field.name: (lambda: model) if field.name == "current" else noop
            for field in fields(ChangesActions)
            if field.name != "commit"
        }
    )
    return await render_component(GraphView, model, actions, phone=True, wide=False, now_ms=0)
