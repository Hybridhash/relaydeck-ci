"""The Console asks for change summaries of the cards the list shows, not of every agent."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import Any

import flet as ft

from relaydeck.client.app.workflows.changes import use_changes_wiring
from relaydeck.client.features.changes.hooks import use_changes_feed
from relaydeck.client.features.changes.repository import ChangesRepository
from relaydeck.client.models.inventory_selectors import AgentRow, with_changes
from relaydeck.client.screens.console.parts.lists import AGENT_CARD_ESTIMATE, AgentsList
from relaydeck.domain.models import AgentStatus, scope_key
from tests.support.host_harness import render_component, run
from tests.support.patch_meter import PatchMeter
from tests.ui.git.gateway import Gateway
from tests.ui.git.harness import files, present, until

HOST = "home-mac"
COUNT = 200
CARD = AGENT_CARD_ESTIMATE + 10  # the card and the space below it, as the list estimates


def key_of(number: int) -> str:
    return scope_key(HOST, f"term-{number:03d}")


def number_of(agent: str) -> int:
    return int(agent.rsplit("-", 1)[1])


def rows() -> tuple[AgentRow, ...]:
    return tuple(
        AgentRow(
            key=key_of(n),
            title=f"agent {n}",
            subtitle="tool-a · 2 min ago",
            status=AgentStatus.WORKING,
            focused=False,
            host_id=HOST,
            workspace_id="w1",
            agent="tool-a",
        )
        for n in range(COUNT)
    )


def asked(gateway: Gateway) -> set[int]:
    return {number_of(a) for call in gateway.calls if call[0] == "summaries" for a in call[1]}


def more_calls(gateway: Gateway, seen: int) -> bool:
    return len(gateway.calls) > seen


def scroll(app: Any, pixels: float) -> None:
    app.find("agents-tiles").on_scroll(SimpleNamespace(pixels=pixels, viewport_dimension=800.0))


async def mount(gateway: Gateway, control: dict[str, Any]) -> Any:
    models = rows()
    pager = SimpleNamespace(
        visible_rows=[
            {"host_id": HOST, "terminal_id": f"term-{n:03d}", "status": "working"}
            for n in range(COUNT)
        ],
        rows={},
    )

    @ft.component
    def Console() -> Any:
        wiring = use_changes_wiring(
            transport=gateway,
            pager=pager,
            destination="agents",
            selection="",
            section="",
            foreground=True,
            wide=True,
            navigate=lambda *a, **k: None,
        )
        control["summaries"] = wiring.summaries
        return AgentsList(
            rows=with_changes(models, wiring.summaries),
            selected="",
            on_select=lambda key: None,
            on_start_agent=lambda: None,
            on_changes=lambda key: None,
            on_window=wiring.on_window,
        )

    return await render_component(Console)


def test_scrolling_changes_the_agents_asked_about_and_cards_out_of_view_keep_their_summary() -> (
    None
):
    gateway = Gateway(files())
    control: dict[str, Any] = {}

    async def scenario() -> None:
        app = await mount(gateway, control)
        await app.wait_for("agents-tiles")
        await until(lambda: 0 in asked(gateway) and key_of(0) in control["summaries"])
        await app.settle(30)
        top = asked(gateway)
        assert 0 < len(top) < 60 and max(top) < 60  # the cards near the top, not 200
        first = control["summaries"][key_of(0)]

        scroll(app, CARD * 150)  # a long way down
        await until(lambda: 150 in asked(gateway), timeout=5.0)
        await app.settle(30)
        asked_now = asked(gateway)
        assert asked_now - top  # the set moved with the list
        assert not any(60 < n < 120 for n in asked_now)  # and never covered what was skipped
        assert max(asked_now) < 200 and len(asked_now) < 120
        # The cards that left the screen keep what they last showed.
        assert control["summaries"][key_of(0)] == first

        scroll(app, 0.0)
        await until(lambda: present(app, f"agent-changes-{key_of(0)}-figure"))

    run(scenario())


def test_three_unchanged_summary_cycles_rebuild_no_card_and_send_nothing() -> None:
    gateway = Gateway(files())
    control: dict[str, Any] = {}
    renders: list[int] = []

    async def scenario() -> None:
        models = rows()[:6]

        @ft.component
        def Console() -> Any:
            renders.append(1)
            feed = use_changes_feed(
                ChangesRepository(gateway),  # type: ignore[arg-type]
                [m.key for m in models],
                {},
                console=True,
                foreground=True,
            )
            control["feed"] = feed
            return AgentsList(
                rows=with_changes(models, feed.summaries),
                selected="",
                on_select=lambda key: None,
                on_start_agent=lambda: None,
                on_changes=lambda key: None,
            )

        app = await render_component(Console)
        await until(lambda: len(control["feed"].summaries) == 6)
        await app.settle(10)
        first = dict(control["feed"].summaries)
        meter = PatchMeter(app)
        meter.reset()
        drawn = len(renders)
        for _cycle in range(3):
            gateway.clock_ms += 10_000  # each check carries a later time, and nothing else
            before = len(gateway.calls)
            control["feed"].recheck(key_of(0))
            await until(partial(more_calls, gateway, before))
            await app.settle(10)
        assert control["feed"].checked[key_of(0)] == gateway.clock_ms
        assert all(control["feed"].summaries[k] is first[k] for k in first)
        assert len(renders) == drawn and meter.bytes == 0

    run(scenario())
