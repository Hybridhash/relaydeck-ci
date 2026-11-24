"""A list whose reader stops where the build did not reach is corrected with no further event."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.design_system.lists import virtual_list as vl
from tests.support.host_harness import HostApp, render_component, run
from tests.support.ui.virtual import leading_space

ROW = 56.0
COUNT = 200


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vl, "SETTLE_DELAY_S", 0.03)
    monkeypatch.setattr(vl, "FILL_DELAY_S", 0.0)


async def mount(holder: dict[str, Any], count: int = COUNT) -> HostApp:
    @ft.component
    def View() -> ft.Control:
        total, holder["count"] = ft.use_state(count)
        context, holder["context"] = ft.use_state("rows")
        place, holder["place"] = ft.use_state(None)
        view: ft.Control = vl.VirtualList(
            name="list",
            count=total,
            height=ROW,
            row=lambda i: vl.Row(f"row-{i}", i, lambda: ft.Text(str(i))),
            context=context,
            place=place,
            footer=[ft.Text("older", key="older")],
        )
        return view

    app = await render_component(View)
    await app.settle(5)
    return app


def event(kind: str, pixels: float, port: float = 600.0, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(event_type=kind, pixels=pixels, viewport_dimension=port, **extra)


def built_rows(app: HostApp) -> tuple[int, int]:
    """The first and last row built (not the spacers or the footer)."""
    numbers = [
        int(str(c.key).split("-")[1])
        for c in app.find("list").controls
        if str(getattr(c, "key", "")).startswith("row-")
    ]
    return min(numbers), max(numbers)


def covers(app: HostApp, pixels: float, port: float) -> bool:
    first, last = built_rows(app)
    return first <= int(pixels // ROW) and last >= int((pixels + port - 1) // ROW)


async def later(app: HostApp) -> None:
    await asyncio.sleep(vl.SETTLE_DELAY_S * 3)
    await app.settle(5)


def test_a_viewport_that_grows_without_a_scroll_event_gets_its_rows() -> None:
    async def scenario() -> None:
        app = await mount({})
        assert not covers(app, 0, 3000.0)  # built for the 800 dp assumed before any report
        app.find("list").on_size_change(SimpleNamespace(width=300.0, height=3000.0))
        await later(app)
        assert covers(app, 0, 3000.0)

    run(scenario())


def test_the_end_of_a_scroll_settles_at_once_even_when_updates_were_thinned() -> None:
    async def scenario() -> None:
        app = await mount({})
        app.find("list").on_scroll(event("update", 0.0))
        far = 150 * ROW  # a fling the thinned updates never reported: only the end arrives
        app.find("list").on_scroll(event("end", far))
        await app.settle(3)  # no settle delay waited
        assert covers(app, far, 600.0)
        await later(app)
        first, last = built_rows(app)
        assert first < 150 and last > 150 + 600 / ROW  # and the margins follow

    run(scenario())


def test_pulling_past_the_end_counts_as_reaching_it_and_shows_the_footer() -> None:
    async def scenario() -> None:
        app = await mount({})
        end = COUNT * ROW
        # The client reports a position well short of the end together with an overscroll.
        app.find("list").on_scroll(event("overscroll", end - 3000.0, overscroll=40.0))
        await later(app)
        assert built_rows(app)[1] == COUNT - 1
        assert app.find("list-after").height == 0 and app.find("older")

    run(scenario())


def test_rows_that_arrive_while_the_reader_is_at_the_end_are_built() -> None:
    holder: dict[str, Any] = {}

    async def scenario() -> None:
        app = await mount(holder, count=100)
        app.find("list").on_scroll(event("end", 100 * ROW))
        await later(app)
        assert built_rows(app)[1] == 99
        holder["count"](160)
        await later(app)
        assert built_rows(app)[1] > 99 and covers(app, 100 * ROW - 600.0 + 1, 600.0)

    run(scenario())


def test_a_burst_of_reports_costs_one_check_and_an_end_costs_one_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = vl.shift

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    async def scenario() -> None:
        app = await mount({})
        await later(app)  # the check that follows the mount
        monkeypatch.setattr(vl, "shift", counting)
        for step in range(40):
            app.find("list").on_scroll(event("update", float(step * 4)))
        for step in range(5):
            app.find("list").on_size_change(SimpleNamespace(width=300.0, height=600.0 + step))
        await later(app)
        assert len(calls) == 40 + 1  # one per event, then one settle for the whole burst
        calls.clear()
        app.find("list").on_scroll(event("end", 160.0))
        await later(app)
        assert len(calls) == 2  # the event's own, and the end's immediate settle; none after

    run(scenario())


def test_a_size_report_inside_the_tolerance_checks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = vl.shift

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    async def scenario() -> None:
        app = await mount({})
        app.find("list").on_scroll(event("update", 0.0, port=600.0))
        await later(app)
        monkeypatch.setattr(vl, "shift", counting)
        app.find("list").on_size_change(SimpleNamespace(width=300.0, height=600.5))
        app.find("list").on_size_change(SimpleNamespace(width=300.0, height=601.5))
        await later(app)
        assert calls == []

    run(scenario())


def test_a_context_change_whose_scroll_request_is_ignored_builds_where_the_list_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(vl, "SETTLE_DELAY_S", 0.2)  # long enough to see the request's window
    holder: dict[str, Any] = {}

    async def scenario() -> None:
        app = await mount(holder, count=400)
        down = 60 * ROW
        app.find("list").on_scroll(event("update", down))
        await later(app)
        assert covers(app, down, 600.0)
        holder["context"]("refreshed")  # opens at the top; the list never takes the scroll_to
        await app.settle(3)
        assert built_rows(app)[0] == 0 and not covers(app, down, 600.0)  # built for the request
        await later(app)
        assert covers(app, down, 600.0)  # rebuilt where the list reported it is
        assert leading_space(app, "list") != 0

    run(scenario())


def test_a_scroll_the_list_confirms_keeps_the_new_contexts_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def answered(listing: Any, offset: float) -> bool:
        return True  # the client took the request; it need not report a position

    async def scenario(by_event: bool) -> None:
        holder: dict[str, Any] = {}
        app = await mount(holder, count=400)
        app.find("list").on_scroll(event("update", 60 * ROW))
        await later(app)
        holder["context"]("refreshed")
        await app.settle(3)
        if by_event:
            app.find("list").on_scroll(event("update", 0.0))  # near the requested top
        await later(app)
        assert covers(app, 0.0, 600.0) and not covers(app, 60 * ROW, 600.0)

    run(scenario(True))
    monkeypatch.setattr(vl, "scroll_list", answered)
    run(scenario(False))


def test_a_list_that_never_reported_is_taken_to_be_at_the_top_until_it_confirms() -> None:
    holder: dict[str, Any] = {}

    async def scenario() -> None:
        app = await mount(holder, count=400)
        holder["place"](vl.Place(60))  # a return to a place; the list never takes the scroll_to
        holder["context"]("returned")
        await later(app)
        assert covers(app, 0.0, 600.0) and not covers(app, 60 * ROW, 600.0)

    run(scenario())


def test_a_list_that_takes_the_request_of_a_return_keeps_its_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def answered(listing: Any, offset: float) -> bool:
        return True

    monkeypatch.setattr(vl, "scroll_list", answered)
    holder: dict[str, Any] = {}

    async def scenario() -> None:
        app = await mount(holder, count=400)
        holder["place"](vl.Place(60))
        holder["context"]("returned")
        await later(app)
        assert covers(app, 60 * ROW, 600.0) and not covers(app, 0.0, 600.0)

    run(scenario())


def test_an_end_that_itself_shifted_the_window_does_not_settle_again_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    real = vl.shift

    def counting(*args: Any) -> Any:
        calls.append(1)
        return real(*args)

    async def scenario() -> None:
        app = await mount({})
        await later(app)
        monkeypatch.setattr(vl, "shift", counting)
        app.find("list").on_scroll(event("end", 150 * ROW))  # far: this event shifts the window
        assert len(calls) == 1  # no second, immediate pass over the same window
        await later(app)
        assert len(calls) == 2  # the debounced check still follows

    run(scenario())
