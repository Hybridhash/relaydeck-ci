"""Device-local terminal and inventory preferences shown in Settings. Flet-free."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from relaydeck.client.models.input.modes import WORKFLOW_AGENT_COMPOSER, WORKFLOW_AGENT_DIRECT

PREFS_KEY = "relaydeck.terminal_prefs.v1"

FONT_MIN = 9.0
FONT_MAX = 22.0
FONT_STEP = 1.0
FONT_DEFAULT_WEB = 13.0
FONT_DEFAULT_PHONE = 12.0

PINS_MAX = 200
AGENT_WORKFLOWS = (WORKFLOW_AGENT_COMPOSER, WORKFLOW_AGENT_DIRECT)


@dataclass(frozen=True)
class TerminalPrefs:
    """`font_size` None means the platform default (13 px web, 12 px phone)."""

    font_size: float | None = None
    agents_open_in: str = WORKFLOW_AGENT_COMPOSER
    confirm_takeover: bool = True
    # Opening an agent takes control automatically (an explicit product decision).
    # Another device holding control still gets the explicit conflict prompt.
    auto_takeover: bool = True
    show_key_row: bool = True
    # a light tap on Send and Copy (native apps; not iPhone/iPad browsers).
    haptics: bool = True
    space_key: str = ""
    # Agents pinned to the top of the Console, by scoped key. Kept in this browser only.
    pinned_agents: tuple[str, ...] = ()
    # The update version this device has seen in Settings (clears the rail dot).
    acknowledged_update: str = ""

    def effective_font_size(self, phone: bool) -> float:
        if self.font_size is not None:
            return self.font_size
        return FONT_DEFAULT_PHONE if phone else FONT_DEFAULT_WEB

    def step_font(self, delta: float, phone: bool) -> TerminalPrefs:
        size = min(FONT_MAX, max(FONT_MIN, self.effective_font_size(phone) + delta))
        return replace(self, font_size=size)


def clean(raw: Any) -> TerminalPrefs:
    """Validate a stored document field by field; bad fields use defaults."""
    if not isinstance(raw, dict):
        return TerminalPrefs()
    prefs = TerminalPrefs()
    size = raw.get("font_size")
    if isinstance(size, int | float) and not isinstance(size, bool):
        prefs = replace(prefs, font_size=min(FONT_MAX, max(FONT_MIN, float(size))))
    workflow = raw.get("agents_open_in")
    if workflow in AGENT_WORKFLOWS:
        prefs = replace(prefs, agents_open_in=workflow)
    confirm = raw.get("confirm_takeover")
    if isinstance(confirm, bool):
        prefs = replace(prefs, confirm_takeover=confirm)
    auto = raw.get("auto_takeover")
    if isinstance(auto, bool):
        prefs = replace(prefs, auto_takeover=auto)
    show_keys = raw.get("show_key_row")
    if isinstance(show_keys, bool):
        prefs = replace(prefs, show_key_row=show_keys)
    haptic = raw.get("haptics")
    if isinstance(haptic, bool):
        prefs = replace(prefs, haptics=haptic)
    space = raw.get("space_key")
    if (
        isinstance(space, str)
        and len(space) <= 512
        and (space == "" or (":" in space and space.strip() == space))
        and all(ord(char) >= 32 and ord(char) != 127 for char in space)
    ):
        prefs = replace(prefs, space_key=space)
    pins = raw.get("pinned_agents")
    if isinstance(pins, list):
        clean_pins = tuple(
            dict.fromkeys(
                pin
                for pin in pins
                if isinstance(pin, str)
                and 0 < len(pin) <= 512
                and ":" in pin
                and all(ord(char) >= 32 and ord(char) != 127 for char in pin)
            )
        )
        prefs = replace(prefs, pinned_agents=clean_pins[:PINS_MAX])
    acknowledged = raw.get("acknowledged_update")
    if (
        isinstance(acknowledged, str)
        and len(acknowledged) <= 32
        and all(char.isdigit() or char == "." for char in acknowledged)
    ):
        prefs = replace(prefs, acknowledged_update=acknowledged)
    return prefs


def encode(prefs: TerminalPrefs) -> str:
    return json.dumps(asdict(prefs), sort_keys=True)


async def try_load(get: Callable[[str], Awaitable[Any]]) -> TerminalPrefs | None:
    """Return stored preferences or defaults for an absent or invalid document, and None for a
    failed read. After a failed read, callers must not overwrite the possibly unseen document
    until reading succeeds.
    """
    try:
        stored = await get(PREFS_KEY)
    except Exception:  # noqa: BLE001 - a failed read is not an absent document
        return None
    if not isinstance(stored, str):
        return TerminalPrefs()
    try:
        return clean(json.loads(stored))
    except ValueError:
        return TerminalPrefs()


async def load(get: Callable[[str], Awaitable[Any]]) -> TerminalPrefs:
    loaded = await try_load(get)
    return loaded if loaded is not None else TerminalPrefs()


async def save(set_: Callable[[str, str], Awaitable[Any]], prefs: TerminalPrefs) -> bool:
    try:
        await set_(PREFS_KEY, encode(prefs))
    except Exception:  # noqa: BLE001 - storage unavailable: keep in memory
        return False
    return True


__all__ = [
    "AGENT_WORKFLOWS",
    "FONT_DEFAULT_PHONE",
    "FONT_DEFAULT_WEB",
    "FONT_MAX",
    "FONT_MIN",
    "FONT_STEP",
    "PINS_MAX",
    "PREFS_KEY",
    "TerminalPrefs",
    "clean",
    "encode",
    "load",
    "save",
    "try_load",
]
