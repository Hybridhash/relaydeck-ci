"""A graph refreshed while the reader is scrolled down shows rows where the list really is."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import flet as ft
import pytest

import relaydeck.client.screens.changes.controller as controller
from relaydeck.client.design_system.lists import virtual_list as vl
from tests.support.host_harness import run
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import click, files, render, row_key, until

ROW = 56  # the wide board
VIEWPORT = 600.0


def built(app: Any) -> list[int]:
    """The commit numbers of the rows the graph has built."""
    listing = app.find("changes-graph")
    return [
        int(cast(Any, c.content).key.rsplit("-", 1)[1], 16)
        for c in listing.controls
        if isinstance(c, ft.Semantics)
    ]


def covers(app: Any, pixels: float) -> bool:
    rows = built(app)
    return (
        bool(rows)
        and min(rows) <= int(pixels // ROW)
        and max(rows) >= int((pixels + VIEWPORT - 1) // ROW)
    )


def test_a_refresh_while_scrolled_down_with_the_scroll_request_ignored_builds_where_the_list_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def ignored(listing: object, offset: float) -> bool:
        return False  # the client does not take the scroll_to

    monkeypatch.setattr(vl, "scroll_list", ignored)
    monkeypatch.setattr(controller, "GRAPH_CHECK_INTERVAL_S", 0.02)
    monkeypatch.setattr(vl, "SETTLE_DELAY_S", 0.05)
    gateway = Gateway(files(), commit_total=300, graph_page_size=100)

    async def scenario() -> None:
        app = await render(gateway, width=1376, height=1000, wide=True)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for(row_key(gateway, 0))
        down = 60.0 * ROW
        app.find("changes-graph").on_scroll(
            SimpleNamespace(event_type="update", pixels=down, viewport_dimension=VIEWPORT)
        )
        await until(lambda: covers(app, down))
        gateway.graph_revision = "-moved"  # the graph is read again: another context
        await app.wait_for("changes-updated")
        await click(app, "changes-updated")
        await app.wait_for(f"graph-row-{gateway.snapshot('branch')}:graph-moved-{0:040x}")
        await asyncio.sleep(vl.SETTLE_DELAY_S * 4)
        assert covers(app, 0.0) and not any(n > 40 for n in built(app))

    run(scenario())


def test_a_graph_context_change_keeps_the_place_the_client_confirmed() -> None:
    gateway = Gateway(files(), commit_total=300, graph_page_size=100)

    async def scenario() -> None:
        app = await render(gateway, width=1376, height=1000, wide=True)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for(row_key(gateway, 0))
        down = 60.0 * ROW
        app.find("changes-graph").on_scroll(
            SimpleNamespace(event_type="update", pixels=down, viewport_dimension=VIEWPORT)
        )
        await until(lambda: covers(app, down))
        await click(app, "graph-mode-branch")
        await until(lambda: app.find("graph-mode-branch").data["selected"])
        await click(app, "graph-mode-all")  # opens where the reader was; the client takes it
        await until(lambda: app.find("graph-mode-all").data["selected"])
        await asyncio.sleep(vl.SETTLE_DELAY_S * 3)
        assert covers(app, down) and not covers(app, 0.0)

    run(scenario())
