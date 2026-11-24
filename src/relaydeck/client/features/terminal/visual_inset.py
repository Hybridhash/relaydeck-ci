"""Per-page sink for the keyboard inset the terminal engine measures from the visual viewport.
The engine control reports page metrics; the page's visible-size owner subscribes here, so
the terminal screen never needs a reference to it.
"""

from __future__ import annotations

import weakref
from collections.abc import Callable
from typing import Any

_sinks: dict[int, Callable[[float], None]] = {}


def register(page: Any, sink: Callable[[float], None]) -> None:
    key = id(page)
    _sinks[key] = sink
    try:
        weakref.finalize(page, _sinks.pop, key, None)
    except TypeError:
        pass


def unregister(page: Any) -> None:
    _sinks.pop(id(page), None)


def publish(page: Any, inset: float) -> None:
    sink = _sinks.get(id(page))
    if sink is not None:
        sink(inset)
