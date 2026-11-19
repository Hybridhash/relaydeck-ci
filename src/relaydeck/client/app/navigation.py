"""Page route and viewport bridge over pure navigation values."""

from __future__ import annotations

from collections.abc import Callable

from relaydeck.client.design_system.theme import layout_mode
from relaydeck.client.models.routing import (
    CHANGES_SECTIONS,
    DEFAULT_HEIGHT,
    ROUTE_DESTINATIONS,
    RouteState,
    agent_section,
    parse_route,
)


def route_title(route: str) -> str:
    """Name the current view without including host or session identities."""
    state = parse_route(route)
    if state.destination == "settings":
        screen = "Hosts" if state.section == "hosts" else "Settings"
    elif state.destination == "usage":
        screen = "Usage"
    elif state.destination == "terminals":
        screen = "Terminal" if state.selection else "Terminals"
    else:
        screen = "Changes" if state.section in CHANGES_SECTIONS else "Console"
    return f"{screen} · RelayDeck"


class RouteBridge:
    """Mutable route mirror bridging page events into the tree. `main()` feeds
    page.on_route_change/on_resize here; the root component subscribes and mirrors the state
    into use_state.
    """

    def __init__(
        self, route: str = "/", width: float = 1280, height: float = DEFAULT_HEIGHT
    ) -> None:
        parsed = parse_route(route)
        self._state = RouteState(
            destination=parsed.destination,
            selection=parsed.selection,
            section=parsed.section,
            step=parsed.step,
            width=width,
            height=height,
            layout=layout_mode(width),
        )
        self._listeners: list[Callable[[RouteState], None]] = []

    @property
    def state(self) -> RouteState:
        return self._state

    def subscribe(self, listener: Callable[[RouteState], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _publish(self, state: RouteState) -> bool:
        if state == self._state:
            return False
        self._state = state
        for listener in list(self._listeners):
            listener(state)
        return True

    def set_route(self, route: str) -> bool:
        parsed = parse_route(route)
        return self._publish(
            RouteState(
                destination=parsed.destination,
                selection=parsed.selection,
                section=parsed.section,
                step=parsed.step,
                width=self._state.width,
                height=self._state.height,
                layout=self._state.layout,
            )
        )

    def set_width(self, width: float) -> bool:
        return self.set_size(width, self._state.height)

    def set_size(self, width: float, height: float) -> bool:
        return self._publish(
            RouteState(
                destination=self._state.destination,
                selection=self._state.selection,
                section=self._state.section,
                step=self._state.step,
                width=width,
                height=height,
                layout=layout_mode(width),
            )
        )

    def set_local(self, destination: str, selection: str, section: str = "", step: int = 0) -> bool:
        """Mirror a tree-initiated navigation (already pushed). A Settings selection is a host in
        the Hosts section."""
        if destination not in ROUTE_DESTINATIONS:
            destination = "agents"
        if destination == "settings" and selection:
            section = "hosts"
        return self._publish(
            RouteState(
                destination=destination,
                selection=selection,
                section=section
                if destination == "settings" or agent_section(destination, selection, section)
                else "",
                step=step if destination == "agents" else 0,
                width=self._state.width,
                height=self._state.height,
                layout=self._state.layout,
            )
        )
