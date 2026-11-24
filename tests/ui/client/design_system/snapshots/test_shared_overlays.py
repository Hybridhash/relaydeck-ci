"""Caller trees preserve the phone and wide overlay presentations."""

from pathlib import Path
from typing import Any

import pytest

from relaydeck.client.design_system import tokens as T
from relaydeck.client.features.rename.rename_model import SheetContext, validate
from relaydeck.client.features.rename.rename_sheet import RenameSheetView, SheetActions, SheetProps
from relaydeck.client.screens.usage.parts.space_sheet import SpaceSheet
from tests.support.host_harness import render_component, run
from tests.support.ui.snapshot import snapshot_scenario

GOLDEN = Path(__file__).parents[3] / "snapshots" / "golden/shared-overlays"


async def render_overlay(kind: str, phone: bool, dark: bool) -> Any:
    if kind == "rename":
        props = SheetProps(
            open=True,
            phone=phone,
            dark=dark,
            value="Example name",
            context=SheetContext("Example work", "Tool", "Example space", "working", T.DONE),
            verdict=validate("Example name", "Previous name", ()),
            error="",
            status="",
            busy=False,
            uncertain=False,
            can_save=True,
            save_label="Save",
            select_all=True,
            suggestions=("First name", "Second name"),
        )
        actions = SheetActions(
            on_change=lambda value: None,
            on_save=lambda: None,
            on_cancel=lambda: None,
            on_dismiss=lambda: None,
            on_check=lambda: None,
            on_pick=lambda value: None,
        )
        return await render_component(RenameSheetView, "rename-overlay", props, actions)
    assert kind == "space"
    return await render_component(
        SpaceSheet,
        open=True,
        rows=(),
        selected="",
        pins=(),
        all_value="0 tokens",
        tokens=T.usage_palette(dark),
        wide=not phone,
        on_done=lambda value: None,
        on_cancel=lambda: None,
        on_pins=lambda value: None,
    )


@pytest.mark.parametrize("kind", ["rename", "space"])
@pytest.mark.parametrize("phone", [False, True])
@pytest.mark.parametrize("dark", [False, True])
def test_overlay_caller_snapshot(kind: str, phone: bool, dark: bool) -> None:
    name = f"{kind}-{'phone' if phone else 'wide'}-{'dark' if dark else 'light'}"
    run(snapshot_scenario(lambda: render_overlay(kind, phone, dark), GOLDEN, name))
