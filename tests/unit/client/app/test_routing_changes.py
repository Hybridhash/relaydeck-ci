"""The agents destination's Changes section round-trips through routes and the bridge."""

from __future__ import annotations

from relaydeck.client.app.navigation import RouteBridge
from relaydeck.client.models.routing import format_route, parse_route


def test_changes_section_round_trips() -> None:
    route = format_route("agents", "h1:t1", "changes")
    assert route == "/agents/h1:t1/changes"
    state = parse_route(route)
    assert (state.destination, state.selection, state.section) == ("agents", "h1:t1", "changes")


def test_unknown_or_unselected_sections_are_dropped() -> None:
    assert parse_route("/agents/h1:t1/elsewhere").section == ""
    assert parse_route("/agents//changes").section == ""
    assert format_route("agents", "", "changes") == "/agents"
    assert format_route("terminals", "h1:t1", "changes") == "/terminals/h1:t1"


def test_bridge_keeps_the_section_across_resizes_and_routes() -> None:
    bridge = RouteBridge("/agents/h1:t1/changes")
    assert bridge.state.section == "changes"
    bridge.set_size(400, 800)
    assert bridge.state.section == "changes"
    bridge.set_local("agents", "h1:t1")
    assert bridge.state.section == ""
    bridge.set_local("agents", "h1:t1", "changes")
    assert bridge.state.section == "changes"
    bridge.set_route("/agents/h1:t1")
    assert bridge.state.section == ""


def test_a_section_carries_the_number_of_its_history_entry() -> None:
    assert format_route("agents", "h1:t1", "commit", 3) == "/agents/h1:t1/commit.3"
    assert format_route("agents", "h1:t1", "commit-file") == "/agents/h1:t1/commit-file"
    state = parse_route("/agents/h1:t1/commit-file.12")
    assert (state.section, state.step) == ("commit-file", 12)
    assert parse_route("/agents/h1:t1/commit").step == 0  # an entry without a number is entry 0


def test_a_bad_number_is_ignored_and_never_widens_the_section() -> None:
    for tail in ("commit.x", "commit.-1", "commit.1.2", "commit.99999999999"):
        state = parse_route(f"/agents/h1:t1/{tail}")
        assert state.step == 0
    assert parse_route("/agents/h1:t1/elsewhere.3").section == ""
    assert parse_route("/agents/h1:t1/elsewhere.3").step == 0
    assert format_route("settings", "", "hosts", 4) == "/settings/hosts"
    assert format_route("agents", "h1:t1", "", 4) == "/agents/h1:t1"


def test_the_bridge_keeps_the_number_with_the_section() -> None:
    bridge = RouteBridge("/agents/h1:t1/commit.5")
    assert (bridge.state.section, bridge.state.step) == ("commit", 5)
    bridge.set_size(500, 700)
    assert bridge.state.step == 5
    bridge.set_local("agents", "h1:t1", "commit-file", 6)
    assert (bridge.state.section, bridge.state.step) == ("commit-file", 6)
    bridge.set_local("agents", "h1:t1", "")
    assert bridge.state.step == 0
    bridge.set_route("/agents/h1:t1/commit.7")
    assert bridge.state.step == 7
