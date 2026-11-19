"""Walk a rendered control tree in declaration order, for the catalog and headless tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import flet as ft

_CHILD_ATTRS = (
    "leading",
    "title",
    "subtitle",
    "content",
    "controls",
    "destinations",
    "actions",
    "trailing",
)


def _is_node(item: Any) -> bool:
    # Controls and declarative Component wrappers (Component is not a
    # Control subclass but owns the rendered body in `_b`).
    return isinstance(item, ft.Control) or hasattr(item, "_b")


def _children(control: Any) -> list[Any]:
    found: list[Any] = []
    body = getattr(control, "_b", None)
    if body is not None:
        found.extend(body if isinstance(body, list) else [body])
    for attr in _CHILD_ATTRS:
        try:
            value = getattr(control, attr, None)
        except Exception:  # noqa: S112 - tolerant tree walk
            continue
        if value is None:
            continue
        items = list(value) if isinstance(value, list | tuple) else [value]
        found.extend(item for item in items if _is_node(item))
    return found


def iter_controls(roots: list[Any], *, active_only: bool = False) -> Iterator[Any]:
    """Yield controls in traversal (declaration) order, cycle-safe."""
    seen: set[int] = set()
    stack: list[Any] = list(reversed(roots))
    while stack:
        control = stack.pop()
        if control is None or id(control) in seen:
            continue
        seen.add(id(control))
        if active_only and (
            getattr(control, "visible", True) is False
            or getattr(control, "disabled", False) is True
        ):
            continue
        yield control
        kids = _children(control)
        stack.extend(reversed(kids))


__all__ = ["iter_controls"]
