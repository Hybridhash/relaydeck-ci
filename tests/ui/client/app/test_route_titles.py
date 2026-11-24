"""Page titles follow initial deep links and browser route events."""

import flet as ft
import httpx
import pytest

from relaydeck.client.app.resources import build_resources
from tests.support.host_harness import render_component, run


@pytest.mark.parametrize(
    ("route", "title"),
    [
        ("/", "Console · RelayDeck"),
        ("/agents/private-host:private-agent", "Console · RelayDeck"),
        ("/agents/private-host:private-agent/commit-file.3", "Changes · RelayDeck"),
        ("/terminals/private-host:private-terminal", "Terminal · RelayDeck"),
        ("/usage", "Usage · RelayDeck"),
        ("/settings", "Settings · RelayDeck"),
        ("/hosts/private-host", "Hosts · RelayDeck"),
    ],
)
def test_initial_deep_link_sets_the_page_title(route: str, title: str) -> None:
    async def scenario() -> None:
        app = await render_component(ft.Column)
        app.session.apply_patch(app.page._i, {"route": route})
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ) as client:
            resources = build_resources(
                app.page,
                base_url="http://gateway.test",
                get_token=lambda: "synthetic",
                host_id="private-host",
                client=client,
            )
            try:
                assert app.page.title == title
                assert "private-" not in app.page.title
            finally:
                await resources.close()

    run(scenario())


def test_route_events_and_history_restore_title_and_bridge_together() -> None:
    async def scenario() -> None:
        app = await render_component(ft.Column)
        app.conn.page_url = "http://gateway.test"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(503))
        ) as client:
            resources = build_resources(
                app.page,
                base_url="http://gateway.test",
                get_token=lambda: "synthetic",
                host_id="private-host",
                client=client,
            )
            changes = []
            resources.bridge.subscribe(changes.append)
            try:
                visits = [
                    ("/usage", "Usage", "usage", ""),
                    ("/settings/hosts/private-host", "Hosts", "settings", "hosts"),
                    ("/usage", "Usage", "usage", ""),
                    ("/settings/hosts/private-host", "Hosts", "settings", "hosts"),
                    (
                        "/agents/private-host:private-agent/changes.2",
                        "Changes",
                        "agents",
                        "changes",
                    ),
                    ("/agents/private-host:private-agent", "Console", "agents", ""),
                    ("/terminals", "Terminals", "terminals", ""),
                    ("/terminals/private-host:private-terminal", "Terminal", "terminals", ""),
                    ("/unknown/private-value", "Console", "agents", ""),
                ]
                for route, title, destination, section in visits:
                    app.session.apply_patch(app.page._i, {"route": route})
                    await app.session.dispatch_event(app.page._i, "route_change", {"route": route})
                    assert app.page.title == f"{title} · RelayDeck"
                    assert resources.bridge.state.destination == destination
                    assert resources.bridge.state.section == section
                assert len(changes) == len(visits)
                await app.session.dispatch_event(
                    app.page._i, "route_change", {"route": app.page.route}
                )
                assert app.page.title == "Console · RelayDeck"
                assert len(changes) == len(visits)
            finally:
                await resources.close()

    run(scenario())
