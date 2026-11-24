"""Actual text-field carriers and search forms with synthetic prepared values."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import flet as ft

import relaydeck.client.widgets.terminal.direct_typing as direct_typing
from relaydeck.client.app.shell.navigation import InventoryPanel
from relaydeck.client.design_system.inputs import FilterField
from relaydeck.client.design_system.theme import palette
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.ask_agent.api import AskScope
from relaydeck.client.features.composer.components import ComposerBox
from relaydeck.client.features.create.components import _workspace_controls
from relaydeck.client.features.rename.rename_model import SheetContext, Verdict
from relaydeck.client.features.rename.rename_sheet import SheetActions, SheetProps
from relaydeck.client.features.rename.rename_sheet import _field as rename_field
from relaydeck.client.screens.changes.parts.ask_sheet import AskSheet
from relaydeck.client.screens.changes.parts.file_list import search_field
from relaydeck.client.screens.console.parts.lists import SearchField as InventorySearch
from relaydeck.client.screens.settings.parts.hosts_pane import ManagementPane
from relaydeck.client.screens.usage.parts.providers import ProvidersView
from relaydeck.client.screens.usage.parts.space_sheet import SpaceSheet
from relaydeck.client.widgets.connect.connect import _field as pairing_field
from relaydeck.client.widgets.terminal.paste_entry import PasteEntrySheet
from tests.support.host_harness import render_component
from tests.support.terminal.direct_typing import _direct, open_compose
from tests.support.ui.navigation import _render_root, _state

CASES = (
    "filter",
    "inventory-search",
    "file-search",
    "workspace",
    "agent-sheet",
    "terminal-sheet",
    "providers-light",
    "providers-dark",
    "space-phone",
    "space-wide",
    "rename-light",
    "rename-dark",
    "rename-error",
    "compose-light",
    "compose-dark",
    "phone-compose",
    "direct-phone",
    "direct-tablet",
    "ask-phone",
    "ask-wide",
    "pairing",
    "pairing-disabled",
    "paste-phone",
    "paste-wide",
    "management",
    "inventory-panel",
)


def noop(*args: Any) -> None:
    pass


def fixture(name: str) -> Any:
    return json.loads(
        (Path(__file__).resolve().parents[2] / f"fixtures/gateway/{name}.json").read_text()
    )


class Hosts:
    async def me(self) -> Any:
        data = fixture("me")
        return {**data, "capabilities": [*data["capabilities"], "hosts.enroll"]}

    async def hosts(self) -> Any:
        return {**fixture("hosts"), "hosts": []}

    async def devices(self) -> Any:
        return fixture("devices")

    async def revoke_host(self, host: str) -> None:
        raise AssertionError("No host mutation is part of a form specimen")


async def render_input(case: str) -> Any:
    if case in ("phone-compose", "direct-phone", "direct-tablet"):
        with patch.object(direct_typing, "_typing_now", lambda: 1000.0):
            app, *_ = await _direct(False, "medium" if case == "direct-tablet" else "compact")
            if case == "phone-compose":
                await open_compose(app)
            return app
    if case in ("agent-sheet", "terminal-sheet"):
        app, *_ = await _render_root(_state())
        if case == "terminal-sheet":
            await app.set_props_and_fire("console-rail-terminals", {}, "click")
        await app.set_props_and_fire(
            "console-new-agent" if case == "agent-sheet" else "new-terminal-open", {}, "click"
        )
        await app.wait_for("start-agent-sheet" if case == "agent-sheet" else "new-terminal-sheet")
        return app
    if case == "management":
        app = await render_component(
            ManagementPane,
            Hosts(),
            fixture("me")["account"],
            "hosts",
            "",
            noop,
            noop,
            initial_fields=("Example host", "", ""),
        )
        await app.set_props_and_fire("enroll-host-open", {}, "click")
        await app.wait_for("enroll-host-label")
        return app

    def view() -> Any:
        if case == "filter":
            return FilterField("Example", noop, hint="Search examples", key="filter")
        if case == "inventory-search":
            return InventorySearch("Example", noop, "Search sessions")
        if case == "file-search":
            return search_field("file-search", "Example", noop, noop, expand=True)
        if case == "workspace":
            return ft.Column(
                controls=_workspace_controls(
                    "example", "new", noop, "/example", noop, "Example space", noop, {}
                )
            )
        if case.startswith("providers-"):
            return ProvidersView(
                lines=(),
                selected_kind="",
                tokens=usage_palette(case.endswith("dark")),
                on_select=noop,
                on_action=noop,
            )
        if case.startswith("space-"):
            return SpaceSheet(
                open=True,
                rows=(),
                selected="",
                pins=(),
                all_value="0 tokens",
                tokens=usage_palette(True),
                wide=case == "space-wide",
                on_done=noop,
                on_cancel=noop,
                on_pins=noop,
            )
        if case.startswith("rename-"):
            props = SheetProps(
                True,
                True,
                case.endswith("dark"),
                "Example name",
                SheetContext("Example work", "Tool", "Space", "idle", "#ffffff"),
                Verdict("", ""),
                "Example error" if case == "rename-error" else "",
                "",
                False,
                False,
                True,
                "Save",
                True,
                (),
            )
            return rename_field(
                props, SheetActions(noop, noop, noop, noop, noop, noop), usage_palette(props.dark)
            )
        if case.startswith("compose-"):
            return ComposerBox(
                draft="Example draft",
                markers=(),
                can_send=True,
                blockers=(),
                submission="idle",
                error=None,
                review=None,
                input_mode="text",
                on_draft_change=noop,
                on_send=noop,
                on_confirm_blocked=noop,
                on_cancel_blocked=noop,
                on_clear_error=noop,
                dark=case.endswith("dark"),
            )
        if case.startswith("ask-"):
            return AskSheet(
                AskScope(
                    "file", "working", "snapshot", "a" * 40, file_id="file-a", path="example.py"
                ),
                "Example question",
                "Example recipient",
                status="idle",
                reason="",
                older=False,
                on_question=noop,
                on_send=noop,
                on_close=noop,
                wide=case == "ask-wide",
            )
        if case.startswith("pairing"):
            return pairing_field(
                "Example endpoint",
                "pairing-field",
                "Example",
                "Enter endpoint",
                noop,
                case == "pairing-disabled",
            )
        if case.startswith("paste-"):
            return PasteEntrySheet(
                visible=True,
                on_submit=noop,
                on_close=noop,
                tokens=palette(True),
                top=12,
                wide=case == "paste-wide",
                width=480,
            )
        assert case == "inventory-panel"

        async def fetch(query: str) -> tuple[str, ...]:
            return ("Example session",)

        return InventoryPanel(fetch)

    return await render_component(view)
