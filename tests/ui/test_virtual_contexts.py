"""A list that changes what it shows adopts the new window at once, whatever the client reports."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft

from relaydeck.client.design_system.lists import virtual_list as vl
from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.models.inventory_selectors import AgentRow
from relaydeck.client.screens.changes.parts.file_list_view import FileListView
from relaydeck.client.screens.console.parts.lists import AgentsList
from relaydeck.client.screens.usage.model import SpaceRow
from relaydeck.client.screens.usage.parts.space_sheet import SpaceSheet
from relaydeck.contracts.git import FileEntry
from relaydeck.domain.models import AgentStatus
from tests.support.host_harness import HostApp, render_component, run
from tests.support.ui.virtual import leading_space


def noop(*_: Any) -> None:
    return None


def entries(n: int) -> tuple[FileEntry, ...]:
    return tuple(
        FileEntry(
            file_id=f"f{i}",
            path=f"pkg/f{i}.py",
            status="M",
            additions=1,
            deletions=1,
            staged=False,
            unstaged=True,
            generated=False,
            withheld=False,
            rename_limited=False,
            renderable=True,
        )
        for i in range(n)
    )


def scroll(app: HostApp, name: str, pixels: float) -> None:
    app.find(name).on_scroll(SimpleNamespace(pixels=pixels, viewport_dimension=600.0))


def top_of(app: HostApp, name: str) -> tuple[float, int]:
    """The leading space and how many rows are built: a blank viewport is a big space at zero."""
    return leading_space(app, name), app.find(name).data["built"]


def test_a_context_that_comes_back_opens_at_the_top_not_where_the_other_one_was() -> None:
    holder: dict[str, Any] = {}

    @ft.component
    def View() -> ft.Control:
        context, holder["set"] = ft.use_state("full")
        view: ft.Control = vl.VirtualList(
            name="list",
            count=5000 if context == "full" else 1,
            height=56,
            row=lambda i: vl.Row(f"{context}-{i}", (context, i), lambda: ft.Text(str(i))),
            context=context,
        )
        return view

    async def scenario() -> None:
        app = await render_component(View)
        scroll(app, "list", 56_000.0)
        await app.settle(8)
        assert top_of(app, "list")[0] > 50_000
        holder["set"]("search")
        await app.settle(8)
        holder["set"]("full")  # no scroll event follows: the client was already at zero
        await app.settle(8)
        before, built = top_of(app, "list")
        assert before == 0 and built > 15

    run(scenario())


def test_clearing_a_file_search_shows_the_top_of_the_list_without_another_scroll() -> None:
    files = entries(5000)
    holder: dict[str, Any] = {}

    @ft.component
    def View() -> ft.Control:
        query, holder["set"] = ft.use_state("")
        view: ft.Control = FileListView(
            name="files",
            files=files,
            query=query,
            selected=None,
            seen=lambda _: False,
            on_pick=noop,
            generated_open=False,
            on_toggle_generated=noop,
        )
        return view

    async def scenario() -> None:
        app = await render_component(View)
        scroll(app, "files", 56_000.0)
        await app.settle(8)
        holder["set"]("f4999.py")
        await app.settle(8)
        scroll(app, "files", 0.0)  # the client reports the reset it was asked for
        await app.settle(8)
        holder["set"]("")
        await app.settle(8)
        before, built = top_of(app, "files")
        assert before == 0 and built > 15

    run(scenario())


def test_changing_the_spaces_search_shows_the_top_of_the_list() -> None:
    spaces = tuple(SpaceRow(str(i), f"Space {i}", i, str(i), False, False) for i in range(500))

    @ft.component
    def View() -> ft.Control:
        view: ft.Control = SpaceSheet(
            open=True,
            rows=spaces,
            selected="",
            pins=(),
            all_value="all",
            tokens=usage_palette(True),
            wide=False,
            on_done=noop,
            on_cancel=noop,
            on_pins=noop,
        )
        return view

    async def scenario() -> None:
        app = await render_component(View)
        scroll(app, "usage-space-list", 50 * 400.0)
        await app.settle(8)
        assert top_of(app, "usage-space-list")[0] > 10_000
        field = app.find("usage-space-search")
        app.session.apply_patch(field._i, {"value": "Space 49"})
        await app.session.dispatch_event(field._i, "change", "Space 49")
        await app.settle(8)
        app.session.apply_patch(field._i, {"value": ""})
        await app.session.dispatch_event(field._i, "change", "")
        await app.settle(8)
        before, built = top_of(app, "usage-space-list")
        assert before == 0 and built > 5

    run(scenario())


def test_measured_heights_of_rows_that_are_gone_are_not_kept() -> None:
    holder: dict[str, Any] = {}

    def agents(batch: int) -> tuple[AgentRow, ...]:
        return tuple(
            AgentRow(
                key=f"h:{batch}:a{i}",
                title=f"Agent {i}",
                subtitle="Synthetic",
                status=AgentStatus.IDLE,
                focused=False,
                agent="claude",
                subject="Task",
            )
            for i in range(20)
        )

    seen: list[Any] = []
    original = vl._Measures

    class Watched(original):  # type: ignore[valid-type, misc]
        def __init__(self) -> None:
            super().__init__()
            seen.append(self)

    vl._Measures = Watched  # type: ignore[misc]

    @ft.component
    def View() -> ft.Control:
        batch, holder["bump"] = ft.use_state(0)
        view: ft.Control = AgentsList(agents(batch), "", noop, noop, phone=True)
        return view

    async def scenario() -> None:
        try:
            app = await render_component(View)
            for batch in range(1, 101):  # the Console's agents are replaced, a hundred times over
                holder["bump"](batch)
                await app.settle(2)
                for card in app.find("agents-tiles").controls[1:-1]:
                    card.on_size_change(SimpleNamespace(height=106.0, width=390.0))
                await asyncio.sleep(0.06)
            await app.settle(5)
        finally:
            vl._Measures = original  # type: ignore[misc]
        (measures,) = seen
        assert len(measures.heights) <= 20, len(measures.heights)

    run(scenario())
