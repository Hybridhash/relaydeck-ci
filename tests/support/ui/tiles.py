"""The cards a windowed Console list has built, without its spacers or measuring wrappers."""

from __future__ import annotations

from typing import Any


def built_tiles(app: Any, name: str = "agents-tiles") -> list[Any]:
    cards: list[Any] = []
    for control in app.find(name).controls:
        if str(getattr(control, "key", "")).endswith(("-before", "-after")):
            continue
        # A measuring wrapper reports its size; a card the list did not wrap is itself.
        wrapped = getattr(control, "on_size_change", None) is not None
        cards.append(control.content if wrapped else control)
    return cards
