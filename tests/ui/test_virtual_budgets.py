"""Lists that grow with the data mount, select and update by what is on screen."""

from __future__ import annotations

import gc
import time
from typing import Any

import flet as ft

from relaydeck.client.design_system.tokens import usage_palette
from relaydeck.client.features.changes.copy_target import CopyLine, HunkGroup
from relaydeck.client.models.inventory_selectors import AgentRow, ShellRow
from relaydeck.client.screens.changes.parts.copy_sheet import CopySheet
from relaydeck.client.screens.changes.parts.diff_view import DiffFileView, DisplayRow
from relaydeck.client.screens.changes.parts.file_list_view import FileListView
from relaydeck.client.screens.console.parts.lists import (
    AgentsList,
    ShellCardsList,
    ShellsList,
)
from relaydeck.client.screens.usage.model import SpaceRow
from relaydeck.client.screens.usage.parts.space_sheet import SpaceSheet
from relaydeck.client.widgets.sessions.shell_cards import ShellListRow
from relaydeck.contracts.git import FileEntry
from relaydeck.domain.models import AgentStatus
from tests.support.host_harness import render_component, run
from tests.support.patch_meter import PatchMeter

BUDGET_S = 0.05  # the interaction budget, for the work of one update (patch work included)
MOUNTED = 600  # controls a window may mount, however much data there is


def noop(*_: Any) -> None:
    return None


def count(app: Any) -> int:
    return sum(1 for _ in app.session.index.values())


async def interact(app: Any, meter: PatchMeter, act: Any) -> tuple[float, int]:
    meter.reset()
    started = time.process_time()
    act()
    await app.settle(2)
    return time.process_time() - started, meter.bytes


def entries(n: int) -> tuple[FileEntry, ...]:
    return tuple(
        FileEntry(
            file_id=f"f{i}",
            path=f"pkg/d{i // 100}/f{i}.py",
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


def test_five_thousand_files_flat_and_as_a_tree() -> None:
    holder: dict[str, Any] = {}
    files = entries(5000)

    for tree in (None, frozenset[str]()):

        @ft.component
        def View(tree: frozenset[str] | None = tree) -> ft.Control:
            chosen, pick_any = ft.use_state(None)
            pick: Any = pick_any
            holder["pick"] = pick
            view: ft.Control = FileListView(
                name="files",
                files=files,
                query="",
                selected=chosen,
                seen=lambda _: False,
                on_pick=lambda file_id: pick(file_id),
                generated_open=False,
                on_toggle_generated=noop,
                tree=tree,
                on_toggle_dir=noop if tree is not None else None,
            )
            return view

        async def scenario() -> None:
            started = time.process_time()
            app = await render_component(View)
            assert time.process_time() - started < 0.5
            meter = PatchMeter(app)
            assert count(app) < MOUNTED
            seconds, sent = await interact(app, meter, lambda: holder["pick"]("f1"))
            assert seconds < BUDGET_S and sent < 2_000, (seconds, sent)
            assert count(app) < MOUNTED

        run(scenario())


def test_four_thousand_ninety_six_agents() -> None:
    holder: dict[str, Any] = {}
    agents = tuple(
        AgentRow(
            key=f"h:a{i}",
            title=f"Agent {i}",
            subtitle="Synthetic work",
            status=AgentStatus.IDLE,
            focused=False,
            agent="claude",
            subject="A synthetic task",
        )
        for i in range(4096)
    )

    @ft.component
    def View() -> ft.Control:
        chosen, pick = ft.use_state("")
        holder["pick"] = pick
        view: ft.Control = AgentsList(agents, chosen, pick, noop, phone=True)
        return view

    async def scenario() -> None:
        app = await render_component(View)
        meter = PatchMeter(app)
        assert count(app) < MOUNTED
        seconds, sent = await interact(app, meter, lambda: holder["pick"]("h:a1"))
        assert seconds < BUDGET_S and sent < 4_000, (seconds, sent)

    run(scenario())


def test_four_thousand_ninety_six_shells() -> None:
    holder: dict[str, Any] = {}
    cards = tuple(
        ShellListRow(f"h:s{i}", f"shell {i}", "pty · 2m", i % 2 == 0) for i in range(4096)
    )
    shells = tuple(ShellRow(f"h:s{i}", f"shell {i}", "pty") for i in range(4096))

    def view(kind: str, chosen: str, pick: Any) -> ft.Control:
        built: ft.Control = (
            ShellCardsList(cards, chosen, pick)
            if kind == "cards"
            else ShellsList(shells, chosen, pick, noop, dark=False)
        )
        return built

    async def scenario(kind: str, target: str) -> None:
        @ft.component
        def View() -> ft.Control:
            chosen, pick_any = ft.use_state("")
            pick: Any = pick_any
            holder["pick"] = pick
            return view(kind, chosen, pick)

        app = await render_component(View)
        meter = PatchMeter(app)
        assert count(app) < MOUNTED, kind
        gc.collect()
        seconds, sent = await interact(app, meter, lambda: holder["pick"](target))
        assert seconds < BUDGET_S and sent < 4_000, (kind, seconds, sent)

    async def all_kinds() -> None:
        await scenario("cards", "h:s1")
        await scenario("shells", "h:s1")

    run(all_kinds())


def test_five_hundred_diff_rows_and_copy_lines() -> None:
    holder: dict[str, Any] = {}
    rows = (
        DisplayRow("hunk", "@@ -1 +1 @@", page=0, hunk=0),
        *(DisplayRow("add", "synthetic line content", new=i + 1, line=i) for i in range(500)),
    )

    @ft.component
    def Diff() -> ft.Control:
        chosen, pick_any = ft.use_state(None)
        pick: Any = pick_any
        holder["pick"] = pick
        view: ft.Control = DiffFileView(
            "Synthetic",
            "Working",
            rows,
            on_hunk=lambda place: pick(place),
            selected_hunk=chosen,
        )
        return view

    async def diff() -> None:
        started = time.process_time()
        app = await render_component(Diff)
        assert time.process_time() - started < 0.5
        meter = PatchMeter(app)
        assert count(app) < MOUNTED
        seconds, sent = await interact(app, meter, lambda: holder["pick"]((0, 0)))
        assert seconds < BUDGET_S and sent < 2_000, (seconds, sent)

    run(diff())

    lines = tuple(CopyLine(0, 0, i, "add", "synthetic content", False, i + 1) for i in range(500))
    group = HunkGroup(0, 0, "@@ -1 +1 @@", lines, True)

    async def sheet() -> None:
        app = await render_component(CopySheet, (group,), noop, noop, noop)
        meter = PatchMeter(app)
        seconds, sent = await interact(
            app,
            meter,
            lambda: next(
                c for c in app.session.index.values() if c.key == "changes-copy-hunk-0-lines"
            ).on_click(None),
        )
        await app.settle(5)
        assert seconds < BUDGET_S and count(app) < MOUNTED

    run(sheet())


def hunk_groups(count: int, body: int) -> tuple[HunkGroup, ...]:
    return tuple(
        HunkGroup(
            i // 60,
            i % 60,
            f"@@ -{i * 20 + 1},{body} +{i * 21 + 1},{body} @@",
            tuple(
                CopyLine(i // 60, i % 60, j, "add" if j == 0 else "context", "synthetic", False, j)
                for j in range(body)
            ),
            True,
        )
        for i in range(count)
    )


def test_hundreds_of_hunks_open_and_come_back_from_lines_within_the_budget() -> None:
    async def scenario(groups: tuple[HunkGroup, ...]) -> None:
        asked: list[tuple[Any, ...]] = []
        app = await render_component(
            CopySheet,
            groups,
            lambda group, form: None,
            noop,
            noop,
            lambda kind, **place: asked.append((kind, place)),
        )
        meter = PatchMeter(app)
        assert count(app) < MOUNTED  # the overview is a window, not every hunk
        opened = app.find("changes-copy-hunk-0-lines")
        seconds, _ = await interact(app, meter, lambda: opened.on_click(None))
        assert seconds < BUDGET_S and count(app) < MOUNTED
        meter.reset()
        started = time.process_time()
        await app.set_props_and_fire("changes-copy-back", {}, "click")
        await app.settle(2)
        seconds, sent = time.process_time() - started, meter.bytes
        assert seconds < BUDGET_S and count(app) < MOUNTED, (seconds, count(app))
        assert sent < 60_000, sent
        # The hunk that was opened is where the reader returns, and Ask still reaches its place.
        await app.settle(3)
        app.find("changes-ask-hunk-0-0-None").on_click(None)
        assert asked == [("hunk", {"page": 0, "hunk": 0, "row": None})]

    run(scenario(hunk_groups(180, 7)))  # three pages of 60 hunks, seven lines each
    run(scenario(hunk_groups(250, 1)))


def test_five_hundred_spaces() -> None:
    holder: dict[str, Any] = {}
    spaces = tuple(SpaceRow(str(i), f"Space {i}", i, str(i), False, False) for i in range(500))

    @ft.component
    def View() -> ft.Control:
        chosen, pick = ft.use_state("")
        holder["pick"] = pick
        view: ft.Control = SpaceSheet(
            open=True,
            rows=spaces,
            selected=chosen,
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
        meter = PatchMeter(app)
        assert count(app) < MOUNTED
        gc.collect()
        seconds, sent = await interact(app, meter, lambda: holder["pick"]("1"))
        assert seconds < BUDGET_S and sent < 4_000, (seconds, sent)

    run(scenario())
