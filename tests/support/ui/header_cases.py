"""Synthetic views for comparing heading placement and local typography."""

import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import flet as ft

from relaydeck.client.app.shell.panes import SessionDetail
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.changes.api import ChangesActions, ChangesModel
from relaydeck.client.features.pairing.api import ConnectState
from relaydeck.client.screens.changes.parts.header import changes_header
from relaydeck.client.screens.usage.screen import UsagePanel, _header, _section
from relaydeck.client.widgets.connect.connect import ConnectPanel
from relaydeck.client.widgets.hosts.hosts import HostsHeader
from relaydeck.client.widgets.sessions.pills import ConsoleHeader
from relaydeck.client.widgets.sessions.shell_cards import ListHeader
from relaydeck.client.widgets.terminal.window import TerminalHeader
from tests.support.host_harness import render_component

CASES = (
    "hosts",
    "list",
    "console",
    "pairing",
    "terminal-wide",
    "terminal-phone",
    "terminal-custom",
    "detail-light",
    "detail-dark",
    "detail-empty-light",
    "detail-empty-dark",
    "usage-phone-light",
    "usage-phone-dark",
    "usage-wide-light",
    "usage-wide-dark",
    "changes-working",
    "changes-branch",
    "usage-section-light",
    "usage-section-dark",
    "providers-phone",
)


def noop(*args: Any) -> None:
    pass


async def render_heading(case: str) -> Any:
    if case == "hosts":
        return await render_component(HostsHeader, "2 hosts", "Hosts")
    if case == "list":
        return await render_component(ListHeader, "2 terminals", "Terminals", [], "list-header")
    if case == "console":
        return await render_component(
            ConsoleHeader, "2 sessions", "Sessions", noop, noop, count="2 of 2", on_refresh=noop
        )
    if case == "pairing":
        return await render_component(
            ConnectPanel,
            ConnectState(),
            on_endpoint=noop,
            on_host_id=noop,
            on_label=noop,
            on_begin=noop,
            on_cancel=noop,
            on_retry=noop,
        )
    if case.startswith("terminal-"):
        return await render_component(
            TerminalHeader,
            title="Example title",
            host_line="Example host",
            pills=[],
            actions=[],
            phone=case != "terminal-wide",
            on_back=noop,
            title_control=ft.Text("Custom title", key="custom-title")
            if case == "terminal-custom"
            else None,
        )
    if case.startswith("detail-"):
        return await render_component(
            SessionDetail,
            title="" if "empty" in case else "Example title",
            subtitle="Example subtitle",
            status="idle",
            on_open_terminal=noop,
            dark=case.endswith("dark"),
        )
    if case == "providers-phone":
        document = json.loads(
            (
                Path(__file__).resolve().parents[2] / "fixtures/usage/client/page-overall.json"
            ).read_text()
        )
        now = int(document["generated_ms"])

        async def fetch(query: Any) -> Any:
            return {**document, "host_id": query.host_id}

        with (
            patch("relaydeck.client.features.usage.hooks.now_ms", lambda: now),
            patch("relaydeck.client.features.usage.ui.now_ms", lambda: now),
        ):
            app = await render_component(
                UsagePanel,
                fetch=fetch,
                identity="example",
                host_id="host-a",
                active=True,
                dark=True,
                wide=False,
                width=390,
            )
            await app.wait_for("usage-summary")
            await app.set_props_and_fire("usage-provider-picker", {}, "click")
            await app.wait_for("usage-providers-title")
            return app
    if case.startswith("usage-section"):
        return await render_component(
            _section,
            "Example section",
            tokens=usage_palette(case.endswith("dark")),
            key="section-title",
            link="Show all",
            on_link=noop,
        )
    if case.startswith("usage-"):

        def view() -> Any:
            return ft.Column(
                controls=_header(
                    SimpleNamespace(meta="Example freshness", meta_tone="normal"),
                    tokens=usage_palette(case.endswith("dark")),
                    wide="wide" in case,
                    refresh=ft.TextButton("Refresh"),
                    controls=[],
                )
            )

        return await render_component(view)
    assert case.startswith("changes-")
    model = ChangesModel(scope="branch" if case.endswith("branch") else "working")
    actions = ChangesActions(
        **{
            field.name: (lambda: model) if field.name == "current" else noop
            for field in fields(ChangesActions)
            if field.name != "commit"
        }
    )
    return await render_component(
        changes_header,
        model,
        actions,
        "Example context",
        summary=None,
        on_back=noop,
        on_copy=None,
        now_ms=0,
    )
