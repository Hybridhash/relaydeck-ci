"""Route-stored navigation. Pure and framework-free."""

from __future__ import annotations

from dataclasses import dataclass

from relaydeck.client.models.layout import DESKTOP

ROUTE_DESTINATIONS = ("agents", "terminals", "usage", "settings")
# Hosts is a Settings section; links to the Hosts destination resolve to it.
SETTINGS_SECTIONS = ("hosts",)
# Use opaque route sections for Changes screen levels while reader positions stay in memory.
# Numbered history entries let Back return one level at a time.
CHANGES_SECTIONS = ("changes", "file", "commit", "commit-file")
AGENT_SECTIONS = CHANGES_SECTIONS
DEFAULT_HEIGHT = 800


@dataclass(frozen=True)
class RouteState:
    destination: str = "agents"
    selection: str = ""
    width: float = 1280
    height: float = DEFAULT_HEIGHT
    section: str = ""  # a Settings section ("hosts") or an agent section ("changes")
    step: int = 0  # which history entry an agent section is: a counter, never an identity

    layout: str = DESKTOP

    @property
    def is_wide(self) -> bool:
        return self.layout == DESKTOP


def parse_route(route: str) -> RouteState:
    parts = [part for part in route.split("/") if part]
    if parts and parts[0] == "hosts":
        # Legacy Hosts links redirect into Settings › Hosts, keeping the host selection.
        host = parts[1] if len(parts) > 1 else ""
        return RouteState(destination="settings", selection=host, section="hosts")
    if not parts or parts[0] not in ROUTE_DESTINATIONS:
        return RouteState()
    destination = parts[0]
    if destination == "settings":
        section = parts[1] if len(parts) > 1 and parts[1] in SETTINGS_SECTIONS else ""
        selection = parts[2] if section and len(parts) > 2 else ""
        return RouteState(destination=destination, selection=selection, section=section)
    selection = parts[1] if len(parts) > 1 else ""
    name, _, count = (parts[2] if len(parts) > 2 else "").partition(".")
    section = name if destination == "agents" and selection and name in AGENT_SECTIONS else ""
    step = int(count) if section and count.isdigit() and len(count) < 10 else 0
    return RouteState(destination=destination, selection=selection, section=section, step=step)


def format_route(destination: str, selection: str = "", section: str = "", step: int = 0) -> str:
    if destination not in ROUTE_DESTINATIONS:
        destination = "agents"
    if destination == "settings":
        # The only Settings selection is a host, which lives in the Hosts section.
        if selection:
            return f"/settings/hosts/{selection}"
        return f"/settings/{section}" if section in SETTINGS_SECTIONS else "/settings"
    if selection and destination == "agents" and section in AGENT_SECTIONS:
        # The entry's number tells a browser Back from a Forward; it names nothing else.
        return f"/{destination}/{selection}/{section}" + (f".{step}" if step > 0 else "")
    if selection:
        return f"/{destination}/{selection}"
    return f"/{destination}"


def agent_section(destination: str, selection: str, section: str) -> bool:
    return destination == "agents" and bool(selection) and section in AGENT_SECTIONS
