"""Actual pending operations render their existing native progress indicators."""

import asyncio
from typing import Any

import pytest

from relaydeck.client.app.shell.navigation import InventoryPanel
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.create.components import NewTerminalSheet, StartAgentSheet
from relaydeck.client.screens.usage.model import RefreshModel
from relaydeck.client.screens.usage.parts.rows import _refresh_control
from tests.support.host_harness import render_component
from tests.support.pictures.attachments import _Api, choose, install_picker
from tests.support.terminal.direct_typing import _direct

CASES = ("agent-submitting", "terminal-submitting", "inventory-loading", "limits-reading", "upload")


def noop(*args: Any) -> None:
    pass


async def render_spinner(case: str) -> Any:
    if case == "upload":
        with pytest.MonkeyPatch.context() as patch:
            pending = asyncio.Event()
            install_picker(patch, _Api(), pending.wait)
            app, *_ = await _direct(True)
            task = asyncio.create_task(choose(app, "terminal-attach-picture"))
            await app.wait_for("picture-upload-cancel")
            assert not task.done()
            return app
    if case == "inventory-loading":

        async def fetch(query: str) -> tuple[str, ...]:
            await asyncio.Event().wait()
            return ()

        return await render_component(InventoryPanel, fetch)
    if case == "limits-reading":
        return await render_component(
            _refresh_control,
            RefreshModel("tool", "Refresh example limits", True, False, ""),
            tokens=usage_palette(True),
            on_refresh=noop,
        )
    props = dict(
        hosts=(("host-a", "Example host"),),
        workspaces_by_host={"host-a": (("space-a", "Example space"),)},
        submitting=True,
        error="",
        command_status="Submitting",
        on_submit=noop,
        on_cancel=noop,
    )
    if case == "agent-submitting":
        return await render_component(StartAgentSheet, kinds=("tool",), **props)
    assert case == "terminal-submitting"
    return await render_component(NewTerminalSheet, **props)
