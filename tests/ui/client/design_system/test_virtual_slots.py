"""Every child of a windowed list is placed where its height says, at every scroll position.

The list sends the client the height of each child, and the client places each child at the sum
of the heights before it, below the list's padding. A child at an index is never drawn where the
child at that index was drawn before, so a window that slides never leaves rows in the wrong
place, whatever the rows' heights.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.design_system.lists import virtual_list as vl
from tests.support.host_harness import HostApp, render_component, run
from tests.support.ui.virtual import extents, leading_space, placed

ROW = 56.0
COUNT = 300
PORT = 600.0
FOOTER = 48.0


@pytest.fixture(autouse=True)
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vl, "SETTLE_DELAY_S", 0.03)
    monkeypatch.setattr(vl, "FILL_DELAY_S", 0.0)


async def mount(
    offsets: tuple[float, ...] | None = None, footer: list[ft.Control] | None = None
) -> HostApp:
    @ft.component
    def View() -> ft.Control:
        return vl.VirtualList(
            name="list",
            count=COUNT,
            height=ROW,
            offsets=offsets,
            row=lambda i: vl.Row(f"row-{i}", i, lambda: ft.Container(height=ROW)),
            context="rows",
            footer=[ft.Container(key="older", height=FOOTER)] if footer is None else footer,
        )

    app = await render_component(View)
    app.session.apply_patch(app.page._i, {"width": 800, "height": 800})
    await app.settle()
    return app


def scroll(app: HostApp, pixels: float) -> None:
    app.find("list").on_scroll(
        SimpleNamespace(event_type="update", pixels=pixels, viewport_dimension=PORT)
    )


async def slid(app: HostApp, pixels: float) -> None:
    scroll(app, pixels)
    await app.settle(5)
    await asyncio.sleep(vl.SETTLE_DELAY_S * 3)


def rows_at_their_place(app: HostApp, top_of: Any) -> None:
    """Every built row is drawn at its own top; the spacer and footer follow the last row."""
    listing = app.find("list")
    children = placed(app, "list")
    sent = extents(app, "list")
    for (key, at), extent in zip(children, sent, strict=True):
        if key.startswith("row-"):
            number = int(key.split("-")[1])
            assert at == top_of(number) and extent == top_of(number + 1) - top_of(number), key
    last_row = max(int(k.split("-")[1]) for k, _ in children if k.startswith("row-"))
    after = next(i for i, c in enumerate(listing.controls) if c.key == "list-after")
    assert children[after][1] == top_of(last_row + 1)
    assert sent[after] == top_of(COUNT) - top_of(last_row + 1)
    assert children[-1][0] == "older" and children[-1][1] == top_of(COUNT)
    assert sent[-1] == FOOTER


def test_a_slid_window_places_every_row_at_its_own_top_in_both_directions() -> None:
    async def scenario() -> None:
        app = await mount()
        rows_at_their_place(app, lambda n: n * ROW)
        for pixels in (900.0, 1800.0, 2600.0, 4000.0, 9000.0, 16000.0, 600.0, 0.0):
            await slid(app, pixels)
            rows_at_their_place(app, lambda n: n * ROW)
            assert leading_space(app, "list") + sum(extents(app, "list")) == COUNT * ROW + FOOTER

    run(scenario())


def test_rows_of_different_heights_are_placed_by_their_own_tops() -> None:
    # A tall row every tenth, as a diff's hunk headers among its lines.
    tops: list[float] = [0.0]
    for n in range(COUNT):
        tops.append(tops[-1] + (120.0 if n % 10 == 0 else ROW))
    offsets = tuple(tops)

    async def scenario() -> None:
        app = await mount(offsets)
        rows_at_their_place(app, lambda n: offsets[n])
        for pixels in (900.0, 5000.0, 12000.0, 17000.0, 3000.0):
            await slid(app, pixels)
            rows_at_their_place(app, lambda n: offsets[n])

    run(scenario())


def test_the_first_child_of_a_long_list_is_a_row_not_a_spacer() -> None:
    async def scenario() -> None:
        app = await mount()
        await slid(app, 9000.0)
        controls: list[Any] = app.find("list").controls
        assert str(controls[0].key).startswith("row-") and leading_space(app, "list") > 0

    run(scenario())


def test_a_footer_without_a_height_is_given_one_touch_target() -> None:
    async def scenario() -> None:
        app = await mount(footer=[ft.Text("No more", key="note"), ft.Container(key="b", height=30)])
        sent = extents(app, "list")
        assert sent[-2:] == [vl.FOOTER_EXTENT, 30.0]
        assert isinstance(app.find("list"), vl.PlacedListView)

    run(scenario())
