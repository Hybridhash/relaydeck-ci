"""Lists of rows measured by the client keep the reader's place as heights are learned."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import flet as ft
import pytest

from relaydeck.client.design_system.lists import virtual_list
from relaydeck.client.design_system.lists.virtual_list import Place
from relaydeck.client.models.inventory_selectors import AgentRow
from relaydeck.client.screens.console.parts.lists import AGENT_CARD_ESTIMATE, AgentsList
from relaydeck.domain.models import AgentStatus
from tests.support.host_harness import render_component, run
from tests.support.ui.virtual import answer_scroll, extents, leading_space

CARD = AGENT_CARD_ESTIMATE + 10  # a card and the space below it, as estimated
REAL = 130.0  # what the client lays a card out at (a changes row and a subtitle present)
COUNT = 4096


def agents() -> tuple[AgentRow, ...]:
    return tuple(
        AgentRow(
            key=f"h:a{i}",
            title=f"Agent {i}",
            subtitle="Synthetic work",
            status=AgentStatus.IDLE,
            focused=False,
            agent="claude",
            subject="A synthetic task",
        )
        for i in range(COUNT)
    )


def list_of(app: Any) -> Any:
    return app.find("agents-tiles")


def cards(app: Any) -> list[Any]:
    return [c for c in list_of(app).controls if not str(c.key).endswith(("-before", "-after"))]


def number(card: Any) -> int:
    return int(str(card.content.key).rsplit("a", 1)[1])


def before_spacer(app: Any) -> float:
    return leading_space(app, "agents-tiles")


def scroll(app: Any, pixels: float) -> None:
    list_of(app).on_scroll(SimpleNamespace(pixels=pixels, viewport_dimension=800.0))


async def lay_out(app: Any, height: float = REAL) -> None:
    """The client reports the height of every card that is built."""
    for card in cards(app):
        card.on_size_change(SimpleNamespace(height=height, width=390.0))
    await asyncio.sleep(0.2)  # the debounced render
    await app.settle(5)


async def mount(positions: Any = None) -> Any:
    rows = agents()

    @ft.component
    def View() -> ft.Control:
        view: ft.Control = AgentsList(
            rows, "", lambda key: None, lambda: None, phone=True, scroll_positions=positions
        )
        return view

    app = await render_component(View)
    await app.wait_for("agents-tiles")
    return app


def test_learning_card_heights_after_a_jump_does_not_throw_the_reader_off_the_rows() -> None:
    async def scenario() -> None:
        app = await mount()
        scroll(app, CARD * 2_000)  # a jump to the middle of the list
        await asyncio.sleep(0.1)
        await app.settle(5)
        before = before_spacer(app)
        assert 1_990 <= number(cards(app)[len(cards(app)) // 2]) <= 2_010
        await lay_out(app)
        # The spacer moved by the rows that entered or left the window, not by every row above.
        assert abs(before_spacer(app) - before) < 12 * REAL, (before, before_spacer(app))
        assert 1_950 <= number(cards(app)[len(cards(app)) // 2]) <= 2_050

    run(scenario())


def test_a_height_learned_above_the_first_row_in_view_scrolls_to_keep_that_row_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[float] = []

    async def record(listing: Any, offset: float) -> None:
        sent.append(offset)

    monkeypatch.setattr(virtual_list, "scroll_list", record)

    async def scenario() -> None:
        app = await mount()
        scroll(app, CARD * 2_000 + 30)  # the reader is 30 dp into card 2,000
        await asyncio.sleep(0.1)
        await app.settle(5)
        sent.clear()
        above = [c for c in cards(app) if number(c) < 2_000][-4:]
        for card in above:  # four cards just above the one in view turn out taller
            card.on_size_change(SimpleNamespace(height=REAL, width=390.0))
        await asyncio.sleep(0.2)
        await app.settle(5)
        assert sent, "the list did not follow the change"
        # The row in view moved down by what the four cards grew; the offset follows it.
        assert abs(sent[-1] - (CARD * 2_000 + 30 + 4 * (REAL - CARD))) < 1.0, sent

    run(scenario())


def test_a_return_opens_at_the_same_card_when_it_is_laid_out_taller_than_estimated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions = SimpleNamespace(current={})

    async def client(listing: Any, offset: float) -> bool:
        answer_scroll(listing, offset)
        return True

    monkeypatch.setattr(virtual_list, "scroll_list", client)

    async def scenario() -> None:
        first = await mount(positions)
        scroll(first, CARD * 100)
        await asyncio.sleep(0.1)
        await lay_out(first)
        built = cards(first)
        top = before_spacer(first) + (100 - number(built[0])) * REAL  # where card 100 really sits
        scroll(first, top + 20)  # 20 dp into it
        remembered = positions.current["agents-tiles"]
        assert isinstance(remembered, Place) and remembered.index == 100
        assert abs(remembered.offset - 20) < 1
        second = await mount(positions)  # leaving and coming back: a fresh mount, fresh estimates
        shown = [number(c) for c in cards(second)]
        assert min(shown) <= 100 <= max(shown), (shown[0], shown[-1])

    run(scenario())


def test_each_card_is_sent_with_its_estimate_and_then_with_its_measured_height() -> None:
    async def scenario() -> None:
        app = await mount()
        sent = extents(app, "agents-tiles")
        assert len(sent) == len(list_of(app).controls)
        assert all(extent == CARD for extent in sent[: len(cards(app))])  # the estimate
        await lay_out(app)
        sent = extents(app, "agents-tiles")
        assert all(extent == REAL for extent in sent[: len(cards(app))])  # what the client said
        # The unbuilt rows below count at the average of what was measured.
        assert sent[len(cards(app))] == (COUNT - len(cards(app))) * REAL

    run(scenario())
