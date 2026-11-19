"""A large graph mounts, selects and scrolls by what is on screen, not by what is loaded."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

import relaydeck.client.screens.changes.parts.graph_rows as graph_rows
from relaydeck.client.features.changes.graph_state import GraphAnchor
from relaydeck.client.features.changes.reading_bookmark import ReadingBookmark
from relaydeck.client.features.changes.state import ChangesModel
from tests.support.commit_graph import resident_graph
from tests.support.host_harness import run
from tests.support.ui.virtual import leading_space
from tests.ui.git.gateway import AGENT, WORKTREE, Gateway
from tests.ui.git.harness import click, files, render, until

REVISION = "graph-1"
ROW = 60  # the phone-portrait board
SIZES = [
    pytest.param(390, 844, False, 60, id="phone-portrait"),
    pytest.param(844, 390, False, 60, id="phone-landscape-unmeasured"),
    pytest.param(1376, 1000, True, 56, id="tablet-landscape"),
]


def bookmark_for(
    count: int, anchor: GraphAnchor | None = None, *, ids: bool = True
) -> ReadingBookmark:
    state = resident_graph(count, ids=ids)
    model = ChangesModel(
        agent_id=AGENT,
        worktree_id=WORKTREE,
        scope="branch",
        view="graph",
        graph=replace(state, anchor=anchor),
    )
    return ReadingBookmark.capture(model)


def built(app: Any) -> list[Any]:
    listing = app.find("changes-graph")
    return [c for c in listing.controls if isinstance(c, ft.Semantics)]


def shown_range(app: Any) -> tuple[int, int]:
    keys = [c.content.key for c in built(app)]
    numbers = [int(k.rsplit("-", 1)[1], 16) for k in keys]
    return min(numbers), max(numbers)


def spacers(app: Any) -> tuple[float, float]:
    controls = app.find("changes-graph").controls
    after = next(c for c in controls if str(c.key).endswith("-after"))
    return leading_space(app, "changes-graph"), after.height


class Counted:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rows = 0
        original = graph_rows.build_row

        def build(*args: Any, **kwargs: Any) -> ft.Control:
            self.rows += 1
            return original(*args, **kwargs)

        monkeypatch.setattr(graph_rows, "build_row", build)


def scroll(app: Any, pixels: float) -> None:
    app.find("changes-graph").on_scroll(SimpleNamespace(pixels=pixels, viewport_dimension=800.0))


@pytest.mark.parametrize("count", [1_000, 10_000])
@pytest.mark.parametrize(("width", "height", "wide", "row"), SIZES)
def test_what_is_mounted_is_the_screen_whatever_the_history(
    count: int, width: int, height: int, wide: bool, row: int
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(
            gateway, width=width, height=height, wide=wide, bookmark=bookmark_for(count)
        )
        await app.wait_for("changes-graph")
        listing = app.find("changes-graph")
        assert listing.data["rows"] == count
        assert len(built(app)) < 60  # a screen and a screen either side
        before, after = spacers(app)
        assert before + after + len(built(app)) * row == count * row
        assert shown_range(app)[0] <= 1

    run(scenario())


def test_selecting_a_commit_builds_only_the_two_rows_whose_highlight_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        counted = Counted(monkeypatch)
        app = await render(gateway, bookmark=bookmark_for(10_000, ids=False))
        await app.wait_for("changes-graph")
        first = counted.rows
        assert first < 60
        rows = built(app)
        await click(app, rows[3].content.key)
        await app.settle(5)
        assert counted.rows - first <= 2
        other = counted.rows
        await click(app, built(app)[5].content.key)
        await app.settle(5)
        assert counted.rows - other <= 2

    run(scenario())


def test_commit_loading_never_rebuilds_the_graph_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        counted = Counted(monkeypatch)
        app = await render(
            gateway, width=1376, height=1000, wide=True, bookmark=bookmark_for(2_000)
        )
        await app.wait_for("changes-graph")
        await click(app, built(app)[2].content.key)
        await app.wait_for("diff-rows")  # the commit opens at its first file, which takes the pane
        settled = counted.rows
        await click(app, "changes-next")  # file and diff changes of the commit
        await app.wait_for("diff-rows")
        await app.settle(10)
        assert counted.rows == settled

    run(scenario())


def test_scrolling_moves_the_window_and_keeps_the_scroll_extent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        counted = Counted(monkeypatch)
        app = await render(gateway, bookmark=bookmark_for(10_000))
        await app.wait_for("changes-graph")
        before = counted.rows
        scroll(app, ROW * 5_000)
        await until(lambda: shown_range(app)[0] > 4_000 and len(built(app)) > 30)
        await app.settle(8)  # the margins follow the rows in view
        low, high = shown_range(app)
        assert low <= 5_000 <= high and len(built(app)) < 60
        top, bottom = spacers(app)
        assert top + bottom + len(built(app)) * ROW == 10_000 * ROW
        assert counted.rows - before < 60 + 5  # the new window, once
        # A step inside the window changes nothing.
        steady = counted.rows
        scroll(app, ROW * 5_000 + 112)
        await app.settle(5)
        assert counted.rows == steady
        # Back to the top.
        scroll(app, 0.0)
        await until(lambda: shown_range(app)[0] <= 1)

    run(scenario())


def test_memory_follows_the_window_not_the_history(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, bookmark=bookmark_for(5_000))
        await app.wait_for("changes-graph")
        for step in range(1, 40):
            scroll(app, ROW * 120 * step)
            await app.settle(1)
        assert len(built(app)) < 60
        assert app.find("changes-graph").data["rows"] == 5_000

    run(scenario())


def test_a_return_opens_the_window_at_the_readers_place() -> None:
    gateway = Gateway(files())
    anchor = GraphAnchor(f"{3_000:040x}", 14.0)

    async def scenario() -> None:
        app = await render(gateway, bookmark=bookmark_for(5_000, anchor))
        await app.wait_for("changes-graph")
        low, high = shown_range(app)
        assert low <= 3_000 <= high and len(built(app)) < 60
        top, bottom = spacers(app)
        assert top + bottom + len(built(app)) * ROW == 5_000 * ROW

    run(scenario())


def test_show_older_stays_after_the_data_and_the_last_rows_can_be_reached() -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, bookmark=bookmark_for(3_000))
        await app.wait_for("changes-graph")
        scroll(app, ROW * 3_000)
        await until(lambda: shown_range(app)[1] >= 3_000)
        listing = app.find("changes-graph")
        assert listing.controls[-1].key == "changes-more-graph"
        assert spacers(app)[1] == 0

    run(scenario())


def test_show_older_at_the_end_builds_the_new_rows_without_a_scroll_event() -> None:
    gateway = Gateway(files(), commit_total=199, graph_page_size=100, issue_commit_ids=False)

    async def scenario() -> None:
        app = await render(gateway, width=390, height=844)
        await app.wait_for("changes-files")
        await click(app, "changes-view-graph")
        await app.wait_for("changes-more-graph")
        scroll(app, ROW * 100.0)  # the bottom of the first page
        await until(lambda: shown_range(app)[1] >= 99)
        assert app.find("changes-graph").data["rows"] == 100
        await click(app, "changes-more-graph")
        await until(lambda: app.find("changes-graph").data["rows"] == 200)
        # No scroll event since the page arrived: the rows after the old end must be there.
        assert shown_range(app)[1] > 100
        top, bottom = spacers(app)
        assert top + bottom + len(built(app)) * ROW == 200 * ROW
        assert bottom < ROW * 100  # not a blank stretch the size of the new page

    run(scenario())


def test_a_jump_builds_the_visible_rows_first_and_the_margins_on_the_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = Gateway(files())

    async def scenario() -> None:
        counted = Counted(monkeypatch)
        app = await render(gateway, bookmark=bookmark_for(10_000, ids=False))
        await app.wait_for("changes-graph")
        before = counted.rows
        scroll(app, ROW * 5_000.0)
        await until(lambda: shown_range(app)[0] > 4_000)
        # The margins follow the first paint without another scroll event.
        await until(lambda: len(built(app)) > 30)
        assert counted.rows - before < 65
        low, high = shown_range(app)
        assert low <= 5_000 <= high

    run(scenario())


def test_a_step_in_the_middle_of_the_list_sends_the_rows_entering_not_the_window() -> None:
    from tests.support.patch_meter import PatchMeter

    gateway = Gateway(files())

    async def scenario() -> None:
        app = await render(gateway, bookmark=bookmark_for(10_000, ids=False))
        await app.wait_for("changes-graph")
        meter = PatchMeter(app)
        meter.reset()
        scroll(app, ROW * 5_000.0)  # a jump: the rows in view, then the margins
        await until(lambda: shown_range(app)[0] > 4_000 and len(built(app)) > 30)
        await app.settle(8)
        window, whole = len(built(app)), meter.bytes
        before = shown_range(app)
        meter.reset()
        scroll(app, ROW * 5_000.0 + ROW * 14)  # about one screen on: rows leave at the front
        await until(lambda: shown_range(app)[1] > before[1])
        await app.settle(8)
        entering = shown_range(app)[1] - before[1]
        assert 0 < entering < window
        # Per row, a step costs about what the window's own build did, not its whole size again.
        assert meter.bytes / entering < 2.5 * whole / window, (meter.bytes, entering, whole, window)
        assert meter.bytes < whole  # and never as much as sending the window afresh

    run(scenario())
