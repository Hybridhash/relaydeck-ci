"""The graph and an open commit share the width the real shell leaves the Changes screen."""

from __future__ import annotations

from typing import Any

import pytest

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.design_system.layout import rail_width
from relaydeck.client.design_system.theme import layout_mode
from relaydeck.client.screens.changes.parts.graph_split import COMMIT_MIN, DIVIDER, GRAPH_MIN
from tests.support.host_harness import run
from tests.support.ui.retained_root import _render
from tests.ui.git.harness import Transport, click, present, row_key, until

ROUTE = "/agents/home-mac:term-1/changes"


def padding(control: Any) -> float:
    value = control.padding
    if value is None:
        return 0.0
    return float(value) if isinstance(value, int | float) else float(value.left + value.right)


def given(app: Any, width: int) -> float:
    """The width the shell leaves the Changes screen, from the controls it really rendered."""
    taken = rail_width(layout_mode(width)) + padding(app.find("inventory-content"))
    taken += padding(app.find("detail-pane"))
    sidebar, handle = app.find("inventory-sidebar"), app.find("sidebar-resize")
    if sidebar.visible:
        taken += sidebar.width
    if handle.visible:
        taken += handle.width
    return width - taken


async def drag(app: Any, delta: float) -> None:
    event = {"l": {"x": 0, "y": 0}, "g": {"x": 0, "y": 0}, "pd": delta}
    await app.set_props_and_fire("sidebar-resize", {}, "horizontal_drag_update", event)


async def report(app: Any, width: int) -> float:
    """What Flutter would report for the screen's surface once the shell is laid out."""
    available = given(app, width)
    await app.set_props_and_fire("changes-surface", {}, "size_change", {"w": available, "h": 700})
    return available


@pytest.mark.parametrize(
    ("width", "resize", "side_by_side"),
    [
        pytest.param(1100, None, False, id="1100-sidebar-visible"),
        pytest.param(1100, -900, False, id="1100-sidebar-narrowest"),
        pytest.param(1100, 900, False, id="1100-sidebar-widest"),
        pytest.param(1376, None, True, id="1376-sidebar-visible"),
        pytest.param(1376, -900, True, id="1376-sidebar-narrowest"),
        pytest.param(1376, 900, True, id="1376-sidebar-widest"),
    ],
)
def test_the_split_uses_the_width_the_real_shell_leaves(
    width: int, resize: float | None, side_by_side: bool
) -> None:
    async def scenario() -> None:
        transport = Transport()
        bridge = RouteBridge(route=ROUTE, width=width, height=800)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport)
        try:
            await app.wait_for("changes-view-graph")
            await click(app, "changes-view-graph")
            await app.wait_for(row_key(transport.git, 0))
            if resize is not None:
                await drag(app, resize)
            available = await report(app, width)
            await click(app, row_key(transport.git, 0))
            await app.wait_for("commit-pane")
            if side_by_side:
                await app.wait_for("changes-graph")
                graph = app.find("graph-pane").width
                assert graph >= GRAPH_MIN
                assert available - graph - DIVIDER >= COMMIT_MIN
                assert app.find("commit-slot").expand is True
            else:
                # Both panes cannot be usable: the commit is pushed over the graph.
                assert not present(app, "changes-graph") and present(app, "commit-back")
                await click(app, "commit-back")
                await app.wait_for("changes-graph")
                assert app.find(row_key(transport.git, 0)).data["selected"]
        finally:
            await pool.shutdown()

    run(scenario())


def test_the_choice_follows_the_width_as_the_sidebar_is_resized() -> None:
    async def scenario() -> None:
        transport = Transport()
        bridge = RouteBridge(route=ROUTE, width=1100, height=800)
        app, _, _, pool, _ = await _render(bridge=bridge, transport=transport)
        try:
            await app.wait_for("changes-view-graph")
            await click(app, "changes-view-graph")
            await app.wait_for(row_key(transport.git, 0))
            await report(app, 1100)
            await click(app, row_key(transport.git, 0))
            await app.wait_for("commit-back")  # pushed at the default sidebar
            await app.set_props_and_fire("console-collapse-toggle", {}, "click")
            await report(app, 1100)  # a collapsed sidebar leaves room for both
            await until(lambda: present(app, "graph-pane"))
            assert present(app, "changes-graph") and present(app, "commit-pane")
            assert app.find("graph-pane").width >= GRAPH_MIN
        finally:
            await pool.shutdown()

    run(scenario())
