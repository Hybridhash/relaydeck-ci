"""Dialog callers retain their content, action slots and native presentation."""

from pathlib import Path
from typing import Any

import pytest

from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.rename.menu import AgentMenu
from relaydeck.client.screens.usage.parts.dialogs import CommandDialog
from relaydeck.client.screens.usage.parts.providers import ProvidersDialog
from tests.support.host_harness import render_component, run
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-overlays"


async def render_dialog(kind: str, dark: bool) -> Any:
    if kind == "menu":
        return await render_component(
            AgentMenu,
            agent_key="example",
            on_rename=lambda: None,
            on_open=lambda: None,
            on_close=lambda: None,
            on_pin=lambda: None,
            dark=dark,
        )
    if kind == "providers":
        return await render_component(
            ProvidersDialog,
            open=True,
            lines=(),
            selected_kind="",
            tokens=usage_palette(dark),
            on_select=lambda value: None,
            on_action=lambda kind, action: None,
            on_close=lambda: None,
        )
    assert kind == "command"
    return await render_component(
        CommandDialog,
        open=True,
        title="Example setup",
        lines=("example setup --local",),
        note="Example explanation",
        doctor="Example check",
        tokens=usage_palette(dark),
        on_close=lambda: None,
    )


@pytest.mark.parametrize("kind", ["menu", "providers", "command"])
@pytest.mark.parametrize("dark", [False, True])
def test_dialog_caller_snapshot(kind: str, dark: bool) -> None:
    name = f"{kind}-dialog-{'dark' if dark else 'light'}"
    run(snapshot_scenario(lambda: render_dialog(kind, dark), GOLDEN, name))
