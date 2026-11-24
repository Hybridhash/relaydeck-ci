"""A phone on its side keeps room for the graph: one header row and one chips-and-summary band."""

from __future__ import annotations

from typing import Any

import flet as ft
import pytest

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.design_system import tokens as t
from relaydeck.client.screens.changes.parts.header import ACTION_BAR, COMPACT_HEADER
from tests.support.flet_layout import expand_violations
from tests.support.host_harness import run
from tests.support.ui.retained_root import _render
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import Transport, click, files, graph, present, render, row_key

ROW = 48
SHORT = [pytest.param(844, 390, id="844x390"), pytest.param(932, 430, id="932x430")]


async def measured(app: Any, width: float, height: float) -> None:
    await app.set_props_and_fire(
        "changes-surface", {}, "size_change", {"w": float(width), "h": float(height)}
    )


def texts(app: Any) -> list[str]:
    found: list[str] = []
    stack: list[Any] = [app.page]
    seen: set[int] = set()
    while stack:
        control = stack.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        if isinstance(control, ft.Text) and control.value:
            found.append(control.value)
        for attr in ("controls", "content", "_b", "views"):
            value = getattr(control, attr, None)
            if value is not None:
                stack.extend(value if isinstance(value, list | tuple) else [value])
    return found


@pytest.mark.parametrize(("width", "height"), SHORT)
def test_a_short_landscape_screen_gets_the_one_row_header_and_a_single_band(
    width: int, height: int
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=width, height=height)
        assert present(app, "changes-header") and not present(app, "changes-header-row")
        await measured(app, width, height)
        row = app.find("changes-header-row")
        keys = [getattr(c, "key", None) for c in row.controls]
        assert keys[0] == "changes-back" and keys[-1] == "changes-tabs"
        assert "Changes" in texts(app) and not any("Console ·" in v for v in texts(app))
        assert app.find("changes-tabs").content.controls[0].height >= t.MIN_TOUCH
        # The graph's summary shares the chips' band instead of taking a line of its own.
        band = app.find("graph-chips").content
        assert [getattr(c, "key", None) for c in band.controls] == [
            "graph-mode-all",
            "graph-mode-branch",
            "graph-summary",
        ]
        assert app.find("graph-mode-all").height >= t.MIN_TOUCH
        assert expand_violations(app.page) == []

    run(scenario())


@pytest.mark.parametrize(("width", "height"), SHORT)
def test_the_graph_keeps_at_least_four_rows_in_view(width: int, height: int) -> None:
    chrome = COMPACT_HEADER + t.MIN_TOUCH + ACTION_BAR
    assert (height - chrome) // ROW >= 4


def test_a_tall_or_portrait_screen_keeps_the_full_header() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await graph(gateway, width=390, height=844)
        await measured(app, 390, 844)
        assert not present(app, "changes-header-row")
        app2 = await graph(gateway, width=1376, height=1000, wide=True)
        await measured(app2, 1100, 1000)
        assert not present(app2, "changes-header-row")

    run(scenario())


def test_working_and_branch_get_the_compact_header_too() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, width=844, height=390)
        await app.wait_for("changes-files")
        await measured(app, 844, 390)
        assert present(app, "changes-header-row") and present(app, "changes-totals")
        await click(app, "changes-scope-branch")
        await app.wait_for("changes-files")
        assert present(app, "changes-header-row") and present(app, "changes-search")

    run(scenario())


@pytest.mark.parametrize(("width", "height"), SHORT)
def test_in_the_real_root_a_short_landscape_graph_has_room_for_rows(
    width: int, height: int
) -> None:
    async def scenario() -> None:
        transport = Transport()
        bridge = RouteBridge(route="/agents/home-mac:term-1/changes", width=width, height=height)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport)
        try:
            await app.wait_for("changes-view-graph")
            await click(app, "changes-view-graph")
            await app.wait_for(row_key(transport.git, 0))
            content = app.find("inventory-content")
            pad = content.padding
            vertical = (
                2 * pad if isinstance(pad, int | float) else (pad.top + pad.bottom if pad else 0)
            )
            await measured(app, width, height - vertical)
            await app.wait_for("changes-header-row")
            room = height - vertical - COMPACT_HEADER - t.MIN_TOUCH - ACTION_BAR
            assert room // ROW >= 3, room
        finally:
            await pool.shutdown()

    run(scenario())
