"""Route labels and the page-to-tree title update."""

from types import SimpleNamespace
from typing import cast

import flet as ft
import pytest

from relaydeck.client.app.navigation import RouteBridge, route_title
from relaydeck.client.app.resources import _route_changed


@pytest.mark.parametrize(
    ("route", "screen"),
    [
        ("", "Console"),
        ("/", "Console"),
        ("/unknown/private-name", "Console"),
        ("/agents", "Console"),
        ("/agents/private-host:private-agent", "Console"),
        ("/agents/private-host:private-agent/changes", "Changes"),
        ("/agents/private-host:private-agent/commit.2", "Changes"),
        ("/agents/private-host:private-agent/commit-file.3", "Changes"),
        ("/agents/private-host:private-agent/unknown", "Console"),
        ("/terminals", "Terminals"),
        ("/terminals/private-host:private-terminal", "Terminal"),
        ("/terminals/private-host:private-terminal/changes", "Terminal"),
        ("/usage", "Usage"),
        ("/usage/private-name", "Usage"),
        ("/settings", "Settings"),
        ("/settings/unknown", "Settings"),
        ("/settings/hosts", "Hosts"),
        ("/settings/hosts/private-host", "Hosts"),
        ("/hosts", "Hosts"),
        ("/hosts/private-host", "Hosts"),
    ],
)
def test_route_title_uses_only_a_fixed_view_label(route: str, screen: str) -> None:
    assert route_title(route) == f"{screen} · RelayDeck"


def test_title_update_preserves_route_notifications_and_callback_result() -> None:
    page = cast(ft.Page, SimpleNamespace(route="/usage", title="stale"))
    bridge = RouteBridge()
    changes = []
    bridge.subscribe(changes.append)
    assert _route_changed(page, bridge, None)
    assert page.title == "Usage · RelayDeck"
    assert bridge.state.destination == "usage"
    assert changes == [bridge.state]
    page.title = "stale"
    assert not _route_changed(page, bridge, None)
    assert page.title == "Usage · RelayDeck"
    assert changes == [bridge.state]
    page.route = ""
    assert _route_changed(page, bridge, None)
    assert page.title == "Console · RelayDeck"
    assert bridge.state.destination == "agents"
    assert len(changes) == 2
